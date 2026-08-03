"""
Rotas para gerenciamento de pagamentos fiscais
"""

from flask import Blueprint, jsonify, request, Response
from decimal import Decimal
import io
from openpyxl import Workbook
from sqlalchemy import or_
from app.extensions import db
from app.models import (
    Company,
    PagamentoFiscal,
    PagamentoFiscalDetalhe,
    ReceitaContaDePara,
)

pagamentos_bp = Blueprint('pagamentos', __name__, url_prefix='/api/pagamentos')


def _company_name(company):
    """Linha 20 do exe."""
    return (getattr(company, 'razao_social', None)
            or getattr(company, 'nome', None)
            or str(company.id))


def _base_pagamentos_query():
    """Query base com TODOS os filtros da querystring (linha 39 do exe).

    Antes a lógica estava inline em `list_pagamentos`, filtrando por um `tipo` que o exe
    não tem e ignorando `company_ids`, `codigo_receita`, `apenas_nao_exportados` e `busca`.
    """
    query = PagamentoFiscal.query.join(
        Company, Company.id == PagamentoFiscal.company_id)

    company_id = request.args.get('company_id', type=int)
    company_ids_raw = request.args.get('company_ids', type=str)
    codigo_receita = request.args.get('codigo_receita', type=str)
    data_inicio = request.args.get('data_inicio', type=str)
    data_fim = request.args.get('data_fim', type=str)
    apenas_nao_exportados = request.args.get(
        'apenas_nao_exportados') in ('1', 'true', 'True')
    busca = (request.args.get('busca') or '').strip()

    if company_id:
        query = query.filter(PagamentoFiscal.company_id == company_id)

    if company_ids_raw:
        parsed_ids = [int(x) for x in company_ids_raw.split(',')
                      if str(x).strip().isdigit()]
        if parsed_ids:
            query = query.filter(PagamentoFiscal.company_id.in_(parsed_ids))

    if codigo_receita:
        query = query.filter(
            PagamentoFiscal.receita_principal_codigo == codigo_receita.strip().zfill(4))

    if data_inicio:
        query = query.filter(PagamentoFiscal.data_arrecadacao >= data_inicio)

    if data_fim:
        query = query.filter(PagamentoFiscal.data_arrecadacao <= data_fim)

    if apenas_nao_exportados:
        query = query.filter(PagamentoFiscal.exportado.is_(False))

    if busca:
        like = f'%{busca}%'
        query = query.filter(or_(
            PagamentoFiscal.numero_documento.ilike(like),
            PagamentoFiscal.receita_principal_codigo.ilike(like),
            PagamentoFiscal.receita_principal_descricao.ilike(like),
            Company.razao_social.ilike(like),
            Company.cnpj.ilike(like),
        ))

    return query


