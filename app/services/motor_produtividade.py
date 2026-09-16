"""
app/services/motor_produtividade.py

Motor do Módulo 2 — Cultura, Lavoura e Produtividade. FUNÇÕES PURAS.

Calcula, por talhão e por safra: produtividade (kg/ha e sacas/ha), custo por
hectare e por saca, receita esperada × realizada, margem, e as três comparações
que a Primato pediu — contra a safra anterior, contra a média regional e entre
área controle × área regenerativa.

Quatro regras do domínio que o código encoda em vez de esconder:

  • **Peso úmido não se compara com peso úmido.** O romaneio traz o peso como
    saiu da colheitadeira, na umidade do dia. Soja a 18% e soja a 12% não são
    a mesma soja: comparar as duas produtividades cruas inventa ganho ou perda
    de até 7%. Toda produtividade é convertida para a umidade padrão de
    comercialização da cultura (`peso_corrigido_kg`) antes de qualquer conta.
    Quando a umidade não é informada, o número é usado como veio e o resultado
    sai marcado `umidade_informada: False` — o dado é mais fraco, e isso é dito.

  • **Saca é unidade de comércio, quilo é unidade de física.** Tudo é calculado
    em kg e convertido para sacas na saída, pelo peso da saca da cultura. É o
    que evita o erro de somar sacas de culturas diferentes.

  • **Custo por saca só existe se houve colheita.** Dividir custo por
    produtividade zero é divisão por zero disfarçada de "custo infinito".
    Sem produção, `custo_por_saca` é None com motivo, nunca um número.

  • **Controle × regenerativo compara MARGEM, não só produtividade.** O
    protocolo pode entregar a mesma saca gastando menos — ou uma saca a menos
    gastando muito menos. Olhar só produtividade reprova manejo que dá lucro.

Nada aqui lê banco ou rede.
"""

from __future__ import annotations

from typing import Iterable, Optional

# ── Culturas ─────────────────────────────────────────────────────────────────
# `umidade_padrao_pct` é a umidade de comercialização (base seca de recebimento).
# Peso da saca em kg. Rascunho a validar com a Primato.
CULTURAS: dict[str, dict] = {
    "soja":    {"rotulo": "Soja",    "saca_kg": 60.0, "umidade_padrao_pct": 14.0},
    "milho":   {"rotulo": "Milho",   "saca_kg": 60.0, "umidade_padrao_pct": 14.0},
    "trigo":   {"rotulo": "Trigo",   "saca_kg": 60.0, "umidade_padrao_pct": 13.0},
    "feijao":  {"rotulo": "Feijão",  "saca_kg": 60.0, "umidade_padrao_pct": 14.0},
    "aveia":   {"rotulo": "Aveia",   "saca_kg": 60.0, "umidade_padrao_pct": 13.0},
    "sorgo":   {"rotulo": "Sorgo",   "saca_kg": 60.0, "umidade_padrao_pct": 13.0},
    "mandioca": {"rotulo": "Mandioca", "saca_kg": 1000.0, "umidade_padrao_pct": None},
    "pastagem": {"rotulo": "Pastagem", "saca_kg": 1000.0, "umidade_padrao_pct": None},
}

# Categorias de custo. As seis primeiras são as da Especificação Funcional;
# `corretivos` foi separada de `fertilizantes` de propósito — a quantidade de
# calcário é entrada direta do inventário de emissões (Módulo 4), e enterrada
# dentro de "fertilizantes" ela não é recuperável.
CATEGORIAS_CUSTO: dict[str, str] = {
    "sementes": "Sementes e mudas",
    "fertilizantes": "Fertilizantes",
    "corretivos": "Corretivos (calcário, gesso)",
    "defensivos": "Defensivos",
    "biologicos": "Biológicos e CoinMax/TriMix",
    "mao_de_obra": "Mão de obra",
    "maquinario": "Maquinário e combustível",
    "arrendamento": "Arrendamento",
    "outros": "Outros",
}

# Categorias cujo insumo é da rede da cooperativa para efeito do Índice de
# Fidelização — a linha CoinMax/TriMix entra aqui.
CATEGORIAS_LINHA_PRIMATO = ("biologicos", "fertilizantes", "defensivos", "sementes")

MANEJOS = {
    "convencional": "Área controle (manejo convencional)",
    "regenerativo": "Área sob protocolo Primato Regenerativa",
    "transicao": "Área em transição",
}


