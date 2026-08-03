"""Auditoria de SHAPE: o formato do corpo da resposta bate com o do exe?

Esta é a classe de bug que status HTTP, rota e nome de funcao NAO detectam — e que
derrubou a tela de Parcelamentos (o frontend faz `.map()` e recebia um objeto).

Para cada funcao de rota no bytecode do exe, reporta:
  - se o jsonify recebe LISTA ou DICT (pela instrucao construtora antes do CALL)
  - as tuplas de chaves usadas (BUILD_CONST_KEY_MAP), que dao as chaves exatas

Compare com o tipo que o app devolve de fato (use --live para consultar o servidor).

Uso:
    .venv/Scripts/python.exe scripts/auditar_shape.py routes/caixa_postal.pyc
    .venv/Scripts/python.exe scripts/auditar_shape.py routes/das_routes.pyc --live
"""
import argparse
import json
import pathlib
import os
import sys
import urllib.error
import urllib.request

AQUI = pathlib.Path(__file__).resolve().parent
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
from marshal312 import Code, load_pyc  # noqa: E402

EXT = pathlib.Path(
    r'C:\Users\parce\OneDrive\Desktop\OneDrive - Nescon\OneDrive\01_Jean\00_Claude'
    r'\00_PROJETOS\Central Pendencias\centralpendencias24072026'
    r'\IntegraContadorDesktop.exe_extracted\PYZ-00.pyz_extracted\app'
)

BASE = 'http://127.0.0.1:5847'


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


def analisar(func):
    """Deduz o shape de cada `return jsonify(...)` da funcao."""
    ins = list(instrucoes(func))
    nomes = [n for n, _ in ins]

    # Para cada RETURN_VALUE, acha o `LOAD_GLOBAL jsonify` que o alimenta e olha SO o
    # trecho entre eles — que é onde os argumentos do jsonify sao montados. Olhar antes
    # disso da falso positivo: o BUILD_CONST_KEY_MAP de um item dentro de um laco
    # `items.append({...})` seria confundido com o retorno.
    idx_jsonify = [i for i, (n, a) in enumerate(ins)
                   if n == 'LOAD_GLOBAL' and func.names[a >> 1] == 'jsonify']

    retornos = [i for i, n in enumerate(nomes) if n == 'RETURN_VALUE']
    shapes = []
    for r in retornos:
        anteriores = [j for j in idx_jsonify if j < r]
        if not anteriores:
            shapes.append('?')
            continue
        ini = anteriores[-1]
        trecho = nomes[ini + 1:r]
        # ATENCAO a ordem: um listcomp inline `jsonify([{...} for x in y])` tem
        # BUILD_CONST_KEY_MAP (o dict do item) E LIST_APPEND. Se houver LIST_APPEND,
        # o retorno é LISTA — checar isso primeiro evita falso positivo de 'dict'.
        if 'LIST_APPEND' in trecho or 'BUILD_LIST' in trecho:
            shapes.append('list')
        elif 'BUILD_CONST_KEY_MAP' in trecho or 'BUILD_MAP' in trecho:
            shapes.append('dict')
        else:
            # jsonify(variavel) — o tipo vem de como a variavel foi montada
            corpo = nomes[:ini]
            shapes.append('list' if ('LIST_APPEND' in corpo or 'BUILD_LIST' in corpo)
                          else 'var?')
        shapes[-1] = shapes[-1]

    tuplas = [k for k in func.consts
              if isinstance(k, tuple) and k and all(isinstance(x, str) for x in k)]
    return shapes, tuplas


def rotas_do_modulo(code):
    """Mapa funcao -> (metodo, path) lido do nivel de modulo."""
    METODOS = {'get', 'post', 'put', 'delete', 'patch', 'route'}
    HTTP = {'GET', 'POST', 'PUT', 'DELETE', 'PATCH'}
    metodo = path = None
    lista = None
    mapa = {}
    for nome, arg in instrucoes(code):
        if nome == 'LOAD_ATTR':
            a = code.names[arg >> 1]
            if a in METODOS:
                metodo = a
        elif nome == 'LOAD_CONST':
            v = code.consts[arg]
            if isinstance(v, str) and (v.startswith('/') or v == ''):
                path = v
            elif isinstance(v, str) and v in HTTP:
                lista = (lista or []) + [v]
        elif nome == 'STORE_NAME' and metodo and path is not None:
            f = code.names[arg]
            if not f.startswith('__'):
                ms = lista if (metodo == 'route' and lista) else [metodo.upper()]
                mapa[f] = (','.join(ms), path)
            metodo = path = lista = None
    return mapa


def achar(code, nome):
    for k in code.consts:
        if isinstance(k, Code):
            if k.name == nome:
                return k
            r = achar(k, nome)
            if r:
                return r


def tipo_no_app(prefixo, path, metodo):
    if 'GET' not in metodo:
        return '(POST/PUT — nao testado)'
    if '<' in path:
        return '(precisa parametro)'
    url = BASE + prefixo + path
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read().decode('utf-8', 'replace'))
        return type(d).__name__
    except urllib.error.HTTPError as e:
        return f'HTTP {e.code}'
    except Exception as e:
        return f'ERRO {type(e).__name__}'


PREFIXOS = {
    'routes/caixa_postal.pyc': '/api/caixa-postal',
    'routes/das_routes.pyc': '/api/das',
    'routes/parcelamentos.pyc': '/api/parcelamentos',
    'routes/pagamentos.pyc': '/api/pagamentos',
    'routes/reports.pyc': '/api/reports',
    'routes/companies.pyc': '/api/companies',
    'routes/settings.pyc': '/api/settings',
    'routes/dashboard.pyc': '/api/dashboard',
    'routes/api_costs.pyc': '/api/api-costs',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('modulos', nargs='+')
    ap.add_argument('--live', action='store_true',
                    help='consulta o servidor para ver o tipo devolvido de fato')
    args = ap.parse_args()

    for rel in args.modulos:
        prefixo = PREFIXOS.get(rel, '')
        code = load_pyc(str(EXT / rel))
        mapa = rotas_do_modulo(code)
        print(f'#### {rel}   (prefixo {prefixo})')
        for func, (metodo, path) in mapa.items():
            f = achar(code, func)
            if not f:
                print(f'  {func}: nao encontrada')
                continue
            shapes, tuplas = analisar(f)
            principal = shapes[-1] if shapes else '?'
            live = f'  |  app devolve: {tipo_no_app(prefixo, path, metodo)}' if args.live else ''
            print(f'  {metodo:<8} {path:<38} exe={principal:<5}{live}')
            for t in tuplas:
                if len(t) > 2:
                    print(f'           chaves: {t}')
        print()


if __name__ == '__main__':
    main()
