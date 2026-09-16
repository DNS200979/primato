"""
Regressão do motor de regularidade documental (Módulo 1).

O defeito que estes testes impedem de voltar é sempre o mesmo em espírito: o
sistema dizer "está tudo certo" quando não sabe. Documento sem data de
validade, condicional que não se aplica, periódico do ano passado — cada um
tem um tratamento diferente, e confundi-los produz ou alarme falso (que ensina
a ignorar alarme) ou silêncio (que deixa a outorga vencer).
"""

from datetime import date

import pytest

from app.services import catalogo_documentos as cat
from app.services import motor_documentos as md

HOJE = date(2026, 9, 15)


def test_vencido_e_reportado_com_os_dias_passados():
    r = md.situacao_documento(
        {"tipo": "outorga_agua", "validade": date(2026, 8, 1)}, hoje=HOJE)
    assert r["situacao"] == "vencido"
    assert r["dias_restantes"] == -45
    assert "45 dias" in r["acao"]


def test_janela_de_aviso_respeita_o_prazo_do_tipo():
    """
    Outorga avisa com 90 dias e licença com 120: renovar no órgão estadual
    leva meses. Um prazo único de 30 dias avisaria tarde demais para as duas.
    """
    # 100 dias à frente: já é alerta para a licença, ainda não para a outorga.
    validade = date(2026, 12, 24)
    outorga = md.situacao_documento({"tipo": "outorga_agua", "validade": validade}, hoje=HOJE)
    licenca = md.situacao_documento({"tipo": "licenca_ambiental", "validade": validade}, hoje=HOJE)
    assert outorga["situacao"] == "em_dia"
    assert licenca["situacao"] == "a_vencer"


def test_documento_que_vence_sem_data_nao_vira_em_dia():
    """O modo de falha caro: ausência de dado lida como ausência de problema."""
    r = md.situacao_documento({"tipo": "outorga_agua", "validade": None}, hoje=HOJE)
    assert r["situacao"] == "sem_data"
    assert r["situacao"] != "em_dia"
    assert "validade" in r["acao"].lower()


def test_documento_sem_validade_propria_nao_vence():
    """Matrícula não tem prazo. Cobrar renovação dela é ruído puro."""
    r = md.situacao_documento({"tipo": "matricula", "validade": None}, hoje=HOJE)
    assert r["situacao"] == "em_dia"


def test_periodico_do_ano_anterior_e_desatualizado_nao_vencido():
    """
    CCIR de exercício antigo não é irregularidade — é pendência de emissão.
    Tratá-lo como 'vencido' bloquearia a propriedade por engano.
    """
    antigo = md.situacao_documento({"tipo": "ccir", "exercicio": 2023},
                                   hoje=HOJE, exercicio_corrente=2026)
    assert antigo["situacao"] == "desatualizado"
    assert antigo["situacao"] not in md.SITUACOES_IRREGULARES

    recente = md.situacao_documento({"tipo": "ccir", "exercicio": 2025},
                                    hoje=HOJE, exercicio_corrente=2026)
    assert recente["situacao"] == "em_dia"


def test_condicional_nao_aplicavel_nao_vira_pendencia():
    """
    Cobrar outorga de quem é de sequeiro produziria pendência falsa — e
    pendência falsa derruba o Índice de Fidelização de quem não deve nada.
    """
    r = md.avaliar_propriedade([], hoje=HOJE, contexto={"possui_captacao_agua": False})
    outorga = next(i for i in r["itens"] if i["tipo"] == "outorga_agua")
    assert outorga["situacao"] == "nao_exigido"
    assert outorga not in r["alertas"]


def test_condicional_aplicavel_e_cobrado_como_ausente():
    r = md.avaliar_propriedade([], hoje=HOJE, contexto={"possui_captacao_agua": True})
    outorga = next(i for i in r["itens"] if i["tipo"] == "outorga_agua")
    assert outorga["situacao"] == "ausente"
    assert not r["regular"]


def test_obrigatorio_ausente_torna_a_propriedade_irregular():
    r = md.avaliar_propriedade([], hoje=HOJE, contexto={})
    assert not r["regular"]
    tipos_ausentes = {i["tipo"] for i in r["alertas"] if i["situacao"] == "ausente"}
    assert set(cat.obrigatorios()) <= tipos_ausentes


def test_reenvio_renovado_substitui_o_vencido_do_mesmo_tipo():
    """
    Enviar a outorga renovada tem que LIMPAR o alerta; se o motor guardasse a
    pior ocorrência por tipo, o documento renovado continuaria pendente e o
    associado seria cobrado por algo que já resolveu.
    """
    docs = [
        {"tipo": "outorga_agua", "validade": date(2025, 1, 1), "id": 1},
        {"tipo": "outorga_agua", "validade": date(2028, 1, 1), "id": 2},
    ]
    r = md.avaliar_propriedade(docs, hoje=HOJE,
                               contexto={"possui_captacao_agua": True})
    outorga = next(i for i in r["itens"] if i["tipo"] == "outorga_agua")
    assert outorga["situacao"] == "em_dia"
    assert outorga["documento_id"] == 2


def test_pior_situacao_prioriza_vencido_sobre_ausente():
    assert md.pior_situacao(["em_dia", "ausente", "vencido"]) == "vencido"
    assert md.pior_situacao(["em_dia", "a_vencer"]) == "a_vencer"
    assert md.pior_situacao([]) == "em_dia"


def test_agenda_inclui_o_que_ja_venceu():
    """O vencido é o que MAIS precisa aparecer na agenda — não o que sai dela."""
    docs = [
        {"tipo": "outorga_agua", "validade": date(2026, 1, 1), "propriedade": "Sede"},
        {"tipo": "licenca_ambiental", "validade": date(2026, 10, 1), "propriedade": "Sede"},
        {"tipo": "outorga_agua", "validade": date(2030, 1, 1), "propriedade": "Sede"},
    ]
    agenda = md.proximos_vencimentos(docs, hoje=HOJE, janela_dias=90)
    assert [a["situacao"] for a in agenda] == ["vencido", "a_vencer"]
    assert agenda[0]["dias_restantes"] < 0


def test_regularidade_pct_conta_so_o_que_e_exigido():
    """
    Se o denominador incluísse os condicionais não aplicáveis, uma propriedade
    de sequeiro nunca chegaria a 100% — e o número viraria decoração.
    """
    docs = [{"tipo": t, "exercicio": 2026} for t in ("ccir", "itr")]
    docs += [{"tipo": "matricula"}, {"tipo": "car"}]
    r = md.avaliar_propriedade(docs, hoje=HOJE, contexto={})
    assert r["regularidade_pct"] == 100.0
    assert r["regular"]


@pytest.mark.parametrize("tipo", sorted(cat.DOCUMENTOS))
def test_todo_tipo_do_catalogo_e_avaliavel(tipo):
    """Tipo novo no catálogo sem tratamento no motor quebraria a tela inteira."""
    r = md.situacao_documento({"tipo": tipo, "validade": None, "exercicio": None},
                              hoje=HOJE)
    assert r["situacao"] in md.SITUACOES
    assert r["rotulo"]
