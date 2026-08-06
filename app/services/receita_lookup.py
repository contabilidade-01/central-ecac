"""Lookup de códigos de receita federal — referência para exibição.

Carrega a tabela `codigos_receita_darf.tsv` uma vez e expõe funções para
enriquecer a exibição de débitos no painel. NÃO altera a lógica de extração
do parser — serve apenas para traduzir código+extensão em nome legível.

Uso:
    from app.services.receita_lookup import descrever_receita

    info = descrever_receita('4406', '01')
    # {'codigo': '4406', 'extensao': '01', 'categoria': 'MAED_SIEF',
    #  'nome_curto': 'MAED - PGDAS-D (Multa Atraso Entrega)',
    #  'nome_oficial': 'MAED - Multa por Atraso na Entrega do PGDAS-D'}
"""

import csv
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TABELA: dict[str, dict] = {}  # chave: "CODIGO-EXTENSAO" ou "CODIGO"
_CARREGADA = False


def _caminho_tsv() -> Path:
    """Procura o TSV na pasta data/ do projeto ou via variável de ambiente."""
    # 1) Variável de ambiente explícita
    env = os.getenv('RECEITA_TSV_PATH')
    if env:
        return Path(env)

    # 2) Dentro de DATA_DIR/references/
    from app.config import DATA_DIR
    candidato = DATA_DIR / 'references' / 'codigos_receita_darf.tsv'
    if candidato.exists():
        return candidato

    # 3) Dentro do próprio repo (desenvolvimento)
    base = Path(__file__).resolve().parent.parent.parent
    candidato = base / 'references' / 'codigos_receita_darf.tsv'
    if candidato.exists():
        return candidato

    return candidato  # retorna mesmo sem existir — o _carregar() loga o erro


def _carregar():
    """Carrega o TSV em memória na primeira chamada."""
    global _TABELA, _CARREGADA
    if _CARREGADA:
        return
    _CARREGADA = True

    caminho = _caminho_tsv()
    if not caminho.exists():
        logger.warning('Tabela de códigos de receita não encontrada: %s', caminho)
        return

    try:
        with open(caminho, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                codigo = (row.get('CODIGO') or '').strip()
                extensao = (row.get('EXTENSAO') or '').strip()
                if not codigo:
                    continue

                entrada = {
                    'codigo': codigo,
                    'extensao': extensao,
                    'categoria': (row.get('CATEGORIA') or '').strip(),
                    'nome_curto': (row.get('NOME_CURTO') or '').strip(),
                    'nome_oficial': (row.get('NOME_OFICIAL') or '').strip(),
                }

                # Chave com extensão (ex: "4406-01")
                if extensao:
                    _TABELA[f'{codigo}-{extensao}'] = entrada

                # Chave sem extensão — só guarda se não colidir
                if codigo not in _TABELA:
                    _TABELA[codigo] = entrada

        logger.info('Tabela de receitas carregada: %d entradas de %s',
                    len(_TABELA), caminho)
    except Exception as e:
        logger.error('Erro ao carregar tabela de receitas: %s', e)


def descrever_receita(codigo: str, extensao: str = '') -> Optional[dict]:
    """Retorna info da receita ou None se não encontrada.

    Busca primeiro por codigo-extensao exato, depois só por codigo.
    """
    _carregar()

    codigo = codigo.strip()
    extensao = extensao.strip()

    if extensao:
        chave = f'{codigo}-{extensao}'
        if chave in _TABELA:
            return _TABELA[chave]

    return _TABELA.get(codigo)


def nome_curto(codigo: str, extensao: str = '') -> str:
    """Retorna o nome curto ou string vazia se não encontrado."""
    info = descrever_receita(codigo, extensao)
    return info['nome_curto'] if info else ''


def categoria(codigo: str, extensao: str = '') -> str:
    """Retorna a categoria ou string vazia se não encontrada."""
    info = descrever_receita(codigo, extensao)
    return info['categoria'] if info else ''
