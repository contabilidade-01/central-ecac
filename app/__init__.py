from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from pathlib import Path
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from app.extensions import db
from app.config import Config
from app.routes.settings import settings_bp
from app.routes.companies import companies_bp
from app.routes.dashboard import dashboard_bp
from app.routes.reports import reports_bp
from app.routes.pagamentos import pagamentos_bp
from app.routes.parcelamentos import parcelamentos_bp
from app.services.startup_service import unlock_stale_processing
from app.migrations import run_migrations
from app.routes.api_costs import api_costs_bp
from app.routes.das_routes import das_bp
from app.routes.caixa_postal import caixa_postal_bp
from app.routes.license import license_bp


def configure_logging(app: Flask):
    logs_dir = Path(app.config['DATA_DIR']) / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / 'app.log'

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not any(isinstance(handler, RotatingFileHandler)
               and getattr(handler, 'baseFilename', '') == str(log_path)
               for handler in root_logger.handlers):
        try:
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=2000000,
                backupCount=5,
                encoding='utf-8',
            )
        except PermissionError:
            fallback_log_path = logs_dir / f'app_{os.getpid()}.log'
            file_handler = RotatingFileHandler(
                fallback_log_path,
                maxBytes=2000000,
                backupCount=2,
                encoding='utf-8',
            )

        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        ))
        root_logger.addHandler(file_handler)

    if not any(isinstance(handler, logging.StreamHandler)
               and not isinstance(handler, RotatingFileHandler)
               for handler in root_logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        ))
        root_logger.addHandler(console_handler)

    app.logger.setLevel(logging.INFO)
    app.logger.info('Log da aplicação iniciado em %s', log_path)


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(Path(__file__).resolve().parent / 'static' / 'app'),
        static_url_path='/',
    )

    app.config.from_object(Config)
    configure_logging(app)

    # DESVIO INTENCIONAL (9o) — atrás do proxy do EasyPanel/Traefik, quem termina o
    # HTTPS é o proxy; sem isto o Flask enxerga a requisição como http e monta URL
    # http:// (o navegador bloquearia por conteúdo misto). Confia nos cabeçalhos
    # X-Forwarded-* — correto atrás de proxy, NÃO exponha o container direto na porta.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    CORS(app, resources={'/api/*': {'origins': '*'}})
    db.init_app(app)

    # DESVIO INTENCIONAL (7o) — autenticacao HTTP Basic + /healthz, necessarios para
    # publicar o sistema numa VPS. So liga quando AUTH_USER/AUTH_PASSWORD existem no
    # ambiente; em localhost sem essas variaveis o comportamento e o do exe.
    from app.security import registrar_seguranca
    registrar_seguranca(app)

    # DESVIO INTENCIONAL do exe: com static_url_path='/', a rota estática do Flask
    # tem precedência sobre o catch-all da SPA, então recarregar/abrir direto uma
    # rota do React Router (ex.: /dashboard) devolvia 404. Ver CHANGELOG 30/07/2026.
    # DESVIO INTENCIONAL (9o) — config.json servido dinamicamente.
    #
    # A SPA compilada do exe lê `./config.json` de forma SÍNCRONA no boot e usa
    # `server_url + /api` como baseURL do axios; sem o arquivo cai no fallback
    # `http://127.0.0.1:5847/api`. O arquivo estático traz `http://localhost:5847`,
    # o que funciona no desktop e QUEBRA em qualquer domínio (além de ser conteúdo
    # misto sob HTTPS). Como o React é bundle sem fonte, a correção tem de ser no
    # servidor: devolvemos a origem da própria requisição.
    #
    # O `endswith` cobre a rota profunda: aberto direto em /parcelamentos, o pedido
    # relativo vira /parcelamentos/config.json.
    @app.before_request
    def servir_config_json():
        if request.path == '/config.json' or request.path.endswith('/config.json'):
            return jsonify({'server_url': request.host_url.rstrip('/')})
        return None

    @app.before_request
    def handle_spa_routes():
        path = request.path
        if (path == '/'
                or path.startswith('/api/')
                or path.startswith('/assets/')
                or path.startswith('/static/')
                or path == '/favicon.ico'
                # DESVIO INTENCIONAL (5o): tela propria servida pelo Flask; sem esta
                # excecao o catch-all devolveria o index.html da SPA do exe.
                or path == '/procuracoes'
                # DESVIO INTENCIONAL (8o): tela do agendamento automatico.
                or path == '/agendamento'
                # DESVIO INTENCIONAL (7o): healthcheck do container.
                or path == '/healthz'):
            return None
        if '.' in path.split('/')[-1]:
            return None
        return send_from_directory(app.static_folder, 'index.html')

    with app.app_context():
        from app import models  # noqa: F401
        db.create_all()
        run_migrations()
        unlock_stale_processing()

    app.register_blueprint(settings_bp, url_prefix='/api/settings')
    app.register_blueprint(companies_bp, url_prefix='/api/companies')
    app.register_blueprint(dashboard_bp, url_prefix='/api/dashboard')
    app.register_blueprint(reports_bp, url_prefix='/api/reports')
    app.register_blueprint(pagamentos_bp)
    app.register_blueprint(parcelamentos_bp, url_prefix='/api/parcelamentos')
    app.register_blueprint(api_costs_bp, url_prefix='/api/api-costs')
    app.register_blueprint(das_bp, url_prefix='/api/das')
    app.register_blueprint(caixa_postal_bp, url_prefix='/api/caixa-postal')
    app.register_blueprint(license_bp, url_prefix='/api/license')

    # DESVIO INTENCIONAL (5o) — blueprint que NAO existe no exe: tela /procuracoes e API
    # /api/procuracoes (situacao de procuracao por empresa + trava de chamadas pagas).
    # Pedido do Jean em 31/07/2026. Registrado por ultimo para nao alterar a ordem dos
    # blueprints do exe.
    from app.routes.procuracoes import procuracoes_bp
    app.register_blueprint(procuracoes_bp)

    # DESVIO INTENCIONAL (8o) — agendamento automatico das rotinas + teto de gasto.
    # Tela /agendamento e API /api/agendamento*. Pedido do Jean em 02/08/2026.
    from app.routes.agendamento import agendamento_bp
    app.register_blueprint(agendamento_bp)

    from app.scheduler import iniciar as iniciar_agendador
    iniciar_agendador(app)

    @app.get('/')
    def index():
        static_dir = Path(app.static_folder)
        return send_from_directory(static_dir, 'index.html')

    @app.get('/<path:path>')
    def spa(path: str):
        static_dir = Path(app.static_folder)
        file_path = static_dir / path
        if file_path.exists() and file_path.is_file():
            return send_from_directory(static_dir, path)
        return send_from_directory(static_dir, 'index.html')

    return app
