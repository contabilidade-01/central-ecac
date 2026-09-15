"""Testes do fluxo PGDAS-D (Calcular → Transmitir → DAS) SEM rede e SEM custo.

A SERPRO é simulada por `FakeSerpro` (transporte injetado no cliente). Cada teste
confere também QUANTAS chamadas pagas teriam saído — o objetivo do módulo é gastar
só quando necessário.

Rodar:  python -m pytest tests/test_escritorio_pgdasd.py -q
"""
import base64
import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

_TMP = tempfile.mkdtemp(prefix='pgdasd_teste_')
os.environ['DATA_DIR'] = _TMP                  # banco/relatórios isolados (antes de importar app)
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['LIMITE_GASTO_MENSAL'] = '0'
os.environ['SCHEDULER_ENABLED'] = '0'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

CNPJ = '50006293000192'
COMP = '2026-08'
PA = '202608'
PDF = base64.b64encode(b'%PDF-1.4\n% teste\n').decode()


# ------------------------------------------------------------------ SERPRO falsa
def ok_msg():
    return [{'codigo': 'Sucesso-PGDASD', 'texto': 'Requisição efetuada com sucesso.'}]


def r_calculo(valores=((1007, 120.5), (1006, 80.25))):
    return 200, {'status': 200, 'mensagens': ok_msg(), 'dados': json.dumps({
        'valoresDevidos': [{'codigoTributo': c, 'valor': v} for c, v in valores]})}


def r_transmissao(numero='00000000202608001'):
    return 200, {'status': 200, 'mensagens': ok_msg(), 'dados': json.dumps({
        'idDeclaracao': numero, 'dataHoraTransmissao': '20260910101010',
        'valoresDevidos': [{'codigoTributo': 1007, 'valor': 120.5}, {'codigoTributo': 1006, 'valor': 80.25}],
        'declaracao': PDF, 'recibo': PDF, 'notificacaoMaed': None, 'darf': None})}


def r_consulta(declaracoes=()):
    operacoes = [{'tipoOperacao': 'Original', 'indiceDeclaracao': {
        'numeroDeclaracao': n, 'dataHoraTransmissao': f'2026090{i + 1}101010', 'malha': None}}
        for i, n in enumerate(declaracoes)]
    return 200, {'status': 200, 'mensagens': ok_msg(), 'dados': json.dumps({
        'anoCalendario': 2026, 'periodo': {'periodoApuracao': int(PA), 'operacoes': operacoes}})}


def r_das(numero='07202625812345678', vencimento='20990120'):
    return 200, {'status': 200, 'mensagens': ok_msg(), 'dados': json.dumps([{
        'pdf': PDF, 'cnpjCompleto': CNPJ, 'detalhamento': {
            'periodoApuracao': PA, 'numeroDocumento': numero, 'dataVencimento': vencimento,
            'valores': {'principal': 200.75, 'multa': 0, 'juros': 0, 'total': 200.75}}}])}


def r_msg(codigo, texto='x', status=200):
    return status, {'status': status, 'mensagens': [{'codigo': codigo, 'texto': texto}], 'dados': ''}


class FakeSerpro:
    def __init__(self, *respostas):
        self.fila = list(respostas)
        self.chamadas = []

    def __call__(self, url, headers, payload, timeout, contexto):
        self.chamadas.append({'url': url, 'payload': payload, 'servico': payload['pedidoDados']['idServico'],
                              'dados': json.loads(payload['pedidoDados']['dados'])})
        if not self.fila:
            raise AssertionError('Chamada à SERPRO não esperada: ' + payload['pedidoDados']['idServico'])
        r = self.fila.pop(0)
        if isinstance(r, Exception):
            raise r
        status, corpo = r
        return SimpleNamespace(status_code=status, text=json.dumps(corpo))

    def servicos(self):
        return [c['servico'] for c in self.chamadas]


def cliente(fake):
    from app.services.serpro_pgdasd_client import SerproPgdasdClient
    return SerproPgdasdClient(
        transporte=fake, gerar_headers=lambda s: {'Authorization': 'Bearer teste'},
        montar_payload=lambda s, cnpj, sv, dados: {
            'contribuinte': {'numero': cnpj, 'tipo': 2},
            'pedidoDados': {'idSistema': sv.id_sistema, 'idServico': sv.id_servico,
                            'versaoSistema': sv.versao, 'dados': json.dumps(dados)}})


# ---------------------------------------------------------------------- fixtures
@pytest.fixture(scope='module')
def app():
    aplicacao = create_app()
    aplicacao.config['TESTING'] = True
    return aplicacao


