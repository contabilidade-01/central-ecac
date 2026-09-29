"""API interna para o portal do cliente (nescon-clientes) — `/api/interno/*`.

⚠️ DESVIO INTENCIONAL (18o) — NÃO existe no exe. Pedido do Jean (29/09/2026).

Divisão de responsabilidades (decidida na revisão do plano de notificações):

* **Este sistema** puxa a situação fiscal, guarda o histórico, emite guias e registra
  quanto custou. É a FONTE das pendências.
* **O portal** é o dono do cliente: contato, canal (e-mail/WhatsApp), opt-out, janela
  de envio, histórico de notificação e a máquina de estados da cobrança.

Por isso NÃO existe aqui e-mail, telefone nem envio de mensagem. Só:

  GET  /api/interno/ping
  GET  /api/interno/pendencias?desde=AAAA-MM-DD[&cnpj=...]     pendências (custo zero)
  GET  /api/interno/pendencias/<cnpj>
  POST /api/interno/das/emitir                                 guia (paga; reaproveita PDF)
  GET  /api/interno/das/emissoes?cnpj=&desde=AAAA-MM-DD        eventos de emissão
  GET  /api/interno/das/<id>/pdf
  POST /api/interno/reprocessar                                regerar relatório em N dias úteis
  GET  /api/interno/reprocessamentos?cnpj=&pendentes=1

Autenticação: cabeçalho `X-Integracao-Token` (ver `services/integracao_token.py`),
verificado em `app/security.py` ANTES de qualquer rota daqui rodar.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from app.services import fila_reprocessamento_service as fila
from app.services import das_emissao_service as emissao
from app.services import pendencias_service as pend
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
