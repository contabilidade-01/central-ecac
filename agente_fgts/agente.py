"""Agente FGTS: roda no PC do escritório e executa os pedidos da Central Ecac.

Ciclo: abre o Chrome do FGTS, conduz o login (certificado automático; captcha com o
operador), manda sinal de vida, pede o próximo pedido, executa, devolve o resultado.
Uso:  python agente.py            (config em config.json, ao lado deste arquivo)
      python agente.py --uma-vez  (um ciclo só, para teste)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA))

import pix  # noqa: E402
import portal  # noqa: E402
import sessao  # noqa: E402
from central import Central, CentralIndisponivel, CentralRecusou  # noqa: E402

log = logging.getLogger('agente_fgts')


def carregar_config() -> dict:
    arquivo = PASTA / 'config.json'
    if not arquivo.exists():
        sys.exit('Crie config.json a partir de config.json.exemplo (URL da Central e token do agente).')
    cfg = json.loads(arquivo.read_text(encoding='utf-8-sig'))   # Bloco de Notas grava com BOM
    for chave in ('central_url', 'token'):
        if not cfg.get(chave):
            sys.exit(f'config.json sem "{chave}".')
    return cfg


class Agente:
    def __init__(self, cfg: dict, central: Central = None):
        self.cfg = cfg
        self.central = central or Central(cfg['central_url'], cfg['token'])
        self.porta = int(cfg.get('porta_chrome', 9222))
        self.perfil = Path(os.path.expandvars(cfg.get('perfil_chrome', r'%LOCALAPPDATA%\Nescon\agente-fgts-chrome')))
        self.maquina = cfg.get('nome_maquina') or platform.node()
        self.config_central: dict = {}
        self._pw = self._navegador = self._contexto = None

    # ------------------------------------------------------------ navegador
    def conectar(self):
        from playwright.sync_api import sync_playwright
        sessao.abrir_chrome(self.porta, self.perfil)
        if not self._pw:
            self._pw = sync_playwright().start()
        if not self._navegador or not self._navegador.is_connected():
            self._navegador = self._pw.chromium.connect_over_cdp(f'http://127.0.0.1:{self.porta}')
            self._contexto = self._navegador.contexts[0]
        return self._contexto

    def pagina(self):
        contexto = self.conectar()
        return sessao.aba_do_portal(contexto) or (contexto.pages[0] if contexto.pages else contexto.new_page())

    def logado(self) -> bool:
        try:
            return sessao.no_portal(self.pagina().url)
        except Exception:
            return False

    def garantir_login(self) -> bool:
        if self.logado():
            return True
        c = self.config_central
        espera = int(c.get('captcha_timeout_s', 300)) + int(c.get('login_timeout_s', 60))
        return sessao.conduzir_login(self.conectar(), c.get('certificado_nome', ''), espera, log.warning)

    # ------------------------------------------------------------ ciclo
    def sinal(self) -> dict:
        resposta = self.central.sinal(self.logado(), self.maquina)
        self.config_central = resposta.get('config') or {}
        if not resposta.get('versao_aceita', True):
            log.error('Esta versão do agente não é mais aceita pela Central. Atualize o agente.')
        return resposta

    def ciclo(self) -> bool:
        """Um ciclo. Devolve True se executou um pedido."""
        resposta = self.sinal()
        if not resposta.get('habilitado') or resposta.get('executor') != 'agente':
            return False
        if not self.logado():
            if not self.garantir_login():
                return False
            self.sinal()
        pedido = self.central.proximo()
        if not pedido:
            return False
        self.executar(pedido)
        return True

    def executar(self, pedido: dict) -> None:
        pid, cnpj = pedido['id'], pedido['cnpj']
        pagina = self.pagina()
        try:
            if pedido['acao'] == 'consultar':
                self.central.progresso(pid, 'consultando')
                comps = portal.consultar(pagina, cnpj, self.config_central.get('filtros') or {},
                                         int(self.config_central.get('max_competencias') or 0),
                                         progresso=lambda t: self.central.progresso(pid, 'consultando', t))
                self.central.resultado(pid, {'competencias': comps, 'datas_permitidas': []})
            else:
                self.central.progresso(pid, 'emitindo')
                for comp in pedido.get('competencias') or []:
                    for guia in portal.emitir(pagina, cnpj, comp, pedido.get('data_pagamento')):
                        self.entregar(pid, comp, guia, pedido.get('data_pagamento'))
                    self.central.progresso(pid, 'emitindo', f'{comp} emitida')
                self.central.resultado(pid, {})
            log.info('Pedido %s (%s) concluído.', pid, pedido['acao'])
        except portal.SemProcuracao as exc:
            self.central.erro(pid, 'sem_procuracao', str(exc))
        except portal.FalhaFgts as exc:
            self.central.erro(pid, 'portal', str(exc))
        except CentralRecusou as exc:
            log.error('Central recusou o pedido %s: %s', pid, exc)
            self.central.erro(pid, 'central_recusou', str(exc))
        except (CentralIndisponivel, KeyboardInterrupt):
            raise
        except Exception as exc:
            log.exception('Falha inesperada no pedido %s', pid)
            self.central.erro(pid, 'falha_agente', f'{type(exc).__name__}: {exc}')
        finally:
            time.sleep(int(self.config_central.get('ritmo_s', 5)))

    def entregar(self, pid: int, competencia: str, guia: dict, data_pagamento) -> None:
        pdf = guia.get('pdf')
        if not pdf:
            raise portal.FalhaFgts('O portal não entregou o PDF da guia emitida.')
        numero = guia.get('identificador') or pix.numero_do_pdf(pdf)
        if not numero:
            raise portal.FalhaFgts('Não encontrei o número da guia emitida. Confira no portal.')
        codigo_pix = pix.do_pdf(pdf)
        self.central.guia(pid, pdf, {'numero': numero, 'competencia': competencia, 'grupo': guia.get('grupo'),
                                     'valor': guia.get('valor'), 'vencimento': guia.get('vencimento'),
                                     'data_pagamento': data_pagamento, 'pix': codigo_pix})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--uma-vez', action='store_true')
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    agente = Agente(carregar_config())
    log.info('Agente FGTS iniciado. Deixe esta janela e o Chrome do FGTS abertos.')
    while True:
        try:
            trabalhou = agente.ciclo()
        except CentralIndisponivel as exc:
            log.warning('%s. Tentando de novo em 30 s.', exc)
            trabalhou = False
            time.sleep(30)
        except KeyboardInterrupt:
            log.info('Agente encerrado.')
            return
        if args.uma_vez:
            return
        if not trabalhou:
            time.sleep(int(agente.config_central.get('agente_sinal_s', 30)))


if __name__ == '__main__':
    main()