@pytest.fixture()
def svc(app, monkeypatch):
    from app import escritorio_models as em
    from app.models import ApiUsageLog, AppSetting, Company
    from app.services import escritorio_pgdasd
    from app.services.procuracao_service import ProcuracaoService

    ctx = app.app_context()
    ctx.push()
    for modelo in (em.EscritorioSerproChamada, em.EscritorioDeclaracao, em.EscritorioLancamento,
                   em.EscritorioPgdasHistorico, em.EscritorioEmpresa, ApiUsageLog, Company, AppSetting):
        modelo.query.delete()
    db.session.commit()
    caminho = ProcuracaoService.caminho()
    if caminho.exists():
        caminho.unlink()

    db.session.add(AppSetting(contador_cnpj='11222333000181'))
    db.session.add(Company(razao_social='RAFAEL ANDERSON TESTE', cnpj=CNPJ, ativo=True))
    db.session.add(em.EscritorioLancamento(
        cnpj=CNPJ, competencia=COMP, perfil='comercio', total_receita=6049.52,
        rec_sem_st=4000.00, rec_monofasica=2049.52, ok=True))
    db.session.commit()
    # padrão dos testes: empresa optante há anos (nenhuma receita anterior exigida)
    empresa = Company.query.filter_by(cnpj=CNPJ).first()
    db.session.add(em.EscritorioEmpresa(company_id=empresa.id, incluso=True, inicio_simples='2020-01'))
    db.session.commit()
    # configuração/certificado são testados à parte
    monkeypatch.setattr(escritorio_pgdasd, 'checar_configuracao', lambda: ([], [], {}))
    yield escritorio_pgdasd
    db.session.remove()
    ctx.pop()


def contexto(svc):
    return svc.carregar(CNPJ, COMP, 'teste')


def chamadas_auditadas():
    from app.escritorio_models import EscritorioSerproChamada
    return EscritorioSerproChamada.query.order_by(EscritorioSerproChamada.id).all()


def custo_registrado():
    from app.models import ApiUsageLog
    return round(sum(float(l.estimated_cost) for l in ApiUsageLog.query.all()), 2)


# ------------------------------------------------------------------ montagem (grátis)
def test_montar_declaracao_mapeia_atividades(svc):
    dados, bloqueios, _avisos = svc.montar_declaracao(contexto(svc))
    assert bloqueios == []
    decl = dados['declaracao']
    assert decl['receitaPaCompetenciaInterno'] == 6049.52
    ativ = {a['idAtividade']: a for a in decl['estabelecimentos'][0]['atividades']}
    assert ativ[1]['valorAtividade'] == 4000.0
    parcela = ativ[2]['receitasAtividade'][0]
    assert parcela['valor'] == 2049.52
    assert {(q['codigoTributo'], q['id']) for q in parcela['qualificacoesTributarias']} == {(1004, 9), (1005, 9)}
    assert 'isencoes' not in parcela      # SERPRO recusa ST/monofásica em isencoes (15/09/2026)


def test_hash_ignora_tipo_e_indicadores(svc):
    dados, _b, _a = svc.montar_declaracao(contexto(svc), tipo=1)
    outro = json.loads(json.dumps(dados))
    outro['declaracao']['tipoDeclaracao'] = 2
    outro['indicadorTransmissao'] = True
    outro['valoresParaComparacao'] = [{'codigoTributo': 1007, 'valor': 1}]
    assert svc.hash_declaracao(dados) == svc.hash_declaracao(outro)


def test_lancamento_nao_conferido_bloqueia_sem_chamar(svc):
    from app.escritorio_models import EscritorioLancamento
    EscritorioLancamento.query.update({'ok': False})
    db.session.commit()
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd) as exc:
        svc.calcular(contexto(svc), cliente(fake))
    assert 'OK' in ' '.join(exc.value.bloqueios)
    assert fake.chamadas == [] and chamadas_auditadas() == [] and custo_registrado() == 0


def test_teto_de_gasto_bloqueia_antes(svc, monkeypatch):
    from app.services.limite_gasto_service import LimiteGastoService
    monkeypatch.setattr(LimiteGastoService, 'pode_gastar', staticmethod(lambda custo=0: (False, 'teto atingido')))
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd):
        svc.calcular(contexto(svc), cliente(fake))
    assert fake.chamadas == []


# ----------------------------------------------------------------------- calcular
def test_calcular_guarda_valores_hash_e_custo(svc):
    fake = FakeSerpro(r_calculo())
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is True
    assert fake.chamadas[0]['dados']['indicadorTransmissao'] is False
    est = r['estado']
    assert est['situacao'] == 'calculada' and est['total_devido'] == 200.75 and est['calculo_valido']
    assert est['pode']['transmitir'] is True and est['pode']['gerar_das'] is False
    [aud] = chamadas_auditadas()
    assert aud.id_servico == 'TRANSDECLARACAO11' and aud.cobravel and aud.sucesso
    assert custo_registrado() > 0


def test_calcular_sem_valores_devidos_nao_valida(svc):
    fake = FakeSerpro((200, {'mensagens': ok_msg(), 'dados': json.dumps({'idDeclaracao': None})}))
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is False and r['estado']['calculo_valido'] is False


def test_sem_conexao_nao_cobra(svc):
    fake = FakeSerpro(requests.exceptions.ConnectTimeout('sem rede'))
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is False and r['serpro']['cobravel'] is False
    assert custo_registrado() == 0


def test_token_recusado_renova_uma_unica_vez(svc):
    fake = FakeSerpro((401, {}), (401, {}))
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is False and len(fake.chamadas) == 2
    assert custo_registrado() == 0


def test_entrada_incorreta_nao_trava_procuracao(svc):
    from app.services.procuracao_service import ProcuracaoService
    for _ in range(3):
        fake = FakeSerpro(r_msg('EntradaIncorreta-PGDASD-MSG_ISN_010', 'campo inválido', 400))
        svc.calcular(contexto(svc), cliente(fake))
    pode, _motivo = ProcuracaoService.pode_gastar(contexto(svc).company)
    assert pode is True


