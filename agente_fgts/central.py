"""Cliente HTTP da Central Ecac, do lado do agente. Só faz chamadas de saída."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

VERSAO = '1.0.0'


class CentralIndisponivel(Exception):
    pass


class CentralRecusou(Exception):
    def __init__(self, status: int, mensagem: str):
        super().__init__(mensagem)
        self.status = status


class Central:
    def __init__(self, url: str, token: str, timeout: int = 30, sessao=None):
        self.base = url.rstrip('/') + '/api/fgts-agente'
        self.sessao = sessao or requests.Session()
        self.sessao.headers.update({'X-Agente-Token': str(token).strip(), 'Accept': 'application/json'})
        self.timeout = timeout

    def _post(self, caminho: str, **kw) -> Optional[Dict[str, Any]]:
        try:
            r = self.sessao.post(self.base + caminho, timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise CentralIndisponivel(f'Central sem resposta: {type(exc).__name__}') from exc
        if r.status_code == 204:
            return None
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if r.status_code >= 500:
            raise CentralIndisponivel(f'Central respondeu HTTP {r.status_code}')
        if r.status_code >= 400:
            raise CentralRecusou(r.status_code, corpo.get('message') or f'HTTP {r.status_code}')
        return corpo

    def sinal(self, login_ativo: bool, maquina: str) -> Dict[str, Any]:
        return self._post('/sinal', json={'versao': VERSAO, 'login_ativo': login_ativo, 'maquina': maquina})

    def proximo(self) -> Optional[Dict[str, Any]]:
        r = self._post('/proximo')
        return r['pedido'] if r else None

    def progresso(self, pedido_id: int, etapa: str, mensagem: Optional[str] = None) -> None:
        self._post(f'/pedidos/{pedido_id}/progresso', json={'etapa': etapa, 'mensagem': mensagem})

    def guia(self, pedido_id: int, pdf: bytes, meta: Dict[str, Any]) -> Dict[str, Any]:
        return self._post(f'/pedidos/{pedido_id}/guias', files={'pdf': ('gfd.pdf', pdf, 'application/pdf')},
                          data={'meta': json.dumps(meta, ensure_ascii=False)})

    def resultado(self, pedido_id: int, corpo: Dict[str, Any]) -> Dict[str, Any]:
        return self._post(f'/pedidos/{pedido_id}/resultado', json=corpo)

    def erro(self, pedido_id: int, codigo: str, mensagem: str) -> Dict[str, Any]:
        return self.resultado(pedido_id, {'erro': {'codigo': codigo, 'mensagem': mensagem[:400]}})
