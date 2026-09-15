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


class EscritorioPgdasHistorico(db.Model):
    """Receita bruta + folha/CPP mensais vindos do PDF PGDAS-D (Upload RBT12).

    Espelha o que o Integra grava em `importar_lancamentos.php`: até 13 competências
    (12 anteriores ao PA + o PA). Usado para RBT12 e Fator R — sem isso, Calcular DAS
    / transmitir declaração não fecha (igual ao Integra).
    """
    __tablename__ = 'escritorio_pgdas_historico'
    __table_args__ = (
        db.UniqueConstraint('cnpj', 'competencia', name='uq_escritorio_pgdas_mes'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=True, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    competencia = db.Column(db.String(7), nullable=False, index=True)  # AAAA-MM
    receita_bruta = db.Column(db.Float, nullable=False, default=0)
    folha_cpp = db.Column(db.Float, nullable=False, default=0)  # folha do mês ant. → CPP
    origem = db.Column(db.String(30), nullable=False, default='import_pgdas')
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    atualizado_por = db.Column(db.String(120), nullable=True)


class EscritorioPgdasMeta(db.Model):
    """Totais oficiais do extrato por PA (RBT12, Folha12, RPA, DAS do PDF)."""
    __tablename__ = 'escritorio_pgdas_meta'
    __table_args__ = (
        db.UniqueConstraint('cnpj', 'pa', name='uq_escritorio_pgdas_meta'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=True, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    pa = db.Column(db.String(7), nullable=False, index=True)  # AAAA-MM do período de apuração
    rbt12 = db.Column(db.Float, nullable=False, default=0)
    folha12 = db.Column(db.Float, nullable=False, default=0)
    rpa = db.Column(db.Float, nullable=False, default=0)
    valor_das = db.Column(db.Float, nullable=False, default=0)
    nome_pdf = db.Column(db.String(255), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    atualizado_por = db.Column(db.String(120), nullable=True)


class EscritorioDeclaracao(db.Model):
    """Estado da declaração PGDAS-D + DAS por empresa e período de apuração.

    É a "memória" do fluxo pago (Calcular → Transmitir → Gerar DAS). Serve para:

    * nunca pagar duas vezes pela mesma coisa (DAS já emitido é servido do disco;
      declaração já transmitida não é transmitida de novo sem "Retificar");
    * garantir que o que foi CALCULADO é exatamente o que será TRANSMITIDO
      (`hash_dados` do cálculo tem de bater com o payload atual);
    * travar clique duplo / duas abas (`operacao` + `operacao_desde`);
    * lembrar quando um envio ficou INCERTO (timeout depois de enviar) — nesse caso a
      única ação liberada é Consultar.
    """
    __tablename__ = 'escritorio_declaracoes'
    __table_args__ = (
        db.UniqueConstraint('cnpj', 'pa', name='uq_escritorio_declaracao'),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=True, index=True)
    cnpj = db.Column(db.String(14), nullable=False, index=True)
    pa = db.Column(db.String(6), nullable=False, index=True)            # AAAAMM

    # rascunho | calculada | transmitida | incerta | erro
    situacao = db.Column(db.String(20), nullable=False, default='rascunho')
    tipo_declaracao = db.Column(db.Integer, nullable=True)              # 1 original, 2 retificadora

    # cálculo (TRANSDECLARACAO11 com indicadorTransmissao=false)
    hash_dados = db.Column(db.String(64), nullable=True)
    valores_devidos_json = db.Column(db.Text, nullable=True)
    total_devido = db.Column(db.Float, nullable=True)
    calculado_em = db.Column(db.DateTime, nullable=True)

    # transmissão
    id_declaracao = db.Column(db.String(30), nullable=True)
    data_hora_transmissao = db.Column(db.String(14), nullable=True)
    transmitido_em = db.Column(db.DateTime, nullable=True)
    hash_transmitido = db.Column(db.String(64), nullable=True)
    arquivo_declaracao = db.Column(db.String(500), nullable=True)
    arquivo_recibo = db.Column(db.String(500), nullable=True)
    arquivo_maed_notificacao = db.Column(db.String(500), nullable=True)
    arquivo_maed_darf = db.Column(db.String(500), nullable=True)

    # consulta na Receita (CONSDECLARACAO13)
    consulta_json = db.Column(db.Text, nullable=True)
    consultado_em = db.Column(db.DateTime, nullable=True)

    # DAS (GERARDAS12)
    das_numero = db.Column(db.String(30), nullable=True)
    das_vencimento = db.Column(db.String(8), nullable=True)             # AAAAMMDD
    das_valor_total = db.Column(db.Float, nullable=True)
    das_data_consolidacao = db.Column(db.String(8), nullable=True)
    das_arquivo = db.Column(db.String(500), nullable=True)
    das_emitido_em = db.Column(db.DateTime, nullable=True)
    das_detalhe_json = db.Column(db.Text, nullable=True)

    # trava de operação em andamento
    operacao = db.Column(db.String(20), nullable=True)
    operacao_desde = db.Column(db.DateTime, nullable=True)
    operacao_por = db.Column(db.String(120), nullable=True)

    ultimo_erro = db.Column(db.Text, nullable=True)
    ultimo_erro_em = db.Column(db.DateTime, nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EscritorioSerproChamada(db.Model):
    """Auditoria de TODA chamada à SERPRO feita pela área Escritório.

    Uma linha por requisição que saiu (ou tentou sair) do servidor. É a prova do que
    foi pedido, do que voltou, quanto provavelmente custou e quem clicou — e é o que
    permite explicar qualquer cobrança do mês.
    O payload NÃO é guardado inteiro (pode ter dados do cliente); guardamos o hash.
    """
    __tablename__ = 'escritorio_serpro_chamadas'

    id = db.Column(db.Integer, primary_key=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    usuario = db.Column(db.String(120), nullable=True)
    company_id = db.Column(db.Integer, nullable=True, index=True)
    cnpj = db.Column(db.String(14), nullable=True, index=True)
    pa = db.Column(db.String(6), nullable=True)
    operacao = db.Column(db.String(30), nullable=False)                # calcular | transmitir | ...
    id_sistema = db.Column(db.String(20), nullable=False)
    id_servico = db.Column(db.String(40), nullable=False)
    endpoint = db.Column(db.String(20), nullable=False)                # Declarar | Emitir | Consultar
    http_status = db.Column(db.Integer, nullable=True)
    sucesso = db.Column(db.Boolean, nullable=False, default=False)
    incerto = db.Column(db.Boolean, nullable=False, default=False)     # timeout após enviar
    codigos = db.Column(db.String(500), nullable=True)
    mensagem = db.Column(db.Text, nullable=True)
    duracao_ms = db.Column(db.Integer, nullable=True)
    custo_estimado = db.Column(db.Float, nullable=False, default=0)
    cobravel = db.Column(db.Boolean, nullable=False, default=True)
    hash_dados = db.Column(db.String(64), nullable=True)
    tentativa = db.Column(db.Integer, nullable=False, default=1)
