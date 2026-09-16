"""
Catálogos do módulo — tudo que o frontend precisa para montar formulário sem
nada hardcoded no HTML.

  GET /documentos   — tipos de documento, exigência, se vence, janela de aviso
  GET /culturas     — culturas, peso da saca e umidade padrão
  GET /custos       — categorias de custo e as da linha Primato
  GET /papeis       — os quatro perfis e o que cada um pode fazer
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import usuario_autenticado
from app.services import acesso, catalogo_documentos as cat, motor_produtividade as mp

router = APIRouter()


@router.get("/documentos", summary="Tipos de documento da propriedade")
def documentos(usuario: dict = Depends(usuario_autenticado)):
    return {
        "versao": cat.CATALOGO_VERSAO,
        "grupos": cat.GRUPOS,
        "documentos": cat.catalogo_publico(),
        "obrigatorios": cat.obrigatorios(),
        "condicionais": cat.condicionais(),
        "aviso": ("Catálogo em rascunho: prazos e obrigatoriedade a validar "
                  "com a Diretoria de Insumos Agrícolas da Primato."),
    }


@router.get("/culturas", summary="Culturas, peso da saca e umidade padrão")
def culturas(usuario: dict = Depends(usuario_autenticado)):
    return {
        "culturas": [{"chave": k, **v} for k, v in mp.CULTURAS.items()],
        "manejos": mp.MANEJOS,
        "nota": ("A produtividade é corrigida para a umidade padrão de "
                 "comercialização antes de qualquer comparação."),
    }


@router.get("/custos", summary="Categorias de custo da safra")
def custos(usuario: dict = Depends(usuario_autenticado)):
    return {
        "categorias": mp.CATEGORIAS_CUSTO,
        "linha_primato": list(mp.CATEGORIAS_LINHA_PRIMATO),
        "nota": ("Corretivos (calcário, gesso) ficam separados de "
                 "fertilizantes porque a quantidade alimenta diretamente o "
                 "inventário de emissões."),
    }


@router.get("/papeis", summary="Perfis de acesso e o que cada um pode fazer")
def papeis(usuario: dict = Depends(usuario_autenticado)):
    return {"papeis": acesso.politica_publica()}
