import os
import secrets
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


def _secret_key() -> str:
    """Chave que assina o cookie de sessão do login (10o desvio).

    Precedência: variável de ambiente → arquivo no volume → gera e grava.

    O fallback fixo que existia aqui (`integra-contador-desktop-local`) era aceitável
    enquanto o sistema era desktop em localhost. Com a tela de login publicada na
    internet, deixou de ser: a string está no repositório, então qualquer um poderia
    **forjar um cookie de sessão** e entrar sem senha. Por isso, sem a variável, agora
    geramos uma chave aleatória e a guardamos no volume — persistente entre redeploys,
    para que a sessão não caia a cada implantação.
    """
    do_ambiente = os.getenv('SECRET_KEY')
    if do_ambiente:
        return do_ambiente

    arquivo = DATA_DIR / 'instance' / 'secret_key.txt'
    try:
        if arquivo.exists():
            guardada = arquivo.read_text(encoding='utf-8').strip()
            if guardada:
                return guardada
        nova = secrets.token_urlsafe(48)
        arquivo.write_text(nova, encoding='utf-8')
        try:
            arquivo.chmod(0o600)
        except OSError:
            pass  # Windows/sistemas sem suporte — o arquivo já está no volume privado
        return nova
    except OSError:
        # disco somente leitura: não dá para persistir; chave por processo.
        # A sessão cai a cada reinício, mas nunca fica previsível.
        return secrets.token_urlsafe(48)


class Config:
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{DB_PATH.as_posix()}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_AS_ASCII = False
    SECRET_KEY = _secret_key()
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
