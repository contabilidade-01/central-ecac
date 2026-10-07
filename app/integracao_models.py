"""Tabelas da integração com o portal do cliente (nescon-clientes) — 18o desvio.

⚠️ Ficam FORA de `app/models.py` de propósito (regra 3 de docs/ARQUITETURA.md): aquele
arquivo espelha o schema do exe e não deve ser alterado. Estas tabelas são novas, não
existem no exe, e são criadas por `db.create_all()` no arranque (ver `app/__init__.py`).

O que NÃO fica aqui, de propósito:
* contato do cliente (e-mail, WhatsApp) — o dono do cadastro é o portal;
* histórico de notificação — idem. Este sistema fornece pendências e emite guias;
  quem fala com o cliente é o portal.
"""

from datetime import datetime

from app.extensions import db


class DasEmissao(db.Model):
    """Toda guia emitida por este sistema fora da área Escritório.

    Serve para três coisas:
    * **não pagar duas vezes** — guia ainda válida é servida do disco;
    * **auditoria** — quem pediu (portal, escritório, lote) e quanto custou;
    * **evento** para o portal — "o cliente recalculou a guia em tal dia" é o que decide
      se o relatório será regerado ou se sai só um lembrete.
    """
    __tablename__ = 'das_emissoes'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=True, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    tipo = db.Column(db.String(10), nullable=False)                # SN | MEI | DCTFWEB
    periodo_apuracao = db.Column(db.String(6), nullable=False, index=True)   # AAAAMM
    categoria = db.Column(db.String(30), nullable=True)            # DCTFWeb: GERAL_MENSAL | GERAL_13o_SALARIO
    data_consolidacao = db.Column(db.String(8), nullable=True)     # AAAAMMDD (pedida)
    numero_documento = db.Column(db.String(40), nullable=True)
    vencimento = db.Column(db.String(8), nullable=True)            # AAAAMMDD (da SERPRO)
    valor_total = db.Column(db.Float, nullable=True)
    pdf_local_path = db.Column(db.String(500), nullable=True)
    origem = db.Column(db.String(20), nullable=False, default='interno')  # portal | escritorio | lote | interno
    solicitado_por = db.Column(db.String(120), nullable=True)
    reuso_de_id = db.Column(db.Integer, nullable=True)             # quando serviu PDF já guardado
    custo_estimado = db.Column(db.Float, nullable=False, default=0)
    detalhe_json = db.Column(db.Text, nullable=True)
    emitido_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


class FilaReprocessamento(db.Model):
    """Pedido de regerar a situação fiscal de UMA empresa numa data futura.

    O agendamento mensal roda a carteira inteira; isto é o complemento por empresa:
    "o cliente recalculou o DAS hoje, confira daqui a 5 dias úteis se pagou". O
    agendador drena a fila a cada ciclo, com as mesmas travas do lote (procuração, teto).
    """
    __tablename__ = 'fila_reprocessamento'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    motivo = db.Column(db.String(60), nullable=False, default='recalculo_guia')
    origem = db.Column(db.String(20), nullable=False, default='interno')
    agendado_para = db.Column(db.Date, nullable=False, index=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    tentativas = db.Column(db.Integer, nullable=False, default=0)
    executado_em = db.Column(db.DateTime, nullable=True)
    sucesso = db.Column(db.Boolean, nullable=True)
    resultado = db.Column(db.Text, nullable=True)
    relatorio_id = db.Column(db.Integer, nullable=True)            # relatório gerado pela execução


class EmpresaMei(db.Model):
    """Marca, por empresa, se ela é MEI. Quem define é o administrador.

    Fica fora de `models.py` (regra 3). Empresa MEI **não entra** na busca de relatórios de
    situação fiscal (lote, agendamento ou botão): o MEI é acompanhado só pelas guias em
    aberto, para não pagar relatório à toa. Sem linha = não é MEI.
    """
    __tablename__ = 'empresas_mei'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    eh_mei = db.Column(db.Boolean, nullable=False, default=True)
    definido_por = db.Column(db.String(120), nullable=True)
    definido_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class MeiGuiaSelecionada(db.Model):
    """Guia de MEI que o empresário quer pagar (competência marcada na tela /mei).

    Só se paga chamada da SERPRO para guia marcada aqui: é o que mantém o custo baixo.
    Fica fora de `models.py` (regra 3).
    """
    __tablename__ = 'mei_guias_selecionadas'
    __table_args__ = (db.UniqueConstraint('company_id', 'competencia', name='uq_mei_guia'),)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    competencia = db.Column(db.String(6), nullable=False, index=True)    # AAAAMM
    data_pagamento = db.Column(db.String(8), nullable=True)               # AAAAMMDD (consolidação)
    status = db.Column(db.String(12), nullable=False, default='pendente')  # pendente | gerada | erro
    das_emissao_id = db.Column(db.Integer, nullable=True)
    erro = db.Column(db.String(300), nullable=True)
    marcado_por = db.Column(db.String(120), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                              nullable=False)


class MeiEnvio(db.Model):
    """Envio da guia MEI ao cliente pelo WhatsApp (feito pelo Nescon Clientes).

    O Nescon é dono do contato, da janela e do teto de envio; aqui fica o que ele respondeu,
    para o painel mostrar enviadas, na fila e falhas. `external_ref` é o que torna o envio
    idempotente do outro lado.
    """
    __tablename__ = 'mei_envios'
    __table_args__ = (db.UniqueConstraint('company_id', 'competencia', name='uq_mei_envio'),)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    competencia = db.Column(db.String(6), nullable=False, index=True)
    external_ref = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False)    # enviada|na_fila|falhou|sem_whatsapp|ignorada|sem_cadastro|erro_rede
    motivo = db.Column(db.String(300), nullable=True)
    enviado_por = db.Column(db.String(120), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                              nullable=False)
