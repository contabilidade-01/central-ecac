"""Conexão com o Chrome do operador (sessão já logada no FGTS Digital).

⚠️ Este módulo NÃO faz login e NÃO resolve captcha. Ele se LIGA, pela porta de depuração,
a um Chrome que o OPERADOR abriu e no qual o OPERADOR entrou à mão no FGTS Digital (com o
certificado e o captcha). A automação só reaproveita essa sessão autenticada para o
trabalho de escritório (perfil, consulta, emissão). Nunca opera a tela de login.

Nada aqui tenta disfarçar que há automação: usa uma ligação de depuração comum
(`connect_over_cdp`), sem alterar impressão digital do navegador. Se o portal recusar
algum passo DEPOIS do login, o erro sobe para a tela — não há contorno.

Ao terminar, fecha só a aba que a automação abriu; o Chrome e as abas do operador ficam.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fgts_portal import FalhaFgts, PortalFgts, URL_SERVICOS

logger = logging.getLogger(__name__)


class SemChrome(FalhaFgts):
    """O Chrome do operador não está acessível na porta de depuração."""


class SemLogin(FalhaFgts):
    """O Chrome está acessível, mas a sessão não está logada no FGTS Digital."""


class AutomacaoLocal(PortalFgts):
    """Liga ao Chrome do operador e roda os passos do FGTS Digital na sessão dele."""

    def __init__(self, porta: int = 9222, pasta_diag: Optional[Path] = None):
        super().__init__(page=None)
        self.porta = porta
        self.max_competencias = int(os.getenv('FGTS_MAX_COMPETENCIAS', '0') or 0)
        self._pw = None
        self._browser = None
        self._cdp = None
        self._pasta_download = Path(tempfile.mkdtemp(prefix='fgts_dl_'))
        self._concluidos: Dict[str, int] = {}     # guid do CDP → 1 quando o download termina
        self._consumidos: set = set()
        self._pasta_diag = pasta_diag

    # ------------------------------------------------------------------ ligação
    def conectar(self) -> None:
        """Liga ao Chrome na porta de depuração e prepara uma aba própria. Não faz login."""
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(f'http://127.0.0.1:{self.porta}', timeout=8000)
        except Exception as exc:
            self._parar_pw()
            raise SemChrome('Não encontrei o Chrome do FGTS aberto. Use o atalho "Abrir Chrome do FGTS" '
                            'e entre no FGTS Digital antes de continuar.') from exc
        if not self._browser.contexts:
            self._parar_pw()
            raise SemChrome('O Chrome do FGTS está aberto, mas sem nenhuma janela. Abra o FGTS Digital nele.')
        contexto = self._browser.contexts[0]
        self._page = contexto.new_page()
        self._page.on('dialog', lambda d: d.dismiss())
        # Downloads da nossa aba vão para uma pasta só nossa (o operador não vê diálogo).
        try:
            self._cdp = self._browser.new_browser_cdp_session()
            self._cdp.on('Browser.downloadProgress',
                         lambda e: e.get('state') == 'completed' and self._concluidos.setdefault(e['guid'], 1))
            self._cdp.send('Browser.setDownloadBehavior',
                           {'behavior': 'allowAndName', 'downloadPath': str(self._pasta_download),
                            'eventsEnabled': True})
        except Exception:
            self._cdp = None       # sem isso ainda sobra a resposta de rede e o download do Playwright

    def logado(self) -> bool:
        """Abre a área logada do FGTS Digital e diz se a sessão do operador está válida."""
        if not self._page:
            return False
        try:
            self._page.goto(URL_SERVICOS, wait_until='domcontentloaded')
            self._esperar_assentar()
        except Exception:
            return False
        return self.no_portal()

    def exigir_login(self) -> None:
        if not self.logado():
            raise SemLogin('Entre no FGTS Digital no Chrome aberto (certificado e captcha) e tente de novo.')

    # ------------------------------------------------------------------ ganchos
    def _download_cdp(self) -> Optional[bytes]:
        for guid in list(self._concluidos):
            if guid in self._consumidos:
                continue
            caminho = self._pasta_download / guid
            try:
                conteudo = caminho.read_bytes()
            except Exception:
                continue
            if conteudo.startswith(b'%PDF'):
                self._consumidos.add(guid)
                return conteudo
        return None

    def _salvar_print(self, nome: str) -> None:
        if not (self._page and self._pasta_diag):
            return
        try:
            self._pasta_diag.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(self._pasta_diag / f'{datetime.now():%Y%m%d_%H%M%S}_{nome}.png'),
                                  full_page=True)
        except Exception:
            pass

    # ------------------------------------------------------------------ fim
    def fechar(self) -> None:
        """Fecha SÓ a aba que abrimos e desliga a ligação. O Chrome do operador continua."""
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()     # connect_over_cdp: desliga, não mata o Chrome
        except Exception:
            pass
        self._parar_pw()
        self._page = self._browser = self._cdp = None

    def _parar_pw(self) -> None:
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None

    def __enter__(self) -> 'AutomacaoLocal':
        self.conectar()
        return self

    def __exit__(self, *exc) -> None:
        self.fechar()


def diagnostico_chrome(porta: int = 9222) -> Dict[str, object]:
    """Para a tela: o Chrome está na porta? Há alguma aba do FGTS? Está logado?"""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{porta}/json/version', timeout=3) as r:
            versao = json.load(r).get('Browser', '')
    except Exception:
        return {'chrome': False, 'logado': False, 'abas_fgts': 0, 'versao': ''}
    abas = 0
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{porta}/json/list', timeout=3) as r:
            abas = sum(1 for t in json.load(r) if 'fgtsdigital.sistema.gov.br' in (t.get('url') or ''))
    except Exception:
        pass
    logado = False
    try:
        sess = AutomacaoLocal(porta)
        sess.conectar()
        try:
            logado = sess.logado()
        finally:
            sess.fechar()
    except Exception:
        logado = False
    return {'chrome': True, 'logado': logado, 'abas_fgts': abas, 'versao': versao}
