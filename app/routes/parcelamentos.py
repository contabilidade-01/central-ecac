"""
Rotas para Parcelamentos via SERPRO.

Reconstituído fielmente do bytecode do exe.
Fonte da verdade: dis/routes/parcelamentos.txt (2005 linhas)

URLs do exe (13 rotas GET + POST):
  GET    /tipos
  POST   /empresa/<int:company_id>/flags
  POST   /buscar-pedidos-empresa/<int:company_id>
  POST   /buscar-pedidos-todas
  GET    /pedidos
  POST   /buscar-parcelas-empresa/<int:company_id>
  POST   /buscar-parcelas-todas-ativas
  GET    /status
  POST   /liberar-processamento
  GET    /parcelas
  GET    /parcelas/zip-hoje
  GET    /pdf/<int:parcela_id>
  POST   /abrir-pasta/<int:company_id>
"""

from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, jsonify, request, send_file, current_app

from app.extensions import db
from app.models import Company, ParcelamentoParcela, ParcelamentoPedido
from app.services.parcelamentos_serpro_service import (
    ParcelamentosSerproService,
    PARCELAMENTO_TYPES,
    update_parcelamento_progress,
)
from app.utils.paths import app_data_dir


parcelamentos_bp = Blueprint("parcelamentos", __name__, url_prefix="/parcelamentos")
service = ParcelamentosSerproService()

# Auto-destravamento: timeout de 30 minutos
PROCESSING_STALE_AFTER = timedelta(minutes=30)


def _is_processing_stale(company: Company) -> bool:
    """Verifica se processamento está travado (stale)."""
    if company.processing_status != "processing":
        return False
    if company.last_processed_at:
        return True
    if not company.processing_started_at:
        return True
    return (datetime.now() - company.processing_started_at) > PROCESSING_STALE_AFTER


def _release_processing(company: Company, message=None) -> None:
    """Libera processamento de uma empresa."""
    company.processing_status = "idle"
    company.processing_progress = 0
    company.processing_message = (
        message or "Processamento liberado. Pode executar novamente."
    )
    company.last_processed_at = datetime.now()


def _release_stale_processing(company: Company) -> bool:
    """Libera processamento travado automaticamente."""
    if not _is_processing_stale(company):
        return False
    _release_processing(
        company,
        "Processamento anterior foi liberado automaticamente por estar travado.",
    )
    db.session.commit()
    return True


def _run_pedidos_empresa_job(app, company_id, tipos):
    """Linha 60 do exe. Antes era um `run_job` aninhado dentro da rota."""
    with app.app_context():
        try:
            ParcelamentosSerproService().buscar_pedidos_empresa(company_id, tipos=tipos)
        finally:
            db.session.remove()


def _run_pedidos_todas_job(app):
    """Linha 68 do exe — as empresas rodam em PARALELO (até 4 por vez)."""
    with app.app_context():
        try:
            company_ids = [
                row[0] for row in db.session.query(Company.id)
                .filter(Company.ativo == True)  # noqa: E712 — como no exe
                .all()
            ]
        finally:
            db.session.remove()

    max_workers = min(4, max(len(company_ids), 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_run_pedidos_empresa_job, app, company_id, None)
            for company_id in company_ids
        ]
        for future in as_completed(futures):
            future.result()


def _run_parcelas_empresa_job(app, company_id, tipos, emitir_pdfs):
    """Linha 88 do exe."""
    with app.app_context():
        try:
            ParcelamentosSerproService().buscar_parcelas_empresa(
                company_id, tipos=tipos, emitir_pdfs=emitir_pdfs)
        finally:
            db.session.remove()


def _run_parcelas_todas_job(app, emitir_pdfs):
    """Linha 100 do exe — mesmas 4 threads em paralelo."""
    with app.app_context():
        try:
            company_ids = [
                row[0] for row in db.session.query(Company.id)
                .filter(Company.ativo == True)  # noqa: E712 — como no exe
                .all()
            ]
        finally:
            db.session.remove()

    max_workers = min(4, max(len(company_ids), 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_run_parcelas_empresa_job, app, company_id, None, emitir_pdfs)
            for company_id in company_ids
        ]
        for future in as_completed(futures):
            future.result()


@parcelamentos_bp.get("/tipos")
def tipos():
    """Lista tipos de parcelamento disponíveis."""
    # O exe devolve LISTA pura com as chaves ('tipo', 'label', 'flag').
    return jsonify([
        {
            "tipo": chave,
            "label": cfg["label"],
            "flag": cfg["flag"],
        }
        for chave, cfg in PARCELAMENTO_TYPES.items()
    ])


