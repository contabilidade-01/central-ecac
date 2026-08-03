"""Autenticação, permissões, healthcheck e erro 500 neutro.

⚠️ DESVIO INTENCIONAL — NÃO existe no exe, que era um aplicativo DESKTOP escutando só em
localhost, com um único operador: o dono da máquina.

Histórico
---------
* **7o desvio** — HTTP Basic por `AUTH_USER`/`AUTH_PASSWORD`.
* **10o desvio** — tela de login com sessão. O Basic dependia de variável de ambiente e,
  quando o painel não a aplicava, o sistema **abria sem senha sem ninguém perceber**
  (aconteceu em 03/08/2026). Hoje, sem credencial, o sistema não abre: exige o primeiro
  acesso.
* **13o desvio** — vários usuários, com **rotinas** e **empresas** limitadas por usuário.

Onde a permissão é aplicada
---------------------------
Num único `before_request` aqui, mais um filtro de resposta. **Nenhuma rota do exe foi
tocada** — a regra vive fora delas, então a fidelidade ao bytecode continua intacta.

Três camadas:
1. **Rotina** — o prefixo da URL é mapeado para uma chave (`caixa_postal`, `das`…) e
   comparado com o que o usuário pode abrir.
2. **Empresa (entrada)** — `company_id` na URL ou no corpo é conferido contra a lista do
   usuário. Operação de escrita **sem** empresa explícita é recusada para usuário
   restrito: é exatamente o caso dos lotes, que rodam sobre a carteira inteira.
3. **Empresa (saída)** — as listas devolvidas pela API são filtradas, para que dado de
   empresa não liberada não chegue ao navegador.

Limitação conhecida e documentada: os **totais do Dashboard** são somados no servidor
sobre a carteira inteira. Filtrar linha a linha não recalcula agregado. Se o operador não
puder ver números globais, **não libere a rotina `dashboard`** para ele.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import timedelta

from flask import (Response, jsonify, redirect, render_template_string, request,
                   session, url_for)

from app.services import permissoes, usuarios_service
from app.ui import CSS, SELO

CAMINHOS_LIVRES = ('/healthz', '/login', '/logout', '/primeiro-acesso',
                   '/definir-senha')

# --- proteção contra tentativa em massa -------------------------------------
_TENTATIVAS: dict = {}
_TENTATIVAS_LOCK = threading.Lock()
MAX_TENTATIVAS = 5
BLOQUEIO_S = 300


def _ip() -> str:
    return request.remote_addr or 'desconhecido'


def _bloqueado() -> int:
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
        _TENTATIVAS[_ip()] = [falhas + 1, time.time() + BLOQUEIO_S]


def _limpar_falhas() -> None:
    with _TENTATIVAS_LOCK:
        _TENTATIVAS.pop(_ip(), None)


def auth_habilitada() -> bool:
    return usuarios_service.configurado()


def usuario_atual():
    """Usuário da sessão (ou do HTTP Basic). None se não autenticado."""
    usuario_id = session.get('usuario_id')
    if usuario_id:
        if usuario_id == 'ambiente':
            return {'id': 'ambiente', 'usuario': os.getenv('AUTH_USER') or 'ambiente',
                    'nome': 'Acesso por variável de ambiente', 'papel': 'admin',
                    'ativo': True, 'empresas': permissoes.TODAS,
                    'rotinas': permissoes.TODAS}
        alvo = usuarios_service.buscar(usuario_id=usuario_id)
        if alvo and alvo.get('ativo'):
            return alvo
        session.clear()
        return None

    credencial = request.authorization
    if credencial:
        return usuarios_service.verificar(credencial.username or '',
                                          credencial.password or '')
    return None


def _destino_seguro(valor):
    if not valor or not valor.startswith('/') or valor.startswith('//'):
        return '/'
    return valor


def _negar(mensagem: str, codigo: int = 403):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'message': mensagem}), codigo
    return Response(mensagem, codigo, {'Content-Type': 'text/plain; charset=utf-8'})


def _empresas_do_pedido():
    """IDs de empresa citados na URL e no corpo da requisição."""
    ids = []
    if request.view_args:
        cid = request.view_args.get('company_id')
        if cid is not None:
            ids.append(cid)

    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        corpo = request.get_json(silent=True)
        if isinstance(corpo, dict):
            if corpo.get('company_id') is not None:
                ids.append(corpo['company_id'])
            for item in (corpo.get('company_ids') or []):
                ids.append(item)
        for chave in ('company_id', 'company_ids'):
            for valor in request.args.getlist(chave) + request.form.getlist(chave):
                ids.extend(str(valor).split(','))
    else:
        for chave in ('company_id', 'company_ids'):
            for valor in request.args.getlist(chave):
                ids.extend(str(valor).split(','))

    return [i for i in ids if str(i).strip()]


def registrar_seguranca(app) -> None:
    app.config.setdefault('PERMANENT_SESSION_LIFETIME', timedelta(hours=12))
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = (
        str(os.getenv('SESSION_COOKIE_SECURE', '')).lower() in ('1', 'true', 'sim', 'on')
    )

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def erro_interno(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc

        app.logger.exception('Erro não tratado em %s %s', request.method, request.path)
        if request.path.startswith('/api/'):
            return jsonify({'success': False,
                            'message': 'Erro interno. Consulte os logs do servidor.'}), 500
        return Response('Erro interno. O detalhe foi registrado no log do servidor.',
                        500, {'Content-Type': 'text/plain; charset=utf-8'})

    # ------------------------------------------------------------- guard único
    @app.before_request
    def exigir_login_e_permissao():
        caminho = request.path

        if caminho in CAMINHOS_LIVRES:
            return None

        if not usuarios_service.configurado():
            if caminho.startswith('/api/'):
                return jsonify({'success': False,
                                'message': 'Sistema sem credencial. Acesse /primeiro-acesso.'}), 401
            return redirect('/primeiro-acesso')

        usuario = usuario_atual()
        if not usuario:
            if caminho.startswith('/api/'):
                return jsonify({'success': False,
                                'message': 'Sessão expirada. Faça login novamente.'}), 401
            return redirect(url_for('login', proximo=caminho))

        # 1) telas exclusivas de administrador
        if any(caminho == p or caminho.startswith(p + '/') for p in permissoes.PREFIXOS_ADMIN):
            if not permissoes.e_admin(usuario):
                return _negar('Área restrita ao administrador.')
            return None

        if caminho in permissoes.LIVRES_LOGADO or caminho.startswith('/api/companies'):
            # `/api/companies` é liberado; a filtragem por empresa é feita na saída,
            # e a checagem de company_id na URL acontece logo abaixo.
            if not _checar_empresas(usuario):
                return _negar('Esta empresa não está liberada para o seu usuário.')
            return None

        # 1b) `/api/settings` no GET é infraestrutura da SPA, não "a tela Configurações".
        #
        # O bundle consulta esse endpoint no boot só para saber se o contador já foi
        # cadastrado (`contatorConfigured`). Bloqueando, ele entende "não configurado" e
        # prende o usuário na tela de Configurações — foi o que aconteceu com o operador
        # que só tinha Dashboard. Liberamos a LEITURA e removemos os segredos da resposta
        # em `_limpar_settings()`; qualquer escrita continua exclusiva de administrador.
        if caminho.startswith('/api/settings'):
            if request.method == 'GET':
                return None
            if not permissoes.pode_rotina(usuario, 'configuracoes'):
                return _negar('Seu usuário não tem acesso a "Configurações".')
            return None

        # 2) rotina
        chave = permissoes.rotina_da_rota(caminho)
        if chave and not permissoes.pode_rotina(usuario, chave):
            nome = permissoes.ROTINAS[chave]['nome']
            return _negar(f'Seu usuário não tem acesso a "{nome}".')

        # 3) empresa
        if not _checar_empresas(usuario):
            return _negar('Esta empresa não está liberada para o seu usuário.')

        return None

    def _checar_empresas(usuario) -> bool:
        """False quando o pedido toca empresa fora da lista do usuário."""
        if permissoes.empresas_do_usuario(usuario) == permissoes.TODAS:
            return True

        citadas = _empresas_do_pedido()
        if citadas:
            return all(permissoes.pode_empresa(usuario, c) for c in citadas)

        # Escrita sem empresa explícita = operação sobre a carteira inteira (lotes).
        # Para usuário restrito isso é recusado — é onde o dinheiro é gasto.
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return False
        return True

    # --------------------------------------------------- filtro de saída
    @app.after_request
    def filtrar_resposta(resposta):
        try:
            if not resposta.is_json or resposta.status_code >= 400:
                return resposta
            if not request.path.startswith('/api/'):
                return resposta
            # `/api/me` descreve o PRÓPRIO usuário (inclusive a lista de empresas
            # permitidas). Passar o filtro aqui seria filtrar a permissão com ela mesma.
            if request.path == '/api/me':
                return resposta

            usuario = usuario_atual()
            if not usuario:
                return resposta

            # Segredos da SERPRO nunca saem para quem não administra — mesmo que o
            # usuário possa ver todas as empresas.
            if request.path.startswith('/api/settings'):
                return _limpar_settings(resposta, usuario)

            if permissoes.empresas_do_usuario(usuario) == permissoes.TODAS:
                return resposta

            # Agregado não se conserta filtrando linha: os cartões do topo do painel
            # são somados no servidor sobre a carteira inteira. Aqui eles são
            # RECALCULADOS para as empresas do usuário.
            if request.path == '/api/dashboard/summary':
                resumo = permissoes.resumo_restrito(
                    permissoes.empresas_do_usuario(usuario))
                resposta.set_data(json.dumps(resumo, ensure_ascii=False))
                return resposta

            corpo = resposta.get_json(silent=True)
            filtrado, mudou = _filtrar_por_empresa(usuario, corpo)
            if mudou:
                resposta.set_data(json.dumps(filtrado, ensure_ascii=False))
        except Exception:  # nunca derrubar a resposta por causa do filtro
            app.logger.exception('Falha ao filtrar resposta por empresa')
        return resposta

    # Campos de `/api/settings` que só o administrador pode enxergar.
    SEGREDOS_SETTINGS = (
        'certificado_password', 'certificado_path',
        'serpro_consumer_key', 'serpro_consumer_secret',
        'procurador_cpf', 'procurador_nome', 'procurador_certificado_path',
        'procurador_certificado_password', 'procurador_token',
        'procurador_token_response_json', 'contador_cnpj',
    )

    def _limpar_settings(resposta, usuario):
        """Deixa passar só o que a SPA precisa; apaga credenciais para não-admin."""
        if permissoes.pode_rotina(usuario, 'configuracoes'):
            return resposta
        corpo = resposta.get_json(silent=True)
        if not isinstance(corpo, dict):
            return resposta
        limpo = {k: v for k, v in corpo.items() if k not in SEGREDOS_SETTINGS}
        for chave in SEGREDOS_SETTINGS:
            if chave in corpo:
                limpo[chave] = ''       # a SPA espera as chaves; devolvemos vazias
        resposta.set_data(json.dumps(limpo, ensure_ascii=False))
        return resposta

    def _filtrar_por_empresa(usuario, corpo):
        CHAVES_LISTA = ('itens', 'items', 'empresas', 'pedidos', 'parcelas', 'mensagens',
                        'companies', 'data', 'resultados', 'results', 'detalhes')

        if isinstance(corpo, list):
            limpo = permissoes.filtrar_empresas(usuario, corpo)
            return limpo, len(limpo) != len(corpo)

        if isinstance(corpo, dict):
            mudou = False
            novo = dict(corpo)
            for chave in CHAVES_LISTA:
                valor = novo.get(chave)
                if isinstance(valor, list):
                    limpo = permissoes.filtrar_empresas(usuario, valor)
                    if len(limpo) != len(valor):
                        novo[chave] = limpo
                        mudou = True
            return novo, mudou

        return corpo, False

    # ------------------------------------------------------------------ rotas
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not usuarios_service.configurado():
            return redirect('/primeiro-acesso')

        proximo = _destino_seguro(request.form.get('proximo') or request.args.get('proximo'))

        if session.get('usuario_id') and request.method == 'GET':
            return redirect(proximo)

        erro = None
        login_informado = ''

        if request.method == 'POST':
            restante = _bloqueado()
            if restante:
                erro = (f'Muitas tentativas. Aguarde {restante // 60 + 1} minuto(s) '
                        f'antes de tentar de novo.')
            else:
                login_informado = (request.form.get('usuario') or '').strip()
                senha = request.form.get('senha') or ''
                encontrado = usuarios_service.verificar(login_informado, senha)
                if encontrado:
                    _limpar_falhas()
                    session.clear()
                    session['usuario_id'] = encontrado['id']
                    session.permanent = True
                    if encontrado['id'] != 'ambiente':
                        usuarios_service.registrar_login(encontrado['id'])
                    app.logger.info('Login OK: %s (%s)', login_informado, _ip())
                    return redirect(proximo)
                _registrar_falha()
                app.logger.warning('Login FALHOU: %s (%s)', login_informado, _ip())
                erro = 'Usuário ou senha inválidos.'

        pagina = render_template_string(PAGINA_LOGIN, erro=erro,
                                        usuario=login_informado, proximo=proximo)
        return (pagina, 401) if erro else pagina

    @app.route('/primeiro-acesso', methods=['GET', 'POST'])
    def primeiro_acesso():
        """Cria o PRIMEIRO administrador. Some assim que existir credencial."""
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
                    criado = usuarios_service.criar(
                        usuario=usuario, nome=request.form.get('nome') or usuario,
                        papel=usuarios_service.PAPEL_ADMIN, senha=senha)
                except ValueError as exc:
                    erro = str(exc)
                else:
                    session.clear()
                    session['usuario_id'] = criado['id']
                    session.permanent = True
                    app.logger.warning('Administrador criado no primeiro acesso: %s (%s)',
                                       usuario, _ip())
                    return redirect('/')

        return render_template_string(PAGINA_PRIMEIRO_ACESSO, erro=erro, usuario=usuario)

    @app.route('/definir-senha', methods=['GET', 'POST'])
    def definir_senha():
        """Primeiro acesso / recuperação por link gerado pelo administrador."""
        token = request.form.get('token') or request.args.get('token') or ''
        valido = usuarios_service.validar_convite(token)

        if not valido:
            return render_template_string(
                PAGINA_DEFINIR_SENHA, token=token, erro=None, expirado=True,
                nome='', tipo=''), 400

        alvo = valido['usuario']
        tipo = valido['convite']['tipo']
        erro = None

        if request.method == 'POST':
            senha = request.form.get('senha') or ''
            confirmacao = request.form.get('confirmacao') or ''
            if senha != confirmacao:
                erro = 'As duas senhas não são iguais.'
            else:
                try:
                    usuario = usuarios_service.consumir_convite(token, senha)
                except ValueError as exc:
                    erro = str(exc)
                else:
                    session.clear()
                    session['usuario_id'] = usuario['id']
                    session.permanent = True
                    usuarios_service.registrar_login(usuario['id'])
                    app.logger.warning('Senha definida por convite: %s (%s)',
                                       usuario['usuario'], _ip())
                    return redirect('/')

        return render_template_string(PAGINA_DEFINIR_SENHA, token=token, erro=erro,
                                      expirado=False, nome=alvo.get('nome') or alvo['usuario'],
                                      tipo=tipo)

    @app.get('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    @app.get('/api/me')
    def api_me():
        usuario = usuario_atual()
        if not usuario:
            return jsonify({'autenticado': False}), 401
        return jsonify({
            'autenticado': True,
            'id': usuario['id'],
            'usuario': usuario['usuario'],
            'nome': usuario.get('nome') or usuario['usuario'],
            'papel': usuario.get('papel'),
            'admin': permissoes.e_admin(usuario),
            'rotinas': permissoes.rotinas_do_usuario(usuario),
            'empresas': permissoes.empresas_do_usuario(usuario),
            # DESVIO INTENCIONAL (14o) — a SPA compilada monta a barra lateral a partir
            # daqui. Mesma fonte que as telas do Flask usam, então as duas não divergem.
            'menu': permissoes.menu_do_usuario(usuario),
            'icone_sair': permissoes.ICONES['sair'],
        })

    @app.get('/healthz')
    def healthz():
        from app.extensions import db

        try:
            db.session.execute(db.text('SELECT 1'))
            banco = 'ok'
        except Exception as exc:  # pragma: no cover
            banco = f'erro: {exc}'

        return jsonify({
            'status': 'ok' if banco == 'ok' else 'degradado',
            'banco': banco,
            'auth': 'ligada' if auth_habilitada() else 'DESLIGADA',
            'credencial': usuarios_service.origem() or 'nenhuma',
        }), (200 if banco == 'ok' else 503)


# --------------------------------------------------------------------- telas

_BASE_CSS = CSS


PAGINA_LOGIN = """
<!doctype html><html lang="pt-br"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entrar — Central Pendências e-CAC</title><style>""" + _BASE_CSS + """</style></head>
<body class="centro">
  <form class="card" method="post" autocomplete="on">
