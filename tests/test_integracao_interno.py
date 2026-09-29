"""Integração com o portal do cliente (18o desvio) — SEM rede e SEM custo.

Cobre o que a revisão do plano de notificações apontou como obrigatório:
* token máquina-a-máquina (sem token → 401; token errado → 401);
* classificação "em atraso" e filtro anti-fantasma das pendências;
* emissão de guia com PDF guardado, reaproveitamento sem custo e limite diário;
* recorte por CNPJ no download do PDF (cliente A não baixa guia do cliente B);
* fila de reprocessamento por empresa (agenda em dias úteis, antecipa, drena, limita);
* dias úteis/feriados e o "ajustar para dia útil" do agendamento;
* retomada do lote depois de o processo cair no meio (não repete empresa paga).

Rodar:  python -m pytest tests/test_integracao_interno.py -q
"""
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

_TMP = tempfile.mkdtemp(prefix='interno_teste_')
os.environ['DATA_DIR'] = _TMP
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['LIMITE_GASTO_MENSAL'] = '0'
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['INTEGRACAO_TOKEN'] = 'token-de-teste-com-mais-de-trinta-e-dois-caracteres-0001'
os.environ['INTEGRACAO_MAX_EMISSOES_DIA'] = '2'
os.environ['INTEGRACAO_MAX_REPROC_MES'] = '2'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

TOKEN = {'X-Integracao-Token': os.environ['INTEGRACAO_TOKEN']}
CNPJ_A = '11222333000181'
CNPJ_B = '11444777000161'
PDF = b'%PDF-1.4\n% guia de teste\n'


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app.integracao_models import DasEmissao, FilaReprocessamento
        from app.models import (ApiUsageLog, Company, DebitoRelatorio, PendenciaRelatorio,
                                RelatorioSitFiscal)
        for modelo in (DasEmissao, FilaReprocessamento, ApiUsageLog, DebitoRelatorio,
                       PendenciaRelatorio, RelatorioSitFiscal, Company):
            modelo.query.delete()
        a = Company(razao_social='ALFA', cnpj=CNPJ_A, ativo=True)
        b = Company(razao_social='BETA', cnpj=CNPJ_B, ativo=True)
        db.session.add_all([a, b])
        db.session.commit()

        hoje = date.today()
        rel = RelatorioSitFiscal(company_id=a.id, data_hora=datetime.now(), situacao='ATIVA')
        db.session.add(rel)
        db.session.flush()
        db.session.add_all([
            # em atraso de verdade
            DebitoRelatorio(relatorio_id=rel.id, tipo='SN', receita='SIMPLES NAC.',
                            periodo_apuracao='07/2026', data_vencimento=hoje - timedelta(days=40),
                            valor_original=Decimal('500.00'), saldo_devedor=Decimal('500.00'),
                            multa=Decimal('10.00'), juros=Decimal('5.00'),
                            saldo_devedor_total=Decimal('515.00'), situacao='DEVEDOR'),
            # a vencer: nunca é atraso
            DebitoRelatorio(relatorio_id=rel.id, tipo='SN', receita='SIMPLES NAC.',
                            periodo_apuracao='09/2026', data_vencimento=hoje + timedelta(days=20),
                            valor_original=Decimal('300.00'), saldo_devedor=Decimal('300.00'),
                            saldo_devedor_total=Decimal('300.00'), situacao='A ANALISAR-A VENCER'),
            # fantasma do leitor de PDF (16o desvio): competência 0001 e valor zero
            DebitoRelatorio(relatorio_id=rel.id, tipo='INSS', receita='DCTFWeb',
                            periodo_apuracao='01/0001', data_vencimento=None,
                            valor_original=Decimal('0'), saldo_devedor_total=Decimal('0'),
                            situacao='DEVEDOR'),
        ])
        db.session.add(PendenciaRelatorio(relatorio_id=rel.id, tipo='PGFN', ano='2026',
                                          meses_json=['Inscrição 80 1 26 000123-45']))
        db.session.commit()
        yield app, app.test_client(), {'A': a.id, 'B': b.id, 'relatorio': rel.id}


# ---------------------------------------------------------------- autenticação
def test_sem_token_nega_e_com_token_libera(ambiente):
    _app, http, _ = ambiente
    assert http.get('/api/interno/ping').status_code == 401
    assert http.get('/api/interno/ping', headers={'X-Integracao-Token': 'errado'}).status_code == 401
    r = http.get('/api/interno/ping', headers=TOKEN)
    assert r.status_code == 200 and r.get_json()['sistema'] == 'central-ecac'


