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


AUTH = {'Authorization': 'Basic dGVzdGU6dGVzdGU='}


def _limpar_selecoes():
    from app.integracao_models import DasEmissao, MeiGuiaSelecionada
    MeiGuiaSelecionada.query.delete()
    DasEmissao.query.delete()
    db.session.commit()


def test_so_gera_guia_marcada(ambiente, monkeypatch):
    app, mei_id, sn_id = ambiente
    from app.integracao_models import MeiGuiaSelecionada
    from app.services import das_emissao_service as svc, mei_service
    from app.models import Company
    _limpar_selecoes()
    outro = Company(razao_social='GAMA MEI', cnpj='11555666000100', ativo=True)
    db.session.add(outro)
    db.session.commit()
    mei_service.definir(mei_id, True)
    mei_service.definir(outro.id, True)

    chamadas = []
    monkeypatch.setattr(svc, '_bloqueio_custo', lambda c: None)

    def falso(tipo, cnpj, pa, categoria, consolidacao):
        chamadas.append((cnpj, pa, consolidacao))
        return b'%PDF-1.4 teste', {'valores': {'total': 71.6}}

    monkeypatch.setattr(svc, '_chamar_serpro', falso)
    c = app.test_client()
    amanha = '2099-01-10'
    r = c.put('/api/mei/selecao', headers=AUTH, json={
        'company_id': mei_id, 'competencia': '202609', 'pagar': True, 'data_pagamento': amanha})
    assert r.status_code == 200, r.get_data(as_text=True)
    r = c.post('/api/mei/gerar', headers=AUTH, json={'competencia': '202609'})
    assert r.status_code == 200
    assert [x[0] for x in chamadas] == ['11222333000181']   # só a marcada
    assert chamadas[0][2] == '20990110'
    assert MeiGuiaSelecionada.query.filter_by(company_id=mei_id).first().status == 'gerada'
    # rodar de novo não paga outra vez
    c.post('/api/mei/gerar', headers=AUTH, json={'competencia': '202609'})
    assert len(chamadas) == 1


def test_selecao_recusa_nao_mei_e_data_passada(ambiente):
    app, mei_id, sn_id = ambiente
    from app.services import mei_service
    _limpar_selecoes()
    mei_service.definir(mei_id, True)
    c = app.test_client()
    r = c.put('/api/mei/selecao', headers=AUTH, json={
        'company_id': sn_id, 'competencia': '202609', 'pagar': True})
    assert r.status_code == 409
    r = c.put('/api/mei/selecao', headers=AUTH, json={
        'company_id': mei_id, 'competencia': '202609', 'pagar': True, 'data_pagamento': '2020-01-01'})
    assert r.status_code == 400


def test_pagina_mei_abre(ambiente):
    app, _, _ = ambiente
    r = app.test_client().get('/mei', headers=AUTH)
    assert r.status_code == 200 and b'MEI' in r.data
