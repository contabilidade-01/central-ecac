"""Token máquina-a-máquina para `/api/interno/*` (portal nescon-clientes → este sistema).

⚠️ DESVIO INTENCIONAL (18o) — NÃO existe no exe. Até aqui só havia sessão de
navegador e HTTP Basic; um sistema chamando outro precisava de senha de gente.

Regras:
* `INTEGRACAO_TOKEN` com **32+ caracteres**; menor que isso é ignorado (com aviso no
  log) — token curto é o mesmo que nenhum.
* Comparação em tempo constante (`hmac.compare_digest`).
* O token vem SÓ no cabeçalho `X-Integracao-Token`. Nunca na URL: URL vai para log
  do proxy, histórico e Referer.
* Quem entra por aqui NÃO é usuário: não tem sessão, não tem menu, e só alcança as
  rotas do blueprint `interno`. Nada mais do sistema aceita esse token.
"""

from __future__ import annotations

import hmac
import logging
import os

logger = logging.getLogger(__name__)

CABECALHO = 'X-Integracao-Token'
PREFIXO = '/api/interno/'
TAMANHO_MINIMO = 32

_avisado = False


def token_configurado() -> str:
    """O token do ambiente, ou '' quando ausente/fraco."""
    global _avisado
    valor = (os.getenv('INTEGRACAO_TOKEN') or '').strip()
    if not valor:
        return ''
    if len(valor) < TAMANHO_MINIMO:
        if not _avisado:
            logger.warning('[INTEGRACAO] INTEGRACAO_TOKEN tem menos de %s caracteres — ignorado. '
                           'Gere um com: python -c "import secrets; print(secrets.token_urlsafe(48))"',
                           TAMANHO_MINIMO)
            _avisado = True
        return ''
    return valor


def habilitado() -> bool:
    return bool(token_configurado())


def token_valido(apresentado: str | None) -> bool:
    esperado = token_configurado()
    if not esperado or not apresentado:
        return False
    return hmac.compare_digest(esperado.encode('utf-8'), str(apresentado).strip().encode('utf-8'))


def caminho_interno(caminho: str) -> bool:
    return caminho.startswith(PREFIXO)
