"""
Utilitários diversos para Integra Contador Desktop
"""

# Este módulo contém funções utilitárias gerais
# Reexportar do módulo paths
from app.utils.paths import app_data_dir, program_dir, resource_path
from app.utils.browser import open_browser_when_ready

__all__ = ['app_data_dir', 'program_dir', 'resource_path', 'open_browser_when_ready']
