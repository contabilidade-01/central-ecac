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
from app.services import parcelas_service
from app.services import pendencias_service as pend
from app.services.parcelamentos_serpro_service import PARCELAMENTO_TYPES
from app.services.calendario_util import eh_dia_util

acesso_bp = Blueprint('acesso', __name__)

PREFIXOS_LIVRES = ('/g/', '/api/acesso/')
ROTULOS = {'SN': 'DAS — Simples Nacional', 'MEI': 'DAS — MEI', 'INSS': 'DARF — INSS (DCTFWeb)'}
JANELA_DIAS = 30
# "Outra competência": guia que ainda não está no relatório (a Receita só lista o que
# foi declarado até a data do relatório; a guia do mês que venceu ontem fica de fora).
# O cliente escolhe o tipo e o mês; a Receita calcula se existir declaração.
TIPOS_LIVRES = {'SN': 'DAS — Simples Nacional', 'MEI': 'DAS — MEI', 'DCTFWEB': 'DARF — INSS (DCTFWeb)'}
COMPETENCIA_MESES_ATRAS = 36


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


def _eh_mei(company_id: int) -> bool:
    from app.integracao_models import EmpresaMei
    marca = EmpresaMei.query.filter_by(company_id=company_id).first()
    return bool(marca and marca.eh_mei)


def tipos_livres(company: Company) -> list:
    """Tipos que o cliente pode pedir em "Outra competência": MEI → DAS MEI; demais → SN e INSS."""
    if _eh_mei(company.id):
        return [{'tipo': 'MEI', 'rotulo': TIPOS_LIVRES['MEI']}]
    return [{'tipo': 'SN', 'rotulo': TIPOS_LIVRES['SN']}, {'tipo': 'DCTFWEB', 'rotulo': TIPOS_LIVRES['DCTFWEB']}]


def competencias_permitidas(hoje: date) -> list:
    """AAAAMM do mês atual para trás (36 meses), para a lista da página."""
    ano, mes = hoje.year, hoje.month
    saida = []
    for _ in range(COMPETENCIA_MESES_ATRAS):
        saida.append(f'{ano:04d}{mes:02d}')
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    return saida


def _agrupar(debitos: list) -> list:
    """Um item por tipo + competência. O relatório traz um débito por código de receita
    (INSS: 1082, 1099, 1138…), mas a guia é uma só por competência — a DARF da DCTFWeb
    junta todos os códigos — e é assim que o cliente a vê. O `id` é o do primeiro
    débito do grupo (qualquer um gera a mesma guia); o vencimento é o mais antigo."""
    grupos: Dict[str, Dict[str, Any]] = {}
    vencimentos: Dict[str, str] = {}
    for d in debitos:
        item = _item(d)
        chave = f"{item['tipo']}|{item['competencia']}"
        venc = d.get('data_vencimento') or ''
        if chave not in grupos:
            grupos[chave] = item
            vencimentos[chave] = venc
            continue
        atual = grupos[chave]
        atual['saldo_devedor_total'] = round(float(atual['saldo_devedor_total'] or 0)
                                             + float(item['saldo_devedor_total'] or 0), 2)
        atual['valor_original'] = round(float(atual['valor_original'] or 0)
                                        + float(item['valor_original'] or 0), 2)
        if venc and (not vencimentos[chave] or venc < vencimentos[chave]):
            vencimentos[chave] = venc
            atual['vencimento'] = item['vencimento']
    return list(grupos.values())


def tipos_parcelamento(company: Company, texto_relatorio: Optional[str]) -> list:
    """Parcelamentos que a página oferece: os marcados no cadastro da empresa e os que o
    relatório da Receita cita (ex.: "SIMPLES NACIONAL - EM PARCELAMENTO"), para o cliente
    gerar a parcela pelo mesmo link."""
    mei = _eh_mei(company.id)
    tipos: list = []
    for tipo, cfg in PARCELAMENTO_TYPES.items():
        if getattr(company, cfg.get('flag') or '', False):
            tipos.append(tipo)
    texto = (texto_relatorio or '').upper()
    if texto:
        sufixo = 'MEI' if mei else 'SN'
        if 'PERT' in texto:
            tipos.append(f'PERT_{sufixo}')
        if 'RELP' in texto:
            tipos.append(f'RELP_{sufixo}')
        if 'PARCELAMENTO' in texto and not any(t.startswith(('PERT', 'RELP')) for t in tipos):
            tipos.append(f'PARC_{sufixo}')
    saida, vistos = [], set()
    for tipo in tipos:
        if tipo in PARCELAMENTO_TYPES and tipo not in vistos:
            vistos.add(tipo)
            saida.append(tipo)
    return saida


