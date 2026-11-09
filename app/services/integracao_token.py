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


def tokens_por_consumidor() -> dict[str, str]:
    """Um token por sistema consumidor, para rotacionar ou revogar um sem derrubar o outro.

    `INTEGRACAO_TOKENS="portal=<token>;atendechat=<token>"`. O `INTEGRACAO_TOKEN` antigo
    continua valendo, com o nome `legado`, até todos os consumidores migrarem.
    """
    saida: dict[str, str] = {}
    for par in (os.getenv('INTEGRACAO_TOKENS') or '').split(';'):
        nome, _, valor = par.partition('=')
        nome, valor = nome.strip().lower(), valor.strip()
        if not nome or not valor:
            continue
        if len(valor) < TAMANHO_MINIMO:
            logger.warning('[INTEGRACAO] token de %s com menos de %s caracteres — ignorado.',
                           nome, TAMANHO_MINIMO)
            continue
        saida[nome] = valor
    legado = token_configurado()
    if legado:
        saida.setdefault('legado', legado)
    return saida


def consumidor(apresentado: str | None) -> str | None:
    """Nome do sistema dono do token, ou None. Compara todos em tempo constante."""
    if not apresentado:
        return None
    recebido = str(apresentado).strip().encode('utf-8')
    achado = None
    for nome, valor in tokens_por_consumidor().items():
        if hmac.compare_digest(valor.encode('utf-8'), recebido):
            achado = nome
    return achado


def habilitado() -> bool:
    return bool(tokens_por_consumidor())


def token_valido(apresentado: str | None) -> bool:
    return consumidor(apresentado) is not None


def caminho_interno(caminho: str) -> bool:
    return caminho.startswith(PREFIXO)
