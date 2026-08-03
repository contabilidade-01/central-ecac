from flask import Blueprint, jsonify
from sqlalchemy import func

from app.extensions import db
from app.models import Company, RelatorioSitFiscal, PendenciaRelatorio, DebitoRelatorio

dashboard_bp = Blueprint('dashboard', __name__)


def latest_reports_query():
    latest = (
        db.session.query(
            RelatorioSitFiscal.company_id,
            func.max(RelatorioSitFiscal.id).label('max_id'),
        )
        .group_by(RelatorioSitFiscal.company_id)
        .subquery()
    )

    return (
        db.session.query(RelatorioSitFiscal)
        .join(latest, RelatorioSitFiscal.id == latest.c.max_id)
        .order_by(RelatorioSitFiscal.company_id.asc())
    )


@dashboard_bp.get('/summary')
def summary():
    latest_reports = latest_reports_query().all()
    latest_report_ids = [r.id for r in latest_reports]

    total_empresas = Company.query.filter_by(ativo=True).count()

    empresas_com_pendencias = (
        db.session.query(PendenciaRelatorio.relatorio_id)
        .filter(PendenciaRelatorio.relatorio_id.in_(latest_report_ids))
        .distinct()
        .count()
    ) if latest_report_ids else 0

    total_pendencias = (
        db.session.query(func.count(PendenciaRelatorio.id))
        .filter(PendenciaRelatorio.relatorio_id.in_(latest_report_ids))
        .scalar() or 0
    ) if latest_report_ids else 0

    total_debitos = (
        db.session.query(func.count(DebitoRelatorio.id))
        .filter(DebitoRelatorio.relatorio_id.in_(latest_report_ids))
        .scalar() or 0
    ) if latest_report_ids else 0

    valor_total_debitos = float(
        (
            db.session.query(func.sum(DebitoRelatorio.saldo_devedor_total))
            .filter(DebitoRelatorio.relatorio_id.in_(latest_report_ids))
            .scalar() or 0
        ) if latest_report_ids else 0
    )

    return jsonify({
        'total_empresas': total_empresas,
        'empresas_com_pendencias': empresas_com_pendencias,
        'total_pendencias': total_pendencias,
        'total_debitos': total_debitos,
        'valor_total_debitos': valor_total_debitos,
    })


@dashboard_bp.get('/companies')
def companies_dashboard():
    latest_reports = latest_reports_query().all()
    items = []

    for relatorio in latest_reports:
        total_pendencias = len(relatorio.pendencias)
        total_debitos = len(relatorio.debitos)
        valor_total_debitos = float(
            sum(d.saldo_devedor_total or 0 for d in relatorio.debitos))

        items.append({
            'id': relatorio.company.id,
            'razao_social': relatorio.company.razao_social,
            'cnpj': relatorio.company.cnpj,
            'situacao': relatorio.situacao,
            'data_hora': relatorio.data_hora.isoformat() if relatorio.data_hora else None,
            'report_id': relatorio.id,
            'simples_nacional_inclusao': (
                relatorio.simples_nacional_inclusao.isoformat()
                if relatorio.simples_nacional_inclusao else None),
            'simples_nacional_exclusao': (
                relatorio.simples_nacional_exclusao.isoformat()
                if relatorio.simples_nacional_exclusao else None),
            'simei_inclusao': (relatorio.simei_inclusao.isoformat()
                               if relatorio.simei_inclusao else None),
            'simei_exclusao': (relatorio.simei_exclusao.isoformat()
                               if relatorio.simei_exclusao else None),
            'total_pendencias': total_pendencias,
            'total_debitos': total_debitos,
            'valor_total_debitos': valor_total_debitos,
            'processing_status': relatorio.company.processing_status,
            'processing_step': relatorio.company.processing_step,
            'processing_progress': relatorio.company.processing_progress,
            'processing_message': relatorio.company.processing_message,
            'current_protocol': relatorio.company.current_protocol,
        })

    companies_without_reports = (
        Company.query
        .filter(~Company.relatorios.any())
        .order_by(Company.razao_social.asc())
        .all()
    )

    for company in companies_without_reports:
        items.append({
            'id': company.id,
            'razao_social': company.razao_social,
            'cnpj': company.cnpj,
            'situacao': 'Sem relatório',
            'data_hora': None,
            'report_id': None,
            'simples_nacional_inclusao': None,
            'simples_nacional_exclusao': None,
            'simei_inclusao': None,
            'simei_exclusao': None,
            'total_pendencias': 0,
            'total_debitos': 0,
            'valor_total_debitos': 0,
            'processing_status': company.processing_status,
            'processing_step': company.processing_step,
            'processing_progress': company.processing_progress,
            'processing_message': company.processing_message,
            'current_protocol': company.current_protocol,
        })

    items.sort(key=lambda x: x['razao_social'].lower())
    return jsonify(items)
