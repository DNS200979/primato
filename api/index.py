"""Entrypoint que a Vercel detecta para rodar o FastAPI como função serverless.

Este repositório tem UM app só (app.api.main). A atribuição de `app` fica no
nível superior porque o builder Python da Vercel faz análise estática do
arquivo ("Could not find a top-level app").
"""

from app.api.main import app

__all__ = ["app"]