def _f(v) -> Optional[float]:
    """Número ou None — nunca levanta, nunca devolve string."""
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n == n and n not in (float("inf"), float("-inf")) else None


def cultura_meta(cultura: Optional[str]) -> dict:
    return CULTURAS.get((cultura or "").strip().lower(),
                        {"rotulo": cultura or "—", "saca_kg": 60.0,
                         "umidade_padrao_pct": None})


def peso_corrigido_kg(peso_kg: float, umidade_pct: Optional[float],
                      cultura: str) -> tuple[float, bool]:
    """
    Converte peso úmido para a umidade padrão de comercialização.

        Pc = Pu × (100 − Uu) / (100 − Up)

    Devolve (peso corrigido, umidade_foi_aplicada). Umidade ausente, igual à
    padrão, ou cultura sem padrão → devolve o peso como veio.
    """
    padrao = cultura_meta(cultura).get("umidade_padrao_pct")
    u = _f(umidade_pct)
    if padrao is None or u is None or u <= 0 or u >= 100:
        return peso_kg, False
    return peso_kg * (100.0 - u) / (100.0 - padrao), True


def produtividade(*, producao_kg: Optional[float], area_ha: Optional[float],
                  cultura: str, umidade_pct: Optional[float] = None) -> dict:
    """Produtividade de um talhão/safra, corrigida pela umidade."""
    meta = cultura_meta(cultura)
    prod = _f(producao_kg)
    area = _f(area_ha)
    if prod is None or area is None or area <= 0:
        return {"disponivel": False,
                "motivo": "Informe a produção colhida e a área do talhão.",
                "cultura": cultura, "cultura_rotulo": meta["rotulo"]}

    corrigido, aplicou = peso_corrigido_kg(prod, umidade_pct, cultura)
    kg_ha = corrigido / area
    saca_kg = meta["saca_kg"] or 60.0
    return {
        "disponivel": True,
        "cultura": cultura,
        "cultura_rotulo": meta["rotulo"],
        "area_ha": round(area, 4),
        "producao_kg": round(prod, 2),
        "producao_kg_corrigida": round(corrigido, 2),
        "umidade_pct": _f(umidade_pct),
        "umidade_padrao_pct": meta["umidade_padrao_pct"],
        "umidade_informada": aplicou,
        "kg_ha": round(kg_ha, 2),
        "sacas": round(corrigido / saca_kg, 2),
        "sacas_ha": round(kg_ha / saca_kg, 2),
        "saca_kg": saca_kg,
    }


def consolidar_custos(lancamentos: Iterable[dict]) -> dict:
    """
    Soma os lançamentos de custo por categoria.

    Cada lançamento: {categoria, valor, quantidade?, unidade?, fornecedor?,
    da_cooperativa?}. Categoria desconhecida cai em `outros` — dinheiro nunca
    some da conta por causa de rótulo errado.
    """
    por_categoria: dict[str, float] = {c: 0.0 for c in CATEGORIAS_CUSTO}
    total = 0.0
    via_cooperativa = 0.0
    itens = 0
    for l in lancamentos or []:
        valor = _f(l.get("valor"))
        if valor is None:
            continue
        cat = (l.get("categoria") or "").strip().lower()
        if cat not in CATEGORIAS_CUSTO:
            cat = "outros"
        por_categoria[cat] += valor
        total += valor
        itens += 1
        if l.get("da_cooperativa"):
            via_cooperativa += valor

    return {
        "total": round(total, 2),
        "itens": itens,
        "por_categoria": {c: round(v, 2) for c, v in por_categoria.items() if v},
        "rotulos": CATEGORIAS_CUSTO,
        "via_cooperativa": round(via_cooperativa, 2),
        "via_cooperativa_pct": round(100.0 * via_cooperativa / total, 1) if total else None,
    }