""" + SELO + """
    <h1>Entrar</h1>
    <p class="sub">Acesso restrito. Sistema com dados fiscais de clientes.</p>
    {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
    <label for="usuario">Usuário</label>
    <input id="usuario" name="usuario" autocomplete="username" required autofocus
           value="{{ usuario or '' }}">
    <label for="senha">Senha</label>
    <input id="senha" name="senha" type="password" autocomplete="current-password" required>
    <input type="hidden" name="proximo" value="{{ proximo }}">
    <button class="primario" type="submit">Entrar</button>
    <p class="rodape">Esqueceu a senha? Peça um link ao administrador.</p>
  </form>
</body></html>
"""

PAGINA_PRIMEIRO_ACESSO = """
<!doctype html><html lang="pt-br"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Primeiro acesso — Central Pendências e-CAC</title>
<style>""" + _BASE_CSS + """</style></head>
<body class="centro">
  <form class="card" method="post" autocomplete="off">
""" + SELO + """
    <h1>Primeiro acesso</h1>
    <p class="sub">
      Nenhuma credencial foi configurada. Defina o <b>administrador</b> do sistema —
      enquanto isso não for feito, nada abre.
    </p>
    <div class="aviso">
      Faça isto <b>imediatamente após publicar</b>. Até a senha existir, quem souber o
      endereço pode cadastrá-la.
    </div>
    {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
    <label for="usuario">Usuário (CPF)</label>
    <input id="usuario" name="usuario" required autofocus value="{{ usuario or '' }}">
    <label for="nome">Nome</label>
    <input id="nome" name="nome">
    <label for="senha">Senha (mínimo 8 caracteres)</label>
    <input id="senha" name="senha" type="password" required>
    <label for="confirmacao">Repita a senha</label>
    <input id="confirmacao" name="confirmacao" type="password" required>
    <button class="primario" type="submit">Definir e entrar</button>
    <p class="rodape">A senha é gravada em hash, no volume de dados.</p>
  </form>
</body></html>
"""

PAGINA_DEFINIR_SENHA = """
<!doctype html><html lang="pt-br"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Definir senha — Central Pendências e-CAC</title>
<style>""" + _BASE_CSS + """</style></head>
<body class="centro">
  {% if expirado %}
  <div class="card">
    <h1>Link inválido</h1>
    <p class="sub">
      Este link já foi usado, expirou ou não é válido. Peça ao administrador que gere
      outro para você.
    </p>
    <p class="rodape"><a href="/login">Ir para a tela de entrada</a></p>
  </div>
  {% else %}
  <form class="card" method="post" autocomplete="off">
    <h1>{% if tipo == 'recuperacao' %}Nova senha{% else %}Bem-vindo{% endif %}</h1>
    <p class="sub">
      {{ nome }}, defina {% if tipo == 'recuperacao' %}a sua nova senha{% else %}a sua
      senha de acesso{% endif %}. Este link é de uso único.
    </p>
    {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
    <input type="hidden" name="token" value="{{ token }}">
    <label for="senha">Senha (mínimo 8 caracteres)</label>
    <input id="senha" name="senha" type="password" required autofocus
           autocomplete="new-password">
    <label for="confirmacao">Repita a senha</label>
    <input id="confirmacao" name="confirmacao" type="password" required
           autocomplete="new-password">
    <button class="primario" type="submit">Salvar e entrar</button>
    <p class="rodape">Só você conhece esta senha — ela é gravada em hash.</p>
  </form>
  {% endif %}
</body></html>
"""
