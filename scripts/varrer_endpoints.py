"""Varre todos os endpoints do sistema e informa quais estão funcionando.

Testa apenas métodos SEGUROS (GET) — nunca dispara POST, para não consumir API da
SERPRO nem alterar dados. Para cada rota com parâmetro, usa um id existente no banco.

Uso (com o servidor no ar em http://localhost:5847):
    .venv/Scripts/python.exe scripts/varrer_endpoints.py
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = 'http://127.0.0.1:5847'


def chamar(url):
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=30) as resp:
            corpo = resp.read().decode('utf-8', 'replace')
            return resp.status, corpo
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except Exception as e:
        return 0, f'{type(e).__name__}: {e}'


def resumir(corpo, limite=90):
    corpo = ' '.join(corpo.split())
    return corpo[:limite]


def main():
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.models import Company, RelatorioSitFiscal

        company = Company.query.order_by(Company.id).first()
        relatorio = RelatorioSitFiscal.query.order_by(RelatorioSitFiscal.id).first()
        subs = {
            'company_id': company.id if company else 1,
            'report_id': relatorio.id if relatorio else 1,
            'id': company.id if company else 1,
            'pedido_id': 1,
            'mensagem_id': 1,
            'parcela_id': 1,
        }

        rotas = []
        for rule in app.url_map.iter_rules():
            if 'GET' not in (rule.methods or set()):
                continue
            caminho = str(rule)
            if not caminho.startswith('/api/'):
                continue
            faltando = [a for a in rule.arguments if a not in subs]
            if faltando:
                rotas.append((caminho, None, f'sem valor para {faltando}'))
                continue
            url = caminho
            for arg, val in subs.items():
                url = url.replace(f'<int:{arg}>', str(val)).replace(f'<{arg}>', str(val))
            rotas.append((caminho, url, None))

    rotas.sort()
    ok, falhas, pulados = [], [], []

    print(f'Varrendo {len(rotas)} endpoints GET em {BASE}\n')
    for caminho, url, motivo in rotas:
        if url is None:
            pulados.append((caminho, motivo))
            continue
        status, corpo = chamar(BASE + url)
        marca = 'OK  ' if 200 <= status < 400 else ('404 ' if status == 404 else 'FALHA')
        linha = f'  [{marca}] {status:>3}  {url}'
        if status >= 500 or status == 0:
            falhas.append((url, status, resumir(corpo)))
            linha += f'\n           -> {resumir(corpo)}'
        else:
            ok.append((url, status))
        print(linha)

    print()
    print('=' * 70)
    print(f'  OK / esperado : {len(ok)}')
    print(f'  FALHANDO      : {len(falhas)}')
    print(f'  não testados  : {len(pulados)}')
    if falhas:
        print('\n--- endpoints quebrados ---')
        for url, status, corpo in falhas:
            print(f'  {status:>3}  {url}')
            print(f'       {corpo}')
    if pulados:
        print('\n--- não testados (precisam de parâmetro) ---')
        for caminho, motivo in pulados:
            print(f'  {caminho}  ({motivo})')


if __name__ == '__main__':
    main()
