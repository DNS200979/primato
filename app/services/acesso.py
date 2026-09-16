"""
app/services/acesso.py

Quem pode fazer o quê no módulo Primato.

A Especificação Funcional define quatro perfis (item 9): **associado, técnico
de campo, gestor Primato e auditor**. Aqui eles viram política — e a política
tem DOIS eixos, não um:

  • **Ação** — o que a pessoa pode fazer (`permitido`).
  • **Escopo de dados** — sobre QUEM ela pode fazer (`escopo`). Um associado
    tem permissão de editar safra; só que da propriedade dele. Sem o segundo
    eixo, "pode editar safra" vira "pode editar a safra do vizinho", que é o
    mesmo dado econômico que a cooperativa promete manter reservado.

Duas regras que não são óbvias e foram decididas aqui:

  • **O técnico de campo não vê o módulo financeiro/tributário.** Ele precisa
    de talhão, insumo, produtividade e evidência de campo. Faturamento,
    balanço, DRE e enquadramento fiscal do associado (Módulo 3) são de outra
    ordem de sensibilidade, e quem atende dez propriedades por semana não
    precisa carregar isso. `ler_financeiro` é de gestor e auditor.

  • **Auditor é somente-leitura sobre o dado do associado, mas ESCREVE o
    trabalho dele.** Registrar achado é saída do auditor, não alteração do
    cliente — mesma distinção que o portal do verificador do ERP faz. Sem
    isso, "somente leitura" impediria a auditoria de existir.

`permitido`/`escopo` são PUROS (só política). `exigir` resolve contra o banco
e levanta 403. Sem cache: tirar alguém da equipe vale na hora.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, status

from app.database.client import get_db_client

PAPEIS = ("gestor", "tecnico", "auditor", "associado")

ROTULO_PAPEL = {
    "gestor": "Gestor Primato",
    "tecnico": "Técnico de campo",
    "auditor": "Auditor",
    "associado": "Associado",
}

# ação → papéis que a exercem. O que não está aqui é negado.
_ACOES: dict[str, set[str]] = {
    # leitura
    "ler": {"gestor", "tecnico", "auditor", "associado"},
    "ler_financeiro": {"gestor", "auditor"},
    "ler_trilha": {"gestor", "auditor"},
    "ler_consolidado": {"gestor", "auditor"},      # dashboard corporativo
    # escrita sobre o dado do associado
    "editar_cadastro": {"gestor", "associado"},
    "editar_propriedade": {"gestor", "tecnico", "associado"},
    "editar_producao": {"gestor", "tecnico", "associado"},
    "editar_financeiro": {"gestor"},
    "enviar_documento": {"gestor", "tecnico", "associado"},
    "remover_documento": {"gestor"},
    # trabalho do auditor
    "registrar_achado": {"gestor", "auditor"},
    # exportação e administração
    "exportar": {"gestor", "auditor", "associado"},
    "administrar": {"gestor"},
}

# Escopo de dados por papel.
#   todos    — a carteira inteira da cooperativa
#   proprios — só o associado vinculado à conta
ESCOPO = {
    "gestor": "todos",
    "tecnico": "todos",
    "auditor": "todos",
    "associado": "proprios",
}

MSG_TABELAS = ("Tabelas do módulo Primato inexistentes — rode "
               "scripts/sql_primato.sql no SQL Editor do Supabase.")


def permitido(papel: Optional[str], acao: str) -> bool:
    return bool(papel) and papel in _ACOES.get(acao, set())


def escopo(papel: Optional[str]) -> str:
    return ESCOPO.get(papel or "", "nenhum")


def acoes_do_papel(papel: str) -> list[str]:
    return sorted(a for a, p in _ACOES.items() if papel in p)


def politica_publica() -> list[dict]:
    """Matriz de papéis para a tela de equipe — nada hardcoded no frontend."""
    return [{
        "papel": p,
        "rotulo": ROTULO_PAPEL[p],
        "escopo": ESCOPO[p],
        "acoes": acoes_do_papel(p),
    } for p in PAPEIS]


# ── Resolução contra o banco ─────────────────────────────────────────────────

def tabela_ausente(e: Exception) -> bool:
    t = str(e).lower()
    return ("does not exist" in t or "42p01" in t or "pgrst205" in t
            or "could not find the table" in t)


def erro_db(e: Exception) -> HTTPException:
    if tabela_ausente(e):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, MSG_TABELAS)
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def membro(usuario_id: str) -> Optional[dict]:
    """Linha de `membros_primato` do usuário: papel + associado vinculado."""
    try:
        r = (get_db_client().table("membros_primato")
             .select("papel,associado_id,nome")
             .eq("usuario_id", usuario_id).limit(1).execute())
    except Exception as e:  # noqa: BLE001
        raise erro_db(e)
    return r.data[0] if r.data else None


def exigir(usuario: dict, acao: str) -> dict:
    """
    Devolve `{papel, associado_id, escopo}` ou levanta 403.

    Não devolve 404 em nenhum caso: negar com "não existe" confirmaria a
    existência do registro a quem não pode vê-lo.
    """
    m = membro(usuario["id"])
    papel = (m or {}).get("papel")
    if not permitido(papel, acao):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Seu perfil não permite esta ação.")
    return {"papel": papel, "associado_id": (m or {}).get("associado_id"),
            "escopo": escopo(papel), "nome": (m or {}).get("nome")}


def exigir_acesso_ao_associado(ctx: dict, associado_id: int) -> None:
    """
    Trava o segundo eixo: quem tem escopo `proprios` só alcança o próprio
    cadastro. Chamar SEMPRE que a rota recebe um `associado_id` do cliente.
    """
    if ctx.get("escopo") == "todos":
        return
    vinculado = ctx.get("associado_id")
    if vinculado is None or int(vinculado) != int(associado_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Você só tem acesso ao seu próprio cadastro.")


def filtrar_por_escopo(query, ctx: dict, coluna: str = "associado_id"):
    """Aplica o escopo a uma query do Supabase (listagens)."""
    if ctx.get("escopo") == "proprios":
        return query.eq(coluna, ctx.get("associado_id") or -1)
    return query
