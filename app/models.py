from datetime import datetime
from decimal import Decimal
from app.extensions import db


class AppSetting(db.Model):
    __tablename__ = 'settings'

    id = db.Column(db.Integer, primary_key=True)
    contador_cnpj = db.Column(db.String(14), nullable=True)
    license_month = db.Column(db.String(7), nullable=True)
    license_path = db.Column(db.String(500), nullable=True)
    license_last_message = db.Column(db.Text, nullable=True)
    license_last_checked_at = db.Column(db.DateTime, nullable=True)
    certificado_path = db.Column(db.String(500), nullable=True)
    certificado_password = db.Column(db.String(255), nullable=True)
    serpro_consumer_key = db.Column(db.String(255), nullable=True)
    serpro_consumer_secret = db.Column(db.String(255), nullable=True)
    procurador_pf_habilitado = db.Column(db.Boolean, default=False, nullable=False)
    procurador_cpf = db.Column(db.String(11), nullable=True)
    procurador_nome = db.Column(db.String(255), nullable=True)
    procurador_certificado_path = db.Column(db.String(500), nullable=True)
    procurador_certificado_password = db.Column(db.String(255), nullable=True)
    procurador_token = db.Column(db.Text, nullable=True)
    procurador_token_expires_at = db.Column(db.DateTime, nullable=True)
    procurador_token_raw_expires = db.Column(db.String(255), nullable=True)
    procurador_token_response_json = db.Column(db.JSON, nullable=True)
    office_name = db.Column(db.String(255), nullable=True)
    office_logo_path = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(db.Integer, primary_key=True)
    razao_social = db.Column(db.String(255), nullable=False)
    cnpj = db.Column(db.String(14), nullable=False, unique=True, index=True)
    ativo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    processing_status = db.Column(db.String(20), default='idle', nullable=False)
    processing_step = db.Column(db.String(100), nullable=True)
    processing_progress = db.Column(db.Integer, default=0, nullable=False)
    processing_message = db.Column(db.Text, nullable=True)
    processing_started_at = db.Column(db.DateTime, nullable=True)
    current_protocol = db.Column(db.String(100), nullable=True)
    protocol_requested_at = db.Column(db.DateTime, nullable=True)
    protocol_wait_until = db.Column(db.DateTime, nullable=True)
    last_processed_at = db.Column(db.DateTime, nullable=True)
    consultar_parc_sn = db.Column(db.Boolean, default=False, nullable=False)
    consultar_parc_mei = db.Column(db.Boolean, default=False, nullable=False)
    consultar_pert_sn = db.Column(db.Boolean, default=False, nullable=False)
    consultar_pert_mei = db.Column(db.Boolean, default=False, nullable=False)
    consultar_relp_sn = db.Column(db.Boolean, default=False, nullable=False)
    consultar_relp_mei = db.Column(db.Boolean, default=False, nullable=False)

    relatorios = db.relationship('RelatorioSitFiscal', back_populates='company',
                                 cascade='all, delete-orphan')


class RelatorioSitFiscal(db.Model):
    __tablename__ = 'relatorios_sitfiscal'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    data_hora = db.Column(db.DateTime, nullable=False)
    situacao = db.Column(db.String(100), nullable=True)
    natureza_juridica_codigo = db.Column(db.String(10), nullable=True)
    natureza_juridica_descricao = db.Column(db.String(255), nullable=True)
    simples_nacional_inclusao = db.Column(db.Date, nullable=True)
    simples_nacional_exclusao = db.Column(db.Date, nullable=True)
    simei_inclusao = db.Column(db.Date, nullable=True)
    simei_exclusao = db.Column(db.Date, nullable=True)
    endereco = db.Column(db.Text, nullable=True)
    responsavel_cpf = db.Column(db.String(14), nullable=True)
    responsavel_nome = db.Column(db.String(255), nullable=True)
    pdf_local_path = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = db.relationship('Company', back_populates='relatorios')
    pendencias = db.relationship('PendenciaRelatorio', back_populates='relatorio',
                                 cascade='all, delete-orphan')
    debitos = db.relationship('DebitoRelatorio', back_populates='relatorio',
                              cascade='all, delete-orphan')