@parcelamentos_bp.post("/empresa/<int:company_id>/flags")
def salvar_flags_empresa(company_id: int):
    """Salva flags de tipos de parcelamento para empresa."""
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({"success": False, "message": "Empresa não encontrada"}), 404

    data = request.get_json() or {}
    for chave, cfg in PARCELAMENTO_TYPES.items():
        flag = cfg["flag"]
        if flag in data:
            setattr(company, flag, bool(data[flag]))

    db.session.commit()
    return jsonify({"success": True})


@parcelamentos_bp.post("/buscar-pedidos-empresa/<int:company_id>")
def buscar_pedidos_empresa(company_id: int):
    """Busca pedidos de parcelamento para uma empresa (thread)."""
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({"success": False, "message": "Empresa não encontrada"}), 404

    # Auto-destravamento se stale
    _release_stale_processing(company)

    thread = threading.Thread(
        target=_run_pedidos_empresa_job,
        args=(current_app._get_current_object(), company_id, None),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True, "company_id": company_id})


@parcelamentos_bp.post("/buscar-pedidos-todas")
def buscar_pedidos_todas():
    """Busca pedidos de todas as empresas (thread)."""
    thread = threading.Thread(
        target=_run_pedidos_todas_job,
        args=(current_app._get_current_object(),),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True})


@parcelamentos_bp.get("/pedidos")
def listar_pedidos():
    """Lista pedidos de parcelamento armazenados."""
    query = db.session.query(ParcelamentoPedido)

    tipo = request.args.get("type")
    if tipo:
        query = query.filter(ParcelamentoPedido.tipo == tipo)

    ativos = request.args.get("ativos")
    if ativos in ("1", "true", "True"):
        query = query.filter(ParcelamentoPedido.ativo.is_(True))

    # O exe devolve LISTA pura, incluindo nome e CNPJ da empresa.
    return jsonify([
        {
            "id": p.id,
            "company_id": p.company_id,
            "company_name": p.company.razao_social if p.company else None,
            "company_cnpj": p.company.cnpj if p.company else None,
            "tipo": p.tipo,
            "numero": p.numero,
            "data_pedido": p.data_pedido.isoformat() if p.data_pedido else None,
            "situacao": p.situacao,
            "data_situacao": p.data_situacao.isoformat() if p.data_situacao else None,
            "ativo": p.ativo,
        }
        for p in query.all()
    ])


@parcelamentos_bp.post("/buscar-parcelas-empresa/<int:company_id>")
def buscar_parcelas_empresa(company_id: int):
    """Busca parcelas de parcelamento para uma empresa (thread)."""
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({"success": False, "message": "Empresa não encontrada"}), 404

    # Auto-destravamento se stale
    _release_stale_processing(company)

    thread = threading.Thread(
        target=_run_parcelas_empresa_job,
        args=(current_app._get_current_object(), company_id, None, True),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True, "company_id": company_id})


@parcelamentos_bp.post("/buscar-parcelas-todas-ativas")
def buscar_parcelas_todas_ativas():
    """Busca parcelas de todas as empresas com pedidos ativos (thread)."""
    thread = threading.Thread(
        target=_run_parcelas_todas_job,
        args=(current_app._get_current_object(), True),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True})


@parcelamentos_bp.get("/status")
def status_processamento():
    """Retorna status de processamento de todas as empresas."""
    companies = db.session.query(Company).filter_by(ativo=True).all()

    items = [
        {
            "id": c.id,
            "razao_social": c.razao_social,
            "cnpj": c.cnpj,
            "processing_status": c.processing_status,
            "processing_step": c.processing_step,
            "processing_progress": c.processing_progress,
            "processing_message": c.processing_message,
            "processing_started_at": (c.processing_started_at.isoformat()
                                      if c.processing_started_at else None),
            "last_processed_at": (c.last_processed_at.isoformat()
                                  if c.last_processed_at else None),
        }
        for c in companies
    ]

    # O exe devolve {'has_processing': bool, 'items': [...]}
    return jsonify({
        "has_processing": any(i["processing_status"] == "processing" for i in items),
        "items": items,
    })


