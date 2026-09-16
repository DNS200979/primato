"""
Entrypoint do módulo Primato — Perfil Regenerativo Digital.

Sistema próprio da Primato Cooperativa Agroindustrial, construído sobre o
núcleo do MBV CarbonOS (geometria, cruzamento com bases públicas, leitura
documental, trilha de auditoria) e com o que a Especificação Funcional pede
de novo: talhão com polígono, safra por talhão, custo e produtividade,
comparação controle × regenerativo, alertas de vencimento e quatro perfis
de acesso.

Fase 1 (esta): Módulos 1 e 2.
  /api/v1/associados    — associados e equipe (os quatro perfis)
  /api/v1/propriedades  — imóvel, talhões, documentos, alertas, cruzamentos
  /api/v1/safras        — cultura, produtividade, custos, receita, comparações
  /api/v1/catalogos     — catálogos para o frontend montar formulário

Próximas fases: Módulo 4 (ESG/emissões por talhão), Módulo 3 (fiscal),
Módulo 5 (Perfil Regenerativo, Índice de Fidelização, dashboard corporativo).

Banco: projeto Supabase PRÓPRIO da Primato — ver scripts/sql_primato.sql.
Uso local:  uvicorn app.api.main:app --port 8000
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.erros import validacao_com_nao_finitos
from app.api.routes import associados, catalogos, propriedades, safras
from app.config import (APP_NOME, CORS_ORIGIN_REGEX, CORS_ORIGINS,
                        COOPERATIVA_NOME, PROJECT_ROOT, SAFRA_CORRENTE,
                        SUPABASE_KEY, SUPABASE_URL,
                        missing_supabase_public_settings)

app = FastAPI(
    title=f"{APP_NOME} — Perfil Regenerativo Digital",
    description=(
        "Cadastro de propriedades e talhões, produtividade por safra, custos "
        "e receita, comparação entre área controle e área sob protocolo "
        "regenerativo, e alertas de vencimento documental."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Corpo com Infinity/NaN faria o 422 estourar na serialização e virar 500.
app.add_exception_handler(RequestValidationError, validacao_com_nao_finitos)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["http://localhost:8000"],
    allow_origin_regex=CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(associados.router, prefix="/api/v1/associados",
                   tags=["Associados e equipe"])
app.include_router(propriedades.router, prefix="/api/v1/propriedades",
                   tags=["Módulo 1 — Propriedades e talhões"])
app.include_router(safras.router, prefix="/api/v1/safras",
                   tags=["Módulo 2 — Cultura, lavoura e produtividade"])
app.include_router(catalogos.router, prefix="/api/v1/catalogos",
                   tags=["Catálogos"])


@app.get("/health", tags=["Health"])
def health():
    from app.config import IA_LEITURA_HABILITADA
    return {
        "status": "healthy",
        "produto": APP_NOME,
        "cooperativa": COOPERATIVA_NOME,
        "modulo": "primato",
        "fase": "1 — Módulos 1 e 2",
        "safra_corrente": SAFRA_CORRENTE,
        "supabase_configured": not missing_supabase_public_settings(),
        "ia_leitura": IA_LEITURA_HABILITADA,
    }


@app.get("/api/v1/public-config", tags=["Health"])
def public_config():
    """Config pública consumida pela tela de login (Supabase Auth)."""
    return {
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_KEY,
        "api_base": "",
        "app_nome": APP_NOME,
        "cooperativa": COOPERATIVA_NOME,
        "safra_corrente": SAFRA_CORRENTE,
        "supabase_configured": not missing_supabase_public_settings(),
    }


# ── Arquivos servidos: SEMPRE rota explícita para UM arquivo. Não montar
# StaticFiles no diretório do projeto (no ERP isso já expôs o .env). ──────────

@app.get("/assets/mbv-logo.png", include_in_schema=False)
def logo():
    return FileResponse(PROJECT_ROOT / "assets" / "mbv-logo.png",
                        media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/", include_in_schema=False)
def painel():
    """Painel da Primato (login próprio, Supabase Auth)."""
    return FileResponse(PROJECT_ROOT / "painel.html",
                        headers={"Cache-Control": "no-cache"})
