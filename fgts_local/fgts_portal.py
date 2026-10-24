"""Telas do FGTS Digital (Playwright) — o que fazer DEPOIS do login.

⚠️ Este módulo NÃO faz login e NÃO resolve captcha. Recebe uma página do Playwright já
logada no FGTS Digital (o OPERADOR entrou à mão, com o certificado e o captcha, no Chrome
dele) e faz só o trabalho de escritório que o procurador tem direito de fazer: definir o
perfil de Procurador do CNPJ, listar as competências em aberto com valores e composição,
ler os dias aceitos no vencimento da Guia Parametrizada e emitir (Rápida = pagar hoje;
Parametrizada = outra data). Nunca abre nem opera a tela de login do gov.br.

Sem dependência de Flask nem do resto do sistema — roda igual no PC do escritório.

Mapeado na tela em 23/10/2026. Ganchos para quem herda: `_receber_pdf` (o que fazer com a
GFD baixada), `_hoje_iso`, `_salvar_print` e `_download_cdp` (download do Chrome do
operador via CDP, quando a GFD não vem como resposta de rede).
"""

from __future__ import annotations

import base64
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

URL_SERVICOS = 'https://fgtsdigital.sistema.gov.br/portal/servicos'
URL_PERFIL = 'https://fgtsdigital.sistema.gov.br/portal/escolhaPerfil'
URL_GUIA_RAPIDA = 'https://fgtsdigital.sistema.gov.br/cobranca/#/gestao-guias/emissao-guia-rapida'
URL_GUIA_PARAM = 'https://fgtsdigital.sistema.gov.br/cobranca/#/gestao-guias/emissao-guia-parametrizada'
MESES_PT = {'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3, 'abril': 4, 'maio': 5, 'junho': 6,
            'julho': 7, 'agosto': 8, 'setembro': 9, 'outubro': 10, 'novembro': 11, 'dezembro': 12}
RE_QTD_DEBITOS = re.compile(r'Quantidade de\s*D[ée]bitos de FGTS\s*(\d+)', re.I)
RE_TOTAL_ITENS = re.compile(r'\bde\s+(\d+)\s+itens', re.I)
RE_COMP = re.compile(r'\b(0[1-9]|1[0-2])/(20\d{2})\b')
RE_TOTAL = re.compile(r'Total\s+Devedor\s*R\$\s*([\d.]+,\d{2})', re.I)
RE_GRUPO = re.compile(r'([A-Za-zÀ-ú ]+?)\s*-\s*Vencimento da Guia:\s*(\d{2}/\d{2}/\d{4})')
RE_IDENT = re.compile(r'\b(\d{16}-\d)\b')
_V = r'(\d{1,3}(?:\.\d{3})*,\d{2})'
RE_LINHA_TOTAL = re.compile(r'\bTotal\s+' + r'\s+'.join([_V] * 6))
COLUNAS = ('fgts_mensal', 'fgts_rescisorio', 'indenizacao', 'processo_trabalhista', 'encargos', 'total')
SEM_PROCURACAO = 'Não existe procuração'


class FalhaFgts(Exception):
    """Erro com mensagem pronta para a tela."""


def fmt_cnpj(cnpj: str) -> str:
    return f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}'


def aaaamm_para_comp(aaaamm: str) -> str:
    return f'{aaaamm[4:]}/{aaaamm[:4]}'


def valor_br(texto: Optional[str]) -> Optional[float]:
    if not texto:
        return None
    try:
        return float(texto.replace('.', '').replace(',', '.'))
    except ValueError:
        return None


