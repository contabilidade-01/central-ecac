"""Disparo automático das rotinas agendadas.

⚠️ DESVIO INTENCIONAL (8o) — NÃO existe no exe.

Por que uma thread e não APScheduler/Celery
-------------------------------------------
O sistema roda com **1 worker** (estado em memória + SQLite, ver `wsgi.py`), então uma
thread daemon no próprio processo resolve sem dependência nova, sem broker e sem risco
de duas instâncias dispararem a mesma rotina. Ela acorda a cada `INTERVALO_S`, pergunta
ao `agendamento_service` o que está vencido e executa.

Ligar/desligar
--------------
* `SCHEDULER_ENABLED=1` liga (padrão no Docker).
* Fica DESLIGADO por padrão fora do Docker, para que rodar o sistema na sua máquina não
  dispare chamada paga sem você pedir.
* `SCHEDULER_INTERVALO_S` ajusta o intervalo de verificação (padrão 300 s).

Segurança
---------
Antes de qualquer execução valem as três travas do `agendamento_service`: mapa de
procurações, teto de gasto e lock de execução única.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

INTERVALO_PADRAO_S = 300

_thread: threading.Thread | None = None
_parar = threading.Event()


def habilitado() -> bool:
    return str(os.getenv('SCHEDULER_ENABLED', '')).lower() in ('1', 'true', 'sim', 'on')


def _intervalo() -> int:
    try:
        return max(60, int(os.getenv('SCHEDULER_INTERVALO_S', INTERVALO_PADRAO_S)))
    except ValueError:
        return INTERVALO_PADRAO_S


def _ciclo(app) -> None:
    from app.services.agendamento_service import (
        MODULOS, carregar, esta_vencida, executar_modulo,
    )

    intervalo = _intervalo()
    logger.info('[SCHEDULER] ligado — verificando a cada %ss', intervalo)

    # respiro no start: evita disparar durante o boot do container
    if _parar.wait(30):
        return

    while not _parar.is_set():
        try:
            with app.app_context():
                # DESVIO INTENCIONAL (15o) — backup automático do banco, uma vez por dia.
                # Vem ANTES das rotinas: se alguma delas estragar dado, a cópia do dia
                # já está guardada. Não gasta nada e não chama a SERPRO.
                try:
                    from app.services import backup_service
                    feito = backup_service.backup_diario_se_preciso()
                    if feito:
                        logger.info('[BACKUP] automático criado: %s (%s KB)',
                                    feito['arquivo'], feito['tamanho_kb'])
                except Exception:
                    logger.exception('[BACKUP] falha no backup automático')

                dados = carregar()
                for modulo in MODULOS:
                    config = dados['modulos'].get(modulo, {})
                    if esta_vencida(config):
                        logger.info('[SCHEDULER] disparando %s', modulo)
                        executar_modulo(modulo)
        except Exception:
            logger.exception('[SCHEDULER] erro no ciclo de verificação')

        _parar.wait(intervalo)

    logger.info('[SCHEDULER] encerrado')


def iniciar(app) -> None:
    """Sobe a thread do agendador, se estiver habilitado."""
    global _thread

    if not habilitado():
        logger.info('[SCHEDULER] desligado (defina SCHEDULER_ENABLED=1 para ativar)')
        return

    if _thread and _thread.is_alive():
        return

    _parar.clear()
    _thread = threading.Thread(target=_ciclo, args=(app,), name='agendador', daemon=True)
    _thread.start()


def parar() -> None:
    _parar.set()
