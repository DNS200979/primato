"""
Regressão do motor de produtividade e custo (Módulo 2).

Estes números vão para a mesa do associado e para o dashboard da Diretoria.
Os defeitos que os testes impedem de voltar:

  • comparar produtividade em umidades diferentes (inventa ganho de até 7%);
  • dividir custo por produção zero (custo "infinito" na tela);
  • médias de médias que fazem um talhão de 3 ha pesar como um de 300 ha;
  • ler o piloto regenerativo só por produtividade, ignorando a margem.
"""

import pytest

from app.services import motor_produtividade as mp


def test_saca_de_60_quilos_e_a_conta_basica():
    """3.600 kg/ha de soja = 60 sc/ha. Se este teste cair, tudo caiu."""
    r = mp.produtividade(producao_kg=36_000, area_ha=10, cultura="soja")
    assert r["kg_ha"] == 3600.0
    assert r["sacas_ha"] == 60.0
    assert r["sacas"] == 600.0


def test_umidade_acima_do_padrao_reduz_a_produtividade():
    """
    Soja colhida a 18% tem água a mais no peso. Sem a correção, 3.600 kg/ha
    úmidos seriam lidos como 60 sc/ha — quando a soja a 14% dá 57,2.
    A conta: 36.000 × (100−18)/(100−14) = 34.325,6 kg.
    """
    umida = mp.produtividade(producao_kg=36_000, area_ha=10,
                             cultura="soja", umidade_pct=18.0)
    assert umida["umidade_informada"] is True
    assert umida["producao_kg_corrigida"] == pytest.approx(34_325.58, abs=0.02)
    assert umida["sacas_ha"] == pytest.approx(57.21, abs=0.02)
    assert umida["sacas_ha"] < 60.0


def test_umidade_ausente_nao_corrige_e_avisa():
    """
    O número é usado como veio — mas o resultado diz que a umidade não entrou.
    Corrigir com um valor 'típico' inventado seria pior que não corrigir.
    """
    r = mp.resultado_safra(cultura="soja", area_ha=10, producao_kg=36_000)
    assert r["produtividade"]["umidade_informada"] is False
    assert any("Umidade não informada" in a for a in r["avisos"])


def test_sem_producao_o_custo_por_saca_e_none_com_motivo():
    """Divisão por zero disfarçada de 'custo infinito' na tela do produtor."""
    r = mp.resultado_safra(cultura="soja", area_ha=10,
                           custos=[{"categoria": "sementes", "valor": 5000}])
    assert r["custo_saca"] is None
    assert r["custo_saca_indisponivel"]
    assert r["custo_ha"] == 500.0


def test_categoria_desconhecida_cai_em_outros_sem_sumir_dinheiro():
    """Rótulo errado nunca pode fazer valor desaparecer do total."""
    c = mp.consolidar_custos([
        {"categoria": "sementes", "valor": 1000},
        {"categoria": "drone", "valor": 500},
    ])
    assert c["total"] == 1500.0
    assert c["por_categoria"]["outros"] == 500.0


def test_corretivos_ficam_separados_de_fertilizantes():
    """
    A tonelada de calcário alimenta o inventário de emissões. Enterrada em
    'fertilizantes' ela não é recuperável depois.
    """
    assert "corretivos" in mp.CATEGORIAS_CUSTO
    c = mp.consolidar_custos([
        {"categoria": "corretivos", "valor": 800},
        {"categoria": "fertilizantes", "valor": 3000},
    ])
    assert c["por_categoria"]["corretivos"] == 800.0
    assert c["por_categoria"]["fertilizantes"] == 3000.0


def test_percentual_de_compra_na_cooperativa():
    """Componente do Índice de Fidelização — adesão a insumos da rede Primato."""
    c = mp.consolidar_custos([
        {"categoria": "biologicos", "valor": 3000, "da_cooperativa": True},
        {"categoria": "defensivos", "valor": 1000, "da_cooperativa": False},
    ])
    assert c["via_cooperativa"] == 3000.0
    assert c["via_cooperativa_pct"] == 75.0


def test_margem_e_preco_de_equilibrio():
    r = mp.resultado_safra(
        cultura="soja", area_ha=10, producao_kg=36_000, umidade_pct=14.0,
        custos=[{"categoria": "sementes", "valor": 30_000}],
        preco_realizado_saca=130.0)
    assert r["receita_realizada"] == 78_000.0        # 600 sc × 130
    assert r["margem"] == 48_000.0
    assert r["margem_ha"] == 4800.0
    assert r["preco_equilibrio_saca"] == 50.0        # 30.000 / 600 sc


def test_desvio_grande_entre_receita_esperada_e_realizada_vira_aviso():
    """Esperava 750 sc (R$ 97.500), colheu 600 (R$ 78.000): 20% abaixo."""
    r = mp.resultado_safra(
        cultura="soja", area_ha=10, producao_kg=36_000, umidade_pct=14.0,
        produtividade_esperada_sacas_ha=75.0, preco_esperado_saca=130.0,
        preco_realizado_saca=130.0)
    assert any("abaixo da esperada" in a for a in r["avisos"])


