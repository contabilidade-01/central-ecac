"""Janela diurna de envio — porte de `api/src/janelaEnvio.js` do portal (19o desvio).

Toda mensagem ao cliente sai só em horário comercial de Brasília, em dia útil:
08:00 (inclusive) às 19:00 (exclusive). Funções puras sobre minutos desde a meia-noite
e sobre `date`, para testar sem mexer no relógio.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.calendario_util import eh_dia_util as _eh_dia_util

TZ = ZoneInfo('America/Sao_Paulo')
JANELA_INICIO_MIN = 8 * 60   # 08:00
JANELA_FIM_MIN = 19 * 60     # 19:00


def minutos_sp(agora: datetime | None = None) -> int:
    agora = agora or datetime.now(TZ)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=TZ)
    local = agora.astimezone(TZ)
    return local.hour * 60 + local.minute


def hoje_sp(agora: datetime | None = None) -> date:
    agora = agora or datetime.now(TZ)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=TZ)
    return agora.astimezone(TZ).date()


def dentro_da_janela(minutos: int) -> bool:
    return JANELA_INICIO_MIN <= minutos < JANELA_FIM_MIN


def eh_dia_util(dia: date | None = None) -> bool:
    """Segunda a sexta, sem feriado nacional (o portal só olha o dia da semana;
    aqui entra o feriado porque o calendário já existe)."""
    return _eh_dia_util(dia or hoje_sp())


def pode_enviar_agora(agora: datetime | None = None) -> bool:
    return dentro_da_janela(minutos_sp(agora)) and eh_dia_util(hoje_sp(agora))


def descricao_janela() -> str:
    def hh(m: int) -> str:
        return f'{m // 60:02d}:{m % 60:02d}'
    return f'{hh(JANELA_INICIO_MIN)}–{hh(JANELA_FIM_MIN)}'
