"""Dias úteis e feriados nacionais — sem dependência externa.

⚠️ DESVIO INTENCIONAL (18o) — NÃO existe no exe. Usado pelo agendamento ("ajustar para
o próximo dia útil") e pela fila de reprocessamento ("regerar N dias úteis depois").

Só feriados NACIONAIS entram: os estaduais e municipais não afetam a Receita Federal,
e é o calendário da Receita que importa aqui. Os móveis (Carnaval, Sexta-feira Santa,
Corpus Christi) saem do cálculo da Páscoa (algoritmo de Meeus/Jones/Butcher), o mesmo
que o portal nescon-clientes usa em `diasBancarios.js` — os dois sistemas concordam.

Funções puras sobre `datetime.date`; nada aqui lê relógio nem banco.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import FrozenSet

# Feriados nacionais de data fixa (mês, dia).
_FIXOS = (
    (1, 1),    # Confraternização Universal
    (4, 21),   # Tiradentes
    (5, 1),    # Dia do Trabalho
    (9, 7),    # Independência
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),   # Finados
    (11, 15),  # Proclamação da República
    (11, 20),  # Consciência Negra (nacional desde 2024, Lei 14.759/2023)
    (12, 25),  # Natal
)


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mes = (h + m - 7 * n + 114) // 31
    dia = (h + m - 7 * n + 114) % 31 + 1
    return date(ano, mes, dia)


@lru_cache(maxsize=64)
def feriados_nacionais(ano: int) -> FrozenSet[date]:
    fixos = {date(ano, m, d) for m, d in _FIXOS if not (m == 11 and d == 20 and ano < 2024)}
    p = pascoa(ano)
    moveis = {
        p - timedelta(days=48),  # segunda de Carnaval
        p - timedelta(days=47),  # terça de Carnaval
        p - timedelta(days=2),   # Sexta-feira Santa
        p + timedelta(days=60),  # Corpus Christi
    }
    return frozenset(fixos | moveis)


def eh_feriado(dia: date) -> bool:
    return dia in feriados_nacionais(dia.year)


def eh_dia_util(dia: date) -> bool:
    """Segunda a sexta, fora dos feriados nacionais."""
    return dia.weekday() < 5 and not eh_feriado(dia)


def proximo_dia_util(dia: date) -> date:
    """O próprio dia, se for útil; senão o primeiro útil depois dele."""
    atual = dia
    for _ in range(31):
        if eh_dia_util(atual):
            return atual
        atual += timedelta(days=1)
    return atual  # inalcançável na prática; evita laço infinito


def somar_dias_uteis(dia: date, quantidade: int) -> date:
    """`quantidade` dias úteis DEPOIS de `dia` (o próprio dia não conta)."""
    atual = dia
    restantes = max(0, int(quantidade))
    while restantes > 0:
        atual += timedelta(days=1)
        if eh_dia_util(atual):
            restantes -= 1
    return atual


def dias_uteis_entre(inicio: date, fim: date) -> int:
    """Quantos dias úteis há em (inicio, fim] — exclui o início, inclui o fim."""
    if fim <= inicio:
        return 0
    total = 0
    atual = inicio
    while atual < fim:
        atual += timedelta(days=1)
        if eh_dia_util(atual):
            total += 1
    return total
