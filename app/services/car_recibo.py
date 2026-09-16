r"""
Extração estruturada do Recibo de Inscrição do Imóvel Rural no CAR (funções puras).

O `link_verde._validar_car` faz TRIAGEM (o recibo é um CAR? está ativo?); este
módulo faz EXTRAÇÃO — devolve os campos que as certificadoras de carbono pedem
no cadastro do projeto (área do imóvel, Reserva Legal, APP, remanescente de
vegetação nativa, matrículas, centroide, módulos fiscais).

Escrito e testado contra o texto real de um recibo do SICAR, não contra um
layout imaginado — e contra os DOIS extratores que o projeto usa: `pypdf`
(produção, coluna única com espaço simples) e `pdftotext -layout` (colunas
largas). Por isso todo separador é `\s+`, nunca `\s{2,}`: ancorar em colunas
funcionava só num dos dois. O recibo tem 3 páginas e repete o cabeçalho em
todas — os regex ancoram no rótulo, nunca na posição da linha.

⚠ LIMITE QUE O PRÓPRIO RECIBO DECLARA — e que vale mais que qualquer campo
extraído aqui:

  "As informações prestadas no CAR são de caráter declaratório"
  "A inscrição do Imóvel Rural no CAR não será considerada título para fins
   de reconhecimento de direito de propriedade ou posse"

Ou seja: o CAR é AUTODECLARATÓRIO e não prova titularidade. Nenhum campo que
sai daqui pode ser apresentado a uma certificadora como verificado — todos
saem marcados com `origem: car_declarado`. Quem prova domínio é a matrícula
atualizada; quem prova a Reserva Legal é a análise do órgão ambiental estadual.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# ── Rótulos das áreas declaradas, como aparecem no recibo ────────────────────
# Chave interna → rótulo no PDF. A ordem importa: rótulos mais específicos
# vêm primeiro para "Área Total do Imóvel" não ser capturado por "Área Total".
_ROTULOS_AREA: tuple[tuple[str, str], ...] = (
    ("total",                          r"Área Total \(ha\) do Imóvel Rural"),
    ("total",                          r"Área Total do Imóvel"),
    ("consolidada",                    r"Área Consolidada"),
    ("servidao_administrativa",        r"Área de Servidão Administrativa"),
    ("remanescente_vegetacao_nativa",  r"Remanescente de Vegetação Nativa"),
    ("liquida",                        r"Área Líquida do Imóvel"),
    ("reserva_legal",                  r"Área de Reserva Legal"),
    ("app",                            r"Área de Preservação Permanente"),
    ("uso_restrito",                   r"Área de Uso Restrito"),
)

_UF_POR_NOME: dict[str, str] = {
    "acre": "AC", "alagoas": "AL", "amapá": "AP", "amazonas": "AM",
    "bahia": "BA", "ceará": "CE", "distrito federal": "DF",
    "espírito santo": "ES", "goiás": "GO", "maranhão": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG",
    "pará": "PA", "paraíba": "PB", "paraná": "PR", "pernambuco": "PE",
    "piauí": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondônia": "RO", "roraima": "RR",
    "santa catarina": "SC", "são paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}


def _num(texto: Optional[str], casas: int = 4) -> Optional[float]:
    """Número do recibo → float.

    O recibo mistura os dois formatos na MESMA página: as áreas declaradas vêm
    em pt-BR ("7,0085") e o aviso de divergência traz "14.0 hectares", com
    ponto decimal. Por isso a heurística: vírgula presente = decimal vírgula;
    só ponto = milhar apenas quando os grupos forem de 3 dígitos.

    Mantém 4 casas por padrão — área de CAR tem 4, e arredondar para 2 aqui
    já introduziria divergência contra o documento na conferência humana.
    """
    s = (texto or "").strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return round(float(s), casas)
    except ValueError:
        return None


def _dms_para_decimal(graus: str, minutos: str, segundos: str,
                      hemisferio: str) -> Optional[float]:
    """'27°38'37,56" S' → -27.643767. 'O'/'W' e 'S' são negativos."""
    try:
        g = float(graus)
        m = float((minutos or "0").replace(",", "."))
        s = float((segundos or "0").replace(",", "."))
    except ValueError:
        return None
    dec = g + m / 60.0 + s / 3600.0
    if (hemisferio or "").strip().upper() in ("S", "O", "W"):
        dec = -dec
    return round(dec, 6)


