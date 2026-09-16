"""Dependência de autenticação compartilhada para todos os endpoints."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.database.client import get_client

_bearer = HTTPBearer()


def usuario_autenticado(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """
    Valida o JWT Supabase e retorna os dados do usuário.
    Levanta 401 se o token for inválido ou expirado.
    """
    token = creds.credentials
    try:
        client = get_client()
        resp = client.auth.get_user(token)
        if not resp or not resp.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido ou expirado.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {"id": resp.user.id, "email": resp.user.email}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Falha na autenticação: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
