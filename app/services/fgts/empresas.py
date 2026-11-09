"""Quais empresas têm FGTS: as que têm funcionário ativo no portal do cliente.

O portal (nescon-clientes) é o dono da folha. A Central lê `GET /api/interno/empresas-funcionarios`
(só CNPJ e quantidade) uma vez por dia, ou pelo botão da tela. O administrador pode abrir
exceção por empresa: ligar (FGTS atrasado de quando havia funcionário) ou desligar.

Antes da primeira sincronização nada é bloqueado, para não parar o que já funciona.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.extensions import db
from app.fgts_models import FgtsEmpresa

VALIDADE = timedelta(hours=24)


def _ultima_sincronizacao() -> Optional[datetime]:
    linha = FgtsEmpresa.query.filter(FgtsEmpresa.sincronizado_em.isnot(None)) \
        .order_by(FgtsEmpresa.sincronizado_em.desc()).first()
    return linha.sincronizado_em if linha else None


def sincronizar() -> Dict[str, Any]:
    """Atualiza a quantidade de funcionários de cada empresa da Central. Não mexe nas exceções."""
    from app.models import Company
    from app.services import nescon_service
    resposta = nescon_service._chamar('GET', '/empresas-funcionarios')
    if resposta.get('status') == 'erro_rede' or not isinstance(resposta.get('empresas'), list):
        return {'ok': False, 'motivo': resposta.get('motivo') or f"Portal respondeu HTTP {resposta.get('_http')}"}
    contagem = {str(e.get('cnpj')): int(e.get('funcionarios') or 0) for e in resposta['empresas']}
    agora = datetime.utcnow()
    com = 0
    for c in Company.query.all():
        linha = db.session.get(FgtsEmpresa, c.id) or FgtsEmpresa(company_id=c.id, cnpj=c.cnpj)
        linha.cnpj = c.cnpj
        linha.funcionarios = contagem.get(c.cnpj)
        linha.sincronizado_em = agora
        db.session.add(linha)
        com += 1 if (linha.funcionarios or 0) > 0 else 0
    db.session.commit()
    return {'ok': True, 'empresas_com_funcionarios': com, 'sincronizado_em': agora.isoformat()}


def sincronizar_se_velho() -> None:
    ultima = _ultima_sincronizacao()
    if not ultima or datetime.utcnow() - ultima > VALIDADE:
        try:
            sincronizar()
        except Exception:
            db.session.rollback()


def situacao(company_id: int) -> Dict[str, Any]:
    linha = db.session.get(FgtsEmpresa, company_id)
    sincronizado = _ultima_sincronizacao() is not None
    funcionarios = linha.funcionarios if linha else None
    if linha and linha.excecao is not None:
        aplicavel, motivo = bool(linha.excecao), 'exceção do administrador'
    elif not sincronizado:
        aplicavel, motivo = True, 'portal ainda não sincronizado'
    elif (funcionarios or 0) > 0:
        aplicavel, motivo = True, f'{funcionarios} funcionário(s) no portal'
    else:
        aplicavel, motivo = False, 'sem funcionário no portal' if funcionarios == 0 else 'empresa não encontrada no portal'
    return {'aplicavel': aplicavel, 'motivo': motivo, 'funcionarios': funcionarios,
            'excecao': linha.excecao if linha else None}


def definir_excecao(company_id: int, cnpj: str, valor: Optional[bool], por: Optional[str]) -> None:
    linha = db.session.get(FgtsEmpresa, company_id) or FgtsEmpresa(company_id=company_id, cnpj=cnpj)
    linha.excecao, linha.excecao_por, linha.excecao_em = valor, (por or '')[:120] or None, datetime.utcnow()
    db.session.add(linha)
    db.session.commit()