def test_desvio_pequeno_nao_vira_aviso():
    """Frustração de 5% é safra normal; virar alerta ensina a ignorar alerta."""
    r = mp.resultado_safra(
        cultura="soja", area_ha=10, producao_kg=36_000, umidade_pct=14.0,
        produtividade_esperada_sacas_ha=63.0, preco_esperado_saca=130.0,
        preco_realizado_saca=130.0)
    assert not any("esperada" in a for a in r["avisos"])


def test_comparacao_com_referencia_regional():
    r = mp.resultado_safra(cultura="soja", area_ha=10, producao_kg=36_000,
                           umidade_pct=14.0)
    c = mp.comparar(r, referencia_sacas_ha=55.0)
    assert c["vs_referencia"]["diferenca"] == 5.0
    assert c["vs_referencia"]["variacao_pct"] == pytest.approx(9.1, abs=0.1)


def test_comparacao_sem_referencia_nao_inventa_numero():
    r = mp.resultado_safra(cultura="soja", area_ha=10, producao_kg=36_000)
    c = mp.comparar(r, referencia_sacas_ha=None)
    assert c["vs_referencia"]["disponivel"] is False


def _res(sacas_ha, custo_ha, preco=130.0, area=10.0):
    return mp.resultado_safra(
        cultura="soja", area_ha=area, producao_kg=sacas_ha * 60 * area,
        umidade_pct=14.0, preco_realizado_saca=preco,
        custos=[{"categoria": "outros", "valor": custo_ha * area}])


def test_regenerativo_com_menos_saca_mas_mais_margem_e_lido_como_ganho():
    """
    A regra que o motor encoda: o protocolo pode entregar menos saca gastando
    bem menos. Ler só produtividade reprovaria manejo que dá lucro.
    """
    controle = _res(sacas_ha=60, custo_ha=4000)        # margem 3.800/ha
    regen = _res(sacas_ha=58, custo_ha=3000)           # margem 4.540/ha
    c = mp.comparar_manejo(controle, regen)
    assert c["produtividade_sacas_ha"]["diferenca"] < 0     # menos saca
    assert c["margem_ha"]["diferenca"] > 0                  # mais margem
    assert "a mais de margem" in c["leitura"]


def test_numero_em_formato_brasileiro_nao_come_a_pontuacao():
    """
    Regressão do defeito achado no primeiro teste em produção (16/09/2026): a
    troca de separadores era aplicada à FRASE inteira, e o ponto final virava
    vírgula — "…que a área controle,". Formatar só o número.
    """
    assert mp._br(1234.5) == "1.234,50"
    assert mp._br(374.49) == "374,49"
    c = mp.comparar_manejo(_res(60, 4000), _res(58, 3000))
    assert c["leitura"].endswith(".")
    assert "R$ 740,00/ha" in c["leitura"] or "R$ " in c["leitura"]


def test_comparacao_de_manejo_sem_colheita_nao_conclui_nada():
    vazio = mp.resultado_safra(cultura="soja", area_ha=10)
    c = mp.comparar_manejo(vazio, vazio)
    assert c["comparavel"] is False
    assert "Ainda não há colheita" in c["leitura"]


def test_comparacao_de_manejo_sempre_traz_o_aviso_de_leitura():
    """
    Talhão não é experimento controlado: solo e relevo diferem. O aviso não é
    formalidade — é o que impede a diferença virar 'prova' do protocolo.
    """
    c = mp.comparar_manejo(_res(60, 4000), _res(65, 4000))
    assert "não é" in c["aviso"] or "indicativa" in c["aviso"]


def test_serie_historica_ordena_e_le_tendencia():
    serie = mp.serie_historica([
        {"safra": "2025/2026", "resultado": _res(62, 4000)},
        {"safra": "2023/2024", "resultado": _res(55, 4000)},
        {"safra": "2024/2025", "resultado": _res(58, 4000)},
    ])
    assert [p["safra"] for p in serie["pontos"]] == \
        ["2023/2024", "2024/2025", "2025/2026"]
    assert serie["tendencia"] == "alta"
    assert serie["media_sacas_ha"] == pytest.approx(58.33, abs=0.02)


def test_valores_invalidos_nao_derrubam_o_motor():
    """Campo vazio ou texto no lugar do número vem de formulário todo dia."""
    r = mp.resultado_safra(cultura="soja", area_ha="", producao_kg="abc",
                           custos=[{"categoria": "sementes", "valor": None}])
    assert r["produtividade"]["disponivel"] is False
    assert r["custos"]["total"] == 0.0


def test_cultura_desconhecida_usa_saca_padrao_sem_quebrar():
    r = mp.produtividade(producao_kg=6000, area_ha=1, cultura="quinoa")
    assert r["disponivel"] is True
    assert r["sacas_ha"] == 100.0
