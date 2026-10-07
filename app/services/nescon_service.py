"""Cliente do Nescon Clientes (portal que envia WhatsApp), só para as guias MEI.

Variáveis:
* `NESCON_API_URL`        ex.: https://clientes.gestaoempresa.com (sem `/api`)
* `NESCON_INTERNAL_TOKEN` o MESMO valor de `INTERNAL_API_TOKEN` do Nescon

Nada aqui lança: falha de rede vira `{'status': 'erro_rede', 'motivo': ...}` para a tela
mostrar, em vez de derrubar o lote.
"""
import base64
import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)
TIMEOUT = 30


def _config():
    url = (os.getenv('NESCON_API_URL') or '').strip().rstrip('/')
    token = (os.getenv('NESCON_INTERNAL_TOKEN') or '').strip()
    return url, token


def configurado() -> bool:
    url, token = _config()
    return bool(url and token)


def _chamar(metodo: str, caminho: str, **kw) -> Dict[str, Any]:
    url, token = _config()
    if not (url and token):
        return {'status': 'erro_rede', 'motivo': 'NESCON_API_URL / NESCON_INTERNAL_TOKEN não configurados'}
    try:
        resp = requests.request(metodo, f'{url}/api/interno{caminho}', timeout=TIMEOUT,
                                headers={'Authorization': f'Bearer {token}'}, **kw)
        try:
            corpo = resp.json()
        except ValueError:
            corpo = {}
        if resp.status_code in (401, 503):
            return {'status': 'erro_rede', 'motivo': f'Nescon recusou o token (HTTP {resp.status_code})'}
        corpo.setdefault('_http', resp.status_code)
        return corpo
    except requests.RequestException as exc:
        logger.warning('[NESCON] %s %s falhou: %s', metodo, caminho, exc)
        return {'status': 'erro_rede', 'motivo': f'Sem resposta do Nescon: {type(exc).__name__}'}


def vinculo(cnpj: str) -> Dict[str, Any]:
    """{vinculada, whatsapp_valido, motivo?} ou {status:'erro_rede', motivo}."""
    return _chamar('GET', '/empresa-vinculo', params={'cnpj': cnpj})


def enviar_guia(cnpj: str, competencia: str, pdf: bytes, external_ref: str,
                vencimento: Optional[str] = None, valor: Optional[float] = None,
                forcar: bool = False, tipo: str = 'DAS_MEI') -> Dict[str, Any]:
    """{status: enviada|na_fila|falhou|sem_whatsapp|ignorada|sem_cadastro|invalido|erro_rede, motivo}."""
    corpo = {'cnpj': cnpj, 'tipo': tipo, 'competencia': competencia, 'external_ref': external_ref,
             'pdf_base64': base64.b64encode(pdf).decode(), 'forcar': bool(forcar)}
    if vencimento:
        corpo['vencimento'] = vencimento
    if valor is not None:
        corpo['valor'] = valor
    return _chamar('POST', '/guia-mei', json=corpo)
