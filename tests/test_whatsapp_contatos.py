"""WhatsApp (uazapi) e tela de contatos — 19o desvio — SEM rede.

A uazapi é simulada por um transporte injetado (`uazapi_service.usar_transporte`).
Cobre o que foi portado do portal e tem de se comportar IGUAL:
* validação/normalização de número (celular, fixo, DDD, 55);
* trava "só cliente cadastrado" (chave DDD+8 dígitos, números do escritório, cache);
* janela diurna e dia útil; teto por hora; backoff;
* envio com registro em `whatsapp_envios` (enviado / bloqueado / falhou) e retentativa;
* API de contatos: salvar, importar por CNPJ, verificar (formato + uazapi), teste.

Rodar:  python -m pytest tests/test_whatsapp_contatos.py -q
"""
import json
import os
import tempfile
from datetime import date, datetime

import pytest

_TMP = tempfile.mkdtemp(prefix='wpp_teste_')
os.environ['DATA_DIR'] = _TMP
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['UAZAPI_SUBDOMAIN'] = 'teste'
os.environ['UAZAPI_TOKEN'] = 'token-teste'
os.environ['ADMIN_WHATSAPP'] = '11948626605'
os.environ['ALERTAS_THROTTLE_S'] = '0'
os.environ['ALERTAS_DELAY_MS'] = '0'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services import uazapi_service as uazapi  # noqa: E402
from app.services import whatsapp_numero as num  # noqa: E402

CNPJ_A = '11222333000181'
CNPJ_B = '11444777000161'


class Resp:
    def __init__(self, status, corpo):
        self.status_code = status
        self.text = json.dumps(corpo)

    def json(self):
        return json.loads(self.text)


class UazapiFalsa:
    """Registra o que saiu e responde conforme o caminho."""

    def __init__(self):
        self.chamadas = []
        self.status = 'connected'
        self.falhar_envios = 0
        self.existentes = set()

    def __call__(self, metodo, url, headers, corpo, timeout):
        caminho = url.split('.uazapi.com', 1)[1]
        self.chamadas.append({'metodo': metodo, 'caminho': caminho, 'corpo': corpo, 'token': headers.get('token')})
        if caminho == '/instance/status':
            return Resp(200, {'instance': {'status': self.status, 'owner': '5511900000000', 'profileName': 'Nescon'}})
        if caminho in ('/send/text', '/send/media'):
            if self.falhar_envios > 0:
                self.falhar_envios -= 1
                return Resp(500, {'error': 'instável'})
            return Resp(200, {'id': f"msg-{len(self.chamadas)}"})
        if caminho == '/chat/check':
            return Resp(200, {'users': [{'query': n, 'isInWhatsapp': n in self.existentes, 'jid': f'{n}@s.whatsapp.net' if n in self.existentes else ''}
                                        for n in corpo['numbers']]})
        if caminho == '/webhook':
            return Resp(200, {'url': 'https://app.exemplo.com/api/whatsapp/webhook?token=abc'})
        return Resp(404, {})


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    falsa = UazapiFalsa()
    uazapi.usar_transporte(falsa)
    uazapi.limpar_cache_destinos()
    uazapi._carimbos.clear()
    uazapi._owner_cache.update({'valor': None, 'ts': 0.0})
    with app.app_context():
        from app.integracao_models import ContatoEmpresa, WhatsappEnvio
        from app.models import Company
        for m in (WhatsappEnvio, ContatoEmpresa, Company):
            m.query.delete()
        a = Company(razao_social='ALFA', cnpj=CNPJ_A, ativo=True)
        b = Company(razao_social='BETA', cnpj=CNPJ_B, ativo=True)
        db.session.add_all([a, b])
        db.session.commit()
        http = app.test_client()
        with http.session_transaction() as sessao:
            sessao['usuario_id'] = 'ambiente'
        yield app, http, falsa, {'A': a.id, 'B': b.id}
    uazapi.usar_transporte(None)


# ------------------------------------------------------------------ número
def test_numero_validacao_igual_ao_portal():
    assert num.validar('34 99999-8888') == {'ok': True, 'numero': '5534999998888', 'motivo': ''}
    assert num.validar('5534999998888')['ok']
    assert num.validar('(11) 94862-6605')['numero'] == '5511948626605'
    fixo = num.validar('34 3333-4444')
    assert not fixo['ok'] and 'fixo' in fixo['motivo']
    assert not num.validar('')['ok']
    assert not num.validar('01 99999-8888')['ok']       # DDD inexistente
    assert 'recebido' in num.validar('123')['motivo']
    assert num.formatar('5534999998888') == '(34) 99999-8888'
    assert num.chave_numero('5534999998888') == num.chave_numero('34 9999-8888') == '3499998888'
    assert num.chave_numero('123') == ''


