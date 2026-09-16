"""
Utilitários brasileiros (funções puras): CPF, nomes, datas e números como
aparecem em documentos de cooperado — foto de RG, extrato do CAF, laudo
técnico digitado por engenheiro, recibo do CAR.

Tudo aqui é determinístico e testado (tests/test_util_br.py). A IA TRANSCREVE
o documento como texto; é este módulo que interpreta o texto em número/data —
assim a conversão é auditável e não depende do modelo.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Optional

UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
       "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
       "SP", "SE", "TO"}

# Amazônia Legal (Lei 12.651/2012, art. 3º, I): AC, AM, AP, PA, RO, RR, TO,
# MT e o Maranhão A OESTE do meridiano de 44° W. Bioma ≠ Amazônia Legal: o
# Cerrado de MT/TO/MA está DENTRO dela (RL 35%), e o ERP já teve bug por
# inferir o percentual só pelo bioma.
UFS_AMAZONIA_LEGAL = {"AC", "AM", "AP", "PA", "RO", "RR", "TO", "MT", "MA"}
MERIDIANO_MA = -44.0

BIOMAS = ("Amazônia", "Cerrado", "Mata Atlântica", "Caatinga", "Pampa", "Pantanal")

_MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
          "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
          "novembro": 11, "dezembro": 12}

_PARTICULAS = {"de", "da", "do", "das", "dos", "e", "d"}


# ── Texto ────────────────────────────────────────────────────────────────────

def normalizar_texto(s: Optional[str]) -> str:
    """minúsculas, sem acento, espaços colapsados."""
    t = unicodedata.normalize("NFKD", str(s or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", t).strip()


def so_digitos(s: Optional[str]) -> str:
    return re.sub(r"\D", "", str(s or ""))


# ── CPF ──────────────────────────────────────────────────────────────────────

def cpf_valido(cpf: Optional[str]) -> bool:
    """Confere os dígitos verificadores. Sequências repetidas são inválidas."""
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        dv = (soma * 10) % 11
        if dv == 10:
            dv = 0
        if dv != int(d[n]):
            return False
    return True


def cnpj_valido(cnpj: Optional[str]) -> bool:
    """Dígitos verificadores do CNPJ (cooperado pessoa jurídica)."""
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False

    def dv(base: str, pesos: list[int]) -> str:
        r = sum(int(a) * b for a, b in zip(base, pesos)) % 11
        return "0" if r < 2 else str(11 - r)
    pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = dv(d[:12], pesos)
    return d[12:] == d1 + dv(d[:12] + d1, [6] + pesos)


def formatar_cnpj(cnpj: Optional[str]) -> str:
    d = so_digitos(cnpj)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else (cnpj or "")


def formatar_cpf(cpf: Optional[str]) -> str:
    d = so_digitos(cpf)
    if len(d) != 11:
        return str(cpf or "")
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"


def mascarar_cpf(cpf: Optional[str]) -> str:
    """***.456.789-** — para telas e mensagens (LGPD: minimização)."""
    d = so_digitos(cpf)
    if len(d) != 11:
        return ""
    return f"***.{d[3:6]}.{d[6:9]}-**"


# ── Nomes ────────────────────────────────────────────────────────────────────

def tokens_nome(s: Optional[str]) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", normalizar_texto(s))
            if t and t not in _PARTICULAS]


def _token_casa(a: str, b: str) -> bool:
    # inicial abreviada ("S." ↔ "Silva") casa com o nome completo
    if len(a) == 1 or len(b) == 1:
        return a[0] == b[0]
    return a == b


def nomes_compativeis(a: Optional[str], b: Optional[str]) -> Optional[bool]:
    """
    O mesmo nome escrito de formas diferentes? None se faltar um dos dois.

    Exige o MESMO primeiro nome e que ≥75% dos termos do nome mais curto
    apareçam no mais longo (tolera sobrenome omitido e inicial abreviada —
    comum em RG antigo e em laudo). Não é prova de identidade: quem prova é o
    CPF; nome só serve de alerta quando o CPF não foi transcrito.
    """
    ta, tb = tokens_nome(a), tokens_nome(b)
    if not ta or not tb:
        return None
    if not _token_casa(ta[0], tb[0]):
        return False
    menor, maior = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    casados = sum(1 for t in menor if any(_token_casa(t, u) for u in maior))
    return casados / len(menor) >= 0.75


# ── Datas ────────────────────────────────────────────────────────────────────

def parse_data(s: Optional[str]) -> Optional[date]:
    """'2024-03-10', '10/03/2024', '10.03.2024', '10/03/24', '10 de março de 2024'."""
    t = normalizar_texto(s)
    if not t:
        return None
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        a, mm, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b", t)
        if m:
            d, mm, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a < 100:
                a += 2000
        else:
            m = re.search(r"\b(\d{1,2})\s*(?:de\s+)?([a-z]+)\s*(?:de\s+)?(\d{4})\b", t)
            if not m or m.group(2) not in _MESES:
                return None
            d, mm, a = int(m.group(1)), _MESES[m.group(2)], int(m.group(3))
    try:
        return date(a, mm, d)
    except ValueError:
        return None


# ── Números ──────────────────────────────────────────────────────────────────

def parse_numero(s) -> Optional[float]:
    """
    Número como aparece no documento: '1.234,56 ha', '12,5', '1234.56',
    '1.234' (milhar). Com '.' e ',' juntos, o ÚLTIMO separador é o decimal.
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    m = re.search(r"-?\d[\d.,]*", t)
    if not m:
        return None
    n = m.group(0).rstrip(".,")
    if "," in n and "." in n:
        if n.rfind(",") > n.rfind("."):
            n = n.replace(".", "").replace(",", ".")
        else:
            n = n.replace(",", "")
    elif "," in n:
        n = n.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", n):
        n = n.replace(".", "")
    try:
        return float(n)
    except ValueError:
        return None


# ── Bioma / Amazônia Legal ───────────────────────────────────────────────────

def normalizar_bioma(s: Optional[str]) -> Optional[str]:
    t = normalizar_texto(s)
    if not t:
        return None
    for nome in BIOMAS:
        if normalizar_texto(nome) in t:
            return nome
    if "atlantica" in t:
        return "Mata Atlântica"
    return None


def em_amazonia_legal(uf: Optional[str], lon: Optional[float] = None) -> Optional[bool]:
    """True/False, ou None quando não dá para saber (MA sem longitude)."""
    u = (uf or "").strip().upper()
    if u not in UFS:
        return None
    if u != "MA":
        return u in UFS_AMAZONIA_LEGAL
    if lon is None:
        return None
    return lon < MERIDIANO_MA
