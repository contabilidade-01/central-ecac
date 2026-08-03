"""Proteção de acesso (HTTP Basic) e healthcheck.

⚠️ DESVIO INTENCIONAL (7o) — NÃO existe no exe. O exe é um aplicativo DESKTOP que só
escuta em localhost; ninguém de fora alcançava as telas. Ao publicar numa VPS, as
mesmas rotas ficam expostas à internet — e elas mostram dados fiscais de 72 empresas
e disparam chamadas PAGAS à SERPRO em nome dos clientes. Sem autenticação o deploy
seria irresponsável.

Como funciona
-------------
* Ligado apenas quando `AUTH_USER` e `AUTH_PASSWORD` existem no ambiente. Sem elas o
  comportamento é o do exe (aberto) — assim o uso local em `localhost` não muda.
* HTTP Basic: o navegador pede usuário/senha uma vez e repete o cabeçalho sozinho.
  Não exige alterar o React compilado (que não temos o fonte).
* Comparação com `secrets.compare_digest` (tempo constante).
* `/healthz` fica SEMPRE liberado — o EasyPanel/Docker precisa dele para saber se o
  container está vivo, e ele não expõe nenhum dado.

Se um dia o sistema tiver vários usuários, troque isto por login de verdade; para um
escritório com um operador, Basic + HTTPS do EasyPanel é adequado e simples.
"""

from __future__ import annotations

import os
import secrets

from flask import Response, jsonify, request

CAMINHOS_LIVRES = ('/healthz',)


def _credenciais():
    return os.getenv('AUTH_USER'), os.getenv('AUTH_PASSWORD')


def auth_habilitada() -> bool:
    usuario, senha = _credenciais()
    return bool(usuario and senha)


def _nao_autorizado() -> Response:
    return Response(
        'Acesso restrito — informe usuário e senha.',
        401,
        {'WWW-Authenticate': 'Basic realm="Central Pendencias e-CAC"'},
    )


def registrar_seguranca(app) -> None:
    """Instala o guard de autenticação, a rota /healthz e o tratador de erro 500."""

    # DESVIO INTENCIONAL (7o) — melhoria #12: o Flask devolveria o traceback padrão,
    # que expõe caminhos do servidor. Aqui o erro completo vai para o LOG e o usuário
    # recebe uma resposta neutra.
    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def erro_interno(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc  # 404, 401, 405… seguem o comportamento normal

        app.logger.exception('Erro não tratado em %s %s', request.method, request.path)
        if request.path.startswith('/api/'):
            return jsonify({
                'success': False,
                'message': 'Erro interno. Consulte os logs do servidor.',
            }), 500
        return Response(
            'Erro interno. O detalhe foi registrado no log do servidor.',
            500,
            {'Content-Type': 'text/plain; charset=utf-8'},
        )

    @app.before_request
    def exigir_login():
        if not auth_habilitada():
            return None
        if request.path in CAMINHOS_LIVRES:
            return None

        usuario, senha = _credenciais()
        credencial = request.authorization
        if (credencial is None
                or not secrets.compare_digest(credencial.username or '', usuario)
                or not secrets.compare_digest(credencial.password or '', senha)):
            return _nao_autorizado()
        return None

    @app.get('/healthz')
    def healthz():
        """Liveness/readiness — responde sem tocar na SERPRO e sem exigir login."""
        from app.extensions import db

        try:
            db.session.execute(db.text('SELECT 1'))
            banco = 'ok'
        except Exception as exc:  # pragma: no cover - só em falha real de disco
            banco = f'erro: {exc}'

        return jsonify({
            'status': 'ok' if banco == 'ok' else 'degradado',
            'banco': banco,
            'auth': 'ligada' if auth_habilitada() else 'DESLIGADA',
        }), (200 if banco == 'ok' else 503)
