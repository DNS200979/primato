"""
app/services/auditoria.py

Trilha de quem-fez-o-quê-quando no módulo Primato.

Existe por dois motivos que se reforçam: o MRV do Módulo 4 exige trilha
auditável para que a evidência tenha valor, e o relatório exportado carrega
hash SHA-256 — hash sem trilha prova que o arquivo não mudou, mas não prova
quem o produziu nem de onde veio o número.

É **best-effort**: nunca levanta exceção e tolera a tabela ainda não existir
(permite subir o código antes da DDL). Registrar evento não pode derrubar o
lançamento de uma safra.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.database.client import get_db_client

ENTIDADES = ("associado", "propriedade", "talhao", "safra", "documento",
             "lancamento", "achado", "relatorio", "membro")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def registrar(usuario: Optional[dict], entidade: str, entidade_id: Optional[int],
              acao: str, *, motivo: Optional[str] = None,
              detalhe: Optional[dict] = None) -> None:
    """Grava um evento. Engole qualquer falha — inclusive tabela ausente."""
    try:
        get_db_client().table("eventos_primato").insert({
            "usuario_id": (usuario or {}).get("id"),
            "usuario_email": (usuario or {}).get("email"),
            "entidade": entidade,
            "entidade_id": entidade_id,
            "acao": acao,
            "motivo": motivo,
            "detalhe": detalhe,
            "criado_em": _agora(),
        }).execute()
    except Exception:  # noqa: BLE001 — trilha nunca bloqueia a ação primária
        pass


def listar(entidade: str, entidade_id: int, limite: int = 200) -> list[dict]:
    try:
        r = (get_db_client().table("eventos_primato").select("*")
             .eq("entidade", entidade).eq("entidade_id", entidade_id)
             .order("criado_em", desc=True).limit(limite).execute())
        return r.data or []
    except Exception:  # noqa: BLE001
        return []


def hash_conteudo(payload: Any) -> str:
    """
    SHA-256 do conteúdo, para os relatórios auditáveis que a especificação
    pede (item 5, "Relatórios auditáveis").

    Serializa com `sort_keys` para que o mesmo conteúdo produza sempre o mesmo
    hash independentemente da ordem em que o dicionário foi montado — sem
    isso, dois relatórios idênticos teriam hashes diferentes e o hash não
    provaria nada.
    """
    if isinstance(payload, (bytes, bytearray)):
        return hashlib.sha256(payload).hexdigest()
    bruto = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                       default=str).encode("utf-8")
    return hashlib.sha256(bruto).hexdigest()
