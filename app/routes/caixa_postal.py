"""
Rotas para Caixa Postal do e-CAC via SERPRO.

Reconstruído fielmente do bytecode do exe.
Fonte da verdade: dis/routes/caixa_postal.txt (1128 linhas)

URLs (10 rotas GET + POST):
- GET /acompanhamento
- GET /monitorar/status
- GET /logs
- POST /monitorar
- POST /empresas/<int:company_id>/consultar
- GET /empresas
- GET /mensagens
- GET /mensagens/<int:message_id>
- POST /mensagens/<int:message_id>/lida
- POST /mensagens/<int:message_id>/detalhe
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import and_, or_

from app.extensions import db
from app.models import CaixaPostalMensagem, CaixaPostalMonitoramento, Company
from app.services.caixa_postal_service import (
    CaixaPostalService,
    get_monitor_status,
)


caixa_postal_bp = Blueprint("caixa_postal", __name__, url_prefix="/api/caixa-postal")


# ========== HELPERS ==========


def _company_name(company: Company) -> str:
    """Retorna razao_social ou id como string."""
    return getattr(company, "razao_social", None) or str(company.id)


def _date(value):
    """Retorna value.isoformat() ou None."""
    return value.isoformat() if value else None


def _datetime(value):
    """Retorna value.isoformat() ou None."""
    return value.isoformat() if value else None


def _mensagem_json(item: CaixaPostalMensagem) -> dict:
    """Retorna dict com 21 campos de CaixaPostalMensagem."""
    return {
        "id": item.id,
        "company_id": item.company_id,
        "company_name": _company_name(item.company),
        "company_cnpj": item.company.cnpj,
        "isn": item.isn,
        "numero_controle": item.numero_controle,
        "assunto": item.assunto,
        "corpo": item.corpo,
        "data_envio": _date(item.data_envio),
        "hora_envio": item.hora_envio,
        "data_leitura": _date(item.data_leitura),
        "data_ciencia": _date(item.data_ciencia),
        "data_validade": _date(item.data_validade),
        "data_expiracao": _date(item.data_expiracao),
        "descricao_origem": item.descricao_origem,
        "indicador_leitura": item.indicador_leitura,
        "relevancia": item.relevancia,
        "detalhe_baixado": bool(item.detalhe_baixado),
        "visualizada_usuario": bool(item.visualizada_usuario),
        "visualizada_at": _datetime(item.visualizada_at),
        "downloaded_at": _datetime(item.downloaded_at),
    }


# ========== ROTAS ==========


@caixa_postal_bp.get("/acompanhamento")
def acompanhamento():
    """Retorna acompanhamento de caixa postal com monitoramentos."""
    try:
        # Query mensagens nao lidas (visualizada_usuario = False)
        unread_company_ids = set(
            db.session.query(CaixaPostalMensagem.company_id)
            .join(Company, Company.id == CaixaPostalMensagem.company_id)
            .filter(Company.ativo.is_(True))
            .filter(CaixaPostalMensagem.visualizada_usuario.is_(False))
            .distinct()
            .all()
        )
        unread_company_ids = {row[0] for row in unread_company_ids}

        # Query monitoramentos com condicoes
        rows = (
            db.session.query(CaixaPostalMonitoramento)
            .join(Company, Company.id == CaixaPostalMonitoramento.company_id)
            .filter(Company.ativo.is_(True))
            .filter(
                or_(
                    CaixaPostalMonitoramento.company_id.in_(unread_company_ids),
                    and_(
                        CaixaPostalMonitoramento.indicador_mensagens_novas > 0,
                        CaixaPostalMonitoramento.mensagens_baixadas.is_(False),
                    ),
                )
            )
            .order_by(Company.razao_social.asc())
            .all()
        )

        items = []
        seen_company_ids = set()

        for row in rows:
            nao_lidas = CaixaPostalMensagem.query.filter_by(
                company_id=row.company_id, visualizada_usuario=False
            ).count()
            pendentes = CaixaPostalMensagem.query.filter_by(
                company_id=row.company_id, detalhe_baixado=False
            ).count()

            seen_company_ids.add(row.company_id)
            items.append({
                "id": row.id,
                "company_id": row.company_id,
                "company_name": _company_name(row.company),
                "company_cnpj": row.company.cnpj,
                "indicador_mensagens_novas": row.indicador_mensagens_novas,
                "mensagens_baixadas": bool(row.mensagens_baixadas),
                "mensagens_nao_lidas": nao_lidas,
                "detalhes_pendentes": pendentes,
                "ultima_baixa_mensagens": _datetime(row.ultima_baixa_mensagens),
                "ultima_consulta_monitoramento": _datetime(row.ultima_consulta_monitoramento),
                "erro": row.erro,
            })

        # Empresas com mensagens nao lidas mas sem monitoramento
        missing_pending_ids = unread_company_ids - seen_company_ids
        if missing_pending_ids:
            companies = (
                Company.query.filter(Company.id.in_(missing_pending_ids))
                .order_by(Company.razao_social.asc())
                .all()
            )
            for company in companies:
                nao_lidas = CaixaPostalMensagem.query.filter_by(
                    company_id=company.id, visualizada_usuario=False
                ).count()
                pendentes = CaixaPostalMensagem.query.filter_by(
                    company_id=company.id, detalhe_baixado=False
                ).count()

                items.append({
                    "id": None,
                    "company_id": company.id,
                    "company_name": _company_name(company),
                    "company_cnpj": company.cnpj,
                    "indicador_mensagens_novas": 0,
                    "mensagens_baixadas": False,
                    "mensagens_nao_lidas": nao_lidas,
                    "detalhes_pendentes": pendentes,
                    "ultima_baixa_mensagens": None,
                    "ultima_consulta_monitoramento": None,
                    "erro": None,
                })

        return jsonify(items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.get("/monitorar/status")
def monitorar_status():
    """Retorna status global do monitor."""
    return jsonify(get_monitor_status())


@caixa_postal_bp.get("/logs")
def logs_caixa_postal():
    """Retorna logs de caixa postal (ultimas 300 linhas com CAIXA_POSTAL)."""
    try:
        log_path = Path(current_app.config["DATA_DIR"]) / "logs" / "app.log"
        if not log_path.exists():
            return jsonify({
                "success": False,
                "message": "Arquivo de log ainda não foi criado.",
                "path": str(log_path),
            }), 404

        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

        # Filtra linhas com [CAIXA_POSTAL] ou "caixa postal"
        caixa_lines = [
            line for line in lines
            if "[CAIXA_POSTAL]" in line or "caixa postal" in line.lower()
        ]

        return jsonify({
            "success": True,
            "path": str(log_path),
            "items": caixa_lines[-300:],
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.post("/monitorar")
def monitorar_manual():
    """Realiza monitoramento manual de empresas."""
    try:
        payload = request.get_json(silent=True) or {}
        company_ids = payload.get("company_ids") or []

        service = CaixaPostalService()
        result = service.monitorar_todas_empresas(
            only_if_due=False,
            baixar_mensagens_quando_houver=True,
            company_ids=company_ids,
        )

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.post("/empresas/<int:company_id>/consultar")
def consultar_empresa(company_id: int):
    """Consulta mensagens de uma empresa."""
    try:
        payload = request.get_json(silent=True) or {}
        status_leitura = int(payload.get("status_leitura") or 0)
        baixar_detalhes = bool(payload.get("baixar_detalhes", True))

        service = CaixaPostalService()
        result = service.consultar_mensagens_empresa(
            company_id,
            status_leitura=status_leitura,
            baixar_detalhes=baixar_detalhes,
        )

        if result.get("success"):
            return jsonify(result), 200
        else:
            return jsonify(result), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.get("/empresas")
def empresas_com_mensagens():
    """Lista empresas com mensagens (outer join CaixaPostalMensagem)."""
    try:
        rows = (
            db.session.query(Company)
            .outerjoin(CaixaPostalMensagem, CaixaPostalMensagem.company_id == Company.id)
            .filter(Company.ativo.is_(True))
            .group_by(Company.id)
            .order_by(Company.razao_social.asc())
            .all()
        )

        items = [
            {
                "id": company.id,
                "razao_social": _company_name(company),
                "cnpj": company.cnpj,
                "total_mensagens": CaixaPostalMensagem.query.filter_by(
                    company_id=company.id
                ).count(),
            }
            for company in rows
        ]

        return jsonify(items)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.get("/mensagens")
def mensagens():
    """Lista mensagens com filtros opcionais: company_id, busca, nao_lidas."""
    try:
        company_id = request.args.get("company_id", type=int)
        busca = (request.args.get("busca") or "").strip()
        nao_lidas = request.args.get("nao_lidas") in ("1", "true", "True")

        query = db.session.query(CaixaPostalMensagem).join(
            Company, Company.id == CaixaPostalMensagem.company_id
        )

        if company_id:
            query = query.filter(CaixaPostalMensagem.company_id == company_id)

        if nao_lidas:
            query = query.filter(CaixaPostalMensagem.visualizada_usuario.is_(False))

        if busca:
            like = f"%{busca}%"
            query = query.filter(
                or_(
                    CaixaPostalMensagem.assunto.ilike(like),
                    CaixaPostalMensagem.corpo.ilike(like),
                    CaixaPostalMensagem.numero_controle.ilike(like),
                    Company.razao_social.ilike(like),
                    Company.cnpj.ilike(like),
                )
            )

        items = (
            query.order_by(
                CaixaPostalMensagem.data_envio.desc(),
                CaixaPostalMensagem.id.desc(),
            )
            .limit(1000)
            .all()
        )

        return jsonify([_mensagem_json(item) for item in items])

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.get("/mensagens/<int:message_id>")
def mensagem_detalhe(message_id: int):
    """Retorna detalhe de uma mensagem."""
    try:
        item = CaixaPostalMensagem.query.get(message_id)
        if not item:
            return jsonify({
                "success": False,
                "message": "Mensagem não encontrada",
            }), 404

        return jsonify(_mensagem_json(item))

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.post("/mensagens/<int:message_id>/lida")
def marcar_mensagem_lida(message_id: int):
    """Marca mensagem como lida (visualizada_usuario = True)."""
    try:
        item = CaixaPostalMensagem.query.get(message_id)
        if not item:
            return jsonify({
                "success": False,
                "message": "Mensagem não encontrada",
            }), 404

        item.visualizada_usuario = True
        item.visualizada_at = datetime.utcnow()
        db.session.add(item)
        db.session.commit()

        return jsonify({
            "success": True,
            "mensagem": _mensagem_json(item),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@caixa_postal_bp.post("/mensagens/<int:message_id>/detalhe")
def baixar_mensagem_detalhe(message_id: int):
    """Baixa detalhe de uma mensagem."""
    try:
        service = CaixaPostalService()
        result = service.baixar_detalhe_mensagem(message_id)

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
