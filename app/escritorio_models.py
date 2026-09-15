"""Tabelas da área Escritório (17o desvio).

⚠️ Ficam FORA de `app/models.py` de propósito: aquele arquivo espelha o schema do exe e
não deve ser alterado (regra 3 de docs/ARQUITETURA.md). Estas tabelas são novas,
não existem no exe, e são criadas por `db.create_all()` quando o blueprint
`escritorio` é registrado.
"""

from datetime import datetime

from app.extensions import db


class EscritorioNcmCst(db.Model):
    """De/para NCM → CST de PIS/COFINS usado pelo leitor de XML de NF-e.

    `ncm` guarda um PREFIXO (2 a 8 dígitos). Na consulta vence o prefixo mais longo,
    então uma exceção (ex.: um NCM de 8 dígitos com CST 1) sobrepõe a regra do
    capítulo/posição.

    CST: 1 = tributável • 4 = monofásico (revenda a alíquota zero)
         5 = substituição tributária • 6 = alíquota zero
    """
    __tablename__ = 'escritorio_ncm_cst'

    id = db.Column(db.Integer, primary_key=True)
    ncm = db.Column(db.String(8), nullable=False, unique=True, index=True)
    cst = db.Column(db.Integer, nullable=False)
    descricao = db.Column(db.String(255), nullable=True)
    base_legal = db.Column(db.String(255), nullable=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    atualizado_por = db.Column(db.String(120), nullable=True)


class EscritorioLancamento(db.Model):
    """Lançamento mensal do Simples Nacional por empresa e perfil.

    O leitor de NF-e grava o perfil `comercio` (origem `xml_nfe`). A tela de
    Lançamentos lê, edita e marca OK / transmitido — mesma conferência do Integra.
    """
    __tablename__ = 'escritorio_lancamentos'
    __table_args__ = (
        db.UniqueConstraint('cnpj', 'competencia', 'perfil', name='uq_escritorio_lanc'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=True, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    competencia = db.Column(db.String(7), nullable=False)          # AAAA-MM
    perfil = db.Column(db.String(20), nullable=False, default='comercio')

    total_receita = db.Column(db.Float, nullable=False, default=0)    # total a declarar no PGDAS-D
    devolucoes = db.Column(db.Float, nullable=False, default=0)
    outras_receitas = db.Column(db.Float, nullable=False, default=0)  # outras saídas
    rec_sem_st = db.Column(db.Float, nullable=False, default=0)       # linha 1 do simulador
    rec_sem_st_isencao = db.Column(db.Float, nullable=False, default=0)
    rec_com_st_mono = db.Column(db.Float, nullable=False, default=0)  # linha 2 (ST + monofásico)
    rec_monofasica = db.Column(db.Float, nullable=False, default=0)   # linha 3 (monofásico)
    rec_com_st = db.Column(db.Float, nullable=False, default=0)       # linha 4 (ST)
    saldo_sefaz = db.Column(db.Float, nullable=False, default=0)

    # Conferência (espelho do Integra): 0=não, 1=auto/SERPRO, 2=manual (amarelo)
    ok = db.Column(db.Boolean, nullable=False, default=False)
    transmitido = db.Column(db.Integer, nullable=False, default=0)

    origem = db.Column(db.String(20), nullable=False, default='xml_nfe')
    detalhe_json = db.Column(db.Text, nullable=True)   # totais por CFOP/CST da leitura
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    atualizado_por = db.Column(db.String(120), nullable=True)