def test_classificar_destino_e_backoff():
    clientes = {num.chave_numero('34999998888')}
    escritorio = {num.chave_numero('11948626605')}
    assert uazapi.classificar_destino('5534999998888', clientes, escritorio) == {'ok': True, 'tipo': 'cliente'}
    assert uazapi.classificar_destino('11 94862-6605', clientes, escritorio)['tipo'] == 'escritorio'
    r = uazapi.classificar_destino('5521988887777', clientes, escritorio)
    assert not r['ok'] and 'não pertence' in r['motivo']
    assert not uazapi.classificar_destino('abc', clientes, escritorio)['ok']
    assert uazapi.calcular_backoff(1) == {'esgotou': False, 'proxima_min': 2}
    assert uazapi.calcular_backoff(4) == {'esgotou': False, 'proxima_min': 16}
    assert uazapi.calcular_backoff(5)['esgotou']


def test_janela_diurna_e_dia_util():
    from app.services import janela_envio as j
    assert not j.dentro_da_janela(7 * 60 + 59)
    assert j.dentro_da_janela(8 * 60)
    assert j.dentro_da_janela(18 * 60 + 59)
    assert not j.dentro_da_janela(19 * 60)
    assert j.descricao_janela() == '08:00–19:00'
    assert j.eh_dia_util(date(2026, 9, 30))
    assert not j.eh_dia_util(date(2026, 10, 3))   # sábado
    assert not j.eh_dia_util(date(2026, 10, 12))  # feriado
    sp = j.TZ
    assert j.pode_enviar_agora(datetime(2026, 9, 30, 10, 0, tzinfo=sp))
    assert not j.pode_enviar_agora(datetime(2026, 9, 30, 3, 0, tzinfo=sp))
    assert not j.pode_enviar_agora(datetime(2026, 10, 3, 10, 0, tzinfo=sp))


# ------------------------------------------------------------------- envio
def test_envio_respeita_trava_registra_e_retenta(ambiente):
    app, http, falsa, ids = ambiente
    from app.integracao_models import WhatsappEnvio
    with app.app_context():
        # sem contato cadastrado → bloqueado, nada sai
        r = uazapi.enviar('34 99999-8888', 'oi', company_id=ids['A'], contexto='teste', respeitar_janela=False)
        assert not r['ok'] and 'não pertence' in r['motivo']
        assert not [c for c in falsa.chamadas if c['caminho'].startswith('/send')]
        assert WhatsappEnvio.query.filter_by(status='bloqueado').count() == 1

        # cadastrando o contato, passa (com retentativa em falha transitória)
        http.post(f"/api/contatos/{ids['A']}", json={'whatsapp': '34 99999-8888'})
        falsa.falhar_envios = 1
        r = uazapi.enviar('34 99999-8888', 'oi', company_id=ids['A'], contexto='teste', respeitar_janela=False)
        assert r['ok'] and r['numero'] == '5534999998888' and r['mensagem_id']
        envios = [c for c in falsa.chamadas if c['caminho'] == '/send/text']
        assert len(envios) == 2 and envios[-1]['corpo'] == {'number': '5534999998888', 'text': 'oi'}
        assert envios[-1]['token'] == 'token-teste'
        ultimo = WhatsappEnvio.query.order_by(WhatsappEnvio.id.desc()).first()
        assert ultimo.status == 'enviado' and ultimo.tentativas == 2

        # número do escritório passa pela trava sem cadastro de empresa
        assert uazapi.enviar('11 94862-6605', 'interno', contexto='teste', respeitar_janela=False)['ok']

        # próprio número da instância → bloqueado
        assert not uazapi.enviar('11 90000-0000', 'x', contexto='teste', respeitar_janela=False)['ok']

        # documento em bytes vira data URI
        r = uazapi.enviar('34 99999-8888', 'segue a guia', company_id=ids['A'], contexto='das_mei',
                          nome_arquivo='DAS.pdf', conteudo=b'%PDF-1.4 x', respeitar_janela=False)
        assert r['ok']
        media = [c for c in falsa.chamadas if c['caminho'] == '/send/media'][-1]['corpo']
        assert media['type'] == 'document' and media['docName'] == 'DAS.pdf'
        assert media['file'].startswith('data:application/pdf;base64,') and media['text'] == 'segue a guia'


def test_teto_por_hora_e_token_invalido(ambiente, monkeypatch):
    app, http, falsa, ids = ambiente
    with app.app_context():
        http.post(f"/api/contatos/{ids['A']}", json={'whatsapp': '34 99999-8888'})
        monkeypatch.setenv('ALERTAS_MAX_POR_HORA', '1')
        assert uazapi.enviar('34 99999-8888', 'a', respeitar_janela=False)['ok']
        r = uazapi.enviar('34 99999-8888', 'b', respeitar_janela=False)
        assert not r['ok'] and 'teto' in r['motivo']
        monkeypatch.setenv('ALERTAS_MAX_POR_HORA', '180')

        falsa.status = 'disconnected'
        uazapi._owner_cache.update({'valor': None, 'ts': 0.0})
        assert uazapi.status_instancia()['categoria'] == 'desconectado'

        def token_ruim(metodo, url, headers, corpo, timeout):
            return Resp(401, {})
        uazapi.usar_transporte(token_ruim)
        uazapi._owner_cache.update({'valor': None, 'ts': 0.0})
        r = uazapi.enviar('34 99999-8888', 'c', respeitar_janela=False)
        assert not r['ok'] and 'Token uazapi' in r['motivo']