def _buscar(padrao: str, texto: str, grupo: int = 1) -> Optional[str]:
    m = re.search(padrao, texto, re.IGNORECASE)
    return m.group(grupo).strip() if m else None


def _extrair_centroide(texto: str) -> Optional[dict[str, float]]:
    """Latitude/Longitude do centroide, em DMS, para decimal."""
    dms = r"(\d{1,3})\s*°\s*(\d{1,2})\s*'\s*([\d,\.]+)?\s*\"?\s*([NSEOW])"
    lat = re.search(r"Latitude\s*:?\s*" + dms, texto, re.IGNORECASE)
    lon = re.search(r"Longitude\s*:?\s*" + dms, texto, re.IGNORECASE)
    if not lat or not lon:
        return None
    la = _dms_para_decimal(lat.group(1), lat.group(2), lat.group(3) or "0", lat.group(4))
    lo = _dms_para_decimal(lon.group(1), lon.group(2), lon.group(3) or "0", lon.group(4))
    if la is None or lo is None:
        return None
    return {"latitude": la, "longitude": lo}


def _extrair_matriculas(texto: str) -> list[dict[str, Any]]:
    """Bloco 'MATRÍCULAS DAS PROPRIEDADES DO IMÓVEL'.

    O recibo repete a mesma matrícula quando há mais de um titular — dedup por
    (número, data), preservando a ordem de aparição.
    """
    i = texto.upper().find("MATRÍCULAS DAS PROPRIEDADES")
    if i < 0:
        return []
    bloco = texto[i: i + 4000]
    # Separador de 1+ espaços: o `pdftotext -layout` alinha em colunas largas,
    # mas o pypdf (usado em produção) devolve espaço simples. A âncora
    # confiável é a DATA no meio da linha, que o cabeçalho não tem.
    linha = re.compile(
        r"^\s*([\w\-./]+)\s+(\d{2}/\d{2}/\d{4})\s+(\S+)\s+(\S+)\s+(.+?)\s*$",
        re.MULTILINE)
    vistas: set[tuple[str, str]] = set()
    saida: list[dict[str, Any]] = []
    for m in linha.finditer(bloco):
        numero, data = m.group(1), m.group(2)
        if (numero, data) in vistas:
            continue
        vistas.add((numero, data))
        saida.append({
            "numero": numero, "data_documento": data,
            "livro": m.group(3), "folha": m.group(4),
            "cartorio_municipio": m.group(5),
        })
    return saida


