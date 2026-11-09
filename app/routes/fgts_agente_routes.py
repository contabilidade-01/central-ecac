"""API do Agente FGTS (`/api/fgts-agente/*`). Desvio 27.

Só aceita o cabeçalho `X-Agente-Token`, verificado em `app/security.py` antes de qualquer
rota daqui rodar. O agente nunca recebe dados de outras rotas e nunca escolhe o CNPJ: o
CNPJ vem do pedido que a Central reservou para ele.
"""

from __future__ import annotations

import json

from flask import Blueprint, g, jsonify, request

from app.extensions import db
from app.fgts_models import FgtsAgente
from app.services.fgts import agentes, guias

fgts_agente_bp = Blueprint('fgts_agente', __name__, url_prefix='/api/fgts-agente')


def _agente() -> FgtsAgente:
    return db.session.get(FgtsAgente, g.fgts_agente_id)


def _erro(mensagem: str, status: int):
    return jsonify({'success': False, 'message': mensagem}), status


@fgts_agente_bp.post('/sinal')
def sinal():
    return jsonify({'success': True, **agentes.sinal(_agente(), request.get_json(silent=True) or {})})


@fgts_agente_bp.post('/proximo')
def proximo():
    pedido = agentes.reservar_proximo(_agente())
    if not pedido:
        return ('', 204)
    return jsonify({'success': True, 'pedido': pedido})


@fgts_agente_bp.post('/pedidos/<int:pedido_id>/progresso')
def progresso(pedido_id: int):
    try:
        return jsonify({'success': True, **agentes.progresso(_agente(), pedido_id, request.get_json(silent=True) or {})})
    except agentes.RecusaAgente as exc:
        return _erro(str(exc), exc.status)


@fgts_agente_bp.post('/pedidos/<int:pedido_id>/resultado')
def resultado(pedido_id: int):
    try:
        return jsonify({'success': True, **agentes.resultado(_agente(), pedido_id, request.get_json(silent=True) or {})})
    except agentes.RecusaAgente as exc:
        return _erro(str(exc), exc.status)


@fgts_agente_bp.post('/pedidos/<int:pedido_id>/guias')
def receber_guia(pedido_id: int):
    """multipart: `pdf` (arquivo) + `meta` (JSON com numero, competencia, grupo, valor,
    vencimento, data_pagamento, pix)."""
    agente = _agente()
    try:
        pedido = agentes._pedido_do_agente(agente, pedido_id)
    except agentes.RecusaAgente as exc:
        return _erro(str(exc), exc.status)
    if pedido.status not in ('executando', 'aguardando_captcha'):
        return _erro('O pedido já foi encerrado.', 409)
    arquivo = request.files.get('pdf')
    try:
        meta = json.loads(request.form.get('meta') or '{}')
    except ValueError:
        return _erro('meta não é JSON.', 400)
    try:
        guia, nova = guias.receber(pedido, arquivo.read() if arquivo else b'', meta)
    except guias.GuiaRecusada as exc:
        db.session.rollback()
        agentes.evento('guia_recusada', str(exc), pedido_id=pedido_id, agente_id=agente.id, cnpj=pedido.cnpj)
        db.session.commit()
        return _erro(str(exc), exc.status)
    agentes._renovar(pedido)
    agentes.evento('guia_recebida' if nova else 'guia_repetida', guia.numero, pedido_id=pedido_id,
                   agente_id=agente.id, cnpj=pedido.cnpj)
    db.session.commit()
    return jsonify({'success': True, 'nova': nova, 'guia': guias.serializar(guia)}), (201 if nova else 200)
