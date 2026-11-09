"""Desvio 27 — FGTS com Agente no PC: parâmetros, tokens, fila, reserva e guia recebida.

SEM rede e SEM portal: o "agente" aqui é o cliente de teste do Flask falando com a API.
Rodar:  python -m pytest tests/test_fgts_agente.py -q
"""
import io
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

_TMP = tempfile.mkdtemp(prefix='fgts_agente_')
os.environ['DATA_DIR'] = _TMP
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['INTEGRACAO_TOKEN'] = 'token-de-teste-com-mais-de-trinta-e-dois-caracteres-0001'
os.environ['INTEGRACAO_TOKENS'] = 'atendechat=token-do-atendechat-com-mais-de-trinta-e-dois-0002'
os.environ['FGTS_DIGITAL_HABILITADO'] = '1'
os.environ.pop('FGTS_EXECUTOR', None)
os.environ.pop('FGTS_LINK_CLIENTE', None)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

INTERNO = {'X-Integracao-Token': os.environ['INTEGRACAO_TOKEN']}
ATENDE = {'X-Integracao-Token': 'token-do-atendechat-com-mais-de-trinta-e-dois-0002'}
CNPJ = '36298150000170'
OUTRO = '11222333000181'
PIX = '00020101021226830014br.gov.bcb.pix2561qrpix.caixa.gov.br/fgts/teste5204000053039865802BR5913FGTS DIGITAL6008BRASILIA62070503***6304'


def _pix_com_crc():
    from app.services.fgts.guias import crc16_ccitt
    return PIX + crc16_ccitt(PIX)


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


@pytest.fixture()
def ambiente(monkeypatch):
    app = create_app()
    app.config['TESTING'] = True
    from app.services.fgts import agentes
    monkeypatch.setattr(agentes, 'dentro_da_janela', lambda agora=None: True)
    with app.app_context():
        from app.fgts_models import FgtsAgente, FgtsEvento, FgtsGuia, FgtsParametro, FgtsParametroHistorico
        from app.integracao_models import FgtsPedido
        from app.models import Company
        for m in (FgtsGuia, FgtsEvento, FgtsAgente, FgtsParametro, FgtsParametroHistorico, FgtsPedido, Company):
            m.query.delete()
        db.session.add_all([Company(razao_social='J F DA SILVA', cnpj=CNPJ, ativo=True),
                            Company(razao_social='OUTRA', cnpj=OUTRO, ativo=True)])
        db.session.commit()
        yield app, app.test_client()


def _agente(app, nome='PC Recepção'):
    from app.services.fgts import agentes
    with app.app_context():
        agente, token = agentes.criar(nome, 'teste')
        return agente.id, {'X-Agente-Token': token}


def _sinal(http, tok, login=True, versao='1.0.0'):
    return http.post('/api/fgts-agente/sinal', json={'versao': versao, 'login_ativo': login, 'maquina': 'PC'},
                     headers=tok)


def _consultar(http, cnpj=CNPJ):
    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': cnpj, 'acao': 'consultar', 'origem': 'atendechat'},
                  headers=ATENDE)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['pedido']['id']


# ----------------------------------------------------------------- tokens
def test_token_por_consumidor_e_legado_continua(ambiente):
    _, http = ambiente
    assert http.get('/api/interno/fgts/config', headers=ATENDE).status_code == 200
    assert http.get('/api/interno/fgts/config', headers=INTERNO).status_code == 200
    assert http.get('/api/interno/fgts/config', headers={'X-Integracao-Token': 'x' * 40}).status_code == 401
    from app.services import integracao_token
    assert integracao_token.consumidor(ATENDE['X-Integracao-Token']) == 'atendechat'
    assert integracao_token.consumidor(INTERNO['X-Integracao-Token']) == 'legado'


def test_token_do_agente_so_abre_a_api_do_agente(ambiente):
    app, http = ambiente
    _, tok = _agente(app)
    assert _sinal(http, tok).status_code == 200
    assert http.get('/api/interno/fgts/config', headers=tok).status_code == 401
    assert http.post('/api/fgts-agente/sinal', json={}, headers={'X-Agente-Token': 'fgag_errado'}).status_code == 401
    assert http.post('/api/fgts-agente/sinal', json={}, headers=INTERNO).status_code == 401


