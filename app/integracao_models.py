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


class ContatoEmpresa(db.Model):
    """Contato de WhatsApp/e-mail de uma empresa — 19o desvio (envio pelo próprio sistema).

    O exe não tinha contato nenhum (era um operador só, na própria máquina). `companies`
    não pode ganhar coluna (regra 3), então o contato mora aqui, uma linha por empresa.

    `whatsapp` é o número de envio; `whatsapp_2` um segundo (sócio). O envio usa o
    PRIMEIRO válido, na ordem — mesma regra do `celularSql` do portal.
    `verificado_status`: 'ok' (formato válido e, se a uazapi respondeu, existe no
    WhatsApp), 'invalido' (formato), 'inexistente' (a uazapi diz que não é WhatsApp),
    'pendente' (nunca verificado).
    """
    __tablename__ = 'contatos_empresa'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    whatsapp = db.Column(db.String(20), nullable=True)
    whatsapp_2 = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    responsavel = db.Column(db.String(120), nullable=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)     # False = não recebe nada
    origem = db.Column(db.String(20), nullable=False, default='manual')  # manual | importado
    observacao = db.Column(db.String(500), nullable=True)
    verificado_status = db.Column(db.String(20), nullable=False, default='pendente')
    verificado_motivo = db.Column(db.String(300), nullable=True)
    verificado_em = db.Column(db.DateTime, nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    atualizado_por = db.Column(db.String(120), nullable=True)


class WhatsappEnvio(db.Model):
    """Toda mensagem que saiu (ou tentou sair) pela uazapi a partir deste sistema.

    Porte da ideia de `alert_sends`/`alert_failures` do portal: prova do que foi mandado,
    para quem, quando e por quê — e o que falhou, com o motivo legível.
    """
    __tablename__ = 'whatsapp_envios'

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=True, index=True)
    numero = db.Column(db.String(20), nullable=True, index=True)
    tipo = db.Column(db.String(12), nullable=False, default='texto')   # texto | documento
    contexto = db.Column(db.String(40), nullable=False, default='manual')  # teste | das_mei | parcela | ...
    texto = db.Column(db.Text, nullable=True)
    nome_arquivo = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(12), nullable=False, default='enviado')  # enviado | falhou | bloqueado
    erro = db.Column(db.String(500), nullable=True)
    mensagem_id = db.Column(db.String(120), nullable=True)
    tentativas = db.Column(db.Integer, nullable=False, default=1)
    enviado_por = db.Column(db.String(120), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
