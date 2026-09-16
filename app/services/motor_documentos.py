"""
app/services/motor_documentos.py

Motor de regularidade documental da propriedade (Módulo 1) — FUNÇÃO PURA.

Responde a duas perguntas que a Primato faz todo dia:

  1. "O que vence, e quando?"  → `situacao_documento` / `alertas`
  2. "Esta propriedade está regular?" → `avaliar_propriedade`

Três decisões de projeto que valem mais que o código:

  • **Vencer e desatualizar não são a mesma coisa.** Uma outorga vencida é uma
    irregularidade: a captação de água perdeu amparo. Um CCIR do exercício
    anterior está DESATUALIZADO — nada ilegal aconteceu, só falta emitir o do
    ano. Misturar os dois no mesmo alerta vermelho ensina o usuário a ignorar
    alerta vermelho.

  • **Documento condicional só é cobrado quando a condição existe.** Cobrar
    outorga de quem é de sequeiro produz pendência falsa, e pendência falsa
    derruba o Índice de Fidelização de quem não deve nada.

  • **Ausência de data não vira "em dia".** Documento que vence sem data de
    validade cadastrada fica `sem_data` — pendência informativa, nunca
    silêncio. O silêncio é o modo de falha caro: o sistema diz "tudo certo"
    porque não sabe de nada.

Nada aqui toca banco, rede ou relógio do sistema: a data de referência entra
por parâmetro. É o que torna o vencimento testável sem esperar o calendário.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

from app.services import catalogo_documentos as cat

# Situações possíveis de um documento, da pior para a melhor.
# A ordem É a regra de precedência usada em `pior_situacao`.
SITUACOES = (
    "vencido",       # passou da validade — irregularidade
    "ausente",       # exigido e nunca enviado
    "desatualizado", # periódico de exercício anterior
    "sem_data",      # vence, mas ninguém cadastrou a validade
    "a_vencer",      # dentro da janela de aviso
    "em_dia",        # vigente e fora da janela
    "nao_exigido",   # condicional cuja condição não se aplica
)

_ORDEM = {s: i for i, s in enumerate(SITUACOES)}

# Texto ao usuário. Sem código de regra, sem jargão — a tela do técnico de
# campo e a do gestor leem a mesma frase.
ROTULO_SITUACAO = {
    "vencido": "Vencido",
    "ausente": "Não enviado",
    "desatualizado": "Exercício anterior",
    "sem_data": "Sem data de validade",
    "a_vencer": "Vence em breve",
    "em_dia": "Em dia",
    "nao_exigido": "Não se aplica",
}

# Só estas travam a regularidade da propriedade.
SITUACOES_IRREGULARES = ("vencido", "ausente")


def _dias_ate(validade: Optional[date], hoje: date) -> Optional[int]:
    if not validade:
        return None
    return (validade - hoje).days


def situacao_documento(doc: dict, *, hoje: date,
                       exercicio_corrente: Optional[int] = None) -> dict:
    """
    Avalia UM documento já cadastrado.

    `doc` aceita: tipo, validade (date|None), exercicio (int|None).
    Devolve dict com situacao, rótulo, dias_restantes e a frase de ação.
    """
    tipo = doc.get("tipo") or ""
    meta = cat.DOCUMENTOS.get(tipo, {})
    validade = doc.get("validade")
    exercicio = doc.get("exercicio")

    # Periódico (CCIR, ITR): compara exercício, não data.
    if meta.get("periodico"):
        ano_alvo = exercicio_corrente if exercicio_corrente is not None else hoje.year
        # O exercício corrente só é cobrável depois que há prazo para emitir;
        # até lá o documento do ano anterior é o documento em vigor.
        if exercicio is None:
            situacao = "sem_data"
        elif int(exercicio) >= ano_alvo - 1:
            situacao = "em_dia"
        else:
            situacao = "desatualizado"
        return _montar(tipo, situacao, None, meta, exercicio=exercicio)

    if not meta.get("vence"):
        return _montar(tipo, "em_dia", None, meta)

    dias = _dias_ate(validade, hoje)
    if dias is None:
        situacao = "sem_data"
    elif dias < 0:
        situacao = "vencido"
    elif dias <= cat.alerta_dias(tipo):
        situacao = "a_vencer"
    else:
        situacao = "em_dia"
    return _montar(tipo, situacao, dias, meta, validade=validade)


def _montar(tipo: str, situacao: str, dias: Optional[int], meta: dict,
            *, validade: Optional[date] = None,
            exercicio: Optional[int] = None) -> dict:
    return {
        "tipo": tipo,
        "rotulo": meta.get("rotulo", tipo),
        "grupo": meta.get("grupo"),
        "situacao": situacao,
        "situacao_rotulo": ROTULO_SITUACAO[situacao],
        "dias_restantes": dias,
        "validade": validade.isoformat() if validade else None,
        "exercicio": exercicio,
        "acao": _acao(tipo, situacao, dias, meta, exercicio),
    }


def _acao(tipo: str, situacao: str, dias: Optional[int], meta: dict,
          exercicio: Optional[int]) -> Optional[str]:
    """A frase que diz ao associado o que fazer. Vale mais que o status."""
    rot = meta.get("rotulo", tipo)
    if situacao == "vencido":
        n = abs(dias) if dias is not None else None
        quando = f" há {n} dia{'s' if n != 1 else ''}" if n is not None else ""
        return f"{rot} venceu{quando}. Providencie a renovação."
    if situacao == "ausente":
        return f"{rot} ainda não foi enviado."
    if situacao == "desatualizado":
        de = f" (o último é de {exercicio})" if exercicio else ""
        return f"Emitir o {rot} do exercício atual{de}."
    if situacao == "sem_data":
        if meta.get("periodico"):
            return f"Informe o exercício do {rot}."
        return f"Informe a data de validade do {rot}."
    if situacao == "a_vencer":
        return (f"{rot} vence em {dias} dia{'s' if dias != 1 else ''}. "
                f"Comece a renovação agora.")
    return None


def pior_situacao(situacoes: Iterable[str]) -> str:
    """A situação mais grave do conjunto — é o que a lista da propriedade mostra."""
    piores = sorted(situacoes, key=lambda s: _ORDEM.get(s, 99))
    return piores[0] if piores else "em_dia"


def _exigivel(tipo: str, contexto: dict) -> bool:
    """Um condicional só é exigido quando o contexto da propriedade o aciona."""
    meta = cat.DOCUMENTOS.get(tipo, {})
    exig = meta.get("exigencia")
    if exig == "obrigatorio":
        return True
    if exig != "condicional":
        return False
    return bool({
        "outorga_agua": contexto.get("possui_captacao_agua"),
        "licenca_ambiental": contexto.get("exige_licenciamento"),
        "contrato_arrendamento": contexto.get("arrendada"),
        "cnpj_contrato_social": contexto.get("pessoa_juridica"),
    }.get(tipo, False))


def avaliar_propriedade(documentos: list[dict], *, hoje: date,
                        contexto: Optional[dict] = None,
                        exercicio_corrente: Optional[int] = None) -> dict:
    """
    Checklist completo da propriedade.

    `contexto` responde às condições do catálogo: possui_captacao_agua,
    exige_licenciamento, arrendada, pessoa_juridica. Campo ausente = falso,
    e o documento condicional correspondente sai como `nao_exigido` — nunca
    como pendência.
    """
    contexto = contexto or {}
    # Do mesmo tipo, vale o mais recente: reenvio de outorga renovada não deve
    # deixar a antiga vencida penando na lista.
    melhor: dict[str, dict] = {}
    for doc in documentos:
        tipo = doc.get("tipo")
        if tipo not in cat.DOCUMENTOS:
            continue
        aval = situacao_documento(doc, hoje=hoje, exercicio_corrente=exercicio_corrente)
        aval["documento_id"] = doc.get("id")
        aval["nome_arquivo"] = doc.get("nome_arquivo")
        atual = melhor.get(tipo)
        if atual is None or _ORDEM[aval["situacao"]] > _ORDEM[atual["situacao"]]:
            melhor[tipo] = aval

    itens: list[dict] = []
    for tipo in cat.DOCUMENTOS:
        if tipo in melhor:
            itens.append(melhor[tipo])
            continue
        if _exigivel(tipo, contexto):
            itens.append(_montar(tipo, "ausente", None, cat.DOCUMENTOS[tipo]))
        else:
            itens.append(_montar(tipo, "nao_exigido", None, cat.DOCUMENTOS[tipo]))

    exigidos = [i for i in itens if i["situacao"] != "nao_exigido"]
    irregulares = [i for i in exigidos if i["situacao"] in SITUACOES_IRREGULARES]
    atencao = [i for i in exigidos
               if i["situacao"] in ("a_vencer", "desatualizado", "sem_data")]
    em_dia = [i for i in exigidos if i["situacao"] == "em_dia"]

    total = len(exigidos) or 1
    return {
        "itens": itens,
        "alertas": sorted(irregulares + atencao,
                          key=lambda i: (_ORDEM[i["situacao"]],
                                         i["dias_restantes"] if i["dias_restantes"] is not None else 9999)),
        "regular": not irregulares,
        "situacao": pior_situacao([i["situacao"] for i in exigidos]) if exigidos else "em_dia",
        "regularidade_pct": round(100.0 * len(em_dia) / total, 1),
        "contagem": {
            "exigidos": len(exigidos),
            "em_dia": len(em_dia),
            "atencao": len(atencao),
            "irregulares": len(irregulares),
        },
        "catalogo_versao": cat.CATALOGO_VERSAO,
    }


def proximos_vencimentos(documentos: list[dict], *, hoje: date,
                         janela_dias: int = 90) -> list[dict]:
    """
    Agenda de vencimentos dos próximos `janela_dias` — inclui o que já venceu,
    porque o vencido é o que mais precisa aparecer na agenda.
    """
    limite = hoje + timedelta(days=janela_dias)
    saida = []
    for doc in documentos:
        validade = doc.get("validade")
        if not validade or not cat.DOCUMENTOS.get(doc.get("tipo"), {}).get("vence"):
            continue
        if validade <= limite:
            aval = situacao_documento(doc, hoje=hoje)
            aval["propriedade_id"] = doc.get("propriedade_id")
            aval["propriedade"] = doc.get("propriedade")
            saida.append(aval)
    return sorted(saida, key=lambda d: d["dias_restantes"])
