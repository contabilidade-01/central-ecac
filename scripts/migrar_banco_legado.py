"""Migra os dados do banco legado (schema inventado) para o schema do exe.

O banco antigo ficava em `instance/integra_contador.db` com:
  - companies(cnpj, nome, processing, ...)
  - app_settings(key, value)   -- chave/valor

O schema do exe (agora restaurado) usa:
  - companies(razao_social, cnpj, processing_status, ...)
  - settings(...)              -- uma linha, uma coluna por campo

Uso:
    python scripts/migrar_banco_legado.py            # aplica
    python scripts/migrar_banco_legado.py --dry-run  # só mostra o que faria
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJETO = Path(__file__).resolve().parent.parent

# O banco legado foi movido para _BACKUP/ porque o banco novo ocupa o mesmo caminho
# (instance/integra_contador.db). Use --origem para apontar outro arquivo.
LEGACY_DB = PROJETO / '_BACKUP' / 'integra_contador_legado.db'

# chave em app_settings -> coluna em settings
SETTING_KEYS = {
    'contador_cnpj': 'contador_cnpj',
    'certificado_path': 'certificado_path',
    'certificado_password': 'certificado_password',
    'serpro_consumer_key': 'serpro_consumer_key',
    'serpro_consumer_secret': 'serpro_consumer_secret',
    'office_name': 'office_name',
    'procurador_cpf': 'procurador_cpf',
    'procurador_nome': 'procurador_nome',
    'procurador_certificado_path': 'procurador_certificado_path',
    'procurador_certificado_password': 'procurador_certificado_password',
    'procurador_pf_habilitado': 'procurador_pf_habilitado',
}

BOOL_KEYS = {'procurador_pf_habilitado'}


def read_legacy(path: Path):
    if not path.exists():
        raise SystemExit(f'Banco legado não encontrado: {path}')

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    companies = [
        {'cnpj': r['cnpj'], 'razao_social': r['nome']}
        for r in conn.execute('SELECT cnpj, nome FROM companies ORDER BY nome')
        if r['cnpj']
    ]

    settings = {r['key']: r['value']
                for r in conn.execute('SELECT key, value FROM app_settings')}

    conn.close()
    return companies, settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--origem', help='caminho do banco legado (schema antigo)')
    args = parser.parse_args()

    origem = Path(args.origem) if args.origem else LEGACY_DB
    companies, legacy_settings = read_legacy(origem)
    print(f'Banco legado: {origem}')
    print(f'  empresas encontradas : {len(companies)}')
    print(f'  chaves de settings   : {len(legacy_settings)}')

    from app import create_app
    from app.extensions import db
    from app.models import AppSetting, Company

    app = create_app()
    with app.app_context():
        print(f'Banco destino: {app.config["SQLALCHEMY_DATABASE_URI"]}')

        criadas = 0
        existentes = 0
        for item in companies:
            if Company.query.filter_by(cnpj=item['cnpj']).first():
                existentes += 1
                continue
            if not args.dry_run:
                db.session.add(Company(
                    razao_social=item['razao_social'] or item['cnpj'],
                    cnpj=item['cnpj'],
                    ativo=True,
                ))
            criadas += 1

        setting = AppSetting.query.first()
        if setting is None:
            setting = AppSetting()
            if not args.dry_run:
                db.session.add(setting)

        aplicados = []
        for legacy_key, column in SETTING_KEYS.items():
            if legacy_key not in legacy_settings:
                continue
            value = legacy_settings[legacy_key]
            if legacy_key in BOOL_KEYS:
                value = str(value).strip().lower() in ('1', 'true', 'on', 'yes')
            if not args.dry_run:
                setattr(setting, column, value)
            aplicados.append(column)

        if not args.dry_run:
            db.session.commit()

        print()
        print(f'  empresas criadas       : {criadas}')
        print(f'  empresas já existentes : {existentes}')
        print(f'  campos de settings     : {", ".join(aplicados)}')

        cert = legacy_settings.get('certificado_path') or ''
        if cert and not Path(cert).exists():
            print()
            print('ATENÇÃO: o caminho do certificado migrado não existe nesta máquina:')
            print(f'  {cert}')
            print('Reenvie o certificado A1 pela tela de Configurações do sistema.')

        if args.dry_run:
            print()
            print('(dry-run: nada foi gravado)')


if __name__ == '__main__':
    main()