# ---------------------------------------------------------------------- transmitir
def test_transmitir_exige_calculo(svc):
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd) as exc:
        svc.transmitir(contexto(svc), cliente=cliente(fake))
    assert 'Calcule' in ' '.join(exc.value.bloqueios) and fake.chamadas == []


def test_transmitir_bloqueia_se_lancamento_mudou(svc):
    from app.escritorio_models import EscritorioLancamento
    svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    EscritorioLancamento.query.update({'rec_sem_st': 4100.0, 'total_receita': 6149.52})
    db.session.commit()
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd) as exc:
        svc.transmitir(contexto(svc), cliente=cliente(fake))
    assert 'mudou' in ' '.join(exc.value.bloqueios) and fake.chamadas == []


def test_transmitir_bloqueia_tela_desatualizada(svc):
    svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd):
        svc.transmitir(contexto(svc), hash_confirmado='hash-velho', cliente=cliente(fake))
    assert fake.chamadas == []


def test_fluxo_completo_transmite_e_gera_das_com_pdf(svc):
    r = svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    h = r['estado']['hash_calculo']

    fake = FakeSerpro(r_consulta(), r_transmissao())
    r = svc.transmitir(contexto(svc), hash_confirmado=h, cliente=cliente(fake))
    assert r['ok'] is True, r
    assert fake.servicos() == ['CONSDECLARACAO13', 'TRANSDECLARACAO11']
    enviado = fake.chamadas[1]['dados']
    assert enviado['indicadorTransmissao'] is True and enviado['indicadorComparacao'] is True
    assert enviado['declaracao']['tipoDeclaracao'] == 1
    assert {v['codigoTributo']: v['valor'] for v in enviado['valoresParaComparacao']} == {1007: 120.5, 1006: 80.25}
    est = r['estado']
    assert est['situacao'] == 'transmitida' and est['arquivos']['declaracao'] and est['arquivos']['recibo']
    assert est['lancamento_transmitido'] == 1

    # repetir a transmissão é bloqueado de graça
    with pytest.raises(svc.BloqueioPgdasd):
        svc.transmitir(contexto(svc), hash_confirmado=h, cliente=cliente(FakeSerpro()))

    fake = FakeSerpro(r_das())
    r = svc.gerar_das(contexto(svc), cliente=cliente(fake))
    assert r['ok'] is True and r['estado']['das']['tem_pdf'] and r['estado']['das']['valor'] == 200.75
    ctx = contexto(svc)
    caminho = svc.caminho_arquivo(ctx, 'das')
    assert caminho and open(caminho, 'rb').read().startswith(b'%PDF')

    # baixar/gerar de novo reaproveita o PDF: nenhuma chamada
    fake = FakeSerpro()
    r = svc.gerar_das(contexto(svc), cliente=cliente(fake))
    assert r['reuso'] is True and fake.chamadas == []


def test_consulta_previa_encontra_declaracao_externa_e_nao_transmite(svc):
    svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    fake = FakeSerpro(r_consulta(['00000000202608009']))
    r = svc.transmitir(contexto(svc), cliente=cliente(fake))
    assert r['ok'] is False and fake.servicos() == ['CONSDECLARACAO13']
    assert r['estado']['situacao'] == 'transmitida' and r['estado']['lancamento_transmitido'] == 2
    assert r['estado']['pode']['gerar_das'] is True


def test_msg_isn_035_invalida_calculo(svc):
    svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    fake = FakeSerpro(r_consulta(), r_msg('Erro-PGDASD-MSG_ISN_035', 'Valores divergentes'))
    r = svc.transmitir(contexto(svc), cliente=cliente(fake))
    assert r['ok'] is False
    assert r['estado']['calculo_valido'] is False and r['estado']['situacao'] == 'rascunho'


def test_timeout_na_transmissao_fica_incerto_ate_consultar(svc):
    svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    fake = FakeSerpro(r_consulta(), requests.exceptions.ReadTimeout('demorou'))
    r = svc.transmitir(contexto(svc), cliente=cliente(fake))
    assert r['ok'] is False and r['serpro']['incerto'] and r['estado']['situacao'] == 'incerta'

    for operacao in (lambda: svc.transmitir(contexto(svc), cliente=cliente(FakeSerpro())),
                     lambda: svc.calcular(contexto(svc), cliente(FakeSerpro())),
                     lambda: svc.gerar_das(contexto(svc), cliente=cliente(FakeSerpro()))):
        with pytest.raises(svc.BloqueioPgdasd):
            operacao()

    r = svc.consultar(contexto(svc), cliente(FakeSerpro(r_consulta(['00000000202608001']))))
    assert r['estado']['situacao'] == 'transmitida' and r['estado']['lancamento_transmitido'] == 1


def test_timeout_e_consulta_vazia_libera_nova_tentativa(svc):
    svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    svc.transmitir(contexto(svc), cliente=cliente(FakeSerpro(r_consulta(), requests.exceptions.ReadTimeout('x'))))
    r = svc.consultar(contexto(svc), cliente(FakeSerpro(r_consulta())))
    assert r['estado']['situacao'] == 'calculada' and r['estado']['pode']['transmitir'] is True


