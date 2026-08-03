from sqlalchemy import extract, func  # noqa: F401  (func mantido como no exe)

from flask import Blueprint, jsonify, request

from app.models import ApiUsageLog
from app.extensions import db


api_costs_bp = Blueprint('api_costs', __name__, url_prefix='/api-costs')


@api_costs_bp.get('/summary')
def api_costs_summary():
    month = request.args.get('month')

    query = db.session.query(ApiUsageLog)

    if month:
        year, month_num = month.split('-')

        query = query.filter(
            extract('year', ApiUsageLog.created_at) == int(year),
            extract('month', ApiUsageLog.created_at) == int(month_num),
        )

    usages = query.all()

    total_consultas = len([u for u in usages if u.route_type == 'consultar'])
    total_emissoes = len([u for u in usages if u.route_type == 'emitir'])

    valor_estimado = float(sum(float(u.estimated_cost or 0) for u in usages))

    return jsonify({
        'total_consultas': total_consultas,
        'total_emissoes': total_emissoes,
        'valor_estimado': valor_estimado,
    })


@api_costs_bp.get('/logs')
def api_costs_logs():
    month = request.args.get('month')

    query = ApiUsageLog.query.order_by(ApiUsageLog.created_at.desc())

    if month:
        year, month_num = month.split('-')

        query = query.filter(
            extract('year', ApiUsageLog.created_at) == int(year),
            extract('month', ApiUsageLog.created_at) == int(month_num),
        )

    items = []

    for item in query.limit(1000).all():
        items.append({
            'id': item.id,
            'company_id': item.company_id,
            'route_type': item.route_type,
            'endpoint': item.endpoint,
            'estimated_cost': float(item.estimated_cost or 0),
            'created_at': item.created_at.isoformat() if item.created_at else None,
        })

    return jsonify(items)
