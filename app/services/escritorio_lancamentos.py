"""Lançamentos do Escritório — leitura e gravação da conferência mensal."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from app.escritorio_models import EscritorioLancamento
from app.extensions import db
from app.models import Company

PERFIL_NOME = {
    'comercio': 'Comércio',
    'servico': 'Serviço',
    'fator_r': 'Fator R',
    'outros': 'Anexo IV',
    'industria': 'Indústria',
    'comunicacao': 'Comunicação',
}

CAMPOS_VALOR = (
    'total_receita', 'devolucoes', 'outras_receitas',
    'rec_sem_st', 'rec_sem_st_isencao', 'rec_com_st', 'rec_monofasica',
    'rec_com_st_mono', 'saldo_sefaz',
)


def _num(valor) -> float:
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def total_comercio(valores: Dict[str, float]) -> float:
    """Mesma regra do Integra: soma das receitas de mercadoria (sem outras/devoluções)."""
    return round(
        valores.get('rec_sem_st', 0) + valores.get('rec_sem_st_isencao', 0)
        + valores.get('rec_com_st', 0) + valores.get('rec_monofasica', 0)
        + valores.get('rec_com_st_mono', 0),
        2,
    )


def diferenca_comercio(valores: Dict[str, float]) -> float:
    """Diferença = total comércio + outras + devoluções − saldo Sefaz."""
    return round(
        total_comercio(valores)
        + valores.get('outras_receitas', 0)
        + valores.get('devolucoes', 0)
        - valores.get('saldo_sefaz', 0),
        2,
    )


def _fmt_cnpj(cnpj: str) -> str:
    d = ''.join(c for c in (cnpj or '') if c.isdigit())
    if len(d) != 14:
        return cnpj or ''
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def _valores_de(lanc: EscritorioLancamento) -> Dict[str, float]:
    return {c: round(float(getattr(lanc, c) or 0), 2) for c in CAMPOS_VALOR}


def listar_competencia(competencia: str, empresa_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Empresas com lançamento na competência, agrupadas por CNPJ."""
    q = EscritorioLancamento.query.filter_by(competencia=competencia)
    if empresa_ids is not None:
        if not empresa_ids:
            return {'empresas': [], 'metricas': _metricas([])}
        q = q.filter(EscritorioLancamento.company_id.in_(empresa_ids))
    lancs = q.order_by(EscritorioLancamento.cnpj, EscritorioLancamento.perfil).all()

    por_cnpj: Dict[str, List[EscritorioLancamento]] = defaultdict(list)
    for lanc in lancs:
        por_cnpj[lanc.cnpj].append(lanc)

    cnpjs = list(por_cnpj.keys())
    empresas_db = {
        c.cnpj: c for c in Company.query.filter(Company.cnpj.in_(cnpjs)).all()
    } if cnpjs else {}

    empresas = []
    for cnpj, itens in por_cnpj.items():
        emp = empresas_db.get(cnpj)
        ok = all(bool(i.ok) for i in itens)
        # transmitido da empresa = máximo entre perfis (1 SERPRO, 2 manual)
        transmitido = max((int(i.transmitido or 0) for i in itens), default=0)
        perfis = []
        for lanc in itens:
            vals = _valores_de(lanc)
            if lanc.perfil == 'comercio':
                vals['total_receita'] = total_comercio(vals)
            perfis.append({
                'id': lanc.id,
                'perfil': lanc.perfil,
                'perfil_nome': PERFIL_NOME.get(lanc.perfil, lanc.perfil),
                'origem': lanc.origem,
                'ok': bool(lanc.ok),
                'transmitido': int(lanc.transmitido or 0),
                'valores': vals,
                'diferenca': diferenca_comercio(vals) if lanc.perfil == 'comercio' else 0.0,
            })
        empresas.append({
            'company_id': emp.id if emp else (itens[0].company_id or None),
            'cnpj': cnpj,
            'cnpj_fmt': _fmt_cnpj(cnpj),
            'razao': (emp.razao_social if emp else None) or f'CNPJ {_fmt_cnpj(cnpj)}',
            'ok': ok,
            'transmitido': transmitido,
            'perfis': perfis,
            'n_perfis': len(perfis),
        })

    empresas.sort(key=lambda e: (e['razao'] or '').upper())
    return {'empresas': empresas, 'metricas': _metricas(empresas)}


def _metricas(empresas: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(empresas)
    ok = sum(1 for e in empresas if e.get('ok'))
    enviadas = sum(1 for e in empresas if int(e.get('transmitido') or 0) > 0)
    # pendente = não OK e não transmitido (como no Integra: barras separadas)
    pendentes = sum(
        1 for e in empresas
        if not e.get('ok') and int(e.get('transmitido') or 0) == 0
    )

    def pct(parte: int) -> str:
        return (f'{(parte / total * 100):.2f}' if total else '0.00').replace('.', ',')

    return {
        'total': total,
        'ok': ok,
        'enviadas': enviadas,
        'pendentes': pendentes,
        'erros': 0,
        'p_ok': pct(ok),
        'p_env': pct(enviadas),
        'p_pend': pct(pendentes),
        'p_err': '0,00',
    }


def salvar_perfil(lanc_id: int, valores: Dict[str, Any], ok: bool, transmitido: int,
                  usuario: str = '') -> EscritorioLancamento:
    lanc = db.session.get(EscritorioLancamento, lanc_id)
    if not lanc:
        raise ValueError('Lançamento não encontrado.')
    for c in CAMPOS_VALOR:
        if c in valores:
            setattr(lanc, c, _num(valores.get(c)))
    if lanc.perfil == 'comercio':
        vals = _valores_de(lanc)
        lanc.total_receita = total_comercio(vals)
    lanc.ok = bool(ok)
    lanc.transmitido = max(0, min(2, int(transmitido or 0)))
    if usuario:
        lanc.atualizado_por = usuario[:120]
    db.session.commit()
    return lanc
