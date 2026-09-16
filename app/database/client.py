from supabase import create_client, Client
from app.config import (
    SUPABASE_URL,
    SUPABASE_KEY,
    SUPABASE_SERVICE_KEY,
    require_supabase_public_settings,
)

_client: Client | None = None
_admin_client: Client | None = None


def get_client() -> Client:
    """
    Cliente com a anon key. Respeita RLS.
    Usado para operações que dependem do contexto do usuário (auth).
    """
    global _client
    require_supabase_public_settings()
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def get_admin_client() -> Client | None:
    """
    Cliente com service role key (admin). IGNORA RLS.
    Usado pelo backend FastAPI para gravar/ler dados em nome de qualquer usuário,
    desde que o JWT do usuário JÁ TENHA SIDO VALIDADO em auth.py.
    Retorna None se SUPABASE_SERVICE_KEY não estiver configurada.
    """
    global _admin_client
    if not SUPABASE_SERVICE_KEY:
        return None
    if _admin_client is None:
        _admin_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _admin_client


def get_db_client() -> Client:
    """
    Cliente preferencial para operações de banco no backend FastAPI.
    Usa service key (sem RLS) se disponível, senão fallback para anon (com RLS).
    O JWT do usuário já é validado em app/api/auth.py — esta função apenas
    devolve o cliente que conseguirá efetivamente gravar.
    """
    admin = get_admin_client()
    return admin if admin else get_client()


def get_storage_client() -> Client:
    """
    Cliente para o Supabase Storage (upload/URL assinada/remoção de evidências).
    Sempre a service key: todo acesso ao bucket privado é proxied pelo backend,
    nunca direto do browser (ver app/api/routes/documentos.py). Uso:
    `get_storage_client().storage.from_(bucket).upload(...)`.
    """
    return get_db_client()
