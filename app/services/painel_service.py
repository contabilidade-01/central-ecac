"""Painel do novo frontend: fila do dia, ficha da empresa, saúde fiscal e busca.

DESVIO INTENCIONAL (28o) — NÃO existe no exe. Fase 4 do plano (09/11/2026). Só lê o que
já está no banco: nenhuma chamada à SERPRO nem ao portal do FGTS sai daqui.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from app.extensions import db
from app.models import CaixaPostalMensagem, Company
from app.services import pendencias_service as pend

DIAS_RELATORIO_VELHO = 40
DIAS_VENCE_LOGO = 5

# Pesos da saúde fiscal. Visíveis na tela: o escritório precisa entender a nota.
PESOS = {'atraso_por_tributo': 12, 'atraso_maximo': 48, 'omissao': 8, 'omissao_maximo': 24,
         'pgfn': 15, 'sem_procuracao': 10, 'mensagem_nao_lida': 3, 'mensagem_maximo': 9,
         'relatorio_velho': 10, 'fgts_vencido': 15}

ROTULO_TIPO = {'SN': 'DAS Simples', 'MEI': 'DAS MEI', 'INSS': 'INSS/DCTFWeb', 'IRPJ': 'IRPJ',
               'CSLL': 'CSLL', 'PIS': 'PIS', 'COFINS': 'COFINS', 'IRRF': 'IRRF'}


def _data(iso: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(iso)[:10]) if iso else None
    except ValueError:
        return None


def _procuracao(company: Company) -> Dict[str, Any]:
    from app.services.procuracao_service import ProcuracaoService
    try:
        return ProcuracaoService.situacao(company)
    except Exception:
        return {'situacao': 'desconhecida'}


def _nao_lidas(company_ids: Iterable[int]) -> Dict[int, int]:
    ids = list(company_ids)
    if not ids:
        return {}
    from sqlalchemy import func
    linhas = (db.session.query(CaixaPostalMensagem.company_id, func.count(CaixaPostalMensagem.id))
              .filter(CaixaPostalMensagem.company_id.in_(ids),
                      CaixaPostalMensagem.visualizada_usuario.is_(False))
              .group_by(CaixaPostalMensagem.company_id).all())
    return {cid: n for cid, n in linhas}


def _fgts(company_id: int, cnpj: str) -> Dict[str, Any]:
    """Situação do FGTS a partir da última consulta guardada (sem ir ao portal)."""
    try:
        from app.services import fgts_pedidos_service as servico
        from app.services.fgts import empresas as fgts_empresas
        sit = fgts_empresas.situacao(company_id)
        consulta = servico.ultima_consulta_hoje(cnpj)
        comps = servico.resultado(consulta).get('competencias', []) if consulta else []
        return {'aplicavel': sit['aplicavel'], 'motivo': sit['motivo'],
                'consultado_em': consulta.concluido_em.isoformat() if consulta and consulta.concluido_em else None,
                'vencidas': sum(1 for c in comps if c.get('situacao') == 'vencida'),
                'total_aberto': round(sum(c.get('total') or 0 for c in comps), 2)}
    except Exception:
        return {'aplicavel': False, 'motivo': 'indisponível', 'consultado_em': None, 'vencidas': 0, 'total_aberto': 0}


def saude(dados: Dict[str, Any], procuracao: Dict[str, Any], nao_lidas: int, fgts: Dict[str, Any],
          hoje: date) -> Dict[str, Any]:
    """Nota de 0 a 100 com a lista do que tirou pontos. Função pura."""
    itens: List[Dict[str, Any]] = []

    def tirar(motivo: str, pontos: int):
        if pontos > 0:
            itens.append({'motivo': motivo, 'pontos': -pontos})

    tipos_atraso = sorted({d['tipo'] for d in dados.get('debitos', []) if d.get('em_atraso')})
    tirar(f"Tributo vencido: {', '.join(ROTULO_TIPO.get(t, t) for t in tipos_atraso)}",
          min(PESOS['atraso_maximo'], PESOS['atraso_por_tributo'] * len(tipos_atraso)))
    omissoes = len(dados.get('omissoes') or [])
    tirar(f'{omissoes} declaração(ões) omissa(s)', min(PESOS['omissao_maximo'], PESOS['omissao'] * omissoes))
    if dados.get('pgfn'):
        tirar('Débito em dívida ativa (PGFN)', PESOS['pgfn'])
    if procuracao.get('situacao') in ('sem_procuracao', 'erro'):
        tirar('Procuração da SERPRO com problema', PESOS['sem_procuracao'])
    tirar(f'{nao_lidas} mensagem(ns) não lida(s) na Caixa Postal',
          min(PESOS['mensagem_maximo'], PESOS['mensagem_nao_lida'] * nao_lidas))
    relatorio = (dados.get('relatorio') or {}).get('data_hora')
    if not relatorio or (hoje - _data(relatorio)).days > DIAS_RELATORIO_VELHO:
        tirar('Situação fiscal sem consulta recente', PESOS['relatorio_velho'])
    if fgts.get('aplicavel') and fgts.get('vencidas'):
        tirar(f"FGTS vencido em {fgts['vencidas']} competência(s)", PESOS['fgts_vencido'])
    nota = max(0, 100 + sum(i['pontos'] for i in itens))
    faixa = 'boa' if nota >= 80 else 'atencao' if nota >= 50 else 'critica'
    return {'nota': nota, 'faixa': faixa, 'itens': itens}


def _empresas_visiveis(usuario: Dict[str, Any]) -> List[Company]:
    from app.services import permissoes
    consulta = Company.query.filter_by(ativo=True)
    permitidas = permissoes.empresas_do_usuario(usuario)
    if permitidas != permissoes.TODAS:
        consulta = consulta.filter(Company.id.in_([int(i) for i in permitidas or []]))
    return consulta.order_by(Company.razao_social).all()


def _agrupar_atraso(debitos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Uma linha por tributo e competência (INSS soma os códigos de receita)."""
    grupos: Dict[tuple, Dict[str, Any]] = {}
    for d in debitos:
        if not d.get('valido'):
            continue
        chave = (d['tipo'], d.get('periodo_aaaamm') or d.get('periodo_apuracao'))
        g = grupos.setdefault(chave, {'tipo': d['tipo'], 'rotulo': ROTULO_TIPO.get(d['tipo'], d['tipo']),
                                      'competencia': d.get('periodo_apuracao'), 'vencimento': d.get('data_vencimento'),
                                      'valor': 0.0, 'em_atraso': d.get('em_atraso'), 'guia': d.get('guia')})
        g['valor'] = round(g['valor'] + (d.get('saldo_devedor_total') or 0), 2)
    return list(grupos.values())