class PendenciaRelatorio(db.Model):
    __tablename__ = 'pendencias_relatorio'

    id = db.Column(db.Integer, primary_key=True)
    relatorio_id = db.Column(db.Integer, db.ForeignKey('relatorios_sitfiscal.id'),
                             nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    ano = db.Column(db.String(4), nullable=False)
    meses_json = db.Column(db.JSON, default=list)

    relatorio = db.relationship('RelatorioSitFiscal', back_populates='pendencias')


class DebitoRelatorio(db.Model):
    __tablename__ = 'debitos_relatorio'

    id = db.Column(db.Integer, primary_key=True)
    relatorio_id = db.Column(db.Integer, db.ForeignKey('relatorios_sitfiscal.id'),
                             nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    receita = db.Column(db.String(255), nullable=True)
    periodo_apuracao = db.Column(db.String(7), nullable=True)
    data_vencimento = db.Column(db.Date, nullable=True)
    valor_original = db.Column(db.Numeric(15, 2), default=0)
    saldo_devedor = db.Column(db.Numeric(15, 2), default=0)
    multa = db.Column(db.Numeric(15, 2), default=0)
    juros = db.Column(db.Numeric(15, 2), default=0)
    saldo_devedor_total = db.Column(db.Numeric(15, 2), default=0)
    situacao = db.Column(db.String(20), nullable=True)
    data_das = db.Column(db.Date, nullable=True)

    relatorio = db.relationship('RelatorioSitFiscal', back_populates='debitos')


class PagamentoFiscal(db.Model):
    __tablename__ = 'pagamentos_fiscais'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False,
                           index=True)
    numero_documento = db.Column(db.String(40), nullable=False, index=True)
    tipo_codigo = db.Column(db.String(10), nullable=True)
    tipo_descricao = db.Column(db.String(255), nullable=True)
    tipo_descricao_abreviada = db.Column(db.String(255), nullable=True)
    periodo_apuracao = db.Column(db.Date, nullable=True)
    data_arrecadacao = db.Column(db.Date, nullable=True, index=True)
    data_vencimento = db.Column(db.Date, nullable=True)
    receita_principal_codigo = db.Column(db.String(4), nullable=False, index=True)
    receita_principal_descricao = db.Column(db.String(255), nullable=True)
    referencia = db.Column(db.String(50), nullable=True)
    valor_total = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_principal = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_multa = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_juros = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    exportado = db.Column(db.Boolean, nullable=False, default=False,
                          server_default=db.text('0'), index=True)
    exported_at = db.Column(db.DateTime, nullable=True)
    source_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    company = db.relationship('Company', backref=db.backref(
        'pagamentos_fiscais', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('company_id', 'numero_documento',
                            name='uq_pagamento_company_documento'),
    )


class ReceitaContaDePara(db.Model):
    __tablename__ = 'receitas_contas_depara'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False,
                           index=True)
    receita_codigo = db.Column(db.String(4), nullable=False, index=True)
    conta_debito_valor_principal = db.Column(db.String(30), nullable=False)
    conta_credito_valor_principal = db.Column(db.String(30), nullable=False)
    conta_debito_multa = db.Column(db.String(30), nullable=True)
    conta_debito_juros = db.Column(db.String(30), nullable=True)
    historico_principal = db.Column(db.String(255), nullable=True)
    historico_juros = db.Column(db.String(255), nullable=True)
    descricao = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    company = db.relationship('Company', backref=db.backref(
        'receitas_contas_depara', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('company_id', 'receita_codigo',
                            name='uq_depara_company_receita'),
    )


class LancamentoContabilConfig(db.Model):
    __tablename__ = 'lancamentos_contabeis_config'

    id = db.Column(db.Integer, primary_key=True)
    conta_credito_padrao = db.Column(db.String(30), nullable=True)
    conta_multa = db.Column(db.String(30), nullable=True)
    conta_juros = db.Column(db.String(30), nullable=True)
    historico_codigo = db.Column(db.String(20), nullable=True)
    historico_descricao = db.Column(db.String(255), nullable=True)
    usuario_lancamento = db.Column(db.String(60), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)


class PagamentoFiscalDetalhe(db.Model):
    __tablename__ = 'pagamentos_fiscais_detalhes'

    id = db.Column(db.Integer, primary_key=True)
    pagamento_fiscal_id = db.Column(db.Integer, db.ForeignKey('pagamentos_fiscais.id'),
                                    nullable=False, index=True)
    sequencial = db.Column(db.String(20), nullable=True)
    receita_codigo = db.Column(db.String(10), nullable=False, index=True)
    receita_descricao = db.Column(db.String(255), nullable=True)
    extensao_receita_codigo = db.Column(db.String(20), nullable=True)
    extensao_receita_descricao = db.Column(db.String(255), nullable=True)
    periodo_apuracao = db.Column(db.Date, nullable=True)
    data_vencimento = db.Column(db.Date, nullable=True)
    valor_total = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_principal = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_multa = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_juros = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_saldo_total = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_saldo_principal = db.Column(db.Numeric(15, 2), nullable=False,
                                      default=Decimal('0.00'))
    valor_saldo_multa = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    valor_saldo_juros = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    cib = db.Column(db.String(100), nullable=True)
    source_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    pagamento = db.relationship('PagamentoFiscal', backref=db.backref(
        'detalhes', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('pagamento_fiscal_id', 'sequencial',
                            name='uq_pagamento_detalhe_pagamento_sequencial'),
    )


class ParcelamentoPedido(db.Model):
    __tablename__ = 'parcelamentos_pedidos'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False,
                           index=True)
    tipo = db.Column(db.String(30), nullable=False, index=True)
    numero = db.Column(db.String(80), nullable=False)
    data_pedido = db.Column(db.Date, nullable=True)
    situacao = db.Column(db.String(120), nullable=True, index=True)
    data_situacao = db.Column(db.Date, nullable=True)
    ativo = db.Column(db.Boolean, default=False, nullable=False, index=True)
    source_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    company = db.relationship('Company', backref=db.backref(
        'parcelamentos_pedidos', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('company_id', 'tipo', 'numero',
                            name='uq_parcelamento_pedido_company_tipo_numero'),
    )


class ParcelamentoParcela(db.Model):
    __tablename__ = 'parcelamentos_parcelas'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False,
                           index=True)
    tipo = db.Column(db.String(30), nullable=False, index=True)
    parcela = db.Column(db.String(80), nullable=False)
    descricao = db.Column(db.String(255), nullable=True)
    data_vencimento = db.Column(db.Date, nullable=True)
    valor_total = db.Column(db.Numeric(15, 2), default=Decimal('0.00'), nullable=False)
    pdf_local_path = db.Column(db.String(500), nullable=True)
    source_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    company = db.relationship('Company', backref=db.backref(
        'parcelamentos_parcelas', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('company_id', 'tipo', 'parcela',
                            name='uq_parcelamento_company_tipo_parcela'),
    )


class ApiUsageLog(db.Model):
    __tablename__ = 'api_usage_logs'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=True,
                           index=True)
    route_type = db.Column(db.String(20), nullable=False, index=True)
    endpoint = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Integer, default=1, nullable=False)
    estimated_cost = db.Column(db.Numeric(15, 2), nullable=False, default=Decimal('0.00'))
    success = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    company = db.relationship('Company', backref=db.backref('api_usage_logs', lazy=True))


class CaixaPostalMonitoramento(db.Model):
    __tablename__ = 'caixa_postal_monitoramentos'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False,
                           unique=True, index=True)
    indicador_mensagens_novas = db.Column(db.Integer, default=0, nullable=False)
    possui_mensagens_novas = db.Column(db.Boolean, default=False, nullable=False,
                                       index=True)
    mensagens_baixadas = db.Column(db.Boolean, default=False, nullable=False, index=True)
    ultima_consulta_monitoramento = db.Column(db.DateTime, nullable=True)
    ultima_baixa_mensagens = db.Column(db.DateTime, nullable=True)
    raw_json = db.Column(db.JSON, nullable=True)
    erro = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    company = db.relationship('Company', backref=db.backref(
        'caixa_postal_monitoramento', uselist=False, cascade='all, delete-orphan'))


