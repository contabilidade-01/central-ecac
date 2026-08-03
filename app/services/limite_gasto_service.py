"""Teto mensal de gasto com a API SERPRO (melhoria #4).

⚠️ DESVIO INTENCIONAL (8o, junto com o agendamento) — NÃO existe no exe. Sem isto, um
clique errado num lote grande gasta sem freio: hoje `api_usage_logs` só registra o custo
DEPOIS que a chamada aconteceu.

Como funciona
-------------
* O teto fica em `<DATA_DIR>/instance/agendamento.json`, chave `limite_gasto`.
* `LIMITE_GASTO_MENSAL` no ambiente sobrepõe o arquivo (útil no servidor).
* `0` = sem teto (comportamento do exe).
* O gasto do mês vem de `api_usage_logs`, a mesma fonte da tela "Custos API".

Onde é aplicado
---------------
* Antes de cada rotina automática (`agendamento_service.executar_modulo`).
* No lote da caixa postal, antes das chamadas PAGAS.
O monitoramento (indicador) é **gratuito** e nunca é bloqueado por teto.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import extract, func

from app.extensions import db
from app.models import ApiUsageLog
from app.services.agendamento_service import carregar, caminho, salvar

logger = logging.getLogger(__name__)

PADRAO_LIMITE = 0.0  # sem teto, como o exe


class LimiteGastoService:

    @staticmethod
    def limite() -> float:
        """Teto mensal em reais. 0 = sem teto."""
        do_ambiente = os.getenv('LIMITE_GASTO_MENSAL')
        if do_ambiente:
            try:
                return float(do_ambiente)
            except ValueError:
                logger.warning('LIMITE_GASTO_MENSAL inválido: %r', do_ambiente)

        try:
            return float(carregar().get('limite_gasto', PADRAO_LIMITE))
        except Exception:
            return PADRAO_LIMITE

    @staticmethod
    def definir_limite(valor: float) -> float:
        if valor < 0:
            raise ValueError('o teto não pode ser negativo')
        dados = carregar()
        dados['limite_gasto'] = float(valor)
        salvar(dados)
        return float(valor)

    @staticmethod
    def gasto_do_mes(referencia: Optional[datetime] = None) -> float:
        agora = referencia or datetime.utcnow()
        total = db.session.query(func.sum(ApiUsageLog.estimated_cost)).filter(
            extract('month', ApiUsageLog.created_at) == agora.month,
            extract('year', ApiUsageLog.created_at) == agora.year,
        ).scalar()
        return float(total or Decimal('0'))

    @staticmethod
    def pode_gastar(custo_previsto: float = 0.0) -> Tuple[bool, Optional[str]]:
        """(pode?, motivo). `custo_previsto` permite barrar ANTES de começar o lote."""
        limite = LimiteGastoService.limite()
        if limite <= 0:
            return True, None

        gasto = LimiteGastoService.gasto_do_mes()
        if gasto + custo_previsto > limite:
            return False, (
                f'teto mensal atingido: R$ {gasto:.2f} de R$ {limite:.2f} já gastos'
                + (f' (esta rodada custaria ~R$ {custo_previsto:.2f})'
                   if custo_previsto else '')
            )
        return True, None

    @staticmethod
    def resumo() -> Dict[str, Any]:
        limite = LimiteGastoService.limite()
        gasto = LimiteGastoService.gasto_do_mes()
        return {
            'limite': limite,
            'gasto_mes': round(gasto, 2),
            'restante': round(limite - gasto, 2) if limite > 0 else None,
            'sem_teto': limite <= 0,
            'arquivo': str(caminho()),
        }
