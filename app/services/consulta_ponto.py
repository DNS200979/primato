"""
Checagem geográfica por COORDENADA (o cooperado marca um ponto no celular —
ele quase nunca tem o KML do imóvel).

Quatro camadas, da mais barata à mais cara:

  1. OFFLINE (puro, sempre roda): coordenada dentro do Brasil? latitude e
     longitude trocadas (o erro mais comum em laudo digitado)? distância entre
     pontos; conversão de "23°32'51\" S" para decimal.
  2. BIOMA (offline, sempre roda): mapa oficial de Biomas do IBGE 1:250.000
     embarcado em app/dados/ (simplificado a ~500 m). Não depende de rede nem
     de Earth Engine — nenhum asset público de biomas foi encontrado no Earth
     Engine (8 caminhos testados em 2026-09-10), e o plano comercial do Earth
     Engine é pago; o mapa do IBGE é a fonte oficial de qualquer forma.
  3. IBGE (rede, sem chave): o ponto cai DENTRO da malha do município que o
     cooperado declarou? API pública de localidades + malhas v3.
  4. EARTH ENGINE (precisa de GOOGLE_EARTH_ENGINE_KEY_JSON): classe de
     cobertura MapBiomas no ponto e % de vegetação nativa num CÍRCULO com a
     área declarada em volta do ponto.

LIMITES DECLARADOS (vão na resposta):
  • O círculo NÃO é o perímetro do imóvel — é uma estimativa indicativa. Para
    medir de verdade, a propriedade precisa da geometria (KML/Shapefile do
    SICAR) no Link Verde, que roda o cruzamento completo sobre o polígono.
  • Bioma perto da divisa (±500 m da simplificação, e a própria escala
    1:250.000) pode sair do lado errado — o aviso diz isso.
  • Nada aqui levanta exceção: falta de rede/credencial vira `consultado:
    False` + motivo — nunca um "zero" que parece medição.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from app.services.util_br import normalizar_texto

# Caixa do território brasileiro, com folga, incluindo as ilhas oceânicas
# (Fernando de Noronha ~ -32,4° de longitude; Trindade ~ -29,3°).
LAT_MIN, LAT_MAX = -33.80, 5.30
LON_MIN, LON_MAX = -74.10, -28.80

_IBGE_LOCALIDADES = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
_IBGE_MALHA = ("https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{codigo}"
               "?formato=application/vnd.geo%2Bjson&qualidade=intermediaria")
_TIMEOUT = float(os.getenv("IBGE_TIMEOUT_S", "12"))
_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 24 * 3600

_ARQ_BIOMAS = Path(__file__).resolve().parents[1] / "dados" / "biomas_ibge_250mil.json.gz"
_BIOMAS: Optional[dict] = None

AVISO_BIOMA = ("Mapa de Biomas do IBGE, escala 1:250.000, simplificado a ~500 m. Perto da "
               "divisa entre biomas o resultado pode inverter — confira no mapa oficial.")
AVISO_BUFFER = ("Estimativa INDICATIVA: círculo com a área declarada, centrado no ponto "
                "marcado — não é o perímetro do imóvel. Para medir, envie a geometria "
                "(KML/Shapefile do SICAR) na jornada Link Verde.")


# ── 1. Offline ───────────────────────────────────────────────────────────────

def parse_coordenada(texto, eixo: Optional[str] = None) -> Optional[float]:
    """
    '-23.5475' | '-23,5475' | "23°32'51\" S" | "23 32 51,2 S" | "S 23°32'51\"".
    `eixo` ('lat'|'lon') só desempata hemisfério ausente em DMS — sem letra de
    hemisfério, o sinal escrito vale como está.
    """
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        v = float(texto)
        return v if math.isfinite(v) else None
    t = str(texto).strip().upper().replace(",", ".")
    if not t:
        return None
    hemisferio = None
    m_h = re.search(r"\b([NSLEOW])\b|([NSLEOW])$|^([NSLEOW])", t)
    if m_h:
        hemisferio = next(g for g in m_h.groups() if g)
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", t)]
    if not nums:
        return None
    if len(nums) == 1 and not re.search(r"[°º'\"]", t):
        v = nums[0]
    else:
        g = nums[0]
        mi = nums[1] if len(nums) > 1 else 0.0
        se = nums[2] if len(nums) > 2 else 0.0
        if mi >= 60 or se >= 60:
            return None
        v = abs(g) + mi / 60.0 + se / 3600.0
        if g < 0:
            v = -v
    if hemisferio in ("S", "O", "W") and v > 0:
        v = -v
    elif hemisferio in ("N", "L", "E") and v < 0:
        v = -v
    elif hemisferio is None and eixo == "lon" and v > 0 and len(nums) > 1:
        # DMS sem hemisfério: no Brasil a longitude é sempre oeste.
        v = -v
    return v if math.isfinite(v) else None


def dentro_do_brasil(lat: float, lon: float) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def validar_coordenada(lat: Optional[float], lon: Optional[float]) -> dict[str, Any]:
    """Diagnóstico da coordenada — nunca levanta."""
    if lat is None or lon is None:
        return {"valida": False, "motivo": "sem_coordenada", "dentro_brasil": False, "trocada": False}
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return {"valida": False, "motivo": "nao_numerica", "dentro_brasil": False, "trocada": False}
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        return {"valida": False, "motivo": "fora_do_intervalo", "dentro_brasil": False, "trocada": False}
    if lat == 0 and lon == 0:
        return {"valida": False, "motivo": "zerada", "dentro_brasil": False, "trocada": False}
    if dentro_do_brasil(lat, lon):
        return {"valida": True, "motivo": None, "dentro_brasil": True, "trocada": False,
                "latitude": lat, "longitude": lon}
    if dentro_do_brasil(lon, lat):
        return {"valida": False, "motivo": "lat_lon_trocadas", "dentro_brasil": False,
                "trocada": True, "sugestao": {"latitude": lon, "longitude": lat}}
    return {"valida": False, "motivo": "fora_do_brasil", "dentro_brasil": False, "trocada": False}


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine (raio médio 6371 km)."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _anel_contem(lon: float, lat: float, anel: list) -> bool:
    dentro = False
    n = len(anel)
    j = n - 1
    for i in range(n):
        xi, yi = anel[i][0], anel[i][1]
        xj, yj = anel[j][0], anel[j][1]
        if (yi > lat) != (yj > lat):
            x_corte = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi
            if lon < x_corte:
                dentro = not dentro
        j = i
    return dentro


def ponto_em_geometria(lat: float, lon: float, geometria: dict) -> bool:
    """Ray casting sobre GeoJSON Polygon/MultiPolygon (com buracos)."""
    tipo = (geometria or {}).get("type")
    coords = (geometria or {}).get("coordinates") or []
    poligonos = [coords] if tipo == "Polygon" else coords if tipo == "MultiPolygon" else []
    for poli in poligonos:
        if not poli:
            continue
        if _anel_contem(lon, lat, poli[0]) and not any(
                _anel_contem(lon, lat, buraco) for buraco in poli[1:]):
            return True
    return False


def buffer_circular(lat: float, lon: float, area_ha: float, lados: int = 48) -> dict:
    """Polígono (GeoJSON) aproximando um círculo de `area_ha` em volta do ponto."""
    raio_m = math.sqrt(max(area_ha, 0.01) * 10_000.0 / math.pi)
    dlat = raio_m / 111_320.0
    dlon = raio_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    anel = [[lon + dlon * math.cos(2 * math.pi * k / lados),
             lat + dlat * math.sin(2 * math.pi * k / lados)] for k in range(lados)]
    anel.append(anel[0])
    return {"type": "Polygon", "coordinates": [anel]}


# ── 2. Bioma (mapa do IBGE embarcado) ────────────────────────────────────────

def _biomas() -> dict:
    global _BIOMAS
    if _BIOMAS is None:
        with gzip.open(_ARQ_BIOMAS, "rt", encoding="utf-8") as f:
            _BIOMAS = json.load(f)
    return _BIOMAS


def bioma_ibge(lat: float, lon: float) -> dict[str, Any]:
    """Bioma oficial (IBGE 1:250.000) no ponto. Offline; nunca levanta.

    Cada bioma é uma lista de anéis e o teste é par-ou-ímpar sobre todos eles,
    o que já trata ilhas e buracos sem precisar classificar anel externo/interno.
    """
    try:
        doc = _biomas()
    except Exception as e:  # noqa: BLE001
        return {"consultado": False, "motivo": f"Mapa de biomas indisponível: {e}"}
    achados = []
    for b in doc["biomas"]:
        x0, y0, x1, y1 = b["bbox"]
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        dentro = False
        for anel in b["aneis"]:
            if _anel_contem(lon, lat, anel):
                dentro = not dentro
        if dentro:
            achados.append(b["bioma"])
    base = {"consultado": True, "fonte": doc.get("fonte"), "aviso": AVISO_BIOMA}
    if not achados:
        return {**base, "bioma": None,
                "observacao": "Ponto fora dos limites do mapa de biomas (mar ou borda do mapa)."}
    saida = {**base, "bioma": achados[0]}
    if len(achados) > 1:  # sobreposição por simplificação na divisa
        saida["ambiguo"] = achados
    return saida


# ── 3. IBGE (rede) ───────────────────────────────────────────────────────────

def _decodificar(corpo: bytes) -> Any:
    """JSON da resposta, descompactando gzip quando o servidor manda assim.

    A partir da Vercel, a API de malhas do IBGE responde gzip mesmo sem o
    cliente pedir, e o urllib não descompacta sozinho: a checagem de município
    caía sempre em "IBGE indisponível" (byte 0x8b). Localmente não aparecia.
    """
    if corpo[:2] == b"\x1f\x8b":
        corpo = gzip.decompress(corpo)
    return json.loads(corpo.decode("utf-8"))


def _get_json(url: str) -> Any:
    hit = _CACHE.get(url)
    if hit and hit[0] > time.time():
        return hit[1]
    req = urllib.request.Request(url, headers={"User-Agent": "MBV-Cooperativas/0.1",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        dado = _decodificar(r.read())
    _CACHE[url] = (time.time() + _TTL, dado)
    return dado


def municipio_ibge(nome: str, uf: str) -> Optional[dict]:
    """{codigo, nome} do município pelo nome (sem acento/caixa) na UF."""
    alvo = normalizar_texto(nome)
    lista = _get_json(_IBGE_LOCALIDADES.format(uf=urllib.parse.quote((uf or "").upper())))
    for m in lista or []:
        if normalizar_texto(m.get("nome")) == alvo:
            return {"codigo": m["id"], "nome": m["nome"]}
    return None


def checar_municipio(lat: float, lon: float, municipio: Optional[str],
                     uf: Optional[str]) -> dict[str, Any]:
    """O ponto cai dentro da malha do município declarado? Nunca levanta."""
    if not municipio or not uf:
        return {"consultado": False, "motivo": "Município/UF não informados."}
    try:
        mun = municipio_ibge(municipio, uf)
        if not mun:
            return {"consultado": True, "dentro": None, "municipio_encontrado": False,
                    "motivo": f"'{municipio}' não encontrado na lista do IBGE para {uf}."}
        malha = _get_json(_IBGE_MALHA.format(codigo=mun["codigo"]))
        feats = (malha or {}).get("features") or []
        geom = feats[0]["geometry"] if feats else malha
        dentro = ponto_em_geometria(lat, lon, geom)
        return {"consultado": True, "dentro": dentro, "municipio_encontrado": True,
                "codigo_ibge": mun["codigo"], "municipio_ibge": mun["nome"],
                "fonte": "IBGE — API de malhas territoriais v3 (qualidade intermediária)"}
    except Exception as e:  # noqa: BLE001 — rede nunca derruba a avaliação
        return {"consultado": False, "motivo": f"IBGE indisponível: {e}"}


# ── 4. Earth Engine (cobertura MapBiomas) ────────────────────────────────────

def consultar_mapbiomas_ponto(lat: float, lon: float, *, area_ha: Optional[float] = None,
                              ano: Optional[int] = None) -> dict[str, Any]:
    """Cobertura MapBiomas no ponto e % nativa no círculo. Nunca levanta."""
    from app.services import vegetacao_nativa as vn

    ok, motivo = vn.configurado()
    if not ok:
        return {"consultado": False, "motivo": motivo}
    ano_c, aviso_ano = vn._ano_valido(int(ano or vn.ANO_PADRAO))
    try:
        ee = vn._inicializar_ee()
    except Exception as e:  # noqa: BLE001
        return {"consultado": False, "motivo": f"Falha ao iniciar o Earth Engine: {e}"}

    ponto = ee.Geometry.Point([lon, lat])
    saida: dict[str, Any] = {"consultado": True, "ano": ano_c,
                             "fonte": "MapBiomas Coleção 10.1 (30 m)",
                             "aviso": vn.AVISO_LIMITACAO}
    if aviso_ano:
        saida["ano_ajustado"] = aviso_ano
    try:
        lulc = ee.Image(vn.ASSET_LULC_MAPBIOMAS).select(f"classification_{ano_c}")
        valor = lulc.reduceRegion(ee.Reducer.first(), ponto, 30).getInfo() or {}
        cod = next(iter(valor.values()), None)
        if cod is not None:
            cod = int(cod)
            saida["cobertura_ponto"] = {"codigo": cod,
                                        "rotulo": vn._LEGENDA_LULC.get(cod, f"classe {cod}"),
                                        "grupo": vn._grupo_cobertura(cod)}
        else:
            saida["cobertura_ponto"] = None
        if area_ha and area_ha > 0:
            circulo = buffer_circular(lat, lon, float(area_ha))
            grupos = vn._areas_por_codigo(ee, lulc.rename("codigo"), ee.Geometry(circulo))
            total = sum(float(g["sum"]) for g in grupos)
            por_grupo: dict[str, float] = {}
            for g in grupos:
                gr = vn._grupo_cobertura(int(g["codigo"]))
                por_grupo[gr] = por_grupo.get(gr, 0.0) + float(g["sum"])
            if total > 0:
                saida["buffer"] = {
                    "area_ha": round(total / 10_000.0, 2),
                    "vegetacao_nativa_pct": round(por_grupo.get("nativa", 0.0) / total * 100, 1),
                    "antropico_pct": round(por_grupo.get("antropico", 0.0) / total * 100, 1),
                    "aviso": AVISO_BUFFER,
                }
            else:
                saida["buffer"] = {"sem_dados": True, "aviso": AVISO_BUFFER}
    except Exception as e:  # noqa: BLE001
        saida["cobertura_erro"] = f"Falha na consulta ao MapBiomas: {e}"
    return saida


# ── Orquestração ─────────────────────────────────────────────────────────────

def checar_localizacao(lat: Optional[float], lon: Optional[float], *,
                       municipio: Optional[str] = None, uf: Optional[str] = None,
                       area_ha: Optional[float] = None,
                       consultar_rede: bool = True) -> dict[str, Any]:
    """Tudo o que dá para checar sobre o ponto do cadastro. Nunca levanta."""
    coord = validar_coordenada(lat, lon)
    saida: dict[str, Any] = {"coordenada": coord}
    if not coord["valida"]:
        nao = {"consultado": False, "motivo": "coordenada inválida"}
        return {**saida, "bioma": nao, "municipio": nao, "mapbiomas": nao}
    # bioma é offline: roda mesmo sem rede
    saida["bioma"] = bioma_ibge(coord["latitude"], coord["longitude"])
    if not consultar_rede:
        nao = {"consultado": False, "motivo": "rede desligada"}
        return {**saida, "municipio": nao, "mapbiomas": nao}
    saida["municipio"] = checar_municipio(coord["latitude"], coord["longitude"], municipio, uf)
    saida["mapbiomas"] = consultar_mapbiomas_ponto(coord["latitude"], coord["longitude"],
                                                   area_ha=area_ha)
    return saida