# --------------------------------------------------------------------------- DAS
def test_gerar_das_sem_declaracao_bloqueia_de_graca(svc):
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd) as exc:
        svc.gerar_das(contexto(svc), cliente=cliente(fake))
    assert 'MSG_ISN_005' in ' '.join(exc.value.bloqueios) and fake.chamadas == []


def test_gerar_das_msg_isn_005(svc):
    fake = FakeSerpro(r_msg('Aviso-PGDASD-MSG_ISN_005', 'Não existe declaração'))
    r = svc.gerar_das(contexto(svc), confirmar_externa=True, cliente=cliente(fake))
    assert r['ok'] is False and 'NÃO há declaração' in r['mensagem']


def test_das_vencido_pede_data_de_consolidacao(svc):
    from app.escritorio_models import EscritorioDeclaracao
    ctx = contexto(svc)
    ctx.decl.situacao = 'transmitida'
    db.session.commit()
    svc.gerar_das(ctx, cliente=cliente(FakeSerpro(r_das(vencimento='20200120'))))
    with pytest.raises(svc.BloqueioPgdasd) as exc:
        svc.gerar_das(contexto(svc), cliente=cliente(FakeSerpro()))
    assert exc.value.status == 409
    amanha = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
    fake = FakeSerpro(r_das(numero='07202625899999999'))
    r = svc.gerar_das(contexto(svc), data_consolidacao=amanha, cliente=cliente(fake))
    assert r['ok'] and fake.chamadas[0]['dados']['dataConsolidacao'] == amanha.replace('-', '')
    assert EscritorioDeclaracao.query.first().das_numero == '07202625899999999'


# ------------------------------------------------------------------------ travas
def test_trava_impede_operacao_simultanea(svc):
    ctx = contexto(svc)
    ctx.decl.operacao, ctx.decl.operacao_desde, ctx.decl.operacao_por = 'transmitir', datetime.utcnow(), 'outro'
    db.session.commit()
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd):
        svc.calcular(contexto(svc), cliente(fake))
    with pytest.raises(svc.BloqueioPgdasd) as exc:
        svc._travar(contexto(svc), 'calcular')
    assert exc.value.status == 409 and fake.chamadas == []


def test_trava_expirada_e_liberada(svc):
    ctx = contexto(svc)
    ctx.decl.operacao, ctx.decl.operacao_desde = 'calcular', datetime.utcnow() - timedelta(hours=1)
    db.session.commit()
    r = svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    assert r['ok'] and r['estado']['operacao'] is None


# -------------------------------------------------------------------------- lote
def test_lote_para_apos_duas_falhas_sistemicas(svc, app, monkeypatch):
    from app.escritorio_models import EscritorioDeclaracao
    from app.models import Company
    cnpjs = ['11222333000181', '11444777000161', CNPJ]
    for c in cnpjs[:2]:
        db.session.add(Company(razao_social=f'EMPRESA {c}', cnpj=c, ativo=True))
    db.session.commit()
    for c in cnpjs:
        ctx = svc.carregar(c, COMP, 'teste')
        ctx.decl.situacao = 'transmitida'
    db.session.commit()
    fake = FakeSerpro((503, {}), (503, {}), (503, {}))
    monkeypatch.setattr(svc, '_novo_cliente', lambda c=None: cliente(fake))
    lote = svc.iniciar_lote(app, 'gerar_das', [{'cnpj': c, 'competencia': COMP} for c in cnpjs], 'teste')
    for _ in range(100):
        estado = svc.status_lote(lote['id'])
        if estado['status'] != 'rodando':
            break
        time.sleep(0.05)
    assert estado['status'] == 'interrompido'
    assert len(fake.chamadas) == 2 and estado['feitos'] == 2
    assert EscritorioDeclaracao.query.filter(EscritorioDeclaracao.das_arquivo.isnot(None)).count() == 0


# ------------------------------------------------------------------------- rotas
@pytest.fixture()
def http(app, svc):
    c = app.test_client()
    with c.session_transaction() as sessao:
        sessao['usuario_id'] = 'ambiente'
    return c


def test_rota_estado_e_confirmacao_de_custo(http, svc, monkeypatch):
    r = http.get(f'/escritorio/api/pgdasd/estado?cnpj={CNPJ}&competencia={COMP}')
    assert r.status_code == 200 and r.json['estado']['situacao'] == 'rascunho'

    fake = FakeSerpro(r_calculo())
    monkeypatch.setattr(svc, '_novo_cliente', lambda c=None: cliente(fake))
    r = http.post('/escritorio/api/pgdasd/calcular', json={'cnpj': CNPJ, 'competencia': COMP})
    assert r.status_code == 428 and fake.chamadas == []
    r = http.post('/escritorio/api/pgdasd/calcular', json={'cnpj': CNPJ, 'competencia': COMP, 'confirmar_custo': True})
    assert r.status_code == 200 and r.json['ok'] and len(fake.chamadas) == 1