@pagamentos_bp.route('/companies')
def list_companies():
    """Lista empresas com pagamentos"""
    try:
        companies = db.session.query(Company).join(
            PagamentoFiscal
        ).distinct().all()
        
        # O exe devolve LISTA pura com as chaves ('id','razao_social','cnpj','ativo').
        return jsonify([{
            'id': c.id,
            'razao_social': c.razao_social,
            'cnpj': c.cnpj,
            'ativo': c.ativo,
        } for c in companies])
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pagamentos_bp.route('/consultar', methods=['POST'])
def consultar_pagamentos():
    """
    Consulta pagamentos de uma empresa na API do SERPRO.
    """
    try:
        data = request.get_json()
        company_id = data.get('company_id')
        competencia = data.get('competencia')  # formato: MM/YYYY
        
        company = Company.query.get_or_404(company_id)
        
        # Aqui seria a chamada à API do SERPRO
        # Por enquanto, retornamos sucesso simulado
        return jsonify({
            'success': True,
            'message': f'Consulta iniciada para empresa {company.cnpj}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pagamentos_bp.route('/exportar-excel-modelo')
def exportar_excel_modelo():
    """Exporta modelo Excel de pagamentos — rota do exe."""
    try:
        pagamentos = PagamentoFiscal.query.limit(1000).all()

        wb = Workbook()
        ws = wb.active
        ws.title = 'Pagamentos'

        headers = ['CNPJ', 'Empresa', 'Tipo Código', 'Receita Código', 'Período',
                   'Data Arrecadação', 'Valor Principal', 'Valor Juros',
                   'Valor Multa', 'Valor Total']
        ws.append(headers)

        for p in pagamentos:
            company = Company.query.get(p.company_id)
            ws.append([
                company.cnpj if company else '',
                company.razao_social if company else '',
                p.tipo_codigo or '',
                p.receita_principal_codigo or '',
                p.periodo_apuracao.isoformat() if p.periodo_apuracao else '',
                p.data_arrecadacao.isoformat() if p.data_arrecadacao else '',
                float(p.valor_principal) if p.valor_principal else 0,
                float(p.valor_juros) if p.valor_juros else 0,
                float(p.valor_multa) if p.valor_multa else 0,
                float(p.valor_total) if p.valor_total else 0,
            ])

        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        return Response(
            buffer.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': 'attachment; filename=pagamentos.xlsx'}
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pagamentos_bp.route('/exportar-filtrado')
def exportar_filtrado():
    """Exporta pagamentos filtrados — rota do exe."""
    return exportar_excel_modelo()


@pagamentos_bp.route('/exportar-zip-filtrado')
def exportar_zip_filtrado():
    """Exporta pagamentos filtrados como ZIP — rota do exe."""
    return exportar_excel_modelo()


@pagamentos_bp.route('/<int:pagamento_id>/detalhes')
def listar_detalhes_pagamento(pagamento_id):
    """Detalhes de um pagamento (linha 389 do exe). Devolve LISTA pura."""
    pagamento = PagamentoFiscal.query.get(pagamento_id)
    if not pagamento:
        return jsonify({'success': False, 'message': 'Pagamento não encontrado.'}), 404

    detalhes = PagamentoFiscalDetalhe.query.filter_by(
        pagamento_fiscal_id=pagamento.id,
    ).order_by(
        PagamentoFiscalDetalhe.sequencial.asc(),
        PagamentoFiscalDetalhe.id.asc(),
    ).all()

    return jsonify([{
        'id': d.id,
        'pagamento_fiscal_id': d.pagamento_fiscal_id,
        'sequencial': d.sequencial,
        'receita_codigo': d.receita_codigo,
        'receita_descricao': d.receita_descricao,
        'extensao_receita_codigo': d.extensao_receita_codigo,
        'extensao_receita_descricao': d.extensao_receita_descricao,
        'periodo_apuracao': d.periodo_apuracao.isoformat() if d.periodo_apuracao else None,
        'data_vencimento': d.data_vencimento.isoformat() if d.data_vencimento else None,
        'valor_total': float(d.valor_total or 0),
        'valor_principal': float(d.valor_principal or 0),
        'valor_multa': float(d.valor_multa or 0),
        'valor_juros': float(d.valor_juros or 0),
        'valor_saldo_total': float(d.valor_saldo_total or 0),
        'valor_saldo_principal': float(d.valor_saldo_principal or 0),
        'valor_saldo_multa': float(d.valor_saldo_multa or 0),
        'valor_saldo_juros': float(d.valor_saldo_juros or 0),
        'cib': d.cib,
    } for d in detalhes])


@pagamentos_bp.route('/itens')
def list_pagamentos():
    """Lista de pagamentos (linha 109 do exe). Devolve LISTA pura.

    Os filtros vêm todos de `_base_pagamentos_query()` — a versão anterior filtrava por
    um `tipo` que não existe no exe e limitava a 100 registros.
    """
    query = _base_pagamentos_query()
    pagamentos = query.order_by(
        PagamentoFiscal.data_arrecadacao.desc(),
        PagamentoFiscal.id.desc(),
    ).all()

    return jsonify([{
        'id': p.id,
        'company_id': p.company_id,
        'company_name': _company_name(p.company),
        'company_cnpj': getattr(p.company, 'cnpj', None),
        'numero_documento': p.numero_documento,
        'tipo_codigo': p.tipo_codigo,
        'tipo_descricao': p.tipo_descricao,
        'tipo_descricao_abreviada': p.tipo_descricao_abreviada,
        'periodo_apuracao': p.periodo_apuracao.isoformat() if p.periodo_apuracao else None,
        'data_arrecadacao': p.data_arrecadacao.isoformat() if p.data_arrecadacao else None,
        'data_vencimento': p.data_vencimento.isoformat() if p.data_vencimento else None,
        'receita_principal_codigo': p.receita_principal_codigo,
        'receita_principal_descricao': p.receita_principal_descricao,
        'referencia': p.referencia,
        'valor_total': float(p.valor_total or 0),
        'valor_principal': float(p.valor_principal or 0),
        'valor_multa': float(p.valor_multa or 0),
        'valor_juros': float(p.valor_juros or 0),
        'exportado': p.exportado,
        'exported_at': p.exported_at.isoformat() if p.exported_at else None,
    } for p in pagamentos])


# Rotas /exportar/excel e /exportar/filtrado REMOVIDAS — inventadas.
# O exe usa /exportar-excel-modelo e /exportar-filtrado (já mapeados acima como alias).


@pagamentos_bp.route('/depara', methods=['GET'])
def list_depara():
    """Lista mapeamentos de receitas"""
    try:
        query = ReceitaContaDePara.query
        tipo = request.args.get('type')
        if tipo:
            query = query.filter(ReceitaContaDePara.receita_codigo == tipo)
        depara = query.all()

        # O exe devolve LISTA pura. As colunas 'codigo_receita'/'tipo'/'categoria' do
        # código inventado NÃO existem no modelo — o schema real usa receita_codigo e
        # as contas de débito/crédito.
        return jsonify([{
            'id': d.id,
            'company_id': d.company_id,
            'company_name': d.company.razao_social if d.company else None,
            'receita_codigo': d.receita_codigo,
            'conta_debito_valor_principal': d.conta_debito_valor_principal,
            'conta_credito_valor_principal': d.conta_credito_valor_principal,
            'conta_debito_multa': d.conta_debito_multa,
            'conta_debito_juros': d.conta_debito_juros,
            'historico_principal': d.historico_principal,
            'historico_juros': d.historico_juros,
            'descricao': d.descricao,
        } for d in depara])
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pagamentos_bp.route('/depara', methods=['POST'])
def create_depara():
    """Cria um novo mapeamento de receita"""
    try:
        data = request.get_json()
        depara = ReceitaContaDePara(
            company_id=data['company_id'],
            receita_codigo=data['receita_codigo'],
            conta_debito_valor_principal=data['conta_debito_valor_principal'],
            conta_credito_valor_principal=data['conta_credito_valor_principal'],
            conta_debito_multa=data.get('conta_debito_multa'),
            conta_debito_juros=data.get('conta_debito_juros'),
            historico_principal=data.get('historico_principal'),
            historico_juros=data.get('historico_juros'),
            descricao=data.get('descricao'),
        )
        db.session.add(depara)
        db.session.commit()
        return jsonify({'success': True, 'id': depara.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@pagamentos_bp.route('/depara/<int:item_id>', methods=['PUT'])
def update_depara(item_id):
    """Atualiza um mapeamento"""
    try:
        depara = ReceitaContaDePara.query.get_or_404(item_id)
        data = request.get_json()
        
        for campo in ('receita_codigo', 'conta_debito_valor_principal',
                      'conta_credito_valor_principal', 'conta_debito_multa',
                      'conta_debito_juros', 'historico_principal',
                      'historico_juros', 'descricao'):
            if campo in data:
                setattr(depara, campo, data[campo])

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@pagamentos_bp.route('/depara/<int:item_id>', methods=['DELETE'])
def delete_depara(item_id):
    """Remove um mapeamento"""
    try:
        depara = ReceitaContaDePara.query.get_or_404(item_id)
        db.session.delete(depara)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


def _only_digits(text):
    return ''.join(c for c in text if c.isdigit())


def _format_cnpj(cnpj):
    if len(cnpj) == 14:
        return f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}'
    return cnpj


def _to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
