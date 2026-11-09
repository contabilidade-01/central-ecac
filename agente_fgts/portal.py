"""Leitura por JSON e emissão pela tela do FGTS Digital, sobre a aba já logada.

Leitura: o mesmo JSON que a tela usa (perfil, lista de competências, detalhe), mapeado na
fase 0 em 09/11/2026. O valor inicial de uma competência vencida pode vir SEM encargos;
por isso `corrigir` é chamado (com nova tentativa) antes de informar o total.

Emissão: pela tela (`fgts_local/fgts_portal.py`), porque o fluxo de emissão por JSON ainda
não foi provado no portal real.
"""
from __future__ import annotations

import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fgts_local'))
from fgts_portal import FalhaFgts, PortalFgts, pdf_e_da_empresa  # noqa: E402

from sessao import BASE  # noqa: E402

FETCH_JS = """async ({url}) => {
  const r = await fetch(url, {credentials: 'include', headers: {Accept: 'application/json, text/plain, */*'}});
  const t = await r.text(); let d = null; try { d = t ? JSON.parse(t) : null; } catch (_) {}
  return {status: r.status, dados: d};
}"""


class SemProcuracao(FalhaFgts):
    pass


def _buscar(pagina, caminho: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    url = BASE + caminho
    if params:
        url += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
    return pagina.evaluate(FETCH_JS, {'url': url})


def _data(texto: Any) -> Optional[date]:
    for formato in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(texto or '')[:10], formato).date()
        except ValueError:
            continue
    return None


def definir_perfil(pagina, cnpj: str) -> None:
    r = _buscar(pagina, '/proxy-api/v2/perfil', {'tipoPerfil': '2', 'niPerfil': cnpj})
    if r['status'] != 200 or not (r['dados'] or {}).get('valido'):
        raise SemProcuracao('O FGTS Digital não aceitou o perfil de procurador para esta empresa '
                            '(sem procuração ou procuração vencida).')


def _corrigir(pagina, valor_filtro: str, tentativas: int = 3) -> bool:
    partes = str(valor_filtro or '').split(',')
    if len(partes) != 5:
        return False
    for i in range(tentativas):
        r = _buscar(pagina, '/cobranca/api/guia-rapida/agrupamento/corrigir',
                    {'noloading': '', 'uuid': str(uuid.uuid4()), 'dtCompetencia': partes[0], 'tipoDebito': partes[1],
                     'hashDebitos': partes[2], 'dtPagamento': partes[3], 'temConsignado': partes[4]})
        d = r['dados'] if isinstance(r['dados'], dict) else {}
        if r['status'] == 200 and (d.get('recuperouEncargos') or (d.get('totalGuia') or 0) > 0):
            return True
        time.sleep(2 + i)
    return False


def _grupo(g: Dict[str, Any]) -> Dict[str, Any]:
    return {'tipo': g.get('tipo'), 'total': (g.get('totalizador') or {}).get('vlTotal') or g.get('totalGuia'),
            'pagamento_ate': g.get('pagamentoAte'), 'tem_guia': bool(g.get('guiaExistente')),
            'tem_consignado': bool(g.get('contemConsignado'))}


def consultar(pagina, cnpj: str, filtros: Dict[str, str], max_competencias: int = 0,
              progresso=lambda texto: None, hoje: Optional[date] = None) -> List[Dict[str, Any]]:
    hoje = hoje or date.today()
    definir_perfil(pagina, cnpj)
    indice = _buscar(pagina, '/cobranca/api/guia-rapida/')
    if indice['status'] != 200 or not isinstance(indice['dados'], list):
        raise FalhaFgts(f"O FGTS Digital não devolveu a lista de competências (HTTP {indice['status']}).")
    ids = [str(i['id']) for i in indice['dados'] if isinstance(i, dict) and i.get('id')]
    if max_competencias > 0:
        ids = ids[:max_competencias]
    saida = []
    for comp in ids:
        progresso(f'Consultando {comp[4:]}/{comp[:4]}')
        caminho = f'/cobranca/api/guia-rapida/v2/{comp}'
        r = _buscar(pagina, caminho, filtros)
        if r['status'] != 200 or not isinstance(r['dados'], dict):
            continue
        dados = r['dados']
        grupos = dados.get('agrupamentos') or []
        vencimento = next((d.get('vencimento') for g in grupos for d in (g.get('detalhamentos') or [])), None)
        venc = _data(vencimento)
        vencida = bool(venc and venc < hoje)
        if vencida and any(not g.get('recuperouEncargos') and not g.get('guiaExistente') for g in grupos):
            if any(_corrigir(pagina, g.get('valorFiltro')) for g in grupos if not g.get('guiaExistente')):
                r2 = _buscar(pagina, caminho, filtros)
                if r2['status'] == 200 and isinstance(r2['dados'], dict):
                    dados, grupos = r2['dados'], r2['dados'].get('agrupamentos') or []
        resumo = dados.get('resumo') or {}
        saida.append({'competencia': comp, 'total': resumo.get('vlTotal'),
                      'vencimento': venc.isoformat() if venc else None,
                      'situacao': 'vencida' if vencida else ('em_dia' if venc else 'sem_data'),
                      'tem_guia': any(g.get('guiaExistente') for g in grupos),
                      'tem_consignado': any(g.get('contemConsignado') for g in grupos),
                      'grupos': [_grupo(g) for g in grupos]})
        time.sleep(1)
    return saida


def emitir(pagina, cnpj: str, competencia: str, data_pagamento: Optional[str],
           receber=lambda nome, pdf: None) -> List[Dict[str, Any]]:
    """Emite pela tela. Hoje (ou sem data) = Guia Rápida; outra data = Parametrizada, que
    falha antes do clique final se a data não aparecer na etapa Emitir Guia."""
    definir_perfil(pagina, cnpj)
    portal = PortalFgts(pagina)
    portal._perfil_cnpj = cnpj
    pdfs: Dict[str, bytes] = {}
    portal._receber_pdf = lambda _cnpj, nome, conteudo: pdfs.__setitem__(nome, conteudo)
    try:
        # A tela confere "Empregador: <CNPJ>" antes de qualquer clique em "Emitir".
        guias = portal.emitir(cnpj, competencia, data_pagamento)
    except FalhaFgts as exc:
        if 'outra empresa' not in str(exc):
            raise
        # A tela ainda não refletiu o perfil definido por JSON: escolhe pela própria tela
        # ("Definir Perfil") e tenta uma vez. Nada foi emitido até aqui.
        portal._perfil_cnpj = None
        portal.definir_perfil(cnpj)
        guias = portal.emitir(cnpj, competencia, data_pagamento)
    for g in guias:
        g['pdf'] = pdfs.get(g.get('arquivo'))
        if g['pdf'] and not pdf_e_da_empresa(g['pdf'], cnpj):
            raise FalhaFgts('O PDF emitido não traz o CNPJ da empresa. A guia não foi enviada à Central.')
    return guias