def test_rota_bloqueio_vira_422_e_pdf_guardado_e_servido(http, svc, monkeypatch):
    r = http.post('/escritorio/api/pgdasd/gerar-das', json={'cnpj': CNPJ, 'competencia': COMP, 'confirmar_custo': True})
    assert r.status_code == 422 and r.json['bloqueios']

    ctx = contexto(svc)
    ctx.decl.situacao = 'transmitida'
    db.session.commit()
    fake = FakeSerpro(r_das())
    monkeypatch.setattr(svc, '_novo_cliente', lambda c=None: cliente(fake))
    r = http.post('/escritorio/api/pgdasd/gerar-das', json={'cnpj': CNPJ, 'competencia': COMP, 'confirmar_custo': True})
    assert r.status_code == 200 and r.json['ok']
    r = http.get(f'/escritorio/api/pgdasd/arquivo/das?cnpj={CNPJ}&competencia={COMP}')
    assert r.status_code == 200 and r.data.startswith(b'%PDF')
    r = http.get(f'/escritorio/api/pgdasd/das-zip?competencia={COMP}')
    assert r.status_code == 200 and r.data[:2] == b'PK'
    r = http.get(f'/escritorio/api/pgdasd/arquivo/..%2F..%2Fsecret?cnpj={CNPJ}&competencia={COMP}')
    assert r.status_code == 404


# ------------------------------------------------------------------- certificado
def test_certificado_senha_errada_e_vencido(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
    from cryptography.x509.oid import NameOID
    from app.services import escritorio_pgdasd as svc

    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'teste')])
    agora = datetime.utcnow()
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(1).not_valid_before(agora - timedelta(days=10))
            .not_valid_after(agora + timedelta(days=5)).sign(chave, hashes.SHA256()))
    pfx = tmp_path / 'a1.pfx'
    pfx.write_bytes(pkcs12.serialize_key_and_certificates(b'a1', chave, cert, None, BestAvailableEncryption(b'certa')))
    assert svc._checar_certificado(str(pfx), 'errada')['ok'] is False
    info = svc._checar_certificado(str(pfx), 'certa')
    assert info['ok'] is True and info['valido_ate']


# ------------------------------------------------------- cliente com o payload real
def test_cliente_real_monta_envelope_e_reaproveita_token(svc, monkeypatch):
    from app.services import serpro_pgdasd_client as mod
    from app.services.serpro_das_service import SerproDasService
    geracoes = []
    monkeypatch.setattr(SerproDasService, '_gerar_headers_a1',
                        lambda self, setting: geracoes.append(1) or
                        {'Authorization': 'Bearer t', 'jwt_token': 'j'})
    mod.CACHE_TOKEN.invalidar()
    fake = FakeSerpro(r_consulta(), r_consulta())
    c = mod.SerproPgdasdClient(transporte=fake)
    for _ in range(2):
        r = c.chamar('CONSDECLARACAO13', CNPJ, {'periodoApuracao': PA}, operacao='consultar')
        assert r.ok
    assert len(geracoes) == 1                                  # token reaproveitado
    envio = fake.chamadas[0]
    assert envio['url'].endswith('/integra-contador/v1/Consultar')
    p = envio['payload']
    assert p['contratante']['numero'] == '11222333000181' and p['contribuinte']['numero'] == CNPJ
    assert p['pedidoDados'] == {'idSistema': 'PGDASD', 'idServico': 'CONSDECLARACAO13', 'versaoSistema': '1.0',
                                'dados': json.dumps({'periodoApuracao': PA})}
    mod.CACHE_TOKEN.invalidar()


# ------------------------------------------------- correções 15/09 (fora do fluxo PGDAS-D)
def test_procurador_ligado_falha_antes_do_envio_e_nao_trava(svc):
    from app.models import AppSetting
    from app.services.procuracao_service import ProcuracaoService
    from app.services.serpro_das_service import SerproDasService
    from app.services.serpro_erros import ErroAntesDoEnvio
    s = AppSetting.query.first()
    s.procurador_pf_habilitado = True
    db.session.commit()
    with pytest.raises(ErroAntesDoEnvio):
        SerproDasService()._get_headers(s)
    with pytest.raises(ErroAntesDoEnvio):
        SerproDasService().montar_payload_emitir(s, CNPJ, 'PGDASD', 'GERARDAS12', {'periodoApuracao': PA})
    empresa = contexto(svc).company
    for _ in range(3):
        try:
            SerproDasService()._get_headers(s)
        except ErroAntesDoEnvio as exc:
            ProcuracaoService.registrar_erro(empresa, 'GERARDAS12', exc)
    assert ProcuracaoService.pode_gastar(empresa)[0] is True


def test_das_service_reaproveita_token_e_renova_uma_vez(svc, monkeypatch):
    from app.models import AppSetting
    from app.services import serpro_das_service as mod
    from app.services.serpro_pgdasd_client import CACHE_TOKEN
    geracoes, posts = [], []
    monkeypatch.setattr(mod.SerproDasService, '_gerar_headers_a1',
                        lambda self, setting: geracoes.append(1) or {'Authorization': f'Bearer {len(geracoes)}'})
    respostas = [SimpleNamespace(status_code=401, text='', json=lambda: {}),
                 SimpleNamespace(status_code=200, text='{}', json=lambda: {'dados': '[]'})]
    monkeypatch.setattr(mod, 'serpro_post', lambda url, headers=None, **kw: posts.append(headers) or respostas.pop(0))
    CACHE_TOKEN.invalidar()
    s = AppSetting.query.first()
    servico = mod.SerproDasService()
    servico._get_headers(s)
    servico._get_headers(s)
    assert len(geracoes) == 1
    servico.emitir(s, {'contribuinte': {'numero': CNPJ}})
    assert len(posts) == 2 and len(geracoes) == 2          # 401 → renova e tenta 1 vez
    CACHE_TOKEN.invalidar()