def _parcelamento_item(cnpj: str, tipo: str) -> Dict[str, Any]:
    consulta = parcelas_service.serializar(parcelas_service.ultima(cnpj, tipo))
    return {
        'tipo': tipo,
        'rotulo': parcelas_service.rotulo(tipo),
        'consultado': consulta['consultado'],
        'consultado_em': _fmt_data(consulta.get('consultado_em')) if consulta.get('consultado_em') else None,
        'hoje': bool(consulta.get('hoje')),
        'parcelas': [{'parcela': p['parcela'], 'competencia': f"{p['parcela'][4:]}/{p['parcela'][:4]}",
                      'valor': p.get('valor')} for p in consulta.get('parcelas', [])],
        'mensagem': consulta.get('mensagem'),
    }


def montar_pagina(link: AcessoLink, company: Company) -> Dict[str, Any]:
    hoje = pend.hoje_brasil()
    dados = pend.pendencias_da_empresa(company, hoje=hoje)
    validos = [d for d in dados['debitos'] if d.get('valido')]
    em_atraso = _agrupar([d for d in validos if d.get('em_atraso')])
    a_vencer = _agrupar([d for d in validos if not d.get('em_atraso')])
    return {
        'empresa': {'razao_social': company.razao_social, 'cnpj': _fmt_cnpj(company.cnpj)},
        'relatorio': {'data': _fmt_data(dados['relatorio']['data_hora'])} if dados['relatorio'] else None,
        'em_atraso': em_atraso,
        'a_vencer': a_vencer,
        'total_em_atraso': round(sum(float(i['saldo_devedor_total'] or 0) for i in em_atraso), 2),
        'parcelamento': bool(dados.get('parcelamento')),
        'parcelamento_texto': dados.get('parcelamento'),
        'parcelamentos': [_parcelamento_item(company.cnpj, t)
                          for t in tipos_parcelamento(company, dados.get('parcelamento'))],
        'pgfn': bool(dados.get('pgfn')),
        'pgfn_texto': dados.get('pgfn'),
        'omissoes': len(dados.get('omissoes') or []),
        'hoje': hoje.isoformat(),
        'data_maxima': (hoje + timedelta(days=JANELA_DIAS)).isoformat(),
        'max_geracoes_dia': links.max_geracoes_dia(),
        'tipos_livres': tipos_livres(company),
        'competencias': competencias_permitidas(hoje),
        'link': {'expira_em': link.expira_em.isoformat() if link.expira_em else None},
        'escritorio': escritorio(),
        'fgts': _fgts_resumo(link, company),
    }


# ------------------------------------------------------------------- FGTS (desvio 22)
# O robô do FGTS Digital (services/fgts_digital_robo.py) entra como procurador e faz o
# trabalho; a página só cria o pedido, acompanha as etapas e — se o gov.br pedir
# captcha — mostra o recorte do desafio e repassa os toques do cliente.
def _fgts_resumo(link: AcessoLink, company: Company) -> Dict[str, Any]:
    from app.services import fgts_pedidos_service as fgts
    if not fgts.habilitado():
        return {'habilitado': False}
    aberto = fgts.pedido_aberto(company.cnpj)
    consulta = fgts.ultima_consulta_hoje(company.cnpj)
    ultimo = fgts.ultimo_pedido_recente(company.cnpj)
    base = f'/api/acesso/{link.token}/fgts/pedidos'
    return {
        'habilitado': True,
        'pedido_aberto': aberto.id if aberto else None,
        'consulta': fgts.serializar(consulta) if consulta else None,
        # Último pedido (consulta ou emissão) das últimas 12 h: ao reabrir o link o
        # cliente vê o andamento e as guias que já saíram.
        'ultimo': fgts.serializar(ultimo, base_pdf=f'{base}/{ultimo.id}/pdf') if ultimo else None,
    }


