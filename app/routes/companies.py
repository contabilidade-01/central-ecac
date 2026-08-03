from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Company

import csv
import io

companies_bp = Blueprint('companies', __name__)


@companies_bp.get('')
def list_companies():
    items = Company.query.order_by(Company.razao_social.asc()).all()
    return jsonify([
        {
            'id': c.id,
            'razao_social': c.razao_social,
            'cnpj': c.cnpj,
            'ativo': c.ativo,
            'consultar_parc_sn': c.consultar_parc_sn,
            'consultar_parc_mei': c.consultar_parc_mei,
            'consultar_pert_sn': c.consultar_pert_sn,
            'consultar_pert_mei': c.consultar_pert_mei,
            'consultar_relp_sn': c.consultar_relp_sn,
            'consultar_relp_mei': c.consultar_relp_mei,
        }
        for c in items
    ])


@companies_bp.post('')
def create_company():
    data = request.get_json()
    company = Company(
        razao_social=data['razao_social'].strip(),
        cnpj=only_digits(data['cnpj']),
        ativo=bool(data.get('ativo', True)),
        consultar_parc_sn=bool(data.get('consultar_parc_sn', False)),
        consultar_parc_mei=bool(data.get('consultar_parc_mei', False)),
        consultar_pert_sn=bool(data.get('consultar_pert_sn', False)),
        consultar_pert_mei=bool(data.get('consultar_pert_mei', False)),
        consultar_relp_sn=bool(data.get('consultar_relp_sn', False)),
        consultar_relp_mei=bool(data.get('consultar_relp_mei', False)),
    )
    db.session.add(company)
    db.session.commit()
    return jsonify({'success': True, 'id': company.id})


@companies_bp.put('/<int:company_id>')
def update_company(company_id: int):
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({'success': False, 'message': 'Empresa não encontrada'}), 404

    data = request.get_json()
    company.razao_social = data.get('razao_social', company.razao_social).strip()
    company.cnpj = only_digits(data.get('cnpj', company.cnpj))
    company.ativo = bool(data.get('ativo', company.ativo))

    for field in ('consultar_parc_sn', 'consultar_parc_mei', 'consultar_pert_sn',
                  'consultar_pert_mei', 'consultar_relp_sn', 'consultar_relp_mei'):
        if field in data:
            setattr(company, field, bool(data.get(field)))

    db.session.commit()
    return jsonify({'success': True})


@companies_bp.delete('/<int:company_id>')
def delete_company(company_id: int):
    company = db.session.get(Company, company_id)
    if not company:
        return jsonify({'success': False, 'message': 'Empresa não encontrada'}), 404

    db.session.delete(company)
    db.session.commit()
    return jsonify({'success': True})


def only_digits(value: str) -> str:
    return ''.join(ch for ch in str(value) if ch.isdigit())


def read_csv_file(file):
    raw = file.read()

    if not raw:
        raise ValueError('Arquivo vazio')

    encodings = ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1']

    content = None
    for enc in encodings:
        try:
            content = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue

    if content is None:
        content = raw.decode('latin-1', errors='ignore')

    lines = content.splitlines()
    if not lines:
        raise ValueError('CSV vazio')

    first_line = lines[0]

    delimiter = ';' if first_line.count(';') > first_line.count(',') else ','

    return csv.reader(io.StringIO(content), delimiter=delimiter)


@companies_bp.post('/importar-csv')
def import_companies_csv():
    file = request.files.get('file')
    if not file:
        return jsonify({'success': False, 'message': 'Arquivo CSV não enviado'}), 400

    filename = (file.filename or '').lower()
    if not filename.endswith('.csv'):
        return jsonify({'success': False, 'message': 'Envie um arquivo CSV'}), 400

    try:
        reader = read_csv_file(file)
    except Exception as exc:
        return jsonify({
            'success': False,
            'message': f'Erro ao ler CSV: {str(exc)}',
        }), 400

    criadas = 0
    ignoradas = 0
    erros = []

    for linha_num, row in enumerate(reader, start=1):
        if not row or not any(str(col or '').strip() for col in row):
            continue

        if len(row) < 3:
            erros.append(
                f'Linha {linha_num}: esperado pelo menos 3 colunas (cnpj,codigo,nome)')
            continue

        cnpj = only_digits(row[0])
        codigo = str(row[1] or '').strip()
        nome = str(row[2] or '').strip()

        if not cnpj:
            erros.append(f'Linha {linha_num}: CNPJ vazio')
            continue

        if len(cnpj) != 14:
            erros.append(f'Linha {linha_num}: CNPJ inválido')
            continue

        if not codigo:
            erros.append(f'Linha {linha_num}: código vazio')
            continue

        if not nome:
            erros.append(f'Linha {linha_num}: nome vazio')
            continue

        razao_social = f'{codigo}-{nome}'

        existente = Company.query.filter_by(cnpj=cnpj).first()
        if existente:
            ignoradas += 1
            continue

        company = Company(
            razao_social=razao_social,
            cnpj=cnpj,
            ativo=True,
        )
        db.session.add(company)
        criadas += 1

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Erro ao salvar empresas: {str(exc)}',
        }), 400

    return jsonify({
        'success': True,
        'message': 'Importação concluída',
        'criadas': criadas,
        'ignoradas': ignoradas,
        'erros': erros,
    })
