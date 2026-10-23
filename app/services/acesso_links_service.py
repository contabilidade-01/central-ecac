"""Links de acesso às guias SEM SENHA — criação, validação, limites e revogação (desvio 21).

⚠️ DESVIO INTENCIONAL — NÃO existe no exe. Pedido do Jean (23/10/2026): o AtendeChat
manda ao cliente um link para ele gerar a guia recalculada direto daqui, sem o portal do
cliente no caminho. Ver `integracao_models.AcessoLink` para o modelo de segurança.

Regras:
* `obter_ou_criar` reaproveita um link da mesma empresa/origem que ainda valha ≥ 3 dias,
  para a mesma pessoa não receber um link diferente a cada mensagem.
* `validar` só aceita token hex de 24 (ou 64, antigos) caracteres, não revogado, não vencido, de empresa
  ativa. Token errado conta como tentativa falha do IP (mesmo bloqueio do login).
* Limite de gerações por token por competência por dia (`LINK_GUIAS_MAX_GERACOES_DIA`,
  padrão 2) — além das travas de custo de `das_emissao_service.emitir` (procuração, teto
  mensal). As emissões do link NÃO contam no limite diário por empresa do portal
  (`INTEGRACAO_MAX_EMISSOES_DIA`): o que o cliente gera por aqui nunca bloqueia o portal.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.integracao_models import AcessoLink, DasEmissao
from app.models import Company

logger = logging.getLogger(__name__)

ORIGENS = ('atendechat', 'escritorio')
ORIGEM_EMISSAO = 'link'          # `das_emissoes.origem` das guias geradas pela página
MARGEM_REUSO_DIAS = 3


def dias_padrao() -> int:
    try:
        return max(1, min(int(os.getenv('LINK_GUIAS_DIAS', '35')), 180))
    except ValueError:
        return 35


def max_geracoes_dia() -> int:
    try:
        return max(1, int(os.getenv('LINK_GUIAS_MAX_GERACOES_DIA', '2')))
    except ValueError:
        return 2


# 24 hex = 96 bits aleatórios: inviolável e curto. O token de 64 caracteres (primeira
# versão) chegava CORTADO no WhatsApp do celular ao reconhecer o link; os antigos
# continuam aceitos em `token_bem_formado`.
TAMANHOS_TOKEN = (24, 64)


def novo_token() -> str:
    return secrets.token_hex(12)


def solicitante(token: str) -> str:
    """Identificação curta gravada em `das_emissoes.solicitado_por`."""
    return f'link:{token[:16]}'


def url_do_link(token: str, base: Optional[str] = None) -> str:
    raiz = (base or os.getenv('PUBLIC_BASE_URL') or '').rstrip('/')
    return f'{raiz}/g/{token}'


def serializar(link: AcessoLink) -> Dict[str, Any]:
    agora = datetime.utcnow()
    return {
        'id': link.id,
        'token': link.token,
        'cnpj': link.cnpj,
        'origem': link.origem,
        'solicitado_por': link.solicitado_por,
        'criado_em': link.criado_em.isoformat() if link.criado_em else None,
        'expira_em': link.expira_em.isoformat() if link.expira_em else None,
        'revogado_em': link.revogado_em.isoformat() if link.revogado_em else None,
        'valido': link.revogado_em is None and link.expira_em > agora,
        'acessos': int(link.acessos or 0),
        'ultimo_acesso_em': link.ultimo_acesso_em.isoformat() if link.ultimo_acesso_em else None,
        'guias_geradas': int(link.guias_geradas or 0),
    }


def obter_ou_criar(company: Company, origem: str = 'atendechat',
                   solicitado_por: Optional[str] = None, dias: Optional[int] = None) -> AcessoLink:
    if origem not in ORIGENS:
        raise ValueError(f'origem inválida: {origem}')
    agora = datetime.utcnow()
    existente = (AcessoLink.query
                 .filter(AcessoLink.company_id == company.id, AcessoLink.origem == origem,
                         AcessoLink.revogado_em.is_(None),
                         AcessoLink.expira_em > agora + timedelta(days=MARGEM_REUSO_DIAS))
                 .order_by(AcessoLink.expira_em.desc())
                 .first())
    if existente:
        return existente
    validade = max(1, min(int(dias or dias_padrao()), 180))
    link = AcessoLink(token=novo_token(), company_id=company.id, cnpj=company.cnpj, origem=origem,
                      solicitado_por=(solicitado_por or '')[:120] or None,
                      expira_em=agora + timedelta(days=validade))
    db.session.add(link)
    db.session.commit()
    return link


def token_bem_formado(token: Any) -> str:
    tok = ''.join(ch for ch in str(token or '').lower() if ch in '0123456789abcdef')
    return tok if len(tok) in TAMANHOS_TOKEN else ''


def validar(token: Any) -> Optional[AcessoLink]:
    """Link válido (não vencido, não revogado, empresa ativa) ou None."""
    tok = token_bem_formado(token)
    if not tok:
        return None
    link = AcessoLink.query.filter_by(token=tok).first()
    if not link or link.revogado_em is not None or link.expira_em <= datetime.utcnow():
        return None
    company = db.session.get(Company, link.company_id)
    if not company or not company.ativo:
        return None
    return link


def registrar_acesso(link: AcessoLink, ip: Optional[str]) -> None:
    try:
        link.acessos = int(link.acessos or 0) + 1
        link.ultimo_acesso_em = datetime.utcnow()
        link.ultimo_acesso_ip = (ip or '')[:60] or None
        db.session.commit()
    except Exception:  # contagem é informativa; nunca derruba a página
        db.session.rollback()
        logger.warning('[LINK] não foi possível registrar o acesso', exc_info=True)


def registrar_guia(link: AcessoLink) -> None:
    try:
        link.guias_geradas = int(link.guias_geradas or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning('[LINK] não foi possível contar a guia gerada', exc_info=True)


def geracoes_hoje(link: AcessoLink, tipo: str, pa: str) -> int:
    """Emissões PAGAS deste token para a competência, hoje (reuso não conta)."""
    from app.services.pendencias_service import hoje_brasil
    inicio = datetime.combine(hoje_brasil(), datetime.min.time()) + timedelta(hours=3)
    return (DasEmissao.query
            .filter(DasEmissao.cnpj == link.cnpj, DasEmissao.tipo == tipo,
                    DasEmissao.periodo_apuracao == pa,
                    DasEmissao.origem == ORIGEM_EMISSAO,
                    DasEmissao.solicitado_por == solicitante(link.token),
                    DasEmissao.reuso_de_id.is_(None),
                    DasEmissao.emitido_em >= inicio)
            .count())


def revogar_da_empresa(company_id: int, quem: Optional[str]) -> int:
    agora = datetime.utcnow()
    ativos = (AcessoLink.query
              .filter(AcessoLink.company_id == company_id, AcessoLink.revogado_em.is_(None),
                      AcessoLink.expira_em > agora)
              .all())
    for link in ativos:
        link.revogado_em = agora
        link.revogado_por = (quem or '')[:120] or None
    db.session.commit()
    return len(ativos)


def listar_da_empresa(company_id: int, limite: int = 10) -> List[Dict[str, Any]]:
    itens = (AcessoLink.query.filter_by(company_id=company_id)
             .order_by(AcessoLink.criado_em.desc()).limit(limite).all())
    return [serializar(i) for i in itens]
