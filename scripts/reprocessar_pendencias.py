"""Relê os PDFs JÁ SALVOS e regrava as pendências, sem chamar a SERPRO.

Serve para aplicar retroativamente a correção da leitura (16o desvio, 03/08/2026): os
relatórios processados antes dela têm pendências fantasma (`ano 0001`, `1099`, `1082`,
anos repetidos) e não têm as omissões de `DASN SIMEI`.

**Custo zero**: os PDFs estão em `reports/`; nada é pedido à SERPRO.

Idempotente: apaga as pendências normais do relatório e regrava a partir do PDF. As de
tipo PARCELAMENTO/PGFN (4o desvio) são preservadas — quem cuida delas é o
`reprocessar_parcelamentos_pgfn.py`.

Uso:
    python scripts/reprocessar_pendencias.py --dry-run     # só mostra o que mudaria
    python scripts/reprocessar_pendencias.py               # aplica
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tipos gravados pelo desvio 4 (parcelamento/PGFN) — não são lidos por _extract_pendencias.
TIPOS_PRESERVADOS = ('PARCELAMENTO', 'PGFN')


def _resumo(pendencias):
    return sorted(f"{p.tipo} {p.ano}" for p in pendencias)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true',
                    help='não grava nada; só mostra a diferença')
    args = ap.parse_args()

    from app import create_app
    from app.extensions import db
    from app.models import PendenciaRelatorio, RelatorioSitFiscal
    from app.services.pdf_parser import PDFParser

    app = create_app()
    with app.app_context():
        relatorios = RelatorioSitFiscal.query.order_by(RelatorioSitFiscal.id).all()
        print(f'Relatórios no banco: {len(relatorios)}')
        if args.dry_run:
            print('MODO SIMULAÇÃO — nada será gravado.\n')

        leitor = PDFParser()
        alterados = sem_pdf = falhas = 0
        removidas = criadas = 0

        for rel in relatorios:
            caminho = Path(rel.pdf_local_path) if rel.pdf_local_path else None
            if not caminho or not caminho.exists():
                sem_pdf += 1
                continue

            try:
                lido = leitor.parse_pdf_content(str(caminho))
            except Exception as exc:
                falhas += 1
                print(f'  [{rel.id}] {rel.company.cnpj}: falha ao ler o PDF — {exc}')
                continue

            antigas = [p for p in rel.pendencias if p.tipo not in TIPOS_PRESERVADOS]
            novas = [(tipo, item.get('ano', ''), item.get('meses', []))
                     for tipo, itens in (lido.get('pendencias') or {}).items()
                     for item in itens]

            antes = _resumo(antigas)
            depois = sorted(f'{t} {a}' for t, a, _ in novas)
            if antes == depois:
                continue

            alterados += 1
            removidas += len(antigas)
            criadas += len(novas)
            nome = (rel.company.razao_social or rel.company.cnpj)[:34]
            print(f'  [{rel.id}] {nome}')
            print(f'      antes : {antes or "—"}')
            print(f'      depois: {depois or "—"}')

            if not args.dry_run:
                for p in antigas:
                    db.session.delete(p)
                for tipo, ano, meses in novas:
                    db.session.add(PendenciaRelatorio(
                        relatorio_id=rel.id, tipo=tipo, ano=ano, meses_json=meses))

        if not args.dry_run:
            db.session.commit()

        print()
        print(f'Relatórios alterados : {alterados}')
        print(f'Pendências removidas : {removidas}')
        print(f'Pendências gravadas  : {criadas}')
        if sem_pdf:
            print(f'Sem PDF em disco     : {sem_pdf} (não dá para reler; reprocesse '
                  f'pela tela — aí custa API)')
        if falhas:
            print(f'Falhas de leitura    : {falhas}')
        if args.dry_run:
            print('\nNada foi gravado. Rode sem --dry-run para aplicar.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
