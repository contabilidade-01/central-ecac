"""Empresas do cadastro central incluídas no Escritório (ticar)."""

from __future__ import annotations

from typing import List, Optional, Set

from app.extensions import db


def garantir_semente() -> int:
    """Na 1ª subida, inclui todas as empresas ativas (não quebra quem já usa o Escritório)."""
    from app.escritorio_models import EscritorioEmpresa
    from app.models import Company

    if EscritorioEmpresa.query.count() > 0:
        return 0
    criados = 0
    for c in Company.query.filter_by(ativo=True).order_by(Company.id.asc()).all():
        db.session.add(EscritorioEmpresa(company_id=c.id, incluso=True, atualizado_por='semente'))
        criados += 1
    if criados:
        db.session.commit()
    return criados


def ids_incluidas() -> List[int]:
    from app.escritorio_models import EscritorioEmpresa
    return [int(r.company_id) for r in
            EscritorioEmpresa.query.filter_by(incluso=True).all()]


def mapa_inclusao(company_ids: Optional[List[int]] = None) -> dict:
    """{company_id: incluso} — empresas sem linha contam como False."""
    from app.escritorio_models import EscritorioEmpresa
    q = EscritorioEmpresa.query
    if company_ids is not None:
        if not company_ids:
            return {}
        q = q.filter(EscritorioEmpresa.company_id.in_(company_ids))
    return {int(r.company_id): bool(r.incluso) for r in q.all()}


def definir(company_id: int, incluso: bool, usuario: str = '') -> dict:
    from app.escritorio_models import EscritorioEmpresa
    from app.models import Company

    company = db.session.get(Company, int(company_id))
    if not company:
        raise LookupError('Empresa não encontrada no cadastro.')
    linha = EscritorioEmpresa.query.filter_by(company_id=company.id).first()
    if not linha:
        linha = EscritorioEmpresa(company_id=company.id)
        db.session.add(linha)
    linha.incluso = bool(incluso)
    linha.atualizado_por = (usuario or '')[:120] or None
    db.session.commit()
    return {
        'id': company.id,
        'cnpj': company.cnpj,
        'razao_social': company.razao_social,
        'ativo': bool(company.ativo),
        'incluso': bool(linha.incluso),
    }


def definir_lote(company_ids: List[int], incluso: bool, usuario: str = '') -> dict:
    """Marca/desmarca várias empresas num único commit.

    Ao INCLUIR, empresas inativas no cadastro são puladas (mesma regra do checkbox).
    Retorna {'alterados': [ids], 'pulados_inativos': [ids], 'nao_encontrados': [ids]}.
    """
    from app.escritorio_models import EscritorioEmpresa
    from app.models import Company

    ids = sorted({int(i) for i in company_ids})
    empresas = {c.id: c for c in Company.query.filter(Company.id.in_(ids or [-1])).all()}
    linhas = {r.company_id: r for r in
              EscritorioEmpresa.query.filter(EscritorioEmpresa.company_id.in_(ids or [-1])).all()}
    alterados, inativos, faltando = [], [], []
    quem = (usuario or '')[:120] or None
    for cid in ids:
        company = empresas.get(cid)
        if company is None:
            faltando.append(cid)
            continue
        if incluso and not company.ativo:
            inativos.append(cid)
            continue
        linha = linhas.get(cid)
        if linha is None:
            linha = EscritorioEmpresa(company_id=cid)
            db.session.add(linha)
        if bool(linha.incluso) != bool(incluso) or linha.id is None:
            linha.incluso = bool(incluso)
            linha.atualizado_por = quem
            alterados.append(cid)
    db.session.commit()
    return {'alterados': alterados, 'pulados_inativos': inativos, 'nao_encontrados': faltando}


def listar(empresa_ids: Optional[List[int]] = None) -> List[dict]:
    """Todas as empresas do cadastro (visão do usuário) + flag incluso."""
    from app.models import Company

    q = Company.query.order_by(Company.razao_social.asc())
    if empresa_ids is not None:
        q = q.filter(Company.id.in_(empresa_ids or [-1]))
    empresas = q.all()
    flags = mapa_inclusao([c.id for c in empresas])
    out = []
    for c in empresas:
        out.append({
            'id': c.id,
            'cnpj': c.cnpj,
            'razao_social': c.razao_social,
            'ativo': bool(c.ativo),
            'incluso': bool(flags.get(c.id, False)),
        })
    return out


def filtrar_ids(liberadas: Optional[List[int]]) -> List[int]:
    """Intersecta permissão do usuário com as ticked no Escritório.

    `liberadas is None` = admin/todas. Lista vazia de ticked → nenhuma empresa.
    """
    ticked: Set[int] = set(ids_incluidas())
    if liberadas is None:
        return sorted(ticked) or [-1]
    return sorted(i for i in liberadas if i in ticked) or [-1]
