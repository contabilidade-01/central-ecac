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
_NA_FILA: list = []   # empresas pedidas em /api/painel/consultar que ainda não começaram
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


@painel_bp.post('/api/painel/consultar')
def consultar():
    """Consulta a situação fiscal (paga, SERPRO) das empresas escolhidas, uma de cada vez.

    Corpo: {"company_ids": [..]} ou {"todas": true}. Mesmo caminho do "processar todas" da
    tela antiga (ReportService.process_company), só que restrito às empresas pedidas e às
    liberadas para o usuário. Empresa já em processamento é pulada.
    """
    import threading
    from flask import current_app
    from app.services.report_service import ReportService
    usuario = _usuario()
    corpo = request.get_json(silent=True) or {}
    visiveis = {c.id: c for c in painel_service._empresas_visiveis(usuario)}
    if corpo.get('todas'):
        ids = list(visiveis)
    else:
        ids = [int(i) for i in corpo.get('company_ids') or [] if str(i).isdigit() and int(i) in visiveis]
    ids = [i for i in ids if visiveis[i].processing_status != 'processing']
    if not ids:
        return jsonify({'success': False,
                        'message': 'Nenhuma empresa para consultar (ou todas já estão em consulta).'}), 400

    novos = [i for i in ids if i not in _NA_FILA]
    _NA_FILA.extend(novos)

    def rodar(app, lista):
        with app.app_context():
            servico = ReportService()
            for cid in lista:
                if cid in _NA_FILA:
                    _NA_FILA.remove(cid)
                try:
                    c = db.session.get(Company, cid)
                    if c and c.ativo and c.processing_status != 'processing':
                        servico.process_company(cid)
                except Exception:
                    db.session.rollback()
                    app.logger.exception('[PAINEL] consulta da empresa %s falhou', cid)

    if not novos:
        return jsonify({'success': False, 'message': 'Essas empresas já estão na fila de consulta.'}), 400
    threading.Thread(target=rodar, args=(current_app._get_current_object(), novos), daemon=True).start()
    return jsonify({'success': True, 'quantidade': len(novos)})


@painel_bp.get('/api/painel/consultas')
def consultas():
    """Andamento das consultas: empresas em processamento agora e as que terminaram com erro."""
    saida = []
    for c in painel_service._empresas_visiveis(_usuario()):
        if c.processing_status in ('processing', 'error'):
            saida.append({'company_id': c.id, 'razao_social': c.razao_social, 'status': c.processing_status,
                          'etapa': c.processing_step, 'progresso': c.processing_progress or 0,
                          'mensagem': c.processing_message})
    return jsonify({'success': True, 'em_andamento': sum(1 for s in saida if s['status'] == 'processing'),
                    'na_fila': len(_NA_FILA), 'empresas': saida})


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
