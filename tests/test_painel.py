"""Desvio 28 — API do novo frontend: mesa do dia, ficha da empresa, saúde fiscal e busca.

Sem rede e sem custo. Rodar:  python -m pytest tests/test_painel.py -q
"""
import base64
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='painel_teste_')
os.environ['AUTH_USER'] = 'teste'
os.environ['AUTH_PASSWORD'] = 'teste'
os.environ['SCHEDULER_ENABLED'] = '0'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

BASIC = {'Authorization': 'Basic ' + base64.b64encode(b'teste:teste').decode()}


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app.models import CaixaPostalMensagem, Company, DebitoRelatorio, PendenciaRelatorio, RelatorioSitFiscal
        for m in (CaixaPostalMensagem, DebitoRelatorio, PendenciaRelatorio, RelatorioSitFiscal, Company):
            m.query.delete()
        a = Company(razao_social='ALFA COMERCIO', cnpj='11222333000181', ativo=True)
        b = Company(razao_social='BETA SERVICOS', cnpj='11444777000161', ativo=True)
        db.session.add_all([a, b])
        db.session.commit()
        hoje = date.today()
        rel = RelatorioSitFiscal(company_id=a.id, data_hora=datetime.now(), situacao='ATIVA')
        db.session.add(rel)
        db.session.flush()
        db.session.add_all([
            DebitoRelatorio(relatorio_id=rel.id, tipo='SN', receita='SIMPLES NAC.', periodo_apuracao='07/2026',
                            data_vencimento=hoje - timedelta(days=40), valor_original=Decimal('500'),
                            saldo_devedor=Decimal('500'), saldo_devedor_total=Decimal('515'), situacao='DEVEDOR'),
            DebitoRelatorio(relatorio_id=rel.id, tipo='INSS', receita='1082', periodo_apuracao='08/2026',
                            data_vencimento=hoje + timedelta(days=2), valor_original=Decimal('100'),
                            saldo_devedor=Decimal('100'), saldo_devedor_total=Decimal('100'),
                            situacao='A ANALISAR-A VENCER'),
            DebitoRelatorio(relatorio_id=rel.id, tipo='INSS', receita='1099', periodo_apuracao='08/2026',
                            data_vencimento=hoje + timedelta(days=2), valor_original=Decimal('50'),
                            saldo_devedor=Decimal('50'), saldo_devedor_total=Decimal('50'),
                            situacao='A ANALISAR-A VENCER'),
        ])
        db.session.add(CaixaPostalMensagem(company_id=a.id, isn='1', assunto='Intimação', data_envio=hoje,
                                           visualizada_usuario=False))
        db.session.commit()
        yield app, app.test_client(), {'A': a.id, 'B': b.id}


def test_mesa_ordena_por_prioridade_e_soma_inss(ambiente):
    _app, http, ids = ambiente
    r = http.get('/api/painel/mesa', headers=BASIC)
    assert r.status_code == 200
    d = r.get_json()
    tipos = [i['tipo'] for i in d['fila']]
    assert tipos[0] == 'vencido' and d['fila'][0]['valor'] == 515.0
    vence = next(i for i in d['fila'] if i['tipo'] == 'vence_logo')
    assert vence['valor'] == 150.0                  # 1082 + 1099 na mesma competência
    assert 'mensagem' in tipos
    assert any(i['tipo'] == 'sem_dado' and i['company_id'] == ids['B'] for i in d['fila'])
    assert d['kpi']['em_atraso_empresas'] == 1 and d['kpi']['mensagens_nao_lidas'] == 1


def test_ficha_traz_saude_com_motivos(ambiente):
    _app, http, ids = ambiente
    d = http.get(f"/api/painel/empresas/{ids['A']}", headers=BASIC).get_json()
    assert d['empresa']['razao_social'] == 'ALFA COMERCIO'
    assert d['saude']['nota'] < 100
    motivos = ' '.join(i['motivo'] for i in d['saude']['itens'])
    assert 'DAS Simples' in motivos and 'Caixa Postal' in motivos
    assert d['caixa_postal']['nao_lidas'] == 1
    assert d['linha_do_tempo'] and d['linha_do_tempo'][0]['tipo'] in ('relatorio', 'mensagem')


def test_saude_sem_problemas_e_100():
    from app.services.painel_service import saude
    hoje = date.today()
    s = saude({'debitos': [], 'omissoes': [], 'relatorio': {'data_hora': hoje.isoformat()}},
              {'situacao': 'ok'}, 0, {'aplicavel': False}, hoje)
    assert s == {'nota': 100, 'faixa': 'boa', 'itens': []}


def test_busca_por_nome_e_cnpj(ambiente):
    _app, http, _ = ambiente
    assert [e['razao_social'] for e in http.get('/api/painel/busca?q=beta', headers=BASIC).get_json()['empresas']] == [
        'BETA SERVICOS']
    assert len(http.get('/api/painel/busca?q=11222', headers=BASIC).get_json()['empresas']) == 1
    assert http.get('/api/painel/busca?q=a', headers=BASIC).get_json()['empresas'] == []


def test_sem_login_nao_abre(ambiente):
    _app, http, _ = ambiente
    assert http.get('/api/painel/mesa').status_code == 401


def test_raiz_abre_a_interface_nova(ambiente):
    _app, http, _ = ambiente
    r = http.get('/', headers=BASIC)
    assert r.status_code == 302 and r.headers['Location'].endswith('/app')
    assert http.get('/?antiga=1', headers=BASIC).status_code == 200
    assert http.get('/?aba=das', headers=BASIC).status_code == 200


def test_empresas_ordena_pela_pior_nota(ambiente):
    _app, http, ids = ambiente
    d = http.get('/api/painel/empresas', headers=BASIC).get_json()
    nomes = [e['razao_social'] for e in d['empresas']]
    assert nomes[0] == 'ALFA COMERCIO'           # vencido + mensagem: nota menor
    alfa = d['empresas'][0]
    assert alfa['vencidos'] == 1 and alfa['total_vencido'] == 515.0 and alfa['nao_lidas'] == 1
    assert sum(d['faixas'].values()) == 2 and 'atraso_por_tributo' in d['pesos']


def test_status_conta_menu(ambiente):
    _app, http, _ = ambiente
    d = http.get('/api/painel/status', headers=BASIC).get_json()
    assert d['caixa_postal'] == 1 and d['mesa'] >= 3


def test_ficha_traz_link_do_portal(ambiente, monkeypatch):
    _app, http, ids = ambiente
    monkeypatch.setenv('NESCON_PORTAL_URL', 'https://portal.exemplo/')
    d = http.get(f"/api/painel/empresas/{ids['A']}", headers=BASIC).get_json()
    assert d['portal_url'] == 'https://portal.exemplo/admin/empresas?busca=11222333000181'


def test_consultar_selecionadas_roda_uma_de_cada_vez(ambiente, monkeypatch):
    import time
    _app, http, ids = ambiente
    chamadas = []
    from app.services import report_service
    monkeypatch.setattr(report_service.ReportService, 'process_company', lambda self, cid: chamadas.append(cid))
    r = http.post('/api/painel/consultar', json={'company_ids': [ids['B'], 999999]}, headers=BASIC)
    assert r.status_code == 200 and r.get_json()['quantidade'] == 1     # 999999 não existe: ignorada
    for _ in range(50):
        if chamadas:
            break
        time.sleep(0.05)
    assert chamadas == [ids['B']]
    assert http.post('/api/painel/consultar', json={'company_ids': []}, headers=BASIC).status_code == 400
    d = http.get('/api/painel/consultas', headers=BASIC).get_json()
    assert d['na_fila'] == 0
