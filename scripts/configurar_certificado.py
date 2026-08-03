"""Aponta o certificado A1 do contador para esta máquina.

Problema que este script resolve
--------------------------------
O banco guarda apenas o CAMINHO do .pfx (`settings.certificado_path`), nunca o
arquivo. Como o caminho é absoluto e contém o nome do usuário do Windows, ele
quebra ao trocar de máquina:

    máquina A: C:\\Users\\Jeandson\\OneDrive\\00_Nescon Contabilidade\\...
    máquina B: C:\\Users\\parce\\OneDrive\\Desktop\\OneDrive - Nescon\\OneDrive\\00_Nescon Contabilidade\\...

O .pfx mora no OneDrive (portanto sincroniza), e o caminho RELATIVO à raiz do
OneDrive é o mesmo nas duas. Este script descobre a raiz do OneDrive a partir da
própria localização do projeto, copia o .pfx para o diretório de certificados
local do app e grava esse caminho local no banco.

Rodar uma vez em cada máquina:

    .venv/Scripts/python.exe scripts/configurar_certificado.py

Se o .pfx estiver em outro lugar:

    .venv/Scripts/python.exe scripts/configurar_certificado.py --pfx "D:\\caminho\\cert.pfx"
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJETO = Path(__file__).resolve().parent.parent

# projeto_recuperado -> Central Pendencias Ecac -> 00_PROJETOS -> 00_Claude -> 01_Jean -> raiz
ONEDRIVE_ROOT = PROJETO.parents[4]

CAMINHO_RELATIVO_PFX = Path(
    '00_Nescon Contabilidade/0007_CERTIFICADO DIGITAL/Clientes/Nescon/e-CNPJ/2026/'
    'NESCON SERVICOS EMPRESARIAIS LTDA/'
    'NESCON SERVICOS EMPRESARIAIS LTDA35736034000123.pfx'
)


def localizar_pfx(informado: str | None) -> Path:
    if informado:
        caminho = Path(informado)
        if not caminho.exists():
            raise SystemExit(f'ERRO: arquivo não encontrado: {caminho}')
        return caminho

    candidato = ONEDRIVE_ROOT / CAMINHO_RELATIVO_PFX
    if candidato.exists():
        return candidato

    # fallback: procura pelo CNPJ no nome, dentro da pasta de certificados
    base = ONEDRIVE_ROOT / '00_Nescon Contabilidade' / '0007_CERTIFICADO DIGITAL'
    if base.exists():
        achados = list(base.rglob('*35736034000123*.pfx'))
        if achados:
            return achados[0]

    raise SystemExit(
        'ERRO: não encontrei o .pfx automaticamente.\n'
        f'  raiz do OneDrive deduzida: {ONEDRIVE_ROOT}\n'
        f'  caminho esperado         : {candidato}\n'
        'Rode novamente passando --pfx "<caminho completo do .pfx>"'
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pfx', help='caminho completo do .pfx (opcional)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    origem = localizar_pfx(args.pfx)
    print(f'Certificado encontrado : {origem}')
    print(f'Tamanho                : {origem.stat().st_size} bytes')

    from app import create_app
    from app.extensions import db
    from app.models import AppSetting

    app = create_app()
    with app.app_context():
        destino = Path(app.config['CERTS_DIR']) / 'contador_certificado.pfx'
        print(f'Destino local          : {destino}')

        setting = AppSetting.query.first()
        if setting is None:
            raise SystemExit(
                'ERRO: não há registro em `settings`. '
                'Rode antes: python scripts/migrar_banco_legado.py'
            )

        if args.dry_run:
            print('\n(dry-run: nada foi copiado nem gravado)')
            return

        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origem, destino)

        setting.certificado_path = str(destino)
        db.session.commit()

        print()
        print('OK: certificado copiado e caminho gravado no banco.')
        if not setting.certificado_password:
            print('ATENÇÃO: a senha do certificado está vazia em `settings`. '
                  'Preencha pela tela de Configurações.')


if __name__ == '__main__':
    main()