def _extrair_divergencia(texto: str) -> Optional[dict[str, Any]]:
    """O recibo declara, quando existe, a divergência entre a área do documento
    de propriedade e a área do polígono. É um achado relevante para a
    certificadora: a área do projeto é a GEOMÉTRICA, não a documental."""
    m = re.search(
        r"declarada.{0,120}?\[\s*([\d.,]+)\s*hectares?\s*\].{0,160}?"
        r"representação gráfica\s*\[\s*([\d.,]+)\s*hectares?\s*\]",
        texto, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    doc, graf = _num(m.group(1)), _num(m.group(2))
    if doc is None or graf is None:
        return None
    return {
        "area_documental_ha": doc,
        "area_grafica_ha": graf,
        "diferenca_ha": round(doc - graf, 4),
        "diferenca_pct": round(100.0 * (doc - graf) / doc, 2) if doc else None,
    }


def extrair_recibo_car(texto: str) -> dict[str, Any]:
    """Recibo do CAR (texto extraído do PDF) → campos estruturados.

    Nunca levanta: o que não for reconhecido volta como None e entra em
    `nao_extraidos`, para a conferência humana saber exatamente o que faltou.
    """
    t = texto or ""
    reconhecido = bool(re.search(
        r"RECIBO DE INSCRIÇÃO DO IMÓVEL RURAL NO CAR|Registro no CAR|"
        r"Cadastro Ambiental Rural", t, re.IGNORECASE))

    registro = _buscar(r"Registro no CAR\s*:?\s*([A-Z]{2}-\d+-[A-F0-9.]+)", t)
    uf_nome = _buscar(r"\bUF\s*:?\s*([A-Za-zÀ-ÿ ]+?)\s*(?:\n|$)", t)
    uf = None
    if registro:
        uf = registro.split("-", 1)[0].upper()
    if not uf and uf_nome:
        uf = _UF_POR_NOME.get(uf_nome.strip().lower())

    areas: dict[str, Optional[float]] = {}
    for chave, rotulo in _ROTULOS_AREA:
        if areas.get(chave) is not None:
            continue                      # já capturado por um rótulo anterior
        areas[chave] = _num(_buscar(rotulo + r"\s*:?\s*([\d.,]+)", t))

    dados: dict[str, Any] = {
        "reconhecido": reconhecido,
        "registro_car": registro,
        "protocolo": _buscar(r"Código do Protocolo\s*:?\s*([A-Z]{2}-\d+-[A-F0-9.]+)", t),
        "data_cadastro": _buscar(r"Data de Cadastro\s*:?\s*(\d{2}/\d{2}/\d{4})", t),
        "nome_imovel": _buscar(r"Nome do Imóvel Rural\s*:?\s*(.+?)\s*(?:\n|$)", t),
        # "Município: X  UF: Y" numa linha só (pypdf) ou em colunas (pdftotext)
        "municipio": _buscar(r"Município\s*:?\s*(.+?)\s+UF\s*:", t)
                     or _buscar(r"Município\s*:?\s*(.+?)\s*(?:\n|$)", t),
        "uf": uf,
        "centroide": _extrair_centroide(t),
        "modulos_fiscais": _num(_buscar(r"Módulos Fiscais\s*:?\s*([\d.,]+)", t)),
        "proprietario_nome": _buscar(r"Nome\s*:?\s*(.+?)\s*(?:\n|$)",
                                     t[t.upper().find("IDENTIFICAÇÃO DO PROPRIETÁRIO"):]
                                     if "IDENTIFICAÇÃO DO PROPRIET" in t.upper() else ""),
        "proprietario_cpf_cnpj": _buscar(r"\bCPF\s*:?\s*([\d.\-/]{11,18})", t)
                                 or _buscar(r"\bCNPJ\s*:?\s*([\d.\-/]{14,18})", t),
        "areas_ha": areas,
        "matriculas": _extrair_matriculas(t),
        "divergencia_area": _extrair_divergencia(t),
    }

    obrigatorios = ("registro_car", "data_cadastro", "municipio", "uf")
    faltando = [c for c in obrigatorios if not dados.get(c)]
    if areas.get("total") is None:
        faltando.append("areas_ha.total")
    if areas.get("reserva_legal") is None:
        faltando.append("areas_ha.reserva_legal")
    dados["nao_extraidos"] = faltando

    avisos = [
        "O CAR é AUTODECLARATÓRIO: o próprio recibo diz que \"as informações "
        "prestadas no CAR são de caráter declaratório\" e que a inscrição "
        "\"não será considerada título para fins de reconhecimento de direito "
        "de propriedade ou posse\". Estes campos entram como DECLARADOS.",
    ]
    if dados["divergencia_area"]:
        d = dados["divergencia_area"]
        avisos.append(
            f"O recibo declara divergência entre a área documental "
            f"({d['area_documental_ha']:.4f} ha) e a do polígono "
            f"({d['area_grafica_ha']:.4f} ha). Para a certificadora vale a "
            f"área GEOMÉTRICA do projeto — resolva a divergência antes de "
            f"submeter.")
    if faltando:
        avisos.append("Não consegui extrair: " + ", ".join(faltando)
                      + " — preencha à mão e confira o recibo.")
    dados["avisos"] = avisos
    return dados
