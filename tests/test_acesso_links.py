"""Link de guias SEM SENHA servido por este sistema (desvio 21) — SEM rede e SEM custo.

Cobre:
* rota interna cria/reaproveita/lista/revoga o link; url pública;
* página e API recusam token errado, vencido, revogado e de empresa inativa; token
  errado repetido bloqueia o IP (mesma regra do login);
* a API só mostra débitos válidos do último relatório (fantasma fica de fora);
* geração: exige data em dia útil dentro da janela para atraso, usa a SERPRO com a
  data, reaproveita PDF da mesma data sem custo, limita por token/competência/dia;
* PDF sai só para emissão da empresa do token;
* emissões do link NÃO contam no limite diário do portal (`INTEGRACAO_MAX_EMISSOES_DIA`).

Rodar:  python -m pytest tests/test_acesso_links.py -q
"""
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

_TMP = tempfile.mkdtemp(prefix='acesso_teste_')
os.environ['DATA_DIR'] = _TMP
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['LIMITE_GASTO_MENSAL'] = '0'
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['INTEGRACAO_TOKEN'] = 'token-de-teste-com-mais-de-trinta-e-dois-caracteres-0001'
os.environ['INTEGRACAO_MAX_EMISSOES_DIA'] = '2'
os.environ['LINK_GUIAS_MAX_GERACOES_DIA'] = '2'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services.calendario_util import proximo_dia_util, somar_dias_uteis  # noqa: E402

TOKEN = {'X-Integracao-Token': os.environ['INTEGRACAO_TOKEN']}
CNPJ_A = '11222333000181'
CNPJ_B = '11444777000161'
PDF = b'%PDF-1.4\n% guia de teste\n'


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app import security
        from app.integracao_models import AcessoLink, DasEmissao
        from app.models import (ApiUsageLog, Company, DebitoRelatorio, PendenciaRelatorio,
                                RelatorioSitFiscal)
        for modelo in (AcessoLink, DasEmissao, ApiUsageLog, DebitoRelatorio, PendenciaRelatorio,
                       RelatorioSitFiscal, Company):
            modelo.query.delete()
        security._TENTATIVAS.clear()
        a = Company(razao_social='ALFA LTDA', cnpj=CNPJ_A, ativo=True)
        b = Company(razao_social='BETA LTDA', cnpj=CNPJ_B, ativo=True)
        db.session.add_all([a, b])
        db.session.commit()
        hoje = date.today()
        rel = RelatorioSitFiscal(company_id=a.id, data_hora=datetime.now(), situacao='ATIVA')
        db.session.add(rel)
        db.session.flush()
        db.session.add_all([
            DebitoRelatorio(relatorio_id=rel.id, tipo='SN', receita='SIMPLES NAC.',
                            periodo_apuracao='07/2026', data_vencimento=hoje - timedelta(days=40),
                            valor_original=Decimal('500.00'), saldo_devedor=Decimal('500.00'),
                            multa=Decimal('10.00'), juros=Decimal('5.00'),
                            saldo_devedor_total=Decimal('515.00'), situacao='DEVEDOR'),
            DebitoRelatorio(relatorio_id=rel.id, tipo='SN', receita='SIMPLES NAC.',
                            periodo_apuracao='09/2026', data_vencimento=hoje + timedelta(days=20),
                            valor_original=Decimal('300.00'), saldo_devedor=Decimal('300.00'),
                            saldo_devedor_total=Decimal('300.00'), situacao='A ANALISAR-A VENCER'),
            DebitoRelatorio(relatorio_id=rel.id, tipo='INSS', receita='DCTFWeb',
                            periodo_apuracao='01/0001', data_vencimento=None,
                            valor_original=Decimal('0'), saldo_devedor_total=Decimal('0'),
                            situacao='DEVEDOR'),
        ])
        db.session.add(PendenciaRelatorio(relatorio_id=rel.id, tipo='PGFN', ano='2026',
                                          meses_json=['Inscrição 80 1 26 000123-45']))
        db.session.commit()
        yield app, app.test_client(), {'A': a.id, 'B': b.id}