def test_agente_revogado_perde_acesso(ambiente):
    app, http = ambiente
    agente_id, tok = _agente(app)
    from app.services.fgts import agentes
    with app.app_context():
        assert agentes.revogar(agente_id, 'teste')
    assert _sinal(http, tok).status_code == 401


# ----------------------------------------------------------------- parâmetros
def test_parametros_validam_e_guardam_historico(ambiente):
    app, _ = ambiente
    from app.services.fgts import parametros
    with app.app_context():
        assert parametros.valor('executor') == 'agente'
        assert parametros.valor('habilitado') is True            # veio do ambiente
        with pytest.raises(parametros.ParametroInvalido):
            parametros.salvar({'ritmo_s': 0}, 'teste')
        with pytest.raises(parametros.ParametroInvalido):
            parametros.salvar({'janela_inicio': '25:00'}, 'teste')
        with pytest.raises(parametros.ParametroInvalido):
            parametros.salvar({'cnpjs_piloto': '123'}, 'teste')
        assert parametros.salvar({'ritmo_s': '7', 'cnpjs_piloto': '36.298.150/0001-70'}, 'jean') == [
            'ritmo_s', 'cnpjs_piloto']
        assert parametros.valor('ritmo_s') == 7
        assert parametros.valor('cnpjs_piloto') == [CNPJ]
        assert parametros.historico()[0]['por'] == 'jean'
        assert parametros.salvar({'ritmo_s': 7}, 'jean') == []   # sem mudança, sem histórico
        assert parametros.filtros_portal() == {'mensal': 'true', 'rescisorio': 'true',
                                               'processoTrabalhista': 'true', 'consignado': 'true'}


def test_consumidor_desligado_barra_pedido_e_piloto_so_vale_no_link(ambiente):
    app, http = ambiente
    from app.services.fgts import parametros
    with app.app_context():
        parametros.salvar({'cnpjs_piloto': [CNPJ]}, 'teste')
    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': OUTRO, 'acao': 'consultar'}, headers=ATENDE)
    assert r.status_code == 200          # o escritório pede de qualquer empresa
    with app.app_context():
        parametros.salvar({'atendechat_habilitado': False}, 'teste')
    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'consultar', 'origem': 'atendechat'},
                  headers=ATENDE)
    assert r.status_code == 503


def test_link_do_cliente_nasce_desligado(ambiente):
    app, _ = ambiente
    from app.services import fgts_pedidos_service as servico
    with app.app_context():
        assert servico.liberado_para(CNPJ) is False


# ----------------------------------------------------------------- fila e reserva
def test_pedido_do_agente_nao_vai_para_o_robo(ambiente, monkeypatch):
    app, http = ambiente
    from app.services import fgts_digital_robo
    chamado = []
    monkeypatch.setattr(fgts_digital_robo.RoboFgts, 'enfileirar', lambda self, pid: chamado.append(pid))
    pid = _consultar(http)
    assert chamado == []
    from app.integracao_models import FgtsPedido
    with app.app_context():
        assert db.session.get(FgtsPedido, pid).executor == 'agente'


def test_sem_login_ou_offline_nao_recebe_trabalho(ambiente):
    app, http = ambiente
    _, tok = _agente(app)
    _consultar(http)
    _sinal(http, tok, login=False)
    assert http.post('/api/fgts-agente/proximo', headers=tok).status_code == 204
    _sinal(http, tok, login=True)
    assert http.post('/api/fgts-agente/proximo', headers=tok).status_code == 200


def test_versao_minima_bloqueia_agente_antigo(ambiente):
    app, http = ambiente
    from app.services.fgts import parametros
    with app.app_context():
        parametros.salvar({'versao_minima': '1.2.0'}, 'teste')
    _, tok = _agente(app)
    _consultar(http)
    assert _sinal(http, tok, versao='1.1.9').get_json()['versao_aceita'] is False
    assert http.post('/api/fgts-agente/proximo', headers=tok).status_code == 204
    _sinal(http, tok, versao='1.10.0')
    assert http.post('/api/fgts-agente/proximo', headers=tok).status_code == 200