def test_token_nao_abre_o_resto_do_sistema(ambiente):
    _app, http, _ = ambiente
    r = http.get('/api/companies', headers=TOKEN)
    assert r.status_code == 401


def test_token_curto_e_ignorado(ambiente, monkeypatch):
    from app.services import integracao_token
    monkeypatch.setenv('INTEGRACAO_TOKEN', 'curto')
    monkeypatch.setattr(integracao_token, '_avisado', False)
    assert not integracao_token.habilitado()
    _app, http, _ = ambiente
    assert http.get('/api/interno/ping', headers={'X-Integracao-Token': 'curto'}).status_code == 503


# ------------------------------------------------------------------ pendências
def test_pendencias_classifica_atraso_e_filtra_fantasma(ambiente):
    _app, http, ids = ambiente
    ontem = (datetime.now() - timedelta(days=1)).date().isoformat()
    r = http.get(f'/api/interno/pendencias?desde={ontem}', headers=TOKEN)
    assert r.status_code == 200
    corpo = r.get_json()
    por_cnpj = {e['cnpj']: e for e in corpo['empresas']}

    alfa = por_cnpj[CNPJ_A]
    assert alfa['relatorio_recente'] is True
    assert alfa['resumo'] == {'qtd_atraso': 1, 'total_atraso': 515.0, 'qtd_a_vencer': 1,
                              'total_a_vencer': 300.0, 'qtd_invalidos': 1, 'qtd_omissoes': 0}
    atrasados = [d for d in alfa['debitos'] if d['em_atraso']]
    assert len(atrasados) == 1
    assert atrasados[0]['periodo_aaaamm'] == '202607'
    assert atrasados[0]['guia'] == 'SN' and atrasados[0]['recalculo_disponivel'] is True
    fantasma = [d for d in alfa['debitos'] if not d['valido']][0]
    assert 'competência fora do plausível' in fantasma['motivos']
    assert 'valor zerado' in fantasma['motivos']
    assert fantasma['em_atraso'] is False
    assert alfa['pgfn'] and 'Inscrição' in alfa['pgfn']

    beta = por_cnpj[CNPJ_B]
    assert beta['relatorio'] is None and beta['relatorio_recente'] is False

    # `desde` no futuro: o relatório deixa de ser "recente" — o portal não cobra em cima dele
    amanha = (datetime.now() + timedelta(days=1)).isoformat()
    r = http.get(f'/api/interno/pendencias/{CNPJ_A}?desde={amanha}', headers=TOKEN)
    assert r.get_json()['empresa']['relatorio_recente'] is False


# --------------------------------------------------------------------- emissão
def _serpro_falsa(monkeypatch, chamadas):
    from app.services import das_emissao_service as srv

    def chamar(tipo, cnpj, pa, categoria, consolidacao):
        chamadas.append({'tipo': tipo, 'cnpj': cnpj, 'pa': pa, 'consolidacao': consolidacao})
        return PDF, {'numeroDocumento': f'0720262581{len(chamadas):07d}',
                     'dataVencimento': consolidacao or '20991231',
                     'valores': {'principal': 500.0, 'multa': 10.0, 'juros': 5.0, 'total': 515.0}}

    monkeypatch.setattr(srv, '_chamar_serpro', chamar)


