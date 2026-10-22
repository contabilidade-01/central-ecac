"""Confirmar pendências na Receita para UMA empresa, na hora — desvio 20.

O portal (nescon-clientes) chama antes de o escritório mandar o alerta ao cliente: o
relatório de situação fiscal é refeito agora e o portal reimporta o espelho (custo zero)
quando terminar. Roda em thread (a SERPRO leva de segundos a alguns minutos para liberar
o relatório) e o portal acompanha por `status()`.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from flask import current_app

from app.extensions import db
from app.integracao_models import ConfirmacaoSitfis
from app.models import Company
from app.services.pendencias_service import hoje_brasil

logger = logging.getLogger(__name__)

#: pedido sem conclusão depois disso é considerado perdido (processo caiu) e libera outro
EM_ANDAMENTO_MAX = timedelta(minutes=15)


class ConfirmacaoBloqueada(Exception):
    def __init__(self, mensagem: str, status: int = 409):
        super().__init__(mensagem)
        self.status = status


def limite_diario() -> int:
    try:
        return max(1, int(os.getenv('INTEGRACAO_MAX_CONFIRMACOES_DIA', '2')))
    except ValueError:
        return 2


def _digits(v: Any) -> str:
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def _inicio_do_dia_utc() -> datetime:
    return datetime.combine(hoje_brasil(), datetime.min.time()) + timedelta(hours=3)


def serializar(c: Optional[ConfirmacaoSitfis]) -> Dict[str, Any]:
    if not c:
        return {'existe': False}
    from app.models import RelatorioSitFiscal
    rel = db.session.get(RelatorioSitFiscal, c.relatorio_id) if c.relatorio_id else None
    em_andamento = c.concluido_em is None and (datetime.utcnow() - c.iniciado_em) < EM_ANDAMENTO_MAX
    return {
        'existe': True,
        'id': c.id,
        'status': 'em_andamento' if em_andamento else ('concluida' if c.sucesso else 'erro'),
        'iniciado_em': c.iniciado_em.isoformat() + 'Z',
        'concluido_em': c.concluido_em.isoformat() + 'Z' if c.concluido_em else None,
        'mensagem': c.mensagem if c.concluido_em or not em_andamento else 'Consultando a Receita…',
        'relatorio_id': c.relatorio_id,
        'relatorio_data': rel.data_hora.isoformat() if rel and rel.data_hora else None,
        'solicitado_por': c.solicitado_por,
    }


def ultima(cnpj: str) -> Optional[ConfirmacaoSitfis]:
    return (ConfirmacaoSitfis.query.filter_by(cnpj=_digits(cnpj))
            .order_by(ConfirmacaoSitfis.id.desc()).first())


def _processar_padrao(company_id: int):
    from app.services.pendencias_service import ultimo_relatorio
    from app.services.report_service import ReportService
    r = ReportService().process_company(company_id)
    ok = bool(getattr(r, 'success', False))
    rel = ultimo_relatorio(company_id) if ok else None
    return ok, str(getattr(r, 'message', '')), (rel.id if rel else None)


def _executar(app, confirmacao_id: int, processar) -> None:
    with app.app_context():
        c = db.session.get(ConfirmacaoSitfis, confirmacao_id)
        if not c:
            return
        try:
            ok, mensagem, relatorio_id = processar(c.company_id)
        except Exception as exc:  # nunca derruba o processo
            logger.exception('[CONFIRMAR] falha na empresa %s', c.company_id)
            ok, mensagem, relatorio_id = False, str(exc), None
        c = db.session.get(ConfirmacaoSitfis, confirmacao_id)
        c.concluido_em = datetime.utcnow()
        c.sucesso = bool(ok)
        c.mensagem = (mensagem or ('Relatório atualizado.' if ok else 'Falhou.'))[:500]
        c.relatorio_id = relatorio_id
        db.session.commit()
        logger.info('[CONFIRMAR] %s: %s (%s)', c.cnpj, 'ok' if ok else 'erro', c.mensagem)


def iniciar(cnpj: str, solicitado_por: Optional[str] = None, processar=None,
            em_thread: bool = True) -> Dict[str, Any]:
    from app.services.das_emissao_service import _bloqueio_custo
    from app.services.mei_service import e_mei

    cnpj = _digits(cnpj)
    if len(cnpj) != 14:
        raise ConfirmacaoBloqueada('CNPJ inválido.', 400)
    company = Company.query.filter_by(cnpj=cnpj).first()
    if not company:
        raise ConfirmacaoBloqueada('Empresa não cadastrada no Central e-CAC.', 404)
    if not company.ativo:
        raise ConfirmacaoBloqueada('Empresa inativa no Central e-CAC.', 409)
    if e_mei(company.id):
        raise ConfirmacaoBloqueada('Empresa MEI: não há relatório de situação fiscal para confirmar.', 409)

    atual = ultima(cnpj)
    if atual and serializar(atual)['status'] == 'em_andamento':
        return {**serializar(atual), 'reuso': True}

    feitas = (ConfirmacaoSitfis.query
              .filter(ConfirmacaoSitfis.cnpj == cnpj, ConfirmacaoSitfis.iniciado_em >= _inicio_do_dia_utc())
              .count())
    if feitas >= limite_diario():
        raise ConfirmacaoBloqueada(f'Limite de {limite_diario()} confirmação(ões) por dia para esta empresa '
                                   'já atingido. A última consulta continua valendo.', 429)

    bloqueio = _bloqueio_custo(company)
    if bloqueio:
        raise ConfirmacaoBloqueada(bloqueio, 409)

    c = ConfirmacaoSitfis(company_id=company.id, cnpj=cnpj, solicitado_por=solicitado_por)
    db.session.add(c)
    db.session.commit()

    processar = processar or _processar_padrao
    app = current_app._get_current_object()
    if em_thread:
        threading.Thread(target=_executar, args=(app, c.id, processar), daemon=True,
                         name=f'confirmar-{cnpj}').start()
    else:
        _executar(app, c.id, processar)
    db.session.expire_all()
    return {**serializar(db.session.get(ConfirmacaoSitfis, c.id)), 'reuso': False}
