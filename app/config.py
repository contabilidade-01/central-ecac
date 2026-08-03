import os
from pathlib import Path
from app.utils.paths import app_data_dir  # noqa: F401  (mantido: usado pelo exe original)

BASE_DIR = Path(__file__).resolve().parent.parent

# DESVIO INTENCIONAL do exe (decisão do Jean, 30/07/2026):
# o exe usa DATA_DIR = %LOCALAPPDATA%/IntegraContadorDesktop, ou seja, banco e PDFs
# locais por máquina. Aqui o DATA_DIR aponta para a própria pasta do projeto, que
# fica no OneDrive, para que banco + relatórios sejam COMPARTILHADOS entre as duas
# máquinas do Jean.
#
# ATENÇÃO: SQLite em pasta sincronizada não tolera escrita simultânea.
# NÃO abrir o sistema nas duas máquinas ao mesmo tempo.
#
# NO SERVIDOR (Docker / EasyPanel): defina a variável de ambiente DATA_DIR apontando
# para o VOLUME PERSISTENTE (ex.: DATA_DIR=/data). É esse diretório que guarda banco,
# PDFs, certificados e logs — sem volume, um redeploy apaga tudo.
DATA_DIR = Path(os.getenv('DATA_DIR') or BASE_DIR)
DB_PATH = DATA_DIR / 'instance' / 'integra_contador.db'
REPORTS_DIR = DATA_DIR / 'reports'
CERTS_DIR = DATA_DIR / 'certificates'
LICENSES_DIR = DATA_DIR / 'licenses'

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
CERTS_DIR.mkdir(parents=True, exist_ok=True)
LICENSES_DIR.mkdir(parents=True, exist_ok=True)


class Config:
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{DB_PATH.as_posix()}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_AS_ASCII = False
    # Em produção SEMPRE definir SECRET_KEY no ambiente (assina a sessão do login).
    SECRET_KEY = os.getenv('SECRET_KEY') or 'integra-contador-desktop-local'
    DATA_DIR = str(DATA_DIR)
    REPORTS_DIR = str(REPORTS_DIR)
    CERTS_DIR = str(CERTS_DIR)
    LICENSES_DIR = str(LICENSES_DIR)
    LICENSE_PRODUCT = 'novointegra'
    LICENSE_PUBLIC_KEY = (
        '-----BEGIN PUBLIC KEY-----\n'
        'MCowBQYDK2VwAyEASawlE34EYprjxr2QU0t6OMILb7gOwtoEAx8I5f0l3Fg=\n'
        '-----END PUBLIC KEY-----'
    )