def resultado_safra(*, cultura: str, area_ha: Optional[float],
                    producao_kg: Optional[float] = None,
                    umidade_pct: Optional[float] = None,
                    custos: Optional[Iterable[dict]] = None,
                    preco_esperado_saca: Optional[float] = None,
                    preco_realizado_saca: Optional[float] = None,
                    produtividade_esperada_sacas_ha: Optional[float] = None,
                    manejo: str = "convencional") -> dict:
    """
    Resultado completo de um talhão numa safra: produtividade, custo/ha,
    custo/saca, receita esperada × realizada e margem.

    Receita ESPERADA usa a produtividade esperada (cadastrada na entrada da
    safra) × preço esperado. Receita REALIZADA usa a produtividade medida ×
    preço realizado. As duas convivem: na entrada da safra só existe a
    primeira, e é assim que o gestor planeja.
    """
    area = _f(area_ha)
    prod = produtividade(producao_kg=producao_kg, area_ha=area_ha,
                         cultura=cultura, umidade_pct=umidade_pct)
    c = consolidar_custos(custos or [])
    meta = cultura_meta(cultura)

    custo_ha = round(c["total"] / area, 2) if area and area > 0 else None
    sacas = prod["sacas"] if prod["disponivel"] else None
    custo_saca = round(c["total"] / sacas, 2) if sacas else None

    pe = _f(preco_esperado_saca)
    pr = _f(preco_realizado_saca)
    sacas_esp_ha = _f(produtividade_esperada_sacas_ha)

    receita_esperada = (round(sacas_esp_ha * area * pe, 2)
                        if sacas_esp_ha is not None and pe is not None and area else None)
    receita_realizada = round(sacas * pr, 2) if sacas and pr is not None else None

    margem = None
    if receita_realizada is not None:
        margem = round(receita_realizada - c["total"], 2)
    margem_ha = round(margem / area, 2) if margem is not None and area else None
    margem_pct = (round(100.0 * margem / receita_realizada, 1)
                  if margem is not None and receita_realizada else None)

    # Preço de equilíbrio: a que preço a safra se paga. É o número que o
    # produtor usa para decidir travar venda.
    preco_equilibrio = round(c["total"] / sacas, 2) if sacas else None

    avisos: list[str] = []
    if prod["disponivel"] and not prod["umidade_informada"] and meta["umidade_padrao_pct"]:
        avisos.append("Umidade não informada: a produtividade não foi corrigida "
                      "para a base de comercialização e pode estar superestimada.")
    if sacas is None and c["total"]:
        avisos.append("Sem produção lançada: custo por saca e margem ficam "
                      "indisponíveis até a colheita ser registrada.")
    if receita_esperada is not None and receita_realizada is not None:
        desvio = receita_realizada - receita_esperada
        if receita_esperada and abs(desvio) / receita_esperada >= 0.15:
            direcao = "acima" if desvio > 0 else "abaixo"
            avisos.append(f"Receita realizada ficou {direcao} da esperada em "
                          f"{abs(round(100.0 * desvio / receita_esperada, 1))}%.")

    return {
        "cultura": cultura,
        "cultura_rotulo": meta["rotulo"],
        "manejo": manejo,
        "manejo_rotulo": MANEJOS.get(manejo, manejo),
        "area_ha": round(area, 4) if area else None,
        "produtividade": prod,
        "custos": c,
        "custo_ha": custo_ha,
        "custo_saca": custo_saca,
        "custo_saca_indisponivel": None if custo_saca is not None else
            "Sem produção colhida registrada.",
        "preco_esperado_saca": pe,
        "preco_realizado_saca": pr,
        "receita_esperada": receita_esperada,
        "receita_realizada": receita_realizada,
        "margem": margem,
        "margem_ha": margem_ha,
        "margem_pct": margem_pct,
        "preco_equilibrio_saca": preco_equilibrio,
        "avisos": avisos,
    }


def _delta(atual: Optional[float], base: Optional[float]) -> dict:
    if atual is None or base is None:
        return {"disponivel": False}
    dif = atual - base
    return {
        "disponivel": True,
        "atual": round(atual, 2),
        "referencia": round(base, 2),
        "diferenca": round(dif, 2),
        "variacao_pct": round(100.0 * dif / base, 1) if base else None,
    }


def comparar(resultado: dict, *, referencia_sacas_ha: Optional[float] = None,
             rotulo_referencia: str = "média regional",
             safra_anterior: Optional[dict] = None) -> dict:
    """
    Compara o resultado com a média de referência e com a safra anterior.
    `safra_anterior` é outro `resultado_safra`.
    """
    atual = resultado.get("produtividade", {})
    atual_sacas_ha = atual.get("sacas_ha") if atual.get("disponivel") else None

    saida = {
        "vs_referencia": {
            **_delta(atual_sacas_ha, _f(referencia_sacas_ha)),
            "rotulo": rotulo_referencia,
            "unidade": "sacas/ha",
        },
    }

    if safra_anterior:
        ant = safra_anterior.get("produtividade", {})
        ant_sacas = ant.get("sacas_ha") if ant.get("disponivel") else None
        saida["vs_safra_anterior"] = {
            **_delta(atual_sacas_ha, ant_sacas),
            "unidade": "sacas/ha",
        }
        saida["custo_ha_vs_safra_anterior"] = {
            **_delta(resultado.get("custo_ha"), safra_anterior.get("custo_ha")),
            "unidade": "R$/ha",
        }
        saida["margem_ha_vs_safra_anterior"] = {
            **_delta(resultado.get("margem_ha"), safra_anterior.get("margem_ha")),
            "unidade": "R$/ha",
        }
    return saida


