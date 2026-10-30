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
        from app.models import ParcelamentoParcela, ParcelamentoPedido
        for modelo in (ParcelamentoParcela, ParcelamentoPedido, PendenciaRelatorio,
                       RelatorioSitFiscal, Company):
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


# --- andamento da busca de pedidos/parcelas (Acompanhamento) ---------------------

def test_busca_pedidos_publica_andamento_e_erro(app, monkeypatch):
    from app.services.parcelamentos_serpro_service import ParcelamentosSerproService
    c = Company(razao_social='ELIUDE', cnpj='11222333000181', ativo=True,
                consultar_parc_sn=True, consultar_parc_mei=True)
    db.session.add(c)
    db.session.commit()
    vistos = []

    def falso(self, company, tipo):
        vistos.append((tipo, db.session.get(Company, company.id).processing_status))
        if tipo == 'PARC_MEI':
            return {'erro': 'Contribuinte não é MEI'}
        return {'success': True, 'pedidos': [], 'message': '0 pedido(s), 0 ativo(s).'}

    monkeypatch.setattr(ParcelamentosSerproService, 'buscar_pedidos_tipo', falso)
    ParcelamentosSerproService().buscar_pedidos_empresa(c.id)

    assert vistos == [('PARC_SN', 'processing'), ('PARC_MEI', 'processing')]
    c = db.session.get(Company, c.id)
    assert c.processing_status == 'done'
    assert c.processing_progress == 100
    assert 'Parcelamento Simples Nacional: 0 pedido(s)' in c.processing_message
    assert 'Parcelamento MEI: erro — Contribuinte não é MEI' in c.processing_message


def test_busca_sem_tipo_marcado_avisa(app):
    from app.services.parcelamentos_serpro_service import ParcelamentosSerproService
    c = Company(razao_social='SEM TIPO', cnpj='11222333000181', ativo=True)
    db.session.add(c)
    db.session.commit()
    ParcelamentosSerproService().buscar_pedidos_empresa(c.id)
    assert 'Nenhum tipo de parcelamento marcado' in db.session.get(Company, c.id).processing_message


def test_rota_marca_processando_antes_da_thread(app, monkeypatch):
    import app.routes.parcelamentos as rotas
    c = Company(razao_social='ALFA', cnpj='11222333000181', ativo=True, consultar_parc_sn=True)
    db.session.add(c)
    db.session.commit()
    monkeypatch.setattr(rotas.threading, 'Thread', lambda *a, **k: type('T', (), {'start': lambda s: None})())
    auth = {'Authorization': 'Basic dGVzdGU6dGVzdGU='}
    cli = app.test_client()
    assert cli.post(f'/api/parcelamentos/buscar-pedidos-empresa/{c.id}', headers=auth).status_code == 200
    assert cli.get('/api/parcelamentos/status', headers=auth).get_json()['has_processing'] is True


def test_pedidos_filtra_empresa_e_tipo(app):
    from app.models import ParcelamentoPedido
    a = Company(razao_social='A', cnpj='11222333000181', ativo=True)
    b = Company(razao_social='B', cnpj='11444777000161', ativo=True)
    db.session.add_all([a, b])
    db.session.flush()
    db.session.add_all([
        ParcelamentoPedido(company_id=a.id, tipo='PARC_SN', numero='1', ativo=True),
        ParcelamentoPedido(company_id=b.id, tipo='PARC_MEI', numero='2', ativo=True),
    ])
    db.session.commit()
    auth = {'Authorization': 'Basic dGVzdGU6dGVzdGU='}
    cli = app.test_client()
    assert [p['numero'] for p in cli.get(f'/api/parcelamentos/pedidos?company_id={b.id}', headers=auth).get_json()] == ['2']
    assert [p['numero'] for p in cli.get('/api/parcelamentos/pedidos?tipo=PARC_SN', headers=auth).get_json()] == ['1']


# --- baixa de parcelas: lê `dados.listaParcelas` e emite pelo caminho do portal ---------

