"""Cota diária de "confirmar situação fiscal": tentativa que falha não gasta a cota.

A chamada só é cobrada quando o relatório chega. Antes, dois erros seguidos
deixavam a empresa sem consulta pelo resto do dia sem ter gastado nada.

Rodar:  python -m pytest tests/test_confirmacao_cota.py -q
"""
import os
import tempfile
from datetime import datetime, timedelta

import pytest

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='cota_teste_')
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['LIMITE_GASTO_MENSAL'] = '0'
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['INTEGRACAO_MAX_CONFIRMACOES_DIA'] = '2'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.services import confirmacao_service as conf  # noqa: E402

CNPJ = '11222333000181'


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app.integracao_models import ConfirmacaoSitfis
        from app.models import Company
        ConfirmacaoSitfis.query.delete()
        Company.query.delete()
        db.session.add(Company(razao_social='ALFA', cnpj=CNPJ, ativo=True))
        db.session.commit()
        yield app


def _tentar(resultado):
    """Uma confirmação síncrona cujo processamento devolve `resultado`."""
    return conf.iniciar(CNPJ, 'teste', processar=lambda cid: resultado, em_thread=False)


FALHOU = (False, 'A Receita (SERPRO) respondeu com erro', None)
DEU_CERTO = (True, 'Relatório atualizado.', None)


def test_tentativa_que_falha_nao_gasta_a_cota(ambiente):
    _tentar(FALHOU)
    _tentar(FALHOU)
    # com a cota contando falhas, esta terceira seria recusada com 429
    r = _tentar(DEU_CERTO)
    assert r['status'] == 'concluida'


def test_sucesso_gasta_a_cota(ambiente):
    assert _tentar(DEU_CERTO)['status'] == 'concluida'
    assert _tentar(DEU_CERTO)['status'] == 'concluida'
    with pytest.raises(conf.ConfirmacaoBloqueada) as e:
        _tentar(DEU_CERTO)
    assert e.value.status == 429
    assert 'Limite de 2' in str(e.value)


def test_sucesso_e_falha_misturados_contam_so_o_sucesso(ambiente):
    _tentar(DEU_CERTO)
    _tentar(FALHOU)
    assert _tentar(DEU_CERTO)['status'] == 'concluida'
    with pytest.raises(conf.ConfirmacaoBloqueada):
        _tentar(DEU_CERTO)


def test_teto_segura_a_empresa_que_so_falha(ambiente):
    """Sem teto, uma SERPRO fora do ar viraria tentativa sem fim."""
    for _ in range(conf.teto_tentativas_dia()):
        _tentar(FALHOU)
    with pytest.raises(conf.ConfirmacaoBloqueada) as e:
        _tentar(FALHOU)
    assert e.value.status == 429
    assert 'tentou' in str(e.value)


def test_pedido_em_andamento_ainda_ocupa_a_vaga(ambiente):
    agora = datetime.utcnow()
    from app.integracao_models import ConfirmacaoSitfis
    c = ConfirmacaoSitfis(company_id=1, cnpj=CNPJ, iniciado_em=agora)
    assert conf.consumiu_cota(c, agora) is True


def test_pedido_abandonado_nao_ocupa_a_vaga(ambiente):
    """Processo que caiu: nunca gravou conclusão, não pode prender a cota."""
    agora = datetime.utcnow()
    from app.integracao_models import ConfirmacaoSitfis
    c = ConfirmacaoSitfis(company_id=1, cnpj=CNPJ,
                          iniciado_em=agora - conf.EM_ANDAMENTO_MAX - timedelta(minutes=1))
    assert conf.consumiu_cota(c, agora) is False
