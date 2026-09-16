"""
Cruzamento do polígono do imóvel com bases públicas — sobreposição e proximidade.

Bases, todas públicas. Cada uma declara se foi consultada: base fora do ar
NUNCA vira "sem sobreposição" (mesma regra da checagem por ponto).
  • SIGEF (INCRA)       — parcelas certificadas, privadas e públicas, da UF do
                          imóvel, pelo WFS do acervo fundiário (GML);
  • Terras indígenas    — WFS do geoserver oficial da FUNAI;
  • Unidades de conservação (CNUC/MMA, ago/2025) — EMBARCADO em
                          app/dados/cnuc_ucs.json.gz (o MMA não publica serviço
                          de consulta), simplificado a ~110 m;
  • Processos minerários ativos (ANM/SIGMINE) — ArcGIS REST;
  • Embargos do ICMBio (unidades federais) — WFS da INDE.
Fica de fora, e é dito: florestas públicas (CNFP), embargos do IBAMA,
territórios quilombolas e assentamentos (ver NAO_COBERTOS).

Geometria sem dependência externa:
  • sobreposição por AMOSTRAGEM — grade de 60×60 pontos sobre o imóvel, cada
    ponto interno testado contra o outro polígono (par-ímpar). Com 3.000 ha,
    cada ponto vale ~1 ha: resolução de ~0,05% da área, erro de borda de 1–2%;
  • distância pela menor distância entre contornos, num plano local (km).
Resultado é TRIAGEM: confirmar no portal de cada órgão antes de decidir.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import requests

UA = {"User-Agent": "Mozilla/5.0"}  # a FUNAI devolve 403 sem User-Agent de navegador
TIMEOUT = 25
GRADE = 60

URL_SIGEF = "https://acervofundiario.incra.gov.br/i3geo/ogc.php"
URL_FUNAI = "https://geoserver.funai.gov.br/geoserver/Funai/ows"
URL_ANM = "https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/MapServer/0/query"
URL_ICMBIO = "https://geoservicos.inde.gov.br/geoserver/ICMBio/ows"
ARQ_CNUC = Path(__file__).resolve().parents[1] / "dados" / "cnuc_ucs.json.gz"

BASES: dict[str, dict[str, str]] = {
    "sigef": {"rotulo": "Parcelas certificadas no SIGEF (INCRA)",
              "fonte": "INCRA — acervo fundiário, WFS (parcelas certificadas privadas e públicas da UF)"},
    "funai": {"rotulo": "Terras indígenas (FUNAI)",
              "fonte": "FUNAI — geoserver oficial, camada tis_poligonais"},
    "cnuc": {"rotulo": "Unidades de conservação (CNUC)",
             "fonte": "MMA — CNUC, shapefile de ago/2025, embarcado e simplificado a ~110 m"},
    "anm": {"rotulo": "Processos minerários ativos (ANM)",
            "fonte": "ANM — SIGMINE, serviço ArcGIS REST"},
    "embargos_icmbio": {"rotulo": "Embargos do ICMBio",
                        "fonte": "ICMBio — WFS da INDE (embargos em unidades federais)"},
}
# Raio de busca por base (km): SIGEF só interessa o que encosta no imóvel.
# TI e UC a 50 km: é o contexto que a due diligence pede (no Lote 72, a TI mais
# próxima está a 40,7 km e ficava fora com 30 km).
RAIOS = {"sigef": 0.5, "funai": 50.0, "cnuc": 50.0, "anm": 10.0, "embargos_icmbio": 5.0}

NAO_COBERTOS = [
    "Florestas públicas (CNFP/SFB): sem serviço aberto de consulta — conferir no portal do SFB.",
    "Embargos do IBAMA: o serviço espacial público estava fora do ar em 11/09/2026 — conferir no portal do IBAMA.",
    "Territórios quilombolas e assentamentos (INCRA): camadas não localizadas no serviço aberto.",
]
METODO = ("Sobreposição por amostragem (grade de 60×60 pontos sobre o imóvel); distância entre contornos "
          "num plano local. Triagem: confirmar no portal de cada órgão.")

Anel = list[tuple[float, float]]
Poligono = list[Anel]


# ── Geometria ───────────────────────────────────────────────────────────────

def poligonos_do_geojson(g: Optional[dict]) -> list[Poligono]:
    """GeoJSON (Polygon, MultiPolygon, Feature, FeatureCollection) → polígonos (anéis, par-ímpar)."""
    if not isinstance(g, dict):
        return []
    t = g.get("type")
    if t == "Feature":
        return poligonos_do_geojson(g.get("geometry"))
    if t == "FeatureCollection":
        return [p for f in g.get("features") or [] for p in poligonos_do_geojson(f)]

    def anel(a) -> Anel:
        return [(float(p[0]), float(p[1])) for p in a if len(p) >= 2]
    if t == "Polygon":
        p = [anel(a) for a in g.get("coordinates") or [] if len(a) >= 4]
        return [p] if p else []
    if t == "MultiPolygon":
        return [q for q in ([anel(a) for a in poly if len(a) >= 4] for poly in g.get("coordinates") or []) if q]
    return []


def _no_anel(x: float, y: float, anel: Anel) -> bool:
    dentro = False
    j = len(anel) - 1
    for i in range(len(anel)):
        xi, yi = anel[i]
        xj, yj = anel[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dentro = not dentro
        j = i
    return dentro


def dentro(x: float, y: float, poligonos: list[Poligono]) -> bool:
    return any(sum(_no_anel(x, y, a) for a in p) % 2 == 1 for p in poligonos)


def caixa(poligonos: list[Poligono]) -> tuple[float, float, float, float]:
    xs = [x for p in poligonos for a in p for x, _ in a]
    ys = [y for p in poligonos for a in p for _, y in a]
    return min(xs), min(ys), max(xs), max(ys)


def _tocam(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _expandir(cx, km: float):
    lat = (cx[1] + cx[3]) / 2
    dy = km / 110.574
    dx = km / (111.320 * math.cos(math.radians(lat)))
    return cx[0] - dx, cx[1] - dy, cx[2] + dx, cx[3] + dy


class _Plano:
    """Projeção local equiretangular em km — suficiente para dezenas de km."""
    def __init__(self, lat0: float):
        self.kx = 111.320 * math.cos(math.radians(lat0))
        self.ky = 110.574

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return lon * self.kx, lat * self.ky


def area_ha(poligonos: list[Poligono]) -> float:
    cx = caixa(poligonos)
    pl = _Plano((cx[1] + cx[3]) / 2)
    total = 0.0
    for p in poligonos:
        for i, a in enumerate(p):
            pts = [pl.xy(*q) for q in a]
            s = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2
            total += s if i == 0 else -s
    return total * 100.0  # km² → ha


def amostra(poligonos: list[Poligono], n: int = GRADE) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = caixa(poligonos)
    dx, dy = (x1 - x0) / n, (y1 - y0) / n
    pts = ((x0 + (i + 0.5) * dx, y0 + (j + 0.5) * dy) for i in range(n) for j in range(n))
    return [(x, y) for x, y in pts if dentro(x, y, poligonos)]


def fracao_dentro(pontos, outro: list[Poligono]) -> float:
    if not pontos:
        return 0.0
    cb = caixa(outro)
    n = sum(1 for x, y in pontos if cb[0] <= x <= cb[2] and cb[1] <= y <= cb[3] and dentro(x, y, outro))
    return n / len(pontos)


def _dist_ponto_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def distancia_km(a: list[Poligono], b: list[Poligono], pl: _Plano) -> float:
    A = [[pl.xy(*q) for q in anel] for p in a for anel in p]
    B = [[pl.xy(*q) for q in anel] for p in b for anel in p]
    melhor = math.inf
    for origem, destino in ((A, B), (B, A)):
        segs = [(s, t) for anel in destino for s, t in zip(anel, anel[1:])]
        for anel in origem:
            for px, py in anel:
                for (ax, ay), (bx, by) in segs:
                    d = _dist_ponto_seg(px, py, ax, ay, bx, by)
                    if d < melhor:
                        melhor = d
    return melhor


# ── Consultas ───────────────────────────────────────────────────────────────

_GML = "{http://www.opengis.net/gml}"


def _ler_gml(conteudo: bytes) -> list[dict]:
    """WFS 1.0.0 / GML2 do MapServer do INCRA → [{attrs, poligonos}]."""
    raiz = ET.fromstring(conteudo)
    if raiz.tag.endswith("ServiceExceptionReport"):
        raise ValueError(" ".join(raiz.itertext()).strip()[:200])
    saida = []
    for membro in raiz.iter(_GML + "featureMember"):
        for feat in membro:
            attrs: dict[str, str] = {}
            polys: list[Poligono] = []
            for filho in feat:
                nome = filho.tag.split("}")[-1]
                if nome == "boundedBy":
                    continue
                if len(filho):  # geometria
                    for pol in filho.iter(_GML + "Polygon"):
                        aneis = [[tuple(float(v) for v in par.split(",")[:2]) for par in (c.text or "").split()]
                                 for c in pol.iter(_GML + "coordinates")]
                        aneis = [a for a in aneis if len(a) >= 4]
                        if aneis:
                            polys.append(aneis)
                else:
                    attrs[nome] = (filho.text or "").strip()
            if polys:
                saida.append({"attrs": attrs, "poligonos": polys})
    return saida


def _data_br(s: Optional[str]) -> str:
    s = (s or "").strip()
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if re.match(r"^\d{4}-\d{2}-\d{2}", s) else (s or "—")


def _sigef(ctx: dict, raio_km: float) -> list[dict]:
    uf = ctx["uf"]
    if not uf:
        raise ValueError("UF do imóvel não informada")
    x0, y0, x1, y1 = _expandir(ctx["caixa"], raio_km)
    itens, erros = [], []
    for camada in (f"certificada_sigef_particular_{uf}", f"certificada_sigef_publico_{uf}"):
        try:
            r = requests.get(URL_SIGEF, headers=UA, timeout=TIMEOUT, params={
                "service": "WFS", "version": "1.0.0", "request": "GetFeature",
                "typeName": camada, "bbox": f"{x0},{y0},{x1},{y1}"})
            r.raise_for_status()
            feats = _ler_gml(r.content)
        except Exception as e:  # noqa: BLE001
            erros.append(f"{camada}: {_motivo(e)}")
            continue
        for f in feats:
            a = f["attrs"]
            itens.append({
                "nome": a.get("nome_area") or "parcela sem nome",
                "detalhe": (f"matrícula {a.get('registro_matricula') or '—'} · RT {a.get('rt') or '—'} · "
                            f"aprovada em {_data_br(a.get('data_aprovacao'))}"),
                "poligonos": f["poligonos"],
                "extra": {"parcela": a.get("parcela_codigo"), "matricula": a.get("registro_matricula"),
                          "rt": a.get("rt"), "status": a.get("status"),
                          "situacao": a.get("situacao_informada"), "codigo_imovel": a.get("codigo_imovel"),
                          "data_aprovacao": _data_br(a.get("data_aprovacao")),
                          "camada": "pública" if "publico" in camada else "privada"}})
    if len(erros) == 2:
        raise ValueError("; ".join(erros))
    return itens


def _funai(ctx: dict, raio_km: float) -> list[dict]:
    x0, y0, x1, y1 = _expandir(ctx["caixa"], raio_km)
    r = requests.get(URL_FUNAI, headers=UA, timeout=TIMEOUT, params={
        "service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "Funai:tis_poligonais",
        "outputFormat": "application/json", "bbox": f"{x0},{y0},{x1},{y1}"})
    r.raise_for_status()
    itens = []
    for f in r.json().get("features") or []:
        p = f.get("properties") or {}
        itens.append({"nome": f"TI {p.get('terrai_nome') or '—'}",
                      "detalhe": ", ".join(x for x in (p.get("etnia_nome"), (p.get("fase_ti") or "").lower(),
                                                       p.get("modalidade_ti")) if x),
                      "poligonos": poligonos_do_geojson(f.get("geometry")),
                      "extra": {"fase": p.get("fase_ti"), "codigo": p.get("terrai_codigo")}})
    return itens


@lru_cache(maxsize=1)
def _base_cnuc() -> dict:
    with gzip.open(ARQ_CNUC, "rt", encoding="utf-8") as fh:
        return json.load(fh)


_MINUSC = {"de", "da", "do", "das", "dos", "e", "em"}


def _nome_proprio(s: str) -> str:
    ps = (s or "").lower().split()
    return " ".join(p if (i and p in _MINUSC) else p[:1].upper() + p[1:] for i, p in enumerate(ps))


def _cnuc(ctx: dict, raio_km: float) -> list[dict]:
    cx = _expandir(ctx["caixa"], raio_km)
    itens = []
    for u in _base_cnuc()["ucs"]:
        if not _tocam(u["bb"], cx):
            continue
        itens.append({"nome": _nome_proprio(u["n"]),
                      "detalhe": ", ".join(x for x in (u.get("c"), (u.get("e") or "").lower(),
                                                       (u.get("g") or "").lower(),
                                                       f"criada em {u['a']}" if u.get("a") else "") if x),
                      "poligonos": [[[tuple(p) for p in anel] for anel in u["r"]]],
                      "extra": {"categoria": u.get("c"), "grupo": u.get("g"), "esfera": u.get("e"),
                                "cnuc": u.get("id")}})
    return itens


def _anm(ctx: dict, raio_km: float) -> list[dict]:
    x0, y0, x1, y1 = _expandir(ctx["caixa"], raio_km)
    r = requests.post(URL_ANM, headers=UA, timeout=TIMEOUT, data={
        "geometry": json.dumps({"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1, "spatialReference": {"wkid": 4674}}),
        "geometryType": "esriGeometryEnvelope", "inSR": "4674", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "PROCESSO,FASE,NOME,SUBS,AREA_HA,ULT_EVENTO", "returnGeometry": "true",
        "outSR": "4674", "f": "json"})
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise ValueError(str(j["error"])[:200])
    por: dict[str, dict] = {}
    for f in j.get("features") or []:
        a = f.get("attributes") or {}
        rings = (f.get("geometry") or {}).get("rings") or []
        it = por.setdefault(a.get("PROCESSO") or "?", {
            "nome": f"Processo {a.get('PROCESSO')}",
            "detalhe": " · ".join(x for x in ((a.get("SUBS") or "").lower(), (a.get("FASE") or "").lower(),
                                              a.get("NOME")) if x),
            "poligonos": [],
            "extra": {"fase": a.get("FASE"), "substancia": a.get("SUBS"), "titular": a.get("NOME"),
                      "ultimo_evento": a.get("ULT_EVENTO")}})
        aneis = [[tuple(p[:2]) for p in anel] for anel in rings if len(anel) >= 4]
        if aneis:
            it["poligonos"].append(aneis)
    return list(por.values())


def _detalhe_seguro(props: dict) -> str:
    """Só campos de identificação do ato — nunca nome, CPF ou CNPJ de autuado (LGPD)."""
    ok = []
    for k, v in props.items():
        kl = k.lower()
        if v in (None, "") or re.search(r"nome|cpf|cnpj|autuad|infrator|pessoa|ender", kl):
            continue
        if re.search(r"num|processo|data|ano|situac|tipo|area|uc", kl):
            ok.append(f"{k}: {v}")
    return "; ".join(ok[:4])


def _icmbio(ctx: dict, raio_km: float) -> list[dict]:
    x0, y0, x1, y1 = _expandir(ctx["caixa"], raio_km)
    r = requests.get(URL_ICMBIO, headers=UA, timeout=TIMEOUT, params={
        "service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": "ICMBio:embargos_icmbio",
        "outputFormat": "application/json", "bbox": f"{x0},{y0},{x1},{y1}"})
    r.raise_for_status()
    return [{"nome": "Embargo do ICMBio", "detalhe": _detalhe_seguro(f.get("properties") or {}),
             "poligonos": poligonos_do_geojson(f.get("geometry"))}
            for f in r.json().get("features") or []]


CONSULTAS: dict[str, Callable[[dict, float], list[dict]]] = {
    "sigef": _sigef, "funai": _funai, "cnuc": _cnuc, "anm": _anm, "embargos_icmbio": _icmbio,
}


# ── Cruzamento ──────────────────────────────────────────────────────────────

def _motivo(e: Exception) -> str:
    if isinstance(e, requests.Timeout):
        return f"sem resposta em {TIMEOUT} s"
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    if isinstance(e, requests.ConnectionError):
        return "sem conexão com o serviço"
    return f"{type(e).__name__}: {str(e)[:160]}"


def _mesma_matricula(a: Optional[str], b: Optional[str]) -> bool:
    da = re.sub(r"\D", "", a or "").lstrip("0")
    db = re.sub(r"\D", "", b or "").lstrip("0")
    return bool(da and db and da == db)


def _avaliar_base(chave: str, ctx: dict, itens: list[dict], raio_km: float,
                  matricula: Optional[str]) -> dict:
    sob, prox = [], []
    for it in itens:
        polys = [p for p in it.get("poligonos") or [] if p]
        if not polys:
            continue
        base = {"nome": it["nome"], "detalhe": it.get("detalhe") or ""}
        if it.get("extra"):
            base["extra"] = it["extra"]
        frac = fracao_dentro(ctx["amostra"], polys) if _tocam(caixa(polys), ctx["caixa"]) else 0.0
        if frac > 0:
            s = {**base, "pct_imovel": round(frac * 100, 2), "ha": round(frac * ctx["area_ha"], 1)}
            if chave == "sigef":
                s["propria"] = _mesma_matricula((it.get("extra") or {}).get("matricula"), matricula)
            sob.append(s)
        else:
            km = distancia_km(ctx["poligonos"], polys, ctx["plano"])
            if km <= raio_km:
                prox.append({**base, "km": round(km, 1)})
    sob.sort(key=lambda s: -s["pct_imovel"])
    prox.sort(key=lambda p: p["km"])
    return {"consultado": True, "raio_km": raio_km, "sobreposicoes": sob, "proximas": prox[:5],
            "no_entorno": len(itens)}


def cruzar(geojson: Optional[dict], *, uf: Optional[str], matricula: Optional[str] = None,
           consultas: Optional[dict[str, Callable]] = None) -> dict[str, Any]:
    """Cruza o polígono com cada base. `consultas` substitui as funções de rede (testes)."""
    polys = poligonos_do_geojson(geojson)
    if not polys:
        return {"disponivel": False, "motivo": "Polígono do imóvel ausente ou inválido."}
    cx = caixa(polys)
    ctx = {"poligonos": polys, "caixa": cx, "uf": (uf or "").strip().lower(),
           "plano": _Plano((cx[1] + cx[3]) / 2), "amostra": amostra(polys), "area_ha": area_ha(polys)}
    consultas = CONSULTAS if consultas is None else consultas

    def rodar(chave: str) -> tuple[str, dict]:
        meta = BASES[chave]
        try:
            itens = consultas[chave](ctx, RAIOS[chave])
            return chave, {**meta, **_avaliar_base(chave, ctx, itens, RAIOS[chave], matricula)}
        except Exception as e:  # noqa: BLE001 — base fora do ar não vira "sem sobreposição"
            return chave, {**meta, "consultado": False, "motivo": _motivo(e)}

    chaves = [k for k in BASES if k in consultas]
    with ThreadPoolExecutor(max_workers=max(1, len(chaves))) as ex:
        bases = dict(ex.map(rodar, chaves))
    return {
        "disponivel": True,
        "consultado_em": datetime.now(timezone.utc).isoformat(),
        "area_poligono_ha": round(ctx["area_ha"], 1),
        "matricula_referencia": matricula,
        "bases": {k: bases[k] for k in chaves},
        "nao_cobertos": NAO_COBERTOS,
        "metodo": METODO,
    }
