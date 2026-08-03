import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    """Pasta de dados da aplicação.

    DESVIO INTENCIONAL (2o, completado em 02/08/2026): no exe isto era sempre
    `%LOCALAPPDATA%/IntegraContadorDesktop`. O `config.py` já apontava para a pasta do
    projeto / volume, mas ESTA função continuava no LOCALAPPDATA — e é ela que define
    onde ficam os arquivos de recuperação de detalhe da caixa postal
    (`CaixaPostalService._recovery_path`). No servidor isso os deixava FORA do volume
    persistente: perdidos a cada redeploy.

    Agora segue o mesmo DATA_DIR do resto do sistema. O caminho antigo permanece como
    último recurso.
    """
    do_ambiente = os.getenv('DATA_DIR')
    if do_ambiente:
        path = Path(do_ambiente)
    else:
        # mesma raiz que app/config.py usa: a pasta do projeto
        path = Path(__file__).resolve().parents[2]
        if not path.exists():  # pragma: no cover - fallback do exe original
            path = Path(os.getenv('LOCALAPPDATA') or Path.home()) / 'IntegraContadorDesktop'

    path.mkdir(parents=True, exist_ok=True)
    return path


def program_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def resource_path(relative_path: str) -> Path:
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).resolve().parent.parent / relative_path
