"""Sugestão de parcelamento a partir do último relatório (tela Parcelamentos) — SEM rede.

Rodar:  python -m pytest tests/test_parcelamentos_sugestoes.py -q
"""
import os
import tempfile
from datetime import date, datetime, timedelta

import pytest

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='parc_sug_teste_')
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['SCHEDULER_ENABLED'] = '0'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Company, PendenciaRelatorio, RelatorioSitFiscal  # noqa: E402
from app.services.pendencias_service import sugestoes_parcelamento  # noqa: E402


def _relatorio(company, quando, itens=None, simei=False):
    rel = RelatorioSitFiscal(company_id=company.id, data_hora=quando, situacao='ATIVA',
                             simei_inclusao=date(2020, 1, 1) if simei else None)
    db.session.add(rel)
    db.session.flush()
    if itens:
        db.session.add(PendenciaRelatorio(relatorio_id=rel.id, tipo='PARCELAMENTO',
                                          ano='2026', meses_json=itens))
    return rel


@pytest.fixture()
def app():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app.models import ParcelamentoParcela, ParcelamentoPedido
        for modelo in (ParcelamentoParcela, ParcelamentoPedido, PendenciaRelatorio,
                       RelatorioSitFiscal, Company):
            modelo.query.delete()
        db.session.commit()
        yield app


def test_tipo_sugerido_pelo_ultimo_relatorio(app):
    sn = Company(razao_social='SN', cnpj='11222333000181', ativo=True)
    mei = Company(razao_social='MEI', cnpj='11444777000161', ativo=True)
    relp = Company(razao_social='RELP', cnpj='22333444000155', ativo=True)
    outro = Company(razao_social='OUTRO', cnpj='33444555000166', ativo=True)
    db.session.add_all([sn, mei, relp, outro])
    db.session.flush()
    agora = datetime.now()
    _relatorio(sn, agora, ['SIMPLES NACIONAL - EM PARCELAMENTO', '2 parcela(s) em atraso'])
    _relatorio(mei, agora, ['SIMPLES NACIONAL - EM PARCELAMENTO'], simei=True)
    _relatorio(relp, agora, ['RELP SIMPLES NACIONAL - EM PARCELAMENTO'])
    _relatorio(outro, agora, ['Parcelamento 12345', 'Valor suspenso: R$ 1.000,00'])
    db.session.commit()

    sug = sugestoes_parcelamento()
    assert sug[sn.id]['tipos'] == ['PARC_SN']
    assert '2 parcela(s) em atraso' in sug[sn.id]['texto']
    assert sug[mei.id]['tipos'] == ['PARC_MEI']
    assert sug[relp.id]['tipos'] == ['RELP_SN']
    # parcelamento fora do Simples: aparece o texto, mas sem tipo para marcar
    assert sug[outro.id]['tipos'] == []
    assert 'Parcelamento 12345' in sug[outro.id]['texto']


def test_sugestao_some_quando_sai_do_relatorio(app):
    c = Company(razao_social='ALFA', cnpj='11222333000181', ativo=True)
    db.session.add(c)
    db.session.flush()
    _relatorio(c, datetime.now() - timedelta(days=30), ['SIMPLES NACIONAL - EM PARCELAMENTO'])
    db.session.commit()
    assert c.id in sugestoes_parcelamento()

    _relatorio(c, datetime.now())  # relatório novo sem parcelamento
    db.session.commit()
    assert c.id not in sugestoes_parcelamento()


def test_rota_sugestoes(app):
    c = Company(razao_social='ALFA', cnpj='11222333000181', ativo=True)
    db.session.add(c)
    db.session.flush()
    _relatorio(c, datetime.now(), ['SIMPLES NACIONAL - EM PARCELAMENTO'])
    db.session.commit()
    cliente = app.test_client()
    resp = cliente.get('/api/parcelamentos/sugestoes', headers={
        'Authorization': 'Basic dGVzdGU6dGVzdGU='})
    assert resp.status_code == 200, resp.data[:200]
    assert resp.get_json()[str(c.id)]['tipos'] == ['PARC_SN']


# --- andamento da busca de pedidos/parcelas (Acompanhamento) ---------------------

def test_busca_pedidos_publica_andamento_e_erro(app, monkeypatch):
    from app.services.parcelamentos_serpro_service import ParcelamentosSerproService
    c = Company(razao_social='ELIUDE', cnpj='11222333000181', ativo=True,
                consultar_parc_sn=True, consultar_parc_mei=True)
    db.session.add(c)
    db.session.commit()
    vistos = []

    def falso(self, company, tipo):
        vistos.append((tipo, db.session.get(Company, company.id).processing_status))
        if tipo == 'PARC_MEI':
            return {'erro': 'Contribuinte não é MEI'}
        return {'success': True, 'pedidos': [], 'message': '0 pedido(s), 0 ativo(s).'}

    monkeypatch.setattr(ParcelamentosSerproService, 'buscar_pedidos_tipo', falso)
    ParcelamentosSerproService().buscar_pedidos_empresa(c.id)

    assert vistos == [('PARC_SN', 'processing'), ('PARC_MEI', 'processing')]
    c = db.session.get(Company, c.id)
    assert c.processing_status == 'done'
    assert c.processing_progress == 100
    assert 'Parcelamento Simples Nacional: 0 pedido(s)' in c.processing_message
    assert 'Parcelamento MEI: erro — Contribuinte não é MEI' in c.processing_message


def test_busca_sem_tipo_marcado_avisa(app):
    from app.services.parcelamentos_serpro_service import ParcelamentosSerproService
    c = Company(razao_social='SEM TIPO', cnpj='11222333000181', ativo=True)
    db.session.add(c)
    db.session.commit()
    ParcelamentosSerproService().buscar_pedidos_empresa(c.id)
    assert 'Nenhum tipo de parcelamento marcado' in db.session.get(Company, c.id).processing_message


def test_rota_marca_processando_antes_da_thread(app, monkeypatch):
    import app.routes.parcelamentos as rotas
    c = Company(razao_social='ALFA', cnpj='11222333000181', ativo=True, consultar_parc_sn=True)
    db.session.add(c)
    db.session.commit()
    monkeypatch.setattr(rotas.threading, 'Thread', lambda *a, **k: type('T', (), {'start': lambda s: None})())
    auth = {'Authorization': 'Basic dGVzdGU6dGVzdGU='}
    cli = app.test_client()
    assert cli.post(f'/api/parcelamentos/buscar-pedidos-empresa/{c.id}', headers=auth).status_code == 200
    assert cli.get('/api/parcelamentos/status', headers=auth).get_json()['has_processing'] is True


def test_pedidos_filtra_empresa_e_tipo(app):
    from app.models import ParcelamentoPedido
    a = Company(razao_social='A', cnpj='11222333000181', ativo=True)
    b = Company(razao_social='B', cnpj='11444777000161', ativo=True)
    db.session.add_all([a, b])
    db.session.flush()
    db.session.add_all([
        ParcelamentoPedido(company_id=a.id, tipo='PARC_SN', numero='1', ativo=True),
        ParcelamentoPedido(company_id=b.id, tipo='PARC_MEI', numero='2', ativo=True),
    ])
    db.session.commit()
    auth = {'Authorization': 'Basic dGVzdGU6dGVzdGU='}
    cli = app.test_client()
    assert [p['numero'] for p in cli.get(f'/api/parcelamentos/pedidos?company_id={b.id}', headers=auth).get_json()] == ['2']
    assert [p['numero'] for p in cli.get('/api/parcelamentos/pedidos?tipo=PARC_SN', headers=auth).get_json()] == ['1']
