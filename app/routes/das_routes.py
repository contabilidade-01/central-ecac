"""Rotas para emissão de DAS / DARF DCTFWeb.

Reconstituído fielmente do bytecode do exe.
Fonte da verdade: dis/routes/das_routes.txt (2930 linhas)

9 rotas:
  POST /emitir                     -> emitir_das_simples()
  POST /mei/emitir                 -> emitir_das_mei()
  GET  /emitir                     -> emitir_das_get_nao_permitido()
  POST /emitir-lote                -> emitir_das_lote()
  POST /emitir-lote/iniciar        -> iniciar_emissao_das_lote()
  GET  /emitir-lote/status/<job_id> -> status_emissao_das_lote()
  GET  /emitir-lote/download/<job_id> -> download_emissao_das_lote()
  POST /dctfweb/emitir             -> emitir_darf_dctfweb()
  POST /dctfweb/emitir-lote        -> emitir_darf_dctfweb_lote()
"""

from __future__ import annotations

import base64
import io
import threading
import uuid
import zipfile
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request

from app.extensions import db
from app.models import AppSetting, Company
from app.services.serpro_das_service import SerproDasService
from app.services.api_usage_service import ApiUsageService


das_bp = Blueprint("das", __name__)

# Estado dos jobs de lote em memória
DAS_BATCH_JOBS: dict = {}
DAS_BATCH_LOCK = threading.Lock()


# ========== HELPERS ==========


def _only_digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _safe_filename_part(value) -> str:
    import re
    import unicodedata
    value = (value or "").strip()
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "SEM_NOME"


def _get_contribuinte_numero_from_payload(payload: dict) -> str:
    return _only_digits(
        (payload.get("contribuinte") or {}).get("numero") or ""
    )


def _set_das_batch_job(job_id: str, **kwargs) -> None:
    with DAS_BATCH_LOCK:
        if job_id not in DAS_BATCH_JOBS:
            DAS_BATCH_JOBS[job_id] = {
                "status": "pending",
                "total": 0,
                "completed": 0,
                "progress": 0,
                "message": "",
                "current_company": None,
                "errors": [],
                "results": [],
                "zip_bytes": None,
                "created_at": datetime.utcnow().isoformat(),
            }
        DAS_BATCH_JOBS[job_id].update(kwargs)


def _get_das_batch_job(job_id: str) -> dict | None:
    with DAS_BATCH_LOCK:
        return DAS_BATCH_JOBS.get(job_id)


def _public_das_batch_job(job: dict) -> dict:
    """Retorna versão pública do job (sem zip_bytes)."""
    return {
        k: v for k, v in job.items() if k != "zip_bytes"
    }


def _run_das_lote_job(app, job_id: str, payload: dict) -> None:
    """Função que roda na thread para emitir DAS em lote."""
    with app.app_context():
        try:
            selecionar_todas = bool(payload.get("selecionar_todas"))
            company_ids = payload.get("company_ids") or []
            tipo_das = payload.get("tipo_das") or "simples"
            periodo_apuracao = payload.get("periodo_apuracao") or ""
            data_consolidacao = payload.get("data_consolidacao")

            periodo_limpo = _only_digits(periodo_apuracao)
            data_consolidacao_limpa = _only_digits(data_consolidacao) if data_consolidacao else None

            if selecionar_todas:
                companies = (
                    Company.query.filter_by(ativo=True)
                    .order_by(Company.razao_social.asc())
                    .all()
                )
            else:
                ids = [int(v) for v in company_ids if v]
                companies = (
                    Company.query.filter(Company.id.in_(ids))
                    .order_by(Company.razao_social.asc())
                    .all()
                )

            total = len(companies)
            service = SerproDasService()
            zip_buffer = io.BytesIO()
            errors = []
            results = []
            generated = 0

            endpoint = "PGMEI/GERARDASPDF21" if tipo_das == "mei" else "PGDASD/GERARDAS12"
            prefix = "DAS_MEI" if tipo_das == "mei" else "DAS_SIMPLES"

            _set_das_batch_job(
                job_id, total=total, completed=0, progress=0,
                status="running", message="Iniciando geração em lote.",
            )

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for index, company in enumerate(companies, start=1):
                    cnpj_limpo = _only_digits(getattr(company, "cnpj", ""))
                    company_name = company.razao_social
                    label = f"{cnpj_limpo or 'SEM_CNPJ'} - {company_name}"

                    _set_das_batch_job(
                        job_id,
                        current_company=company_name,
                        message=f"Gerando {prefix.replace('_', ' ')} para {company_name}.",
                        progress=int((index - 1) / max(total, 1) * 100),
                    )

                    try:
                        if not cnpj_limpo:
                            raise ValueError("Empresa sem CNPJ informado.")

                        pdf_bytes = service.emitir_pdf(
                            contribuinte_numero=cnpj_limpo,
                            periodo_apuracao=periodo_limpo,
                            data_consolidacao=data_consolidacao_limpa or None,
                            tipo_das=tipo_das,
                        )

                        ApiUsageService.register_usage(
                            route_type="emitir",
                            endpoint=endpoint,
                            company_id=company.id,
                        )

                        filename = (
                            f"{prefix}_{cnpj_limpo}_"
                            f"{_safe_filename_part(company.razao_social)}_"
                            f"{periodo_limpo}.pdf"
                        )

                        if pdf_bytes:
                            zip_file.writestr(filename, pdf_bytes)
                            generated += 1
                            results.append({
                                "company_id": company.id,
                                "cnpj": cnpj_limpo,
                                "company_name": company_name,
                                "success": True,
                                "filename": filename,
                            })
                        else:
                            errors.append({
                                "company_id": company.id,
                                "cnpj": cnpj_limpo,
                                "company_name": company_name,
                                "error": "PDF não retornado pela SERPRO",
                            })

                    except Exception as exc:
                        errors.append({
                            "company_id": company.id,
                            "cnpj": cnpj_limpo,
                            "company_name": company_name,
                            "error": str(exc),
                        })

            _set_das_batch_job(
                job_id,
                status="completed",
                progress=100,
                completed=total,
                message=f"Concluído. {generated} guia(s) gerada(s).",
                errors=errors,
                results=results,
                zip_bytes=zip_buffer.getvalue(),
            )

        except Exception as exc:
            _set_das_batch_job(
                job_id,
                status="error",
                message=f"Erro fatal: {str(exc)}",
            )


