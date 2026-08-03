"""Backup do banco — sob demanda, automático e com retenção.

⚠️ DESVIO INTENCIONAL (15o) — NÃO existe no exe, que era desktop: o banco ficava na
máquina do usuário, dentro do backup dele. Num servidor, sem isto, um erro operacional
não tem volta.

Como o backup é feito
---------------------
`sqlite3.backup()` — a API de cópia online do próprio SQLite. É diferente de copiar o
arquivo: ela respeita as transações em curso, então o backup **nunca sai pela metade**,
mesmo com o sistema em uso. Copiar `.db` na mão pode capturar um estado inconsistente.

Retenção
--------
Guarda os `MANTER` mais recentes (padrão 5) e apaga os antigos. Conta tudo que está em
`<DATA_DIR>/backups`, inclusive as cópias que a tela de restauração cria antes de trocar
o banco — assim o número de arquivos não cresce sem limite.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

MANTER = 5
PREFIXO = 'backup-'
PADRAO = re.compile(r'^(backup-|integra_contador-antes-).+\.db$')

_LOCK = threading.Lock()


def _pasta() -> Path:
    from app.config import DATA_DIR
    destino = Path(DATA_DIR) / 'backups'
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _banco() -> Path:
    from app.config import DATA_DIR
    return Path(DATA_DIR) / 'instance' / 'integra_contador.db'


def criar(motivo: str = 'manual') -> Dict[str, Any]:
    """Gera um backup consistente e devolve os dados do arquivo criado."""
    origem = _banco()
    if not origem.exists():
        raise FileNotFoundError('O banco ainda não existe.')

    carimbo = datetime.now().strftime('%Y%m%d-%H%M%S')
    destino = _pasta() / f'{PREFIXO}{carimbo}-{motivo}.db'

    with _LOCK:
        con_origem = sqlite3.connect(f'file:{origem}?mode=ro', uri=True)
        con_destino = sqlite3.connect(destino)
        try:
            with con_destino:
                con_origem.backup(con_destino)
        finally:
            con_origem.close()
            con_destino.close()
        expurgar()

    return {
        'arquivo': destino.name,
        'caminho': str(destino),
        'tamanho_kb': round(destino.stat().st_size / 1024, 1),
        'criado_em': datetime.now().isoformat(),
        'motivo': motivo,
    }


def listar() -> List[Dict[str, Any]]:
    """Backups existentes, do mais novo para o mais antigo."""
    itens = []
    for arquivo in _pasta().iterdir():
        if not arquivo.is_file() or not PADRAO.match(arquivo.name):
            continue
        info = arquivo.stat()
        itens.append({
            'arquivo': arquivo.name,
            'tamanho_kb': round(info.st_size / 1024, 1),
            'criado_em': datetime.fromtimestamp(info.st_mtime).isoformat(),
            'automatico': '-automatico' in arquivo.name,
            'pre_restauracao': arquivo.name.startswith('integra_contador-antes-'),
        })
    return sorted(itens, key=lambda i: i['criado_em'], reverse=True)


def expurgar(manter: int = MANTER) -> List[str]:
    """Apaga os backups mais antigos, preservando os `manter` mais recentes."""
    apagados = []
    for item in listar()[manter:]:
        try:
            (_pasta() / item['arquivo']).unlink()
            apagados.append(item['arquivo'])
        except OSError:
            pass
    return apagados


def caminho_seguro(nome: str) -> Path:
    """Resolve o nome pedido dentro da pasta de backups.

    Confere o nome contra o padrão e o caminho final contra a pasta — sem isso, um
    `../../` no nome baixaria qualquer arquivo do servidor.
    """
    if not PADRAO.match(nome or ''):
        raise ValueError('Nome de backup inválido.')
    alvo = (_pasta() / nome).resolve()
    if alvo.parent != _pasta().resolve() or not alvo.is_file():
        raise ValueError('Backup não encontrado.')
    return alvo


def backup_diario_se_preciso() -> Dict[str, Any] | None:
    """Cria no máximo um backup automático por dia. Chamado pelo agendador."""
    hoje = datetime.now().strftime('%Y%m%d')
    for item in listar():
        if item['automatico'] and item['arquivo'].startswith(f'{PREFIXO}{hoje}'):
            return None
    return criar(motivo='automatico')
