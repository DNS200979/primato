"""
app/services/catalogo_documentos.py

Catálogo dos documentos de uma propriedade rural da Primato (Módulo 1).

É só DADO — nenhuma regra é aplicada aqui; quem decide vencimento e
regularidade é `motor_documentos.py`. A separação existe porque este catálogo
é RASCUNHO: os prazos e a obrigatoriedade precisam ser validados com a
Diretoria de Insumos Agrícolas da Primato antes de virar alerta na tela de
alguém. Mudar a regra é editar dicionário, não reescrever motor.

Três eixos por documento:

  • `vence`     — o documento tem validade própria (outorga de água, licença
                  ambiental, certidões) ou não (matrícula, contrato social).
  • `periodico` — não "vence", mas precisa ser refeito todo ano (ITR, CCIR).
                  A diferença importa: um ITR de 2024 não está "vencido", está
                  DESATUALIZADO — e a conversa com o associado é outra.
  • `exigencia` — obrigatorio | condicional | opcional. Condicional traz
                  `condicao`, em português, dita ao usuário tal como está aqui.

Fonte: Especificação Funcional — Perfil Regenerativo Digital para a Primato
(Capital Regenerativo, setembro/2026), Módulo 1.
"""

from __future__ import annotations

from typing import Optional

CATALOGO_VERSAO = "0.1-rascunho (2026-09-15)"

# Dias de antecedência do aviso, por tipo. Licença ambiental avisa com muita
# folga de propósito: renovar licença de operação leva meses no órgão estadual,
# e um aviso de 30 dias chega quando já não dá tempo.
ALERTA_PADRAO_DIAS = 60

DOCUMENTOS: dict[str, dict] = {
    # ── Registro e regularidade do imóvel ────────────────────────────────────
    "matricula": {
        "rotulo": "Matrícula do imóvel",
        "grupo": "imovel",
        "exigencia": "obrigatorio",
        "vence": False,
        "periodico": False,
        "campos": ["numero", "cartorio", "comarca", "data_emissao", "area_ha"],
        "nota": "Certidão de inteiro teor. Bancos e certificadoras costumam "
                "exigir emissão recente (até 30 dias) na hora da operação — "
                "a matrícula em si não vence.",
    },
    "car": {
        "rotulo": "CAR / SICAR",
        "grupo": "imovel",
        "exigencia": "obrigatorio",
        "vence": False,
        "periodico": False,
        "campos": ["numero", "situacao", "area_ha", "area_reserva_legal_ha",
                   "area_app_ha", "municipio", "uf"],
        "nota": "Autodeclaratório: o próprio recibo diz que não é título de "
                "propriedade nem de posse. Serve para área e localização, "
                "nunca para titularidade.",
    },
    "ccir": {
        "rotulo": "CCIR (INCRA)",
        "grupo": "imovel",
        "exigencia": "obrigatorio",
        "vence": False,
        "periodico": True,
        "periodo": "exercicio",
        "campos": ["numero", "exercicio", "area_ha"],
        "nota": "Emitido por exercício. Exigido em venda, partilha e "
                "financiamento; sem ele o cartório não registra.",
    },
    "itr": {
        "rotulo": "ITR (recibo de entrega)",
        "grupo": "imovel",
        "exigencia": "obrigatorio",
        "vence": False,
        "periodico": True,
        "periodo": "exercicio",
        "campos": ["exercicio", "nirf", "area_ha", "area_tributavel_ha"],
        "nota": "Declaração anual. A área declarada no ITR costuma divergir "
                "da do CAR — divergência é informação, não erro.",
    },

    # ── Ambiental e água ─────────────────────────────────────────────────────
    "outorga_agua": {
        "rotulo": "Outorga de uso de água",
        "grupo": "ambiental",
        "exigencia": "condicional",
        "condicao": "Há captação de água (irrigação, açude, poço) na propriedade.",
        "vence": True,
        "periodico": False,
        "alerta_dias": 90,
        "campos": ["numero", "orgao", "vazao", "validade", "corpo_hidrico"],
        "nota": "Renovação no órgão estadual (no Paraná, o IAT) leva meses. "
                "O aviso sai com 90 dias para dar tempo de protocolar.",
    },
    "licenca_ambiental": {
        "rotulo": "Licença ambiental",
        "grupo": "ambiental",
        "exigencia": "condicional",
        "condicao": "A atividade exige licenciamento (confinamento, "
                    "armazenagem, agroindústria na propriedade).",
        "vence": True,
        "periodico": False,
        "alerta_dias": 120,
        "campos": ["numero", "tipo", "orgao", "validade", "atividade"],
        "nota": "O pedido de renovação costuma precisar ser protocolado com "
                "120 dias de antecedência para prorrogar a licença vigente.",
    },
    "analise_solo": {
        "rotulo": "Análise de solo",
        "grupo": "ambiental",
        "exigencia": "opcional",
        "vence": True,
        "periodico": False,
        "alerta_dias": 60,
        "validade_meses_padrao": 24,
        "campos": ["laboratorio", "data_coleta", "talhao", "profundidade"],
        "nota": "Evidência de MRV do protocolo regenerativo (Módulo 4) e base "
                "da recomendação de calcário — que é fonte de emissão.",
    },

    # ── Posse, uso e certidões ───────────────────────────────────────────────
    "contrato_arrendamento": {
        "rotulo": "Contrato de arrendamento ou parceria",
        "grupo": "posse",
        "exigencia": "condicional",
        "condicao": "A propriedade é arrendada, em parceria ou em sociedade.",
        "vence": True,
        "periodico": False,
        "alerta_dias": 120,
        "campos": ["contraparte", "vigencia_inicio", "validade", "area_ha",
                   "safras_cobertas"],
        "nota": "Define quem colhe o resultado da safra — e quem pode "
                "reivindicar o crédito de carbono da área. Contrato vencido "
                "no meio da safra é risco comercial, não só documental.",
    },
    "certidao_negativa": {
        "rotulo": "Certidão negativa (fiscal, trabalhista ou ambiental)",
        "grupo": "posse",
        "exigencia": "opcional",
        "vence": True,
        "periodico": False,
        "alerta_dias": 30,
        "validade_meses_padrao": 6,
        "campos": ["orgao", "tipo", "emissao", "validade"],
        "nota": "Entra no Índice de Fidelização pela regularidade documental.",
    },
    "cnpj_contrato_social": {
        "rotulo": "Contrato social / cartão CNPJ",
        "grupo": "posse",
        "exigencia": "condicional",
        "condicao": "O associado é pessoa jurídica.",
        "vence": False,
        "periodico": False,
        "campos": ["cnpj", "razao_social", "socios"],
        "nota": None,
    },

    # ── Produção (Módulo 2) ──────────────────────────────────────────────────
    "nota_fiscal_insumo": {
        "rotulo": "Nota fiscal de insumo",
        "grupo": "producao",
        "exigencia": "opcional",
        "vence": False,
        "periodico": False,
        "campos": ["numero", "fornecedor", "data", "valor", "produto"],
        "nota": "Comprova o custo lançado na safra e a origem do insumo — é o "
                "que sustenta o componente de adesão do Índice de Fidelização "
                "e a quantidade de fertilizante do inventário de emissões.",
    },
    "laudo_colheita": {
        "rotulo": "Romaneio / laudo de colheita",
        "grupo": "producao",
        "exigencia": "opcional",
        "vence": False,
        "periodico": False,
        "campos": ["safra", "talhao", "quantidade_sacas", "umidade", "data"],
        "nota": "Origem da produtividade realizada. Sem ele, a produtividade "
                "é declarada pelo associado.",
    },
    "foto_campo": {
        "rotulo": "Foto de campo (evidência de MRV)",
        "grupo": "producao",
        "exigencia": "opcional",
        "vence": False,
        "periodico": False,
        "campos": ["talhao", "data", "coordenada"],
        "nota": "Evidência do protocolo regenerativo. Foto com coordenada e "
                "data vale muito mais que foto solta.",
    },
}

