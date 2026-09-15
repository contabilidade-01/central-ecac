"""Ticar empresas em lote (Marcar todos / Desmarcar todos) — sem rede."""
import os
import tempfile

import pytest

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='emp_lote_'))
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ.setdefault('SCHEDULER_ENABLED', '0')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402


@pytest.fixture()
def http():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app.escritorio_models import EscritorioEmpresa
        from app.models import Company
        EscritorioEmpresa.query.delete()
        Company.query.delete()
        db.session.add_all([
            Company(razao_social='ALFA', cnpj='11222333000181', ativo=True),
            Company(razao_social='BETA', cnpj='11444777000161', ativo=True),
            Company(razao_social='GAMA INATIVA', cnpj='50006293000192', ativo=False),
        ])
        db.session.commit()
        cliente = app.test_client()
        with cliente.session_transaction() as sessao:
            sessao['usuario_id'] = 'ambiente'
        yield cliente, {c.razao_social: c.id for c in Company.query.all()}
        db.session.remove()


def test_marcar_todos_pula_inativas_e_desmarcar(http):
    from app.services import escritorio_empresas as emp
    cliente, ids = http
    todos = list(ids.values())
    r = cliente.post('/escritorio/api/empresas/toggle-lote', json={'company_ids': todos, 'incluso': True})
    assert r.status_code == 200 and r.json['ok']
    assert sorted(r.json['alterados']) == sorted([ids['ALFA'], ids['BETA']])
    assert r.json['pulados_inativos'] == [ids['GAMA INATIVA']]
    assert sorted(emp.ids_incluidas()) == sorted([ids['ALFA'], ids['BETA']])

    r = cliente.post('/escritorio/api/empresas/toggle-lote', json={'company_ids': [ids['ALFA']], 'incluso': False})
    assert r.json['alterados'] == [ids['ALFA']] and emp.ids_incluidas() == [ids['BETA']]

    r = cliente.post('/escritorio/api/empresas/toggle-lote', json={'company_ids': [], 'incluso': True})
    assert r.status_code == 400


def test_tela_tem_filtros_e_botoes(http):
    cliente, _ids = http
    r = cliente.get('/escritorio/empresas')
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    for trecho in ('id="fBusca"', 'id="fEscritorio"', 'id="fCadastro"', 'id="btnMarcarTodos"',
                   'id="btnDesmarcarTodos"', '/escritorio/api/empresas/toggle-lote'):
        assert trecho in html
