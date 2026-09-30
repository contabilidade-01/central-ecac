"""Número de WhatsApp: validação e normalização.

⚠️ DESVIO INTENCIONAL (19o) — NÃO existe no exe. Porte fiel de
`api/src/whatsappNumero.js` do portal nescon-clientes, para os dois sistemas
aceitarem e recusarem exatamente os mesmos números.

Regras (as mesmas do portal):
* **Completa o DDI.** O escritório digita como fala — "34 99999-8888" — e o sistema põe o 55.
* **Só celular.** WhatsApp não existe em fixo: 10 dígitos são recusados com mensagem
  própria, para o operador corrigir o cadastro em vez de procurar defeito no envio.
* **DDD real** (`[1-9][1-9]`) e **o 9 na frente** (celular brasileiro desde 2016).
"""

from __future__ import annotations

import re
from typing import Any, Dict

# 55 + DDD + 9 + 8 dígitos = 13.
RE_CELULAR = re.compile(r'^55(?:1[1-9]|[2-9][1-9])9\d{8}$')
# 55 + DDD + 8 dígitos = 12. Fixo (ou celular antigo, sem o 9).
RE_FIXO = re.compile(r'^55(?:1[1-9]|[2-9][1-9])\d{8}$')


def so_digitos(valor: Any) -> str:
    return re.sub(r'\D', '', str(valor or ''))


def normalizar(valor: Any) -> str:
    """Completa o DDI quando veio só DDD + número; o resto passa para a validação decidir."""
    d = so_digitos(valor)
    if not d:
        return ''
    if len(d) in (10, 11):
        return f'55{d}'
    return d


def validar(valor: Any) -> Dict[str, Any]:
    """{'ok', 'numero', 'motivo'} — `numero` só vem quando ok; o motivo é para a tela."""
    numero = normalizar(valor)
    if not numero:
        return {'ok': False, 'numero': None, 'motivo': 'WhatsApp em branco'}
    if RE_CELULAR.match(numero):
        return {'ok': True, 'numero': numero, 'motivo': ''}
    if RE_FIXO.match(numero):
        return {'ok': False, 'numero': None,
                'motivo': ('Parece telefone fixo (falta o 9 do celular). O WhatsApp só '
                           'funciona em celular — corrija o cadastro.')}
    return {'ok': False, 'numero': None,
            'motivo': f'Formato inválido: esperado DDD + 9 dígitos (recebido {len(numero)} dígito(s)).'}


def formatar(valor: Any) -> str:
    """Para exibir: (34) 99999-8888."""
    d = so_digitos(valor)
    sem55 = d[2:] if d.startswith('55') and len(d) >= 12 else d
    if len(sem55) == 11:
        return f'({sem55[:2]}) {sem55[2:7]}-{sem55[7:]}'
    if len(sem55) == 10:
        return f'({sem55[:2]}) {sem55[2:6]}-{sem55[6:]}'
    return str(valor or '')


def chave_numero(valor: Any) -> str:
    """DDD + últimos 8 dígitos — ignora o 55 e o 9º dígito (porte de whatsappDestino.js).

    O mesmo celular aparece escrito com e sem eles no cadastro; a chave junta tudo.
    """
    d = normalizar(valor)
    if d.startswith('55') and len(d) >= 12:
        d = d[2:]
    if len(d) < 10:
        return ''
    return d[:2] + d[-8:]
