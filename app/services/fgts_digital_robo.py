"""Robô do FGTS Digital (Playwright) — desvio 22.

⚠️ DESVIO INTENCIONAL — NÃO existe no exe. Pedido do Jean (23/10/2026): o cliente pede o
recálculo da guia de FGTS pelo link que o AtendeChat manda; este robô faz o trabalho no
portal https://fgtsdigital.sistema.gov.br como PROCURADOR, com o certificado A1 da
Nescon que já está no volume (`settings.certificado_path` / senha no banco).

Fluxo (mapeado na tela em 23/10/2026):
  1. portal/servicos → sem sessão cai em portal/login → "Entrar com GOV.BR"
  2. sso.acesso.gov.br → "Seu certificado digital" → o gov.br PODE abrir um hCaptcha
     de imagens (decide por risco: às vezes passa direto). Com captcha, o pedido vai
     para `aguardando_captcha`: a página do cliente mostra o recorte do desafio e cada
     toque dele vira um clique aqui (`clicar_captcha`). Este código NUNCA tenta
     resolver o captcha sozinho.
  3. certificado.sso.acesso.gov.br pede o certificado do cliente TLS → o Playwright
     apresenta o .pfx (`client_certificates`) → volta para portal/escolhaPerfil
  4. Perfil "Procurador" + CNPJ do cliente → "Definir". Sem procuração o portal diz
     "Não existe procuração para o Número de Inscrição selecionado."
  5. cobranca/#/gestao-guias/emissao-guia-rapida → competências com débito na lista
     "Competência de Apuração" → Pesquisar → "Total Devedor" e um bloco por grupo
     ("Mensal - Vencimento da Guia: dd/mm/aaaa") com o botão "Emitir guia"
  6. "Emitir guia" baixa a GFD em PDF (valor atualizado para hoje, pagar até hoje).

Arquitetura:
* UM thread dono do navegador (a API síncrona do Playwright não pode ser usada de
  outros threads). Pedidos entram numa fila e rodam um de cada vez — também porque o
  perfil "procurador de X" é da sessão inteira.
* Regra do Jean (23/10/2026): o navegador SEMPRE fecha ao fim de cada pedido (deu certo
  ou não) — nada de sessão do procurador aberta no servidor. Cada pedido faz login novo.
* Pop-ups do caminho (cookies, localização, avisos) são sempre fechados/recusados:
  o contexto nasce sem permissão de localização e o banner de cookies é dispensado.
* Antes de emitir, confere na tela "Empregador: <CNPJ do pedido>". Depois de baixar,
  confere a raiz do CNPJ no texto do PDF. Qualquer divergência = erro, nada é servido.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

URL_SERVICOS = 'https://fgtsdigital.sistema.gov.br/portal/servicos'
URL_PERFIL = 'https://fgtsdigital.sistema.gov.br/portal/escolhaPerfil'
URL_GUIA_RAPIDA = 'https://fgtsdigital.sistema.gov.br/cobranca/#/gestao-guias/emissao-guia-rapida'
URL_GUIA_PARAM = 'https://fgtsdigital.sistema.gov.br/cobranca/#/gestao-guias/emissao-guia-parametrizada'
MESES_PT = {'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3, 'abril': 4, 'maio': 5, 'junho': 6,
            'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12}
RE_QTD_DEBITOS = re.compile(r'Quantidade de\s*D[ée]bitos de FGTS\s*(\d+)', re.I)
RE_TOTAL_ITENS = re.compile(r'\bde\s+(\d+)\s+itens', re.I)
ORIGEM_CERTIFICADO = 'https://certificado.sso.acesso.gov.br'

RE_COMP = re.compile(r'\b(0[1-9]|1[0-2])/(20\d{2})\b')
RE_TOTAL = re.compile(r'Total\s+Devedor\s*R\$\s*([\d.]+,\d{2})', re.I)
RE_GRUPO = re.compile(r'([A-Za-zÀ-ú ]+?)\s*-\s*Vencimento da Guia:\s*(\d{2}/\d{2}/\d{4})')
RE_IDENT = re.compile(r'\b(\d{16}-\d)\b')
_V = r'(\d{1,3}(?:\.\d{3})*,\d{2})'
# Linha "Total" da tabela de cada grupo: FGTS Mensal, FGTS Rescisório, Indenização
# Compensatória, FGTS Processo Trabalhista, Encargos FGTS, Total (ordem da tela).
RE_LINHA_TOTAL = re.compile(r'\bTotal\s+' + r'\s+'.join([_V] * 6))
COLUNAS = ('fgts_mensal', 'fgts_rescisorio', 'indenizacao', 'processo_trabalhista', 'encargos', 'total')
SEM_PROCURACAO = 'Não existe procuração'


class FalhaRobo(Exception):
    """Erro com mensagem pronta para o cliente."""


def _int_env(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, str(padrao)))
    except ValueError:
        return padrao


def fmt_cnpj(cnpj: str) -> str:
    return f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}'


def comp_para_aaaamm(texto: str) -> Optional[str]:
    m = RE_COMP.search(texto or '')
    return f'{m.group(2)}{m.group(1)}' if m else None


def aaaamm_para_comp(aaaamm: str) -> str:
    return f'{aaaamm[4:]}/{aaaamm[:4]}'


def valor_br(texto: Optional[str]) -> Optional[float]:
    if not texto:
        return None
    try:
        return float(texto.replace('.', '').replace(',', '.'))
    except ValueError:
        return None


def _sem_query(url: str) -> str:
    return (url or '').split('?')[0].split('#')[0]


def _campos_form(corpo: Optional[bytes]) -> str:
    """Nomes dos campos de um POST com o TAMANHO de cada valor — nunca o conteúdo
    (o token do captcha e códigos de sessão não vão para o log)."""
    if not corpo:
        return '-'
    from urllib.parse import parse_qsl
    try:
        pares = parse_qsl(corpo.decode('utf-8', 'replace'), keep_blank_values=True)
    except Exception:
        return f'{len(corpo)} bytes'
    return ', '.join(f'{k}[{len(v)}]' for k, v in pares) or f'{len(corpo)} bytes'


def _destino(url: str) -> str:
    """Para onde um redirecionamento manda: host+caminho e só os NOMES da query
    (o gov.br põe o código do erro ali)."""
    if not url:
        return '-'
    from urllib.parse import parse_qsl, urlsplit
    u = urlsplit(url)
    nomes = [k for k, _ in parse_qsl(u.query, keep_blank_values=True)]
    return _sem_query(url) + (f' ?{",".join(nomes)}' if nomes else '')


def db_commit() -> None:
    from app.extensions import db
    db.session.commit()


def _msg_guias(guias: List[Dict[str, Any]]) -> str:
    total = sum(g.get('valor') or 0 for g in guias)
    if not total:
        return f'{len(guias)} guia(s) emitida(s).'
    valor = f'{total:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'{len(guias)} guia(s) emitida(s): R$ {valor}'


def pfx_para_pem(pfx: bytes, senha: str) -> Dict[str, bytes]:
    """Certificado A1 → {'cert': PEM (com a cadeia), 'key': PEM}, só em memória.

    O .pfx das ACs brasileiras costuma vir com criptografia LEGADA (RC2/3DES); o motor
    do Playwright (Node/OpenSSL 3) recusa: "Unsupported TLS certificate... deprecated by
    OpenSSL" (visto na VPS em 23/10/2026). A biblioteca `cryptography` lê esse formato,
    então convertemos aqui. Nada é gravado em disco; o arquivo do volume não muda.
    """
    from cryptography.hazmat.primitives.serialization import (Encoding, NoEncryption,
                                                              PrivateFormat, pkcs12)
    try:
        chave, cert, cadeia = pkcs12.load_key_and_certificates(pfx, (senha or '').encode())
    except ValueError:
        raise FalhaRobo('Não consegui abrir o certificado do escritório (senha?). Fale com o escritório.')
    if chave is None or cert is None:
        raise FalhaRobo('O certificado do escritório está incompleto. Fale com o escritório.')
    cert_pem = cert.public_bytes(Encoding.PEM) + b''.join(c.public_bytes(Encoding.PEM) for c in (cadeia or []))
    key_pem = chave.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return {'cert': cert_pem, 'key': key_pem}


class RoboFgts:
    def __init__(self, app):
        self.app = app
        self.fila: 'queue.Queue[int]' = queue.Queue()
        self._cliques: 'queue.Queue[Tuple[int, float, float]]' = queue.Queue()
        self._escolhas: 'queue.Queue[Tuple[int, List[str], Optional[str]]]' = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Estado do captcha (lido pelos threads HTTP; escrito só pelo thread do robô).
        self.captcha_pedido: Optional[int] = None
        self.captcha_jpeg: Optional[bytes] = None
        self.captcha_em: float = 0.0
        # Playwright (só o thread do robô toca).
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._logado = False
        self._perfil_cnpj: Optional[str] = None
        self._pfx: Optional[bytes] = None
        self._senha: Optional[str] = None

    # ------------------------------------------------------------------ API pública
    def enfileirar(self, pedido_id: int) -> None:
        self._garantir_thread()
        self.fila.put(pedido_id)

    def captcha_imagem(self, pedido_id: int) -> Optional[bytes]:
        if self.captcha_pedido != pedido_id:
            return None
        return self.captcha_jpeg

    def clicar_captcha(self, pedido_id: int, fx: float, fy: float) -> bool:
        """Toque do cliente na imagem (fração 0..1 da largura/altura do recorte)."""
        if self.captcha_pedido != pedido_id:
            return False
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            return False
        self._cliques.put((pedido_id, fx, fy))
        return True

    def escolher(self, pedido_id: int, competencias: List[str], data_pagamento: Optional[str] = None) -> None:
        """Meses (e a data de pagamento, AAAA-MM-DD) escolhidos — já validados pela rota."""
        self._escolhas.put((pedido_id, competencias, data_pagamento))

    def estado(self) -> Dict[str, Any]:
        return {
            'thread_viva': bool(self._thread and self._thread.is_alive()),
            'navegador_aberto': self._browser is not None,
            'logado': self._logado,
            'perfil_cnpj': self._perfil_cnpj,
            'fila': self.fila.qsize(),
            'captcha_pedido': self.captcha_pedido,
        }

    # ------------------------------------------------------------------ thread
    def _garantir_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name='robo-fgts', daemon=True)
            self._thread.start()
            self._retomar_pendentes()

    def _retomar_pendentes(self) -> None:
        """Pedido que estava andando quando o container caiu volta para a fila."""
        try:
            with self.app.app_context():
                from app.extensions import db
                from app.integracao_models import FgtsPedido
                for p in FgtsPedido.query.filter(FgtsPedido.status.in_(('executando', 'aguardando_captcha', 'aguardando_escolha'))).all():
                    p.status, p.etapa = 'fila', 'fila'
                db.session.commit()
                for p in FgtsPedido.query.filter_by(status='fila').order_by(FgtsPedido.id).all():
                    self.fila.put(p.id)
        except Exception:
            logger.exception('FGTS: falha ao retomar pedidos pendentes')

    def _loop(self) -> None:
        while True:
            pedido_id = self.fila.get()
            with self.app.app_context():
                try:
                    self._processar(pedido_id)
                except Exception:
                    logger.exception('FGTS: pedido %s quebrou', pedido_id)
                finally:
                    self._fechar()          # SEMPRE fecha (regra do Jean)
                    from app.extensions import db
                    db.session.remove()

    # ------------------------------------------------------------------ pedido
    def _processar(self, pedido_id: int) -> None:
        from app.extensions import db
        from app.integracao_models import FgtsPedido
        from app.services import fgts_pedidos_service as svc

        pedido = db.session.get(FgtsPedido, pedido_id)
        if not pedido or pedido.status != 'fila':
            return
        svc.atualizar(pedido, status='executando', etapa='login')
        extras: Dict[str, Any] = {}
        try:
            self._garantir_login(pedido, extras)
            svc.atualizar(pedido, status='executando', etapa='perfil')
            self._definir_perfil(pedido.cnpj)
            if pedido.acao == 'consultar':
                svc.atualizar(pedido, etapa='consultando')
                competencias = self._consultar(pedido.cnpj)
                extras['competencias'] = competencias
                if competencias:
                    # Dias que o FGTS Digital aceita como vencimento (Guia Parametrizada).
                    extras['datas_permitidas'] = self._datas_permitidas(pedido.cnpj, competencias[0]['competencia'])
                svc.gravar_resultado(pedido, extras)
                if not competencias:
                    svc.atualizar(pedido, status='concluido', etapa='concluido',
                                  mensagem='Nenhum débito de FGTS em aberto no FGTS Digital.')
                    return
                # Mesma sessão: espera o cliente escolher (um login, no máximo um captcha).
                escolha, data = self._esperar_escolha(pedido)
                if not escolha:
                    svc.atualizar(pedido, status='concluido', etapa='concluido',
                                  mensagem=f'{len(competencias)} competência(s) com FGTS em aberto.')
                    return
                pedido.competencia = escolha[0]
                extras['escolha'] = escolha
                extras['data_pagamento'] = data
                svc.atualizar(pedido, status='executando', etapa='emitindo', mensagem='')
                self._emitir_varias(pedido, escolha, extras, data)
            else:
                svc.atualizar(pedido, etapa='emitindo')
                anterior = svc.resultado(pedido)
                escolha = anterior.get('escolha') or [pedido.competencia]
                data = anterior.get('data_pagamento')
                extras['escolha'] = escolha
                extras['data_pagamento'] = data
                self._emitir_varias(pedido, escolha, extras, data)
        except FalhaRobo as exc:
            svc.gravar_resultado(pedido, extras)
            svc.atualizar(pedido, status='erro', mensagem=str(exc))
        except Exception as exc:
            logger.exception('FGTS: erro no pedido %s', pedido_id)
            svc.gravar_resultado(pedido, extras)
            svc.atualizar(pedido, status='erro',
                          mensagem='O FGTS Digital não respondeu como esperado. Tente de novo em alguns minutos.')
            self._salvar_print(f'erro_{pedido_id}')
        finally:
            self.captcha_pedido = None
            self.captcha_jpeg = None

    def _emitir_varias(self, pedido, escolha: List[str], extras: Dict[str, Any],
                       data: Optional[str] = None) -> None:
        """Uma GFD por mês escolhido. Mês que falhar não derruba os outros: a guia dos
        demais sai e a falha aparece por mês na mensagem."""
        from app.services import fgts_pedidos_service as svc
        guias: List[Dict[str, Any]] = []
        falhas: List[str] = []
        extras['emitiu'] = True
        for aaaamm in escolha:
            try:
                for g in self._emitir(pedido.cnpj, aaaamm, data):
                    g['competencia'] = aaaamm
                    guias.append(g)
            except FalhaRobo as exc:
                falhas.append(f'{aaaamm_para_comp(aaaamm)}: {exc}')
            extras['guias'] = guias
            svc.gravar_resultado(pedido, extras)      # PDF já pronto aparece na hora
            db_commit()
        if not guias:
            raise FalhaRobo(' '.join(falhas) or 'Nenhuma guia foi emitida.')
        msg = _msg_guias(guias)
        if falhas:
            msg += ' Não saiu: ' + '; '.join(falhas)
        svc.atualizar(pedido, status='concluido', etapa='concluido', mensagem=msg)

    def _esperar_escolha(self, pedido) -> Tuple[List[str], Optional[str]]:
        from app.services import fgts_pedidos_service as svc
        svc.atualizar(pedido, status='aguardando_escolha', etapa='consultando',
                      mensagem='Escolha a(s) competência(s) para gerar a guia.')
        limite = time.time() + _int_env('FGTS_ESCOLHA_TIMEOUT_S', 240)
        while time.time() < limite:
            try:
                pid, lista, data = self._escolhas.get(timeout=1)
            except queue.Empty:
                continue
            if pid == pedido.id and lista:
                return lista, data
        return [], None

    # ------------------------------------------------------------------ navegador
    def _certificado(self) -> Tuple[bytes, str]:
        from app.models import AppSetting
        from app.extensions import db
        from app.services import certificado
        caminho = os.getenv('FGTS_CERTIFICADO_PATH')
        senha = os.getenv('FGTS_CERTIFICADO_SENHA')
        if not caminho or not senha:
            s = db.session.query(AppSetting).first()
            if not s:
                raise FalhaRobo('Certificado do escritório não configurado. Fale com o escritório.')
            caminho = caminho or s.certificado_path
            senha = senha or (s.certificado_password or '').strip()
        try:
            return certificado.carregar(caminho), senha
        except ValueError as exc:
            logger.error('FGTS: %s', exc)
            raise FalhaRobo('Certificado do escritório não encontrado. Fale com o escritório.')

    def _abrir(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        pfx, senha = self._certificado()
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=os.getenv('FGTS_HEADLESS', '1') != '0',
            executable_path=os.getenv('FGTS_CHROMIUM_PATH') or None,
            args=['--disable-dev-shm-usage', '--no-sandbox'])
        if pfx:
            pfx_para_pem(pfx, senha)      # valida senha/certificado cedo, com mensagem clara
        self._pfx, self._senha = pfx, senha
        self._context = self._browser.new_context(
            locale='pt-BR', timezone_id='America/Sao_Paulo',
            viewport={'width': 1366, 'height': 900},
            accept_downloads=True,
            user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'),
            permissions=[],
        )
        self._context.set_default_timeout(30_000)
        # O certificado NÃO vai pelo navegador: em 23/10/2026, na VPS, o POST para
        # certificado.sso.acesso.gov.br ficava pendurado para sempre com o
        # `client_certificates` do Playwright (botão "carregando", sem resposta nem erro).
        # Esse host é atendido aqui, em Python, com o mesmo requests_pkcs12 que a
        # integração SERPRO já usa com este .pfx; a resposta volta ao navegador.
        self._ao_criar_contexto(self._context)
        if pfx:   # registrada por último = tem prioridade sobre outras rotas
            self._context.route(re.compile(r'^https://certificado\.sso\.acesso\.gov\.br/'), self._rota_certificado)
        self._page = self._context.new_page()
        # Diálogo JS (alert/confirm) nunca trava o robô: fecha.
        self._page.on('dialog', lambda d: d.dismiss())
        # Diagnóstico no log do container (EasyPanel): por onde o login passou e o que
        # falhou na rede. Só host+caminho — a query do gov.br leva códigos de sessão.
        self._page.on('framenavigated', lambda f: f == self._page.main_frame and logger.warning(
            'FGTS nav: %s', _sem_query(f.url)))
        self._page.on('requestfailed', lambda r: 'gov.br' in r.url and logger.warning(
            'FGTS falhou: %s %s', _sem_query(r.url), r.failure))
        # POSTs ao gov.br: só nomes de campo e tamanhos (mostra se o token do captcha saiu).
        self._page.on('request', lambda r: r.method == 'POST' and 'acesso.gov.br' in r.url
                      and 'certificado.sso' not in r.url and logger.warning(
                          'FGTS post: %s | %s', _sem_query(r.url), _campos_form(r.post_data_buffer)))
        self._page.on('response', lambda r: 'certificado.sso' in r.url and logger.warning(
            'FGTS certificado: %s %s', r.status, _sem_query(r.url)))
        self._logado = False
        self._perfil_cnpj = None

    def _chamar_certificado(self, metodo: str, url: str, headers: Dict[str, str], corpo: Optional[bytes]):
        """Uma chamada a certificado.sso com o A1 (TLS cliente) — sem seguir redirecionamento."""
        import requests_pkcs12
        return requests_pkcs12.request(metodo, url, headers=headers, data=corpo, allow_redirects=False,
                                       pkcs12_data=self._pfx, pkcs12_password=self._senha, timeout=40)

    def _rota_certificado(self, route) -> None:
        req = route.request
        try:
            headers = {k: v for k, v in req.all_headers().items()
                       if not k.startswith(':') and k.lower() not in ('host', 'content-length', 'connection')}
            resp = self._chamar_certificado(req.method, req.url, headers, req.post_data_buffer)
        except Exception as exc:
            logger.warning('FGTS certificado (python) falhou: %s %s', _sem_query(req.url), exc)
            route.abort('failed')
            return
        saida: Dict[str, str] = {}
        for k, v in resp.raw.headers.items():
            kl = k.lower()
            if kl in ('content-encoding', 'content-length', 'transfer-encoding', 'connection'):
                continue          # o corpo já vem decodificado pelo requests
            saida[kl] = f'{saida[kl]}\n{v}' if kl in saida else v   # vários Set-Cookie: um por linha
        logger.warning('FGTS certificado (python): %s %s -> %s | enviado: %s | volta: %s',
                       req.method, _sem_query(req.url), resp.status_code,
                       _campos_form(req.post_data_buffer), _destino(saida.get('location', '')))
        route.fulfill(status=resp.status_code, headers=saida, body=resp.content)

    def _ao_criar_contexto(self, context) -> None:
        """Gancho para os testes (site falso via `context.route`). Sem efeito em produção."""

    def _fechar(self) -> None:
        for obj in (self._context, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None
        self._pfx = self._senha = None
        self._logado = False
        self._perfil_cnpj = None

    def _salvar_print(self, nome: str) -> None:
        """Print da tela no volume para diagnóstico (nunca vai para o cliente)."""
        try:
            if self._page:
                from app.config import DATA_DIR
                pasta = os.path.join(str(DATA_DIR), 'fgts', '_diagnostico')
                os.makedirs(pasta, exist_ok=True)
                self._page.screenshot(path=os.path.join(
                    pasta, f'{datetime.now():%Y%m%d_%H%M%S}_{nome}.png'), full_page=True)
        except Exception:
            pass

    # ------------------------------------------------------------------ login
    def _no_portal(self) -> bool:
        url = self._page.url
        return 'fgtsdigital.sistema.gov.br' in url and '/portal/login' not in url and 'acessogov' not in url

    def _garantir_login(self, pedido, extras: Dict[str, Any]) -> None:
        self._abrir()
        page = self._page
        page.goto(URL_SERVICOS, wait_until='domcontentloaded')
        self._esperar_assentar()
        if self._no_portal():
            self._logado = True
            return
        self._logado = False
        self._perfil_cnpj = None
        # portal/login → gov.br
        if '/portal/login' in page.url:
            page.get_by_role('button', name=re.compile('Entrar com GOV.BR', re.I)).click()
            page.wait_for_url(re.compile(r'sso\.acesso\.gov\.br|fgtsdigital'), timeout=30_000)
        limite = time.time() + _int_env('FGTS_LOGIN_TIMEOUT_S', 60)
        ultimo_clique = 0.0          # 0 = ainda não clicou em "Seu certificado digital"
        rodadas = 0
        while time.time() < limite:
            if self._no_portal():
                self._logado = True
                return
            if self._captcha_visivel():
                extras['teve_captcha'] = True
                rodadas += 1
                self._esperar_captcha(pedido)
                # O desafio sumiu: ou passou (o gov.br segue para o certificado) ou
                # EXPIROU (o hCaptcha fecha sozinho depois de ~2 min sem resposta e a
                # tela volta ao login — visto na VPS em 23/10/2026). No 2º caso o laço
                # abaixo clica de novo em "Seu certificado digital" e abre outro desafio.
                ultimo_clique = time.time()
                limite = time.time() + _int_env('FGTS_LOGIN_TIMEOUT_S', 60)
                continue
            parado_no_login = 'sso.acesso.gov.br/login' in page.url
            if parado_no_login and (not ultimo_clique or time.time() - ultimo_clique > 10):
                if rodadas:
                    # Voltou ao login depois de um desafio: guarda o que o gov.br mostra.
                    self._diagnosticar(pedido, extras, f'login_volta_{rodadas}')
                if rodadas >= _int_env('FGTS_CAPTCHA_RODADAS', 3):
                    raise FalhaRobo('O gov.br voltou para a tela de login depois da verificação '
                                    f'({rodadas}x). Veja o diagnóstico abaixo; peça de novo.')
                alvo = page.get_by_text('Seu certificado digital', exact=True)
                if alvo.count():
                    alvo.first.click()
                    ultimo_clique = time.time()
            page.wait_for_timeout(800)
        self._diagnosticar(pedido, extras, 'login_timeout')
        raise FalhaRobo('Não foi possível entrar no gov.br agora. Tente de novo em alguns minutos.')

    def _diagnosticar(self, pedido, extras: Dict[str, Any], nome: str) -> None:
        """Print no volume + URL e o começo do texto visível no log e no pedido (o painel
        do escritório mostra). Nunca leva query string nem campos de formulário."""
        try:
            url = _sem_query(self._page.url)
            texto = ' '.join(self._texto().split())[:400]
        except Exception:
            url, texto = '?', ''
        logger.warning('FGTS diagnóstico %s pedido=%s url=%s texto=%s', nome, pedido.id, url, texto)
        extras['diagnostico'] = {'etapa': nome, 'url': url, 'texto': texto,
                                 'quando': datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
        self._salvar_print(f'{nome}_{pedido.id}')

    def _esperar_assentar(self) -> None:
        try:
            self._page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        self._fechar_avisos()

    def _fechar_avisos(self) -> None:
        """Banner de cookies do FGTS Digital: recusa o não essencial quando houver a opção;
        senão só some com o banner (ele não bloqueia nada, mas cobre botões)."""
        page = self._page
        for nome in ('Rejeitar', 'Recusar', 'Rejeitar cookies'):
            try:
                b = page.get_by_role('button', name=nome)
                if b.count() and b.first.is_visible():
                    b.first.click()
                    return
            except Exception:
                continue
        try:
            page.evaluate("""() => { for (const el of document.querySelectorAll('.br-cookiebar, [class*=cookie]')) {
                if (/cookies/i.test(el.innerText || '') && el.offsetHeight < 400) el.style.display = 'none'; } }""")
        except Exception:
            pass

    # ------------------------------------------------------------------ captcha
    def _caixas_captcha(self) -> List[Dict[str, float]]:
        caixas = []
        for f in self._page.locator('iframe[src*="hcaptcha"]').all():
            try:
                if not f.is_visible():
                    continue
                b = f.bounding_box()
                if b and b['width'] > 60 and b['height'] > 60:
                    caixas.append(b)
            except Exception:
                continue
        return caixas

    def _captcha_visivel(self) -> bool:
        # O widget invisível do hCaptcha tem iframe 0x0; o desafio de imagens abre um
        # iframe grande (≈400x580). Só esse conta.
        return any(c['height'] > 200 for c in self._caixas_captcha())

    def _recorte(self) -> Optional[Dict[str, float]]:
        caixas = [c for c in self._caixas_captcha() if c['height'] > 200]
        if not caixas:
            return None
        c = max(caixas, key=lambda b: b['width'] * b['height'])
        vp = self._page.viewport_size or {'width': 1366, 'height': 900}
        x = max(0, c['x'] - 4)
        y = max(0, c['y'] - 4)
        w = min(vp['width'] - x, c['width'] + 8)
        h = min(vp['height'] - y, c['height'] + 8)
        return {'x': x, 'y': y, 'width': w, 'height': h}

    def _esperar_captcha(self, pedido) -> None:
        from app.services import fgts_pedidos_service as svc
        svc.atualizar(pedido, status='aguardando_captcha', etapa='captcha',
                      mensagem='Toque nas imagens pedidas e depois em Verificar/Avançar.')
        self.captcha_pedido = pedido.id
        limite = time.time() + _int_env('FGTS_CAPTCHA_TIMEOUT_S', 300)
        try:
            while time.time() < limite:
                if not self._captcha_visivel():
                    break
                caixa = self._recorte()
                if caixa:
                    try:
                        self.captcha_jpeg = self._page.screenshot(clip=caixa, type='jpeg', quality=80)
                        self.captcha_em = time.time()
                    except Exception:
                        pass
                # Toques do cliente (só os deste pedido).
                try:
                    while True:
                        pid, fx, fy = self._cliques.get_nowait()
                        if pid == pedido.id and caixa:
                            x = caixa['x'] + fx * caixa['width']
                            y = caixa['y'] + fy * caixa['height']
                            # Move antes de clicar: depois do print recortado o Chromium só
                            # entrega o clique ao iframe do desafio após um movimento do
                            # mouse (e o hCaptcha também espera trajetória, como uma pessoa).
                            self._page.mouse.move(x, y, steps=8)
                            self._page.wait_for_timeout(120)
                            self._page.mouse.click(x, y)
                            self._page.wait_for_timeout(250)
                except queue.Empty:
                    pass
                self._page.wait_for_timeout(500)
            else:
                raise FalhaRobo('A verificação de segurança não foi concluída a tempo. Peça de novo.')
        finally:
            self.captcha_pedido = None
            self.captcha_jpeg = None
        svc.atualizar(pedido, status='executando', etapa='login', mensagem='')

    # ------------------------------------------------------------------ perfil
    def _texto(self) -> str:
        try:
            return self._page.locator('body').inner_text(timeout=10_000)
        except Exception:
            return ''

    def _empregador_na_tela(self, cnpj: str) -> bool:
        return f'Empregador: {fmt_cnpj(cnpj)}' in self._texto().replace('\xa0', ' ')

    def _definir_perfil(self, cnpj: str) -> None:
        page = self._page
        if self._perfil_cnpj == cnpj:
            page.goto(URL_SERVICOS, wait_until='domcontentloaded')
            self._esperar_assentar()
            if self._empregador_na_tela(cnpj):
                return
        page.goto(URL_PERFIL, wait_until='domcontentloaded')
        self._esperar_assentar()
        # Modal "Definir Perfil" (Angular, role=dialog). Abre sozinho em escolhaPerfil;
        # se não abrir, o botão "Trocar Perfil" do cabeçalho abre.
        dialogo = page.locator('[role=dialog]').filter(has_text='Definir Perfil')
        if not dialogo.count() or not dialogo.first.is_visible():
            page.get_by_role('button', name='Trocar Perfil').first.click()
        dialogo = page.locator('[role=dialog]').filter(has_text='Definir Perfil').first
        dialogo.wait_for()
        # Lista "Perfil" (ng-select) → opção "Procurador".
        dialogo.locator('ng-select').first.click()
        page.locator('.ng-option').filter(has_text=re.compile(r'^\s*Procurador\s*$')).first.click()
        campo_ni = dialogo.get_by_placeholder(re.compile('CNPJ ou CPF', re.I))
        campo_ni.wait_for()
        campo_ni.fill('')
        campo_ni.press_sequentially(cnpj, delay=30)
        dialogo.get_by_role('button', name='Definir').click()
        # Sucesso = o modal fecha; sem procuração = mensagem de erro dentro do modal.
        limite = time.time() + 25
        while time.time() < limite:
            texto = self._texto()
            if SEM_PROCURACAO in texto:
                raise FalhaRobo('O escritório ainda não tem procuração no FGTS Digital para esta empresa. '
                                'Fale com o escritório.')
            if not page.locator('[role=dialog]').filter(has_text='Definir Perfil').count():
                break
            page.wait_for_timeout(500)
        else:
            self._salvar_print('perfil')
            raise FalhaRobo('Não foi possível abrir a empresa no FGTS Digital agora.')
        page.goto(URL_SERVICOS, wait_until='domcontentloaded')
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            self._salvar_print('perfil_divergente')
            raise FalhaRobo('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        self._perfil_cnpj = cnpj

    # ------------------------------------------------------------------ guia rápida
    def _abrir_guia_rapida(self, cnpj: str) -> None:
        page = self._page
        page.goto(URL_GUIA_RAPIDA, wait_until='domcontentloaded')
        page.get_by_text('Emissão de Guia Rápida').first.wait_for()
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            raise FalhaRobo('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')

    def _campo_competencia(self):
        # ng-select logo depois do rótulo "Competência de Apuração" (mapeado 23/10/2026).
        rotulo = self._page.locator('label').filter(has_text='Competência de Apuração').first
        return rotulo.locator('xpath=following::ng-select[1]')

    def _listar_competencias(self) -> List[str]:
        campo = self._campo_competencia()
        campo.click()
        self._page.locator('.ng-option').first.wait_for()
        textos = self._page.locator('.ng-option').all_inner_texts()
        vistas: List[str] = []
        for t in textos:
            for m in RE_COMP.finditer(t):
                aaaamm = f'{m.group(2)}{m.group(1)}'
                if aaaamm not in vistas:
                    vistas.append(aaaamm)
        self._page.keyboard.press('Escape')
        return vistas

    def _pesquisar(self, aaaamm: str) -> str:
        page = self._page
        campo = self._campo_competencia()
        campo.click()
        page.wait_for_timeout(300)
        alvo = page.locator('.ng-option').filter(
            has_text=re.compile(r'^\s*' + re.escape(aaaamm_para_comp(aaaamm)) + r'\s*$'))
        if not alvo.count():
            page.keyboard.press('Escape')
            raise FalhaRobo(f'A competência {aaaamm_para_comp(aaaamm)} não está mais em aberto no FGTS Digital.')
        alvo.first.click()
        page.wait_for_timeout(300)
        with page.expect_response(lambda r: '/guia-rapida/v2/' + aaaamm in r.url, timeout=30_000):
            page.get_by_role('button', name='Pesquisar').click()
        page.get_by_text('Total Devedor').first.wait_for(timeout=30_000)
        self._esperar_assentar()
        return self._texto()

    def _consultar(self, cnpj: str) -> List[Dict[str, Any]]:
        self._abrir_guia_rapida(cnpj)
        comps = self._listar_competencias()
        teto = _int_env('FGTS_MAX_COMPETENCIAS', 0)      # 0 = todas (pedido do Jean)
        if teto > 0:
            comps = comps[:teto]
        return [self._detalhar(aaaamm, self._pesquisar(aaaamm)) for aaaamm in comps]

    @staticmethod
    def _detalhar(aaaamm: str, texto: str) -> Dict[str, Any]:
        """Valores da competência como a tela mostra: total devedor, um item por grupo
        (Mensal, Rescisório…) com vencimento da guia, e a composição somada."""
        texto = texto.replace('\xa0', ' ')
        total = RE_TOTAL.search(texto)
        grupos = [{'grupo': g.strip(), 'vencimento': v} for g, v in RE_GRUPO.findall(texto)]
        linhas = RE_LINHA_TOTAL.findall(texto)
        composicao = {c: 0.0 for c in COLUNAS}
        for i, linha in enumerate(linhas):
            valores = dict(zip(COLUNAS, (valor_br(v) or 0.0 for v in linha)))
            if i < len(grupos):
                grupos[i]['valor'] = valores['total']
            for c in COLUNAS:
                composicao[c] = round(composicao[c] + valores[c], 2)
        return {'competencia': aaaamm, 'rotulo': aaaamm_para_comp(aaaamm),
                'total': valor_br(total.group(1)) if total else (composicao['total'] or None),
                'grupos': grupos, 'composicao': composicao if linhas else None}

    def _emitir(self, cnpj: str, aaaamm: str, data: Optional[str] = None) -> List[Dict[str, Any]]:
        """Data de pagamento = hoje (ou vazia) → Guia Rápida. Outra data → Guia Parametrizada."""
        from app.services.pendencias_service import hoje_brasil
        if data and data != hoje_brasil().isoformat():
            return self._emitir_parametrizada(cnpj, aaaamm, data)
        return self._emitir_rapida(cnpj, aaaamm)

    def _emitir_rapida(self, cnpj: str, aaaamm: str) -> List[Dict[str, Any]]:
        from app.services import fgts_pedidos_service as svc
        page = self._page
        self._abrir_guia_rapida(cnpj)
        texto = self._pesquisar(aaaamm)
        if not self._empregador_na_tela(cnpj):
            raise FalhaRobo('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        grupos = RE_GRUPO.findall(texto)
        botoes = page.get_by_role('button', name=re.compile(r'^\s*Emitir guia\s*$', re.I))
        n = botoes.count()
        if not n:
            raise FalhaRobo('O FGTS Digital não ofereceu guia para esta competência (pode já ter sido paga).')
        guias = []
        pasta = svc.pasta_pdfs(cnpj)
        for i in range(n):
            botao = page.get_by_role('button', name=re.compile(r'^\s*Emitir guia\s*$', re.I)).first
            with page.expect_download(timeout=60_000) as baixa:
                botao.click()
                self._confirmar_modal_se_houver()
            download = baixa.value
            nome = f'{aaaamm}_{i}_{datetime.now():%Y%m%d%H%M%S}.pdf'
            destino = pasta / nome
            download.save_as(str(destino))
            conteudo = destino.read_bytes()
            if not conteudo.startswith(b'%PDF') or not svc.pdf_e_da_empresa(conteudo, cnpj):
                destino.unlink(missing_ok=True)
                raise FalhaRobo('A guia baixada não confere com a empresa. Nada foi enviado; fale com o escritório.')
            grupo, venc = grupos[i] if i < len(grupos) else ('FGTS', None)
            guias.append({'grupo': grupo.strip(), 'vencimento': venc, 'arquivo': nome,
                          'identificador': None, 'valor': None})
            page.wait_for_timeout(800)
        texto = self._texto()
        idents = RE_IDENT.findall(texto)
        detalhe = self._detalhar(aaaamm, texto)
        for i, g in enumerate(guias):
            g['identificador'] = idents[i] if i < len(idents) else None
            if i < len(detalhe['grupos']) and detalhe['grupos'][i].get('valor') is not None:
                g['valor'] = detalhe['grupos'][i]['valor']
        if len(guias) == 1 and guias[0]['valor'] is None:
            guias[0]['valor'] = detalhe['total']
        return guias

    # ------------------------------------------------------- guia parametrizada
    # Mapeada na tela em 23/10/2026: assistente em 4 passos — 1 Selecionar Débitos FGTS
    # (filtro Competência Inicial/Final, tabela por trabalhador com "#selecionar-todos",
    # 5 por página, "#btn-next-page", botão "Adicionar à guia" → resumo), 2 Consignado,
    # 3 Definir Vencimento (campo "Vencimento da Guia" dd/mm/aaaa + calendário
    # bs-datepicker: dia liberado = span[bsdatepickerdaydecorator] sem .disabled;
    # data fora do limite é RECUSADA EM SILÊNCIO — o campo volta ao valor anterior),
    # 4 Emitir Guia ("Vencimento da Guia: dd/mm/aaaa", tabela igual à da Guia Rápida).
    def _visivel(self, nome: str):
        return self._page.get_by_role('button', name=nome, exact=True).filter(visible=True)

    def _ng_apos_rotulo(self, rotulo: str, valor: str) -> None:
        page = self._page
        campo = page.locator('label').filter(has_text=re.compile(r'^\s*' + rotulo + r'\b')).first \
            .locator('xpath=following::ng-select[1]')
        campo.click()
        alvo = page.locator('.ng-option').filter(has_text=re.compile(r'^\s*' + re.escape(valor) + r'\s*$'))
        if not alvo.count():
            page.keyboard.press('Escape')
            raise FalhaRobo(f'A competência {valor} não está mais em aberto no FGTS Digital.')
        alvo.first.click()
        page.wait_for_timeout(300)

    def _param_selecionar(self, cnpj: str, aaaamm: str, todos: bool = True) -> None:
        """Passo 1: filtra a competência e põe TODOS os débitos dela no resumo da guia."""
        page = self._page
        page.goto(URL_GUIA_PARAM, wait_until='domcontentloaded')
        page.get_by_text('Emissão de Guia Parametrizada').first.wait_for()
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            raise FalhaRobo('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        comp = aaaamm_para_comp(aaaamm)
        self._ng_apos_rotulo('Inicial', comp)
        self._ng_apos_rotulo('Final', comp)
        with page.expect_response(lambda r: '/guia-parametrizada/debitos?' in r.url, timeout=30_000):
            page.get_by_role('button', name='Pesquisar', exact=True).click()
        self._esperar_assentar()
        if not page.locator('#selecionar-todos').count():
            raise FalhaRobo(f'O FGTS Digital não listou débitos de {comp} na guia parametrizada.')
        m = RE_TOTAL_ITENS.search(self._texto())
        total = int(m.group(1)) if m else None
        for _ in range(50):   # páginas de 5
            page.locator('label[for="selecionar-todos"]').first.click()
            page.wait_for_timeout(300)
            self._visivel('Adicionar à guia').first.click()
            page.wait_for_timeout(600)
            if not todos:
                break
            prox = page.locator('#btn-next-page')
            if not prox.count() or not prox.first.is_enabled():
                break
            prox.first.click()
            self._esperar_assentar()
        if todos and total is not None:
            q = RE_QTD_DEBITOS.search(self._texto())
            if not q or int(q.group(1)) != total:
                self._salvar_print('param_resumo')
                raise FalhaRobo(f'Não consegui pôr todos os débitos de {comp} na guia. Nada foi emitido.')

    def _param_ir_vencimento(self) -> None:
        """Passos 2 e 3: Avançar (consignado) → Avançar → tela Definir Vencimento."""
        page = self._page
        self._visivel('Avançar').first.click()
        page.wait_for_timeout(1200)
        self._visivel('Avançar').first.click()
        self._campo_vencimento().wait_for()
        self._esperar_assentar()

    def _campo_vencimento(self):
        return self._page.locator('label').filter(has_text='Vencimento da Guia').first \
            .locator('xpath=following::input[1]')

    def _datas_permitidas(self, cnpj: str, aaaamm: str) -> List[str]:
        """Dias liberados no calendário do vencimento (AAAA-MM-DD). Só lê; não emite.
        Falhou a leitura → lista vazia (a página oferece só "hoje", pela Guia Rápida)."""
        page = self._page
        try:
            self._param_selecionar(cnpj, aaaamm, todos=False)
            self._param_ir_vencimento()
            page.locator('.fa-calendar-alt').filter(visible=True).first.click()
            page.locator('bs-datepicker-container, .bs-datepicker').first.wait_for()
            datas: List[str] = []
            for _ in range(3):
                cab = page.locator('.bs-datepicker-head button').all_inner_texts()
                mes = next((MESES_PT[t.strip().lower()] for t in cab if t.strip().lower() in MESES_PT), None)
                ano = next((int(t.strip()) for t in cab if t.strip().isdigit() and len(t.strip()) == 4), None)
                if not mes or not ano:
                    break
                livres = page.locator('span[bsdatepickerdaydecorator]:not(.disabled):not(.is-other-month)')
                dias = [int(t.strip()) for t in livres.all_inner_texts() if t.strip().isdigit()]
                if not dias and datas:
                    break
                datas += [f'{ano:04d}-{mes:02d}-{d:02d}' for d in dias]
                page.locator('.bs-datepicker-head button.next').first.click()
                page.wait_for_timeout(300)
            page.keyboard.press('Escape')
            return sorted(set(datas))
        except Exception:
            logger.warning('FGTS: não li o calendário da guia parametrizada', exc_info=True)
            self._salvar_print('calendario')
            return []

    def _emitir_parametrizada(self, cnpj: str, aaaamm: str, data: str) -> List[Dict[str, Any]]:
        from app.services import fgts_pedidos_service as svc
        page = self._page
        dia = datetime.strptime(data, '%Y-%m-%d')
        dia_br = dia.strftime('%d/%m/%Y')
        self._param_selecionar(cnpj, aaaamm, todos=True)
        self._param_ir_vencimento()
        campo = self._campo_vencimento()
        campo.click(click_count=3)
        campo.press_sequentially(dia_br, delay=40)
        campo.press('Tab')
        try:
            page.wait_for_response(lambda r: 'debitos-revisao' in r.url and f'dtPagamento={data}' in r.url,
                                   timeout=20_000)
        except Exception:
            pass
        self._esperar_assentar()
        if campo.input_value() != dia_br:
            raise FalhaRobo(f'O FGTS Digital não aceitou {dia_br} como vencimento. Escolha outra data da lista.')
        self._visivel('Avançar').first.click()
        page.get_by_text(re.compile(r'Vencimento da Guia:\s*' + re.escape(dia_br))).first.wait_for()
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            raise FalhaRobo('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        detalhe = self._detalhar(aaaamm, self._texto())
        with page.expect_download(timeout=60_000) as baixa:
            self._visivel('Emitir Guia').last.click()
            self._confirmar_modal_se_houver()
        nome = f'{aaaamm}_p_{datetime.now():%Y%m%d%H%M%S}.pdf'
        destino = svc.pasta_pdfs(cnpj) / nome
        baixa.value.save_as(str(destino))
        conteudo = destino.read_bytes()
        if not conteudo.startswith(b'%PDF') or not svc.pdf_e_da_empresa(conteudo, cnpj):
            destino.unlink(missing_ok=True)
            raise FalhaRobo('A guia baixada não confere com a empresa. Nada foi enviado; fale com o escritório.')
        page.wait_for_timeout(800)
        idents = RE_IDENT.findall(self._texto())
        return [{'grupo': 'FGTS', 'vencimento': dia_br, 'arquivo': nome,
                 'identificador': idents[0] if idents else None, 'valor': detalhe['total']}]

    def _confirmar_modal_se_houver(self) -> None:
        page = self._page
        for nome in ('Confirmar', 'Sim', 'OK'):
            b = page.get_by_role('dialog').get_by_role('button', name=nome)
            try:
                if b.count() and b.first.is_visible():
                    b.first.click()
                    return
            except Exception:
                continue


_robo: Optional[RoboFgts] = None
_robo_lock = threading.Lock()


def robo() -> RoboFgts:
    global _robo
    with _robo_lock:
        if _robo is None:
            from flask import current_app
            _robo = RoboFgts(current_app._get_current_object())
        return _robo