def mesa(usuario: Dict[str, Any], hoje: Optional[date] = None) -> Dict[str, Any]:
    hoje = hoje or pend.hoje_brasil()
    empresas = _empresas_visiveis(usuario)
    nao_lidas = _nao_lidas(c.id for c in empresas)
    fila: List[Dict[str, Any]] = []
    kpi = {'vence_logo': 0, 'em_atraso_empresas': 0, 'em_atraso_total': 0.0, 'mensagens_nao_lidas': 0,
           'sem_dado_recente': 0, 'empresas': len(empresas)}
    for c in empresas:
        dados = pend.pendencias_da_empresa(c, hoje=hoje)
        empresa = {'company_id': c.id, 'razao_social': c.razao_social, 'cnpj': c.cnpj}
        linhas = _agrupar_atraso(dados['debitos'])
        if any(l['em_atraso'] for l in linhas):
            kpi['em_atraso_empresas'] += 1
        for l in linhas:
            venc = _data(l['vencimento'])
            if l['em_atraso']:
                kpi['em_atraso_total'] = round(kpi['em_atraso_total'] + l['valor'], 2)
                fila.append({**empresa, 'tipo': 'vencido', 'prioridade': 1,
                             'titulo': f"{l['rotulo']} vencido", 'detalhe': f"Competência {l['competencia']}",
                             'valor': l['valor'], 'data': l['vencimento'],
                             'acao': 'gerar_guia' if l.get('guia') else 'ver'})
            elif venc and 0 <= (venc - hoje).days <= DIAS_VENCE_LOGO:
                kpi['vence_logo'] += 1
                fila.append({**empresa, 'tipo': 'vence_logo', 'prioridade': 2 if venc == hoje else 3,
                             'titulo': f"{l['rotulo']} vence {'hoje' if venc == hoje else venc.strftime('%d/%m')}",
                             'detalhe': f"Competência {l['competencia']}", 'valor': l['valor'], 'data': l['vencimento'],
                             'acao': 'gerar_guia' if l.get('guia') else 'ver'})
        n = nao_lidas.get(c.id, 0)
        if n:
            kpi['mensagens_nao_lidas'] += n
            fila.append({**empresa, 'tipo': 'mensagem', 'prioridade': 4, 'titulo': 'Caixa Postal',
                         'detalhe': f'{n} mensagem(ns) não lida(s)', 'valor': None, 'data': None, 'acao': 'caixa_postal'})
        proc = _procuracao(c)
        if proc.get('situacao') in ('sem_procuracao', 'erro'):
            fila.append({**empresa, 'tipo': 'procuracao', 'prioridade': 5, 'titulo': 'Procuração com problema',
                         'detalhe': 'Chamadas pagas travadas para esta empresa', 'valor': None, 'data': None,
                         'acao': 'procuracoes'})
        relatorio = _data((dados.get('relatorio') or {}).get('data_hora'))
        if not relatorio or (hoje - relatorio).days > DIAS_RELATORIO_VELHO:
            kpi['sem_dado_recente'] += 1
            fila.append({**empresa, 'tipo': 'sem_dado', 'prioridade': 6, 'titulo': 'Sem consulta recente',
                         'detalhe': ('Última situação fiscal em ' + relatorio.strftime('%d/%m/%Y')) if relatorio
                         else 'Nunca consultada', 'valor': None, 'data': None, 'acao': 'ver'})
    fila.sort(key=lambda i: (i['prioridade'], i['data'] or '9999', i['razao_social']))
    return {'gerado_em': datetime.now().isoformat(timespec='seconds'), 'hoje': hoje.isoformat(),
            'kpi': kpi, 'fila': fila[:500], 'total_fila': len(fila)}


