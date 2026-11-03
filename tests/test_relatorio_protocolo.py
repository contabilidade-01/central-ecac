"""Protocolo do SITFIS: tentativa que falha não pode deixar protocolo velho guardado.

Sem isto, o primeiro erro da SERPRO congelava a empresa: toda tentativa seguinte
reaproveitava o mesmo protocolo vencido e falhava igual, para sempre.

Rodar:  python -m pytest tests/test_relatorio_protocolo.py -q
"""
import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='protocolo_teste_')
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['LIMITE_GASTO_MENSAL'] = '0'
os.environ['SCHEDULER_ENABLED'] = '0'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services import report_service as rs  # noqa: E402


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app.models import Company
        Company.query.delete()
        empresa = Company(razao_social='ALFA LTDA', cnpj='11222333000181', ativo=True)
        db.session.add(empresa)
        db.session.commit()
        yield app, empresa.id


# --------------------------------------------------------------- helpers puros

def test_protocolo_fresco_e_reaproveitado():
    agora = datetime(2026, 11, 3, 12, 0, 0)
    assert rs.protocolo_reutilizavel('ABC', agora - timedelta(minutes=2), agora)


def test_protocolo_velho_nao_e_reaproveitado():
    agora = datetime(2026, 11, 3, 12, 0, 0)
    assert not rs.protocolo_reutilizavel('ABC', agora - timedelta(hours=5), agora)


def test_protocolo_sem_data_nao_e_reaproveitado():
    assert not rs.protocolo_reutilizavel('ABC', None, datetime.now())


def test_mensagem_de_erro_500_vira_frase_em_portugues():
    texto = rs.explicar_falha_relatorio('Request failed with status code: 500')
    assert 'Receita' in texto
    assert 'status code: 500' in texto  # o original fica para o suporte


def test_mensagem_de_erro_403_fala_de_procuracao():
    assert 'procuração' in rs.explicar_falha_relatorio(
        'Request failed with status code: 403')


# ------------------------------------------------------- comportamento completo

class _SerproFalhando:
    """Devolve protocolo na hora de pedir e erro 500 na hora de capturar."""

    def __init__(self, *args, **kwargs):
        pass

    def request_protocol(self, cnpj):
        return {'success': True, 'protocol': 'PROTO-NOVO', 'wait_time': 0}

    def get_report(self, cnpj, protocol):
        return {'success': False,
                'error': 'Request failed with status code: 500'}


def _sem_rede(monkeypatch):
    monkeypatch.setattr(rs.ReportService, '_get_setting', lambda self: SimpleNamespace(
        contador_cnpj='12345678000199',
        certificado_path='/nao/existe.pfx',
        certificado_password='x',
        serpro_consumer_key='k',
        serpro_consumer_secret='s',
    ))
    monkeypatch.setattr(rs.ReportService, '_load_certificate', lambda self, p: b'pfx')
    monkeypatch.setattr(rs, 'SerproService', _SerproFalhando)


def test_falha_na_captura_limpa_o_protocolo(ambiente, monkeypatch):
    app, company_id = ambiente
    from app.models import Company
    _sem_rede(monkeypatch)

    r = rs.ReportService().process_company(company_id)

    assert r.success is False
    empresa = db.session.get(Company, company_id)
    assert empresa.current_protocol is None, 'protocolo velho ficou guardado'
    assert empresa.protocol_requested_at is None
    assert empresa.protocol_wait_until is None
    assert empresa.processing_status == 'error'
    assert 'Receita' in (empresa.processing_message or '')


def test_segunda_tentativa_pede_protocolo_novo(ambiente, monkeypatch):
    """A prova do bug: depois de falhar, a próxima tentativa não pode reusar o velho."""
    app, company_id = ambiente
    from app.models import Company
    _sem_rede(monkeypatch)

    pedidos = []

    class _Contando(_SerproFalhando):
        def request_protocol(self, cnpj):
            pedidos.append(cnpj)
            return super().request_protocol(cnpj)

    monkeypatch.setattr(rs, 'SerproService', _Contando)

    rs.ReportService().process_company(company_id)
    rs.ReportService().process_company(company_id)

    assert len(pedidos) == 2, 'a segunda tentativa reaproveitou o protocolo vencido'
    assert db.session.get(Company, company_id).current_protocol is None


def test_protocolo_vencido_no_banco_e_trocado(ambiente, monkeypatch):
    app, company_id = ambiente
    from app.models import Company
    _sem_rede(monkeypatch)

    empresa = db.session.get(Company, company_id)
    empresa.current_protocol = 'PROTO-VELHO'
    empresa.protocol_requested_at = datetime.now() - timedelta(hours=6)
    empresa.protocol_wait_until = datetime.now() - timedelta(hours=5)
    db.session.commit()

    usados = []

    class _Anotando(_SerproFalhando):
        def get_report(self, cnpj, protocol):
            usados.append(protocol)
            return super().get_report(cnpj, protocol)

    monkeypatch.setattr(rs, 'SerproService', _Anotando)

    rs.ReportService().process_company(company_id)

    assert usados == ['PROTO-NOVO'], f'usou o protocolo vencido: {usados}'
