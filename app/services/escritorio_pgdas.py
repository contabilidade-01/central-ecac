"""Histórico PGDAS-D (RBT12 / receitas anteriores / folha CPP).

Espelha o contrato do Integra Contador (`impext/importar_lancamentos.php`):
o browser lê o PDF e envia JSON; aqui só gravamos e consultamos.

Sem esse histórico, Calcular DAS / entrega da declaração não tem RBT12 — a SERPRO
e a apuração local falham ou ficam inconsistentes.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.escritorio_models import EscritorioPgdasHistorico, EscritorioPgdasMeta
from app.extensions import db
from app.models import Company


def only_digits(value: Any) -> str:
    return re.sub(r'\D', '', str(value or ''))


def moeda_para_numero(valor: Any) -> float:
    """Aceita float, int, '1.234,56', '1234.56' ou vazio."""
    if valor is None or valor == '' or valor == 'Não encontrado':
        return 0.0
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)
    texto = str(valor).strip()
    if not texto:
        return 0.0
    # pt-BR: remove milhar e troca vírgula
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    texto = re.sub(r'[^\d.\-]', '', texto)
    try:
        return round(float(texto or 0), 2)
    except ValueError:
        return 0.0


def normalizar_competencia(valor: Any) -> str:
    """Converte MM/AAAA, AAAAMM, AAAA-MM → AAAA-MM."""
    if valor is None:
        return ''
    s = str(valor).strip()
    dig = only_digits(s)
    if len(dig) == 6:
        # AAAAMM ou MMAAAA? Integra usa converterMesAno de MM/AAAA → AAAA-MM
        if '/' in s:
            partes = s.split('/')
            if len(partes) == 2 and len(partes[0]) <= 2:
                return f'{partes[1]}-{int(partes[0]):02d}'
        # se veio 202608
        if dig[:2] in ('19', '20'):
            return f'{dig[:4]}-{dig[4:6]}'
        # MMAAAA
        return f'{dig[2:6]}-{dig[:2]}'
    if len(s) == 7 and s[4] == '-':
        return s
    if '/' in s:
        p = s.split('/')
        if len(p) == 2:
            mes, ano = p[0].zfill(2), p[1]
            if len(ano) == 4:
                return f'{ano}-{mes}'
    return ''


def somar_mes(competencia: str, delta: int) -> str:
    ano, mes = map(int, competencia.split('-'))
    idx = ano * 12 + (mes - 1) + delta
    return f'{idx // 12}-{idx % 12 + 1:02d}'


def meses_rbt12(pa: str) -> List[str]:
    """12 competências imediatamente anteriores ao PA (não inclui o PA)."""
    return [somar_mes(pa, -i) for i in range(12, 0, -1)]


def _company_por_cnpj(cnpj: str) -> Optional[Company]:
    return Company.query.filter_by(cnpj=cnpj).first()


def rbt12_para(cnpj: str, pa: str) -> Tuple[float, str]:
    """Retorna (valor, origem). Prefere meta do PA; senão soma dos 12 meses."""
    cnpj = only_digits(cnpj)
    pa = normalizar_competencia(pa)
    if not cnpj or not pa:
        return 0.0, 'vazio'
    meta = EscritorioPgdasMeta.query.filter_by(cnpj=cnpj, pa=pa).first()
    if meta and float(meta.rbt12 or 0) > 0:
        return round(float(meta.rbt12), 2), 'meta'
    meses = meses_rbt12(pa)
    rows = (
        EscritorioPgdasHistorico.query
        .filter(
            EscritorioPgdasHistorico.cnpj == cnpj,
            EscritorioPgdasHistorico.competencia.in_(meses),
        )
        .all()
    )
    if not rows:
        return 0.0, 'ausente'
    total = round(sum(float(r.receita_bruta or 0) for r in rows), 2)
    return total, 'soma_historico'


def rbt12_em_lote(cnpjs: List[str], pa: str) -> Dict[str, float]:
    saida = {}
    for cnpj in cnpjs:
        dig = only_digits(cnpj)
        if not dig:
            continue
        valor, _ = rbt12_para(dig, pa)
        saida[dig] = valor
    return saida


def tem_historico_suficiente(cnpj: str, pa: str) -> Dict[str, Any]:
    """Indica se dá para apurar RBT12 antes de transmitir."""
    cnpj = only_digits(cnpj)
    pa = normalizar_competencia(pa)
    valor, origem = rbt12_para(cnpj, pa)
    meses = meses_rbt12(pa) if pa else []
    qtd = 0
    if meses:
        qtd = (
            EscritorioPgdasHistorico.query
            .filter(
                EscritorioPgdasHistorico.cnpj == cnpj,
                EscritorioPgdasHistorico.competencia.in_(meses),
            )
            .count()
        )
    ok = valor > 0 and (origem == 'meta' or qtd >= 1)
    return {
        'ok': ok,
        'rbt12': valor,
        'origem': origem,
        'meses_historico': qtd,
        'meses_esperados': 12,
        'aviso': (
            None if ok else
            'Importe o Extrato/Declaração PGDAS-D (Upload RBT12) antes de calcular/enviar. '
            'Sem receitas dos 12 meses anteriores o RBT12 fica zerado e a declaração falha.'
        ),
    }


def importar_payload(dados: Dict[str, Any], usuario: str = '') -> Dict[str, Any]:
    """Grava o JSON no mesmo formato que o Integra envia a importar_lancamentos.php.

    Campos esperados:
      cnpj, nome (opcional), pa (MM/AAAA ou AAAA-MM),
      rbt12, folha12, rpa, valorDas (opcionais),
      tabela: [{ mes, receita, folha }, ...]  — até 13 linhas
    """
    cnpj = only_digits(dados.get('cnpj'))
    if len(cnpj) != 14:
        raise ValueError('CNPJ inválido ou ausente no PDF/importação.')

    pa = normalizar_competencia(dados.get('pa'))
    if not pa:
        raise ValueError('Período de apuração (PA) não encontrado.')

    company = _company_por_cnpj(cnpj)
    if not company:
        raise ValueError(
            f'Empresa {cnpj} não cadastrada. Cadastre o CNPJ no sistema antes de importar.'
        )

    tabela = dados.get('tabela') or []
    if not isinstance(tabela, list):
        raise ValueError('Campo tabela inválido.')

    linhas_gravadas = 0
    for item in tabela:
        if not isinstance(item, dict):
            continue
        mes = normalizar_competencia(item.get('mes'))
        if not mes:
            continue
        receita = moeda_para_numero(item.get('receita') or item.get('valor'))
        folha = moeda_para_numero(item.get('folha') or item.get('folha_cpp'))
        row = (
            EscritorioPgdasHistorico.query
            .filter_by(cnpj=cnpj, competencia=mes)
            .first()
        )
        if not row:
            row = EscritorioPgdasHistorico(cnpj=cnpj, competencia=mes)
            db.session.add(row)
        row.company_id = company.id
        row.receita_bruta = receita
        row.folha_cpp = folha
        row.origem = 'import_pgdas'
        row.atualizado_por = (usuario or '')[:120]
        linhas_gravadas += 1

    rbt12 = moeda_para_numero(dados.get('rbt12'))
    folha12 = moeda_para_numero(dados.get('folha12'))
    rpa = moeda_para_numero(dados.get('rpa'))
    valor_das = moeda_para_numero(dados.get('valorDas') or dados.get('valor_das'))

    # Se não veio RBT12 oficial, recalcula pela soma dos 12 meses anteriores
    if rbt12 <= 0 and linhas_gravadas:
        rbt12, _ = rbt12_para(cnpj, pa)
        # ainda sem meta — soma direta
        if rbt12 <= 0:
            meses = meses_rbt12(pa)
            por_mes = {
                normalizar_competencia(i.get('mes')): moeda_para_numero(
                    i.get('receita') or i.get('valor'))
                for i in tabela if isinstance(i, dict)
            }
            rbt12 = round(sum(por_mes.get(m, 0) for m in meses), 2)

    meta = EscritorioPgdasMeta.query.filter_by(cnpj=cnpj, pa=pa).first()
    if not meta:
        meta = EscritorioPgdasMeta(cnpj=cnpj, pa=pa)
        db.session.add(meta)
    meta.company_id = company.id
    meta.rbt12 = rbt12
    meta.folha12 = folha12
    meta.rpa = rpa
    meta.valor_das = valor_das
    meta.nome_pdf = (dados.get('nome') or dados.get('nome_pdf') or '')[:255]
    meta.atualizado_por = (usuario or '')[:120]

    db.session.commit()

    conf = tem_historico_suficiente(cnpj, pa)
    return {
        'ok': True,
        'cnpj': cnpj,
        'company_id': company.id,
        'razao': company.razao_social,
        'pa': pa,
        'linhas': linhas_gravadas,
        'rbt12': rbt12,
        'folha12': folha12,
        'rpa': rpa,
        'valor_das': valor_das,
        'pronto_transmitir': conf['ok'],
        'aviso': conf.get('aviso'),
    }
