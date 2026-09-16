"""
Geometria da propriedade rural — parsing de KML/Shapefile em GeoJSON (funções
puras, sem I/O de rede/banco).

Fonte: o produtor rural baixa o arquivo de geometria do seu próprio imóvel na
consulta pública do SICAR (https://consulta.car.gov.br/publico/imoveis/index —
sem login, sem custo) e sobe no Link Verde como o documento tipo "geometria".
Não usamos a API institucional do SICAR (SICAR-Tema/Conecta gov.br) porque
ela exige cadastro de órgão/empresa e liberação de IP — fora do alcance de um
upload feito na hora pelo usuário.

Dois formatos aceitos:
  • KML — XML (parseado com defusedxml, evita XXE de arquivo não confiável).
  • Shapefile — .zip contendo .shp/.shx/.dbf (lido com pyshp, puro Python).
    O zip do SICAR normalmente traz VÁRIAS camadas (área do imóvel, reserva
    legal, APP, vegetação nativa...); preferimos a camada de ÁREA DO IMÓVEL
    (perímetro do imóvel) para a estatística de vegetação cobrir a propriedade
    inteira — busca por nome de arquivo, com fallback pro maior polígono.

O resultado é sempre GeoJSON (RFC 7946), coordenadas em WGS84 (lon, lat) —
formato que `vegetacao_nativa.py` consome direto no Earth Engine.

Isto é uma CONVERSÃO DE FORMATO, não uma fonte de verdade fundiária: a
geometria vale o que o arquivo que o produtor subiu vale — mesmo espírito de
`link_verde.validar_documento` (triagem, não substitui conferência humana).
"""

import io
import zipfile
from typing import Any, Optional

# Nomes de camada que o SICAR costuma usar para o perímetro do imóvel —
# ordem de preferência (a primeira que bater com um `.shp` do zip é usada).
_NOMES_AREA_IMOVEL = (
    "area_imovel", "areaimovel", "perimetro", "imovel",
)


class GeometriaInvalida(ValueError):
    pass


def _anel_para_lonlat(coords_texto: str) -> list[list[float]]:
    """Converte o texto de <coordinates> do KML ('lon,lat,alt lon,lat,alt ...')
    numa lista [[lon, lat], ...]."""
    anel = []
    for grupo in coords_texto.split():
        partes = grupo.split(",")
        if len(partes) < 2:
            continue
        lon, lat = float(partes[0]), float(partes[1])
        anel.append([lon, lat])
    return anel


def kml_para_geojson(conteudo: bytes) -> dict[str, Any]:
    """Extrai o(s) primeiro(s) Polygon/MultiGeometry de um KML → GeoJSON
    Polygon ou MultiPolygon. Levanta GeometriaInvalida se não achar nenhum
    polígono."""
    try:
        from defusedxml import ElementTree as ET
    except Exception as e:
        raise GeometriaInvalida(f"Parser XML indisponível: {e}")

    try:
        raiz = ET.fromstring(conteudo)
    except Exception as e:
        raise GeometriaInvalida(f"KML inválido (XML malformado): {e}")

    ns = {"kml": "http://www.opengis.net/kml/2.2"}

    def _find_all(tag):
        # aceita KML com ou sem namespace declarado
        achados = raiz.findall(f".//kml:{tag}", ns)
        if not achados:
            achados = raiz.findall(f".//{tag}")
        return achados

    poligonos = []
    for poly_el in _find_all("Polygon"):
        outer = poly_el.find(".//kml:outerBoundaryIs//kml:coordinates", ns)
        if outer is None:
            outer = poly_el.find(".//outerBoundaryIs//coordinates")
        if outer is None or not (outer.text or "").strip():
            continue
        anel_ext = _anel_para_lonlat(outer.text)
        if len(anel_ext) < 3:
            continue
        aneis = [anel_ext]
        for inner in poly_el.findall(".//kml:innerBoundaryIs//kml:coordinates", ns) or \
                     poly_el.findall(".//innerBoundaryIs//coordinates"):
            if (inner.text or "").strip():
                anel_int = _anel_para_lonlat(inner.text)
                if len(anel_int) >= 3:
                    aneis.append(anel_int)
        poligonos.append(aneis)

    if not poligonos:
        raise GeometriaInvalida("Nenhum <Polygon> encontrado no KML — confira "
                                "se é o arquivo de geometria (não o recibo em PDF).")

    if len(poligonos) == 1:
        return {"type": "Polygon", "coordinates": poligonos[0]}
    return {"type": "MultiPolygon", "coordinates": poligonos}


def _dbf_texto(shape_reader, indice: int) -> str:
    try:
        rec = shape_reader.record(indice)
        return " ".join(str(v) for v in rec).lower()
    except Exception:
        return ""