def _serpro_falsa(monkeypatch, chamadas):
    from app.services import das_emissao_service as srv

    def chamar(tipo, cnpj, pa, categoria, consolidacao):
        chamadas.append({'tipo': tipo, 'cnpj': cnpj, 'pa': pa, 'consolidacao': consolidacao})
        return PDF, {'numeroDocumento': f'0720262581{len(chamadas):07d}',
                     'dataVencimento': consolidacao or '20991231',
                     'valores': {'principal': 500.0, 'multa': 10.0, 'juros': 5.0, 'total': 515.0}}

    monkeypatch.setattr(srv, '_chamar_serpro', chamar)


def _criar_link(http, cnpj=CNPJ_A, **extra):
    r = http.post('/api/interno/acesso-links', json={'cnpj': cnpj, 'solicitado_por': 'atendechat:jean', **extra},
                  headers=TOKEN)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _dia_util(a_partir_de_dias=1):
    return proximo_dia_util(date.today() + timedelta(days=a_partir_de_dias)).isoformat()


# ------------------------------------------------------------------ rota interna
def test_interno_cria_reaproveita_lista_e_revoga(ambiente):
    _app, http, _ = ambiente
    primeiro = _criar_link(http)
    token = primeiro['link']['token']
    assert len(token) == 64 and all(c in '0123456789abcdef' for c in token)
    assert primeiro['url'].endswith(f'/g/{token}')
    assert primeiro['link']['valido'] is True and primeiro['link']['origem'] == 'atendechat'

    # mesma empresa/origem com validade sobrando → mesmo token (a pessoa não recebe outro link)
    segundo = _criar_link(http)
    assert segundo['link']['token'] == token

    r = http.get(f'/api/interno/acesso-links?cnpj={CNPJ_A}', headers=TOKEN)
    assert r.status_code == 200 and r.get_json()['total'] == 1

    r = http.post('/api/interno/acesso-links/revogar', json={'cnpj': CNPJ_A, 'solicitado_por': 'jean'}, headers=TOKEN)
    assert r.get_json()['revogados'] == 1
    assert http.get(f'/api/acesso/{token}').status_code == 404
    # depois de revogar, um pedido novo gera outro token
    assert _criar_link(http)['link']['token'] != token


def test_interno_recusa_empresa_desconhecida_inativa_e_origem_invalida(ambiente):
    app, http, ids = ambiente
    assert http.post('/api/interno/acesso-links', json={'cnpj': '00000000000000'}, headers=TOKEN).status_code == 404
    assert http.post('/api/interno/acesso-links', json={'cnpj': CNPJ_A, 'origem': 'portal'}, headers=TOKEN).status_code == 400
    from app.models import Company
    db.session.get(Company, ids['B']).ativo = False
    db.session.commit()
    assert http.post('/api/interno/acesso-links', json={'cnpj': CNPJ_B}, headers=TOKEN).status_code == 409


def test_rota_interna_exige_token_de_integracao(ambiente):
    _app, http, _ = ambiente
    assert http.post('/api/interno/acesso-links', json={'cnpj': CNPJ_A}).status_code == 401


# ------------------------------------------------------------- página e validação
def test_pagina_abre_sem_login_e_conta_acesso(ambiente):
    app, http, _ = ambiente
    token = _criar_link(http)['link']['token']
    r = http.get(f'/g/{token}')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert token in html and 'Carregando' in html and 'noindex' in html
    from app.integracao_models import AcessoLink
    db.session.expire_all()
    assert AcessoLink.query.filter_by(token=token).first().acessos == 1


