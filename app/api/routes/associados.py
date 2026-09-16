"""
Associados e equipe da Primato.

  GET    /                 — carteira de associados (escopo do perfil)
  POST   /                 — cadastra associado (gestor)
  GET    /{id}             — ficha do associado + propriedades
  PUT    /{id}             — edita
  DELETE /{id}             — remove (gestor, com motivo)
  GET    /equipe/membros   — quem tem acesso e com qual perfil (gestor)
  POST   /equipe/membros   — dá acesso a um e-mail (gestor)
  DELETE /equipe/membros/{usuario_id}
  GET    /eu               — o perfil de quem está logado (o frontend monta o menu daqui)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.auth import usuario_autenticado
from app.database.client import get_db_client
from app.services import acesso, auditoria
from app.services.util_br import cnpj_valido, cpf_valido, so_digitos

router = APIRouter()


class AssociadoEntrada(BaseModel):
    nome: str = Field(..., min_length=3, max_length=200)
    tipo_pessoa: str = Field("fisica", description="fisica | juridica")
    cpf_cnpj: Optional[str] = Field(None, max_length=20)
    matricula_primato: Optional[str] = Field(None, max_length=40)
    data_associacao: Optional[str] = None
    telefone: Optional[str] = Field(None, max_length=30)
    email: Optional[str] = Field(None, max_length=200)
    municipio: Optional[str] = Field(None, max_length=120)
    uf: Optional[str] = Field(None, max_length=2)
    regiao: Optional[str] = Field(None, max_length=120)
    observacao: Optional[str] = Field(None, max_length=2000)


class AssociadoEdicao(AssociadoEntrada):
    nome: Optional[str] = Field(None, min_length=3, max_length=200)
    tipo_pessoa: Optional[str] = None
    situacao: Optional[str] = None


class MembroEntrada(BaseModel):
    email: str = Field(..., min_length=5, max_length=200)
    papel: str = Field(..., description="gestor | tecnico | auditor | associado")
    associado_id: Optional[int] = Field(
        None, description="Obrigatório quando o papel é 'associado'.")


def _validar_documento(tipo_pessoa: Optional[str], doc: Optional[str]) -> Optional[str]:
    """
    Confere o dígito verificador. Um CPF/CNPJ errado não dá erro visível hoje —
    dá associado fantasma que nunca casa com a nota fiscal nem com a base da
    Receita depois, e aí já são milhares de linhas para reconciliar.
    """
    if not doc:
        return None
    d = so_digitos(doc)
    if (tipo_pessoa or "fisica") == "juridica":
        if not cnpj_valido(d):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "CNPJ inválido (dígito verificador não confere).")
    elif not cpf_valido(d):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "CPF inválido (dígito verificador não confere).")
    return d


@router.get("/eu", summary="Perfil de quem está logado")
def eu(usuario: dict = Depends(usuario_autenticado)):
    m = acesso.membro(usuario["id"])
    if not m:
        return {"tem_acesso": False,
                "mensagem": "Sua conta ainda não foi liberada por um gestor da Primato."}
    papel = m["papel"]
    return {
        "tem_acesso": True,
        "papel": papel,
        "papel_rotulo": acesso.ROTULO_PAPEL.get(papel, papel),
        "nome": m.get("nome"),
        "associado_id": m.get("associado_id"),
        "escopo": acesso.escopo(papel),
        "acoes": acesso.acoes_do_papel(papel),
    }


@router.get("", summary="Carteira de associados")
def listar(busca: Optional[str] = Query(None, max_length=120),
           regiao: Optional[str] = None,
           situacao: Optional[str] = None,
           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    db = get_db_client()
    try:
        q = db.table("associados").select("*")
        q = acesso.filtrar_por_escopo(q, ctx, "id")
        if regiao:
            q = q.eq("regiao", regiao)
        if situacao:
            q = q.eq("situacao", situacao)
        if busca:
            q = q.ilike("nome", f"%{busca}%")
        linhas = (q.order("nome").limit(500).execute()).data or []
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    return {"associados": linhas, "total": len(linhas), "escopo": ctx["escopo"]}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Cadastrar associado")
def criar(entrada: AssociadoEntrada, usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    doc = _validar_documento(entrada.tipo_pessoa, entrada.cpf_cnpj)
    payload = entrada.model_dump(exclude_none=True)
    payload["cpf_cnpj"] = doc
    try:
        r = get_db_client().table("associados").insert(payload).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    novo = r.data[0]
    auditoria.registrar(usuario, "associado", novo["id"], "criou",
                        detalhe={"nome": novo.get("nome")})
    return novo


@router.get("/{associado_id}", summary="Ficha do associado com as propriedades")
def detalhe(associado_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    acesso.exigir_acesso_ao_associado(ctx, associado_id)
    db = get_db_client()
    try:
        a = (db.table("associados").select("*").eq("id", associado_id)
             .limit(1).execute()).data
        if not a:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Associado não encontrado.")
        props = (db.table("propriedades").select("*")
                 .eq("associado_id", associado_id).order("nome").execute()).data or []
        safras = (db.table("safras").select("*")
                  .eq("associado_id", associado_id)
                  .order("safra", desc=True).limit(200).execute()).data or []
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)

    area = sum(float(p.get("area_total_ha") or 0) for p in props)
    return {
        "associado": a[0],
        "propriedades": props,
        "safras": safras,
        "resumo": {
            "propriedades": len(props),
            "area_total_ha": round(area, 2),
            "safras_registradas": len(safras),
        },
    }


@router.put("/{associado_id}", summary="Editar associado")
def editar(associado_id: int, entrada: AssociadoEdicao,
           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_cadastro")
    acesso.exigir_acesso_ao_associado(ctx, associado_id)
    dados = entrada.model_dump(exclude_none=True)
    if not dados:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nada para alterar.")
    if "cpf_cnpj" in dados:
        dados["cpf_cnpj"] = _validar_documento(dados.get("tipo_pessoa"), dados["cpf_cnpj"])
    dados["atualizado_em"] = datetime.now(timezone.utc).isoformat()
    try:
        r = (get_db_client().table("associados").update(dados)
             .eq("id", associado_id).execute())
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Associado não encontrado.")
    auditoria.registrar(usuario, "associado", associado_id, "editou",
                        detalhe={"campos": sorted(dados)})
    return r.data[0]


@router.delete("/{associado_id}", summary="Remover associado (gestor)")
def remover(associado_id: int,
            motivo: str = Query(..., min_length=5, max_length=500),
            usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    try:
        r = (get_db_client().table("associados").delete()
             .eq("id", associado_id).execute())
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Associado não encontrado.")
    # A trilha fica: o cadastro sai, o registro de que saiu e por quê, não.
    auditoria.registrar(usuario, "associado", associado_id, "removeu", motivo=motivo,
                        detalhe={"nome": r.data[0].get("nome")})
    return {"removido": True, "motivo": motivo,
            "aviso": "Propriedades, talhões e safras vinculados foram removidos junto. "
                     "Os arquivos no armazenamento precisam ser apagados à parte."}


# ── Equipe ───────────────────────────────────────────────────────────────────

@router.get("/equipe/membros", summary="Quem tem acesso ao sistema")
def membros(usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    try:
        linhas = (get_db_client().table("membros_primato").select("*")
                  .order("papel").execute()).data or []
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    return {"membros": [{**m, "papel_rotulo": acesso.ROTULO_PAPEL.get(m["papel"], m["papel"])}
                        for m in linhas],
            "papeis": acesso.politica_publica()}


@router.post("/equipe/membros", summary="Dar acesso a um e-mail")
def adicionar_membro(entrada: MembroEntrada,
                     usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    if entrada.papel not in acesso.PAPEIS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Perfil inválido. Use: {', '.join(acesso.PAPEIS)}.")
    # Perfil 'associado' sem vínculo veria a carteira inteira pelo caminho
    # do escopo 'proprios' com associado_id nulo — recusar é a trava.
    if entrada.papel == "associado" and not entrada.associado_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Informe o associado ao qual esta conta pertence.")

    db = get_db_client()
    try:
        conta = db.auth.admin.list_users()
        alvo = next((u for u in (conta or [])
                     if (getattr(u, "email", "") or "").lower() == entrada.email.lower()), None)
    except Exception:  # noqa: BLE001
        alvo = None
    if not alvo:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Nenhuma conta com este e-mail. Peça para a pessoa "
                            "criar a conta no sistema antes de liberar o acesso.")
    payload = {"usuario_id": str(alvo.id), "email": entrada.email,
               "papel": entrada.papel, "associado_id": entrada.associado_id}
    try:
        r = db.table("membros_primato").upsert(payload, on_conflict="usuario_id").execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "membro", None, "concedeu",
                        detalhe={"email": entrada.email, "papel": entrada.papel})
    return r.data[0] if r.data else payload


@router.delete("/equipe/membros/{usuario_id}", summary="Retirar acesso")
def remover_membro(usuario_id: str, usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    db = get_db_client()
    try:
        gestores = (db.table("membros_primato").select("usuario_id")
                    .eq("papel", "gestor").execute()).data or []
        # Sem gestor ninguém mais libera acesso a ninguém — o sistema fica órfão.
        if len(gestores) <= 1 and any(g["usuario_id"] == usuario_id for g in gestores):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Este é o último gestor. Promova outra pessoa antes.")
        r = db.table("membros_primato").delete().eq("usuario_id", usuario_id).execute()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membro não encontrado.")
    auditoria.registrar(usuario, "membro", None, "revogou",
                        detalhe={"usuario_id": usuario_id})
    return {"removido": True}
