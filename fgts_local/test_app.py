"""Teste da camada do app local (Flask + Executor) com uma sessão simulada — sem Chrome.
A integração real com o portal via CDP está em test_local.py.

Rodar:  cd fgts_local && python -m pytest test_app.py -q
"""

from __future__ import annotations

import time

import pytest

import app_local
import conexao
from fgts_portal import FalhaFgts
from test_local import GFD_A

LOGADO = [True]
COMPETENCIAS = [{'competencia': '202602', 'rotulo': '02/2026', 'total': 359.93,
                 'grupos': [{'grupo': 'Mensal', 'vencimento': '23/10/2026', 'valor': 359.93}],
                 'composicao': {'fgts_mensal': 312.0, 'fgts_rescisorio': 0.0, 'indenizacao': 0.0,
                                'processo_trabalhista': 0.0, 'encargos': 47.93, 'total': 359.93}}]


class FakeSess:
    def __init__(self, porta, diag=None):
        self.pdfs = {}

    def conectar(self):
        pass

    def exigir_login(self):
        if not LOGADO[0]:
            raise conexao.SemLogin('Entre no FGTS Digital no Chrome aberto.')

    def definir_perfil(self, cnpj):
        if cnpj == '11444777000161':
            raise FalhaFgts('O escritório não tem procuração no FGTS Digital para esta empresa.')

    def consultar(self, cnpj):
        return [dict(c) for c in COMPETENCIAS]

    def datas_permitidas(self, cnpj, aaaamm):
        return ['2026-11-20']

    def emitir(self, cnpj, aaaamm, data):
        nome = f'{aaaamm}_x.pdf'
        self.pdfs[nome] = GFD_A
        return [{'grupo': 'FGTS', 'vencimento': ('20/11/2026' if data else None),
                 'arquivo': nome, 'identificador': '0126102368549700-5', 'valor': 359.93}]

    def fechar(self):
        pass


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    LOGADO[0] = True
    monkeypatch.setattr(app_local.conexao, 'AutomacaoLocal', FakeSess)
    monkeypatch.setattr(app_local.conexao, 'diagnostico_chrome',
                        lambda porta: {'chrome': True, 'logado': LOGADO[0], 'abas_fgts': 1, 'versao': 'x'})


@pytest.fixture
def cli():
    return app_local.app.test_client()


def _esperar(cli, status, segundos=8):
    limite = time.time() + segundos
    while time.time() < limite:
        s = cli.get('/api/estado').get_json()
        if s['status'] == status:
            return s
        time.sleep(0.1)
    raise AssertionError(f'status ficou em {cli.get("/api/estado").get_json()["status"]}, esperava {status}')


def test_cnpj_invalido():
    cli = app_local.app.test_client()
    r = cli.post('/api/consultar', json={'cnpj': '123'})
    assert r.status_code == 400


def test_fluxo_consulta_e_emissao(cli):
    assert cli.post('/api/consultar', json={'cnpj': '36.298.150/0001-70'}).status_code == 200
    s = _esperar(cli, 'aguardando_escolha')
    assert s['competencias'][0]['total'] == 359.93
    assert s['datas_permitidas'] == ['2026-11-20']
    # mês fora da lista é recusado
    assert cli.post('/api/emitir', json={'cnpj': '36298150000170', 'competencias': ['209912']}).status_code == 400
    # data fora da lista é recusada
    assert cli.post('/api/emitir', json={'cnpj': '36298150000170', 'competencias': ['202602'],
                                         'data_pagamento': '2026-12-31'}).status_code == 400
    assert cli.post('/api/emitir', json={'cnpj': '36298150000170', 'competencias': ['202602']}).status_code == 200
    s = _esperar(cli, 'concluido')
    assert len(s['guias']) == 1 and s['guias'][0]['valor'] == 359.93
    arquivo = s['guias'][0]['arquivo']
    r = cli.get(f'/guias/{arquivo}')
    assert r.status_code == 200 and r.data.startswith(b'%PDF')


def test_emissao_parametrizada_passa_data(cli):
    cli.post('/api/consultar', json={'cnpj': '36298150000170'})
    _esperar(cli, 'aguardando_escolha')
    cli.post('/api/emitir', json={'cnpj': '36298150000170', 'competencias': ['202602'],
                                  'data_pagamento': '2026-11-20'})
    s = _esperar(cli, 'concluido')
    assert s['guias'][0]['vencimento'] == '20/11/2026'


def test_sem_login_vira_erro(cli):
    LOGADO[0] = False
    cli.post('/api/consultar', json={'cnpj': '36298150000170'})
    s = _esperar(cli, 'erro')
    assert 'FGTS Digital' in s['mensagem']


def test_sem_procuracao_vira_erro(cli):
    cli.post('/api/consultar', json={'cnpj': '11444777000161'})
    s = _esperar(cli, 'erro')
    assert 'procuração' in s['mensagem']


def test_pdf_fora_da_pasta_404(cli):
    assert cli.get('/guias/..%2f..%2fetc%2fpasswd').status_code == 404
    assert cli.get('/guias/nao-existe.pdf').status_code == 404
