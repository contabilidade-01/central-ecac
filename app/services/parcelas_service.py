"""Parcelas de parcelamento (Simples/MEI, PERT, RELP) para o portal — DESVIO 19.

Duas chamadas pagas, cada uma com trava:

* **Consultar** (`PARCELASPARAGERAR`, R$ ~0,24): quais parcelas podem ser emitidas e o
  valor. O resultado fica em `parcelas_consultas`; no mesmo dia (horário de Brasília) a
  tela lê de lá sem custo. `forcar` refaz, até `INTEGRACAO_MAX_EMISSOES_DIA` vezes/dia.
* **Emitir** (`GERARDAS...`, R$ ~0,32): passa por `das_emissao_service.emitir` (guia do
  dia reaproveitada, procuração, teto, limite diário) e só aceita parcela que consta na
  última consulta — competência digitada errada não vira chamada paga.

PGFN (SISPAR) não existe no Integra Contador: dívida ativa continua no Regularize.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.integracao_models import ParcelasConsulta
from app.models import Company
from app.services.api_usage_service import ApiUsageService
from app.services.parcelamentos_serpro_service import PARCELAMENTO_TYPES
from app.services.pendencias_service import hoje_brasil

logger = logging.getLogger(__name__)

TIPOS = tuple(PARCELAMENTO_TYPES.keys())   # PARC_SN, PARC_MEI, PERT_SN, PERT_MEI, RELP_SN, RELP_MEI


class ConsultaBloqueada(Exception):
    def __init__(self, mensagem: str, status: int = 409):
        super().__init__(mensagem)
        self.status = status


def _digits(v: Any) -> str:
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def rotulo(tipo: str) -> str:
    return PARCELAMENTO_TYPES.get(tipo, {}).get('label', tipo)


def _inicio_do_dia_utc() -> datetime:
    # consultado_em é UTC; o dia é o de Brasília (UTC-3, sem horário de verão).
    return datetime.combine(hoje_brasil(), datetime.min.time()) + timedelta(hours=3)


def _parcelas_do_corpo(corpo: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lê `listaParcelas` com tolerância a variações de nome (parcela/valor)."""
    dados = corpo.get('dados')
    if isinstance(dados, str):
        try:
            dados = json.loads(dados) if dados.strip() else {}
        except Exception:
            dados = {}
    if isinstance(dados, list):
        lista = dados
    elif isinstance(dados, dict):
        lista = dados.get('listaParcelas') or dados.get('parcelas') or []
    else:
        lista = []
    saida = []
    for item in lista if isinstance(lista, list) else []:
        if not isinstance(item, dict):
            continue
        parcela = _digits(item.get('parcela') or item.get('parcelaParaEmitir') or item.get('anoMesParcela'))
        if len(parcela) != 6:
            continue
        valor = item.get('valor', item.get('valorParcela'))
        try:
            valor = round(float(valor), 2) if valor is not None else None
        except (TypeError, ValueError):
            valor = None
        saida.append({'parcela': parcela, 'valor': valor})
    saida.sort(key=lambda x: x['parcela'])
    return saida


def _mensagens(corpo: Dict[str, Any]) -> Optional[str]:
    msgs = corpo.get('mensagens') or []
    textos = [str(m.get('texto') or m.get('mensagem') or '') for m in msgs if isinstance(m, dict)]
    textos = [t for t in textos if t]
    return ' · '.join(textos)[:500] or None


def serializar(c: Optional[ParcelasConsulta]) -> Dict[str, Any]:
    if not c:
        return {'consultado': False, 'parcelas': []}
    try:
        parcelas = json.loads(c.parcelas_json or '[]')
    except ValueError:
        parcelas = []
    return {
        'consultado': True,
        'id': c.id,
        'tipo': c.tipo,
        'rotulo': rotulo(c.tipo),
        'consultado_em': c.consultado_em.isoformat() + 'Z' if c.consultado_em else None,
        'hoje': bool(c.consultado_em and c.consultado_em >= _inicio_do_dia_utc()),
        'parcelas': parcelas,
        'mensagem': c.mensagem,
    }


def ultima(cnpj: str, tipo: str) -> Optional[ParcelasConsulta]:
    return (ParcelasConsulta.query.filter_by(cnpj=_digits(cnpj), tipo=tipo)
            .order_by(ParcelasConsulta.id.desc()).first())


def parcela_consta(cnpj: str, tipo: str, parcela: str) -> bool:
    c = ultima(cnpj, tipo)
    if not c:
        return False
    return any(p.get('parcela') == _digits(parcela) for p in serializar(c)['parcelas'])


def consultar(cnpj: str, tipo: str, forcar: bool = False, solicitado_por: Optional[str] = None,
              chamar=None) -> Dict[str, Any]:
    """Devolve a consulta (do dia, sem custo; ou nova, paga). Lança ConsultaBloqueada."""
    from app.services.das_emissao_service import _bloqueio_custo, limite_diario
    cnpj = _digits(cnpj)
    tipo = str(tipo or '').upper()
    if len(cnpj) != 14:
        raise ConsultaBloqueada('CNPJ inválido.', 400)
    if tipo not in TIPOS:
        raise ConsultaBloqueada(f'Tipo de parcelamento inválido: {tipo}.', 400)
    company = Company.query.filter_by(cnpj=cnpj).first()
    if not company:
        raise ConsultaBloqueada('Empresa não cadastrada no Central e-CAC.', 404)

    inicio = _inicio_do_dia_utc()
    do_dia = (ParcelasConsulta.query
              .filter(ParcelasConsulta.cnpj == cnpj, ParcelasConsulta.tipo == tipo,
                      ParcelasConsulta.consultado_em >= inicio)
              .order_by(ParcelasConsulta.id.desc()))
    if not forcar and do_dia.first():
        return {**serializar(do_dia.first()), 'reuso': True}
    if do_dia.count() >= limite_diario():
        raise ConsultaBloqueada(f'Limite de {limite_diario()} consulta(s) por dia para este tipo já '
                                'atingido. A última consulta continua valendo.', 429)

    bloqueio = _bloqueio_custo(company)
    if bloqueio:
        raise ConsultaBloqueada(bloqueio, 409)

    cfg = PARCELAMENTO_TYPES[tipo]['parcelas']
    if chamar is None:
        from app.services.serpro_das_service import consultar_parcelas_para_gerar as chamar
    corpo = chamar(cnpj, cfg['idSistema'], cfg['idServico'])

    custo = float(ApiUsageService._get_consulta_cost(1))
    try:
        ApiUsageService.register_usage(route_type='consultar', endpoint=f"{cfg['idSistema']}/{cfg['idServico']}",
                                       company_id=company.id)
    except Exception:
        db.session.rollback()
        logger.exception('Falha ao registrar custo da consulta de parcelas')

    parcelas = _parcelas_do_corpo(corpo or {})
    registro = ParcelasConsulta(
        company_id=company.id, cnpj=cnpj, tipo=tipo,
        parcelas_json=json.dumps(parcelas), mensagem=_mensagens(corpo or {}),
        solicitado_por=solicitado_por, custo_estimado=custo,
    )
    db.session.add(registro)
    db.session.commit()
    logger.info('[PARCELAS] %s %s: %d parcela(s) para emitir', tipo, cnpj, len(parcelas))
    return {**serializar(registro), 'reuso': False}