def test_dois_agentes_nao_pegam_o_mesmo_pedido(ambiente):
    app, http = ambiente
    _, tok1 = _agente(app, 'PC 1')
    _, tok2 = _agente(app, 'PC 2')
    _sinal(http, tok1)
    _sinal(http, tok2)
    pid = _consultar(http)
    r1 = http.post('/api/fgts-agente/proximo', headers=tok1)
    assert r1.status_code == 200 and r1.get_json()['pedido']['id'] == pid
    assert r1.get_json()['pedido']['cnpj'] == CNPJ
    assert http.post('/api/fgts-agente/proximo', headers=tok2).status_code == 204
    r = http.post(f'/api/fgts-agente/pedidos/{pid}/progresso', json={'etapa': 'consultando'}, headers=tok2)
    assert r.status_code == 404


def test_consulta_completa_e_libera_emissao(ambiente):
    app, http = ambiente
    _, tok = _agente(app)
    _sinal(http, tok)
    pid = _consultar(http)
    http.post('/api/fgts-agente/proximo', headers=tok)
    assert http.post(f'/api/fgts-agente/pedidos/{pid}/progresso', json={'etapa': 'captcha'},
                     headers=tok).status_code == 200
    ver = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ}', headers=ATENDE).get_json()['pedido']
    assert ver['status'] == 'aguardando_captcha'
    corpo = {'competencias': [{'competencia': '202511', 'total': 463.86, 'situacao': 'vencida',
                               'grupos': [{'tipo': 'Mensal', 'total': 463.86, 'cpf': '12345678909'}]},
                              {'competencia': 'lixo'}],
             'datas_permitidas': ['2026-11-16', '2026-11-17']}
    r = http.post(f'/api/fgts-agente/pedidos/{pid}/resultado', json=corpo, headers=tok)
    assert r.status_code == 200 and r.get_json()['status'] == 'concluido'
    ver = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ}', headers=ATENDE).get_json()['pedido']
    assert [c['competencia'] for c in ver['competencias']] == ['202511']
    assert 'cpf' not in json.dumps(ver['competencias'])
    assert ver['datas_permitidas'] == ['2026-11-16', '2026-11-17']
    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'emitir', 'competencias': ['202511'],
                                                     'data_pagamento': '2026-11-16'}, headers=ATENDE)
    assert r.status_code == 200
    proximo = http.post('/api/fgts-agente/proximo', headers=tok).get_json()['pedido']
    assert proximo['acao'] == 'emitir' and proximo['competencias'] == ['202511']
    assert proximo['data_pagamento'] == '2026-11-16'


def _pedido_emitir(app, http, tok):
    _sinal(http, tok)
    pid = _consultar(http)
    http.post('/api/fgts-agente/proximo', headers=tok)
    http.post(f'/api/fgts-agente/pedidos/{pid}/resultado',
              json={'competencias': [{'competencia': '202511', 'total': 463.86}]}, headers=tok)
    http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'emitir', 'competencias': ['202511']},
              headers=ATENDE)
    return http.post('/api/fgts-agente/proximo', headers=tok).get_json()['pedido']['id']


def _enviar_guia(http, tok, pid, pdf, **meta):
    base = {'numero': '0126102368549700-5', 'competencia': '202511', 'grupo': 'Mensal', 'valor': '463,86',
            'vencimento': '16/11/2026', 'pix': _pix_com_crc()}
    base.update(meta)
    return http.post(f'/api/fgts-agente/pedidos/{pid}/guias', headers=tok, content_type='multipart/form-data',
                     data={'pdf': (io.BytesIO(pdf), 'gfd.pdf'), 'meta': json.dumps(base)})