def test_emissao_guarda_pdf_reaproveita_sem_custo_e_limita_por_dia(ambiente, monkeypatch):
    app, http, _ = ambiente
    chamadas = []
    _serpro_falsa(monkeypatch, chamadas)
    from app.models import ApiUsageLog

    corpo = {'cnpj': CNPJ_A, 'tipo': 'SN', 'periodo_apuracao': '202607',
             'data_consolidacao': '20991230', 'origem': 'portal', 'solicitado_por': 'cliente'}

    r1 = http.post('/api/interno/das/emitir', json=corpo, headers=TOKEN)
    assert r1.status_code == 200, r1.get_json()
    e1 = r1.get_json()
    assert e1['reuso'] is False
    assert e1['emissao']['valor_total'] == 515.0
    assert e1['emissao']['vencimento'] == '20991230'
    assert len(chamadas) == 1

    # mesma guia, ainda válida → do disco, sem chamada e sem custo
    r2 = http.post('/api/interno/das/emitir', json=corpo, headers=TOKEN)
    assert r2.status_code == 200 and r2.get_json()['reuso'] is True
    assert r2.get_json()['emissao']['reuso_de_id'] == e1['emissao']['id']
    assert len(chamadas) == 1
    with app.app_context():
        assert ApiUsageLog.query.filter_by(route_type='emitir').count() == 1

    # forçar = paga de novo (2ª do dia); a 3ª bate no limite diário
    r3 = http.post('/api/interno/das/emitir', json={**corpo, 'forcar': True}, headers=TOKEN)
    assert r3.status_code == 200 and r3.get_json()['reuso'] is False
    r4 = http.post('/api/interno/das/emitir', json={**corpo, 'forcar': True}, headers=TOKEN)
    assert r4.status_code == 429
    assert len(chamadas) == 2

    # o evento aparece na lista que o portal consome
    lista = http.get(f'/api/interno/das/emissoes?cnpj={CNPJ_A}', headers=TOKEN).get_json()
    assert lista['total'] == 3 and sum(1 for e in lista['emissoes'] if not e['reuso']) == 2


def test_download_do_pdf_respeita_o_cnpj(ambiente, monkeypatch):
    _app, http, _ = ambiente
    _serpro_falsa(monkeypatch, [])
    r = http.post('/api/interno/das/emitir', json={'cnpj': CNPJ_A, 'tipo': 'SN',
                                                   'periodo_apuracao': '202607'}, headers=TOKEN)
    emissao_id = r.get_json()['emissao']['id']

    ok = http.get(f'/api/interno/das/{emissao_id}/pdf?cnpj={CNPJ_A}', headers=TOKEN)
    assert ok.status_code == 200 and ok.data.startswith(b'%PDF')
    outro = http.get(f'/api/interno/das/{emissao_id}/pdf?cnpj={CNPJ_B}', headers=TOKEN)
    assert outro.status_code == 403
    assert http.get(f'/api/interno/das/{emissao_id}/pdf').status_code == 401


def test_dctfweb_recusa_data_de_consolidacao(ambiente):
    _app, http, _ = ambiente
    r = http.post('/api/interno/das/emitir', json={'cnpj': CNPJ_A, 'tipo': 'DCTFWEB',
                                                   'periodo_apuracao': '202607',
                                                   'data_consolidacao': '20991230'}, headers=TOKEN)
    assert r.status_code == 400 and 'fale com o escritório' in r.get_json()['message']


def test_empresa_desconhecida_nao_emite(ambiente):
    _app, http, _ = ambiente
    r = http.post('/api/interno/das/emitir', json={'cnpj': '99999999000191', 'tipo': 'SN',
                                                   'periodo_apuracao': '202607'}, headers=TOKEN)
    assert r.status_code == 404


# ------------------------------------------------------------ fila de reprocessamento
def test_fila_agenda_em_dias_uteis_antecipa_drena_e_limita(ambiente):
    app, http, ids = ambiente
    from app.services import fila_reprocessamento_service as fila
    from app.services.calendario_util import somar_dias_uteis

    hoje = date.today()
    r = http.post('/api/interno/reprocessar', json={'cnpj': CNPJ_A, 'dias_uteis': 5},
                  headers=TOKEN)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['item']['agendado_para'] == somar_dias_uteis(hoje, 5).isoformat()

    # pedido novo só antecipa — nunca há dois pendentes para a mesma empresa
    r = http.post('/api/interno/reprocessar', json={'cnpj': CNPJ_A, 'dias_uteis': 1},
                  headers=TOKEN)
    assert r.get_json()['mensagem'] == 'Pedido antecipado.'
    pendentes = http.get('/api/interno/reprocessamentos?pendentes=1', headers=TOKEN).get_json()
    assert pendentes['total'] == 1

    with app.app_context():
        # nada vencido hoje → nada roda
        assert fila.drenar(processar=lambda c: (True, 'ok', 1), hoje=hoje)['executados'] == 0
        # no dia agendado → roda uma vez, grava o relatório gerado
        alvo = somar_dias_uteis(hoje, 1)
        res = fila.drenar(processar=lambda c: (True, 'ok', 77), hoje=alvo)
        assert res == {'executados': 1, 'sucesso': 1, 'falhas': 0, 'adiados': 0, 'teto': False}
        feito = fila.listar(cnpj=CNPJ_A)[0]
        assert feito['pendente'] is False and feito['relatorio_id'] == 77

        # falha adia para o próximo dia útil; 3 falhas seguidas desistem
        from app.models import Company
        empresa = db.session.get(Company, ids['A'])
        assert fila.agendar(empresa, dias_uteis=0, hoje=hoje)['ok']
        for _ in range(3):
            fila.drenar(processar=lambda c: (False, 'SERPRO fora', None), hoje=date(2099, 1, 4))
        ultimo = fila.listar(cnpj=CNPJ_A)[0]
        assert ultimo['pendente'] is False and ultimo['sucesso'] is False
        assert 'desistiu após 3' in ultimo['resultado']

        # limite mensal: já houve 1 sucesso; mais 1 e o próximo pedido é recusado
        assert fila.agendar(empresa, dias_uteis=0, hoje=hoje)['ok']
        fila.drenar(processar=lambda c: (True, 'ok', 78), hoje=date(2099, 1, 4))
        recusado = fila.agendar(empresa, dias_uteis=0, hoje=hoje)
        assert recusado['ok'] is False and 'Limite' in recusado['mensagem']


