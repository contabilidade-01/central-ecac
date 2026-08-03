"""Backup do volume de dados (banco + PDFs + mapa de procurações).

Gera um .zip com data/hora em `<DATA_DIR>/backups/`. O banco é copiado com a API
`sqlite3.backup()`, que produz um snapshot **consistente mesmo com o sistema em uso** —
copiar o arquivo .db na mão durante uma escrita pode gerar cópia corrompida.

Uso:
    python scripts/backup_dados.py                 # backup completo
    python scripts/backup_dados.py --somente-banco # só o .db (rápido)
    python scripts/backup_dados.py --manter 14     # apaga backups mais antigos

No servidor, agende no cron do EasyPanel/VPS (ver docs/DEPLOY_EASYPANEL.md, passo 9):
    0 3 * * *  docker exec central-pendencias-ecac python scripts/backup_dados.py --manter 14
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DATA_DIR, DB_PATH, REPORTS_DIR  # noqa: E402


def snapshot_banco(destino: Path) -> Path:
    """Cópia consistente do SQLite, mesmo com o sistema escrevendo."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    origem = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    try:
        copia = sqlite3.connect(destino)
        try:
            origem.backup(copia)
        finally:
            copia.close()
    finally:
        origem.close()
    return destino


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--somente-banco', action='store_true')
    ap.add_argument('--manter', type=int, default=0,
                    help='quantos backups manter (0 = não apaga nada)')
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f'ERRO: banco não encontrado em {DB_PATH}')
        return 1

    pasta = Path(DATA_DIR) / 'backups'
    pasta.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime('%Y%m%d_%H%M%S')

    banco_tmp = pasta / f'_snapshot_{carimbo}.db'
    snapshot_banco(banco_tmp)

    destino = pasta / f'backup_{carimbo}.zip'
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(banco_tmp, 'instance/integra_contador.db')

        mapa = Path(DB_PATH).parent / 'procuracoes.json'
        if mapa.exists():
            z.write(mapa, 'instance/procuracoes.json')

        if not args.somente_banco and Path(REPORTS_DIR).exists():
            for pdf in Path(REPORTS_DIR).rglob('*.pdf'):
                z.write(pdf, str(Path('reports') / pdf.relative_to(REPORTS_DIR)))

    banco_tmp.unlink(missing_ok=True)
    print(f'backup gerado: {destino}  ({destino.stat().st_size / 1024:.0f} KB)')

    if args.manter > 0:
        antigos = sorted(pasta.glob('backup_*.zip'), reverse=True)[args.manter:]
        for velho in antigos:
            velho.unlink()
            print(f'  removido antigo: {velho.name}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