# ========== ROTAS ==========


@das_bp.route("/emitir", methods=["POST"])
def emitir_das_simples():
    """Emite DAS Simples Nacional."""
    try:
        payload = request.get_json(silent=True) or {}
        contribuinte_numero = _only_digits(payload.get("contribuinte_numero") or "")
        periodo_apuracao = payload.get("periodo_apuracao") or ""
        data_consolidacao = payload.get("data_consolidacao")

        if not contribuinte_numero:
            return jsonify({"success": False, "error": "contribuinte_numero obrigatório"}), 400

        service = SerproDasService()
        pdf_bytes = service.emitir_pdf(
            contribuinte_numero=contribuinte_numero,
            periodo_apuracao=_only_digits(periodo_apuracao),
            data_consolidacao=_only_digits(data_consolidacao) if data_consolidacao else None,
            tipo_das="simples",
        )

        if pdf_bytes:
            return jsonify({
                "success": True,
                "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            })
        else:
            return jsonify({"success": False, "error": "PDF não retornado pela SERPRO"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@das_bp.route("/mei/emitir", methods=["POST"])
def emitir_das_mei():
    """Emite DAS MEI."""
    try:
        payload = request.get_json(silent=True) or {}
        contribuinte_numero = _only_digits(payload.get("contribuinte_numero") or "")
        periodo_apuracao = payload.get("periodo_apuracao") or ""
        data_consolidacao = payload.get("data_consolidacao")

        if not contribuinte_numero:
            return jsonify({"success": False, "error": "contribuinte_numero obrigatório"}), 400

        service = SerproDasService()
        pdf_bytes = service.emitir_pdf(
            contribuinte_numero=contribuinte_numero,
            periodo_apuracao=_only_digits(periodo_apuracao),
            data_consolidacao=_only_digits(data_consolidacao) if data_consolidacao else None,
            tipo_das="mei",
        )

        if pdf_bytes:
            return jsonify({
                "success": True,
                "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            })
        else:
            return jsonify({"success": False, "error": "PDF não retornado pela SERPRO"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@das_bp.get("/emitir")
def emitir_das_get_nao_permitido():
    """GET /emitir — retorna 405, método não permitido."""
    return jsonify({
        "success": False,
        "message": "Use POST para emitir o DAS.",
    }), 405


@das_bp.route("/emitir-lote", methods=["POST"])
def emitir_das_lote():
    """Emite DAS em lote — roda job em thread, retorna job_id."""
    try:
        payload = request.get_json(silent=True) or {}
        job_id = str(uuid.uuid4())[:8]

        _set_das_batch_job(job_id)

        thread = threading.Thread(
            target=_run_das_lote_job,
            args=(current_app._get_current_object(), job_id, payload),
            daemon=True,
        )
        thread.start()

        return jsonify({"success": True, "data": {"job_id": job_id}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@das_bp.route("/emitir-lote/iniciar", methods=["POST"])
def iniciar_emissao_das_lote():
    """Alias para emitir_das_lote."""
    return emitir_das_lote()


@das_bp.get("/emitir-lote/status/<job_id>")
def status_emissao_das_lote(job_id: str):
    """Retorna status de um job de emissão em lote."""
    job = _get_das_batch_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job não encontrado"}), 404
    return jsonify({"success": True, "data": _public_das_batch_job(job)})


@das_bp.get("/emitir-lote/download/<job_id>")
def download_emissao_das_lote(job_id: str):
    """Download do ZIP gerado pelo job de lote."""
    job = _get_das_batch_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job não encontrado"}), 404
    if job["status"] != "completed":
        return jsonify({"success": False, "error": "Job ainda em processamento"}), 400

    zip_bytes = job.get("zip_bytes")
    if not zip_bytes:
        return jsonify({"success": False, "error": "ZIP não disponível"}), 404

    return Response(
        zip_bytes,
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=DAS_LOTE_{job_id}.zip"},
    )


@das_bp.route("/dctfweb/emitir", methods=["POST"])
def emitir_darf_dctfweb():
    """Emite DARF DCTFWeb."""
    try:
        payload = request.get_json(silent=True) or {}
        contribuinte_numero = _only_digits(payload.get("contribuinte_numero") or "")
        categoria = payload.get("categoria") or ""
        competencia = payload.get("competencia")
        ano_pa = payload.get("ano_pa")

        if not contribuinte_numero:
            return jsonify({"success": False, "error": "contribuinte_numero obrigatório"}), 400

        service = SerproDasService()
        pdf_bytes = service.emitir_pdf_dctfweb(
            contribuinte_numero=contribuinte_numero,
            categoria=categoria,
            competencia=competencia,
            ano_pa=ano_pa,
        )

        if pdf_bytes:
            return jsonify({
                "success": True,
                "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            })
        else:
            return jsonify({"success": False, "error": "PDF não retornado pela SERPRO"}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@das_bp.route("/dctfweb/emitir-lote", methods=["POST"])
def emitir_darf_dctfweb_lote():
    """Emite DARF DCTFWeb em lote."""
    try:
        payload = request.get_json(silent=True) or {}
        job_id = str(uuid.uuid4())[:8]

        # Reutiliza a mesma infra de jobs
        _set_das_batch_job(job_id)

        def run_dctfweb_lote(app, jid, p):
            with app.app_context():
                try:
                    company_ids = p.get("company_ids") or []
                    categoria = p.get("categoria") or ""
                    competencia = p.get("competencia")
                    ano_pa = p.get("ano_pa")

                    ids = [int(v) for v in company_ids if v]
                    companies = (
                        Company.query.filter(Company.id.in_(ids))
                        .order_by(Company.razao_social.asc())
                        .all()
                    )

                    total = len(companies)
                    service = SerproDasService()
                    zip_buffer = io.BytesIO()
                    errors = []
                    generated = 0

                    _set_das_batch_job(
                        jid, total=total, status="running",
                        message="Iniciando geração DARF em lote.",
                    )

                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                        for idx, company in enumerate(companies, start=1):
                            cnpj_limpo = _only_digits(getattr(company, "cnpj", ""))
                            try:
                                pdf_bytes = service.emitir_pdf_dctfweb(
                                    contribuinte_numero=cnpj_limpo,
                                    categoria=categoria,
                                    competencia=competencia,
                                    ano_pa=ano_pa,
                                )
                                if pdf_bytes:
                                    fname = f"DARF_{cnpj_limpo}_{_safe_filename_part(company.razao_social)}.pdf"
                                    zf.writestr(fname, pdf_bytes)
                                    generated += 1

                                ApiUsageService.register_usage(
                                    route_type="emitir",
                                    endpoint="DCTFWEB/GERARGUIA31",
                                    company_id=company.id,
                                )
                            except Exception as exc:
                                errors.append({
                                    "company_id": company.id,
                                    "error": str(exc),
                                })

                            _set_das_batch_job(
                                jid,
                                progress=int(idx / max(total, 1) * 100),
                                completed=idx,
                            )

                    _set_das_batch_job(
                        jid,
                        status="completed",
                        progress=100,
                        message=f"Concluído. {generated} DARF(s) gerado(s).",
                        errors=errors,
                        zip_bytes=zip_buffer.getvalue(),
                    )
                except Exception as exc:
                    _set_das_batch_job(
                        jid, status="error", message=f"Erro: {str(exc)}",
                    )

        thread = threading.Thread(
            target=run_dctfweb_lote,
            args=(current_app._get_current_object(), job_id, payload),
            daemon=True,
        )
        thread.start()

        return jsonify({"success": True, "data": {"job_id": job_id}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
