"""Tabelas do FGTS Digital com executor plugável (agente no PC ou robô na VPS).

DESVIO INTENCIONAL (27o) — NÃO existe no exe. Pedido do Jean (09/11/2026). Ficam fora de
`models.py` pela regra 3 da arquitetura. Ver `docs/FGTS_DIGITAL.md`.

Nenhuma tabela guarda CPF ou nome de trabalhador (decisão de 09/11/2026).
"""

from datetime import datetime

from app.extensions import db


class FgtsAgente(db.Model):
    """Agente instalado em um PC do escritório. O token só é mostrado uma vez; aqui fica o hash."""
    __tablename__ = 'fgts_agentes'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    criado_por = db.Column(db.String(120), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    revogado_em = db.Column(db.DateTime, nullable=True)
    versao = db.Column(db.String(30), nullable=True)
    maquina = db.Column(db.String(120), nullable=True)
    login_ativo = db.Column(db.Boolean, nullable=False, default=False)
    ultimo_sinal_em = db.Column(db.DateTime, nullable=True)
    pedido_atual_id = db.Column(db.Integer, nullable=True)


class FgtsParametro(db.Model):
    """Valor gravado pelo administrador. Sem linha = vale o padrão (ou a variável de ambiente)."""
    __tablename__ = 'fgts_parametros'

    chave = db.Column(db.String(60), primary_key=True)
    valor_json = db.Column(db.Text, nullable=False)
    atualizado_por = db.Column(db.String(120), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class FgtsParametroHistorico(db.Model):
    __tablename__ = 'fgts_parametros_historico'

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(60), nullable=False, index=True)
    anterior_json = db.Column(db.Text, nullable=True)
    novo_json = db.Column(db.Text, nullable=False)
    por = db.Column(db.String(120), nullable=True)
    em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class FgtsGuia(db.Model):
    """GFD entregue pelo executor. O número da guia é único: reenvio não duplica."""
    __tablename__ = 'fgts_guias'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    competencia = db.Column(db.String(6), nullable=False, index=True)       # AAAAMM
    grupo = db.Column(db.String(40), nullable=True)                         # Mensal, Rescisório...
    numero = db.Column(db.String(40), nullable=False, unique=True)
    valor_centavos = db.Column(db.Integer, nullable=True)
    vencimento = db.Column(db.String(10), nullable=True)                    # AAAA-MM-DD
    data_pagamento = db.Column(db.String(10), nullable=True)                # AAAA-MM-DD pedida
    pdf_path = db.Column(db.String(500), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    pix_copia_e_cola = db.Column(db.Text, nullable=True)
    pedido_id = db.Column(db.Integer, nullable=True, index=True)
    origem = db.Column(db.String(20), nullable=False, default='agente')     # agente | vps
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class FgtsEvento(db.Model):
    """Trilha de auditoria do FGTS, sem segredos nem dados de trabalhador."""
    __tablename__ = 'fgts_eventos'

    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(40), nullable=False, index=True)
    pedido_id = db.Column(db.Integer, nullable=True, index=True)
    agente_id = db.Column(db.Integer, nullable=True)
    cnpj = db.Column(db.String(14), nullable=True, index=True)
    mensagem = db.Column(db.String(500), nullable=True)
    em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class FgtsEmpresa(db.Model):
    """Empresa tem FGTS a recolher? Vem do portal (funcionários ativos) ou de exceção do admin."""
    __tablename__ = 'fgts_empresas'

    company_id = db.Column(db.Integer, primary_key=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    funcionarios = db.Column(db.Integer, nullable=True)          # None = o portal não conhece a empresa
    sincronizado_em = db.Column(db.DateTime, nullable=True)
    excecao = db.Column(db.Boolean, nullable=True)               # None = segue o portal; True/False = admin
    excecao_por = db.Column(db.String(120), nullable=True)
    excecao_em = db.Column(db.DateTime, nullable=True)