def comparar_manejo(controle: dict, regenerativo: dict) -> dict:
    """
    Área controle × área regenerativa — o coração do piloto de campo.

    Compara produtividade, custo/ha e margem/ha, e devolve uma LEITURA em uma
    frase. A leitura considera margem, não só produtividade: o protocolo pode
    entregar menos saca gastando bem menos, e isso é ganho.
    """
    pc = controle.get("produtividade", {})
    pr = regenerativo.get("produtividade", {})
    c_sacas = pc.get("sacas_ha") if pc.get("disponivel") else None
    r_sacas = pr.get("sacas_ha") if pr.get("disponivel") else None

    prod = _delta(r_sacas, c_sacas)
    custo = _delta(regenerativo.get("custo_ha"), controle.get("custo_ha"))
    margem = _delta(regenerativo.get("margem_ha"), controle.get("margem_ha"))

    if margem.get("disponivel"):
        d = margem["diferenca"]
        if d > 0:
            leitura = (f"A área regenerativa entregou R$ {abs(d):,.2f}/ha a mais "
                       f"de margem que a área controle.")
        elif d < 0:
            leitura = (f"A área regenerativa ficou R$ {abs(d):,.2f}/ha abaixo da "
                       f"área controle em margem.")
        else:
            leitura = "As duas áreas empataram em margem por hectare."
        leitura = leitura.replace(",", "X").replace(".", ",").replace("X", ".")
    elif prod.get("disponivel"):
        d = prod["diferenca"]
        sinal = "acima" if d > 0 else ("abaixo" if d < 0 else "igual")
        leitura = (f"A área regenerativa ficou {abs(d)} sacas/ha {sinal} da área "
                   f"controle. Sem custo e preço lançados, não dá para ler margem.")
    else:
        leitura = ("Ainda não há colheita registrada nas duas áreas para comparar.")

    return {
        "produtividade_sacas_ha": prod,
        "custo_ha": custo,
        "margem_ha": margem,
        "leitura": leitura,
        "comparavel": bool(prod.get("disponivel") or margem.get("disponivel")),
        "aviso": ("Comparação entre áreas da mesma propriedade e safra. Solo, "
                  "relevo e histórico diferem entre talhões — a diferença é "
                  "indicativa, não resultado de experimento controlado."),
    }


def serie_historica(resultados: list[dict]) -> dict:
    """
    Evolução ao longo das safras. `resultados` são dicts com `safra` e o
    resultado_safra embutido, na ordem que vierem — aqui são ordenados.
    """
    pontos = []
    for r in sorted(resultados, key=lambda x: str(x.get("safra") or "")):
        res = r.get("resultado") or r
        prod = res.get("produtividade", {})
        pontos.append({
            "safra": r.get("safra"),
            "cultura": res.get("cultura"),
            "manejo": res.get("manejo"),
            "sacas_ha": prod.get("sacas_ha") if prod.get("disponivel") else None,
            "custo_ha": res.get("custo_ha"),
            "margem_ha": res.get("margem_ha"),
        })

    validos = [p["sacas_ha"] for p in pontos if p["sacas_ha"] is not None]
    media = round(sum(validos) / len(validos), 2) if validos else None
    tendencia = None
    if len(validos) >= 2:
        dif = validos[-1] - validos[0]
        tendencia = "alta" if dif > 0 else ("queda" if dif < 0 else "estável")

    return {
        "pontos": pontos,
        "safras": len(pontos),
        "media_sacas_ha": media,
        "tendencia": tendencia,
        "melhor": max(pontos, key=lambda p: p["sacas_ha"] or -1) if validos else None,
    }


def media_por(resultados: list[dict], chave: str = "sacas_ha") -> Optional[float]:
    """Média simples de uma métrica — usada no benchmarking interno (Módulo 5)."""
    vals = []
    for r in resultados:
        v = (r.get("produtividade", {}) or {}).get(chave) if chave == "sacas_ha" else r.get(chave)
        if v is not None:
            vals.append(v)
    return round(sum(vals) / len(vals), 2) if vals else None
