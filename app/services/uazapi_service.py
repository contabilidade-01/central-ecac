"""Cliente uazapi (WhatsApp) — porte fiel de `api/src/uazapi.js`, `whatsappDestino.js` e
do limitador de `alertasEnvio.js` do portal nescon-clientes.

⚠️ DESVIO INTENCIONAL (19o) — NÃO existe no exe. Pedido do Jean (30/09/2026): "deixar
tudo igual" ao portal, para os dois sistemas mandarem WhatsApp pela MESMA instância com
as MESMAS regras:

1. **Só cliente cadastrado recebe mensagem.** Toda saída passa por
   `exigir_destino_permitido`: número de empresa ativa com contato cadastrado
   (`contatos_empresa`) ou número do escritório. Desligar só em emergência:
   `WHATSAPP_SO_CLIENTES=false`.
2. **Janela diurna** (08–19h, dia útil) — quem chama decide se enfileira; aqui só se
   expõe `pode_enviar_agora`.
3. **Teto por hora** (`ALERTAS_MAX_POR_HORA`, padrão 180), **pausa entre envios**
   (`ALERTAS_THROTTLE_S`, 0,6 s) e **"digitando…"** (`ALERTAS_DELAY_MS`, 1200).
   ⚠️ O teto é por PROCESSO: o portal tem o dele. Como a instância é uma só, deixe a soma
   dos dois abaixo do que o número aguenta (ex.: 90 aqui + 90 lá).
4. **Retentativa** uma vez em falha transitória; token inválido e destino não permitido
   não se repetem.
5. **Não manda para o próprio número** da instância (falha em silêncio na uazapi).
6. **Tudo fica registrado** em `whatsapp_envios` (enviado/falhou/bloqueado, motivo).

Diferença única do portal: `enviar_documento` aceita **bytes** além de URL — o PDF de
DAS/parcela mora no volume deste sistema e não tem URL pública; vai em base64 (data URI).
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import requests

from app.extensions import db
from app.services import whatsapp_numero as num
from app.services.janela_envio import pode_enviar_agora

logger = logging.getLogger(__name__)


class UazapiNaoConfigurado(Exception):
    pass


class UazapiTokenInvalido(Exception):
    pass


class UazapiDestinoNaoPermitido(Exception):
    """Número que não é cliente ativo cadastrado (nem do escritório). Não adianta repetir."""


# ------------------------------------------------------------------ configuração

def _env_num(nome: str, padrao: float) -> float:
    try:
        return float(os.getenv(nome, '') or padrao)
    except ValueError:
        return padrao


def credenciais() -> Dict[str, str]:
    return {
        'subdominio': (os.getenv('UAZAPI_SUBDOMAIN') or '').strip(),
        'token': (os.getenv('UAZAPI_TOKEN') or '').strip(),
    }


def configurado() -> bool:
    c = credenciais()
    return bool(c['subdominio'] and c['token'])


def base_url() -> str:
    return f"https://{credenciais()['subdominio']}.uazapi.com"


def _cabecalhos() -> Dict[str, str]:
    return {'token': credenciais()['token'], 'Content-Type': 'application/json'}


def throttle_s() -> float:
    return max(0.0, _env_num('ALERTAS_THROTTLE_S', 0.6))


def max_por_hora() -> int:
    return max(1, int(_env_num('ALERTAS_MAX_POR_HORA', 180)))


def delay_digitando_ms() -> int:
    return max(0, int(_env_num('ALERTAS_DELAY_MS', 1200)))


def trava_ligada() -> bool:
    return (os.getenv('WHATSAPP_SO_CLIENTES') or '').strip().lower() != 'false'


# Transporte injetável (testes). Assinatura: (metodo, url, headers, json, timeout) -> resposta
# com .status_code e .text — o mesmo contrato de `requests`.
_transporte = None


def usar_transporte(fn) -> None:
    global _transporte
    _transporte = fn


def chamar(caminho: str, metodo: str = 'GET', corpo: Optional[dict] = None,
           timeout_s: float = 30.0) -> Dict[str, Any]:
    if not configurado():
        raise UazapiNaoConfigurado('UAZAPI_SUBDOMAIN/UAZAPI_TOKEN não configurados.')
    url = f'{base_url()}{caminho}'
    try:
        if _transporte:
            resp = _transporte(metodo, url, _cabecalhos(), corpo, timeout_s)
        else:
            resp = requests.request(metodo, url, headers=_cabecalhos(), json=corpo, timeout=timeout_s)
    except requests.RequestException as exc:
        raise Exception(f'uazapi inacessível: {exc}') from exc
    if resp.status_code == 401:
        raise UazapiTokenInvalido(
            "Token uazapi rejeitado ou instância desconectada. Confira o painel (status 'connected'?).")
    texto = resp.text or ''
    if not 200 <= resp.status_code < 300:
        raise Exception(f'uazapi HTTP {resp.status_code}: {texto[:200]}')
    try:
        dados = resp.json() if hasattr(resp, 'json') else None
        if dados is None:
            import json as _json
            dados = _json.loads(texto) if texto else {}
        return dados if isinstance(dados, dict) else {'dados': dados}
    except Exception:
        return {}


# ------------------------------------------------------------ status / owner

def status_instancia() -> Dict[str, Any]:
    """Diagnóstico pronto para a tela. Nunca lança."""
    if not configurado():
        return {'ok': False, 'categoria': 'nao_configurado',
                'mensagem': 'uazapi não configurada no ambiente.'}
    try:
        j = chamar('/instance/status', timeout_s=15)
        inst = j.get('instance') or {}
        status = inst.get('status') or 'desconhecido'
        if status != 'connected':
            return {'ok': False, 'categoria': 'desconectado',
                    'mensagem': f"Instância está '{status}' (esperado 'connected'). "
                                'Abra o painel e leia o QR code de novo.'}
        return {'ok': True, 'categoria': 'ok',
                'mensagem': f"Conectado ao número {inst.get('owner') or '?'} "
                            f"({inst.get('profileName') or 'sem nome'}).",
                'owner': inst.get('owner')}
    except UazapiTokenInvalido as exc:
        return {'ok': False, 'categoria': 'token_invalido', 'mensagem': str(exc)}
    except Exception as exc:
        return {'ok': False, 'categoria': 'rede', 'mensagem': f'Falha ao falar com a uazapi: {exc}'}


_owner_cache: Dict[str, Any] = {'valor': None, 'ts': 0.0}


def owner() -> Optional[str]:
    """Número conectado na instância, em cache de 2 min."""
    if time.time() - _owner_cache['ts'] < 120:
        return _owner_cache['valor']
    s = status_instancia()
    _owner_cache.update({'valor': s.get('owner') if s.get('ok') else None, 'ts': time.time()})
    return _owner_cache['valor']


def ler_webhook_cadastrado() -> Dict[str, Any]:
    if not configurado():
        return {'ok': False, 'motivo': 'UAZAPI_SUBDOMAIN/TOKEN ausentes no container.'}

    def _url_de(j: dict) -> str:
        w = j.get('webhook')
        return str(j.get('url') or j.get('webhookUrl') or j.get('webhook_url')
                   or (w if isinstance(w, str) else (w or {}).get('url')) or '')

    try:
        bruto = chamar('/webhook', timeout_s=15)
        url = _url_de(bruto)
        if not url:
            try:
                inst = chamar('/instance', timeout_s=15)
                url = _url_de(inst)
            except Exception:
                pass
        import re
        return {'ok': True,
                'url_mascarada': re.sub(r'([?&]token=)[^&]+', r'\1***', url, flags=re.I) or None,
                'chaves': list(bruto.keys())[:20]}
    except Exception as exc:
        return {'ok': False, 'motivo': str(exc)}


def verificar_numeros(numeros: List[str]) -> Dict[str, Dict[str, Any]]:
    """Existe no WhatsApp? `POST /chat/check` da uazapi. Devolve por número normalizado:
    {'existe': bool|None, 'jid': str|None, 'erro': str|None}. Best-effort: se a uazapi não
    tiver o serviço, tudo volta com `existe=None` e o motivo."""
    limpos = [n for n in (num.normalizar(x) for x in numeros) if n]
    saida = {n: {'existe': None, 'jid': None, 'erro': None} for n in limpos}
    if not limpos:
        return saida
    try:
        j = chamar('/chat/check', metodo='POST', corpo={'numbers': limpos}, timeout_s=30)
    except Exception as exc:
        for n in limpos:
            saida[n]['erro'] = str(exc)[:200]
        return saida
    itens = j.get('dados') if isinstance(j.get('dados'), list) else (
        j.get('users') or j.get('numbers') or j.get('result') or [])
    if isinstance(itens, dict):
        itens = [itens]
    for item in itens or []:
        if not isinstance(item, dict):
            continue
        query = num.normalizar(item.get('query') or item.get('number') or item.get('numero') or '')
        jid = item.get('jid') or item.get('JID') or ''
        existe = item.get('isInWhatsapp')
        if existe is None:
            existe = item.get('exists')
        if existe is None:
            existe = bool(jid)
        alvo = query or num.normalizar(str(jid).split('@')[0])
        if alvo in saida:
            saida[alvo] = {'existe': bool(existe), 'jid': str(jid) or None, 'erro': None}
    return saida


# ------------------------------------------------------- destino permitido

_TTL_S = 60.0
_cache_destino: Dict[str, Any] = {'clientes': None, 'escritorio': None, 'ts': 0.0}


def classificar_destino(numero: str, clientes: Set[str], escritorio: Set[str]) -> Dict[str, Any]:
    """Função pura, idêntica à do portal."""
    chave = num.chave_numero(numero)
    if not chave:
        return {'ok': False, 'tipo': None, 'motivo': 'Número inválido'}
    if chave in clientes:
        return {'ok': True, 'tipo': 'cliente'}
    if chave in escritorio:
        return {'ok': True, 'tipo': 'escritorio'}
    return {'ok': False, 'tipo': None,
            'motivo': 'Número não pertence a nenhum cliente ativo cadastrado'}


def numeros_do_escritorio() -> List[str]:
    return [n for n in ((os.getenv('ADMIN_WHATSAPP') or '').strip(),
                        (os.getenv('ESCRITORIO_WHATSAPP') or '').strip()) if n]


def _carregar_destinos() -> Dict[str, Any]:
    from app.integracao_models import ContatoEmpresa
    from app.models import Company

    clientes: Set[str] = set()
    linhas = (db.session.query(ContatoEmpresa.whatsapp, ContatoEmpresa.whatsapp_2)
              .join(Company, Company.id == ContatoEmpresa.company_id)
              .filter(Company.ativo.is_(True), ContatoEmpresa.ativo.is_(True)).all())
    for w1, w2 in linhas:
        for n in (w1, w2):
            k = num.chave_numero(n)
            if k:
                clientes.add(k)
    escritorio = {k for k in (num.chave_numero(n) for n in numeros_do_escritorio()) if k}
    _cache_destino.update({'clientes': clientes, 'escritorio': escritorio, 'ts': time.time()})
    return _cache_destino


def destino_permitido(numero: str) -> Dict[str, Any]:
    if not trava_ligada():
        return {'ok': True, 'tipo': 'trava_desligada'}
    c = _cache_destino
    if c['clientes'] is None or time.time() - c['ts'] > _TTL_S:
        c = _carregar_destinos()
    r = classificar_destino(numero, c['clientes'], c['escritorio'])
    if not r['ok'] and c['ts'] < time.time() - 2:
        c = _carregar_destinos()
        r = classificar_destino(numero, c['clientes'], c['escritorio'])
    return r


def limpar_cache_destinos() -> None:
    _cache_destino.update({'clientes': None, 'escritorio': None, 'ts': 0.0})


def exigir_destino_permitido(numero: str) -> None:
    r = destino_permitido(numero)
    if not r['ok']:
        logger.warning('[uazapi] envio bloqueado para ...%s: %s', num.so_digitos(numero)[-4:], r['motivo'])
        raise UazapiDestinoNaoPermitido(f"Envio bloqueado: {r['motivo']}.")


# ------------------------------------------------------------- teto por hora

_carimbos: List[float] = []
_lock = threading.Lock()


def sob_o_teto() -> bool:
    agora = time.time()
    with _lock:
        _carimbos[:] = [t for t in _carimbos if agora - t < 3600]
        return len(_carimbos) < max_por_hora()


def marcar_enviado() -> None:
    with _lock:
        _carimbos.append(time.time())


def enviados_na_ultima_hora() -> int:
    agora = time.time()
    with _lock:
        return len([t for t in _carimbos if agora - t < 3600])


def calcular_backoff(tentativas: int, maximo: int = 5) -> Dict[str, Any]:
    t = max(1, int(tentativas or 1))
    if t >= maximo:
        return {'esgotou': True, 'proxima_min': None}
    return {'esgotou': False, 'proxima_min': min(2 ** t, 360)}


# ---------------------------------------------------------------- envio bruto

def _id_da_resposta(r: Any) -> Optional[str]:
    if not isinstance(r, dict):
        return None
    for chave in ('id', 'messageid', 'messageId'):
        if r.get(chave):
            return str(r[chave])[:120]
    for pai in ('key', 'message', 'data'):
        filho = r.get(pai)
        if isinstance(filho, dict) and filho.get('id'):
            return str(filho['id'])[:120]
    return None


def enviar_texto(numero: str, texto: str, delay_ms: int = 0) -> Dict[str, Any]:
    exigir_destino_permitido(numero)
    corpo: Dict[str, Any] = {'number': numero, 'text': texto}
    if delay_ms > 0:
        corpo['delay'] = int(delay_ms)
    return chamar('/send/text', metodo='POST', corpo=corpo, timeout_s=60)


def enviar_documento(numero: str, nome_arquivo: str, file_url: Optional[str] = None,
                     conteudo: Optional[bytes] = None, legenda: Optional[str] = None,
                     delay_ms: int = 0, mime: str = 'application/pdf') -> Dict[str, Any]:
    """PDF por URL pública (como o portal) ou por bytes (base64 data URI)."""
    exigir_destino_permitido(numero)
    if not file_url and not conteudo:
        raise ValueError('Informe file_url ou conteudo.')
    arquivo = file_url or f'data:{mime};base64,{base64.b64encode(conteudo).decode()}'
    corpo: Dict[str, Any] = {'number': numero, 'type': 'document', 'file': arquivo, 'docName': nome_arquivo}
    if legenda:
        corpo['text'] = legenda
    if delay_ms > 0:
        corpo['delay'] = int(delay_ms)
    return chamar('/send/media', metodo='POST', corpo=corpo, timeout_s=120)


def enviar_com_retry(fn, tentativas: int = 2):
    """Uma re-tentativa em falha transitória. Token inválido/destino não se repetem."""
    ultimo: Optional[Exception] = None
    for i in range(tentativas):
        try:
            return fn()
        except (UazapiTokenInvalido, UazapiDestinoNaoPermitido, UazapiNaoConfigurado):
            raise
        except Exception as exc:
            ultimo = exc
            logger.warning('[uazapi] envio falhou (%s/%s): %s', i + 1, tentativas, exc)
            if i + 1 < tentativas:
                time.sleep(0.8 * (i + 1))
    raise ultimo  # type: ignore[misc]


# --------------------------------------------------- envio "completo" (com log)

def _registrar(company_id, numero, tipo, contexto, texto, nome_arquivo, status, erro,
               mensagem_id, tentativas, enviado_por):
    from app.integracao_models import WhatsappEnvio
    try:
        db.session.add(WhatsappEnvio(
            company_id=company_id, numero=numero, tipo=tipo, contexto=contexto[:40],
            texto=(texto or '')[:4000] or None, nome_arquivo=nome_arquivo, status=status,
            erro=(erro or '')[:500] or None, mensagem_id=mensagem_id, tentativas=tentativas,
            enviado_por=enviado_por))
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception('[uazapi] não registrou o envio')


def enviar(numero_bruto: str, texto: Optional[str] = None, *, company_id: Optional[int] = None,
           contexto: str = 'manual', nome_arquivo: Optional[str] = None,
           conteudo: Optional[bytes] = None, file_url: Optional[str] = None,
           enviado_por: Optional[str] = None, respeitar_janela: bool = True,
           delay_ms: Optional[int] = None) -> Dict[str, Any]:
    """Envio com TODAS as travas e com registro. Nunca lança: devolve {'ok', 'motivo', ...}.

    Ordem das travas (a mesma de `docNotify.js`): configurada? → número válido? → janela? →
    próprio número? → teto/hora? → destino permitido (dentro do enviar_*) → retry.
    """
    tipo = 'documento' if (conteudo or file_url) else 'texto'

    def falha(motivo: str, status: str = 'bloqueado', numero: Optional[str] = None,
              tentativas: int = 0) -> Dict[str, Any]:
        _registrar(company_id, numero, tipo, contexto, texto, nome_arquivo, status, motivo,
                   None, tentativas, enviado_por)
        return {'ok': False, 'motivo': motivo, 'numero': numero}

    if not configurado():
        return falha('uazapi não configurada')
    v = num.validar(numero_bruto)
    if not v['ok']:
        return falha(v['motivo'])
    numero = v['numero']
    if respeitar_janela and not pode_enviar_agora():
        return falha('fora da janela diurna (08–19h, dia útil)', numero=numero)
    meu = owner()
    if meu and num.normalizar(meu) == numero:
        return falha('é o próprio número da instância — o envio falharia em silêncio', numero=numero)
    if not sob_o_teto():
        return falha('teto de envios por hora atingido', numero=numero)

    atraso = delay_digitando_ms() if delay_ms is None else delay_ms
    tentativas = 0

    def _uma_vez():
        nonlocal tentativas
        tentativas += 1
        if tipo == 'documento':
            return enviar_documento(numero, nome_arquivo or 'documento.pdf', file_url=file_url,
                                    conteudo=conteudo, legenda=texto, delay_ms=atraso)
        return enviar_texto(numero, texto or '', delay_ms=atraso)

    try:
        r = enviar_com_retry(_uma_vez)
    except UazapiDestinoNaoPermitido as exc:
        return falha(str(exc), numero=numero, tentativas=tentativas)
    except Exception as exc:
        return falha(str(exc), status='falhou', numero=numero, tentativas=tentativas)

    marcar_enviado()
    if throttle_s() > 0:
        time.sleep(throttle_s())
    mid = _id_da_resposta(r)
    _registrar(company_id, numero, tipo, contexto, texto, nome_arquivo, 'enviado', None, mid,
               tentativas, enviado_por)
    return {'ok': True, 'numero': numero, 'mensagem_id': mid, 'enviado_em': datetime.utcnow().isoformat()}
