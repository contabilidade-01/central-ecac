"""Rotas para emissão de DAS / DARF DCTFWeb.

A SPA compilada (idêntica à do exe) é o contrato destas rotas:

  POST /emitir e /mei/emitir e /dctfweb/emitir
      corpo: company_id + cnpj (não contribuinte_numero)
      sucesso: PDF binário; erro: {message}

  POST /emitir-lote/iniciar + GET status + GET download
      iniciar/status devolvem o job no raiz (não {success, data})
      status: queued | running | finished | error
      errors/generated são números; results[].status é ok|error

  POST /dctfweb/emitir-lote
      ZIP síncrono + cabeçalhos X-Dctfweb-Generated e X-Dctfweb-Errors
"""

from __future__ import annotations

import io
import threading
import uuid
import zipfile
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request

from app.extensions import db
from app.models import Company
from app.services.api_usage_service import ApiUsageService
from app.services.serpro_das_service import SerproApiError, SerproDasService


das_bp = Blueprint("das", __name__)

DAS_BATCH_JOBS: dict = {}
DAS_BATCH_LOCK = threading.Lock()


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


def _erro(mensagem: str, codigo: int = 400):
    return jsonify({"success": False, "message": mensagem}), codigo


def _falha_emissao(exc: Exception):
    if isinstance(exc, SerproApiError):
        codigo = 400 if 400 <= exc.status_code < 500 else 502
        return _erro(str(exc), codigo)
    return _erro(str(exc), 500)


def _pdf_response(pdf_bytes: bytes, filename: str) -> Response:
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _zip_response(zip_bytes: bytes, filename: str, extra_headers: dict | None = None) -> Response:
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        **(extra_headers or {}),
    }
    return Response(zip_bytes, mimetype="application/zip", headers=headers)


def _contribuinte_do_payload(payload: dict) -> str:
    """A SPA manda cnpj/company_id; o exe também aceitava contribuinte_numero."""
    numero = _only_digits(
        payload.get("contribuinte_numero")
        or payload.get("cnpj")
        or (payload.get("contribuinte") or {}).get("numero")
        or ""
    )
    if numero:
        return numero
    company_id = payload.get("company_id")
    if company_id not in (None, ""):
        try:
            company = db.session.get(Company, int(company_id))
        except (TypeError, ValueError):
            company = None
        if company:
            return _only_digits(getattr(company, "cnpj", ""))
    return ""


def _empresas_do_lote(payload: dict) -> list:
    if payload.get("selecionar_todas"):
        return (
            Company.query.filter_by(ativo=True)
            .order_by(Company.razao_social.asc())
            .all()
        )
    ids = []
    for value in payload.get("company_ids") or []:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []
    return (
        Company.query.filter(Company.id.in_(ids))
        .order_by(Company.razao_social.asc())
        .all()
    )


def _set_das_batch_job(job_id: str, **kwargs) -> None:
    with DAS_BATCH_LOCK:
        if job_id not in DAS_BATCH_JOBS:
            DAS_BATCH_JOBS[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "total": 0,
                "completed": 0,
                "generated": 0,
                "errors": 0,
                "progress": 0,
                "message": "",
                "current_company": "",
                "results": [],
                "filename": f"DAS_LOTE_{job_id}.zip",
                "zip_bytes": None,
                "created_at": datetime.utcnow().isoformat(),
            }
        DAS_BATCH_JOBS[job_id].update(kwargs)


def _get_das_batch_job(job_id: str) -> dict | None:
    with DAS_BATCH_LOCK:
        job = DAS_BATCH_JOBS.get(job_id)
        return dict(job) if job else None


def _public_das_batch_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "zip_bytes"}


