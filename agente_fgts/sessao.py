"""Chrome dedicado ao FGTS e login conduzido.

O agente clica nos botões públicos do login e escolhe o certificado configurado na janela
do Chrome. O captcha do gov.br é SEMPRE resolvido por uma pessoa: o agente só detecta que
ele está aberto, avisa e espera. Não há disfarce da automação.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

HOST = 'fgtsdigital.sistema.gov.br'
BASE = f'https://{HOST}'


def no_portal(url: str) -> bool:
    partes = urlsplit(url or '')
    return partes.hostname == HOST and '/portal/login' not in partes.path


def achar_chrome() -> Optional[str]:
    for raiz in (os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'), os.environ.get('LOCALAPPDATA')):
        if raiz:
            caminho = Path(raiz) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'
            if caminho.exists():
                return str(caminho)
    return None


def porta_responde(porta: int) -> bool:
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{porta}/json/version', timeout=1)
        return True
    except Exception:
        return False


def abrir_chrome(porta: int, perfil: Path) -> None:
    """Abre o Chrome do FGTS se ainda não estiver aberto. Porta só em 127.0.0.1."""
    if porta_responde(porta):
        return
    exe = achar_chrome()
    if not exe:
        raise RuntimeError('Google Chrome não encontrado neste PC.')
    perfil.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([exe, '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={porta}',
                      f'--remote-allow-origins=http://127.0.0.1:{porta}', f'--user-data-dir={perfil}',
                      '--no-first-run', BASE + '/portal/servicos'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if porta_responde(porta):
            return
        time.sleep(0.5)
    raise RuntimeError('O Chrome do FGTS não abriu a porta local.')


def aba_do_portal(contexto):
    for pagina in contexto.pages:
        if urlsplit(pagina.url).hostname == HOST:
            return pagina
    return None


def captcha_aberto(pagina) -> bool:
    for quadro in pagina.locator('iframe[src*="hcaptcha"]').all():
        try:
            caixa = quadro.bounding_box() if quadro.is_visible() else None
        except Exception:
            caixa = None
        if caixa and caixa['height'] > 200:
            return True
    return False


def escolher_certificado(nome: str) -> str:
    """Responde à janela nativa do Chrome, só quando ela pede certificado para o gov.br.
    Devolve 'ausente', 'escolhido' ou 'nao_encontrado'."""
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
    for janela in Desktop(backend='uia').windows(class_name='Chrome_WidgetWin_1'):
        if 'gov.br' not in janela.window_text():
            continue
        for dialogo in janela.descendants(control_type='Window'):
            if dialogo.window_text() != 'Selecione um certificado':
                continue
            textos = [t.window_text() for t in dialogo.descendants(control_type='Text')]
            if not any('certificado.sso.acesso.gov.br' in t for t in textos):
                continue
            linhas = dialogo.descendants(control_type='DataItem')
            if not linhas:
                return 'nao_encontrado'
            janela.set_focus()
            linhas[0].click_input()
            send_keys('{HOME}')
            for _ in range(60):          # a lista só expõe as linhas visíveis
                time.sleep(0.15)
                item = next((i for i in dialogo.descendants(control_type='DataItem') if i.window_text() == nome), None)
                if item is not None:
                    item.click_input()
                    time.sleep(0.2)
                    next(b for b in dialogo.descendants(control_type='Button') if b.window_text() == 'OK').click_input()
                    return 'escolhido'
                send_keys('{DOWN}')
            return 'nao_encontrado'
    return 'ausente'


def conduzir_login(contexto, certificado: str, espera_s: int, avisar: Callable[[str], None]) -> bool:
    pagina = aba_do_portal(contexto) or (contexto.pages[0] if contexto.pages else contexto.new_page())
    if 'acesso.gov.br' not in pagina.url and not no_portal(pagina.url):
        pagina.goto(BASE + '/portal/servicos', wait_until='domcontentloaded')
        pagina.wait_for_timeout(2500)
    limite = time.time() + espera_s
    ultimo_clique, ultimo_aviso = 0.0, ''

    def aviso(texto):
        nonlocal ultimo_aviso
        if texto != ultimo_aviso:
            ultimo_aviso = texto
            avisar(texto)

    while time.time() < limite:
        if no_portal(pagina.url):
            aviso('Login concluído.')
            return True
        if certificado and 'acesso.gov.br' in pagina.url:
            situacao = escolher_certificado(certificado)
            if situacao == 'escolhido':
                aviso('Certificado escolhido.')
                pagina.wait_for_timeout(2500)
                continue
            if situacao == 'nao_encontrado':
                aviso('O certificado configurado não está na lista. Escolha na janela do Chrome.')
        if '/portal/login' in pagina.url:
            botao = pagina.get_by_role('button', name=re.compile('Entrar com GOV.BR', re.I))
            if botao.count():
                botao.first.click()
                pagina.wait_for_timeout(2500)
                continue
        if captcha_aberto(pagina):
            aviso('Resolva a verificação do gov.br na janela do Chrome do FGTS.')
        elif 'sso.acesso.gov.br/login' in pagina.url and time.time() - ultimo_clique > 15:
            alvo = pagina.get_by_text('Seu certificado digital', exact=True)
            if alvo.count():
                alvo.first.click()
                ultimo_clique = time.time()
        pagina.wait_for_timeout(1000)
    aviso('O login não terminou a tempo.')
    return False
