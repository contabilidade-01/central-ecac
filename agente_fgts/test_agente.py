"""Contrato agente ⇄ Central, sem rede e sem portal.

A Central de verdade roda no cliente de teste do Flask; o portal é substituído por funções
falsas. Prova que o agente pede, executa, entrega a guia e encerra do jeito que a Central
aceita. Rodar (da raiz do repositório):  python -m pytest agente_fgts/test_agente.py -q
"""
import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / 'agente_fgts'))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='agente_fgts_')
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['INTEGRACAO_TOKEN'] = 'token-de-teste-com-mais-de-trinta-e-dois-caracteres-0001'
os.environ['FGTS_DIGITAL_HABILITADO'] = '1'
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')

import agente as modulo_agente  # noqa: E402
import pix  # noqa: E402
import portal  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from central import Central  # noqa: E402

CNPJ = '36298150000170'
INTERNO = {'X-Integracao-Token': os.environ['INTEGRACAO_TOKEN']}


def pdf_com_texto(texto: str) -> bytes:
    conteudo = f'BT /F1 12 Tf 50 750 Td ({texto}) Tj ET'.encode('latin-1')
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
            b'/Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length ' + str(len(conteudo)).encode() + b' >>\nstream\n' + conteudo + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    saida, offsets = b'%PDF-1.4\n', []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(saida))
        saida += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(saida)
    saida += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    saida += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    saida += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    return saida


class Resposta:
    def __init__(self, r):
        self.status_code, self._r = r.status_code, r

    def json(self):
        return self._r.get_json()


class SessaoFlask:
    """Imita `requests.Session` sobre o cliente de teste do Flask."""

    def __init__(self, cliente):
        self.cliente, self.headers = cliente, {}

    def post(self, url, timeout=None, json=None, files=None, data=None):
        caminho = url.split('://', 1)[-1].split('/', 1)[-1]
        if files:
            nome, conteudo, _tipo = files['pdf']
            corpo = {**(data or {}), 'pdf': (io.BytesIO(conteudo), nome)}
            r = self.cliente.post('/' + caminho, headers=self.headers, data=corpo,
                                  content_type='multipart/form-data')
        else:
            r = self.cliente.post('/' + caminho, headers=self.headers, json=json)
        return Resposta(r)


@pytest.fixture()
def cenario(monkeypatch):
    app = create_app()
    app.config['TESTING'] = True
    from app.services.fgts import agentes
    monkeypatch.setattr(agentes, 'dentro_da_janela', lambda agora=None: True)
    with app.app_context():
        from app.fgts_models import FgtsAgente, FgtsGuia
        from app.integracao_models import FgtsPedido
        from app.models import Company
        for m in (FgtsGuia, FgtsAgente, FgtsPedido, Company):
            m.query.delete()
        db.session.add(Company(razao_social='J F DA SILVA', cnpj=CNPJ, ativo=True))
        db.session.commit()
        _, token = agentes.criar('PC teste', 'teste')
    http = app.test_client()
    sessao = SessaoFlask(http)
    central = Central('http://central', token, sessao=sessao)
    ag = modulo_agente.Agente({'central_url': 'http://central', 'token': token}, central=central)
    monkeypatch.setattr(ag, 'logado', lambda: True)
    monkeypatch.setattr(ag, 'pagina', lambda: object())
    monkeypatch.setattr(modulo_agente.time, 'sleep', lambda s: None)
    return app, http, ag


def test_consulta_e_emissao_de_ponta_a_ponta(cenario, monkeypatch):
    app, http, ag = cenario
    monkeypatch.setattr(portal, 'consultar', lambda pagina, cnpj, filtros, maximo, progresso=None: [
        {'competencia': '202511', 'total': 463.86, 'situacao': 'vencida', 'grupos': [{'tipo': 'Mensal'}]}])
    pdf = pdf_com_texto('Empregador 36.298.150/0001-70 GFD 0126102368549700-5')
    monkeypatch.setattr(portal, 'emitir', lambda pagina, cnpj, comp, data: [
        {'grupo': 'Mensal', 'vencimento': '09/11/2026', 'valor': 463.86, 'identificador': '0126102368549700-5',
         'pdf': pdf}])
    monkeypatch.setattr(pix, 'do_pdf', lambda conteudo: None)

    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'consultar'}, headers=INTERNO)
    pid = r.get_json()['pedido']['id']
    assert ag.ciclo() is True
    ver = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ}', headers=INTERNO).get_json()['pedido']
    assert ver['status'] == 'concluido' and ver['competencias'][0]['competencia'] == '202511'

    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'emitir', 'competencias': ['202511']},
                  headers=INTERNO)
    pid = r.get_json()['pedido']['id']
    assert ag.ciclo() is True
    ver = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ}', headers=INTERNO).get_json()['pedido']
    assert ver['status'] == 'concluido'
    assert ver['guias'][0]['identificador'] == '0126102368549700-5'
    assert ver['guias'][0]['valor'] == 463.86


def test_sem_procuracao_vira_erro_legivel(cenario, monkeypatch):
    app, http, ag = cenario

    def sem(*a, **k):
        raise portal.SemProcuracao('Sem procuração para esta empresa.')
    monkeypatch.setattr(portal, 'consultar', sem)
    pid = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'consultar'},
                    headers=INTERNO).get_json()['pedido']['id']
    ag.ciclo()
    ver = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ}', headers=INTERNO).get_json()['pedido']
    assert ver['status'] == 'erro' and 'procuração' in ver['mensagem']


def test_guia_de_outra_empresa_nao_conclui(cenario, monkeypatch):
    app, http, ag = cenario
    monkeypatch.setattr(portal, 'consultar', lambda *a, **k: [{'competencia': '202511', 'total': 1.0}])
    monkeypatch.setattr(portal, 'emitir', lambda *a, **k: [
        {'grupo': 'Mensal', 'vencimento': '09/11/2026', 'valor': 1.0, 'identificador': '0126102368549700-5',
         'pdf': pdf_com_texto('Empregador 11.222.333/0001-81')}])
    monkeypatch.setattr(pix, 'do_pdf', lambda conteudo: None)
    http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'consultar'}, headers=INTERNO)
    ag.ciclo()
    pid = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'emitir', 'competencias': ['202511']},
                    headers=INTERNO).get_json()['pedido']['id']
    ag.ciclo()
    ver = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ}', headers=INTERNO).get_json()['pedido']
    assert ver['status'] == 'erro' and not ver['guias']


def test_pix_crc():
    base = '00020101021226830014br.gov.bcb.pix2561qrpix.caixa.gov.br/fgts/teste5204000053039865802BR5913FGTS6008BRASILIA62070503***6304'
    assert pix.valido(base + pix.crc16_ccitt(base))
    assert not pix.valido(base + '0000')
