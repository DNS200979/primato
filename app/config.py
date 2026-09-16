"""
Configuração do módulo Primato.

Projeto SEPARADO do módulo Cooperativas (CONFAF) e do ERP CarbonFree: repo,
Supabase e projeto Vercel próprios. O dado econômico e produtivo de associado
da Primato não divide banco com carteira de outra cooperativa.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BASE_DIR

# SÓ o .env deste projeto. `load_dotenv()` sem caminho sobe diretórios até
# achar um .env — e esta pasta mora dentro da do módulo Cooperativas, que por
# sua vez mora na do ERP: rodaria a Primato contra o Supabase de produção de
# outro cliente. Caminho explícito, sempre.
load_dotenv(BASE_DIR / ".env")

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

CORS_ORIGINS: list[str] = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,null"
    ).split(",") if o.strip()
]
# Cada preview da Vercel ganha um subdomínio novo — allowlist fixa não acompanha.
CORS_ORIGIN_REGEX: str = os.getenv("CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app")


def missing_supabase_public_settings() -> list[str]:
    return [n for n, v in {"SUPABASE_URL": SUPABASE_URL,
                           "SUPABASE_KEY": SUPABASE_KEY}.items() if not v]


def require_supabase_public_settings() -> None:
    faltando = missing_supabase_public_settings()
    if faltando:
        raise RuntimeError("Configuração Supabase incompleta. Defina: "
                           + ", ".join(faltando))


# ─── Documentos ──────────────────────────────────────────────────────────────
SUPABASE_BUCKET_DOCUMENTOS: str = os.getenv("SUPABASE_BUCKET_DOCUMENTOS",
                                            "documentos-primato")

# 4 MB: a Vercel recusa corpo de requisição acima de ~4,5 MB com
# FUNCTION_PAYLOAD_TOO_LARGE, ANTES de chegar ao FastAPI — medido no módulo
# Cooperativas em 11/09/2026, inclusive na configuração moderna (sem "builds").
# Arquivo maior sobe por URL assinada, direto ao Storage.
DOCUMENTO_TAMANHO_MAX_BYTES: int = 4 * 1024 * 1024
ENVIO_DIRETO_TAMANHO_MAX_BYTES: int = 20 * 1024 * 1024

DOCUMENTO_MIME_PERMITIDOS: set[str] = {
    "application/pdf", "image/jpeg", "image/png", "image/webp",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel", "text/csv", "text/xml", "application/xml",
}

# ─── Leitura documental por IA ───────────────────────────────────────────────
# A IA só TRANSCREVE o que está escrito; as regras decidem. Sem chave, a
# leitura cai na heurística embarcada e o resto vai à conferência humana.
CLAUDE_MODELO_LEITURA: str = os.getenv("CLAUDE_MODELO_LEITURA", "claude-opus-5")
IA_LEITURA_HABILITADA: bool = os.getenv("IA_LEITURA_HABILITADA", "1").strip() \
    not in ("0", "false", "nao", "não")

# ─── Identidade ──────────────────────────────────────────────────────────────
APP_NOME: str = "MBV CarbonOS · Primato"
COOPERATIVA_NOME: str = "Primato Cooperativa Agroindustrial"
APP_BASE_URL: str = os.getenv("APP_BASE_URL", "").rstrip("/")

# Safra corrente padrão (rótulo "2026/2027"). Usado quando a rota não recebe.
SAFRA_CORRENTE: str = os.getenv("SAFRA_CORRENTE", "2026/2027")