def _fgts_pedido(link: AcessoLink, pedido_id: int):
    from app.integracao_models import FgtsPedido
    pedido = db.session.get(FgtsPedido, pedido_id)
    # Recorte pelo CNPJ do token: pedido de outra empresa não aparece aqui.
    if not pedido or pedido.cnpj != link.cnpj:
        return None
    return pedido


@acesso_bp.post('/api/acesso/<token>/fgts/pedidos')
def fgts_criar(token: str):
    from app.services import fgts_pedidos_service as fgts
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, company = resultado
    corpo = request.get_json(silent=True) or {}
    try:
        pedido = fgts.criar(company_id=company.id, cnpj=company.cnpj,
                            acao=str(corpo.get('acao') or 'consultar'),
                            competencia=corpo.get('competencias') or corpo.get('competencia'),
                            link_id=link.id,
                            origem='link', solicitado_por=links.solicitante(link.token))
    except fgts.PedidoRecusado as exc:
        return _erro(str(exc), exc.status)
    return jsonify({'success': True, 'pedido': fgts.serializar(
        pedido, base_pdf=f'/api/acesso/{link.token}/fgts/pedidos/{pedido.id}/pdf')})


@acesso_bp.get('/api/acesso/<token>/fgts/pedidos/<int:pedido_id>')
def fgts_ver(token: str, pedido_id: int):
    from app.services import fgts_pedidos_service as fgts
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, _company = resultado
    pedido = _fgts_pedido(link, pedido_id)
    if not pedido:
        return _erro('Pedido não encontrado.', 404)
    return jsonify({'success': True, 'pedido': fgts.serializar(
        pedido, base_pdf=f'/api/acesso/{link.token}/fgts/pedidos/{pedido.id}/pdf')})