def shapefile_zip_para_geojson(conteudo: bytes) -> dict[str, Any]:
    """Lê um .zip com .shp/.shx/.dbf (padrão de download do SICAR) e devolve
    o polígono da ÁREA DO IMÓVEL em GeoJSON. Se houver várias camadas/shapes
    no zip, prioriza a de perímetro do imóvel (por nome de arquivo/atributo);
    sem isso, usa o polígono de maior área como aproximação."""
    try:
        import shapefile  # pyshp
    except Exception as e:
        raise GeometriaInvalida(f"Parser de shapefile indisponível: {e}")

    try:
        zf = zipfile.ZipFile(io.BytesIO(conteudo))
    except Exception as e:
        raise GeometriaInvalida(f"Arquivo não é um .zip válido: {e}")

    shp_names = [n for n in zf.namelist() if n.lower().endswith(".shp")]
    if not shp_names:
        raise GeometriaInvalida("O .zip não contém nenhum arquivo .shp — confira "
                                "se é o pacote de shapefile baixado do SICAR.")

    # prioriza o .shp cujo nome sugere o perímetro do imóvel
    shp_names.sort(key=lambda n: next(
        (i for i, alvo in enumerate(_NOMES_AREA_IMOVEL) if alvo in n.lower()),
        len(_NOMES_AREA_IMOVEL)))

    melhor_geojson: Optional[dict] = None
    melhor_area = -1.0

    for shp_name in shp_names:
        base = shp_name[:-4]
        try:
            shp = io.BytesIO(zf.read(base + ".shp"))
            shx_name = next((n for n in zf.namelist() if n.lower() == (base + ".shx").lower()), None)
            dbf_name = next((n for n in zf.namelist() if n.lower() == (base + ".dbf").lower()), None)
            shx = io.BytesIO(zf.read(shx_name)) if shx_name else None
            dbf = io.BytesIO(zf.read(dbf_name)) if dbf_name else None
            leitor = shapefile.Reader(shp=shp, shx=shx, dbf=dbf)
        except Exception:
            continue

        if leitor.shapeType not in (
            shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM
        ):
            continue

        for i, shape in enumerate(leitor.shapes()):
            if not shape.points:
                continue
            geojson = shape.__geo_interface__  # já normaliza pra GeoJSON
            area = _area_planar_aprox_ha(geojson)
            # nome de arquivo bateu com "área do imóvel"? usa direto.
            if any(alvo in base.lower() for alvo in _NOMES_AREA_IMOVEL):
                return geojson
            if area > melhor_area:
                melhor_area, melhor_geojson = area, geojson

    if melhor_geojson is None:
        raise GeometriaInvalida("Nenhum polígono válido encontrado nos shapefiles do .zip.")
    return melhor_geojson


def extrair_geometria(nome_arquivo: str, conteudo: bytes) -> dict[str, Any]:
    """Dispatcher por extensão/assinatura de arquivo. Levanta GeometriaInvalida
    se não reconhecer o formato ou não achar polígono."""
    nome = (nome_arquivo or "").lower()
    if nome.endswith(".zip") or conteudo[:2] == b"PK":
        return shapefile_zip_para_geojson(conteudo)
    if nome.endswith((".kml", ".xml")) or conteudo.lstrip()[:5] in (b"<?xml", b"<kml "):
        return kml_para_geojson(conteudo)
    raise GeometriaInvalida("Formato não reconhecido — envie um .kml ou um .zip "
                            "de shapefile (baixados da consulta pública do SICAR).")


def _area_planar_aprox_ha(geojson: dict[str, Any]) -> float:
    """Área aproximada em hectares por projeção planar simples (shoelace com
    graus escalados por cos(latitude média)) — suficiente para CONFERÊNCIA
    (bate com a área declarada no cadastro?), NÃO para uso legal/cartográfico
    preciso (isso exigiria projeção UTM/geodésica de verdade)."""
    def _anel_area_graus(anel: list[list[float]]) -> float:
        n = len(anel)
        if n < 3:
            return 0.0
        soma = 0.0
        for i in range(n):
            x1, y1 = anel[i]
            x2, y2 = anel[(i + 1) % n]
            soma += x1 * y2 - x2 * y1
        return abs(soma) / 2.0

    tipo = geojson.get("type")
    coords = geojson.get("coordinates") or []
    poligonos = coords if tipo == "MultiPolygon" else [coords]

    lats = [pt[1] for poly in poligonos for anel in poly for pt in anel]
    if not lats:
        return 0.0
    lat_media = sum(lats) / len(lats)
    import math
    m_por_grau_lat = 111_320.0
    m_por_grau_lon = 111_320.0 * math.cos(math.radians(lat_media))

    area_m2 = 0.0
    for poly in poligonos:
        if not poly:
            continue
        externo, *buracos = poly
        area_graus = _anel_area_graus(externo)
        for buraco in buracos:
            area_graus -= _anel_area_graus(buraco)
        area_m2 += area_graus * m_por_grau_lat * m_por_grau_lon

    return round(area_m2 / 10_000.0, 2)


def resumo_geometria(geojson: dict[str, Any]) -> dict[str, Any]:
    """Metadados leves pra exibir no cadastro sem precisar do Earth Engine:
    tipo, nº de polígonos/vértices e a área aproximada (triagem)."""
    tipo = geojson.get("type")
    coords = geojson.get("coordinates") or []
    poligonos = coords if tipo == "MultiPolygon" else [coords]
    n_poligonos = len(poligonos)
    n_vertices = sum(len(anel) for poly in poligonos for anel in poly)
    return {
        "tipo": tipo,
        "poligonos": n_poligonos,
        "vertices": n_vertices,
        "area_aproximada_ha": _area_planar_aprox_ha(geojson),
        "aviso": ("Área calculada por projeção planar simples — é uma "
                 "conferência (bate com o cadastro?), não uma medição "
                 "cartográfica oficial."),
    }
