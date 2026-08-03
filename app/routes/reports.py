import io
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file, current_app

from app.extensions import db
from app.models import Company, RelatorioSitFiscal, AppSetting
from app.services.report_service import ReportService
import threading

reports_bp = Blueprint('reports', __name__)
service = ReportService()


def _safe_filename_part(value):
    allowed = []
    for char in str(value or '').strip():
        if char.isalnum() or char in (' ', '-', '_'):
            allowed.append(char)
        else:
            allowed.append('_')

    normalized = ' '.join(''.join(allowed).split())
    return normalized.replace(' ', '_')[:80] or 'EMPRESA'


def _unique_zip_name(used_names, filename):
    if filename not in used_names:
        used_names.add(filename)
        return filename

    stem, suffix = filename.rsplit('.', 1) if '.' in filename else (filename, '')
    index = 2
    while True:
        candidate = f'{stem}_{index}.{suffix}' if suffix else f'{stem}_{index}'
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index += 1


@reports_bp.post('/process-company/<int:company_id>')
def process_company(company_id: int):
    company = db.session.get(Company, company_id)

    if not company:
        return jsonify({
            'success': False,
            'message': 'Empresa não encontrada',
        }), 404

    if company.processing_status == 'processing':
        return jsonify({
            'success': False,
            'message': 'Empresa já está em processamento',
        }), 409

    def run_job(app, cid):
        with app.app_context():
            local_service = ReportService()
            local_service.process_company(cid)

    thread = threading.Thread(
        target=run_job,
        args=(current_app._get_current_object(), company_id),
        daemon=True,
    )
    thread.start()

    return jsonify({
        'success': True,
        'message': 'Processamento iniciado',
        'company_id': company_id,
    }), 200


@reports_bp.post('/process-all')
def process_all():
    def run_job(app):
        with app.app_context():
            service = ReportService()
            service.process_all_background()

    thread = threading.Thread(
        target=run_job,
        args=(current_app._get_current_object(),),
        daemon=True,
    )
    thread.start()

    return jsonify({
        'success': True,
        'message': 'Processamento de todas as empresas iniciado.',
    }), 200


@reports_bp.get('/company/<int:company_id>/latest')
def latest_company_report(company_id: int):
    relatorio = (
        RelatorioSitFiscal.query
        .filter_by(company_id=company_id)
        .order_by(RelatorioSitFiscal.id.desc())
        .first()
    )

    if not relatorio:
        return jsonify({'success': False, 'message': 'Relatório não encontrado'}), 404

    return jsonify({
        'report_id': relatorio.id,
        'company_id': relatorio.company_id,
        'situacao': relatorio.situacao,
        'data_hora': relatorio.data_hora.isoformat() if relatorio.data_hora else None,
        'pendencias': [
            {
                'id': p.id,
                'tipo': p.tipo,
                'ano': p.ano,
                'meses': p.meses_json,
            }
            for p in relatorio.pendencias
        ],
        'debitos': [
            {
                'id': d.id,
                'tipo': d.tipo,
                'receita': d.receita,
                'periodo_apuracao': d.periodo_apuracao,
                'data_vencimento': (d.data_vencimento.isoformat()
                                    if d.data_vencimento else None),
                'valor_original': float(d.valor_original or 0),
                'saldo_devedor': float(d.saldo_devedor or 0),
                'multa': float(d.multa or 0),
                'juros': float(d.juros or 0),
                'saldo_devedor_total': float(d.saldo_devedor_total or 0),
                'situacao': d.situacao,
            }
            for d in relatorio.debitos
        ],
    })


@reports_bp.get('/company/<int:company_id>/pendencias')
def company_pendencias(company_id: int):
    relatorio = (
        RelatorioSitFiscal.query
        .filter_by(company_id=company_id)
        .order_by(RelatorioSitFiscal.id.desc())
        .first()
    )

    if not relatorio:
        return jsonify([])

    return jsonify([
        {
            'id': p.id,
            'tipo': p.tipo,
            'ano': p.ano,
            'meses': p.meses_json,
        }
        for p in relatorio.pendencias
    ])


@reports_bp.get('/company/<int:company_id>/debitos')
def company_debitos(company_id: int):
    relatorio = (
        RelatorioSitFiscal.query
        .filter_by(company_id=company_id)
        .order_by(RelatorioSitFiscal.id.desc())
        .first()
    )

    if not relatorio:
        return jsonify([])

    return jsonify([
        {
            'id': d.id,
            'tipo': d.tipo,
            'receita': d.receita,
            'periodo_apuracao': d.periodo_apuracao,
            'data_vencimento': (d.data_vencimento.isoformat()
                                if d.data_vencimento else None),
            'valor_original': float(d.valor_original or 0),
            'saldo_devedor': float(d.saldo_devedor or 0),
            'multa': float(d.multa or 0),
            'juros': float(d.juros or 0),
            'saldo_devedor_total': float(d.saldo_devedor_total or 0),
            'situacao': d.situacao,
        }
        for d in relatorio.debitos
    ])