def pdf_e_da_empresa(conteudo: bytes, cnpj: str) -> bool:
    """O texto da GFD traz o CNPJ do empregador (ex.: '36.298.150'). Sem ele, não serve.
    O PDF da GFD costuma imprimir só a RAIZ (8 primeiros dígitos) do CNPJ."""
    raiz = cnpj[:8]
    raiz_fmt = f'{raiz[:2]}.{raiz[2:5]}.{raiz[5:8]}'
    try:
        import io
        try:
            from pypdf import PdfReader           # pacote atual
        except ImportError:
            from PyPDF2 import PdfReader           # nome antigo (mesma API 3.x)
        texto = ''.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(conteudo)).pages[:2])
    except Exception:
        logger.warning('PDF do FGTS ilegível para conferir CNPJ %s', cnpj)
        return False
    so_digitos = ''.join(ch for ch in texto if ch.isdigit())
    return raiz_fmt in texto or raiz in so_digitos


def _pdf_da_resposta(resp) -> Optional[bytes]:
    """Corpo de uma resposta de rede quando ele É o PDF (ou traz o PDF em base64)."""
    try:
        tipo = (resp.headers.get('content-type') or '').lower()
        if not any(t in tipo for t in ('pdf', 'octet-stream', 'json', 'text/plain')):
            return None
        corpo = resp.body()
    except Exception:
        return None
    if corpo.startswith(b'%PDF'):
        return corpo
    m = re.search(rb'JVBERi0[A-Za-z0-9+/=]{100,}', corpo)     # "%PDF-" em base64
    if m:
        try:
            pdf = base64.b64decode(m.group(0))
            return pdf if pdf.startswith(b'%PDF') else None
        except Exception:
            return None
    return None