def test_token_errado_vencido_e_empresa_inativa_nao_abrem(ambiente):
    app, http, ids = ambiente
    r = http.get('/g/' + 'f' * 64)
    assert r.status_code == 404 and 'Link indisponível' in r.get_data(as_text=True)
    assert http.get('/api/acesso/nao-e-token').status_code == 404
    assert http.get('/g/qualquer-coisa').status_code == 404

    token = _criar_link(http)['link']['token']
    from app.integracao_models import AcessoLink
    link = AcessoLink.query.filter_by(token=token).first()
    link.expira_em = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert http.get(f'/api/acesso/{token}').status_code == 404

    token_b = _criar_link(http, cnpj=CNPJ_B)['link']['token']
    from app.models import Company
    db.session.get(Company, ids['B']).ativo = False
    db.session.commit()
    assert http.get(f'/api/acesso/{token_b}').status_code == 404


def test_token_errado_repetido_bloqueia_o_ip(ambiente):
    _app, http, _ = ambiente
    token = _criar_link(http)['link']['token']
    for _ in range(5):
        assert http.get('/api/acesso/' + 'a' * 64).status_code == 404
    # bloqueado: nem o token certo abre até o tempo passar
    assert http.get(f'/api/acesso/{token}').status_code == 429
    assert http.get(f'/g/{token}').status_code == 429


def test_api_mostra_so_debitos_validos_do_ultimo_relatorio(ambiente):
    _app, http, _ = ambiente
    token = _criar_link(http)['link']['token']
    r = http.get(f'/api/acesso/{token}')
    assert r.status_code == 200
    j = r.get_json()
    assert j['empresa'] == {'razao_social': 'ALFA LTDA', 'cnpj': '11.222.333/0001-81'}
    assert j['relatorio']['data']
    assert [d['competencia'] for d in j['em_atraso']] == ['07/2026']
    assert j['em_atraso'][0]['rotulo'].startswith('DAS') and j['em_atraso'][0]['pode_gerar'] is True
    assert [d['competencia'] for d in j['a_vencer']] == ['09/2026']
    assert j['total_em_atraso'] == 515.0
    assert j['pgfn'] is True and j['parcelamento'] is False
    # o fantasma (01/0001, valor zero) não aparece em lugar nenhum
    assert all(d['competencia'] != '01/0001' for d in j['em_atraso'] + j['a_vencer'])


# -------------------------------------------------------------------- geração
def test_gera_guia_em_atraso_com_data_e_baixa_o_pdf(ambiente, monkeypatch):
    _app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token = _criar_link(http)['link']['token']
    debito = http.get(f'/api/acesso/{token}').get_json()['em_atraso'][0]
    dia = _dia_util(1)

    r = http.post(f'/api/acesso/{token}/guias', json={'debito_id': debito['id'], 'data_pagamento': dia})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j['reuso'] is False and j['guia']['valor_total'] == 515.0
    assert chamadas == [{'tipo': 'SN', 'cnpj': CNPJ_A, 'pa': '202607', 'consolidacao': dia.replace('-', '')}]

    pdf = http.get(j['guia']['pdf_url'])
    assert pdf.status_code == 200 and pdf.data == PDF
    assert pdf.headers['Content-Disposition'].startswith('inline')

    # mesma data de novo → PDF guardado, sem chamar a SERPRO
    r2 = http.post(f'/api/acesso/{token}/guias', json={'debito_id': debito['id'], 'data_pagamento': dia})
    assert r2.status_code == 200 and r2.get_json()['reuso'] is True and len(chamadas) == 1

    # a emissão ficou registrada como do link, com o token curto
    r3 = http.get(f'/api/interno/das/emissoes?cnpj={CNPJ_A}', headers=TOKEN)
    origens = {(e['origem'], e['solicitado_por']) for e in r3.get_json()['emissoes']}
    assert ('link', f'link:{token[:16]}') in origens


def test_guia_a_vencer_sai_sem_data(ambiente, monkeypatch):
    _app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token = _criar_link(http)['link']['token']
    debito = http.get(f'/api/acesso/{token}').get_json()['a_vencer'][0]
    r = http.post(f'/api/acesso/{token}/guias', json={'debito_id': debito['id']})
    assert r.status_code == 200, r.get_json()
    assert chamadas[-1]['consolidacao'] is None and chamadas[-1]['pa'] == '202609'