def test_emitir_das_avulso_respeita_teto_e_registra_custo(http, svc, monkeypatch):
    from app.services import serpro_das_service as mod
    from app.services.limite_gasto_service import LimiteGastoService
    chamadas = []
    monkeypatch.setattr(mod.SerproDasService, 'emitir_pdf', lambda self, **kw: chamadas.append(kw) or b'%PDF-1.4')
    monkeypatch.setattr(LimiteGastoService, 'pode_gastar', staticmethod(lambda custo=0: (False, 'teto')))
    r = http.post('/api/das/emitir', json={'cnpj': CNPJ, 'periodo_apuracao': PA})
    assert r.status_code == 409 and chamadas == []
    monkeypatch.setattr(LimiteGastoService, 'pode_gastar', staticmethod(lambda custo=0: (True, None)))
    r = http.post('/api/das/emitir', json={'cnpj': CNPJ, 'periodo_apuracao': PA})
    assert r.status_code == 200 and len(chamadas) == 1 and custo_registrado() > 0


def r_ultima_declaracao(numero='00000000202608001'):
    return 200, {'status': 200, 'mensagens': ok_msg(), 'dados': json.dumps({
        'numeroDeclaracao': numero, 'recibo': {'nomeArquivo': 'recibo.pdf', 'pdf': PDF},
        'declaracao': {'nomeArquivo': 'declaracao.pdf', 'pdf': PDF}, 'maed': None})}


def test_recuperar_documentos_guarda_pdfs_e_nao_repete(svc):
    with pytest.raises(svc.BloqueioPgdasd):                     # sem declaração conhecida
        svc.recuperar_documentos(contexto(svc), cliente=cliente(FakeSerpro()))
    ctx = contexto(svc)
    ctx.decl.situacao = 'transmitida'
    db.session.commit()
    fake = FakeSerpro(r_ultima_declaracao())
    r = svc.recuperar_documentos(contexto(svc), cliente=cliente(fake))
    assert r['ok'] and fake.servicos() == ['CONSULTIMADECREC14']
    assert r['estado']['arquivos']['declaracao'] and r['estado']['arquivos']['recibo']
    assert r['estado']['pode']['recuperar'] is False
    with pytest.raises(svc.BloqueioPgdasd):                     # já guardados: não paga de novo
        svc.recuperar_documentos(contexto(svc), cliente=cliente(FakeSerpro()))


# ------------------------------------- recusa "Campo inválido" (caso real 15/09/2026)
def test_recusa_de_preenchimento_nao_repete_pedido_identico_nem_trava_procuracao(svc):
    from app.services.procuracao_service import ProcuracaoService
    recusa = r_msg('Erro-SNENTREGAR', "SN-Entregar: Campo 'isencao/identificacao' inválido.", 400)
    fake = FakeSerpro(recusa)
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is False and len(fake.chamadas) == 1

    # mesmo lançamento = mesmo JSON: bloqueado sem chamar e sem custo
    custo_antes = custo_registrado()
    fake = FakeSerpro()
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is False and fake.chamadas == [] and 'já foi recusado' in r['serpro']['mensagem']
    assert custo_registrado() == custo_antes

    # erro de preenchimento não conta para a trava de procuração
    assert ProcuracaoService.pode_gastar(contexto(svc).company)[0] is True

    # corrigiu o lançamento (JSON diferente): pode enviar
    from app.escritorio_models import EscritorioLancamento
    EscritorioLancamento.query.update({'rec_sem_st': 3900.0, 'rec_monofasica': 2149.52})
    db.session.commit()
    fake = FakeSerpro(r_calculo())
    assert svc.calcular(contexto(svc), cliente(fake))['ok'] is True and len(fake.chamadas) == 1


def test_recusa_meses_anteriores_pode_repetir_mesmo_json(svc):
    """SERPRO usa EntradaIncorreta também quando faltam 06/07 — situação muda sem mudar o JSON."""
    recusa = r_msg('EntradaIncorreta',
                   'SN-Entregar: É necessário transmitir as seguintes declarações: 06/2026 e 07/2026.',
                   400)
    fake = FakeSerpro(recusa)
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is False and len(fake.chamadas) == 1

    # mesmos dados: pode chamar de novo depois que 06/07 foram transmitidos (aqui: confirmado
    # pelo usuário; sem confirmação o pré-voo bloqueia de graça — ver teste de ordem dos PAs)
    fake = FakeSerpro(r_calculo())
    r = svc.calcular(contexto(svc), cliente(fake), confirmar_pendencias=True)
    assert r['ok'] is True and len(fake.chamadas) == 1 and 'já foi recusado' not in (r.get('serpro') or {}).get('mensagem', '')


