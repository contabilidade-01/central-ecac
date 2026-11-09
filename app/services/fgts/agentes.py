"""Agente FGTS no PC do escritório: autenticação, sinal de vida e fila de pedidos.

A Central nunca chama o agente: ele pede trabalho (`proximo`) e devolve o resultado. Por
isso o PC não abre porta para a internet. Regras que impedem trabalho em dobro:

* reserva com prazo (`reserva_min`): só o agente que reservou mexe no pedido;
* reserva vencida de CONSULTA volta para a fila;
* reserva vencida de EMISSÃO vai para `verificar_portal`: a guia pode ter saído antes da
  queda, então ninguém emite de novo sem um humano conferir no portal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import update

from app.extensions import db
from app.fgts_models import FgtsAgente, FgtsEvento
from app.services.fgts import parametros

CABECALHO = 'X-Agente-Token'
PREFIXO = '/api/fgts-agente/'
STATUS_VERIFICAR = 'verificar_portal'


class RecusaAgente(Exception):
    def __init__(self, mensagem: str, status: int = 409):
        super().__init__(mensagem)
        self.status = status


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def caminho_do_agente(caminho: str) -> bool:
    return caminho.startswith(PREFIXO)


def evento(tipo: str, mensagem: str = '', *, pedido_id=None, agente_id=None, cnpj=None) -> None:
    db.session.add(FgtsEvento(tipo=tipo, mensagem=(mensagem or '')[:500] or None,
                              pedido_id=pedido_id, agente_id=agente_id, cnpj=cnpj))


# ------------------------------------------------------------------ cadastro
def criar(nome: str, por: Optional[str]) -> Tuple[FgtsAgente, str]:
    """Devolve o agente e o token em claro, que só aparece aqui."""
    nome = (nome or '').strip()[:80]
    if not nome:
        raise RecusaAgente('Dê um nome ao agente, por exemplo o nome do PC.', 400)
    token = 'fgag_' + secrets.token_urlsafe(36)
    agente = FgtsAgente(nome=nome, token_hash=_hash(token), criado_por=(por or '')[:120] or None)
    db.session.add(agente)
    db.session.flush()
    evento('agente_criado', f'{nome} por {por or "?"}', agente_id=agente.id)
    db.session.commit()
    return agente, token


def revogar(agente_id: int, por: Optional[str]) -> bool:
    agente = db.session.get(FgtsAgente, agente_id)
    if not agente or agente.revogado_em:
        return False
    agente.revogado_em = datetime.utcnow()
    agente.login_ativo = False
    evento('agente_revogado', f'{agente.nome} por {por or "?"}', agente_id=agente.id)
    db.session.commit()
    return True


def autenticar(token: Optional[str]) -> Optional[FgtsAgente]:
    if not token or not str(token).startswith('fgag_'):
        return None
    alvo = _hash(str(token).strip())
    agente = FgtsAgente.query.filter_by(token_hash=alvo).first()
    if not agente or agente.revogado_em or not hmac.compare_digest(agente.token_hash, alvo):
        return None
    return agente


# ------------------------------------------------------------------ estado
def online(agente: FgtsAgente, agora: Optional[datetime] = None) -> bool:
    if agente.revogado_em or not agente.ultimo_sinal_em:
        return False
    agora = agora or datetime.utcnow()
    return agora - agente.ultimo_sinal_em <= timedelta(seconds=parametros.valor('agente_offline_s'))


def _versao_tupla(texto: str) -> tuple:
    return tuple(int(p) for p in str(texto or '0').split('.') if p.isdigit())


def versao_aceita(versao: Optional[str]) -> bool:
    minima = parametros.valor('versao_minima')
    return not minima or _versao_tupla(versao or '') >= _versao_tupla(minima)


def serializar(agente: FgtsAgente) -> Dict[str, Any]:
    return {'id': agente.id, 'nome': agente.nome, 'maquina': agente.maquina, 'versao': agente.versao,
            'online': online(agente), 'login_ativo': bool(agente.login_ativo and online(agente)),
            'ultimo_sinal_em': agente.ultimo_sinal_em.isoformat() if agente.ultimo_sinal_em else None,
            'pedido_atual_id': agente.pedido_atual_id, 'revogado': bool(agente.revogado_em),
            'criado_em': agente.criado_em.isoformat(), 'criado_por': agente.criado_por}


def prontidao() -> Dict[str, Any]:
    """As luzes da tela: executor, agentes online e com login."""
    executor = parametros.valor('executor')
    ativos = [a for a in FgtsAgente.query.filter(FgtsAgente.revogado_em.is_(None)).all()]
    com_login = [a for a in ativos if online(a) and a.login_ativo]
    return {'habilitado': parametros.valor('habilitado'), 'executor': executor,
            'agentes_online': sum(1 for a in ativos if online(a)), 'agentes_com_login': len(com_login),
            'pronto': bool(parametros.valor('habilitado')) and (executor == 'vps' or bool(com_login))}


def sinal(agente: FgtsAgente, dados: Dict[str, Any]) -> Dict[str, Any]:
    estava_logado = agente.login_ativo
    agente.ultimo_sinal_em = datetime.utcnow()
    agente.versao = str(dados.get('versao') or '')[:30] or None
    agente.maquina = str(dados.get('maquina') or '')[:120] or None
    agente.login_ativo = bool(dados.get('login_ativo'))
    if estava_logado and not agente.login_ativo:
        evento('login_caiu', agente.nome, agente_id=agente.id)
    liberar_expirados()
    db.session.commit()
    return {'ok': True, 'versao_aceita': versao_aceita(agente.versao),
            'executor': parametros.valor('executor'), 'habilitado': parametros.valor('habilitado'),
            'config': configuracao_do_agente()}


def configuracao_do_agente() -> Dict[str, Any]:
    chaves = ('agente_sinal_s', 'ritmo_s', 'reserva_min', 'captcha_timeout_s', 'login_timeout_s',
              'max_competencias', 'certificado_nome', 'tolerancia_centavos', 'janela_inicio',
              'janela_fim', 'so_dias_uteis', 'corte_pagar_hoje')
    return {**{c: parametros.valor(c) for c in chaves}, 'filtros': parametros.filtros_portal()}


# ------------------------------------------------------------------ fila
def dentro_da_janela(agora: Optional[datetime] = None) -> bool:
    from app.services.calendario_util import eh_dia_util
    agora = agora or datetime.now()
    if parametros.valor('so_dias_uteis') and not eh_dia_util(agora.date()):
        return False
    hora = agora.strftime('%H:%M')
    return parametros.valor('janela_inicio') <= hora <= parametros.valor('janela_fim')


def liberar_expirados(agora: Optional[datetime] = None) -> int:
    from app.integracao_models import FgtsPedido
    agora = agora or datetime.utcnow()
    vencidos = (FgtsPedido.query.filter(FgtsPedido.agente_id.isnot(None),
                                        FgtsPedido.status.in_(('executando', 'aguardando_captcha')),
                                        FgtsPedido.reservado_ate < agora).all())
    for p in vencidos:
        if p.acao == 'emitir' and p.etapa == 'emitindo':
            p.status, p.mensagem = STATUS_VERIFICAR, ('A emissão foi interrompida. Confira no portal se a guia '
                                                      'saiu antes de pedir de novo.')
            evento('emissao_interrompida', '', pedido_id=p.id, agente_id=p.agente_id, cnpj=p.cnpj)
        else:
            p.status, p.etapa, p.mensagem = 'fila', 'fila', None
            evento('reserva_vencida', '', pedido_id=p.id, agente_id=p.agente_id, cnpj=p.cnpj)
        p.agente_id, p.reservado_ate = None, None
    return len(vencidos)


def _pedido_do_agente(agente: FgtsAgente, pedido_id: int):
    from app.integracao_models import FgtsPedido
    pedido = db.session.get(FgtsPedido, pedido_id)
    if not pedido or pedido.agente_id != agente.id:
        raise RecusaAgente('Este pedido não está reservado para este agente.', 404)
    return pedido


def _renovar(pedido) -> None:
    pedido.reservado_ate = datetime.utcnow() + timedelta(minutes=parametros.valor('reserva_min'))
    pedido.atualizado_em = datetime.utcnow()


def reservar_proximo(agente: FgtsAgente) -> Optional[Dict[str, Any]]:
    """Reserva o pedido mais antigo da fila. None quando não há trabalho para este agente."""
    from app.integracao_models import FgtsPedido
    from app.services import fgts_pedidos_service as servico
    liberar_expirados()
    db.session.commit()
    if (parametros.valor('executor') != 'agente' or not parametros.valor('habilitado')
            or not agente.login_ativo or not online(agente) or not versao_aceita(agente.versao)
            or not dentro_da_janela()):
        return None
    fila = FgtsPedido.query.filter_by(status='fila', executor='agente').order_by(FgtsPedido.id).limit(5).all()
    for candidato in fila:
        prazo = datetime.utcnow() + timedelta(minutes=parametros.valor('reserva_min'))
        tomado = db.session.execute(
            update(FgtsPedido)
            .where(FgtsPedido.id == candidato.id, FgtsPedido.status == 'fila')
            .values(status='executando', etapa='perfil', agente_id=agente.id, reservado_ate=prazo,
                    iniciado_em=datetime.utcnow(), atualizado_em=datetime.utcnow()))
        if tomado.rowcount != 1:
            db.session.rollback()
            continue
        agente.pedido_atual_id = candidato.id
        evento('pedido_reservado', candidato.acao, pedido_id=candidato.id, agente_id=agente.id, cnpj=candidato.cnpj)
        db.session.commit()
        pedido = db.session.get(FgtsPedido, candidato.id)
        db.session.refresh(pedido)
        dados = servico.resultado(pedido)
        return {'id': pedido.id, 'acao': pedido.acao, 'cnpj': pedido.cnpj,
                'competencias': dados.get('escolha') or ([pedido.competencia] if pedido.competencia else []),
                'data_pagamento': dados.get('data_pagamento'),
                'reservado_ate': pedido.reservado_ate.isoformat() + 'Z'}
    return None


ETAPAS_AGENTE = ('perfil', 'consultando', 'captcha', 'emitindo', 'login')


def progresso(agente: FgtsAgente, pedido_id: int, dados: Dict[str, Any]) -> Dict[str, Any]:
    pedido = _pedido_do_agente(agente, pedido_id)
    if pedido.status not in ('executando', 'aguardando_captcha'):
        raise RecusaAgente('O pedido já foi encerrado.')
    etapa = str(dados.get('etapa') or '')
    if etapa not in ETAPAS_AGENTE:
        raise RecusaAgente('Etapa desconhecida.', 400)
    pedido.etapa = etapa
    pedido.status = 'aguardando_captcha' if etapa == 'captcha' else 'executando'
    if dados.get('mensagem') is not None:
        pedido.mensagem = str(dados['mensagem'])[:500]
    _renovar(pedido)
    db.session.commit()
    return {'ok': True, 'reservado_ate': pedido.reservado_ate.isoformat() + 'Z'}


def _competencias_validas(itens: Any) -> list:
    saida = []
    for c in itens if isinstance(itens, list) else []:
        if not isinstance(c, dict):
            continue
        comp = ''.join(ch for ch in str(c.get('competencia') or '') if ch.isdigit())
        if len(comp) != 6:
            continue
        item = {'competencia': comp}
        for chave in ('total', 'vencimento', 'situacao', 'tem_guia', 'tem_consignado'):
            if chave in c:
                item[chave] = c[chave]
        item['grupos'] = [{k: g.get(k) for k in ('tipo', 'total', 'pagamento_ate', 'tem_guia', 'tem_consignado')}
                          for g in (c.get('grupos') or []) if isinstance(g, dict)][:10]
        saida.append(item)
    return saida


def resultado(agente: FgtsAgente, pedido_id: int, dados: Dict[str, Any]) -> Dict[str, Any]:
    """Encerra o pedido. Erro vem com código e mensagem legível; sucesso, com o que foi lido."""
    from app.services import fgts_pedidos_service as servico
    pedido = _pedido_do_agente(agente, pedido_id)
    if pedido.status not in ('executando', 'aguardando_captcha'):
        raise RecusaAgente('O pedido já foi encerrado.')
    atual = servico.resultado(pedido)
    erro = dados.get('erro') if isinstance(dados.get('erro'), dict) else None
    if erro:
        codigo = str(erro.get('codigo') or 'falha')[:40]
        servico.gravar_resultado(pedido, {**atual, 'erro_codigo': codigo})
        servico.atualizar(pedido, status='erro', mensagem=str(erro.get('mensagem') or 'Falha no agente.'))
        evento('pedido_erro', codigo, pedido_id=pedido.id, agente_id=agente.id, cnpj=pedido.cnpj)
    else:
        if pedido.acao == 'consultar':
            atual['competencias'] = _competencias_validas(dados.get('competencias'))
            atual['datas_permitidas'] = sorted({str(d)[:10] for d in (dados.get('datas_permitidas') or [])})
        if pedido.acao == 'emitir' and not atual.get('guias'):
            raise RecusaAgente('Nenhuma guia foi entregue para este pedido de emissão.')
        atual['executor'] = 'agente'
        servico.gravar_resultado(pedido, atual)
        servico.atualizar(pedido, status='concluido', etapa='concluido', mensagem=None)
        evento('pedido_concluido', pedido.acao, pedido_id=pedido.id, agente_id=agente.id, cnpj=pedido.cnpj)
    pedido.reservado_ate = None   # agente_id fica: registra quem executou
    agente.pedido_atual_id = None
    db.session.commit()
    return {'ok': True, 'status': pedido.status}


def marcar_verificado(pedido_id: int, guia_saiu: bool, por: Optional[str]) -> None:
    """Depois de um humano conferir no portal uma emissão interrompida."""
    from app.integracao_models import FgtsPedido
    pedido = db.session.get(FgtsPedido, pedido_id)
    if not pedido or pedido.status != STATUS_VERIFICAR:
        raise RecusaAgente('Este pedido não está aguardando conferência.', 404)
    pedido.status = 'concluido' if guia_saiu else 'erro'
    pedido.mensagem = ('Conferido no portal: a guia saiu.' if guia_saiu
                       else 'Conferido no portal: a guia não saiu. Peça de novo.')
    pedido.concluido_em = datetime.utcnow()
    evento('emissao_conferida', f'saiu={guia_saiu} por {por or "?"}', pedido_id=pedido.id, cnpj=pedido.cnpj)
    db.session.commit()


def json_seguro(texto: Optional[str]) -> Dict[str, Any]:
    try:
        return json.loads(texto or '{}')
    except ValueError:
        return {}