@reports_bp.get('/download-pdf/<int:report_id>')
def download_pdf(report_id: int):
    relatorio = db.session.get(RelatorioSitFiscal, report_id)
    if not relatorio or not relatorio.pdf_local_path:
        return jsonify({'success': False, 'message': 'PDF não encontrado'}), 404

    path = Path(relatorio.pdf_local_path)
    if not path.exists():
        return jsonify({'success': False,
                        'message': 'Arquivo PDF não encontrado'}), 404

    return send_file(path, as_attachment=True,
                     download_name=f'relatorio_{relatorio.company.cnpj}.pdf')


@reports_bp.get('/company/<int:company_id>/download-pdf/latest')
def download_latest_company_pdf(company_id: int):
    relatorio = (
        RelatorioSitFiscal.query
        .filter_by(company_id=company_id)
        .order_by(RelatorioSitFiscal.id.desc())
        .first()
    )

    if not relatorio or not relatorio.pdf_local_path:
        return jsonify({'success': False, 'message': 'PDF não encontrado'}), 404

    path = Path(relatorio.pdf_local_path)
    if not path.exists():
        return jsonify({'success': False,
                        'message': 'Arquivo PDF não encontrado'}), 404

    return send_file(path, as_attachment=True,
                     download_name=f'relatorio_{relatorio.company.cnpj}.pdf')


@reports_bp.post('/download-pdfs/latest-zip')
def download_latest_company_pdfs_zip():
    payload = request.get_json(silent=True) or {}
    company_ids = payload.get('company_ids') or []

    ids = []
    for value in company_ids:
        try:
            parsed = int(value)
        except Exception:
            continue
        if parsed not in ids:
            ids.append(parsed)

    if not ids:
        return jsonify({
            'success': False,
            'message': 'Nenhuma empresa informada para gerar o ZIP.',
        }), 400

    zip_buffer = io.BytesIO()
    used_names = set()
    errors = []
    generated = 0

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for company_id in ids:
            company = db.session.get(Company, company_id)

            if not company:
                errors.append(f'Empresa ID {company_id}: empresa não encontrada.')
                continue

            relatorio = (
                RelatorioSitFiscal.query
                .filter_by(company_id=company.id)
                .order_by(RelatorioSitFiscal.id.desc())
                .first()
            )

            if not relatorio or not relatorio.pdf_local_path:
                errors.append(
                    f'{company.cnpj} - {company.razao_social}: PDF não encontrado.')
                continue

            path = Path(relatorio.pdf_local_path)

            if not path.exists():
                errors.append(
                    f'{company.cnpj} - {company.razao_social}: '
                    f'arquivo PDF não encontrado.')
                continue

            filename = _unique_zip_name(
                used_names,
                f'situacao_fiscal_{company.cnpj}_'
                f'{_safe_filename_part(company.razao_social)}.pdf',
            )

            zip_file.write(path, filename)
            generated += 1

        if errors:
            zip_file.writestr('erros_situacao_fiscal.txt',
                              '\n'.join(errors).encode('utf-8'))

    if generated == 0:
        return jsonify({
            'success': False,
            'message': 'Nenhum PDF de situação fiscal encontrado para as '
                       'empresas filtradas.',
        }), 404

    zip_buffer.seek(0)
    filename = f"situacao_fiscal_filtrada_{datetime.now():%Y%m%d_%H%M%S}.zip"

    return Response(
        zip_buffer.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@reports_bp.get('/diagnostic-data/<int:report_id>')
def diagnostic_data(report_id: int):
    relatorio = db.session.get(RelatorioSitFiscal, report_id)
    if not relatorio:
        return jsonify({'success': False, 'message': 'Relatório não encontrado'}), 404

    settings = AppSetting.query.first()

    return jsonify({
        'office': {
            'name': settings.office_name if settings else '',
            'logo_url': ('/api/settings/logo'
                         if settings and settings.office_logo_path else None),
        },
        'company': {
            'razao_social': relatorio.company.razao_social,
            'cnpj': relatorio.company.cnpj,
        },
        'report': {
            'id': relatorio.id,
            'data_hora': relatorio.data_hora.isoformat() if relatorio.data_hora else None,
            'situacao': relatorio.situacao,
            'natureza_juridica_codigo': relatorio.natureza_juridica_codigo,
            'natureza_juridica_descricao': relatorio.natureza_juridica_descricao,
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
            'endereco': relatorio.endereco,
            'responsavel_cpf': relatorio.responsavel_cpf,
            'responsavel_nome': relatorio.responsavel_nome,
        },
        'pendencias': [
            {
                'tipo': p.tipo,
                'ano': p.ano,
                'meses': p.meses_json or [],
            }
            for p in relatorio.pendencias
        ],
        'debitos': [
            {
                'tipo': d.tipo,
                'receita': d.receita,
                'periodo_apuracao': d.periodo_apuracao,
                'data_vencimento': (d.data_vencimento.isoformat()
                                    if d.data_vencimento else None),
                'valor_original': float(d.valor_original or 0),
                'saldo_devedor': float(d.saldo_devedor or 0),
                'multa': float(d.multa or 0),
                'juros': float(d.juros or 0),
                'saldo_devedor_total': float(d.saldo_devedor_total or 0),
                'situacao': d.situacao,
            }
            for d in relatorio.debitos
        ],
    })