def _linha_do_tempo(company: Company, limite: int = 15) -> List[Dict[str, Any]]:
    from app.integracao_models import DasEmissao
    from app.models import RelatorioSitFiscal
    eventos = []
    for r in RelatorioSitFiscal.query.filter_by(company_id=company.id).order_by(RelatorioSitFiscal.id.desc()).limit(5):
        if r.data_hora:
            eventos.append({'em': r.data_hora.isoformat(), 'tipo': 'relatorio',
                            'texto': f"Situação fiscal consultada: {r.situacao or 'sem situação'}"})
    for e in DasEmissao.query.filter_by(cnpj=company.cnpj).order_by(DasEmissao.id.desc()).limit(8):
        eventos.append({'em': e.emitido_em.isoformat(), 'tipo': 'guia',
                        'texto': f"Guia {e.tipo} {e.periodo_apuracao} {'reaproveitada' if e.reuso_de_id else 'emitida'}"
                                 f" por {e.origem}" + (f" ({e.solicitado_por})" if e.solicitado_por else '')})
    for m in (CaixaPostalMensagem.query.filter_by(company_id=company.id)
              .order_by(CaixaPostalMensagem.data_envio.desc()).limit(5)):
        if m.data_envio:
            eventos.append({'em': m.data_envio.isoformat(), 'tipo': 'mensagem',
                            'texto': f"Caixa Postal: {(m.assunto or 'mensagem')[:120]}"})
    eventos.sort(key=lambda e: e['em'], reverse=True)
    return eventos[:limite]


def ficha(company: Company, hoje: Optional[date] = None) -> Dict[str, Any]:
    hoje = hoje or pend.hoje_brasil()
    dados = pend.pendencias_da_empresa(company, hoje=hoje)
    proc = _procuracao(company)
    nao_lidas = _nao_lidas([company.id]).get(company.id, 0)
    fgts = _fgts(company.id, company.cnpj)
    from app.integracao_models import DasEmissao
    from app.services import das_emissao_service
    guias = [das_emissao_service.serializar(e) for e in
             DasEmissao.query.filter_by(cnpj=company.cnpj).order_by(DasEmissao.id.desc()).limit(10)]
    return {'empresa': {'company_id': company.id, 'razao_social': company.razao_social, 'cnpj': company.cnpj,
                        'ativo': bool(company.ativo)},
            'pendencias': _agrupar_atraso(dados['debitos']), 'omissoes': dados['omissoes'],
            'parcelamento': dados['parcelamento'], 'pgfn': dados['pgfn'], 'relatorio': dados['relatorio'],
            'resumo': dados['resumo'], 'procuracao': {'situacao': proc.get('situacao', 'desconhecida')},
            'caixa_postal': {'nao_lidas': nao_lidas}, 'fgts': fgts, 'guias': guias,
            'saude': saude(dados, proc, nao_lidas, fgts, hoje), 'linha_do_tempo': _linha_do_tempo(company)}


def buscar(usuario: Dict[str, Any], termo: str, limite: int = 12) -> List[Dict[str, Any]]:
    termo = (termo or '').strip().lower()
    digitos = ''.join(ch for ch in termo if ch.isdigit())
    if len(termo) < 2:
        return []
    saida = []
    for c in _empresas_visiveis(usuario):
        if termo in c.razao_social.lower() or (len(digitos) >= 3 and digitos in c.cnpj):
            saida.append({'company_id': c.id, 'razao_social': c.razao_social, 'cnpj': c.cnpj})
            if len(saida) >= limite:
                break
    return saida
