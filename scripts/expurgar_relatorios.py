"""Expurgo de relatórios de situação fiscal antigos (melhoria #6).

Cada processamento cria um `RelatorioSitFiscal` novo (com seus débitos e pendências) e
guarda um PDF de 30–160 KB. É assim que o painel faz um débito pago "sumir": ele lê só o
relatório MAIS RECENTE de cada empresa. O efeito colateral é acúmulo — com 72 empresas
processadas todo mês são ~900 PDFs/ano.

Este script mantém os N relatórios mais recentes de cada empresa e apaga o resto,
incluindo os PDFs em disco. **O mais recente nunca é tocado**, então o painel não muda.

Uso:
    python scripts/expurgar_relatorios.py --manter 12 --dry-run   # simula (padrão)
    python scripts/expurgar_relatorios.py --manter 12 --aplicar   # executa

Sugestão: rodar 1x por ano, ou quando o volume incomodar. Faça backup antes
(`scripts/backup_dados.py`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Company,
    DebitoRelatorio,
    PendenciaRelatorio,
    RelatorioSitFiscal,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--manter', type=int, default=12,
                    help='quantos relatórios manter por empresa (padrão 12)')
    ap.add_argument('--aplicar', action='store_true',
                    help='sem esta flag o script apenas simula')
    args = ap.parse_args()

    if args.manter < 1:
        print('ERRO: --manter deve ser no mínimo 1 (o painel lê o mais recente)')
        return 1

    app = create_app()
    with app.app_context():
        total_relatorios = total_debitos = total_pendencias = total_pdfs = 0

        for company in Company.query.order_by(Company.id.asc()).all():
            relatorios = (RelatorioSitFiscal.query
                          .filter_by(company_id=company.id)
                          .order_by(RelatorioSitFiscal.id.desc())
                          .all())
            velhos = relatorios[args.manter:]
            if not velhos:
                continue

            ids = [r.id for r in velhos]
            debitos = DebitoRelatorio.query.filter(
                DebitoRelatorio.relatorio_id.in_(ids)).count()
            pendencias = PendenciaRelatorio.query.filter(
                PendenciaRelatorio.relatorio_id.in_(ids)).count()

            print(f'{company.razao_social[:40]:42} mantém {args.manter:>3} · '
                  f'remove {len(velhos):>3} relatórios, {debitos} débitos, '
                  f'{pendencias} pendências')

            total_relatorios += len(velhos)
            total_debitos += debitos
            total_pendencias += pendencias

            if args.aplicar:
                for relatorio in velhos:
                    caminho = relatorio.pdf_local_path
                    if caminho:
                        arquivo = Path(caminho)
                        if arquivo.exists():
                            arquivo.unlink()
                            total_pdfs += 1
                DebitoRelatorio.query.filter(
                    DebitoRelatorio.relatorio_id.in_(ids)).delete(synchronize_session=False)
                PendenciaRelatorio.query.filter(
                    PendenciaRelatorio.relatorio_id.in_(ids)).delete(synchronize_session=False)
                RelatorioSitFiscal.query.filter(
                    RelatorioSitFiscal.id.in_(ids)).delete(synchronize_session=False)

        if args.aplicar:
            db.session.commit()
            print(f'\nAPLICADO: {total_relatorios} relatórios, {total_debitos} débitos, '
                  f'{total_pendencias} pendências e {total_pdfs} PDFs removidos.')
        else:
            print(f'\nSIMULAÇÃO: removeria {total_relatorios} relatórios, '
                  f'{total_debitos} débitos e {total_pendencias} pendências.')
            print('Rode com --aplicar para executar (faça backup antes).')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
