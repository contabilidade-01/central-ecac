"""Auditoria estrutural: compara as funcoes/metodos de cada modulo .py com o .pyc do exe.

Usa AST no arquivo atual e o bytecode no exe. Aponta:
  FALTA  -> funcao existe no exe e nao no .py atual
  SOBRA  -> funcao existe no .py atual e nao no exe

Nao valida a LOGICA interna (isso exige ler o disassembly), mas pega modulo nao
convertido, funcao esquecida e funcao inventada.

Uso:
    .venv/Scripts/python.exe scripts/auditar_estrutura.py
"""
import ast
import pathlib
import os
import sys

AQUI = pathlib.Path(__file__).resolve().parent
PROJ = AQUI.parent
# As ferramentas de engenharia reversa ficam FORA do repositorio, em
# ../_ARQUIVO/engenharia_reversa/exe_reverse/ (ver docs/ARQUITETURA.md).
# Sobrescreva o caminho com a variavel de ambiente EXE_REVERSE_DIR se necessario.
EXE_REVERSE = pathlib.Path(os.getenv('EXE_REVERSE_DIR') or
                           AQUI.parent.parent / '_ARQUIVO' / 'engenharia_reversa' / 'exe_reverse')
if not EXE_REVERSE.exists():
    raise SystemExit(
        f'Ferramentas de engenharia reversa nao encontradas em {EXE_REVERSE}. '
        'Defina EXE_REVERSE_DIR apontando para a pasta exe_reverse.')
sys.path.insert(0, str(EXE_REVERSE))

from marshal312 import Code, load_pyc  # noqa: E402

EXT = pathlib.Path(
    r'C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\01_Jean\00_Claude'
    r'\00_PROJETOS\Central Pendencias\centralpendencias24072026'
    r'\IntegraContadorDesktop.exe_extracted\PYZ-00.pyz_extracted\app'
)

MODULOS = [
    'models.py', 'migrations.py', 'config.py', 'extensions.py',
    'routes/api_costs.py', 'routes/caixa_postal.py', 'routes/companies.py',
    'routes/das_routes.py', 'routes/dashboard.py', 'routes/license.py',
    'routes/pagamentos.py', 'routes/parcelamentos.py', 'routes/reports.py',
    'routes/settings.py',
    'services/api_usage_service.py', 'services/caixa_postal_service.py',
    'services/dominio_export_service.py', 'services/pagamentos_fiscais_service.py',
    'services/parcelamentos_serpro_service.py', 'services/pdf_parser.py',
    'services/report_service.py', 'services/serpro_das_service.py',
    'services/serpro_logging.py', 'services/serpro_pagamentos_service.py',
    'services/serpro_procurador_service.py', 'services/serpro_service.py',
    'services/startup_service.py',
]

IGNORAR = {'<lambda>', '<genexpr>', '<listcomp>', '<dictcomp>', '<setcomp>', '<module>'}


def nomes_do_exe(rel):
    """Nomes qualificados de funcoes/metodos definidos no .pyc."""
    code = load_pyc(str(EXT / rel.replace('.py', '.pyc')))
    achados = set()

    def anda(c):
        for k in c.consts:
            if isinstance(k, Code):
                q = k.qualname
                if not any(x in q for x in IGNORAR) and '<locals>' not in q:
                    achados.add(q)
                anda(k)

    anda(code)
    return achados


def nomes_do_py(caminho):
    arvore = ast.parse(caminho.read_text(encoding='utf-8'))
    achados = set()

    def anda(no, prefixo=''):
        for filho in no.body:
            if isinstance(filho, (ast.FunctionDef, ast.AsyncFunctionDef)):
                achados.add(prefixo + filho.name)
            elif isinstance(filho, ast.ClassDef):
                achados.add(prefixo + filho.name)
                anda(filho, prefixo + filho.name + '.')

    anda(arvore)
    return achados


def main():
    total_falta = total_sobra = 0
    linhas_problema = []

    for rel in MODULOS:
        caminho = PROJ / 'app' / rel
        if not caminho.exists():
            print(f'### {rel:<45} [ARQUIVO NAO EXISTE]')
            continue
        try:
            do_exe = nomes_do_exe(rel)
        except FileNotFoundError:
            print(f'### {rel:<45} [sem .pyc correspondente no exe]')
            continue

        do_py = nomes_do_py(caminho)

        falta = sorted(do_exe - do_py)
        sobra = sorted(do_py - do_exe)

        estado = 'OK' if not falta and not sobra else 'DIVERGENTE'
        print(f'### {rel:<45} exe={len(do_exe):>3} py={len(do_py):>3}  [{estado}]')
        for n in falta:
            print(f'    FALTA  {n}')
        for n in sobra:
            print(f'    SOBRA  {n}')
        if falta or sobra:
            linhas_problema.append((rel, len(falta), len(sobra)))
        total_falta += len(falta)
        total_sobra += len(sobra)

    print()
    print('=' * 72)
    print(f'  funcoes do exe ausentes  (FALTA) : {total_falta}')
    print(f'  funcoes nao previstas    (SOBRA) : {total_sobra}')
    if linhas_problema:
        print('\n  modulos divergentes:')
        for rel, f, s in linhas_problema:
            print(f'    {rel:<45} falta={f} sobra={s}')


if __name__ == '__main__':
    main()
