"""App local do FGTS — roda no PC do escritório (interface em localhost).

⚠️ O LOGIN é do operador: ele abre o Chrome do FGTS (atalho "Abrir Chrome do FGTS"),
entra no FGTS Digital à mão (certificado e captcha) e só então usa esta tela. A automação
reaproveita essa sessão para consultar e emitir. Não resolve captcha nem opera o login.

Como usar:
  1. Dê um duplo-clique em "Abrir Chrome do FGTS.bat" e entre no FGTS Digital.
  2. Dê um duplo-clique em "Rodar FGTS.bat" (abre esta tela em http://127.0.0.1:5055).
  3. Informe o CNPJ do cliente, consulte, escolha os meses e gere as guias.

Tudo roda no PC; os PDFs ficam na pasta "guias". Nada é enviado para fora.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

import conexao
from fgts_portal import FalhaFgts, aaaamm_para_comp

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('fgts-local')

BASE = Path(__file__).resolve().parent


def _config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    arq = BASE / 'config.json'
    if arq.exists():
        try:
            cfg = json.loads(arq.read_text(encoding='utf-8'))
        except Exception:
            logger.warning('config.json inválido; usando padrões')
    return cfg


CFG = _config()
PORTA_CHROME = int(os.getenv('FGTS_PORTA_CHROME') or CFG.get('porta_chrome') or 9222)
PORTA_APP = int(os.getenv('FGTS_PORTA_APP') or CFG.get('porta_app') or 5055)
PASTA_SAIDA = Path(os.getenv('FGTS_PASTA_GUIAS') or CFG.get('pasta_guias') or (BASE / 'guias'))
PASTA_DIAG = BASE / 'diagnostico'
PASTA_SAIDA.mkdir(parents=True, exist_ok=True)


def _digitos(valor: Any) -> str:
    return ''.join(ch for ch in str(valor or '') if ch.isdigit())


def _msg_guias(guias: List[Dict[str, Any]]) -> str:
    total = sum(g.get('valor') or 0 for g in guias)
    if not total:
        return f'{len(guias)} guia(s) gerada(s).'
    valor = f'{total:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'{len(guias)} guia(s) gerada(s): R$ {valor}'


class Executor:
    """Um thread dono do Playwright (a API síncrona não pode ser usada de outros threads).
    Cada trabalho liga ao Chrome do operador, faz a tarefa e desliga."""

    def __init__(self) -> None:
        self.fila: 'queue.Queue[tuple]' = queue.Queue()
        self.lock = threading.Lock()
        self.estado: Dict[str, Any] = {
            'status': 'ocioso', 'mensagem': '', 'cnpj': None,
            'competencias': [], 'datas_permitidas': [], 'guias': [],
            'chrome': {'chrome': False, 'logado': False, 'abas_fgts': 0, 'versao': ''},
            'atualizado_em': None,
        }
        threading.Thread(target=self._loop, name='fgts-local', daemon=True).start()

    # -------------------------------------------------------------- estado
    def _set(self, **kw: Any) -> None:
        with self.lock:
            self.estado.update(kw)
            self.estado['atualizado_em'] = datetime.now().strftime('%H:%M:%S')

    def ler(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.estado)

    def ocupado(self) -> bool:
        return self.ler()['status'] in ('consultando', 'emitindo')

    # -------------------------------------------------------------- fila
    def verificar(self) -> None:
        self.fila.put(('verificar', None, None, None))

    def consultar(self, cnpj: str) -> None:
        self._set(status='consultando', cnpj=cnpj, mensagem='Na fila…',
                  competencias=[], datas_permitidas=[], guias=[])
        self.fila.put(('consultar', cnpj, None, None))

    def emitir(self, cnpj: str, competencias: List[str], data: Optional[str]) -> None:
        self._set(status='emitindo', cnpj=cnpj, mensagem='Na fila…', guias=[])
        self.fila.put(('emitir', cnpj, competencias, data))

    # -------------------------------------------------------------- laço
    def _loop(self) -> None:
        while True:
            tipo, cnpj, comps, data = self.fila.get()
            try:
                self._processar(tipo, cnpj, comps, data)
            except Exception:
                logger.exception('Falha no trabalho %s', tipo)
                self._set(status='erro', mensagem='Erro inesperado. Veja a pasta "diagnostico".')

    def _processar(self, tipo: str, cnpj: Optional[str], comps, data) -> None:
        if tipo == 'verificar':
            self._set(chrome=conexao.diagnostico_chrome(PORTA_CHROME))
            return
        sess = conexao.AutomacaoLocal(PORTA_CHROME, PASTA_DIAG)
        try:
            self._set(mensagem='Ligando ao Chrome do FGTS…')
            sess.conectar()
            sess.exigir_login()
            self._set(chrome={'chrome': True, 'logado': True,
                              'abas_fgts': self.ler()['chrome'].get('abas_fgts', 0), 'versao': ''})
            self._set(mensagem='Abrindo a empresa (perfil de procurador)…')
            sess.definir_perfil(cnpj)
            if tipo == 'consultar':
                self._set(mensagem='Consultando débitos de FGTS…')
                competencias = sess.consultar(cnpj)
                datas = sess.datas_permitidas(cnpj, competencias[0]['competencia']) if competencias else []
                if competencias:
                    self._set(status='aguardando_escolha', competencias=competencias,
                              datas_permitidas=datas,
                              mensagem=f'{len(competencias)} competência(s) em aberto. Escolha e gere.')
                else:
                    self._set(status='concluido', competencias=[], datas_permitidas=[],
                              mensagem='Nenhum débito de FGTS em aberto.')
            else:
                guias: List[Dict[str, Any]] = []
                falhas: List[str] = []
                for aaaamm in comps:
                    self._set(mensagem=f'Gerando guia de {aaaamm_para_comp(aaaamm)}…')
                    try:
                        for g in sess.emitir(cnpj, aaaamm, data):
                            g['competencia'] = aaaamm
                            nome = g['arquivo']
                            (PASTA_SAIDA / nome).write_bytes(sess.pdfs[nome])
                            guias.append(g)
                            self._set(guias=list(guias))
                    except FalhaFgts as exc:
                        falhas.append(f'{aaaamm_para_comp(aaaamm)}: {exc}')
                if guias:
                    msg = _msg_guias(guias) + (' Não saiu: ' + '; '.join(falhas) if falhas else '')
                    self._set(status='concluido', mensagem=msg)
                else:
                    self._set(status='erro', mensagem='; '.join(falhas) or 'Nenhuma guia foi gerada.')
        except conexao.SemChrome as exc:
            self._set(status='erro', mensagem=str(exc), chrome=conexao.diagnostico_chrome(PORTA_CHROME))
        except conexao.SemLogin as exc:
            self._set(status='erro', mensagem=str(exc),
                      chrome={'chrome': True, 'logado': False, 'abas_fgts': 0, 'versao': ''})
        except FalhaFgts as exc:
            self._set(status='erro', mensagem=str(exc))
        finally:
            sess.fechar()


EXEC = Executor()
app = Flask(__name__)


@app.get('/')
def home():
    return render_template('index.html', porta_chrome=PORTA_CHROME, pasta=str(PASTA_SAIDA))


@app.get('/api/estado')
def estado():
    return jsonify(EXEC.ler())


@app.post('/api/verificar')
def verificar():
    EXEC.verificar()
    return jsonify({'ok': True})


@app.post('/api/consultar')
def consultar():
    if EXEC.ocupado():
        return jsonify({'ok': False, 'message': 'Já tem um trabalho em andamento.'}), 409
    cnpj = _digitos((request.get_json(silent=True) or {}).get('cnpj'))
    if len(cnpj) != 14:
        return jsonify({'ok': False, 'message': 'Informe um CNPJ com 14 dígitos.'}), 400
    EXEC.consultar(cnpj)
    return jsonify({'ok': True})


@app.post('/api/emitir')
def emitir():
    estado_atual = EXEC.ler()
    if EXEC.ocupado():
        return jsonify({'ok': False, 'message': 'Já tem um trabalho em andamento.'}), 409
    corpo = request.get_json(silent=True) or {}
    cnpj = _digitos(corpo.get('cnpj'))
    validas = {c['competencia'] for c in estado_atual.get('competencias', [])}
    escolha: List[str] = []
    for c in corpo.get('competencias') or []:
        comp = _digitos(c)
        if comp in validas and comp not in escolha:
            escolha.append(comp)
    if not escolha:
        return jsonify({'ok': False, 'message': 'Escolha ao menos um mês da lista consultada.'}), 400
    data = corpo.get('data_pagamento') or None
    if data and data not in (estado_atual.get('datas_permitidas') or []):
        return jsonify({'ok': False, 'message': 'Essa data de pagamento não está na lista aceita.'}), 400
    EXEC.emitir(cnpj, escolha, data)
    return jsonify({'ok': True})


@app.get('/guias/<path:nome>')
def baixar(nome: str):
    if '/' in nome or '\\' in nome or '..' in nome:
        abort(404)
    if not (PASTA_SAIDA / nome).exists():
        abort(404)
    return send_from_directory(PASTA_SAIDA, nome, as_attachment=False)


def main() -> None:
    url = f'http://127.0.0.1:{PORTA_APP}/'
    logger.info('FGTS local em %s — Chrome na porta %s — guias em %s', url, PORTA_CHROME, PASTA_SAIDA)
    try:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    except Exception:
        pass
    app.run(host='127.0.0.1', port=PORTA_APP, threaded=True)


if __name__ == '__main__':
    main()