def test_guia_recebida_com_conferencias(ambiente):
    app, http = ambiente
    _, tok = _agente(app)
    pid = _pedido_emitir(app, http, tok)
    pdf_ok = pdf_com_texto('Empregador 36.298.150/0001-70 GFD')
    assert _enviar_guia(http, tok, pid, b'nao e pdf').status_code == 422
    assert _enviar_guia(http, tok, pid, pdf_com_texto('Empregador 11.222.333/0001-81')).status_code == 422
    assert _enviar_guia(http, tok, pid, pdf_ok, pix=PIX + '0000').status_code == 422
    assert _enviar_guia(http, tok, pid, pdf_ok, competencia='202510').status_code == 409
    r = _enviar_guia(http, tok, pid, pdf_ok)
    assert r.status_code == 201 and r.get_json()['guia']['valor'] == 463.86
    assert _enviar_guia(http, tok, pid, pdf_ok).status_code == 200          # reenvio não duplica
    assert http.post(f'/api/fgts-agente/pedidos/{pid}/resultado', json={}, headers=tok).get_json()['status'] == 'concluido'
    ver = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ}', headers=ATENDE).get_json()['pedido']
    assert len(ver['guias']) == 1 and ver['guias'][0]['identificador'] == '0126102368549700-5'
    pdf = http.get(ver['guias'][0]['pdf_url'] + f'?cnpj={CNPJ}', headers=ATENDE)
    assert pdf.status_code == 200 and pdf.data.startswith(b'%PDF')


def test_emissao_sem_guia_nao_conclui(ambiente):
    app, http = ambiente
    _, tok = _agente(app)
    pid = _pedido_emitir(app, http, tok)
    assert http.post(f'/api/fgts-agente/pedidos/{pid}/resultado', json={}, headers=tok).status_code == 409
    r = http.post(f'/api/fgts-agente/pedidos/{pid}/resultado',
                  json={'erro': {'codigo': 'sem_procuracao', 'mensagem': 'Sem procuração.'}}, headers=tok)
    assert r.get_json()['status'] == 'erro'


def test_reserva_vencida_consulta_volta_e_emissao_vai_para_conferencia(ambiente):
    app, http = ambiente
    _, tok = _agente(app)
    from app.integracao_models import FgtsPedido
    from app.services.fgts import agentes
    pid = _pedido_emitir(app, http, tok)
    http.post(f'/api/fgts-agente/pedidos/{pid}/progresso', json={'etapa': 'emitindo'}, headers=tok)
    with app.app_context():
        p = db.session.get(FgtsPedido, pid)
        p.reservado_ate = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        agentes.liberar_expirados()
        db.session.commit()
        assert db.session.get(FgtsPedido, pid).status == agentes.STATUS_VERIFICAR
    # Empresa bloqueada até alguém conferir: novo pedido retoma o mesmo, sem emitir de novo.
    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ, 'acao': 'consultar'}, headers=ATENDE)
    assert r.get_json()['pedido']['id'] == pid
    with app.app_context():
        agentes.marcar_verificado(pid, False, 'jean')
        assert db.session.get(FgtsPedido, pid).status == 'erro'


def test_tela_e_api_admin_so_para_administrador(ambiente):
    app, http = ambiente
    import base64
    basic = {'Authorization': 'Basic ' + base64.b64encode(
        f"{os.environ['AUTH_USER']}:{os.environ['AUTH_PASSWORD']}".encode()).decode()}
    pagina = http.get('/fgts', headers=basic)
    assert pagina.status_code == 200 and b'<h1>FGTS Digital</h1>' in pagina.data
    assert http.get('/api/fgts/parametros', headers=basic).status_code == 200
    from app.services import permissoes
    assert permissoes.rotina_da_rota('/api/fgts/parametros') == 'fgts'
    assert permissoes.rotina_da_rota('/api/fgts-agente/sinal') is None
    assert permissoes.pode_rotina({'papel': 'operador', 'rotinas': 'todas'}, 'fgts') is False


def test_pix_crc():
    from app.services.fgts.guias import pix_valido
    assert pix_valido(_pix_com_crc())
    assert not pix_valido(PIX + 'FFFF')
    assert not pix_valido('qualquer coisa')


def test_backup_restaura_o_banco(ambiente):
    """O snapshot do backup abre, passa no integrity_check e traz as mesmas linhas."""
    import sqlite3
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import backup_dados
    destino = Path(_TMP) / 'teste_backup.db'
    backup_dados.snapshot_banco(destino)
    con = sqlite3.connect(destino)
    try:
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert con.execute('SELECT count(*) FROM companies').fetchone()[0] == 2
    finally:
        con.close()