def _run_das_lote_job(app, job_id: str, payload: dict) -> None:
    with app.app_context():
        try:
            tipo_das = payload.get("tipo_das") or "simples"
            periodo_limpo = _only_digits(payload.get("periodo_apuracao") or "")
            data_consolidacao = payload.get("data_consolidacao")
            data_consolidacao_limpa = (
                _only_digits(data_consolidacao) if data_consolidacao else None
            )

            companies = _empresas_do_lote(payload)
            total = len(companies)
            service = SerproDasService()
            zip_buffer = io.BytesIO()
            results = []
            generated = 0
            error_count = 0

            endpoint = "PGMEI/GERARDASPDF21" if tipo_das == "mei" else "PGDASD/GERARDAS12"
            prefix = "DAS_MEI" if tipo_das == "mei" else "DAS_SIMPLES"
            filename = f"{prefix}_LOTE_{periodo_limpo or job_id}.zip"

            _set_das_batch_job(
                job_id,
                total=total,
                completed=0,
                generated=0,
                errors=0,
                progress=0,
                status="running",
                filename=filename,
                message="Iniciando geração em lote.",
            )

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for index, company in enumerate(companies, start=1):
                    cnpj_limpo = _only_digits(getattr(company, "cnpj", ""))
                    company_name = company.razao_social or "SEM_NOME"

                    _set_das_batch_job(
                        job_id,
                        current_company=company_name,
                        message=f"Gerando {prefix.replace('_', ' ')} para {company_name}.",
                        progress=int((index - 1) / max(total, 1) * 100),
                        completed=index - 1,
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

                        if not pdf_bytes:
                            raise ValueError("PDF não retornado pela SERPRO")

                        ApiUsageService.register_usage(
                            route_type="emitir",
                            endpoint=endpoint,
                            company_id=company.id,
                        )

                        pdf_name = (
                            f"{prefix}_{cnpj_limpo}_"
                            f"{_safe_filename_part(company.razao_social)}_"
                            f"{periodo_limpo}.pdf"
                        )
                        zip_file.writestr(pdf_name, pdf_bytes)
                        generated += 1
                        results.append({
                            "company_id": company.id,
                            "cnpj": cnpj_limpo,
                            "company_name": company_name,
                            "status": "ok",
                            "message": pdf_name,
                        })

                    except Exception as exc:
                        error_count += 1
                        results.append({
                            "company_id": company.id,
                            "cnpj": cnpj_limpo,
                            "company_name": company_name,
                            "status": "error",
                            "message": str(exc),
                        })

                    _set_das_batch_job(
                        job_id,
                        completed=index,
                        generated=generated,
                        errors=error_count,
                        results=list(results),
                        progress=int(index / max(total, 1) * 100),
                    )

            _set_das_batch_job(
                job_id,
                status="finished",
                progress=100,
                completed=total,
                generated=generated,
                errors=error_count,
                results=results,
                current_company="",
                message=f"Concluído. {generated} guia(s) gerada(s).",
                zip_bytes=zip_buffer.getvalue(),
            )

        except Exception as exc:
            _set_das_batch_job(
                job_id,
                status="error",
                message=f"Erro fatal: {str(exc)}",
            )


def _iniciar_job_das(payload: dict):
    job_id = str(uuid.uuid4())[:8]
    _set_das_batch_job(job_id, status="queued", message="Iniciando processamento.")
    thread = threading.Thread(
        target=_run_das_lote_job,
        args=(current_app._get_current_object(), job_id, payload),
        daemon=True,
    )
    thread.start()
    job = _get_das_batch_job(job_id)
    return jsonify(_public_das_batch_job(job))


# ========== ROTAS ==========


@das_bp.route("/emitir", methods=["POST"])
def emitir_das_simples():
    try:
        payload = request.get_json(silent=True) or {}
        contribuinte_numero = _contribuinte_do_payload(payload)
        periodo_apuracao = _only_digits(payload.get("periodo_apuracao") or "")
        data_consolidacao = payload.get("data_consolidacao")

        if not contribuinte_numero:
            return _erro("CNPJ da empresa não informado.")
        if len(periodo_apuracao) != 6:
            return _erro("Informe a competência no formato AAAAMM.")

        service = SerproDasService()
        pdf_bytes = service.emitir_pdf(
            contribuinte_numero=contribuinte_numero,
            periodo_apuracao=periodo_apuracao,
            data_consolidacao=_only_digits(data_consolidacao) if data_consolidacao else None,
            tipo_das="simples",
        )
        if not pdf_bytes:
            return _erro("PDF não retornado pela SERPRO", 500)
        return _pdf_response(pdf_bytes, f"DAS_{contribuinte_numero}_{periodo_apuracao}.pdf")
    except Exception as e:
        return _falha_emissao(e)


@das_bp.route("/mei/emitir", methods=["POST"])
def emitir_das_mei():
    try:
        payload = request.get_json(silent=True) or {}
        contribuinte_numero = _contribuinte_do_payload(payload)
        periodo_apuracao = _only_digits(payload.get("periodo_apuracao") or "")

        if not contribuinte_numero:
            return _erro("CNPJ da empresa não informado.")
        if len(periodo_apuracao) != 6:
            return _erro("Informe a competência no formato AAAAMM.")

        service = SerproDasService()
        pdf_bytes = service.emitir_pdf(
            contribuinte_numero=contribuinte_numero,
            periodo_apuracao=periodo_apuracao,
            tipo_das="mei",
        )
        if not pdf_bytes:
            return _erro("PDF não retornado pela SERPRO", 500)
        return _pdf_response(pdf_bytes, f"DAS_MEI_{contribuinte_numero}_{periodo_apuracao}.pdf")
    except Exception as e:
        return _falha_emissao(e)


@das_bp.get("/emitir")
def emitir_das_get_nao_permitido():
    return _erro("Use POST para emitir o DAS.", 405)


@das_bp.route("/emitir-lote", methods=["POST"])
def emitir_das_lote():
    try:
        payload = request.get_json(silent=True) or {}
        return _iniciar_job_das(payload)
    except Exception as e:
        return _erro(str(e), 500)


@das_bp.route("/emitir-lote/iniciar", methods=["POST"])
def iniciar_emissao_das_lote():
    return emitir_das_lote()


@das_bp.get("/emitir-lote/status/<job_id>")
def status_emissao_das_lote(job_id: str):
    job = _get_das_batch_job(job_id)
    if not job:
        return _erro("Job não encontrado", 404)
    return jsonify(_public_das_batch_job(job))


@das_bp.get("/emitir-lote/download/<job_id>")
def download_emissao_das_lote(job_id: str):
    job = _get_das_batch_job(job_id)
    if not job:
        return _erro("Job não encontrado", 404)
    if job["status"] != "finished":
        return _erro("Job ainda em processamento")
    zip_bytes = job.get("zip_bytes")
    if not zip_bytes:
        return _erro("ZIP não disponível", 404)
    return _zip_response(zip_bytes, job.get("filename") or f"DAS_LOTE_{job_id}.zip")


@das_bp.route("/dctfweb/emitir", methods=["POST"])
def emitir_darf_dctfweb():
    try:
        payload = request.get_json(silent=True) or {}
        contribuinte_numero = _contribuinte_do_payload(payload)
        categoria = payload.get("categoria") or ""
        competencia = payload.get("competencia")
        ano_pa = payload.get("ano_pa")

        if not contribuinte_numero:
            return _erro("CNPJ da empresa não informado.")

        service = SerproDasService()
        pdf_bytes = service.emitir_pdf_dctfweb(
            contribuinte_numero=contribuinte_numero,
            categoria=categoria,
            competencia=competencia,
            ano_pa=ano_pa,
        )
        if not pdf_bytes:
            return _erro("PDF não retornado pela SERPRO", 500)

        if str(categoria) == "GERAL_13o_SALARIO":
            sufixo = _only_digits(ano_pa or competencia or "")[:4]
            filename = f"DARF_DCTFWEB_13_{contribuinte_numero}_{sufixo}.pdf"
        else:
            sufixo = _only_digits(competencia or "")
            filename = f"DARF_DCTFWEB_{contribuinte_numero}_{sufixo}.pdf"
        return _pdf_response(pdf_bytes, filename)
    except Exception as e:
        return _falha_emissao(e)


@das_bp.route("/dctfweb/emitir-lote", methods=["POST"])
def emitir_darf_dctfweb_lote():
    """A SPA espera o ZIP na mesma resposta, não job em background."""
    try:
        payload = request.get_json(silent=True) or {}
        categoria = payload.get("categoria") or ""
        competencia = payload.get("competencia")
        ano_pa = payload.get("ano_pa")

        companies = _empresas_do_lote(payload)
        if not companies:
            return _erro("Selecione ao menos uma empresa.")

        service = SerproDasService()
        zip_buffer = io.BytesIO()
        generated = 0
        error_count = 0

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for company in companies:
                cnpj_limpo = _only_digits(getattr(company, "cnpj", ""))
                try:
                    if not cnpj_limpo:
                        raise ValueError("Empresa sem CNPJ informado.")
                    pdf_bytes = service.emitir_pdf_dctfweb(
                        contribuinte_numero=cnpj_limpo,
                        categoria=categoria,
                        competencia=competencia,
                        ano_pa=ano_pa,
                    )
                    if not pdf_bytes:
                        raise ValueError("PDF não retornado pela SERPRO")
                    fname = (
                        f"DARF_{cnpj_limpo}_"
                        f"{_safe_filename_part(company.razao_social)}.pdf"
                    )
                    zf.writestr(fname, pdf_bytes)
                    generated += 1
                    ApiUsageService.register_usage(
                        route_type="emitir",
                        endpoint="DCTFWEB/GERARGUIA31",
                        company_id=company.id,
                    )
                except Exception:
                    error_count += 1

        if str(categoria) == "GERAL_13o_SALARIO":
            periodo = _only_digits(ano_pa or competencia or "")[:4]
        else:
            periodo = _only_digits(competencia or "")
        filename = f"DARF_DCTFWEB_LOTE_{periodo or 'LOTE'}.zip"

        return _zip_response(
            zip_buffer.getvalue(),
            filename,
            extra_headers={
                "X-Dctfweb-Generated": str(generated),
                "X-Dctfweb-Errors": str(error_count),
            },
        )
    except Exception as e:
        return _erro(str(e), 500)
