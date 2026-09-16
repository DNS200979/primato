"""
Módulo 1 — Propriedades rurais, talhões e documentos.

  GET    /                          — propriedades (escopo do perfil)
  POST   /                          — cadastra
  GET    /{id}                      — detalhe + talhões + checklist documental
  PUT    /{id}                      — edita
  DELETE /{id}                      — remove (gestor)
  POST   /{id}/geometria            — KML/shapefile .zip → polígono
  POST   /{id}/cruzamentos          — cruza o polígono com as bases públicas
  GET    /{id}/talhoes              — talhões
  POST   /{id}/talhoes              — cria talhão
  PUT    /talhoes/{id}              — edita talhão
  DELETE /talhoes/{id}              — remove talhão
  POST   /talhoes/{id}/geometria    — polígono do talhão
  GET    /{id}/documentos           — documentos + situação de vencimento
  POST   /{id}/documentos           — envia documento
  DELETE /documentos/{id}           — remove documento (gestor)
  GET    /alertas/vencimentos       — agenda de vencimentos da carteira
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile, status)
from pydantic import BaseModel, Field

from app.api.auth import usuario_autenticado
from app.config import (DOCUMENTO_MIME_PERMITIDOS, DOCUMENTO_TAMANHO_MAX_BYTES,
                        SUPABASE_BUCKET_DOCUMENTOS)
from app.database.client import get_db_client, get_storage_client
from app.services import (acesso, auditoria, catalogo_documentos as cat,
                          cruzamentos_geo, geometria_rural, motor_documentos as md)

router = APIRouter()

MANEJOS = ("convencional", "regenerativo", "transicao")
POSSES = ("propria", "arrendada", "parceria", "sociedade", "comodato")


class PropriedadeEntrada(BaseModel):
    associado_id: int
    nome: str = Field(..., min_length=2, max_length=200)
    matricula: Optional[str] = Field(None, max_length=60)
    cartorio: Optional[str] = Field(None, max_length=200)
    car: Optional[str] = Field(None, max_length=80)
    ccir: Optional[str] = Field(None, max_length=60)
    nirf: Optional[str] = Field(None, max_length=40)
    municipio: Optional[str] = Field(None, max_length=120)
    uf: Optional[str] = Field(None, max_length=2)
    latitude: Optional[float] = Field(None, ge=-34, le=6)
    longitude: Optional[float] = Field(None, ge=-74, le=-33)
    area_total_ha: Optional[float] = Field(None, gt=0, le=5_000_000)
    area_reserva_legal_ha: Optional[float] = Field(None, ge=0, le=5_000_000)
    area_app_ha: Optional[float] = Field(None, ge=0, le=5_000_000)
    area_produtiva_ha: Optional[float] = Field(None, ge=0, le=5_000_000)
    posse: str = Field("propria")
    possui_captacao_agua: bool = False
    exige_licenciamento: bool = False


class PropriedadeEdicao(PropriedadeEntrada):
    associado_id: Optional[int] = None
    nome: Optional[str] = Field(None, min_length=2, max_length=200)
    posse: Optional[str] = None
    possui_captacao_agua: Optional[bool] = None
    exige_licenciamento: Optional[bool] = None


class TalhaoEntrada(BaseModel):
    nome: str = Field(..., min_length=1, max_length=120)
    area_ha: Optional[float] = Field(None, gt=0, le=1_000_000)
    manejo: str = Field("convencional")
    talhao_controle_id: Optional[int] = None
    tipo_solo: Optional[str] = Field(None, max_length=120)
    declividade_pct: Optional[float] = Field(None, ge=0, le=100)
    observacao: Optional[str] = Field(None, max_length=2000)


class TalhaoEdicao(TalhaoEntrada):
    nome: Optional[str] = Field(None, min_length=1, max_length=120)
    manejo: Optional[str] = None


def _db():
    return get_db_client()


def _carregar_propriedade(propriedade_id: int, ctx: dict) -> dict:
    try:
        r = (_db().table("propriedades").select("*")
             .eq("id", propriedade_id).limit(1).execute())
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Propriedade não encontrada.")
    prop = r.data[0]
    acesso.exigir_acesso_ao_associado(ctx, prop["associado_id"])
    return prop


def _carregar_talhao(talhao_id: int, ctx: dict) -> tuple[dict, dict]:
    try:
        r = (_db().table("talhoes").select("*").eq("id", talhao_id)
             .limit(1).execute())
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Talhão não encontrado.")
    talhao = r.data[0]
    return talhao, _carregar_propriedade(talhao["propriedade_id"], ctx)


def _data(valor) -> Optional[date]:
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _docs_para_motor(linhas: list[dict]) -> list[dict]:
    return [{**d, "validade": _data(d.get("validade"))} for d in linhas]


def _contexto_documental(prop: dict, associado: Optional[dict] = None) -> dict:
    return {
        "possui_captacao_agua": bool(prop.get("possui_captacao_agua")),
        "exige_licenciamento": bool(prop.get("exige_licenciamento")),
        "arrendada": prop.get("posse") in ("arrendada", "parceria", "comodato"),
        "pessoa_juridica": (associado or {}).get("tipo_pessoa") == "juridica",
    }


# ── Propriedades ─────────────────────────────────────────────────────────────

@router.get("", summary="Propriedades")
def listar(associado_id: Optional[int] = None,
           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    try:
        q = _db().table("propriedades").select("*")
        q = acesso.filtrar_por_escopo(q, ctx)
        if associado_id:
            acesso.exigir_acesso_ao_associado(ctx, associado_id)
            q = q.eq("associado_id", associado_id)
        linhas = (q.order("nome").limit(1000).execute()).data or []
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    return {"propriedades": linhas, "total": len(linhas),
            "area_total_ha": round(sum(float(p.get("area_total_ha") or 0)
                                       for p in linhas), 2)}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Cadastrar propriedade")
def criar(entrada: PropriedadeEntrada, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_propriedade")
    acesso.exigir_acesso_ao_associado(ctx, entrada.associado_id)
    if entrada.posse not in POSSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Posse inválida. Use: {', '.join(POSSES)}.")
    dados = entrada.model_dump(exclude_none=True)
    _checar_areas(dados)
    try:
        r = _db().table("propriedades").insert(dados).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    nova = r.data[0]
    auditoria.registrar(usuario, "propriedade", nova["id"], "criou",
                        detalhe={"nome": nova.get("nome")})
    return nova


def _checar_areas(dados: dict) -> None:
    """
    A soma de RL + APP não pode passar da área total. Não é preciosismo: área
    inconsistente aqui vira hectare a mais no dashboard corporativo e no
    inventário de emissões, e ninguém encontra a origem depois.
    """
    total = dados.get("area_total_ha")
    if total is None:
        return
    partes = sum(float(dados.get(c) or 0) for c in
                 ("area_reserva_legal_ha", "area_app_ha"))
    if partes > float(total) * 1.001:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Reserva Legal + APP ({partes:.2f} ha) passam da área total "
            f"({float(total):.2f} ha). Confira os números.")


@router.get("/{propriedade_id}", summary="Detalhe da propriedade")
def detalhe(propriedade_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    prop = _carregar_propriedade(propriedade_id, ctx)
    db = _db()
    try:
        talhoes = (db.table("talhoes").select("*")
                   .eq("propriedade_id", propriedade_id).order("nome").execute()).data or []
        docs = (db.table("documentos_primato").select("*")
                .eq("propriedade_id", propriedade_id).is_("removido_em", "null")
                .order("criado_em", desc=True).execute()).data or []
        assoc = (db.table("associados").select("*")
                 .eq("id", prop["associado_id"]).limit(1).execute()).data
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)

    associado = assoc[0] if assoc else None
    checklist = md.avaliar_propriedade(
        _docs_para_motor(docs), hoje=date.today(),
        contexto=_contexto_documental(prop, associado))

    area_talhoes = sum(float(t.get("area_ha") or 0) for t in talhoes)
    regenerativa = sum(float(t.get("area_ha") or 0) for t in talhoes
                       if t.get("manejo") == "regenerativo")
    return {
        "propriedade": prop,
        "associado": associado,
        "talhoes": talhoes,
        "documentos": docs,
        "checklist": checklist,
        "resumo": {
            "talhoes": len(talhoes),
            "area_talhoes_ha": round(area_talhoes, 4),
            "area_regenerativa_ha": round(regenerativa, 4),
            "area_regenerativa_pct": (round(100.0 * regenerativa / area_talhoes, 1)
                                      if area_talhoes else None),
        },
    }


@router.put("/{propriedade_id}", summary="Editar propriedade")
def editar(propriedade_id: int, entrada: PropriedadeEdicao,
           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_propriedade")
    prop = _carregar_propriedade(propriedade_id, ctx)
    dados = entrada.model_dump(exclude_none=True)
    dados.pop("associado_id", None)   # mudar de dono é outra operação
    if not dados:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nada para alterar.")
    _checar_areas({**prop, **dados})
    dados["atualizado_em"] = datetime.now(timezone.utc).isoformat()
    try:
        r = (_db().table("propriedades").update(dados)
             .eq("id", propriedade_id).execute())
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "propriedade", propriedade_id, "editou",
                        detalhe={"campos": sorted(dados)})
    return r.data[0]


@router.delete("/{propriedade_id}", summary="Remover propriedade (gestor)")
def remover(propriedade_id: int, motivo: str = Query(..., min_length=5, max_length=500),
            usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    try:
        r = _db().table("propriedades").delete().eq("id", propriedade_id).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Propriedade não encontrada.")
    auditoria.registrar(usuario, "propriedade", propriedade_id, "removeu", motivo=motivo)
    return {"removido": True}


# ── Geometria e cruzamentos ──────────────────────────────────────────────────

@router.post("/{propriedade_id}/geometria", summary="Polígono da propriedade (KML ou shapefile .zip)")
async def enviar_geometria(propriedade_id: int, arquivo: UploadFile = File(...),
                           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_propriedade")
    prop = _carregar_propriedade(propriedade_id, ctx)
    conteudo = await arquivo.read()
    if len(conteudo) > DOCUMENTO_TAMANHO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            "Arquivo acima de 4 MB.")
    try:
        geojson = geometria_rural.extrair_geometria(arquivo.filename or "", conteudo)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Não consegui ler o polígono: {e}")
    resumo = geometria_rural.resumo_geometria(geojson)
    area = resumo.get("area_ha")

    # Divergência entre a área do polígono e a do cadastro é INFORMAÇÃO, não
    # erro: o CAR costuma divergir da matrícula. Registra os dois e sinaliza.
    divergencia = None
    cadastrada = prop.get("area_total_ha")
    if area and cadastrada:
        dif = abs(area - float(cadastrada)) / float(cadastrada)
        if dif > 0.15:
            divergencia = (f"O polígono mede {area:,.2f} ha e o cadastro diz "
                           f"{float(cadastrada):,.2f} ha ({dif * 100:.1f}% de diferença). "
                           f"Confira qual é a área correta."
                           ).replace(",", "X").replace(".", ",").replace("X", ".")
    try:
        _db().table("propriedades").update({
            "geometria_geojson": geojson,
            "geometria_area_ha": area,
            "geometria_origem": resumo.get("origem") or "arquivo",
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }).eq("id", propriedade_id).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "propriedade", propriedade_id, "anexou_geometria",
                        detalhe={"area_ha": area, "arquivo": arquivo.filename})
    return {"geometria": resumo, "area_ha": area, "divergencia": divergencia}


@router.post("/{propriedade_id}/cruzamentos", summary="Cruzar o polígono com as bases públicas")
def cruzar(propriedade_id: int, usuario: dict = Depends(usuario_autenticado)):
    """
    SIGEF (INCRA), terras indígenas (FUNAI), unidades de conservação (CNUC),
    processos minerários (ANM) e embargos (ICMBio).

    Base que não respondeu sai como `consultado: False` — nunca vira "sem
    ocorrência". A diferença entre "consultei e não achei nada" e "não
    consegui consultar" é a diferença entre um laudo e um chute.
    """
    ctx = acesso.exigir(usuario, "ler")
    prop = _carregar_propriedade(propriedade_id, ctx)
    geo = prop.get("geometria_geojson")
    if not geo:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Envie o polígono da propriedade antes de cruzar.")
    resultado = cruzamentos_geo.cruzar(geo, uf=prop.get("uf"),
                                       matricula=prop.get("matricula"))
    try:
        _db().table("propriedades").update({
            "cruzamentos": resultado,
            "cruzamentos_em": datetime.now(timezone.utc).isoformat(),
        }).eq("id", propriedade_id).execute()
    except Exception:  # noqa: BLE001 — guardar o resultado é conveniência
        pass
    auditoria.registrar(usuario, "propriedade", propriedade_id, "cruzou_bases")
    return resultado


# ── Talhões ──────────────────────────────────────────────────────────────────

@router.get("/{propriedade_id}/talhoes", summary="Talhões da propriedade")
def listar_talhoes(propriedade_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    _carregar_propriedade(propriedade_id, ctx)
    try:
        linhas = (_db().table("talhoes").select("*")
                  .eq("propriedade_id", propriedade_id).order("nome").execute()).data or []
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    return {"talhoes": linhas, "total": len(linhas)}


@router.post("/{propriedade_id}/talhoes", status_code=status.HTTP_201_CREATED,
             summary="Criar talhão")
def criar_talhao(propriedade_id: int, entrada: TalhaoEntrada,
                 usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_propriedade")
    prop = _carregar_propriedade(propriedade_id, ctx)
    if entrada.manejo not in MANEJOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Manejo inválido. Use: {', '.join(MANEJOS)}.")
    db = _db()
    try:
        existentes = (db.table("talhoes").select("area_ha")
                      .eq("propriedade_id", propriedade_id).execute()).data or []
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)

    # A soma dos talhões não pode passar da área da propriedade — é o erro de
    # digitação que infla produtividade média e hectare sob protocolo.
    soma = sum(float(t.get("area_ha") or 0) for t in existentes) + float(entrada.area_ha or 0)
    total = prop.get("area_total_ha")
    if total and soma > float(total) * 1.001:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"A soma dos talhões ({soma:.2f} ha) passaria da área da "
            f"propriedade ({float(total):.2f} ha).")

    dados = {**entrada.model_dump(exclude_none=True), "propriedade_id": propriedade_id}
    try:
        r = db.table("talhoes").insert(dados).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "talhao", r.data[0]["id"], "criou",
                        detalhe={"nome": entrada.nome, "manejo": entrada.manejo})
    return r.data[0]


@router.put("/talhoes/{talhao_id}", summary="Editar talhão")
def editar_talhao(talhao_id: int, entrada: TalhaoEdicao,
                  usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_propriedade")
    _carregar_talhao(talhao_id, ctx)
    dados = entrada.model_dump(exclude_none=True)
    if not dados:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nada para alterar.")
    dados["atualizado_em"] = datetime.now(timezone.utc).isoformat()
    try:
        r = _db().table("talhoes").update(dados).eq("id", talhao_id).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "talhao", talhao_id, "editou",
                        detalhe={"campos": sorted(dados)})
    return r.data[0]


@router.delete("/talhoes/{talhao_id}", summary="Remover talhão")
def remover_talhao(talhao_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_propriedade")
    _carregar_talhao(talhao_id, ctx)
    try:
        safras = (_db().table("safras").select("id").eq("talhao_id", talhao_id)
                  .limit(1).execute()).data or []
        if safras:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Este talhão tem safras lançadas. Apagá-lo levaria "
                                "junto o histórico de produtividade.")
        _db().table("talhoes").delete().eq("id", talhao_id).execute()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "talhao", talhao_id, "removeu")
    return {"removido": True}


@router.post("/talhoes/{talhao_id}/geometria", summary="Polígono do talhão")
async def geometria_talhao(talhao_id: int, arquivo: UploadFile = File(...),
                           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_propriedade")
    _carregar_talhao(talhao_id, ctx)
    conteudo = await arquivo.read()
    if len(conteudo) > DOCUMENTO_TAMANHO_MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Arquivo acima de 4 MB.")
    try:
        geojson = geometria_rural.extrair_geometria(arquivo.filename or "", conteudo)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Não consegui ler o polígono: {e}")
    resumo = geometria_rural.resumo_geometria(geojson)
    try:
        _db().table("talhoes").update({
            "geometria_geojson": geojson,
            "geometria_area_ha": resumo.get("area_ha"),
            "atualizado_em": datetime.now(timezone.utc).isoformat(),
        }).eq("id", talhao_id).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "talhao", talhao_id, "anexou_geometria",
                        detalhe={"area_ha": resumo.get("area_ha")})
    return {"geometria": resumo}


# ── Documentos ───────────────────────────────────────────────────────────────

@router.get("/{propriedade_id}/documentos", summary="Documentos e situação de vencimento")
def listar_documentos(propriedade_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    prop = _carregar_propriedade(propriedade_id, ctx)
    db = _db()
    try:
        docs = (db.table("documentos_primato").select("*")
                .eq("propriedade_id", propriedade_id).is_("removido_em", "null")
                .order("criado_em", desc=True).execute()).data or []
        assoc = (db.table("associados").select("tipo_pessoa")
                 .eq("id", prop["associado_id"]).limit(1).execute()).data
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    checklist = md.avaliar_propriedade(
        _docs_para_motor(docs), hoje=date.today(),
        contexto=_contexto_documental(prop, assoc[0] if assoc else None))
    return {"documentos": docs, "checklist": checklist}


@router.post("/{propriedade_id}/documentos", status_code=status.HTTP_201_CREATED,
             summary="Enviar documento")
async def enviar_documento(propriedade_id: int,
                           tipo: str = Form(...),
                           arquivo: UploadFile = File(...),
                           numero: Optional[str] = Form(None),
                           orgao: Optional[str] = Form(None),
                           emissao: Optional[str] = Form(None),
                           validade: Optional[str] = Form(None),
                           exercicio: Optional[int] = Form(None),
                           talhao_id: Optional[int] = Form(None),
                           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "enviar_documento")
    prop = _carregar_propriedade(propriedade_id, ctx)
    if tipo not in cat.tipos_validos():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Tipo de documento desconhecido: {tipo}.")
    meta = cat.DOCUMENTOS[tipo]
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Arquivo vazio.")
    if len(conteudo) > DOCUMENTO_TAMANHO_MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "Arquivo acima de 4 MB. Reduza a qualidade da digitalização ou "
            "envie página por página.")
    mime = (arquivo.content_type or "").split(";")[0].strip()
    if mime not in DOCUMENTO_MIME_PERMITIDOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Envie PDF, foto (JPG/PNG) ou planilha.")

    # Documento que vence sem data de validade é o modo de falha silencioso:
    # entra no sistema parecendo regular e nunca dispara alerta.
    if meta["vence"] and not validade:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{meta['rotulo']} tem validade. Informe a data de vencimento — "
            f"sem ela o documento nunca entra no alerta.")
    if meta["periodico"] and exercicio is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Informe o exercício do {meta['rotulo']}.")

    caminho = (f"{prop['associado_id']}/{propriedade_id}/"
               f"{uuid.uuid4().hex}_{(arquivo.filename or 'documento')[-80:]}")
    try:
        (get_storage_client().storage.from_(SUPABASE_BUCKET_DOCUMENTOS)
         .upload(caminho, conteudo, {"content-type": mime}))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Falha ao guardar o arquivo: {e}")

    linha = {
        "associado_id": prop["associado_id"],
        "propriedade_id": propriedade_id,
        "talhao_id": talhao_id,
        "tipo": tipo,
        "nome_arquivo": arquivo.filename,
        "caminho": caminho,
        "mime": mime,
        "tamanho": len(conteudo),
        "hash_sha256": hashlib.sha256(conteudo).hexdigest(),
        "numero": numero,
        "orgao": orgao,
        "emissao": emissao,
        "validade": validade,
        "exercicio": exercicio,
        "origem_leitura": "manual",
        "enviado_por": usuario["id"],
    }
    try:
        r = _db().table("documentos_primato").insert(
            {k: v for k, v in linha.items() if v is not None}).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    doc = r.data[0]
    auditoria.registrar(usuario, "documento", doc["id"], "anexou",
                        detalhe={"tipo": tipo, "propriedade_id": propriedade_id,
                                 "hash": linha["hash_sha256"]})
    situacao = md.situacao_documento({**doc, "validade": _data(validade)},
                                     hoje=date.today())
    return {"documento": doc, "situacao": situacao}


@router.get("/documentos/{documento_id}/url", summary="Link temporário para baixar")
def url_documento(documento_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    try:
        r = (_db().table("documentos_primato").select("*")
             .eq("id", documento_id).limit(1).execute())
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data or r.data[0].get("removido_em"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado.")
    doc = r.data[0]
    acesso.exigir_acesso_ao_associado(ctx, doc["associado_id"])
    try:
        assinado = (get_storage_client().storage
                    .from_(SUPABASE_BUCKET_DOCUMENTOS)
                    .create_signed_url(doc["caminho"], 300))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Falha ao gerar o link: {e}")
    auditoria.registrar(usuario, "documento", documento_id, "baixou")
    return {"url": assinado.get("signedURL") or assinado.get("signedUrl"),
            "expira_em_segundos": 300}


@router.delete("/documentos/{documento_id}", summary="Remover documento (gestor)")
def remover_documento(documento_id: int,
                      motivo: str = Query(..., min_length=5, max_length=500),
                      usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "remover_documento")
    db = _db()
    try:
        r = (db.table("documentos_primato").select("*")
             .eq("id", documento_id).limit(1).execute())
        if not r.data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento não encontrado.")
        doc = r.data[0]
        db.table("documentos_primato").update({
            "removido_em": datetime.now(timezone.utc).isoformat()
        }).eq("id", documento_id).execute()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    # Apaga o arquivo também: manter o binário de um documento "removido" é
    # guardar dado pessoal sem finalidade.
    apagado = True
    try:
        (get_storage_client().storage.from_(SUPABASE_BUCKET_DOCUMENTOS)
         .remove([doc["caminho"]]))
    except Exception:  # noqa: BLE001
        apagado = False
    auditoria.registrar(usuario, "documento", documento_id, "removeu", motivo=motivo,
                        detalhe={"arquivo_apagado": apagado})
    return {"removido": True, "arquivo_apagado": apagado}


# ── Agenda de vencimentos ────────────────────────────────────────────────────

@router.get("/alertas/vencimentos", summary="Agenda de vencimentos da carteira")
def vencimentos(janela_dias: int = Query(90, ge=1, le=730),
                usuario: dict = Depends(usuario_autenticado)):
    """
    O que vence nos próximos `janela_dias` — e o que já venceu, que é o que
    mais precisa aparecer.
    """
    ctx = acesso.exigir(usuario, "ler")
    db = _db()
    try:
        q = (db.table("documentos_primato")
             .select("id,tipo,validade,propriedade_id,associado_id,nome_arquivo")
             .is_("removido_em", "null").not_.is_("validade", "null"))
        q = acesso.filtrar_por_escopo(q, ctx)
        docs = (q.order("validade").limit(2000).execute()).data or []
        ids = sorted({d["propriedade_id"] for d in docs if d.get("propriedade_id")})
        props = {}
        if ids:
            props = {p["id"]: p for p in
                     (db.table("propriedades").select("id,nome,associado_id")
                      .in_("id", ids).execute()).data or []}
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)

    enriquecidos = [{**d, "validade": _data(d.get("validade")),
                     "propriedade": (props.get(d.get("propriedade_id")) or {}).get("nome")}
                    for d in docs]
    agenda = md.proximos_vencimentos(enriquecidos, hoje=date.today(),
                                     janela_dias=janela_dias)
    vencidos = [a for a in agenda if a["situacao"] == "vencido"]
    return {
        "janela_dias": janela_dias,
        "alertas": agenda,
        "total": len(agenda),
        "vencidos": len(vencidos),
        "a_vencer": len(agenda) - len(vencidos),
    }