class CaixaPostalMensagem(db.Model):
    __tablename__ = 'caixa_postal_mensagens'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False,
                           index=True)
    isn = db.Column(db.String(20), nullable=False, index=True)
    numero_controle = db.Column(db.String(30), nullable=True, index=True)
    assunto = db.Column(db.String(500), nullable=True)
    corpo = db.Column(db.Text, nullable=True)
    data_envio = db.Column(db.Date, nullable=True, index=True)
    hora_envio = db.Column(db.String(8), nullable=True)
    data_leitura = db.Column(db.Date, nullable=True)
    data_ciencia = db.Column(db.Date, nullable=True)
    data_validade = db.Column(db.Date, nullable=True)
    data_expiracao = db.Column(db.Date, nullable=True)
    codigo_sistema_remetente = db.Column(db.String(20), nullable=True)
    codigo_modelo = db.Column(db.String(20), nullable=True)
    origem_modelo = db.Column(db.String(20), nullable=True)
    tipo_origem = db.Column(db.String(20), nullable=True)
    descricao_origem = db.Column(db.String(255), nullable=True)
    indicador_leitura = db.Column(db.String(5), nullable=True)
    indicador_favorito = db.Column(db.String(5), nullable=True)
    relevancia = db.Column(db.String(5), nullable=True)
    valor_parametro_assunto = db.Column(db.String(100), nullable=True)
    variaveis_json = db.Column(db.JSON, nullable=True)
    lista_raw_json = db.Column(db.JSON, nullable=True)
    detalhe_raw_json = db.Column(db.JSON, nullable=True)
    detalhe_baixado = db.Column(db.Boolean, default=False, nullable=False, index=True)
    visualizada_usuario = db.Column(db.Boolean, default=False, nullable=False, index=True)
    visualizada_at = db.Column(db.DateTime, nullable=True)
    downloaded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)

    company = db.relationship('Company', backref=db.backref(
        'caixa_postal_mensagens', lazy=True, cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('company_id', 'isn', name='uq_caixa_postal_company_isn'),
    )
