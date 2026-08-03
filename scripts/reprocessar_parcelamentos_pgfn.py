"""Reprocessa os PDFs JA SALVOS para extrair parcelamento ativo e inscricao na PGFN.

Nao chama a SERPRO — le os PDFs de `reports/` que ja estao em disco, entao nao consome
API. Serve para aplicar retroativamente o desvio pedido pelo Jean em 31/07/2026 nos
relatorios que foram processados antes da mudanca.

Idempotente: apaga as pendencias de tipo PARCELAMENTO/PGFN do relatorio antes de gravar.

Uso:
    .venv/Scripts/python.exe scripts/reprocessar_parcelamentos_pgfn.py [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TIPOS_DESVIO = ('PARCELAMENTO', 'PGFN')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    from app import create_app
    from app.extensions import db
    from app.models import PendenciaRelatorio, RelatorioSitFiscal
    from app.services.pdf_parser import PDFParser
    from app.services.report_service import ReportService

    app = create_app()
    with app.app_context():
        relatorios = RelatorioSitFiscal.query.order_by(RelatorioSitFiscal.id).all()
        print(f'Relatorios no banco: {len(relatorios)}')

        parser_pdf = PDFParser()
        tocados = 0
        sem_pdf = 0

        for rel in relatorios:
            caminho = Path(rel.pdf_local_path) if rel.pdf_local_path else None
            if not caminho or not caminho.exists():
                sem_pdf += 1
                print(f'  [{rel.id}] {rel.company.cnpj}: PDF ausente, pulando')
                continue

            parsed = parser_pdf.parse_pdf_content(str(caminho))
            itens = parsed.get('parcelamentos_pgfn') or []

            antigas = [p for p in rel.pendencias if p.tipo in TIPOS_DESVIO]

            print(f'  [{rel.id}] {rel.company.cnpj} - {rel.company.razao_social[:38]}')
            print(f'        itens no PDF: {len(itens)} | pendencias desse tipo já '
                  f'gravadas: {len(antigas)}')

            if args.dry_run:
                for it in itens:
                    print(f'        -> {it["origem"]}/{it["tipo"]}: {it["descricao"]}')
                continue

            for p in antigas:
                db.session.delete(p)
            db.session.flush()

            ReportService._gravar_pendencias_parcelamento_pgfn(rel, parsed)
            tocados += 1

        if not args.dry_run:
            db.session.commit()

        print()
        print(f'  relatorios atualizados : {tocados}')
        print(f'  sem PDF em disco       : {sem_pdf}')
        if args.dry_run:
            print('  (dry-run: nada foi gravado)')


if __name__ == '__main__':
    main()
