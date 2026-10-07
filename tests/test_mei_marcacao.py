"""Marcação de MEI pelo admin: MEI sai da busca de relatórios (sem rede, sem custo).

Rodar:  python -m pytest tests/test_mei_marcacao.py -q
"""
import os
import tempfile

import pytest

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='mei_teste_')
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['LIMITE_GASTO_MENSAL'] = '0'
os.environ['SCHEDULER_ENABLED'] = '0'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app.integracao_models import EmpresaMei
        from app.models import Company
        EmpresaMei.query.delete()
        Company.query.delete()
        a = Company(razao_social='ALFA MEI', cnpj='11222333000181', ativo=True)
        b = Company(razao_social='BETA SN', cnpj='11444777000161', ativo=True)
        db.session.add_all([a, b])
        db.session.commit()
        yield app, a.id, b.id


def test_mei_marcado_nao_busca_relatorio(ambiente):
    from app.services import mei_service
    from app.services.report_service import ReportService
    _, mei_id, sn_id = ambiente
    mei_service.definir(mei_id, True, 'admin')
    resultado = ReportService().process_company(mei_id)
    assert resultado.success is False
    assert 'MEI' in resultado.message
    assert mei_service.ids_mei() == {mei_id}
    assert not mei_service.e_mei(sn_id)


def test_desmarcar_volta_a_buscar(ambiente):
    from app.services import mei_service
    _, mei_id, _ = ambiente
    mei_service.definir(mei_id, True)
    mei_service.definir(mei_id, False)
    assert not mei_service.e_mei(mei_id)


def test_agendamento_pula_mei(ambiente):
    from app.services import agendamento_service, mei_service
    from app.services.procuracao_service import ProcuracaoService
    _, mei_id, sn_id = ambiente
    mei_service.definir(mei_id, True)
    original = ProcuracaoService.pode_gastar
    ProcuracaoService.pode_gastar = staticmethod(lambda c: (True, ''))
    try:
        ids = {c.id for c in agendamento_service._empresas_liberadas()}
    finally:
        ProcuracaoService.pode_gastar = original
    assert ids == {sn_id}


def test_rota_marcar_mei(ambiente):
    app, mei_id, _ = ambiente
    from app.services import mei_service
    cliente = app.test_client()
    resp = cliente.put(f'/api/companies/{mei_id}/mei', json={'eh_mei': True},
                       headers={'Authorization': 'Basic dGVzdGU6dGVzdGU='})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert mei_service.e_mei(mei_id)
