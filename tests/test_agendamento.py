"""Cálculo de vencimento do agendamento (sem rede, sem custo).

Caso real que motivou: situação fiscal ligada (mensal, dia 25, 03:00, adiar para dia
útil) e nunca executada pelo agendador — não disparou em 26/10/2026 (25/10 era domingo)
e a tela já mostrava 25/11 como próxima.

Rodar:  python -m pytest tests/test_agendamento.py -q
"""
import os
import tempfile
from datetime import datetime

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='agenda_teste_')
os.environ['SCHEDULER_ENABLED'] = '0'

from app.services import agendamento_service as ag  # noqa: E402


def _situacao_fiscal(**extra):
    config = dict(ag.MODULOS['situacao_fiscal']['padrao'])
    config.update(extra)
    return config


def _em(texto):
    return datetime.fromisoformat(texto)


def test_nunca_rodou_dispara_no_dia_util_seguinte_ao_25():
    config = _situacao_fiscal(ativo_desde='2026-10-02T16:00:00')
    assert not ag.esta_vencida(config, _em('2026-10-25T03:05'))  # domingo: adia
    assert not ag.esta_vencida(config, _em('2026-10-26T02:59'))
    assert ag.esta_vencida(config, _em('2026-10-26T03:05'))      # segunda, 03:00


def test_rodada_perdida_roda_uma_vez_na_volta():
    # estado real da produção: ligada, sem ultima_execucao e sem ativo_desde
    config = _situacao_fiscal()
    assert ag.esta_vencida(config, _em('2026-10-30T12:00'))
    assert ag.ocorrencia_anterior(config, _em('2026-10-30T12:00')) == _em('2026-10-26T03:00')

    config['ultima_execucao'] = '2026-10-30T12:05:00'
    assert not ag.esta_vencida(config, _em('2026-11-24T12:00'))
    assert ag.proxima_execucao(config, _em('2026-10-30T12:05')) == _em('2026-11-25T03:00')
    assert ag.esta_vencida(config, _em('2026-11-25T03:05'))


def test_com_execucao_anterior_segue_o_calendario():
    config = _situacao_fiscal(ultima_execucao='2026-09-25T03:01:00')
    assert not ag.esta_vencida(config, _em('2026-10-26T02:00'))
    assert ag.esta_vencida(config, _em('2026-10-26T03:00'))


def test_ligada_agora_nao_cobra_horario_que_ja_passou():
    config = _situacao_fiscal(ativo_desde='2026-10-28T10:00:00')
    assert not ag.esta_vencida(config, _em('2026-10-30T12:00'))
    assert ag.esta_vencida(config, _em('2026-11-25T03:00'))


def test_desligada_nunca_vence():
    assert not ag.esta_vencida(_situacao_fiscal(ativo=False), _em('2026-10-30T12:00'))


def test_semanal_nunca_rodou():
    config = {'ativo': True, 'frequencia': 'semanal', 'dia_semana': 0, 'hora': '04:00',
              'ativo_desde': '2026-10-22T10:00:00'}
    assert not ag.esta_vencida(config, _em('2026-10-26T03:59'))  # segunda antes da hora
    assert ag.esta_vencida(config, _em('2026-10-26T04:00'))
    config['ultima_execucao'] = '2026-10-26T04:01:00'
    assert not ag.esta_vencida(config, _em('2026-11-01T23:00'))
    assert ag.esta_vencida(config, _em('2026-11-02T04:00'))


def test_virada_de_ano():
    config = _situacao_fiscal(dia_mes=5, ajustar_dia_util=False)
    assert ag.ocorrencia_anterior(config, _em('2027-01-03T00:00')) == _em('2026-12-05T03:00')


def test_ligar_pela_tela_grava_ativo_desde_e_bloqueio_aparece():
    ag.atualizar_modulo('parcelamentos', {'ativo': True})
    resumo = {m['modulo']: m for m in ag.resumo()['modulos']}
    assert ag.carregar()['modulos']['parcelamentos'].get('ativo_desde')
    assert not resumo['parcelamentos']['atrasada']

    ag.registrar_bloqueio('situacao_fiscal', 'teto atingido')
    resumo = {m['modulo']: m for m in ag.resumo()['modulos']}
    assert resumo['situacao_fiscal']['ultimo_bloqueio']['motivo'] == 'teto atingido'

    ag.registrar_execucao('situacao_fiscal', {'success': True})
    assert 'ultimo_bloqueio' not in ag.carregar()['modulos']['situacao_fiscal']