def test_recusa_data_ausente_passada_fora_da_janela_ou_fim_de_semana(ambiente, monkeypatch):
    _app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token = _criar_link(http)['link']['token']
    debito = http.get(f'/api/acesso/{token}').get_json()['em_atraso'][0]
    url = f'/api/acesso/{token}/guias'
    assert http.post(url, json={'debito_id': debito['id']}).status_code == 400
    assert http.post(url, json={'debito_id': debito['id'], 'data_pagamento': '2020-01-02'}).status_code == 400
    longe = (date.today() + timedelta(days=60)).isoformat()
    assert http.post(url, json={'debito_id': debito['id'], 'data_pagamento': longe}).status_code == 400
    hoje = date.today()
    sabado = hoje + timedelta(days=(5 - hoje.weekday()) % 7 or 7)
    assert http.post(url, json={'debito_id': debito['id'], 'data_pagamento': sabado.isoformat()}).status_code == 400
    assert chamadas == []


def test_so_emite_debito_do_proprio_relatorio(ambiente, monkeypatch):
    _app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token_b = _criar_link(http, cnpj=CNPJ_B)['link']['token']
    debito_a = http.get(f'/api/acesso/{_criar_link(http)["link"]["token"]}').get_json()['em_atraso'][0]
    # id de débito da empresa A apresentado no link da empresa B
    r = http.post(f'/api/acesso/{token_b}/guias', json={'debito_id': debito_a['id'], 'data_pagamento': _dia_util(1)})
    assert r.status_code == 404 and chamadas == []
    assert http.post(f'/api/acesso/{token_b}/guias', json={'debito_id': 999999}).status_code == 404


def test_pdf_so_sai_para_emissao_da_empresa_do_token(ambiente, monkeypatch):
    _app, http, _ = ambiente
    _serpro_falsa(monkeypatch, [])
    token_a = _criar_link(http)['link']['token']
    debito = http.get(f'/api/acesso/{token_a}').get_json()['em_atraso'][0]
    j = http.post(f'/api/acesso/{token_a}/guias', json={'debito_id': debito['id'], 'data_pagamento': _dia_util(1)}).get_json()
    emissao_id = j['guia']['id']
    token_b = _criar_link(http, cnpj=CNPJ_B)['link']['token']
    assert http.get(f'/api/acesso/{token_b}/guias/{emissao_id}/pdf').status_code == 404
    assert http.get(f'/api/acesso/{token_a}/guias/{emissao_id}/pdf').status_code == 200


def test_limite_por_token_e_nao_bloqueia_o_portal(ambiente, monkeypatch):
    _app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token = _criar_link(http)['link']['token']
    debito = http.get(f'/api/acesso/{token}').get_json()['em_atraso'][0]
    url = f'/api/acesso/{token}/guias'
    d1, d2, d3 = _dia_util(1), somar_dias_uteis(date.today(), 2).isoformat(), somar_dias_uteis(date.today(), 3).isoformat()
    assert http.post(url, json={'debito_id': debito['id'], 'data_pagamento': d1}).status_code == 200
    assert http.post(url, json={'debito_id': debito['id'], 'data_pagamento': d2}).status_code == 200
    # LINK_GUIAS_MAX_GERACOES_DIA=2: a terceira data nova é recusada sem chamar a SERPRO
    r = http.post(url, json={'debito_id': debito['id'], 'data_pagamento': d3})
    assert r.status_code == 429 and len(chamadas) == 2
    # ...mas o PDF da mesma data continua saindo (reuso)
    assert http.post(url, json={'debito_id': debito['id'], 'data_pagamento': d1}).get_json()['reuso'] is True

    # o portal (INTEGRACAO_MAX_EMISSOES_DIA=2) não foi consumido pelas 2 emissões do link
    r = http.post('/api/interno/das/emitir', json={'cnpj': CNPJ_A, 'tipo': 'SN', 'periodo_apuracao': '202607',
                                                   'data_consolidacao': d3.replace('-', ''), 'origem': 'portal'},
                  headers=TOKEN)
    assert r.status_code == 200, r.get_json()
    assert len(chamadas) == 3


