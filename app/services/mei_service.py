"""Quem é MEI no sistema. A marcação é do administrador (tabela `empresas_mei`)."""
from datetime import datetime
from typing import Optional, Set

from app.extensions import db
from app.integracao_models import EmpresaMei


def e_mei(company_id: int) -> bool:
    marca = EmpresaMei.query.filter_by(company_id=company_id).first()
    return bool(marca and marca.eh_mei)


def ids_mei() -> Set[int]:
    return {m.company_id for m in EmpresaMei.query.filter_by(eh_mei=True).all()}


def definir(company_id: int, eh_mei: bool, por: Optional[str] = None) -> EmpresaMei:
    marca = EmpresaMei.query.filter_by(company_id=company_id).first()
    if not marca:
        marca = EmpresaMei(company_id=company_id)
        db.session.add(marca)
    marca.eh_mei = bool(eh_mei)
    marca.definido_por = (por or '')[:120] or None
    marca.definido_em = datetime.utcnow()
    db.session.commit()
    return marca
