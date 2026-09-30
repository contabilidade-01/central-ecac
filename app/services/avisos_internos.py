"""Avisos internos ao escritório pelo WhatsApp — o uso do módulo uazapi neste sistema.

⚠️ DESVIO INTENCIONAL (19o) — NÃO existe no exe. Nada aqui fala com cliente: o destino é
o número do escritório (`ESCRITORIO_WHATSAPP`, senão `ADMIN_WHATSAPP`).

Quando avisa:
* lote de rotina concluído (situação fiscal, parcelamentos) — com quantas empresas
  falharam, quais, e quais foram puladas por procuração;
* lote interrompido pelo teto de gasto;
* fila de reprocessamento com falha definitiva.

Como avisa: o aviso entra numa fila em arquivo (`<DATA_DIR>/instance/avisos_internos.json`)
e o agendador a drena a cada ciclo DENTRO da janela diurna — o lote roda de madrugada e
ninguém precisa acordar com WhatsApp às 3h. Nunca lança: falha de aviso vai só para o log.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DB_PATH

logger = logging.getLogger(__name__)

ARQUIVO = 'avisos_internos.json'
MAX_PENDENTES = 50
_LOCK = threading.Lock()


def caminho() -> Path:
    return Path(DB_PATH).parent / ARQUIVO


def destino() -> str:
    return (os.getenv('ESCRITORIO_WHATSAPP') or os.getenv('ADMIN_WHATSAPP') or '').strip()


def _ler() -> List[Dict[str, Any]]:
    arquivo = caminho()
    if not arquivo.exists():
        return []
    try:
        dados = json.loads(arquivo.read_text(encoding='utf-8'))
        return dados if isinstance(dados, list) else []
    except Exception:
        logger.exception('[AVISOS] arquivo ilegível — descartando')
        return []


def _gravar(itens: List[Dict[str, Any]]) -> None:
    arquivo = caminho()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(itens[-MAX_PENDENTES:], ensure_ascii=False, indent=2), encoding='utf-8')


def enfileirar(texto: str, contexto: str = 'aviso_interno') -> bool:
    """Guarda o aviso para sair na próxima passagem dentro da janela. Nunca lança."""
    if not destino():
        logger.info('[AVISOS] sem ESCRITORIO_WHATSAPP/ADMIN_WHATSAPP — aviso só no log: %s', texto[:200])
        return False
    try:
        with _LOCK:
            itens = _ler()
            itens.append({'texto': texto[:3000], 'contexto': contexto[:40],
                          'criado_em': datetime.now().isoformat(timespec='seconds')})
            _gravar(itens)
        return True
    except Exception:
        logger.exception('[AVISOS] não enfileirou')
        return False


def pendentes() -> List[Dict[str, Any]]:
    with _LOCK:
        return _ler()


def drenar() -> Dict[str, int]:
    """Envia o que está na fila, se for hora. Chamado pelo agendador a cada ciclo."""
    from app.services import uazapi_service as uazapi
    from app.services.janela_envio import pode_enviar_agora

    with _LOCK:
        itens = _ler()
    if not itens:
        return {'enviados': 0, 'pendentes': 0}
    if not uazapi.configurado() or not destino() or not pode_enviar_agora():
        return {'enviados': 0, 'pendentes': len(itens)}

    restantes: List[Dict[str, Any]] = []
    enviados = 0
    for item in itens:
        r = uazapi.enviar(destino(), item['texto'], contexto=item.get('contexto') or 'aviso_interno',
                          enviado_por='agendador', respeitar_janela=True)
        if r.get('ok'):
            enviados += 1
        else:
            # motivo transitório (teto/hora, rede) fica para o próximo ciclo; bloqueio
            # definitivo (número inválido, trava) não adianta repetir
            transitorio = any(p in (r.get('motivo') or '') for p in ('teto', 'inacessível', 'HTTP', 'janela'))
            if transitorio:
                restantes.append(item)
            else:
                logger.warning('[AVISOS] descartado: %s', r.get('motivo'))
    with _LOCK:
        _gravar(restantes)
    return {'enviados': enviados, 'pendentes': len(restantes)}


# ------------------------------------------------------------- textos prontos

def _nomes(empresas: List[Any], limite: int = 10) -> str:
    nomes = [getattr(e, 'razao_social', None) or str(e) for e in empresas]
    texto = '; '.join(nomes[:limite])
    if len(nomes) > limite:
        texto += f' … e mais {len(nomes) - limite}'
    return texto


def aviso_fim_de_lote(modulo: str, titulo: str, resultado: Dict[str, Any],
                      falhas: Optional[List[Any]] = None, puladas: Optional[List[Any]] = None) -> str:
    falhas = falhas or []
    puladas = puladas or []
    linhas = [f'🗂️ *Central e-CAC — {titulo}*']
    if resultado.get('interrompido_por') == 'teto_de_gasto':
        linhas.append(f"⛔ Lote INTERROMPIDO pelo teto de gasto: {resultado.get('message')}")
        linhas.append(f"{resultado.get('processadas', 0)} feita(s) · {resultado.get('pendentes', 0)} pendente(s). "
                      'Aumente o teto em /agendamento para continuar de onde parou.')
    elif not resultado.get('success'):
        linhas.append(f"❌ Falhou: {resultado.get('message')}")
    else:
        linhas.append(f"✅ Concluído: {resultado.get('processadas', 0)} empresa(s) ok · "
                      f"{resultado.get('falhas', 0)} falha(s) · {resultado.get('duracao_s', 0)} s")
    if falhas:
        linhas.append(f'⚠️ Falharam ({len(falhas)}): {_nomes(falhas)}')
    if puladas:
        linhas.append(f'🔒 Puladas por procuração ({len(puladas)}): {_nomes(puladas)}')
    return '\n'.join(linhas)


def aviso_fila(resultado: Dict[str, Any], falhas_definitivas: List[str]) -> Optional[str]:
    if not falhas_definitivas and not resultado.get('teto'):
        return None
    linhas = ['🗂️ *Central e-CAC — Reprocessamentos do portal*']
    if resultado.get('teto'):
        linhas.append(f"⛔ Parou pelo teto de gasto: {resultado.get('motivo')}")
    if falhas_definitivas:
        linhas.append(f'❌ Desistiu após 3 tentativas: {"; ".join(falhas_definitivas[:10])}')
    return '\n'.join(linhas)