@parcelamentos_bp.post("/liberar-processamento")
def liberar_processamento():
    """Libera processamento travado (timeout)."""
    data = request.get_json(silent=True) or {}
    company_id = data.get("company_id")

    liberadas = 0

    if company_id:
        company = db.session.get(Company, company_id)
        if company and company.processing_status == "processing":
            _release_processing(company)
            liberadas = 1
    else:
        for company in db.session.query(Company).filter_by(
                processing_status="processing").all():
            _release_processing(company)
            liberadas += 1

    if liberadas:
        db.session.commit()

    # O exe devolve {'success', 'released', 'message'}
    return jsonify({
        "success": True,
        "released": liberadas,
        "message": (f"{liberadas} processamento(s) liberado(s)."
                    if liberadas else "Nenhum processamento travado."),
    })


@parcelamentos_bp.get("/parcelas")
def listar_parcelas():
    """Lista parcelas de parcelamento armazenadas."""
    query = db.session.query(ParcelamentoParcela)

    tipo = request.args.get("type")
    if tipo:
        query = query.filter(ParcelamentoParcela.tipo == tipo)

    # O exe devolve LISTA pura. Em vez do caminho do PDF, expõe has_pdf +
    # download_message/download_status.
    return jsonify([
        {
            "id": p.id,
            "company_id": p.company_id,
            "company_name": p.company.razao_social if p.company else None,
            "company_cnpj": p.company.cnpj if p.company else None,
            "tipo": p.tipo,
            "parcela": p.parcela,
            "descricao": p.descricao,
            "data_vencimento": p.data_vencimento.isoformat() if p.data_vencimento else None,
            "valor_total": float(p.valor_total or 0),
            "has_pdf": bool(p.pdf_local_path),
            "download_message": None,
            "download_status": None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in query.all()
    ])


@parcelamentos_bp.get("/parcelas/zip-hoje")
def exportar_zip_parcelas_hoje():
    """Exporta ZIP com parcelas de hoje em memória."""
    hoje = datetime.now().date()
    parcelas = (
        db.session.query(ParcelamentoParcela)
        .filter(db.func.DATE(ParcelamentoParcela.created_at) == hoje)
        .all()
    )

    # Monta ZIP em memória. Nomes e mensagens conforme o bytecode do exe:
    # item = "<cnpj>_parcela_<parcela>.pdf" (partes saneadas, limite de 80 chars)
    # arquivo = "parcelas_baixadas_hoje_%Y%m%d.zip"
    zip_buffer = BytesIO()
    incluidas = 0
    with ZipFile(zip_buffer, "w", ZIP_DEFLATED) as zf:
        for parcela in parcelas:
            if parcela.pdf_local_path and Path(parcela.pdf_local_path).exists():
                cnpj = (parcela.company.cnpj if parcela.company else "") or ""
                nome = f"{cnpj}_parcela_{parcela.parcela}"[:80] + ".pdf"
                with open(parcela.pdf_local_path, "rb") as pdf_file:
                    zf.writestr(nome, pdf_file.read())
                incluidas += 1

    if not incluidas:
        return jsonify({
            "success": False,
            "message": "Nenhuma parcela baixada hoje com PDF encontrado.",
        }), 404

    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        as_attachment=True,
        mimetype="application/zip",
        download_name=f"parcelas_baixadas_hoje_{datetime.now():%Y%m%d}.zip",
    )


@parcelamentos_bp.get("/pdf/<int:parcela_id>")
def baixar_pdf(parcela_id: int):
    """Baixa PDF de uma parcela."""
    parcela = db.session.get(ParcelamentoParcela, parcela_id)
    if not parcela or not parcela.pdf_local_path:
        return jsonify({"success": False, "message": "PDF não encontrado"}), 404

    pdf_path = Path(parcela.pdf_local_path)
    if not pdf_path.exists():
        return jsonify({
            "success": False,
            "message": "Arquivo PDF não encontrado",
        }), 404

    return send_file(
        pdf_path,
        as_attachment=True,
        mimetype="application/pdf",
        download_name=f"parcela_{parcela.tipo}_{parcela.parcela}.pdf",
    )


@parcelamentos_bp.post("/abrir-pasta/<int:company_id>")
def abrir_pasta(company_id: int):
    """Abre pasta de parcelas no Explorer (Windows)."""
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({"success": False, "message": "Empresa não encontrada"}), 404

    pasta = Path(app_data_dir()) / "parcelas" / str(company_id)
    if not pasta.exists():
        pasta.mkdir(parents=True, exist_ok=True)

    try:
        # Abre Explorer no Windows
        if os.name == "nt":
            subprocess.Popen(f'explorer "{pasta}"')
        else:
            subprocess.Popen(["open", str(pasta)])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