class PortalFgts:
    """Passos do FGTS Digital sobre `self._page` (uma página Playwright já logada)."""

    max_competencias = 0          # 0 = todas

    def __init__(self, page=None):
        self._page = page
        self._perfil_cnpj: Optional[str] = None
        self.pdfs: Dict[str, bytes] = {}      # nome → conteúdo

    # ------------------------------------------------------------------ ganchos
    def _receber_pdf(self, cnpj: str, nome: str, conteudo: bytes) -> None:
        """GFD baixada. Padrão: guarda em memória, conferindo o CNPJ. Subclasse pode salvar."""
        if not conteudo.startswith(b'%PDF') or not pdf_e_da_empresa(conteudo, cnpj):
            raise FalhaFgts('A guia baixada não confere com a empresa. Nada foi guardado.')
        self.pdfs[nome] = conteudo

    def _hoje_iso(self) -> str:
        # Brasília (UTC-3, sem horário de verão desde 2019). Sem zoneinfo para não exigir
        # tzdata no Windows.
        return datetime.now(timezone(timedelta(hours=-3))).date().isoformat()

    def _salvar_print(self, nome: str) -> None:
        """Print para diagnóstico — cada lugar guarda onde fizer sentido."""

    def _download_cdp(self) -> Optional[bytes]:
        """Download capturado pelo Chrome do operador (via CDP). No padrão não há."""
        return None

    # ------------------------------------------------------------------ login (só confere)
    def no_portal(self) -> bool:
        """True se a página está logada no FGTS Digital (não é a tela de login)."""
        try:
            url = self._page.url
        except Exception:
            return False
        return 'fgtsdigital.sistema.gov.br' in url and '/portal/login' not in url and 'acessogov' not in url

    def _esperar_assentar(self) -> None:
        try:
            self._page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        self._fechar_avisos()

    def _fechar_avisos(self) -> None:
        """Banner de cookies do FGTS Digital: recusa o não essencial quando houver a opção;
        senão só esconde o banner (ele não bloqueia nada, mas cobre botões)."""
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

    # ------------------------------------------------------------------ texto
    def _texto(self) -> str:
        try:
            return self._page.locator('body').inner_text(timeout=10_000)
        except Exception:
            return ''

    def _empregador_na_tela(self, cnpj: str) -> bool:
        return f'Empregador: {fmt_cnpj(cnpj)}' in self._texto().replace('\xa0', ' ')

    # ------------------------------------------------------------------ perfil
    def definir_perfil(self, cnpj: str) -> None:
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
                raise FalhaFgts('O escritório não tem procuração no FGTS Digital para esta empresa.')
            if not page.locator('[role=dialog]').filter(has_text='Definir Perfil').count():
                break
            page.wait_for_timeout(500)
        else:
            self._salvar_print('perfil')
            raise FalhaFgts('Não foi possível abrir a empresa no FGTS Digital agora.')
        page.goto(URL_SERVICOS, wait_until='domcontentloaded')
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            self._salvar_print('perfil_divergente')
            raise FalhaFgts('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        self._perfil_cnpj = cnpj

    # ------------------------------------------------------------------ guia rápida / consulta
    def _abrir_guia_rapida(self, cnpj: str) -> None:
        page = self._page
        page.goto(URL_GUIA_RAPIDA, wait_until='domcontentloaded')
        page.get_by_text('Emissão de Guia Rápida').first.wait_for()
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            raise FalhaFgts('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')

    def _campo_competencia(self):
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
            raise FalhaFgts(f'A competência {aaaamm_para_comp(aaaamm)} não está mais em aberto no FGTS Digital.')
        alvo.first.click()
        page.wait_for_timeout(300)
        with page.expect_response(lambda r: '/guia-rapida/v2/' + aaaamm in r.url, timeout=30_000):
            page.get_by_role('button', name='Pesquisar').click()
        page.get_by_text('Total Devedor').first.wait_for(timeout=30_000)
        self._esperar_assentar()
        return self._texto()

    def consultar(self, cnpj: str) -> List[Dict[str, Any]]:
        self._abrir_guia_rapida(cnpj)
        comps = self._listar_competencias()
        if self.max_competencias > 0:
            comps = comps[:self.max_competencias]
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

    # ------------------------------------------------------------------ emissão
    def emitir(self, cnpj: str, aaaamm: str, data: Optional[str] = None) -> List[Dict[str, Any]]:
        """Data de pagamento = hoje (ou vazia) → Guia Rápida. Outra data → Guia Parametrizada."""
        if data and data != self._hoje_iso():
            return self._emitir_parametrizada(cnpj, aaaamm, data)
        return self._emitir_rapida(cnpj, aaaamm)

    def _emitir_rapida(self, cnpj: str, aaaamm: str) -> List[Dict[str, Any]]:
        page = self._page
        self._abrir_guia_rapida(cnpj)
        texto = self._pesquisar(aaaamm)
        if not self._empregador_na_tela(cnpj):
            raise FalhaFgts('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        grupos = RE_GRUPO.findall(texto)
        botoes = page.get_by_role('button', name=re.compile(r'^\s*Emitir guia\s*$', re.I))
        n = botoes.count()
        if not n:
            raise FalhaFgts('O FGTS Digital não ofereceu guia para esta competência (pode já ter sido paga).')
        guias = []
        for i in range(n):
            botao = page.get_by_role('button', name=re.compile(r'^\s*Emitir guia\s*$', re.I)).first
            conteudo = self._baixar_pdf(lambda: (botao.click(), self._confirmar_modal_se_houver()))
            nome = f'{aaaamm}_{i}_{datetime.now():%Y%m%d%H%M%S}.pdf'
            self._receber_pdf(cnpj, nome, conteudo)
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
    # Assistente em 4 passos: 1 Selecionar Débitos FGTS (filtro Competência Inicial/Final,
    # tabela por trabalhador com "#selecionar-todos", 5 por página, "#btn-next-page",
    # "Adicionar à guia"), 2 Consignado, 3 Definir Vencimento (campo "Vencimento da Guia"
    # dd/mm/aaaa + calendário bs-datepicker; data fora do limite é recusada em silêncio),
    # 4 Emitir Guia (tabela igual à da Guia Rápida).
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
            raise FalhaFgts(f'A competência {valor} não está mais em aberto no FGTS Digital.')
        alvo.first.click()
        page.wait_for_timeout(300)

    def _param_selecionar(self, cnpj: str, aaaamm: str, todos: bool = True) -> None:
        page = self._page
        page.goto(URL_GUIA_PARAM, wait_until='domcontentloaded')
        page.get_by_text('Emissão de Guia Parametrizada').first.wait_for()
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            raise FalhaFgts('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        comp = aaaamm_para_comp(aaaamm)
        self._ng_apos_rotulo('Inicial', comp)
        self._ng_apos_rotulo('Final', comp)
        with page.expect_response(lambda r: '/guia-parametrizada/debitos?' in r.url, timeout=30_000):
            page.get_by_role('button', name='Pesquisar', exact=True).click()
        self._esperar_assentar()
        if not page.locator('#selecionar-todos').count():
            raise FalhaFgts(f'O FGTS Digital não listou débitos de {comp} na guia parametrizada.')
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
                raise FalhaFgts(f'Não consegui pôr todos os débitos de {comp} na guia. Nada foi emitido.')

    def _param_ir_vencimento(self) -> None:
        page = self._page
        self._visivel('Avançar').first.click()
        page.wait_for_timeout(1200)
        self._visivel('Avançar').first.click()
        self._campo_vencimento().wait_for()
        self._esperar_assentar()

    def _campo_vencimento(self):
        return self._page.locator('label').filter(has_text='Vencimento da Guia').first \
            .locator('xpath=following::input[1]')

    def datas_permitidas(self, cnpj: str, aaaamm: str) -> List[str]:
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
            raise FalhaFgts(f'O FGTS Digital não aceitou {dia_br} como vencimento. Escolha outra data da lista.')
        self._visivel('Avançar').first.click()
        page.get_by_text(re.compile(r'Vencimento da Guia:\s*' + re.escape(dia_br))).first.wait_for()
        self._esperar_assentar()
        if not self._empregador_na_tela(cnpj):
            raise FalhaFgts('O FGTS Digital abriu outra empresa. Nada foi emitido; tente de novo.')
        detalhe = self._detalhar(aaaamm, self._texto())
        conteudo = self._baixar_pdf(lambda: (self._visivel('Emitir Guia').last.click(),
                                             self._confirmar_modal_se_houver()))
        nome = f'{aaaamm}_p_{datetime.now():%Y%m%d%H%M%S}.pdf'
        self._receber_pdf(cnpj, nome, conteudo)
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

    # ------------------------------------------------------------------ PDF
    def _baixar_pdf(self, clicar: Callable[[], Any], segundos: int = 60) -> bytes:
        """Clica e pega a GFD pelo caminho que aparecer primeiro: (1) a resposta de rede
        com o PDF (ou com o PDF em base64 num JSON), (2) o download do Playwright,
        (3) o download do Chrome do operador via CDP. Só olha esta aba e as que ela abrir."""
        page = self._page
        respostas: List[Any] = []
        baixados: List[Any] = []

        def ouvir(p):
            p.on('response', lambda r: respostas.append(r))
            p.on('download', lambda d: baixados.append(d))
            p.on('popup', ouvir)

        ouvir(page)
        vistas = 0
        try:
            clicar()
            limite = time.time() + segundos
            while time.time() < limite:
                while vistas < len(respostas):
                    pdf = _pdf_da_resposta(respostas[vistas])
                    vistas += 1
                    if pdf:
                        return pdf
                for d in baixados:
                    try:
                        conteudo = Path(d.path()).read_bytes()
                        if conteudo.startswith(b'%PDF'):
                            return conteudo
                    except Exception:
                        continue
                conteudo = self._download_cdp()
                if conteudo:
                    return conteudo
                page.wait_for_timeout(300)
        finally:
            for p in list(page.context.pages):
                try:
                    if p is not page and p.opener() is page:
                        p.close()          # aba que o "Emitir" abriu (PDF) — sempre fecha
                except Exception:
                    pass
        raise FalhaFgts('O FGTS Digital não entregou o PDF da guia. Tente de novo.')