# ------------------- "Houve um problema na transmissão" (caso real 15/09/2026, 17:55)
def test_problema_na_transmissao_obriga_consulta_antes_de_repetir(svc):
    r = svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    h = r['estado']['hash_calculo']
    falha = r_msg('Erro-SNENTREGAR', 'SN-Entregar: Houve um problema na transmissão. Tente novamente mais tarde.')
    fake = FakeSerpro(r_consulta(), falha)
    r = svc.transmitir(contexto(svc), hash_confirmado=h, cliente=cliente(fake))
    assert r['ok'] is False and r['serpro']['sistemico'] is True
    assert r['estado']['situacao'] == 'calculada' and r['estado']['consultado_em'] is None
    assert any('consulta antes' in a for a in r['avisos'])

    # nova tentativa: consulta primeiro (mesmo com consulta recente antes da falha) e só então transmite
    fake = FakeSerpro(r_consulta(), r_transmissao())
    r = svc.transmitir(contexto(svc), hash_confirmado=h, cliente=cliente(fake))
    assert r['ok'] is True and fake.servicos() == ['CONSDECLARACAO13', 'TRANSDECLARACAO11']


def test_problema_na_transmissao_mas_declaracao_entrou_nao_retransmite(svc):
    r = svc.calcular(contexto(svc), cliente(FakeSerpro(r_calculo())))
    h = r['estado']['hash_calculo']
    falha = r_msg('Erro-SNENTREGAR', 'SN-Entregar: Houve um problema na transmissão. Tente novamente mais tarde.')
    svc.transmitir(contexto(svc), hash_confirmado=h, cliente=cliente(FakeSerpro(r_consulta(), falha)))
    fake = FakeSerpro(r_consulta(['00000000202608001']))
    r = svc.transmitir(contexto(svc), hash_confirmado=h, cliente=cliente(fake))
    assert r['ok'] is False and fake.servicos() == ['CONSDECLARACAO13']
    assert r['estado']['situacao'] == 'transmitida'



# -------------------- 1ª declaração no Simples / ordem dos PAs (caso real 15/09/2026)
def _datas(inicio_atividade=None, inicio_simples=None):
    from app.escritorio_models import EscritorioEmpresa
    from app.models import Company
    emp = EscritorioEmpresa.query.filter_by(company_id=Company.query.filter_by(cnpj=CNPJ).first().id).first()
    emp.inicio_atividade, emp.inicio_simples = inicio_atividade, inicio_simples
    db.session.commit()


def _historico(meses_valores):
    from app.escritorio_models import EscritorioPgdasHistorico
    for comp, valor in meses_valores.items():
        db.session.add(EscritorioPgdasHistorico(cnpj=CNPJ, competencia=comp, receita_bruta=valor))
    db.session.commit()


def test_optante_ha_12_meses_nao_exige_receitas_anteriores(svc):
    dados, bloqueios, _a = svc.montar_declaracao(contexto(svc))
    assert bloqueios == [] and 'receitasBrutasAnteriores' not in dados['declaracao']


def test_sem_datas_com_movimento_cobra_extrato(svc):
    _datas(None, None)
    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd) as exc:
        svc.calcular(contexto(svc), cliente(fake))
    texto = ' '.join(exc.value.bloqueios)
    assert 'Receitas brutas anteriores obrigatórias' in texto and '08/2025' in texto and fake.chamadas == []


def test_primeira_declaracao_exige_so_meses_antes_da_opcao_e_usa_extrato(svc):
    _datas('2025-01', '2026-06')          # abriu em 01/2025, entrou no Simples em 06/2026
    ctx = contexto(svc)
    _d, bloqueios, _a = svc.montar_declaracao(ctx)
    assert ctx.rba['exigidos'] == ['2025-08', '2025-09', '2025-10', '2025-11', '2025-12', '2026-01',
                                   '2026-02', '2026-03', '2026-04', '2026-05']
    assert bloqueios                        # com movimento e sem espelho → bloqueia

    _historico({m: 1000.0 for m in ctx.rba['exigidos']})
    ctx = contexto(svc)
    dados, bloqueios, avisos = svc.montar_declaracao(ctx)
    assert bloqueios == []
    rba = {r['pa']: r['valorInterno'] for r in dados['declaracao']['receitasBrutasAnteriores']}
    assert rba[202508] == 1000.0 and 202607 not in rba
    assert any('Ordem dos PAs' in a and '06/2026' in a and '07/2026' in a for a in avisos)


def test_abertura_no_proprio_pa_dispensa_receitas_anteriores(svc):
    _datas('2026-08', '2026-08')
    ctx = contexto(svc)
    dados, bloqueios, _a = svc.montar_declaracao(ctx)
    assert bloqueios == [] and ctx.rba['exigidos'] == [] and 'receitasBrutasAnteriores' not in dados['declaracao']


def test_abertura_recente_so_exige_meses_desde_a_abertura(svc):
    _datas('2026-03', '2026-06')           # abriu 03/2026, opção em 06/2026
    ctx = contexto(svc)
    svc.montar_declaracao(ctx)
    assert ctx.rba['exigidos'] == ['2026-03', '2026-04', '2026-05']


