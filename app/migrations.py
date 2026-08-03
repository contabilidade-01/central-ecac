from sqlalchemy import text
from flask import current_app
from app.extensions import db


def table_exists(table_name: str) -> bool:
    result = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table"),
        {'table': table_name}
    ).fetchone()
    return result is not None


def column_exists(table_name: str, column_name: str) -> bool:
    result = db.session.execute(text(f'PRAGMA table_info({table_name})')).fetchall()
    columns = [row[1] for row in result]
    return column_name in columns


def add_column_if_not_exists(table, column, sql):
    if not column_exists(table, column):
        current_app.logger.info(f'Adicionando coluna {table}.{column}')
        db.session.execute(text(sql))
        db.session.commit()


def create_table_if_not_exists(table_name, sql):
    if not table_exists(table_name):
        current_app.logger.info(f'Criando tabela {table_name}')
        db.session.execute(text(sql))
        db.session.commit()


def run_migrations():
    db.create_all()

    add_column_if_not_exists(
        'companies',
        'processing_status',
        "ALTER TABLE companies ADD COLUMN processing_status VARCHAR(20) DEFAULT 'idle'"
    )
    add_column_if_not_exists(
        'companies',
        'processing_step',
        'ALTER TABLE companies ADD COLUMN processing_step VARCHAR(100)'
    )
    add_column_if_not_exists(
        'companies',
        'processing_progress',
        'ALTER TABLE companies ADD COLUMN processing_progress INTEGER DEFAULT 0'
    )
    add_column_if_not_exists(
        'companies',
        'processing_message',
        'ALTER TABLE companies ADD COLUMN processing_message TEXT'
    )
    add_column_if_not_exists(
        'companies',
        'processing_started_at',
        'ALTER TABLE companies ADD COLUMN processing_started_at DATETIME'
    )
    add_column_if_not_exists(
        'companies',
        'current_protocol',
        'ALTER TABLE companies ADD COLUMN current_protocol VARCHAR(100)'
    )
    add_column_if_not_exists(
        'companies',
        'protocol_requested_at',
        'ALTER TABLE companies ADD COLUMN protocol_requested_at DATETIME'
    )
    add_column_if_not_exists(
        'companies',
        'protocol_wait_until',
        'ALTER TABLE companies ADD COLUMN protocol_wait_until DATETIME'
    )
    add_column_if_not_exists(
        'companies',
        'last_processed_at',
        'ALTER TABLE companies ADD COLUMN last_processed_at DATETIME'
    )

    add_column_if_not_exists(
        'companies',
        'consultar_parc_sn',
        'ALTER TABLE companies ADD COLUMN consultar_parc_sn BOOLEAN DEFAULT 0'
    )
    add_column_if_not_exists(
        'companies',
        'consultar_parc_mei',
        'ALTER TABLE companies ADD COLUMN consultar_parc_mei BOOLEAN DEFAULT 0'
    )
    add_column_if_not_exists(
        'companies',
        'consultar_pert_sn',
        'ALTER TABLE companies ADD COLUMN consultar_pert_sn BOOLEAN DEFAULT 0'
    )
    add_column_if_not_exists(
        'companies',
        'consultar_pert_mei',
        'ALTER TABLE companies ADD COLUMN consultar_pert_mei BOOLEAN DEFAULT 0'
    )
    add_column_if_not_exists(
        'companies',
        'consultar_relp_sn',
        'ALTER TABLE companies ADD COLUMN consultar_relp_sn BOOLEAN DEFAULT 0'
    )
    add_column_if_not_exists(
        'companies',
        'consultar_relp_mei',
        'ALTER TABLE companies ADD COLUMN consultar_relp_mei BOOLEAN DEFAULT 0'
    )

    add_column_if_not_exists(
        'settings',
        'license_month',
        'ALTER TABLE settings ADD COLUMN license_month VARCHAR(7)'
    )
    add_column_if_not_exists(
        'settings',
        'license_path',
        'ALTER TABLE settings ADD COLUMN license_path VARCHAR(500)'
    )
    add_column_if_not_exists(
        'settings',
        'license_last_message',
        'ALTER TABLE settings ADD COLUMN license_last_message TEXT'
    )
    add_column_if_not_exists(
        'settings',
        'license_last_checked_at',
        'ALTER TABLE settings ADD COLUMN license_last_checked_at DATETIME'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_pf_habilitado',
        'ALTER TABLE settings ADD COLUMN procurador_pf_habilitado BOOLEAN DEFAULT 0'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_cpf',
        'ALTER TABLE settings ADD COLUMN procurador_cpf VARCHAR(11)'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_nome',
        'ALTER TABLE settings ADD COLUMN procurador_nome VARCHAR(255)'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_certificado_path',
        'ALTER TABLE settings ADD COLUMN procurador_certificado_path VARCHAR(500)'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_certificado_password',
        'ALTER TABLE settings ADD COLUMN procurador_certificado_password VARCHAR(255)'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_token',
        'ALTER TABLE settings ADD COLUMN procurador_token TEXT'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_token_expires_at',
        'ALTER TABLE settings ADD COLUMN procurador_token_expires_at DATETIME'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_token_raw_expires',
        'ALTER TABLE settings ADD COLUMN procurador_token_raw_expires VARCHAR(255)'
    )
    add_column_if_not_exists(
        'settings',
        'procurador_token_response_json',
        'ALTER TABLE settings ADD COLUMN procurador_token_response_json JSON'
    )

    create_table_if_not_exists(
        'caixa_postal_monitoramentos',
        """
        CREATE TABLE caixa_postal_monitoramentos (
            id INTEGER NOT NULL PRIMARY KEY,
            company_id INTEGER NOT NULL UNIQUE,
            indicador_mensagens_novas INTEGER NOT NULL DEFAULT 0,
            possui_mensagens_novas BOOLEAN NOT NULL DEFAULT 0,
            mensagens_baixadas BOOLEAN NOT NULL DEFAULT 0,
            ultima_consulta_monitoramento DATETIME,
            ultima_baixa_mensagens DATETIME,
            raw_json JSON,
            erro TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies (id)
        )
        """
    )

    create_table_if_not_exists(
        'caixa_postal_mensagens',
        """
        CREATE TABLE caixa_postal_mensagens (
            id INTEGER NOT NULL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            isn VARCHAR(20) NOT NULL,
            numero_controle VARCHAR(30),
            assunto VARCHAR(500),
            corpo TEXT,
            data_envio DATE,
            hora_envio VARCHAR(8),
            data_leitura DATE,
            data_ciencia DATE,
            data_validade DATE,
            data_expiracao DATE,
            codigo_sistema_remetente VARCHAR(20),
            codigo_modelo VARCHAR(20),
            origem_modelo VARCHAR(20),
            tipo_origem VARCHAR(20),
            descricao_origem VARCHAR(255),
            indicador_leitura VARCHAR(5),
            indicador_favorito VARCHAR(5),
            relevancia VARCHAR(5),
            valor_parametro_assunto VARCHAR(100),
            variaveis_json JSON,
            lista_raw_json JSON,
            detalhe_raw_json JSON,
            detalhe_baixado BOOLEAN NOT NULL DEFAULT 0,
            visualizada_usuario BOOLEAN NOT NULL DEFAULT 0,
            visualizada_at DATETIME,
            downloaded_at DATETIME,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies (id),
            CONSTRAINT uq_caixa_postal_company_isn UNIQUE (company_id, isn)
        )
        """
    )

    add_column_if_not_exists(
        'caixa_postal_mensagens',
        'visualizada_usuario',
        'ALTER TABLE caixa_postal_mensagens ADD COLUMN visualizada_usuario '
        'BOOLEAN NOT NULL DEFAULT 0'
    )
    add_column_if_not_exists(
        'caixa_postal_mensagens',
        'visualizada_at',
        'ALTER TABLE caixa_postal_mensagens ADD COLUMN visualizada_at DATETIME'
    )

    current_app.logger.info('Migrações executadas com sucesso.')
