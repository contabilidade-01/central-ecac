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
        for modelo in (PendenciaRelatorio, RelatorioSitFiscal, Company):
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