def test_sem_movimento_nao_bloqueia_mas_avisa(svc):
    from app.escritorio_models import EscritorioLancamento
    EscritorioLancamento.query.update({'total_receita': 0, 'rec_sem_st': 0, 'rec_monofasica': 0})
    db.session.commit()
    _datas(None, None)
    _d, bloqueios, avisos = svc.montar_declaracao(contexto(svc))
    assert bloqueios == [] and any('sem movimento' in a for a in avisos)


def test_lancamento_ok_serve_de_espelho_com_aviso(svc):
    from app.escritorio_models import EscritorioLancamento
    _datas('2026-05', '2026-07')           # exige 05/2026 e 06/2026
    for comp in ('2026-05', '2026-06'):
        db.session.add(EscritorioLancamento(cnpj=CNPJ, competencia=comp, perfil='comercio',
                                            total_receita=500.0, rec_sem_st=500.0, ok=True))
    db.session.commit()
    ctx = contexto(svc)
    _d, bloqueios, avisos = svc.montar_declaracao(ctx)
    assert bloqueios == [] and ctx.rba['de_lancamento'] == ['2026-05', '2026-06']
    assert any('vêm dos lançamentos' in a for a in avisos)


def test_recusa_por_ordem_bloqueia_de_graca_ate_transmitir_ou_confirmar(svc):
    recusa = r_msg('Erro-SNENTREGAR', 'SN-Entregar: É necessário transmitir as seguintes declarações: 07/2026.')
    fake = FakeSerpro(recusa)
    r = svc.calcular(contexto(svc), cliente(fake))
    assert r['ok'] is False and len(fake.chamadas) == 1
    assert r['estado']['pendencias_receita'] == ['2026-07']

    fake = FakeSerpro()
    with pytest.raises(svc.BloqueioPgdasd) as exc:            # repetir sem resolver: grátis, bloqueado
        svc.calcular(contexto(svc), cliente(fake))
    assert '07/2026' in ' '.join(exc.value.bloqueios) and fake.chamadas == []

    fake = FakeSerpro(r_calculo())                              # usuário confirma que transmitiu fora
    assert svc.calcular(contexto(svc), cliente(fake), confirmar_pendencias=True)['ok'] is True


def test_recusa_por_ordem_liberada_quando_mes_anterior_transmitido_no_central(svc):
    from app.escritorio_models import EscritorioLancamento
    svc.calcular(contexto(svc), cliente(FakeSerpro(
        r_msg('Erro-SNENTREGAR', 'SN-Entregar: É necessário transmitir as seguintes declarações: 07/2026.'))))
    db.session.add(EscritorioLancamento(cnpj=CNPJ, competencia='2026-07', perfil='comercio',
                                        total_receita=1.0, rec_sem_st=1.0, ok=True, transmitido=1))
    db.session.commit()
    fake = FakeSerpro(r_calculo())
    assert svc.calcular(contexto(svc), cliente(fake))['ok'] is True and len(fake.chamadas) == 1


def test_rota_datas_inicio(http, svc):
    r = http.post('/escritorio/api/pgdasd/datas-inicio',
                  json={'cnpj': CNPJ, 'competencia': COMP, 'inicio_atividade': '2026-08', 'inicio_simples': '2026-08'})
    assert r.status_code == 200 and r.json['estado']['rba']['exigidos'] == []
    r = http.post('/escritorio/api/pgdasd/datas-inicio',
                  json={'cnpj': CNPJ, 'competencia': COMP, 'inicio_atividade': '2026-08', 'inicio_simples': '2026-01'})
    assert r.status_code == 400


# ------------------------- empresa sem NF na competência (abriu 05/06/2026, 16/09/2026)
def test_incluir_empresa_sem_nf_cria_lancamento_zerado_e_declara_sem_movimento(http, svc):
    from app.escritorio_models import EscritorioLancamento
    _datas('2026-06', '2026-06')
    r = http.get('/escritorio/simples/transmitir?competencia=2026-06')
    assert 'Incluir empresa sem NF (1)' in r.get_data(as_text=True)

    empresa_id = contexto(svc).company.id
    r = http.post('/escritorio/api/lancamentos/incluir',
                  json={'company_id': empresa_id, 'competencia': '2026-06', 'sem_movimento_conferido': True})
    assert r.status_code == 200 and r.json['ok']
    lanc = EscritorioLancamento.query.filter_by(cnpj=CNPJ, competencia='2026-06').one()
    assert lanc.ok is True and lanc.total_receita == 0

    r = http.post('/escritorio/api/lancamentos/incluir', json={'company_id': empresa_id, 'competencia': '2026-06'})
    assert r.status_code == 409                                   # não duplica
    r = http.post('/escritorio/api/lancamentos/incluir', json={'company_id': empresa_id, 'competencia': '2099-01'})
    assert r.status_code == 400                                   # competência futura

    ctx = svc.carregar(CNPJ, '2026-06', 'teste')
    dados, bloqueios, avisos = svc.montar_declaracao(ctx)
    assert bloqueios == [] and ctx.rba['exigidos'] == []           # abriu no próprio PA: nada anterior
    assert dados['declaracao']['receitaPaCompetenciaInterno'] == 0
    assert dados['declaracao']['estabelecimentos'] == [{'cnpjCompleto': CNPJ}]
    assert any('SEM MOVIMENTO' in a for a in avisos)