# ------------------------------------------------- outra competência / data em qualquer guia
def test_a_vencer_aceita_data_de_pagamento_opcional(ambiente, monkeypatch):
    _app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token = _criar_link(http)['link']['token']
    debito = http.get(f'/api/acesso/{token}').get_json()['a_vencer'][0]
    dia = _dia_util(2)
    r = http.post(f'/api/acesso/{token}/guias', json={'debito_id': debito['id'], 'data_pagamento': dia})
    assert r.status_code == 200, r.get_json()
    assert chamadas[-1] == {'tipo': 'SN', 'cnpj': CNPJ_A, 'pa': '202609', 'consolidacao': dia.replace('-', '')}


def test_outra_competencia_gera_guia_que_nao_esta_no_relatorio(ambiente, monkeypatch):
    _app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token = _criar_link(http)['link']['token']
    pagina = http.get(f'/api/acesso/{token}').get_json()
    # empresa comum: SN e INSS; a lista de meses começa no mês atual
    assert [t['tipo'] for t in pagina['tipos_livres']] == ['SN', 'DCTFWEB']
    hoje = date.today()
    assert pagina['competencias'][0] == f'{hoje.year:04d}{hoje.month:02d}' and len(pagina['competencias']) == 36

    comp = pagina['competencias'][1]  # mês passado: ainda não consta no relatório
    dia = _dia_util(1)
    r = http.post(f'/api/acesso/{token}/guias', json={'tipo': 'SN', 'competencia': comp, 'data_pagamento': dia})
    assert r.status_code == 200, r.get_json()
    assert chamadas[-1] == {'tipo': 'SN', 'cnpj': CNPJ_A, 'pa': comp, 'consolidacao': dia.replace('-', '')}
    # INSS do 13º
    r = http.post(f'/api/acesso/{token}/guias', json={'tipo': 'DCTFWEB', 'competencia': f'{hoje.year}13', 'data_pagamento': dia})
    assert r.status_code == 200, r.get_json()
    assert chamadas[-1]['pa'] == f'{hoje.year}13'
    # sem data também sai (guia original)
    r = http.post(f'/api/acesso/{token}/guias', json={'tipo': 'SN', 'competencia': pagina['competencias'][2]})
    assert r.status_code == 200 and chamadas[-1]['consolidacao'] is None


def test_outra_competencia_recusa_tipo_e_mes_invalidos(ambiente, monkeypatch):
    app, http, ids = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    token = _criar_link(http)['link']['token']
    url = f'/api/acesso/{token}/guias'
    assert http.post(url, json={'tipo': 'PARC_SN', 'competencia': '202601'}).status_code == 400
    assert http.post(url, json={'tipo': 'MEI', 'competencia': '202601'}).status_code == 400  # não é MEI
    assert http.post(url, json={'tipo': 'SN', 'competencia': '201501'}).status_code == 400  # fora dos 36 meses
    futuro = date.today().year + 1
    assert http.post(url, json={'tipo': 'SN', 'competencia': f'{futuro}01'}).status_code == 400
    assert chamadas == []

    # empresa MEI: só DAS MEI
    from app.integracao_models import EmpresaMei
    db.session.add(EmpresaMei(company_id=ids['B'], eh_mei=True))
    db.session.commit()
    token_b = _criar_link(http, cnpj=CNPJ_B)['link']['token']
    pagina = http.get(f'/api/acesso/{token_b}').get_json()
    assert [t['tipo'] for t in pagina['tipos_livres']] == ['MEI']
    assert pagina['relatorio'] is None  # sem relatório, mas a seção "outra competência" existe
    r = http.post(f'/api/acesso/{token_b}/guias', json={'tipo': 'MEI', 'competencia': pagina['competencias'][1]})
    assert r.status_code == 200, r.get_json()
    assert chamadas[-1]['tipo'] == 'MEI' and chamadas[-1]['cnpj'] == CNPJ_B
