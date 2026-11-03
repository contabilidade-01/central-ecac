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


# ------------------------------------------------ 5xx na captura (SerproService)

class _CurlFalso:
    """pycurl.Curl de mentira: devolve os status da fila, um por perform()."""

    URL = WRITEDATA = POSTFIELDS = HTTPHEADER = SSLVERSION = TIMEOUT = 0

    def __init__(self, fila):
        self.fila = fila
        self.buf = None

    def setopt(self, opt, val):
        if hasattr(val, 'write'):
            self.buf = val

    def perform(self):
        status, corpo = self.fila[0]
        self.atual = self.fila.pop(0)
        self.buf.write(corpo.encode())

    def getinfo(self, _):
        return self.atual[0]

    def close(self):
        pass


def _serpro_com_fila(monkeypatch, fila):
    import json
    from app.services import serpro_service as ss
    curl = _CurlFalso(fila)
    monkeypatch.setattr(ss.pycurl, 'Curl', lambda: curl)
    monkeypatch.setattr(ss.time, 'sleep', lambda s: None)
    svc = ss.SerproService.__new__(ss.SerproService)
    svc.contratante_cnpj = svc.contador_cnpj = '12345678000199'
    monkeypatch.setattr(svc, '_procurador_setting', lambda: None)
    monkeypatch.setattr(svc, '_get_auth_token',
                        lambda: {'jwt_token': 'j', 'access_token': 'a'})
    return svc, json


def test_500_momentaneo_na_captura_e_repetido(monkeypatch):
    import json
    pdf_ok = json.dumps({'dados': json.dumps([{'pdf': 'UERG'}])})
    erro = json.dumps({'mensagens': [{'codigo': 'Erro-SITFIS-500', 'texto': 'Instável'}]})
    svc, _ = _serpro_com_fila(monkeypatch, [(500, erro), (200, pdf_ok)])
    r = svc.get_report('11222333000181', 'P1')
    assert r == {'success': True, 'pdf_base64': 'UERG'}


def test_500_persistente_traz_o_texto_da_serpro(monkeypatch):
    import json
    erro = json.dumps({'mensagens': [{'codigo': 'Erro-SITFIS-500',
                                      'texto': 'Falha ao gerar em 03/11/2026'}]})
    svc, _ = _serpro_com_fila(monkeypatch, [(500, erro)] * 6)
    r = svc.get_report('11222333000181', 'P1')
    assert r['success'] is False
    assert 'status code: 500' in r['error']
    assert 'Falha ao gerar' in r['error']
    texto = rs.explicar_falha_relatorio(r['error'])
    assert 'Receita' in texto


def test_texto_com_2026_nao_vira_relatorio_em_processamento(ambiente, monkeypatch):
    app, company_id = ambiente
    from app.models import Company
    _sem_rede(monkeypatch)

    class _ComData(_SerproFalhando):
        def get_report(self, cnpj, protocol):
            return {'success': False, 'error':
                    'Request failed with status code: 500 - Erro em 03/11/2026'}

    monkeypatch.setattr(rs, 'SerproService', _ComData)
    rs.ReportService().process_company(company_id)
    assert db.session.get(Company, company_id).processing_status == 'error'


def test_404_com_texto_ainda_e_reconhecido():
    assert 'não encontrou' in rs.explicar_falha_relatorio(
        'Request failed with status code: 404 - qualquer coisa')


# ------------------------------------- ER05: a SERPRO manda pedir protocolo novo

ER05 = ('Request failed with status code: 500 - [Erro-Sitfis-ER05] Erro ao processar a '
        'requisição. Inicie uma nova solicitação /apoiar.')


def test_er05_pede_protocolo_novo_e_captura(ambiente, monkeypatch):
    app, company_id = ambiente
    from app.models import Company
    _sem_rede(monkeypatch)
    protocolos, capturas = [], []

    class _Er05UmaVez(_SerproFalhando):
        def request_protocol(self, cnpj):
            protocolos.append(f'P{len(protocolos) + 1}')
            return {'success': True, 'protocol': protocolos[-1], 'wait_time': 0}

        def get_report(self, cnpj, protocol):
            capturas.append(protocol)
            if protocol == 'P1':
                return {'success': False, 'error': ER05}
            return {'success': False, 'error': 'Request failed with status code: 404'}

    monkeypatch.setattr(rs, 'SerproService', _Er05UmaVez)
    rs.ReportService().process_company(company_id)
    assert capturas == ['P1', 'P2'], 'não pediu protocolo novo depois do ER05'
    assert 'não encontrou' in db.session.get(Company, company_id).processing_message


def test_er05_persistente_para_depois_das_rodadas(ambiente, monkeypatch):
    app, company_id = ambiente
    from app.models import Company
    _sem_rede(monkeypatch)
    pedidos = []

    class _SempreEr05(_SerproFalhando):
        def request_protocol(self, cnpj):
            pedidos.append(1)
            return {'success': True, 'protocol': f'P{len(pedidos)}', 'wait_time': 0}

        def get_report(self, cnpj, protocol):
            return {'success': False, 'error': ER05}

    monkeypatch.setattr(rs, 'SerproService', _SempreEr05)
    r = rs.ReportService().process_company(company_id)
    assert r.success is False and len(pedidos) == rs.RODADAS_PROTOCOLO
    empresa = db.session.get(Company, company_id)
    assert empresa.processing_status == 'error' and empresa.current_protocol is None


def test_get_report_nao_repete_captura_no_er05(monkeypatch):
    import json
    erro = json.dumps({'mensagens': [{'codigo': '[Erro-Sitfis-ER05]',
                                      'texto': 'Inicie uma nova solicitação /apoiar.'}]})
    fila = [(500, erro)] * 6
    svc, _ = _serpro_com_fila(monkeypatch, fila)
    r = svc.get_report('11222333000181', 'P1')
    assert r['success'] is False and 'ER05' in r['error']
    assert len(fila) == 5, 'repetiu a captura com protocolo morto'


def test_mesmo_protocolo_de_volta_nao_gasta_as_rodadas(ambiente, monkeypatch):
    """A Receita pode devolver o MESMO protocolo no /Apoiar (resposta 304, ETag).

    Insistir nele depois do ER05 só repete o erro e queima as três rodadas.
    """
    app, company_id = ambiente
    from app.models import Company
    _sem_rede(monkeypatch)
    capturas = []

    class _SempreOMesmoProtocolo(_SerproFalhando):
        def request_protocol(self, cnpj):
            return {'success': True, 'protocol': 'P-UNICO', 'wait_time': 0}

        def get_report(self, cnpj, protocol):
            capturas.append(protocol)
            return {'success': False, 'error': ER05}

    monkeypatch.setattr(rs, 'SerproService', _SempreOMesmoProtocolo)
    r = rs.ReportService().process_company(company_id)

    assert r.success is False
    assert capturas == ['P-UNICO'], f'insistiu no mesmo protocolo: {capturas}'
    empresa = db.session.get(Company, company_id)
    assert empresa.current_protocol is None
    assert 'mesmo protocolo' in (empresa.processing_message or '')


def test_mensagem_do_protocolo_repetido_nao_mostra_o_marcador():
    texto = rs.explicar_falha_relatorio(rs.PROTOCOLO_REPETIDO)
    assert rs.PROTOCOLO_REPETIDO not in texto
    assert 'alguns minutos' in texto