@acesso_bp.get('/api/acesso/<token>/fgts/pedidos/<int:pedido_id>/captcha.jpg')
def fgts_captcha(token: str, pedido_id: int):
    from app.services import fgts_digital_robo
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, _company = resultado
    pedido = _fgts_pedido(link, pedido_id)
    if not pedido or pedido.status != 'aguardando_captcha':
        return _erro('Nenhuma verificação pendente.', 404)
    imagem = fgts_digital_robo.robo().captcha_imagem(pedido.id)
    if not imagem:
        return _erro('Carregando a verificação…', 202)
    return Response(imagem, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


@acesso_bp.post('/api/acesso/<token>/fgts/pedidos/<int:pedido_id>/captcha/toque')
def fgts_captcha_toque(token: str, pedido_id: int):
    from app.services import fgts_digital_robo
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, _company = resultado
    pedido = _fgts_pedido(link, pedido_id)
    if not pedido or pedido.status != 'aguardando_captcha':
        return _erro('Nenhuma verificação pendente.', 404)
    corpo = request.get_json(silent=True) or {}
    try:
        fx, fy = float(corpo.get('x')), float(corpo.get('y'))
    except (TypeError, ValueError):
        return _erro('Toque inválido.')
    if not fgts_digital_robo.robo().clicar_captcha(pedido.id, fx, fy):
        return _erro('Toque inválido.')
    return jsonify({'success': True})


@acesso_bp.post('/api/acesso/<token>/fgts/pedidos/<int:pedido_id>/escolha')
def fgts_escolha(token: str, pedido_id: int):
    """Cliente escolhe a(s) competência(s) da lista; o robô emite na MESMA sessão."""
    from app.services import fgts_digital_robo, fgts_pedidos_service as fgts
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, _company = resultado
    pedido = _fgts_pedido(link, pedido_id)
    if not pedido:
        return _erro('Pedido não encontrado.', 404)
    corpo = request.get_json(silent=True) or {}
    try:
        escolha = fgts.validar_escolha(pedido, corpo.get('competencias'))
    except fgts.PedidoRecusado as exc:
        return _erro(str(exc), exc.status)
    fgts_digital_robo.robo().escolher(pedido.id, escolha)
    return jsonify({'success': True, 'competencias': escolha})


@acesso_bp.get('/api/acesso/<token>/fgts/pedidos/<int:pedido_id>/pdf/<int:indice>')
def fgts_pdf(token: str, pedido_id: int, indice: int):
    from app.services import fgts_pedidos_service as fgts
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, _company = resultado
    pedido = _fgts_pedido(link, pedido_id)
    if not pedido:
        return _erro('Guia não encontrada.', 404)
    caminho = fgts.caminho_pdf(pedido, indice)
    if not caminho:
        return _erro('Esta guia não está mais disponível. Peça de novo.', 410)
    conteudo = caminho.read_bytes()
    if not fgts.pdf_e_da_empresa(conteudo, link.cnpj):
        return _erro('Guia não encontrada.', 404)
    nome = f'FGTS_{pedido.competencia or ""}_{indice + 1}.pdf'
    return Response(conteudo, mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename="{nome}"',
                             'Cache-Control': 'private, no-store'})


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
    hoje = pend.hoje_brasil()

    if corpo.get('debito_id') is not None:
        # Guia que consta no relatório.
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
        exige_data = bool(debito.get('em_atraso'))
    elif str(corpo.get('tipo') or '').upper() in PARCELAMENTO_TYPES:
        # Parcela de parcelamento: só a que consta na última consulta (emitir confere).
        tipo_guia = str(corpo.get('tipo')).upper()
        dados_empresa = pend.pendencias_da_empresa(company)
        if tipo_guia not in tipos_parcelamento(company, dados_empresa.get('parcelamento')):
            return _erro('Parcelamento não disponível para esta empresa.')
        pa = ''.join(ch for ch in str(corpo.get('parcela') or '') if ch.isdigit())
        if len(pa) != 6:
            return _erro('Informe a parcela.')
        exige_data = False
        corpo = {**corpo, 'data_pagamento': None}  # a Receita calcula a parcela para o mês
    else:
        # "Outra competência": guia que não está no relatório (venceu depois dele, ou foi
        # declarada depois). O cliente escolhe tipo e mês; a Receita calcula se houver
        # declaração — se não houver, a SERPRO recusa e a mensagem aparece na tela.
        tipo_guia = str(corpo.get('tipo') or '').upper()
        if tipo_guia not in {t['tipo'] for t in tipos_livres(company)}:
            return _erro('Tipo de guia não disponível para esta empresa.')
        pa = ''.join(ch for ch in str(corpo.get('competencia') or '') if ch.isdigit())
        if tipo_guia == 'DCTFWEB' and len(pa) == 6 and pa[4:] == '13':
            pass  # DCTFWeb do 13º salário
        elif pa not in competencias_permitidas(hoje):
            return _erro('Escolha uma competência entre o mês atual e os últimos 36 meses.')
        exige_data = False

    data_pagamento = None
    bruto = ''.join(ch for ch in str(corpo.get('data_pagamento') or '') if ch.isdigit())
    if bruto or exige_data:
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


@acesso_bp.post('/api/acesso/<token>/parcelas/consultar')
def consultar_parcelas(token: str):
    """Consulta paga das parcelas (a do dia é reaproveitada sem custo) — `{tipo}`."""
    resultado, erro = _link_ou_erro(token)
    if erro:
        return erro
    link, company = resultado
    corpo = request.get_json(silent=True) or {}
    tipo = str(corpo.get('tipo') or '').upper()
    dados_empresa = pend.pendencias_da_empresa(company)
    if tipo not in tipos_parcelamento(company, dados_empresa.get('parcelamento')):
        return _erro('Parcelamento não disponível para esta empresa.')
    try:
        parcelas_service.consultar(cnpj=company.cnpj, tipo=tipo, forcar=False,
                                   solicitado_por=links.solicitante(link.token))
    except parcelas_service.ConsultaBloqueada as exc:
        return _erro(str(exc), exc.status)
    except Exception as exc:
        from app.services.serpro_das_service import SerproApiError
        if isinstance(exc, SerproApiError):
            return _erro(str(exc), 400 if 400 <= exc.status_code < 500 else 502)
        return _erro(f'Não foi possível consultar as parcelas agora: {exc}', 502)
    return jsonify({'success': True, 'parcelamento': _parcelamento_item(company.cnpj, tipo)})


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
