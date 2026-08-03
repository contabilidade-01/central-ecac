"""
Integra Contador Desktop - Production Entry Point
=====================================================

Executor robusto para PRODUÇÃO com servidor local (Waitress).
Usa configurações de produção e logging adequado.

Para executar:
    python main_production.py

Ou com Gunicorn (mais robusto):
    gunicorn -w 4 -b 0.0.0.0:8587 main_production:app
"""

import os
import sys
import logging
from pathlib import Path

# Adicionar o diretório do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configurar variável de ambiente para produção
os.environ.setdefault('FLASK_ENV', 'production')

from app import create_app

# Configurar logging para produção
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('integra_contador.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Criar aplicação (usa configuração definida em create_app)
app = create_app()
# Apenas sobrescrever opções de produção sem alterar o banco
app.config['DEBUG'] = False
app.config['TESTING'] = False

if __name__ == '__main__':
    logger.info('='*60)
    logger.info('Integra Contador Desktop - PRODUÇÃO')
    logger.info('Central de Pendências e-CAC')
    logger.info('='*60)
    logger.info(f'Ambiente: {app.config.get("ENV", "production")}')
    logger.info(f'Debug: {app.config.get("DEBUG", False)}')
    logger.info(f'Database: {app.config.get("SQLALCHEMY_DATABASE_URI")}')
    logger.info('')

    # Usar Waitress para servidor robusto
    try:
        from waitress import serve
        logger.info('Iniciando servidor Waitress em http://0.0.0.0:5847')
        logger.info('Pressione Ctrl+C para encerrar')
        logger.info('')
        serve(
            app,
            host='0.0.0.0',
            port=5847,
            threads=4,
            _quiet=False,
        )
    except ImportError:
        logger.warning('Waitress não instalado. Usando Flask dev server (NÃO RECOMENDADO PARA PRODUÇÃO)')
        app.run(
            host='0.0.0.0',
            port=5847,
            debug=False,
            use_reloader=False
        )