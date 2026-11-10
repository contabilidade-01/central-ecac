"""Novo frontend da Central (`/app`) e a API que ele usa (`/api/painel/*`). Desvio 28.

O frontend é um build estático (React) em `app/static/novo`, gerado de `frontend/`. A SPA
antiga do exe continua em `/`; cada tela nova só substitui a antiga depois de aprovada.
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory

from app.extensions import db
from app.models import Company
from app.services import painel_service, permissoes

painel_bp = Blueprint('painel', __name__)
PASTA_NOVO = Path(__file__).resolve().parents[1] / 'static' / 'novo'


def _usuario():
    from app.security import usuario_atual
    return usuario_atual() or {}


@painel_bp.get('/api/painel/mesa')
def mesa():
    return jsonify({'success': True, **painel_service.mesa(_usuario())})


@painel_bp.get('/api/painel/empresas/<int:company_id>')
def ficha(company_id: int):
    usuario = _usuario()
    if not permissoes.pode_empresa(usuario, company_id):
        return jsonify({'success': False, 'message': 'Esta empresa não está liberada para o seu usuário.'}), 403
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({'success': False, 'message': 'Empresa não encontrada.'}), 404
    return jsonify({'success': True, **painel_service.ficha(company)})


@painel_bp.get('/api/painel/empresas')
def empresas():
    return jsonify({'success': True, **painel_service.empresas(_usuario())})


@painel_bp.get('/api/painel/empresas/<int:company_id>/contato')
def contato(company_id: int):
    """Só diz se a empresa está no portal e se o WhatsApp é válido: a Central não copia contato."""
    usuario = _usuario()
    if not permissoes.pode_empresa(usuario, company_id):
        return jsonify({'success': False, 'message': 'Esta empresa não está liberada para o seu usuário.'}), 403
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({'success': False, 'message': 'Empresa não encontrada.'}), 404
    from app.services import nescon_service
    r = nescon_service.vinculo(company.cnpj)
    if r.get('status') == 'erro_rede':
        return jsonify({'success': True, 'disponivel': False, 'motivo': r.get('motivo')})
    return jsonify({'success': True, 'disponivel': True, 'vinculada': bool(r.get('vinculada')),
                    'whatsapp_valido': bool(r.get('whatsapp_valido')), 'motivo': r.get('motivo')})


@painel_bp.get('/api/painel/status')
def status():
    return jsonify({'success': True, **painel_service.status(_usuario())})


@painel_bp.get('/api/painel/busca')
def busca():
    return jsonify({'success': True, 'empresas': painel_service.buscar(_usuario(), request.args.get('q', ''))})


@painel_bp.get('/api/painel/eu')
def eu():
    usuario = _usuario()
    return jsonify({'success': True, 'nome': usuario.get('nome') or usuario.get('usuario') or '',
                    'admin': permissoes.e_admin(usuario), 'menu': permissoes.menu_do_usuario(usuario)})


@painel_bp.get('/app')
@painel_bp.get('/app/')
@painel_bp.get('/app/<path:caminho>')
def frontend(caminho: str = ''):
    if not (PASTA_NOVO / 'index.html').exists():
        abort(404)
    alvo = PASTA_NOVO / caminho
    if caminho and alvo.is_file() and PASTA_NOVO in alvo.resolve().parents:
        return send_from_directory(PASTA_NOVO, caminho)
    resposta = send_from_directory(PASTA_NOVO, 'index.html')
    resposta.headers['Cache-Control'] = 'no-store'
    return resposta
