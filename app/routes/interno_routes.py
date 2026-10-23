"""API interna para o portal do cliente (nescon-clientes) — `/api/interno/*`.

⚠️ DESVIO INTENCIONAL (18o) — NÃO existe no exe. Pedido do Jean (29/09/2026).

Divisão de responsabilidades (decidida na revisão do plano de notificações):

* **Este sistema** puxa a situação fiscal, guarda o histórico, emite guias e registra
  quanto custou. É a FONTE das pendências.
* **O portal** é o dono do cliente: contato, canal (e-mail/WhatsApp), opt-out, janela
  de envio, histórico de notificação e a máquina de estados da cobrança.

Por isso NÃO existe aqui e-mail, telefone nem envio de mensagem. Só:

  GET  /api/interno/ping
  GET  /api/interno/empresas?busca=                            busca por razão social/CNPJ
  GET  /api/interno/empresa/<cnpj>                             cadastro (MEI, parcelamentos)
  GET  /api/interno/pendencias?desde=AAAA-MM-DD[&cnpj=...]     pendências (custo zero)
  GET  /api/interno/pendencias/<cnpj>
  POST /api/interno/das/emitir                                 guia (paga; reaproveita PDF;
                                                               `apenas_reuso` nunca paga)
  GET  /api/interno/das/emissoes?cnpj=&desde=AAAA-MM-DD        eventos de emissão
  GET  /api/interno/das/<id>/pdf
  POST /api/interno/reprocessar                                regerar relatório em N dias úteis
  GET  /api/interno/reprocessamentos?cnpj=&pendentes=1
  GET  /api/interno/fgts/estado                                robô do FGTS Digital (desvio 22)
  GET  /api/interno/fgts/pedidos?cnpj=                         pedidos ao robô e andamento
  POST /api/interno/fgts/pedidos                               escritório pede consulta/emissão
  GET  /api/interno/fgts/pedidos/<id>?cnpj=                    andamento (CNPJ conferido)
  POST /api/interno/fgts/pedidos/<id>/escolha                  meses escolhidos na sessão aberta
  GET  /api/interno/fgts/pedidos/<id>/captcha.jpg?cnpj=        recorte do captcha
  POST /api/interno/fgts/pedidos/<id>/captcha/toque            toque (0..1)
  GET  /api/interno/fgts/pedidos/<id>/pdf/<n>?cnpj=            GFD
  POST /api/interno/acesso-links                               link sem senha servido AQUI (desvio 21)
  GET  /api/interno/acesso-links?cnpj=
  POST /api/interno/acesso-links/revogar

Autenticação: cabeçalho `X-Integracao-Token` (ver `services/integracao_token.py`),
verificado em `app/security.py` ANTES de qualquer rota daqui rodar.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from app.services import fila_reprocessamento_service as fila
from app.services import das_emissao_service as emissao
from app.services import pendencias_service as pend
from app.services import parcelas_service
from app.services import confirmacao_service
from app.services import acesso_links_service as links
from app.extensions import db
from app.integracao_models import DasEmissao

interno_bp = Blueprint('interno', __name__)


def _erro(mensagem: str, codigo: int = 400):
    return jsonify({'success': False, 'message': mensagem}), codigo


def _data(valor: str | None):
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


@interno_bp.get('/ping')
def ping():
    from app.services.limite_gasto_service import LimiteGastoService
    return jsonify({
        'success': True,
        'sistema': 'central-ecac',
        'agora': datetime.now().isoformat(timespec='seconds'),
        'limite_gasto': LimiteGastoService.resumo(),
    })


def _eh_mei(company_id: int) -> bool:
    from app.integracao_models import EmpresaMei
    marca = EmpresaMei.query.filter_by(company_id=company_id).first()
    return bool(marca and marca.eh_mei)


def _empresa_resumo(company) -> dict:
    return {'company_id': company.id, 'cnpj': company.cnpj, 'razao_social': company.razao_social,
            'ativo': bool(company.ativo), 'mei': _eh_mei(company.id)}


@interno_bp.get('/empresas')
def buscar_empresas():
    """Busca para vincular contato → empresa no AtendeChat. Custo zero, até 20 itens."""
    from app.models import Company
    busca = (request.args.get('busca') or '').strip()
    if len(busca) < 2:
        return _erro('Digite ao menos 2 caracteres.')
    digitos = ''.join(ch for ch in busca if ch.isdigit())
    consulta = Company.query
    if digitos and len(digitos) >= 2 and not any(ch.isalpha() for ch in busca):
        consulta = consulta.filter(Company.cnpj.like(f'%{digitos}%'))
    else:
        termo = busca.replace('\\', '').replace('%', '').replace('_', '')
        consulta = consulta.filter(Company.razao_social.ilike(f'%{termo}%'))
    itens = consulta.order_by(Company.ativo.desc(), Company.razao_social).limit(20).all()
    return jsonify({'success': True, 'total': len(itens), 'empresas': [_empresa_resumo(c) for c in itens]})


@interno_bp.get('/empresa/<cnpj>')
def empresa_cadastro(cnpj: str):
    """Cadastro da empresa para a tela de guias: é MEI? quais parcelamentos acompanha?"""
    from app.services.parcelamentos_serpro_service import PARCELAMENTO_TYPES
    company = pend.empresa_por_cnpj(cnpj)
    if not company:
        return _erro('Empresa não cadastrada.', 404)
    parcelamentos = [{'tipo': tipo, 'rotulo': parcelas_service.rotulo(tipo),
                      'habilitado': bool(getattr(company, cfg.get('flag') or '', False))}
                     for tipo, cfg in PARCELAMENTO_TYPES.items()]
    return jsonify({'success': True, 'empresa': {**_empresa_resumo(company), 'parcelamentos': parcelamentos}})


@interno_bp.get('/pendencias')
def listar_pendencias():
    desde = _data(request.args.get('desde'))
    if request.args.get('desde') and not desde:
        return _erro('desde deve ser AAAA-MM-DD.')
    cnpj = request.args.get('cnpj')
    if cnpj:
        company = pend.empresa_por_cnpj(cnpj)
        if not company:
            return _erro('Empresa não cadastrada.', 404)
        empresas = [pend.pendencias_da_empresa(company, desde=desde)]
    else:
        empresas = pend.pendencias_da_carteira(desde=desde, apenas_ativas=True)
    return jsonify({
        'success': True,
        'gerado_em': datetime.now().isoformat(timespec='seconds'),
        'desde': desde.isoformat() if desde else None,
        'total': len(empresas),
        'empresas': empresas,
    })


@interno_bp.get('/pendencias/<cnpj>')
def pendencias_empresa(cnpj: str):
    company = pend.empresa_por_cnpj(cnpj)
    if not company:
        return _erro('Empresa não cadastrada.', 404)
    desde = _data(request.args.get('desde'))
    return jsonify({'success': True, 'empresa': pend.pendencias_da_empresa(company, desde=desde)})


@interno_bp.post('/das/emitir')
def emitir_guia():
    corpo = request.get_json(silent=True) or {}
    try:
        registro, reuso = emissao.emitir(
            cnpj=corpo.get('cnpj'),
            tipo=corpo.get('tipo') or 'SN',
            periodo_apuracao=corpo.get('periodo_apuracao'),
            data_consolidacao=corpo.get('data_consolidacao'),
            categoria=corpo.get('categoria'),
            origem=str(corpo.get('origem') or 'portal')[:20],
            solicitado_por=(str(corpo.get('solicitado_por'))[:120]
                            if corpo.get('solicitado_por') else None),
            forcar=bool(corpo.get('forcar')),
            apenas_reuso=bool(corpo.get('apenas_reuso')),
        )
    except emissao.EmissaoBloqueada as exc:
        return _erro(str(exc), exc.status)
    except Exception as exc:  # erro da SERPRO ou de rede — mensagem legível, sem stack
        from app.services.serpro_das_service import SerproApiError
        if isinstance(exc, SerproApiError):
            return _erro(str(exc), 400 if 400 <= exc.status_code < 500 else 502)
        return _erro(f'Falha na emissão: {exc}', 502)

    return jsonify({
        'success': True,
        'reuso': reuso,
        'emissao': emissao.serializar(registro, reuso=reuso),
        'pdf_url': f'/api/interno/das/{registro.id}/pdf',
    })


@interno_bp.get('/parcelas/<cnpj>')
def parcelas_guardadas(cnpj: str):
    """Última consulta de parcelas de um tipo — custo zero."""
    tipo = str(request.args.get('tipo') or 'PARC_SN').upper()
    if tipo not in parcelas_service.TIPOS:
        return _erro(f'Tipo de parcelamento inválido: {tipo}.', 400)
    return jsonify({'success': True, 'tipos': {t: parcelas_service.rotulo(t) for t in parcelas_service.TIPOS},
                    **parcelas_service.serializar(parcelas_service.ultima(cnpj, tipo))})


@interno_bp.post('/parcelas/consultar')
def parcelas_consultar():
    """Consulta paga (a do dia é reaproveitada). `{cnpj, tipo, forcar?, solicitado_por?}`."""
    corpo = request.get_json(silent=True) or {}
    try:
        r = parcelas_service.consultar(
            cnpj=corpo.get('cnpj'), tipo=corpo.get('tipo') or 'PARC_SN', forcar=bool(corpo.get('forcar')),
            solicitado_por=(str(corpo.get('solicitado_por'))[:120] if corpo.get('solicitado_por') else None))
    except parcelas_service.ConsultaBloqueada as exc:
        return _erro(str(exc), exc.status)
    except Exception as exc:
        from app.services.serpro_das_service import SerproApiError
        if isinstance(exc, SerproApiError):
            return _erro(str(exc), 400 if 400 <= exc.status_code < 500 else 502)
        return _erro(f'Falha na consulta: {exc}', 502)
    return jsonify({'success': True, **r})


@interno_bp.post('/situacao/confirmar')
def situacao_confirmar():
    """Relatório de situação fiscal NA HORA para uma empresa (pago, ~R$ 0,32). `{cnpj, solicitado_por?}`.
    Responde logo; o portal acompanha em GET /situacao/<cnpj>/confirmacao."""
    corpo = request.get_json(silent=True) or {}
    try:
        r = confirmacao_service.iniciar(
            corpo.get('cnpj'),
            solicitado_por=(str(corpo.get('solicitado_por'))[:120] if corpo.get('solicitado_por') else None))
    except confirmacao_service.ConfirmacaoBloqueada as exc:
        return _erro(str(exc), exc.status)
    return jsonify({'success': True, **r})


@interno_bp.get('/situacao/<cnpj>/confirmacao')
def situacao_confirmacao(cnpj: str):
    return jsonify({'success': True, **confirmacao_service.serializar(confirmacao_service.ultima(cnpj))})


@interno_bp.get('/das/emissoes')
def listar_emissoes():
    consulta = DasEmissao.query
    cnpj = ''.join(ch for ch in (request.args.get('cnpj') or '') if ch.isdigit())
    if cnpj:
        consulta = consulta.filter_by(cnpj=cnpj)
    desde = _data(request.args.get('desde'))
    if desde:
        consulta = consulta.filter(DasEmissao.emitido_em >= desde)
    itens = consulta.order_by(DasEmissao.id.desc()).limit(500).all()
    return jsonify({'success': True, 'total': len(itens),
                    'emissoes': [emissao.serializar(i) for i in itens]})


@interno_bp.get('/das/<int:emissao_id>/pdf')
def baixar_pdf(emissao_id: int):
    registro = db.session.get(DasEmissao, emissao_id)
    if not registro:
        return _erro('Emissão não encontrada.', 404)
    # Recorte por CNPJ: o portal SEMPRE manda o CNPJ do JWT do cliente. Emissão de outra
    # empresa com id adivinhado não sai.
    cnpj = ''.join(ch for ch in (request.args.get('cnpj') or '') if ch.isdigit())
    if cnpj and cnpj != registro.cnpj:
        return _erro('Emissão não pertence a esta empresa.', 403)
    conteudo = emissao.ler_pdf(registro)
    if not conteudo:
        return _erro('PDF não está mais disponível. Emita novamente.', 410)
    nome = f'{registro.tipo}_{registro.cnpj}_{registro.periodo_apuracao}.pdf'
    return Response(conteudo, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{nome}"'})


@interno_bp.post('/reprocessar')
def reprocessar():
    corpo = request.get_json(silent=True) or {}
    company = pend.empresa_por_cnpj(corpo.get('cnpj'))
    if not company:
        return _erro('Empresa não cadastrada.', 404)
    try:
        dias = int(corpo.get('dias_uteis', 5))
    except (TypeError, ValueError):
        return _erro('dias_uteis deve ser inteiro.')
    resultado = fila.agendar(company, dias_uteis=dias,
                             motivo=str(corpo.get('motivo') or 'recalculo_guia')[:60],
                             origem=str(corpo.get('origem') or 'portal')[:20])
    codigo = 200 if resultado.get('ok') else 409
    return jsonify({'success': bool(resultado.get('ok')), **resultado}), codigo


@interno_bp.get('/reprocessamentos')
def listar_reprocessamentos():
    itens = fila.listar(cnpj=request.args.get('cnpj'),
                        apenas_pendentes=request.args.get('pendentes') in ('1', 'true'))
    return jsonify({'success': True, 'total': len(itens), 'itens': itens})


# ------------------------------------------------------------------ links sem senha
# DESVIO 21 — o AtendeChat pede o link e manda na conversa; a página é servida por
# este sistema (`/g/<token>`, ver routes/acesso_routes.py). O portal do cliente tem o
# `/g/` dele, com a tabela dele; nada aqui toca aquilo.
def _base_publica() -> str:
    return (request.headers.get('X-Public-Base-Url') or '').strip() or request.host_url.rstrip('/')


@interno_bp.post('/acesso-links')
def criar_acesso_link():
    corpo = request.get_json(silent=True) or {}
    company = pend.empresa_por_cnpj(corpo.get('cnpj'))
    if not company:
        return _erro('Empresa não cadastrada.', 404)
    if not company.ativo:
        return _erro('Empresa inativa no Central e-CAC.', 409)
    origem = str(corpo.get('origem') or 'atendechat')[:20]
    if origem not in links.ORIGENS:
        return _erro('origem inválida.')
    try:
        dias = int(corpo['dias']) if corpo.get('dias') else None
    except (TypeError, ValueError):
        return _erro('dias deve ser inteiro.')
    link = links.obter_ou_criar(company, origem=origem,
                                solicitado_por=str(corpo.get('solicitado_por') or '')[:120] or None,
                                dias=dias)
    from app.services import fgts_pedidos_service as fgts
    return jsonify({'success': True, 'link': links.serializar(link),
                    'url': links.url_do_link(link.token, _base_publica()),
                    # A página do link tem a seção FGTS (desvio 22)? O AtendeChat cita no texto.
                    'fgts': fgts.liberado_para(company.cnpj)})


@interno_bp.get('/acesso-links')
def listar_acesso_links():
    company = pend.empresa_por_cnpj(request.args.get('cnpj'))
    if not company:
        return _erro('Empresa não cadastrada.', 404)
    itens = links.listar_da_empresa(company.id)
    for item in itens:
        item['url'] = links.url_do_link(item['token'], _base_publica())
    return jsonify({'success': True, 'total': len(itens), 'links': itens})


@interno_bp.post('/acesso-links/revogar')
def revogar_acesso_links():
    corpo = request.get_json(silent=True) or {}
    company = pend.empresa_por_cnpj(corpo.get('cnpj'))
    if not company:
        return _erro('Empresa não cadastrada.', 404)
    quem = str(corpo.get('solicitado_por') or corpo.get('origem') or 'interno')[:120]
    return jsonify({'success': True, 'revogados': links.revogar_da_empresa(company.id, quem)})


# ------------------------------------------------------------------- FGTS (desvio 22)
@interno_bp.get('/fgts/estado')
def fgts_estado():
    """Saúde do robô do FGTS Digital (custo zero, não abre navegador)."""
    from app.services import fgts_digital_robo, fgts_pedidos_service as fgts
    return jsonify({'success': True, 'habilitado': fgts.habilitado(),
                    'robo': fgts_digital_robo.robo().estado() if fgts.habilitado() else None})


@interno_bp.get('/fgts/pedidos')
def fgts_pedidos():
    """Últimos pedidos ao robô, por CNPJ (o AtendeChat mostra o andamento ao atendente)."""
    from app.integracao_models import FgtsPedido
    from app.services import fgts_pedidos_service as fgts
    consulta = FgtsPedido.query
    cnpj = ''.join(ch for ch in (request.args.get('cnpj') or '') if ch.isdigit())
    if cnpj:
        consulta = consulta.filter_by(cnpj=cnpj)
    itens = consulta.order_by(FgtsPedido.id.desc()).limit(50).all()
    return jsonify({'success': True, 'total': len(itens),
                    'pedidos': [{**fgts.serializar(p), 'cnpj': p.cnpj, 'origem': p.origem}
                                for p in itens]})


def _fgts_pedido_do_cnpj(pedido_id: int):
    """Pedido + CNPJ conferido: o chamador SEMPRE manda o CNPJ do vínculo (o AtendeChat
    tira do cadastro do contato); pedido de outra empresa = 404."""
    from app.integracao_models import FgtsPedido
    cnpj = ''.join(ch for ch in (request.args.get('cnpj') or
                                 (request.get_json(silent=True) or {}).get('cnpj') or '') if ch.isdigit())
    pedido = db.session.get(FgtsPedido, pedido_id)
    if not cnpj or not pedido or pedido.cnpj != cnpj:
        return None
    return pedido


def _fgts_json(pedido):
    from app.services import fgts_pedidos_service as fgts
    return {**fgts.serializar(pedido, base_pdf=f'/api/interno/fgts/pedidos/{pedido.id}/pdf'),
            'cnpj': pedido.cnpj, 'origem': pedido.origem}


@interno_bp.post('/fgts/pedidos')
def fgts_criar_pedido():
    """Escritório pede ao robô: `{cnpj, acao: consultar}` ou `{cnpj, acao: emitir,
    competencias: [AAAAMM…]}` (meses da consulta de hoje). Retoma o pedido aberto."""
    from app.services import fgts_pedidos_service as fgts
    corpo = request.get_json(silent=True) or {}
    company = pend.empresa_por_cnpj(corpo.get('cnpj'))
    if not company:
        return _erro('Empresa não cadastrada.', 404)
    if not company.ativo:
        return _erro('Empresa inativa no Central e-CAC.', 409)
    origem = str(corpo.get('origem') or 'atendechat')[:20]
    try:
        pedido = fgts.criar(company_id=company.id, cnpj=company.cnpj,
                            acao=str(corpo.get('acao') or 'consultar'),
                            competencia=corpo.get('competencias') or corpo.get('competencia'),
                            link_id=None, origem=origem,
                            solicitado_por=str(corpo.get('solicitado_por') or origem)[:120])
    except fgts.PedidoRecusado as exc:
        return _erro(str(exc), exc.status)
    return jsonify({'success': True, 'pedido': _fgts_json(pedido)})


@interno_bp.get('/fgts/pedidos/<int:pedido_id>')
def fgts_ver_pedido(pedido_id: int):
    pedido = _fgts_pedido_do_cnpj(pedido_id)
    if not pedido:
        return _erro('Pedido não encontrado.', 404)
    return jsonify({'success': True, 'pedido': _fgts_json(pedido)})


@interno_bp.post('/fgts/pedidos/<int:pedido_id>/escolha')
def fgts_escolher(pedido_id: int):
    from app.services import fgts_digital_robo, fgts_pedidos_service as fgts
    pedido = _fgts_pedido_do_cnpj(pedido_id)
    if not pedido:
        return _erro('Pedido não encontrado.', 404)
    try:
        escolha = fgts.validar_escolha(pedido, (request.get_json(silent=True) or {}).get('competencias'))
    except fgts.PedidoRecusado as exc:
        return _erro(str(exc), exc.status)
    fgts_digital_robo.robo().escolher(pedido.id, escolha)
    return jsonify({'success': True, 'competencias': escolha})


@interno_bp.get('/fgts/pedidos/<int:pedido_id>/captcha.jpg')
def fgts_captcha_imagem(pedido_id: int):
    from app.services import fgts_digital_robo
    pedido = _fgts_pedido_do_cnpj(pedido_id)
    if not pedido or pedido.status != 'aguardando_captcha':
        return _erro('Nenhuma verificação pendente.', 404)
    imagem = fgts_digital_robo.robo().captcha_imagem(pedido.id)
    if not imagem:
        return _erro('Carregando a verificação…', 202)
    return Response(imagem, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


@interno_bp.post('/fgts/pedidos/<int:pedido_id>/captcha/toque')
def fgts_captcha_toque_interno(pedido_id: int):
    from app.services import fgts_digital_robo
    pedido = _fgts_pedido_do_cnpj(pedido_id)
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


@interno_bp.get('/fgts/pedidos/<int:pedido_id>/pdf/<int:indice>')
def fgts_pdf_interno(pedido_id: int, indice: int):
    from app.services import fgts_pedidos_service as fgts
    pedido = _fgts_pedido_do_cnpj(pedido_id)
    if not pedido:
        return _erro('Guia não encontrada.', 404)
    caminho = fgts.caminho_pdf(pedido, indice)
    if not caminho:
        return _erro('Esta guia não está mais disponível. Gere novamente.', 410)
    conteudo = caminho.read_bytes()
    if not fgts.pdf_e_da_empresa(conteudo, pedido.cnpj):
        return _erro('Guia não encontrada.', 404)
    return Response(conteudo, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="FGTS_{pedido.cnpj}_{indice + 1}.pdf"'})
