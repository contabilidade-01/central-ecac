"""Página de guias SEM SENHA — `/g/<token>` e `/api/acesso/<token>/*` (desvio 21).

⚠️ DESVIO INTENCIONAL — NÃO existe no exe. Pedido do Jean (23/10/2026): o AtendeChat
manda o link na conversa e o cliente gera a guia recalculada aqui, sem o portal do
cliente no caminho. É a primeira página pública (sem login) deste sistema; por isso:

* o token é validado em TODA rota (`acesso_links_service.validar`); token errado conta
  como tentativa falha do IP, com o mesmo bloqueio do login;
* a página nunca recebe CNPJ, id de empresa nem id de emissão "de fora": tudo sai do
  token. A guia só é emitida para débito que consta no ÚLTIMO relatório daquela empresa;
* custo: `emitir` com todas as travas (procuração, teto mensal, reuso do PDF) + limite
  por token por competência por dia. As emissões daqui (`origem='link'`) não contam no
  limite diário do portal — o que o cliente gera aqui nunca bloqueia o portal;
* o que a página NÃO faz: parcelamento (consulta paga), PGFN, omissões — só avisa
  "fale com o escritório".
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from flask import Blueprint, Response, jsonify, render_template, request

from app import security
from app.extensions import db
from app.integracao_models import AcessoLink, DasEmissao
from app.models import Company
from app.services import acesso_links_service as links
from app.services import das_emissao_service as emissao
from app.services import pendencias_service as pend
from app.services.calendario_util import eh_dia_util

acesso_bp = Blueprint('acesso', __name__)

PREFIXOS_LIVRES = ('/g/', '/api/acesso/')
ROTULOS = {'SN': 'DAS — Simples Nacional', 'MEI': 'DAS — MEI', 'INSS': 'DARF — INSS (DCTFWeb)'}
JANELA_DIAS = 30


def caminho_publico(caminho: str) -> bool:
    return any(caminho.startswith(p) for p in PREFIXOS_LIVRES)


def _erro(mensagem: str, codigo: int = 400):
    return jsonify({'success': False, 'message': mensagem}), codigo


def _ip() -> str:
    return request.remote_addr or 'desconhecido'


def _link_ou_erro(token: str):
    """(link, company) ou (None, resposta de erro). Token inválido conta como falha do IP."""
    bloqueado = security.segundos_bloqueado()
    if bloqueado:
        return None, _erro(f'Muitas tentativas. Tente de novo em {bloqueado} s.', 429)
    link = links.validar(token)
    if not link:
        security.registrar_tentativa_falha()
        return None, _erro('Este link expirou ou não é válido. Peça um novo ao escritório.', 404)
    company = db.session.get(Company, link.company_id)
    if not company:
        return None, _erro('Este link expirou ou não é válido. Peça um novo ao escritório.', 404)
    return (link, company), None


def _fmt_cnpj(cnpj: str) -> str:
    d = ''.join(ch for ch in (cnpj or '') if ch.isdigit())
    if len(d) != 14:
        return cnpj or ''
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def _fmt_data(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso[:19]).strftime('%d/%m/%Y')
    except ValueError:
        return iso


def _competencia(debito: Dict[str, Any]) -> str:
    pa = debito.get('periodo_aaaamm') or ''
    if len(pa) == 6:
        return f'{pa[4:]}/{pa[:4]}'
    return debito.get('periodo_apuracao') or ''


def _item(debito: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'id': debito['id'],
        'tipo': debito['tipo'],
        'rotulo': ROTULOS.get(debito['tipo'], debito.get('receita') or debito['tipo']),
        'competencia': _competencia(debito),
        'vencimento': _fmt_data(debito.get('data_vencimento')),
        'valor_original': debito.get('valor_original'),
        'saldo_devedor_total': debito.get('saldo_devedor_total'),
        'em_atraso': bool(debito.get('em_atraso')),
        'pode_gerar': bool(debito.get('recalculo_disponivel')) and bool(debito.get('periodo_aaaamm')),
    }


def escritorio() -> Dict[str, Optional[str]]:
    return {
        'nome': os.getenv('ESCRITORIO_NOME') or 'Nescon Contabilidade',
        'whatsapp': os.getenv('ESCRITORIO_WHATSAPP') or None,
    }


def montar_pagina(link: AcessoLink, company: Company) -> Dict[str, Any]:
    hoje = pend.hoje_brasil()
    dados = pend.pendencias_da_empresa(company, hoje=hoje)
    validos = [d for d in dados['debitos'] if d.get('valido')]
    em_atraso = [_item(d) for d in validos if d.get('em_atraso')]
    a_vencer = [_item(d) for d in validos if not d.get('em_atraso')]
    return {
        'empresa': {'razao_social': company.razao_social, 'cnpj': _fmt_cnpj(company.cnpj)},
        'relatorio': {'data': _fmt_data(dados['relatorio']['data_hora'])} if dados['relatorio'] else None,
        'em_atraso': em_atraso,
        'a_vencer': a_vencer,
        'total_em_atraso': round(sum(float(i['saldo_devedor_total'] or 0) for i in em_atraso), 2),
        'parcelamento': bool(dados.get('parcelamento')),
        'pgfn': bool(dados.get('pgfn')),
        'omissoes': len(dados.get('omissoes') or []),
        'hoje': hoje.isoformat(),
        'data_maxima': (hoje + timedelta(days=JANELA_DIAS)).isoformat(),
        'max_geracoes_dia': links.max_geracoes_dia(),
        'link': {'expira_em': link.expira_em.isoformat() if link.expira_em else None},
        'escritorio': escritorio(),
    }


# ----------------------------------------------------------------------- página
@acesso_bp.get('/g/<token>')
def pagina(token: str):
    resultado, erro = _link_ou_erro(token)
    if erro:
        corpo, codigo = erro
        return render_template('acesso/guias.html', token='', invalido=True,
                               mensagem=corpo.get_json()['message'], escritorio=escritorio()), codigo
    link, _company = resultado
    links.registrar_acesso(link, _ip())
    return render_template('acesso/guias.html', token=link.token, invalido=False, mensagem='',
                           escritorio=escritorio())


# -------------------------------------------------------------------------- API
@acesso_bp.get('/api/acesso/<token>')
def dados(token: str):
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, company = resultado
    return jsonify({'success': True, **montar_pagina(link, company)})


@acesso_bp.post('/api/acesso/<token>/guias')
def gerar(token: str):
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, company = resultado
    corpo = request.get_json(silent=True) or {}

    try:
        debito_id = int(corpo.get('debito_id'))
    except (TypeError, ValueError):
        return _erro('Informe o débito.')
    dados_empresa = pend.pendencias_da_empresa(company)
    debito = next((d for d in dados_empresa['debitos'] if d['id'] == debito_id), None)
    if not debito or not debito.get('valido') or not debito.get('recalculo_disponivel'):
        return _erro('Este débito não consta no relatório atual da empresa.', 404)
    pa = debito.get('periodo_aaaamm')
    tipo_guia = pend.GUIA_POR_TIPO.get(debito['tipo'])
    if not pa or not tipo_guia:
        return _erro('Não é possível gerar guia para este débito por aqui. Fale com o escritório.', 409)

    hoje = pend.hoje_brasil()
    data_pagamento = None
    if debito.get('em_atraso'):
        bruto = ''.join(ch for ch in str(corpo.get('data_pagamento') or '') if ch.isdigit())
        if len(bruto) != 8:
            return _erro('Escolha a data em que vai pagar.')
        try:
            dia = date(int(bruto[:4]), int(bruto[4:6]), int(bruto[6:]))
        except ValueError:
            return _erro('Data inválida.')
        if dia < hoje:
            return _erro('A data de pagamento não pode ser anterior a hoje.')
        if dia > hoje + timedelta(days=JANELA_DIAS):
            return _erro(f'Escolha uma data em até {JANELA_DIAS} dias.')
        if not eh_dia_util(dia):
            return _erro('Escolha um dia útil (a guia só pode ser paga em dia de expediente bancário).')
        data_pagamento = dia.strftime('%Y%m%d')

    # Limite por token só vale para emissão PAGA: guia já guardada para a mesma data
    # continua saindo do disco, sem custo.
    categoria = None
    if tipo_guia == 'DCTFWEB':
        categoria = 'GERAL_13o_SALARIO' if pa[4:] == '13' else 'GERAL_MENSAL'
    guardada = emissao.guia_valida_guardada(company.cnpj, tipo_guia, pa, categoria, data_pagamento)
    if not guardada and links.geracoes_hoje(link, tipo_guia, pa) >= links.max_geracoes_dia():
        return _erro(f'Limite de {links.max_geracoes_dia()} guias por dia para esta competência. '
                     'A última guia gerada continua disponível abaixo.', 429)

    try:
        registro, reuso = emissao.emitir(
            cnpj=company.cnpj, tipo=tipo_guia, periodo_apuracao=pa,
            data_consolidacao=data_pagamento, origem=links.ORIGEM_EMISSAO,
            solicitado_por=links.solicitante(link.token))
    except emissao.EmissaoBloqueada as exc:
        return _erro(str(exc), exc.status)
    except Exception as exc:  # erro da SERPRO/rede — mensagem legível
        from app.services.serpro_das_service import SerproApiError
        if isinstance(exc, SerproApiError):
            return _erro(str(exc), 400 if 400 <= exc.status_code < 500 else 502)
        return _erro(f'Não foi possível gerar a guia agora: {exc}', 502)

    if not reuso:
        links.registrar_guia(link)
    return jsonify({
        'success': True,
        'reuso': reuso,
        'guia': {
            'id': registro.id,
            'valor_total': registro.valor_total,
            'vencimento': _fmt_data(datetime.strptime(registro.vencimento, '%Y%m%d').isoformat())
            if registro.vencimento and len(registro.vencimento) == 8 else None,
            'pdf_url': f'/api/acesso/{link.token}/guias/{registro.id}/pdf',
        },
    })


@acesso_bp.get('/api/acesso/<token>/guias/<int:emissao_id>/pdf')
def pdf(token: str, emissao_id: int):
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, _company = resultado
    registro = db.session.get(DasEmissao, emissao_id)
    # Recorte pelo CNPJ do token: id de emissão de outra empresa não sai daqui.
    if not registro or registro.cnpj != link.cnpj:
        return _erro('Guia não encontrada.', 404)
    conteudo = emissao.ler_pdf(registro)
    if not conteudo:
        return _erro('Esta guia não está mais disponível. Gere novamente.', 410)
    nome = f'{registro.tipo}_{registro.periodo_apuracao}.pdf'
    return Response(conteudo, mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{nome}"',
                             'Cache-Control': 'private, no-store'})
