"""Pendências de uma empresa em JSON — o que o portal do cliente consome.

⚠️ DESVIO INTENCIONAL (18o) — NÃO existe no exe. Lê SOMENTE o que já está no banco
(último relatório de situação fiscal); nunca chama a SERPRO, logo custa zero.

Três regras que este módulo aplica e que o portal NÃO deve repetir por conta própria,
para que "em atraso" signifique a mesma coisa nos dois sistemas:

1. **Em atraso** = situação `DEVEDOR` **e** vencimento anterior a hoje (Brasília).
   `A ANALISAR-A VENCER` nunca é atraso. Débito sem vencimento legível também não —
   vai para revisão, não para cobrança.
2. **Fantasma** (16o desvio): o leitor do PDF já produziu linhas com ano `0001`, valor
   zero e competência impossível. Cada débito sai com `valido` e `motivos`; o portal
   só notifica o que é válido e mostra o resto ao escritório.
3. **Relatório recente**: a empresa pulada no lote (procuração travada, teto, erro)
   fica com relatório velho. Quem consome passa `desde` e recebe `relatorio_recente`
   para não cobrar em cima de dado de dois meses atrás.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.extensions import db
from app.models import Company, DebitoRelatorio, PendenciaRelatorio, RelatorioSitFiscal

#: quais tipos de débito o portal consegue recalcular pela API, e como
GUIA_POR_TIPO = {
    'SN': 'SN',            # PGDASD/GERARDAS12 com dataConsolidacao
    'MEI': 'MEI',          # PGMEI/GERARDASPDF21
    'INSS': 'DCTFWEB',     # DCTFWEB/GERARGUIA31 — só competência corrente (sem data)
}

_RE_PA_MES = re.compile(r'^(\d{2})/(\d{4})$')
_RE_PA_DIA = re.compile(r'^(\d{2})/(\d{2})/(\d{4})$')


def hoje_brasil() -> date:
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def _f(valor: Any) -> float:
    if valor is None:
        return 0.0
    if isinstance(valor, Decimal):
        return float(valor)
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _ano_plausivel(ano: int, hoje: date) -> bool:
    return 2000 <= ano <= hoje.year + 1


def periodo_para_aaaamm(periodo_apuracao: Optional[str]) -> Optional[str]:
    """'MM/AAAA' ou 'DD/MM/AAAA' → 'AAAAMM' (o que a emissão de DAS pede)."""
    texto = (periodo_apuracao or '').strip()
    m = _RE_PA_MES.match(texto)
    if m:
        return f'{m.group(2)}{m.group(1)}'
    m = _RE_PA_DIA.match(texto)
    if m:
        return f'{m.group(3)}{m.group(2)}'
    return None


def validar_debito(debito: DebitoRelatorio, hoje: Optional[date] = None) -> List[str]:
    """Motivos pelos quais este débito NÃO deve ir para o cliente. Vazio = válido."""
    hoje = hoje or hoje_brasil()
    motivos: List[str] = []

    if not (debito.receita or '').strip():
        motivos.append('sem descrição da receita')

    pa = periodo_para_aaaamm(debito.periodo_apuracao)
    if not pa:
        motivos.append('competência ilegível')
    else:
        ano, mes = int(pa[:4]), int(pa[4:])
        if not _ano_plausivel(ano, hoje) or not 1 <= mes <= 12:
            motivos.append('competência fora do plausível')

    if debito.data_vencimento is None:
        motivos.append('sem data de vencimento')
    elif not _ano_plausivel(debito.data_vencimento.year, hoje):
        motivos.append('vencimento fora do plausível')

    if _f(debito.saldo_devedor_total) <= 0 and _f(debito.valor_original) <= 0:
        motivos.append('valor zerado')

    return motivos


def em_atraso(debito: DebitoRelatorio, hoje: Optional[date] = None) -> bool:
    hoje = hoje or hoje_brasil()
    situacao = (debito.situacao or '').upper()
    if 'DEVEDOR' not in situacao:
        return False
    if debito.data_vencimento is None:
        return False
    return debito.data_vencimento < hoje


def _serializar_debito(debito: DebitoRelatorio, hoje: date) -> Dict[str, Any]:
    motivos = validar_debito(debito, hoje)
    tipo = (debito.tipo or 'OUTROS').upper()
    return {
        'id': debito.id,
        'tipo': tipo,
        'receita': debito.receita or '',
        'periodo_apuracao': debito.periodo_apuracao or '',
        'periodo_aaaamm': periodo_para_aaaamm(debito.periodo_apuracao),
        'data_vencimento': debito.data_vencimento.isoformat() if debito.data_vencimento else None,
        'valor_original': round(_f(debito.valor_original), 2),
        'saldo_devedor': round(_f(debito.saldo_devedor), 2),
        'multa': round(_f(debito.multa), 2),
        'juros': round(_f(debito.juros), 2),
        'saldo_devedor_total': round(_f(debito.saldo_devedor_total), 2),
        'situacao': debito.situacao or '',
        'em_atraso': not motivos and em_atraso(debito, hoje),
        'valido': not motivos,
        'motivos': motivos,
        'guia': GUIA_POR_TIPO.get(tipo),
        # SN e MEI aceitam data de consolidação; a DARF da DCTFWeb só sai por competência.
        'recalculo_disponivel': tipo in ('SN', 'MEI'),
    }


def _serializar_omissoes(pendencias: List[PendenciaRelatorio]) -> Dict[str, Any]:
    omissoes = []
    parcelamento: List[str] = []
    pgfn: List[str] = []
    for p in pendencias:
        if p.tipo == 'PARCELAMENTO':
            parcelamento.extend([str(x) for x in (p.meses_json or [])])
        elif p.tipo == 'PGFN':
            pgfn.extend([str(x) for x in (p.meses_json or [])])
        else:
            omissoes.append({'tipo': p.tipo, 'ano': p.ano, 'meses': list(p.meses_json or [])})
    return {
        'omissoes': omissoes,
        'parcelamento': ' · '.join(parcelamento) or None,
        'pgfn': ' · '.join(pgfn) or None,
    }


def ultimo_relatorio(company_id: int) -> Optional[RelatorioSitFiscal]:
    return (RelatorioSitFiscal.query.filter_by(company_id=company_id)
            .order_by(RelatorioSitFiscal.id.desc()).first())


def pendencias_da_empresa(company: Company, desde: Optional[datetime] = None,
                          hoje: Optional[date] = None) -> Dict[str, Any]:
    """Retrato completo de uma empresa a partir do ÚLTIMO relatório dela."""
    hoje = hoje or hoje_brasil()
    relatorio = ultimo_relatorio(company.id)

    base: Dict[str, Any] = {
        'company_id': company.id,
        'cnpj': company.cnpj,
        'razao_social': company.razao_social,
        'ativo': bool(company.ativo),
        'relatorio': None,
        'relatorio_recente': False,
        'debitos': [],
        'omissoes': [],
        'parcelamento': None,
        'pgfn': None,
        'resumo': {'qtd_atraso': 0, 'total_atraso': 0.0, 'qtd_a_vencer': 0,
                   'total_a_vencer': 0.0, 'qtd_invalidos': 0, 'qtd_omissoes': 0},
    }
    if not relatorio:
        return base

    debitos = [_serializar_debito(d, hoje) for d in relatorio.debitos]
    extras = _serializar_omissoes(list(relatorio.pendencias))

    atraso = [d for d in debitos if d['em_atraso']]
    a_vencer = [d for d in debitos if d['valido'] and not d['em_atraso']]
    invalidos = [d for d in debitos if not d['valido']]

    base.update({
        'relatorio': {
            'id': relatorio.id,
            'data_hora': relatorio.data_hora.isoformat() if relatorio.data_hora else None,
            'situacao': relatorio.situacao,
            'pdf_local_path': relatorio.pdf_local_path,
        },
        'relatorio_recente': bool(desde and relatorio.data_hora and relatorio.data_hora >= desde),
        'debitos': debitos,
        'omissoes': extras['omissoes'],
        'parcelamento': extras['parcelamento'],
        'pgfn': extras['pgfn'],
        'resumo': {
            'qtd_atraso': len(atraso),
            'total_atraso': round(sum(d['saldo_devedor_total'] for d in atraso), 2),
            'qtd_a_vencer': len(a_vencer),
            'total_a_vencer': round(sum(d['saldo_devedor_total'] for d in a_vencer), 2),
            'qtd_invalidos': len(invalidos),
            'qtd_omissoes': len(extras['omissoes']),
        },
    })
    return base


def pendencias_da_carteira(desde: Optional[datetime] = None,
                           apenas_ativas: bool = True) -> List[Dict[str, Any]]:
    consulta = Company.query
    if apenas_ativas:
        consulta = consulta.filter_by(ativo=True)
    hoje = hoje_brasil()
    return [pendencias_da_empresa(c, desde=desde, hoje=hoje)
            for c in consulta.order_by(Company.id.asc()).all()]


def empresa_por_cnpj(cnpj: str) -> Optional[Company]:
    digitos = ''.join(ch for ch in str(cnpj or '') if ch.isdigit())
    if len(digitos) != 14:
        return None
    return Company.query.filter_by(cnpj=digitos).first()


def total_relatorios_desde(desde: datetime) -> int:
    return (db.session.query(func.count(RelatorioSitFiscal.id))
            .filter(RelatorioSitFiscal.data_hora >= desde).scalar() or 0)
