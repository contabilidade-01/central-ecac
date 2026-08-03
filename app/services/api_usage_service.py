from datetime import datetime
from decimal import Decimal

from sqlalchemy import extract, func

from app.extensions import db
from app.models import ApiUsageLog


class ApiUsageService:

    @staticmethod
    def _get_consulta_cost(total_mes: int) -> Decimal:
        if total_mes <= 300:
            return Decimal('0.24')
        return Decimal('0.21')

    @staticmethod
    def _get_emitir_cost(total_mes: int) -> Decimal:
        if total_mes <= 500:
            return Decimal('0.32')
        return Decimal('0.29')

    @staticmethod
    def register_usage(route_type: str, endpoint: str, company_id: int | None = None):
        now = datetime.utcnow()

        total_mes = db.session.query(
            func.count(ApiUsageLog.id)
        ).filter(
            ApiUsageLog.route_type == route_type,
            extract('month', ApiUsageLog.created_at) == now.month,
            extract('year', ApiUsageLog.created_at) == now.year,
        ).scalar() or 0

        total_mes += 1

        if route_type == 'consultar':
            estimated_cost = ApiUsageService._get_consulta_cost(total_mes)
        else:
            estimated_cost = ApiUsageService._get_emitir_cost(total_mes)

        usage = ApiUsageLog(
            company_id=company_id,
            route_type=route_type,
            endpoint=endpoint,
            quantity=1,
            estimated_cost=estimated_cost,
            success=True,
        )

        db.session.add(usage)
        db.session.commit()
