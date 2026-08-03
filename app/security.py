"""Autenticação (tela de login), healthcheck e erro 500 neutro.

⚠️ DESVIO INTENCIONAL — NÃO existe no exe, que era um aplicativo DESKTOP escutando só em
localhost. Publicado numa VPS, as mesmas rotas expõem dados fiscais de 72 empresas e
botões que gastam dinheiro na API da SERPRO.

Histórico
---------
* **7o desvio** — HTTP Basic ligado por `AUTH_USER`/`AUTH_PASSWORD`.
* **10o desvio** — tela de login com sessão (melhoria #3 de `docs/MELHORIAS.md`). O Basic
  continua aceito, para `curl` e scripts, mas a porta de entrada do navegador agora é uma
  tela de verdade, com logout e proteção contra tentativa em massa.

Por que sessão e não só Basic
-----------------------------
O HTTP Basic depende de variável de ambiente. Se o painel de deploy não aplicar a
variável, o sistema **abre sem senha e ninguém percebe** — foi exatamente o que aconteceu
em 03/08/2026. Agora, quando não há credencial nenhuma, o sistema **não abre**: ele exige
o primeiro acesso, onde a senha é definida e gravada no volume.

Onde fica a credencial: `app/services/usuarios_service.py` (JSON no `DATA_DIR`, senha em
hash). Nada foi acrescentado a `app/models.py` — a regra inviolável 3 continua de pé.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import timedelta

from flask import (Response, jsonify, redirect, render_template_string, request,
                   session, url_for)

from app.services import usuarios_service

# Rotas que nunca exigem login.
CAMINHOS_LIVRES = ('/healthz', '/login', '/logout', '/primeiro-acesso')

# --- proteção contra tentativa em massa -------------------------------------
_TENTATIVAS: dict = {}
_TENTATIVAS_LOCK = threading.Lock()
MAX_TENTATIVAS = 5
BLOQUEIO_S = 300


def _ip() -> str:
    return request.remote_addr or 'desconhecido'


def _bloqueado() -> int:
    """Segundos restantes de bloqueio para este IP (0 = liberado)."""
    with _TENTATIVAS_LOCK:
        registro = _TENTATIVAS.get(_ip())
        if not registro:
            return 0
        falhas, ate = registro
        restante = int(ate - time.time())
        if restante <= 0:
            _TENTATIVAS.pop(_ip(), None)
            return 0
        return restante if falhas >= MAX_TENTATIVAS else 0


def _registrar_falha() -> None:
    with _TENTATIVAS_LOCK:
        falhas, _ = _TENTATIVAS.get(_ip(), [0, 0])
        falhas += 1
        _TENTATIVAS[_ip()] = [falhas, time.time() + BLOQUEIO_S]


def _limpar_falhas() -> None:
    with _TENTATIVAS_LOCK:
        _TENTATIVAS.pop(_ip(), None)


def auth_habilitada() -> bool:
    return usuarios_service.configurado()


def _destino_seguro(valor):
    """Evita open redirect: só aceita caminho interno."""
    if not valor or not valor.startswith('/') or valor.startswith('//'):
        return '/'
    return valor


# --------------------------------------------------------------------- telas

_BASE_CSS = """
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, "Segoe UI", sans-serif; margin:0; min-height:100vh;
         display:flex; align-items:center; justify-content:center; padding:24px;
         background:#f5f6f8; color:#1c1e21; }
  @media (prefers-color-scheme: dark) {
    body { background:#15171a; color:#e8eaed; }
    .card { background:#1e2125 !important; border-color:#2c3036 !important; }
    input { background:#15171a !important; color:#e8eaed !important;
            border-color:#2c3036 !important; } }
  .card { background:#fff; border:1px solid #e3e5e8; border-radius:12px; padding:28px;
          width:100%; max-width:380px; box-shadow:0 1px 3px rgba(0,0,0,.06); }
  h1 { font-size:19px; margin:0 0 4px; }
  p.sub { margin:0 0 22px; color:#6b7280; font-size:13px; line-height:1.5; }
  label { display:block; font-size:13px; font-weight:600; margin:0 0 6px; }
  input { width:100%; font:inherit; padding:10px 12px; border:1px solid #c7cad1;
          border-radius:8px; margin-bottom:16px; }
  input:focus { outline:2px solid #1a73e8; outline-offset:1px; border-color:#1a73e8; }
  button { width:100%; font:inherit; font-weight:600; padding:11px; border:0;
           border-radius:8px; background:#1a73e8; color:#fff; cursor:pointer; }
  button:hover { background:#1666d0; }
  .erro { background:#fce8e6; color:#b3261e; border-radius:8px; padding:10px 12px;
          font-size:13px; margin-bottom:16px; }
  .aviso { background:#fef7e0; color:#8a6116; border-radius:8px; padding:10px 12px;
           font-size:13px; margin-bottom:16px; line-height:1.5; }
  .rodape { margin:18px 0 0; font-size:12px; color:#6b7280; text-align:center; }
"""

PAGINA_LOGIN = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entrar — Central Pendências e-CAC</title>
<style>""" + _BASE_CSS + """</style>
</head>
<body>
  <form class="card" method="post" autocomplete="on">
    <h1>Central Pendências e-CAC</h1>
    <p class="sub">Acesso restrito. Sistema com dados fiscais de clientes.</p>

    {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}

    <label for="usuario">Usuário</label>
    <input id="usuario" name="usuario" autocomplete="username" required autofocus
           value="{{ usuario or '' }}">

    <label for="senha">Senha</label>
    <input id="senha" name="senha" type="password" autocomplete="current-password" required>

    <input type="hidden" name="proximo" value="{{ proximo }}">
    <button type="submit">Entrar</button>
  </form>
</body>
</html>
"""

PAGINA_PRIMEIRO_ACESSO = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Primeiro acesso — Central Pendências e-CAC</title>
<style>""" + _BASE_CSS + """</style>
</head>
<body>
  <form class="card" method="post" autocomplete="off">
    <h1>Primeiro acesso</h1>
    <p class="sub">
      Nenhuma credencial foi configurada ainda. Defina agora o usuário e a senha de
      acesso — enquanto isso não for feito, o sistema fica bloqueado.
    </p>

    <div class="aviso">
      Faça isto <b>imediatamente após publicar</b>. Até a senha ser definida, qualquer
      pessoa que souber o endereço pode cadastrá-la.
    </div>

    {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}

    <label for="usuario">Usuário</label>
    <input id="usuario" name="usuario" required autofocus value="{{ usuario or '' }}">

    <label for="senha">Senha (mínimo 8 caracteres)</label>
    <input id="senha" name="senha" type="password" required>

    <label for="confirmacao">Repita a senha</label>
    <input id="confirmacao" name="confirmacao" type="password" required>

    <button type="submit">Definir e entrar</button>
    <p class="rodape">A senha é gravada em hash, no volume de dados.</p>
  </form>
</body>
</html>
"""


def registrar_seguranca(app) -> None:
    """Instala login, guard de sessão, /healthz e o tratador de erro 500."""

    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(hours=12))
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # Em produção (HTTPS) ligue SESSION_COOKIE_SECURE=1. Fora dele o cookie não seria
    # enviado por http:// e o login local pararia de funcionar.
    app.config['SESSION_COOKIE_SECURE'] = (
        str(os.getenv('SESSION_COOKIE_SECURE', '')).lower() in ('1', 'true', 'sim', 'on')
    )

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
        caminho = request.path

        if caminho in CAMINHOS_LIVRES:
            return None

        # Nada configurado: ninguém entra sem antes definir a credencial.
        if not usuarios_service.configurado():
            if caminho.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'message': 'Sistema sem credencial definida. Acesse /primeiro-acesso.',
                }), 401
            return redirect('/primeiro-acesso')

        if session.get('usuario'):
            return None

        # HTTP Basic segue aceito, para curl/scripts (compatibilidade com o 7o desvio).
        credencial = request.authorization
        if credencial and usuarios_service.verificar(
                credencial.username or '', credencial.password or ''):
            return None

        if caminho.startswith('/api/'):
            return jsonify({
                'success': False,
                'message': 'Sessão expirada. Faça login novamente.',
            }), 401
        return redirect(url_for('login', proximo=caminho))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not usuarios_service.configurado():
            return redirect('/primeiro-acesso')

        proximo = _destino_seguro(
            request.form.get('proximo') or request.args.get('proximo'))

        if session.get('usuario') and request.method == 'GET':
            return redirect(proximo)

        erro = None
        usuario = ''

        if request.method == 'POST':
            restante = _bloqueado()
            if restante:
                erro = (f'Muitas tentativas. Aguarde {restante // 60 + 1} minuto(s) '
                        f'antes de tentar de novo.')
            else:
                usuario = (request.form.get('usuario') or '').strip()
                senha = request.form.get('senha') or ''
                if usuarios_service.verificar(usuario, senha):
                    _limpar_falhas()
                    session.clear()
                    session['usuario'] = usuario
                    session.permanent = True
                    app.logger.info('Login OK: %s (%s)', usuario, _ip())
                    return redirect(proximo)
                _registrar_falha()
                app.logger.warning('Login FALHOU: %s (%s)', usuario, _ip())
                erro = 'Usuário ou senha inválidos.'

        pagina = render_template_string(
            PAGINA_LOGIN, erro=erro, usuario=usuario, proximo=proximo)
        return (pagina, 401) if erro else pagina

    @app.route('/primeiro-acesso', methods=['GET', 'POST'])
    def primeiro_acesso():
        # Já existe credencial? Então esta tela não pode mais ser usada.
        if usuarios_service.configurado():
            return redirect('/login')

        erro = None
        usuario = ''

        if request.method == 'POST':
            usuario = (request.form.get('usuario') or '').strip()
            senha = request.form.get('senha') or ''
            confirmacao = request.form.get('confirmacao') or ''
            if senha != confirmacao:
                erro = 'As duas senhas não são iguais.'
            else:
                try:
                    usuarios_service.definir(usuario, senha)
                except ValueError as exc:
                    erro = str(exc)
                else:
                    session.clear()
                    session['usuario'] = usuario
                    session.permanent = True
                    app.logger.warning('Credencial definida no primeiro acesso: %s (%s)',
                                       usuario, _ip())
                    return redirect('/')

        return render_template_string(PAGINA_PRIMEIRO_ACESSO, erro=erro, usuario=usuario)

    @app.get('/logout')
    def logout():
        session.clear()
        return redirect('/login')

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
            'credencial': usuarios_service.origem() or 'nenhuma',
        }), (200 if banco == 'ok' else 503)
