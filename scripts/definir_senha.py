"""Define ou troca a credencial de acesso ao sistema.

Uso (no terminal do container, no EasyPanel, ou na máquina local):

    python scripts/definir_senha.py                    # pergunta usuário e senha
    python scripts/definir_senha.py 05487541523        # já informa o usuário

A senha é pedida sem eco (não aparece na tela nem fica no histórico do shell) e é
gravada em **hash** em `<DATA_DIR>/instance/usuarios.json`. Rode de novo a qualquer
momento para trocar a senha — inclusive se esquecê-la, já que sobrescreve.

Este script NÃO chama a SERPRO e não gasta nada.
"""

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.services import usuarios_service  # noqa: E402


def main() -> int:
    app = create_app()
    with app.app_context():
        atual = usuarios_service.carregar()
        if atual:
            print(f'Credencial atual: usuário "{atual["usuario"]}" '
                  f'(atualizada em {atual.get("atualizado_em", "?")})')
            print('Continuar substitui a senha.\n')
        else:
            print('Nenhuma credencial definida ainda.\n')

        if len(sys.argv) > 1:
            usuario = sys.argv[1].strip()
            print(f'Usuário: {usuario}')
        else:
            usuario = input('Usuário: ').strip()

        senha = getpass.getpass('Senha (mínimo 8 caracteres): ')
        confirmacao = getpass.getpass('Repita a senha: ')

        if senha != confirmacao:
            print('\nERRO: as senhas não conferem. Nada foi alterado.')
            return 1

        try:
            resultado = usuarios_service.definir(usuario, senha)
        except ValueError as exc:
            print(f'\nERRO: {exc}')
            return 1

        destino = Path(app.config['DATA_DIR']) / 'instance' / usuarios_service.ARQUIVO
        print(f'\nOK — credencial gravada em {destino}')
        print(f'Usuário: {resultado["usuario"]}')
        print('A senha foi salva em hash; o texto não fica em lugar nenhum.')

        if os.getenv('AUTH_USER') or os.getenv('AUTH_PASSWORD'):
            print('\nAviso: AUTH_USER/AUTH_PASSWORD ainda existem no ambiente. O arquivo '
                  'tem precedência, então essas variáveis podem ser removidas.')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
