"""
Módulo 2 — Cultura, lavoura e produtividade.

  GET    /                        — safras (filtros: associado, propriedade, safra, cultura)
  POST   /                        — abre a safra de um talhão (planejamento)
  GET    /{id}                    — safra com resultado calculado e comparações
  PUT    /{id}                    — atualiza (inclusive a colheita)
  DELETE /{id}
  GET    /{id}/lancamentos        — custos e insumos
  POST   /{id}/lancamentos        — lança custo/insumo
  DELETE /lancamentos/{id}
  GET    /comparativo/manejo      — área controle × área regenerativa
  GET    /historico/talhao/{id}   — série histórica do talhão
  GET    /referencias             — médias regionais cadastradas
  POST   /referencias             — cadastra média de referência (gestor)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.auth import usuario_autenticado
from app.config import SAFRA_CORRENTE
from app.database.client import get_db_client
from app.services import acesso, auditoria, motor_produtividade as mp

router = APIRouter()

SITUACOES = ("planejada", "em_curso", "colhida", "cancelada")
CICLOS = ("principal", "segunda", "terceira", "inverno")


class SafraEntrada(BaseModel):
    talhao_id: int
    safra: str = Field(..., min_length=4, max_length=20)
    cultura: str = Field(..., min_length=2, max_length=60)
    ciclo: str = Field("principal")
    variedade: Optional[str] = Field(None, max_length=120)
    area_ha: Optional[float] = Field(None, gt=0, le=1_000_000)
    manejo: Optional[str] = None
    data_plantio: Optional[str] = None
    produtividade_esperada_sacas_ha: Optional[float] = Field(None, ge=0, le=100_000)
    preco_esperado_saca: Optional[float] = Field(None, ge=0, le=1_000_000)
    observacao: Optional[str] = Field(None, max_length=2000)


class SafraEdicao(BaseModel):
    variedade: Optional[str] = Field(None, max_length=120)
    area_ha: Optional[float] = Field(None, gt=0, le=1_000_000)
    manejo: Optional[str] = None
    data_plantio: Optional[str] = None
    data_colheita: Optional[str] = None
    produtividade_esperada_sacas_ha: Optional[float] = Field(None, ge=0, le=100_000)
    preco_esperado_saca: Optional[float] = Field(None, ge=0, le=1_000_000)
    producao_kg: Optional[float] = Field(None, ge=0, le=1_000_000_000)
    umidade_pct: Optional[float] = Field(None, ge=0, lt=100)
    preco_realizado_saca: Optional[float] = Field(None, ge=0, le=1_000_000)
    situacao: Optional[str] = None
    observacao: Optional[str] = Field(None, max_length=2000)


class LancamentoEntrada(BaseModel):
    categoria: str = Field(..., max_length=40)
    descricao: Optional[str] = Field(None, max_length=300)
    produto: Optional[str] = Field(None, max_length=200)
    quantidade: Optional[float] = Field(None, ge=0, le=100_000_000)
    unidade: Optional[str] = Field(None, max_length=10)
    valor: float = Field(..., ge=0, le=1_000_000_000)
    fornecedor: Optional[str] = Field(None, max_length=200)
    da_cooperativa: bool = False
    linha_primato: Optional[str] = Field(None, max_length=60)
    data: Optional[str] = None
    nota_fiscal: Optional[str] = Field(None, max_length=60)


class ReferenciaEntrada(BaseModel):
    cultura: str = Field(..., max_length=60)
    safra: str = Field(..., max_length=20)
    sacas_ha: float = Field(..., gt=0, le=100_000)
    uf: Optional[str] = Field(None, max_length=2)
    regiao: Optional[str] = Field(None, max_length=120)
    fonte: Optional[str] = Field(None, max_length=120)


def _db():
    return get_db_client()


def _carregar_safra(safra_id: int, ctx: dict) -> dict:
    try:
        r = _db().table("safras").select("*").eq("id", safra_id).limit(1).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Safra não encontrada.")
    s = r.data[0]
    acesso.exigir_acesso_ao_associado(ctx, s["associado_id"])
    return s


def _lancamentos(safra_id: int) -> list[dict]:
    try:
        return (_db().table("lancamentos_safra").select("*")
                .eq("safra_id", safra_id).order("data").execute()).data or []
    except Exception:  # noqa: BLE001
        return []


def _resultado(safra: dict, lancs: Optional[list[dict]] = None) -> dict:
    """Aplica o motor puro sobre a linha do banco."""
    lancs = _lancamentos(safra["id"]) if lancs is None else lancs
    return mp.resultado_safra(
        cultura=safra.get("cultura"),
        area_ha=safra.get("area_ha"),
        producao_kg=safra.get("producao_kg"),
        umidade_pct=safra.get("umidade_pct"),
        custos=lancs,
        preco_esperado_saca=safra.get("preco_esperado_saca"),
        preco_realizado_saca=safra.get("preco_realizado_saca"),
        produtividade_esperada_sacas_ha=safra.get("produtividade_esperada_sacas_ha"),
        manejo=safra.get("manejo") or "convencional",
    )


def _referencia(cultura: str, safra: str, uf: Optional[str]) -> Optional[dict]:
    """Média de referência mais específica disponível: UF, senão geral."""
    try:
        linhas = (_db().table("referencias_produtividade").select("*")
                  .eq("cultura", cultura).eq("safra", safra).execute()).data or []
    except Exception:  # noqa: BLE001
        return None
    if not linhas:
        return None
    if uf:
        da_uf = [l for l in linhas if (l.get("uf") or "").upper() == uf.upper()]
        if da_uf:
            return da_uf[0]
    return linhas[0]


# ── Safras ───────────────────────────────────────────────────────────────────

@router.get("", summary="Safras lançadas")
def listar(associado_id: Optional[int] = None, propriedade_id: Optional[int] = None,
           safra: Optional[str] = None, cultura: Optional[str] = None,
           manejo: Optional[str] = None,
           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    try:
        q = _db().table("safras").select("*")
        q = acesso.filtrar_por_escopo(q, ctx)
        if associado_id:
            acesso.exigir_acesso_ao_associado(ctx, associado_id)
            q = q.eq("associado_id", associado_id)
        if propriedade_id:
            q = q.eq("propriedade_id", propriedade_id)
        if safra:
            q = q.eq("safra", safra)
        if cultura:
            q = q.eq("cultura", cultura)
        if manejo:
            q = q.eq("manejo", manejo)
        linhas = (q.order("safra", desc=True).limit(1000).execute()).data or []
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)

    area = sum(float(s.get("area_ha") or 0) for s in linhas)
    regen = sum(float(s.get("area_ha") or 0) for s in linhas
                if s.get("manejo") == "regenerativo")
    return {
        "safras": linhas,
        "total": len(linhas),
        "area_ha": round(area, 2),
        "area_regenerativa_ha": round(regen, 2),
        "safra_corrente": SAFRA_CORRENTE,
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Abrir safra de um talhão")
def criar(entrada: SafraEntrada, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_producao")
    db = _db()
    try:
        t = (db.table("talhoes").select("*").eq("id", entrada.talhao_id)
             .limit(1).execute()).data
        if not t:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Talhão não encontrado.")
        talhao = t[0]
        p = (db.table("propriedades").select("id,associado_id,uf")
             .eq("id", talhao["propriedade_id"]).limit(1).execute()).data
        if not p:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Propriedade não encontrada.")
        prop = p[0]
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    acesso.exigir_acesso_ao_associado(ctx, prop["associado_id"])

    if entrada.ciclo not in CICLOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Ciclo inválido. Use: {', '.join(CICLOS)}.")
    area = entrada.area_ha or talhao.get("area_ha")
    if area and talhao.get("area_ha") and float(area) > float(talhao["area_ha"]) * 1.001:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"A área plantada ({float(area):.2f} ha) passa da área do talhão "
            f"({float(talhao['area_ha']):.2f} ha).")

    dados = {
        **entrada.model_dump(exclude_none=True),
        "propriedade_id": talhao["propriedade_id"],
        "associado_id": prop["associado_id"],
        "area_ha": area,
        # O manejo herda do talhão quando não informado: é o talhão que está
        # sob protocolo, não a safra solta.
        "manejo": entrada.manejo or talhao.get("manejo") or "convencional",
    }
    try:
        r = db.table("safras").insert(dados).execute()
    except Exception as e:  # noqa: BLE001
        if "duplicate" in str(e).lower() or "23505" in str(e):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Já existe esta cultura neste talhão, safra e ciclo.")
        raise acesso.erro_db(e)
    nova = r.data[0]
    auditoria.registrar(usuario, "safra", nova["id"], "criou",
                        detalhe={"talhao_id": entrada.talhao_id,
                                 "cultura": entrada.cultura, "safra": entrada.safra})
    return nova


# ── Médias de referência ─────────────────────────────────────────────────────
# ATENÇÃO: estas rotas ficam ANTES de `/{safra_id}`. O FastAPI casa na ordem de
# declaração, e `/referencias` tem a mesma forma de `/{safra_id}` — declarada
# depois, a listagem tentaria converter "referencias" em inteiro e devolveria 422.

@router.get("/referencias", summary="Médias de produtividade de referência")
def listar_referencias(cultura: Optional[str] = None, safra: Optional[str] = None,
                       usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "ler")
    try:
        q = _db().table("referencias_produtividade").select("*")
        if cultura:
            q = q.eq("cultura", cultura)
        if safra:
            q = q.eq("safra", safra)
        linhas = (q.order("safra", desc=True).limit(500).execute()).data or []
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    return {"referencias": linhas, "total": len(linhas)}


@router.post("/referencias", status_code=status.HTTP_201_CREATED,
             summary="Cadastrar média de referência (gestor)")
def criar_referencia(entrada: ReferenciaEntrada,
                     usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    dados = entrada.model_dump(exclude_none=True)
    try:
        r = (_db().table("referencias_produtividade")
             .upsert(dados, on_conflict="cultura,safra,uf,regiao").execute())
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "safra", None, "cadastrou_referencia", detalhe=dados)
    return r.data[0] if r.data else dados


# ── Detalhe e edição ─────────────────────────────────────────────────────────

@router.get("/{safra_id}", summary="Safra com resultado e comparações")
def detalhe(safra_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    s = _carregar_safra(safra_id, ctx)
    lancs = _lancamentos(safra_id)
    resultado = _resultado(s, lancs)
    db = _db()

    # Safra anterior do MESMO talhão e cultura — é a comparação honesta.
    anterior = None
    try:
        antes = (db.table("safras").select("*")
                 .eq("talhao_id", s["talhao_id"]).eq("cultura", s["cultura"])
                 .lt("safra", s["safra"]).order("safra", desc=True)
                 .limit(1).execute()).data or []
        if antes:
            anterior = _resultado(antes[0])
            anterior["safra"] = antes[0]["safra"]
    except Exception:  # noqa: BLE001
        anterior = None

    uf = None
    try:
        p = (db.table("propriedades").select("uf,nome")
             .eq("id", s["propriedade_id"]).limit(1).execute()).data
        uf = (p[0].get("uf") if p else None)
    except Exception:  # noqa: BLE001
        pass
    ref = _referencia(s["cultura"], s["safra"], uf)

    comparacoes = mp.comparar(
        resultado,
        referencia_sacas_ha=(ref or {}).get("sacas_ha"),
        rotulo_referencia=(f"média {(ref or {}).get('fonte') or 'de referência'}"
                           f"{' — ' + uf if uf and ref else ''}"),
        safra_anterior=anterior)

    return {
        "safra": s,
        "resultado": resultado,
        "lancamentos": lancs,
        "comparacoes": comparacoes,
        "referencia": ref,
        "safra_anterior": anterior,
    }


@router.put("/{safra_id}", summary="Atualizar safra (inclusive a colheita)")
def editar(safra_id: int, entrada: SafraEdicao,
           usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_producao")
    s = _carregar_safra(safra_id, ctx)
    dados = entrada.model_dump(exclude_none=True)
    if not dados:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Nada para alterar.")
    if "situacao" in dados and dados["situacao"] not in SITUACOES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Situação inválida. Use: {', '.join(SITUACOES)}.")
    # Lançar produção marca a safra como colhida sozinha: ninguém lembra de
    # mudar o status depois de digitar o romaneio.
    if "producao_kg" in dados and dados.get("situacao") is None:
        dados["situacao"] = "colhida"
    dados["atualizado_em"] = datetime.now(timezone.utc).isoformat()
    try:
        r = _db().table("safras").update(dados).eq("id", safra_id).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    atualizada = r.data[0]
    auditoria.registrar(usuario, "safra", safra_id, "editou",
                        detalhe={"campos": sorted(dados)})
    return {"safra": atualizada, "resultado": _resultado(atualizada)}


@router.delete("/{safra_id}", summary="Remover safra")
def remover(safra_id: int, motivo: str = Query(..., min_length=5, max_length=500),
            usuario: dict = Depends(usuario_autenticado)):
    acesso.exigir(usuario, "administrar")
    try:
        r = _db().table("safras").delete().eq("id", safra_id).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not r.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Safra não encontrada.")
    auditoria.registrar(usuario, "safra", safra_id, "removeu", motivo=motivo)
    return {"removido": True}


# ── Lançamentos de custo e insumo ────────────────────────────────────────────

@router.get("/{safra_id}/lancamentos", summary="Custos e insumos da safra")
def listar_lancamentos(safra_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    _carregar_safra(safra_id, ctx)
    lancs = _lancamentos(safra_id)
    return {"lancamentos": lancs, "consolidado": mp.consolidar_custos(lancs)}


@router.post("/{safra_id}/lancamentos", status_code=status.HTTP_201_CREATED,
             summary="Lançar custo ou insumo")
def criar_lancamento(safra_id: int, entrada: LancamentoEntrada,
                     usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_producao")
    _carregar_safra(safra_id, ctx)
    cat = entrada.categoria.strip().lower()
    if cat not in mp.CATEGORIAS_CUSTO:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Categoria inválida. Use: {', '.join(mp.CATEGORIAS_CUSTO)}.")
    # Insumo sem quantidade custa certo e emite errado: é a quantidade que
    # alimenta o inventário (kg de N, tonelada de calcário), não o valor.
    aviso = None
    if cat in ("fertilizantes", "corretivos") and entrada.quantidade is None:
        aviso = ("Sem a quantidade, este insumo entra no custo mas fica de fora "
                 "do cálculo de emissões do Módulo 4.")
    dados = {**entrada.model_dump(exclude_none=True),
             "categoria": cat, "safra_id": safra_id}
    try:
        r = _db().table("lancamentos_safra").insert(dados).execute()
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "lancamento", r.data[0]["id"], "criou",
                        detalhe={"safra_id": safra_id, "categoria": cat,
                                 "valor": entrada.valor})
    return {"lancamento": r.data[0], "aviso": aviso,
            "consolidado": mp.consolidar_custos(_lancamentos(safra_id))}


@router.delete("/lancamentos/{lancamento_id}", summary="Remover lançamento")
def remover_lancamento(lancamento_id: int,
                       usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "editar_producao")
    db = _db()
    try:
        r = (db.table("lancamentos_safra").select("*")
             .eq("id", lancamento_id).limit(1).execute())
        if not r.data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Lançamento não encontrado.")
        _carregar_safra(r.data[0]["safra_id"], ctx)
        db.table("lancamentos_safra").delete().eq("id", lancamento_id).execute()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    auditoria.registrar(usuario, "lancamento", lancamento_id, "removeu")
    return {"removido": True}


# ── Comparações ──────────────────────────────────────────────────────────────

@router.get("/comparativo/manejo", summary="Área controle × área regenerativa")
def comparativo_manejo(propriedade_id: Optional[int] = None,
                       associado_id: Optional[int] = None,
                       safra: Optional[str] = None,
                       cultura: Optional[str] = None,
                       usuario: dict = Depends(usuario_autenticado)):
    """
    O comparativo do piloto de campo. Agrega as safras de cada manejo e
    compara produtividade, custo/ha e margem/ha.
    """
    ctx = acesso.exigir(usuario, "ler")
    try:
        q = _db().table("safras").select("*").eq("situacao", "colhida")
        q = acesso.filtrar_por_escopo(q, ctx)
        if propriedade_id:
            q = q.eq("propriedade_id", propriedade_id)
        if associado_id:
            acesso.exigir_acesso_ao_associado(ctx, associado_id)
            q = q.eq("associado_id", associado_id)
        if safra:
            q = q.eq("safra", safra)
        if cultura:
            q = q.eq("cultura", cultura)
        linhas = (q.limit(1000).execute()).data or []
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)

    if not linhas:
        return {"comparavel": False,
                "motivo": "Nenhuma safra colhida encontrada com esses filtros."}

    grupos: dict[str, list[dict]] = {"convencional": [], "regenerativo": [], "transicao": []}
    for s in linhas:
        grupos.setdefault(s.get("manejo") or "convencional", []).append(_resultado(s))

    def agregar(resultados: list[dict]) -> dict:
        """
        Agrega ponderando pela ÁREA, não por média simples de médias: um talhão
        de 3 ha não pode pesar igual a um de 300 ha na produtividade do grupo.
        """
        area = sum(r.get("area_ha") or 0 for r in resultados)
        if not area:
            return {"produtividade": {"disponivel": False}, "talhoes": len(resultados)}
        sacas = sum((r["produtividade"].get("sacas") or 0)
                    for r in resultados if r["produtividade"].get("disponivel"))
        custo = sum(r["custos"]["total"] for r in resultados)
        receita = sum(r.get("receita_realizada") or 0 for r in resultados)
        margem = receita - custo if receita else None
        return {
            "talhoes": len(resultados),
            "area_ha": round(area, 2),
            "produtividade": {"disponivel": bool(sacas),
                              "sacas_ha": round(sacas / area, 2) if sacas else None,
                              "sacas": round(sacas, 2)},
            "custo_ha": round(custo / area, 2) if custo else None,
            "receita_realizada": round(receita, 2) if receita else None,
            "margem_ha": round(margem / area, 2) if margem is not None else None,
        }

    controle = agregar(grupos.get("convencional") or [])
    regenerativo = agregar(grupos.get("regenerativo") or [])
    comparacao = mp.comparar_manejo(controle, regenerativo)
    return {
        "filtros": {"propriedade_id": propriedade_id, "associado_id": associado_id,
                    "safra": safra, "cultura": cultura},
        "controle": controle,
        "regenerativo": regenerativo,
        "transicao": agregar(grupos.get("transicao") or []),
        "comparacao": comparacao,
    }


@router.get("/historico/talhao/{talhao_id}", summary="Série histórica do talhão")
def historico(talhao_id: int, usuario: dict = Depends(usuario_autenticado)):
    ctx = acesso.exigir(usuario, "ler")
    try:
        linhas = (_db().table("safras").select("*").eq("talhao_id", talhao_id)
                  .order("safra").limit(100).execute()).data or []
    except Exception as e:  # noqa: BLE001
        raise acesso.erro_db(e)
    if not linhas:
        return {"pontos": [], "safras": 0}
    acesso.exigir_acesso_ao_associado(ctx, linhas[0]["associado_id"])
    return mp.serie_historica([{"safra": s["safra"], "resultado": _resultado(s)}
                               for s in linhas])