def test_janela_bloqueia_fora_do_horario(ambiente, monkeypatch):
    app, http, falsa, ids = ambiente
    from app.services import janela_envio
    with app.app_context():
        http.post(f"/api/contatos/{ids['A']}", json={'whatsapp': '34 99999-8888'})
        monkeypatch.setattr(uazapi, 'pode_enviar_agora', lambda: False)
        r = uazapi.enviar('34 99999-8888', 'x', respeitar_janela=True)
        assert not r['ok'] and 'janela' in r['motivo']
        assert janela_envio.descricao_janela() in '08:00–19:00'


# ----------------------------------------------------------------- contatos
def test_api_contatos_salvar_importar_verificar_testar(ambiente):
    app, http, falsa, ids = ambiente

    lista = http.get('/api/contatos').get_json()
    assert lista['resumo']['empresas'] == 2 and lista['resumo']['sem_contato'] == 2

    # salvar: normaliza, valida formato, marca verificação
    r = http.post(f"/api/contatos/{ids['A']}", json={'whatsapp': '34 99999-8888', 'whatsapp_2': '34 3333-4444',
                                                       'email': 'a@x.com', 'responsavel': 'Ana'})
    item = r.get_json()['item']
    assert item['whatsapp'] == '5534999998888' and item['numero_envio'] == '5534999998888'
    assert item['verificado_status'] == 'ok' and 'não conferido online' in item['verificado_motivo']

    # fixo no principal e nada no segundo → inválido
    r = http.post(f"/api/contatos/{ids['B']}", json={'whatsapp': '34 3333-4444'})
    assert r.get_json()['item']['verificado_status'] == 'invalido'

    # importar por CNPJ (o segundo campo do lote conserta a BETA)
    r = http.post('/api/contatos/importar', json={'itens': [
        {'cnpj': '11.444.777/0001-61', 'whatsapp': '(21) 98888-7777'},
        {'cnpj': '99999999000191', 'whatsapp': '11 91111-2222'},
    ]})
    corpo = r.get_json()
    assert corpo['importados'] == 1 and corpo['sem_cadastro'] == ['99999999000191']

    # verificar online: a uazapi diz que só a ALFA existe
    falsa.existentes = {'5534999998888'}
    r = http.post('/api/contatos/verificar', json={}).get_json()
    assert r['verificados'] == 2 and r['online'] is True
    por_cnpj = {i['cnpj']: i for i in http.get('/api/contatos').get_json()['itens']}
    assert por_cnpj[CNPJ_A]['verificado_status'] == 'ok' and 'existe' in por_cnpj[CNPJ_A]['verificado_motivo']
    assert por_cnpj[CNPJ_B]['verificado_status'] == 'inexistente'
    check = [c for c in falsa.chamadas if c['caminho'] == '/chat/check'][-1]
    assert sorted(check['corpo']['numbers']) == ['5521988887777', '5534999998888']

    # teste de envio: sai mesmo fora da janela, marcado como teste, registrado
    r = http.post('/api/contatos/teste', json={'company_id': ids['A']})
    assert r.status_code == 200 and r.get_json()['ok']
    enviado = [c for c in falsa.chamadas if c['caminho'] == '/send/text'][-1]['corpo']
    assert enviado['number'] == '5534999998888' and 'MENSAGEM DE TESTE' in enviado['text']
    hist = http.get(f"/api/contatos/historico?company_id={ids['A']}").get_json()['itens']
    assert hist[0]['status'] == 'enviado' and hist[0]['contexto'] == 'teste'
    assert http.get('/api/contatos').get_json()['resumo']['verificados_ok'] == 1

    # desativar contato tira o número da lista de permitidos
    http.post(f"/api/contatos/{ids['A']}", json={'ativo': False})
    with app.app_context():
        assert not uazapi.destino_permitido('5534999998888')['ok']

    # status da instância na tela
    s = http.get('/api/contatos/whatsapp/status').get_json()
    assert s['configurada'] and s['instancia']['ok'] and s['teto_por_hora'] >= 1
    assert s['webhook']['url_mascarada'].endswith('token=***')


def test_tela_contatos_abre_e_menu_tem_o_item(ambiente):
    _app, http, _falsa, _ids = ambiente
    r = http.get('/contatos')
    assert r.status_code == 200 and 'Contatos e WhatsApp' in r.get_data(as_text=True)
    menu = http.get('/api/me').get_json()['menu']
    assert any(i.get('url') == '/contatos' for grupo in menu for i in grupo['itens'])
