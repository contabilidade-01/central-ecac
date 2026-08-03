"""Auditoria: compara as rotas registradas no app com as rotas do exe.

Para cada blueprint, extrai do bytecode do exe o conjunto (metodo, path) e compara com
o que o app expoe hoje. Aponta:
  FALTA   -> rota existe no exe e nao no app        (funcionalidade ausente)
  SOBRA   -> rota existe no app e nao no exe        (rota inventada)

Uso:
    .venv/Scripts/python.exe scripts/auditar_rotas.py
"""
import pathlib
import os
import sys

AQUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
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

import dis312 as D  # noqa: E402
from marshal312 import load_pyc  # noqa: E402

EXT = pathlib.Path(
    r'C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\01_Jean\00_Claude'
    r'\00_PROJETOS\Central Pendencias\centralpendencias24072026'
    r'\IntegraContadorDesktop.exe_extracted\PYZ-00.pyz_extracted\app'
)

# prefixo com que cada blueprint e registrado no app/__init__.py do exe
PREFIXOS = {
    'routes/settings.pyc': '/api/settings',
    'routes/companies.pyc': '/api/companies',
    'routes/dashboard.pyc': '/api/dashboard',
    'routes/reports.pyc': '/api/reports',
    'routes/parcelamentos.pyc': '/api/parcelamentos',
    'routes/api_costs.pyc': '/api/api-costs',
    'routes/das_routes.pyc': '/api/das',
    'routes/caixa_postal.pyc': '/api/caixa-postal',
    'routes/license.pyc': '/api/license',
    # pagamentos_bp e registrado SEM url_prefix: o proprio blueprint define o dele
    'routes/pagamentos.pyc': '/api/pagamentos',
}

METODOS = {'get', 'post', 'put', 'delete', 'patch', 'route'}
HTTP = {'GET', 'POST', 'PUT', 'DELETE', 'PATCH'}


def instrucoes(code):
    bc = code.code
    i = 0
    ext = 0
    while i < len(bc):
        op = bc[i]
        arg = bc[i + 1]
        if op == D.EXTENDED_ARG:
            ext = (ext << 8) | arg
            i += 2
            continue
        full = (ext << 8) | arg if ext else arg
        ext = 0
        nome = D.OPNAME.get(op, f'op{op}')
        if nome != 'CACHE':
            yield nome, full
        i += 2 + D.CACHES.get(op, 0) * 2


def rotas_do_exe(rel, prefixo):
    code = load_pyc(str(EXT / rel))
    metodo = None
    path = None
    lista_metodos = None
    achados = set()
    for nome, arg in instrucoes(code):
        if nome == 'LOAD_ATTR':
            attr = code.names[arg >> 1]
            if attr in METODOS:
                metodo = attr
        elif nome == 'LOAD_CONST':
            v = code.consts[arg]
            if isinstance(v, str) and (v.startswith('/') or v == ''):
                path = v
            elif isinstance(v, str) and v in HTTP:
                # methods=['POST'] -> cada metodo vem como LOAD_CONST isolado
                lista_metodos = (lista_metodos or []) + [v]
            elif isinstance(v, (list, tuple)) and v and all(
                    isinstance(x, str) and x in HTTP for x in v):
                lista_metodos = list(v)
        elif nome == 'STORE_NAME' and metodo and path is not None:
            func = code.names[arg]
            if not func.startswith('__'):
                if metodo == 'route':
                    ms = lista_metodos or ['GET']
                else:
                    ms = [metodo.upper()]
                for m in ms:
                    achados.add((m, prefixo + path))
            metodo = None
            path = None
            lista_metodos = None
    return achados


def main():
    from app import create_app

    app = create_app()

    atuais = set()
    for r in app.url_map.iter_rules():
        caminho = str(r)
        if not caminho.startswith('/api/'):
            continue
        for m in (r.methods or set()) - {'HEAD', 'OPTIONS'}:
            atuais.add((m, caminho))

    total_falta = 0
    total_sobra = 0

    for rel, prefixo in sorted(PREFIXOS.items()):
        do_exe = rotas_do_exe(rel, prefixo)
        do_app = {(m, p) for (m, p) in atuais if p.startswith(prefixo + '/')
                  or p == prefixo}

        falta = sorted(do_exe - do_app)
        sobra = sorted(do_app - do_exe)

        estado = 'OK' if not falta and not sobra else 'DIVERGENTE'
        print(f'### {rel.replace("routes/", "").replace(".pyc", ""):<16} '
              f'exe={len(do_exe):>2}  app={len(do_app):>2}  [{estado}]')
        for m, p in falta:
            print(f'    FALTA  {m:<7} {p}')
        for m, p in sobra:
            print(f'    SOBRA  {m:<7} {p}')
        total_falta += len(falta)
        total_sobra += len(sobra)

    print()
    print('=' * 70)
    print(f'  rotas do exe ausentes no app (FALTA) : {total_falta}')
    print(f'  rotas inventadas no app     (SOBRA)  : {total_sobra}')
    return 1 if (total_falta or total_sobra) else 0


if __name__ == '__main__':
    sys.exit(main())
