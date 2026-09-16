"""
Handler de erro de validação que sobrevive a valores não-finitos.

── O problema ────────────────────────────────────────────────────────────────
O JSON do Python aceita `Infinity` e `NaN` (extensão não-padrão), então um
corpo como `{"kwh": Infinity}` é parseado sem reclamar. Com
`allow_inf_nan=False` nos modelos, o Pydantic rejeita corretamente — mas o
detalhe do erro 422 ECOA o valor ofensivo no campo `input`:

    {'type': 'finite_number', 'loc': ('kwh',), 'input': inf}

E o `JSONResponse` do Starlette serializa com `allow_nan=False`, que é o
comportamento certo (JSON padrão não tem Infinity). Resultado: a resposta de
erro não consegue ser serializada e o 422 vira

    ValueError: Out of range float values are not JSON compliant: inf

ou seja, **500 com stack trace** em vez do 422 que o usuário deveria ver.

── A correção ────────────────────────────────────────────────────────────────
Troca os não-finitos do payload de erro por uma string legível antes de
serializar. O status volta a ser 422 e a mensagem diz qual campo recusou o
valor — que é a informação útil.

Registrar nos TRÊS apps (main, main_cbe, main_crve) com
`app.add_exception_handler(RequestValidationError, validacao_com_nao_finitos)`.
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _sanear(valor: Any) -> Any:
    """Troca inf/-inf/NaN por texto, recursivamente. O resto passa intacto."""
    if isinstance(valor, float) and not math.isfinite(valor):
        if math.isnan(valor):
            return "NaN (valor não numérico)"
        return ("Infinity (valor infinito)" if valor > 0
                else "-Infinity (valor infinito negativo)")
    if isinstance(valor, dict):
        return {k: _sanear(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_sanear(v) for v in valor]
    # bytes e exceções aparecem no detalhe do Pydantic e não são serializáveis
    if isinstance(valor, bytes):
        return valor.decode("utf-8", errors="replace")
    if isinstance(valor, BaseException):
        return str(valor)
    return valor


async def validacao_com_nao_finitos(request: Request,
                                    exc: RequestValidationError) -> JSONResponse:
    """422 padrão do FastAPI, mas com o payload livre de inf/NaN."""
    return JSONResponse(
        status_code=422,   # nome da constante mudou entre versões do Starlette
        content={"detail": _sanear(exc.errors())},
    )