def test_baixa_parcelas_le_lista_e_emite_pdf(app, monkeypatch):
    from app.integracao_models import DasEmissao, ParcelasConsulta
    from app.models import ParcelamentoParcela, ParcelamentoPedido
    from app.services import das_emissao_service
    from app.services.parcelamentos_serpro_service import ParcelamentosSerproService
    import app.services.serpro_das_service as sds
    for modelo in (DasEmissao, ParcelasConsulta):
        modelo.query.delete()
    c = Company(razao_social='SANDRA', cnpj='11222333000181', ativo=True,
                consultar_parc_sn=True, consultar_parc_mei=True)
    db.session.add(c)
    db.session.flush()
    db.session.add(ParcelamentoPedido(company_id=c.id, tipo='PARC_SN', numero='3', ativo=True))
    db.session.commit()

    consultas, emissoes = [], []

    def consultar_falso(cnpj, sistema, servico):
        consultas.append(servico)
        return {'dados': '{"listaParcelas": [{"parcela": 202609, "valor": 211.72},'
                         ' {"parcela": 202610, "valor": 210.5}]}', 'mensagens': []}

    def emitir_falso(tipo, cnpj, pa, categoria, consolidacao):
        emissoes.append(pa)
        if pa == '202610':
            raise das_emissao_service.EmissaoBloqueada('Teto de gasto: limite atingido.', 409)
        return b'%PDF-1.4\n% parcela\n', {'numeroDocumento': f'07202625810{pa}',
                                          'dataVencimento': '20261130',
                                          'valores': {'total': 211.72}}

    monkeypatch.setattr(sds, 'consultar_parcelas_para_gerar', consultar_falso)
    monkeypatch.setattr(das_emissao_service, '_chamar_serpro', emitir_falso)

    r = ParcelamentosSerproService().buscar_parcelas_empresa(c.id, emitir_pdfs=True)

    # PARC_MEI marcado mas sem pedido "Em parcelamento": não consulta (chamada paga)
    assert consultas == ['PARCELASPARAGERAR162']
    assert emissoes == ['202609', '202610']
    p1, p2 = ParcelamentoParcela.query.filter_by(company_id=c.id).order_by(ParcelamentoParcela.parcela).all()
    assert (p1.parcela, float(p1.valor_total), str(p1.data_vencimento)) == ('202609', 211.72, '2026-11-30')
    assert p1.pdf_local_path and p1.pdf_local_path.endswith('.pdf')
    assert p2.pdf_local_path is None and 'Teto de gasto' in p2.source_json['aviso']
    msg = db.session.get(Company, c.id).processing_message
    assert 'Parcelamento Simples Nacional: 2 parcela(s)' in msg and '202610: Teto de gasto' in msg
    assert 'Parcelamento MEI: sem pedido' in msg
    assert r['parcelas']['PARC_SN']

    # rodar de novo no mesmo dia: consulta reaproveitada e a guia emitida reaproveitada
    ParcelamentosSerproService().buscar_parcelas_empresa(c.id, emitir_pdfs=True)
    assert consultas == ['PARCELASPARAGERAR162']
    assert emissoes == ['202609', '202610', '202610']
    assert ParcelamentoParcela.query.filter_by(company_id=c.id).count() == 2

    auth = {'Authorization': 'Basic dGVzdGU6dGVzdGU='}
    lista = app.test_client().get('/api/parcelamentos/parcelas', headers=auth).get_json()
    avisos = {x['parcela']: (x['has_pdf'], x['download_message']) for x in lista}
    assert avisos['202609'] == (True, None)
    assert avisos['202610'][0] is False and 'Teto de gasto' in avisos['202610'][1]
    # situação por empresa/tipo junta pedido, parcelas, PDF e motivo
    linhas = app.test_client().get(f'/api/parcelamentos/resumo?company_id={c.id}', headers=auth).get_json()
    sn = next(x for x in linhas if x['tipo'] == 'PARC_SN')
    mei = next(x for x in linhas if x['tipo'] == 'PARC_MEI')
    assert sn['pedido']['numero'] == '3' and sn['consulta']['qtd'] == 2
    assert sn['tom'] == 'aviso' and '1 de 2 PDF(s)' in sn['situacao'] and 'Teto de gasto' in sn['situacao']
    assert sn['resultado']['parcelas']['ok'] is True
    assert mei['pedido'] is None and mei['tom'] == 'pendente'


def test_resumo_mostra_erro_da_busca_de_pedidos(app, monkeypatch):
    from app.services.parcelamentos_serpro_service import ParcelamentosSerproService
    c = Company(razao_social='ELIUDE', cnpj='11222333000181', ativo=True, consultar_parc_mei=True)
    db.session.add(c)
    db.session.commit()
    monkeypatch.setattr(ParcelamentosSerproService, 'buscar_pedidos_tipo',
                        lambda self, company, tipo: {'erro': 'Acesso negado: procuração'})
    ParcelamentosSerproService().buscar_pedidos_empresa(c.id)
    auth = {'Authorization': 'Basic dGVzdGU6dGVzdGU='}
    [linha] = app.test_client().get('/api/parcelamentos/resumo', headers=auth).get_json()
    assert linha['tom'] == 'erro' and 'Acesso negado: procuração' in linha['situacao']