# ------------------------------------------------------------ calendário / agendamento
def test_calendario_feriados_e_dias_uteis():
    from app.services import calendario_util as cal
    assert cal.pascoa(2026) == date(2026, 4, 5)
    assert cal.eh_feriado(date(2026, 2, 17))     # terça de Carnaval
    assert cal.eh_feriado(date(2026, 4, 3))      # Sexta-feira Santa
    assert cal.eh_feriado(date(2026, 6, 4))      # Corpus Christi
    assert cal.eh_feriado(date(2026, 11, 20))    # Consciência Negra (nacional desde 2024)
    assert not cal.eh_feriado(date(2023, 11, 20))
    assert cal.proximo_dia_util(date(2026, 9, 26)) == date(2026, 9, 28)   # sábado → segunda
    assert cal.proximo_dia_util(date(2026, 4, 21)) == date(2026, 4, 22)   # Tiradentes → dia seguinte
    assert cal.somar_dias_uteis(date(2026, 9, 29), 5) == date(2026, 10, 6)
    assert cal.somar_dias_uteis(date(2026, 4, 17), 1) == date(2026, 4, 20)  # sexta → segunda
    assert cal.dias_uteis_entre(date(2026, 9, 25), date(2026, 9, 28)) == 1


def test_agendamento_mensal_ajusta_para_dia_util():
    from app.services.agendamento_service import proxima_execucao
    base = {'ativo': True, 'frequencia': 'mensal', 'dia_mes': 25, 'hora': '03:00'}
    referencia = datetime(2026, 4, 1, 12, 0)
    # 25/04/2026 é sábado
    assert proxima_execucao(base, referencia) == datetime(2026, 4, 25, 3, 0)
    assert proxima_execucao({**base, 'ajustar_dia_util': True}, referencia) == datetime(2026, 4, 27, 3, 0)
    # depois de rodar na segunda 27, a próxima é a de maio (25/05/2026, segunda)
    assert proxima_execucao({**base, 'ajustar_dia_util': True},
                            datetime(2026, 4, 27, 3, 5)) == datetime(2026, 5, 25, 3, 0)


def test_lote_retoma_de_onde_parou_apos_queda_do_processo(ambiente):
    app, _http, ids = ambiente
    from app.services import agendamento_service as ag

    class ProcessoCaiu(BaseException):
        """Simula SIGKILL: nada trata, o loop não chega ao fim."""

    with app.app_context():
        processadas = []

        def cai_na_segunda(company):
            if len(processadas) == 1:
                raise ProcessoCaiu()
            processadas.append(company.id)
            return True

        with pytest.raises(ProcessoCaiu):
            ag._lote_com_retomada('situacao_fiscal', cai_na_segunda)

        ckpt = ag.checkpoint('situacao_fiscal')
        assert ckpt['interrompido_por'] == 'em_andamento'
        assert ckpt['concluidas'] == [ids['A']]
        assert ckpt['pendentes'] == [ids['B']]

        # a execução seguinte só consulta a que faltava — ALFA não é cobrada de novo
        segunda_rodada = []
        res = ag._lote_com_retomada('situacao_fiscal', lambda c: segunda_rodada.append(c.id) or True)
        assert segunda_rodada == [ids['B']]
        assert res['retomada'] is True and res['processadas'] == 1
        assert ag.checkpoint('situacao_fiscal') == {}