GRUPOS = {
    "imovel": "Registro do imóvel",
    "ambiental": "Ambiental e água",
    "posse": "Posse, uso e certidões",
    "producao": "Produção e evidências",
}


def tipos_validos() -> set[str]:
    return set(DOCUMENTOS)


def rotulo(tipo: str) -> str:
    return DOCUMENTOS.get(tipo, {}).get("rotulo", tipo)


def alerta_dias(tipo: str) -> int:
    """Antecedência do aviso de vencimento, em dias."""
    return int(DOCUMENTOS.get(tipo, {}).get("alerta_dias", ALERTA_PADRAO_DIAS))


def obrigatorios() -> list[str]:
    return [t for t, d in DOCUMENTOS.items() if d["exigencia"] == "obrigatorio"]


def condicionais() -> dict[str, str]:
    """tipo → condição em português que torna o documento exigível."""
    return {t: d["condicao"] for t, d in DOCUMENTOS.items()
            if d["exigencia"] == "condicional"}


def catalogo_publico() -> list[dict]:
    """Catálogo em lista, para o frontend montar formulário sem nada hardcoded."""
    saida = []
    for tipo, d in DOCUMENTOS.items():
        saida.append({
            "tipo": tipo,
            "rotulo": d["rotulo"],
            "grupo": d["grupo"],
            "grupo_rotulo": GRUPOS.get(d["grupo"], d["grupo"]),
            "exigencia": d["exigencia"],
            "condicao": d.get("condicao"),
            "vence": d["vence"],
            "periodico": d["periodico"],
            "alerta_dias": alerta_dias(tipo),
            "campos": d.get("campos", []),
            "nota": d.get("nota"),
        })
    return saida


def validade_padrao_meses(tipo: str) -> Optional[int]:
    return DOCUMENTOS.get(tipo, {}).get("validade_meses_padrao")
