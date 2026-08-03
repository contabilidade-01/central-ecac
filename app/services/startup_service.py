from datetime import datetime, timedelta

from app.extensions import db
from app.models import Company


def unlock_stale_processing(timeout_minutes: int = 15):
    threshold = datetime.now() - timedelta(minutes=timeout_minutes)

    stale_companies = Company.query.filter(
        Company.processing_status == 'processing',
        Company.processing_started_at.isnot(None),
        Company.processing_started_at < threshold,
    ).all()

    for company in stale_companies:
        company.processing_status = 'idle'
        company.processing_step = None
        company.processing_progress = 0
        company.processing_message = (
            'Processamento anterior interrompido por fechamento inesperado.'
        )
        company.current_protocol = None
        company.protocol_requested_at = None
        company.protocol_wait_until = None
        company.processing_started_at = None

    if stale_companies:
        db.session.commit()
