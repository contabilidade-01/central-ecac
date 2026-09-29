"""Fila de reprocessamento por empresa (situação fiscal fora do lote mensal).

⚠️ DESVIO INTENCIONAL (18o) — NÃO existe no exe.

Caso de uso: o cliente recalculou o DAS pelo portal. Não adianta regerar o relatório
no dia seguinte — o pagamento leva 2 a 4 dias úteis para aparecer na Receita. O portal
pede "regere daqui a 5 dias úteis"; a fila guarda o pedido e o agendador executa no dia,
com as MESMAS travas do lote (procuração, teto de gasto).

Regras:
* uma empresa tem no máximo UM pedido pendente — pedido novo só antecipa a data;
* limite de reprocessamentos por empresa por mês (`INTEGRACAO_MAX_REPROC_MES`, padrão 2):
  cada regeração é uma chamada paga;
* falha não some: `tentativas` sobe e o item é reagendado para o próximo dia útil,
  até 3 vezes; depois fica marcado como falho para o escritório ver.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.integracao_models import FilaReprocessamento
from app.models import Company
from app.services.calendario_util import proximo_dia_util, somar_dias_uteis
from app.services.pendencias_service import hoje_brasil

logger = logging.getLogger(__name__)

MAX_TENTATIVAS = 3


def limite_mensal() -> int:
    try:
        return max(1, int(os.getenv('INTEGRACAO_MAX_REPROC_MES', '2')))
    except ValueError:
        return 2


def serializar(item: FilaReprocessamento) -> Dict[str, Any]:
    return {
        'id': item.id,
        'company_id': item.company_id,
        'cnpj': item.cnpj,
        'motivo': item.motivo,
        'origem': item.origem,
        'agendado_para': item.agendado_para.isoformat() if item.agendado_para else None,
        'criado_em': item.criado_em.isoformat() if item.criado_em else None,
        'tentativas': item.tentativas,
        'executado_em': item.executado_em.isoformat() if item.executado_em else None,
        'sucesso': item.sucesso,
        'resultado': item.resultado,
        'relatorio_id': item.relatorio_id,
        'pendente': item.executado_em is None,
    }


def pendente_da_empresa(company_id: int) -> Optional[FilaReprocessamento]:
    return (FilaReprocessamento.query
            .filter_by(company_id=company_id)
            .filter(FilaReprocessamento.executado_em.is_(None))
            .order_by(FilaReprocessamento.agendado_para.asc()).first())


def executados_no_mes(company_id: int, hoje: Optional[date] = None) -> int:
    hoje = hoje or hoje_brasil()
    inicio = datetime(hoje.year, hoje.month, 1)
    return (FilaReprocessamento.query
            .filter_by(company_id=company_id)
            .filter(FilaReprocessamento.executado_em >= inicio,
                    FilaReprocessamento.sucesso.is_(True))
            .count())


def agendar(company: Company, dias_uteis: int = 5, motivo: str = 'recalculo_guia',
            origem: str = 'interno', hoje: Optional[date] = None) -> Dict[str, Any]:
    """Cria (ou antecipa) o pedido. Devolve {'ok', 'item', 'mensagem'}."""
    hoje = hoje or hoje_brasil()
    dias_uteis = max(0, min(int(dias_uteis or 0), 30))
    alvo = somar_dias_uteis(hoje, dias_uteis) if dias_uteis else proximo_dia_util(hoje)

    if not company.ativo:
        return {'ok': False, 'mensagem': 'Empresa inativa no Central e-CAC.'}

    if executados_no_mes(company.id, hoje) >= limite_mensal():
        return {'ok': False, 'mensagem': (
            f'Limite de {limite_mensal()} reprocessamento(s) por mês já usado para esta '
            'empresa. O lote mensal traz o relatório atualizado.')}

    existente = pendente_da_empresa(company.id)
    if existente:
        if alvo < existente.agendado_para:
            existente.agendado_para = alvo
            existente.motivo = motivo
            db.session.commit()
            return {'ok': True, 'item': serializar(existente), 'mensagem': 'Pedido antecipado.'}
        return {'ok': True, 'item': serializar(existente), 'mensagem': 'Já havia um pedido pendente.'}

    item = FilaReprocessamento(company_id=company.id, cnpj=company.cnpj, motivo=motivo,
                               origem=origem, agendado_para=alvo)
    db.session.add(item)
    db.session.commit()
    return {'ok': True, 'item': serializar(item), 'mensagem': f'Agendado para {alvo.isoformat()}.'}


def listar(cnpj: Optional[str] = None, apenas_pendentes: bool = False,
           limite: int = 200) -> List[Dict[str, Any]]:
    consulta = FilaReprocessamento.query
    if cnpj:
        consulta = consulta.filter_by(cnpj=''.join(ch for ch in cnpj if ch.isdigit()))
    if apenas_pendentes:
        consulta = consulta.filter(FilaReprocessamento.executado_em.is_(None))
    return [serializar(i) for i in consulta.order_by(FilaReprocessamento.id.desc()).limit(limite).all()]


def vencidos(hoje: Optional[date] = None) -> List[FilaReprocessamento]:
    hoje = hoje or hoje_brasil()
    return (FilaReprocessamento.query
            .filter(FilaReprocessamento.executado_em.is_(None),
                    FilaReprocessamento.agendado_para <= hoje)
            .order_by(FilaReprocessamento.agendado_para.asc(), FilaReprocessamento.id.asc())
            .all())


def drenar(processar=None, hoje: Optional[date] = None) -> Dict[str, Any]:
    """Executa os pedidos vencidos. `processar(company)` → (sucesso, mensagem, relatorio_id).

    Só é chamado pelo agendador (ou por teste). Respeita procuração e teto ANTES de cada
    empresa, como o lote; ao bater no teto para e deixa o resto para o próximo ciclo.
    """
    from app.services.limite_gasto_service import LimiteGastoService
    from app.services.procuracao_service import ProcuracaoService

    hoje = hoje or hoje_brasil()
    itens = vencidos(hoje)
    if not itens:
        return {'executados': 0, 'sucesso': 0, 'falhas': 0, 'adiados': 0, 'teto': False}

    if processar is None:
        processar = _processar_situacao_fiscal

    executados = sucesso = falhas = adiados = 0
    for item in itens:
        company = db.session.get(Company, item.company_id)
        if not company or not company.ativo:
            item.executado_em = datetime.utcnow()
            item.sucesso = False
            item.resultado = 'empresa inexistente ou inativa'
            db.session.commit()
            falhas += 1
            continue

        pode, motivo = ProcuracaoService.pode_gastar(company)
        if not pode:
            _adiar(item, f'procuração: {motivo}')
            adiados += 1
            continue

        pode, motivo = LimiteGastoService.pode_gastar()
        if not pode:
            logger.warning('[FILA] teto de gasto atingido — %s', motivo)
            return {'executados': executados, 'sucesso': sucesso, 'falhas': falhas,
                    'adiados': adiados, 'teto': True, 'motivo': motivo}

        try:
            ok, mensagem, relatorio_id = processar(company)
        except Exception as exc:  # nunca derruba o agendador
            logger.exception('[FILA] falha ao reprocessar empresa %s', company.id)
            ok, mensagem, relatorio_id = False, str(exc), None

        executados += 1
        if ok:
            item.executado_em = datetime.utcnow()
            item.sucesso = True
            item.resultado = mensagem
            item.relatorio_id = relatorio_id
            sucesso += 1
        else:
            item.tentativas = (item.tentativas or 0) + 1
            if item.tentativas >= MAX_TENTATIVAS:
                item.executado_em = datetime.utcnow()
                item.sucesso = False
                item.resultado = f'desistiu após {item.tentativas} tentativas: {mensagem}'
                falhas += 1
            else:
                _adiar(item, mensagem)
                adiados += 1
        db.session.commit()

    return {'executados': executados, 'sucesso': sucesso, 'falhas': falhas,
            'adiados': adiados, 'teto': False}


def _adiar(item: FilaReprocessamento, motivo: str) -> None:
    item.agendado_para = somar_dias_uteis(hoje_brasil(), 1)
    item.resultado = f'adiado: {motivo}'[:1000]
    db.session.commit()


def _processar_situacao_fiscal(company: Company):
    from app.services.pendencias_service import ultimo_relatorio
    from app.services.report_service import ReportService

    resultado = ReportService().process_company(company.id)
    relatorio = ultimo_relatorio(company.id) if getattr(resultado, 'success', False) else None
    return (bool(getattr(resultado, 'success', False)),
            str(getattr(resultado, 'message', '')),
            relatorio.id if relatorio else None)
