"""Tela de restauração de dados — envio do banco e do certificado pelo navegador.

⚠️ DESVIO INTENCIONAL (11o) — NÃO existe no exe, que era desktop: o banco e o `.pfx` já
estavam na máquina do usuário.

Por que existe
--------------
Publicado numa VPS, levar esses dois arquivos para o volume exigia SSH, `scp` e ainda um
`chown` (o container roda como uid 10001, e um arquivo enviado por root deixa o SQLite
somente leitura — erro difícil de diagnosticar). Aqui o **próprio app** grava, então o
dono do arquivo já sai correto, e nada disso precisa ser explicado.

Segurança
---------
* Exige login (o guard de `security.py` cobre esta rota como qualquer outra).
* O banco enviado é **validado** antes de entrar no lugar: precisa ser SQLite íntegro e
  conter as tabelas do sistema. Arquivo errado é recusado sem tocar no que está no ar.
* O banco atual é **copiado para `backups/`** antes da troca. Nada é perdido.
* Depois da troca, as migrações rodam de novo — banco de versão antiga é atualizado.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, render_template_string, request

from app.extensions import db
from app.models import AppSetting, Company

restaurar_bp = Blueprint('restaurar', __name__)

TABELAS_ESPERADAS = {'companies', 'settings', 'relatorios_sitfiscal'}
NOME_CERTIFICADO = 'contador_certificado.pfx'


def _dirs():
    from app.config import DATA_DIR
    return {
        'banco': Path(DATA_DIR) / 'instance' / 'integra_contador.db',
        'certificados': Path(DATA_DIR) / 'certificates',
        'backups': Path(DATA_DIR) / 'backups',
    }


def _estado() -> dict:
    caminhos = _dirs()
    certificado = caminhos['certificados'] / NOME_CERTIFICADO
    try:
        empresas = Company.query.count()
    except Exception:
        empresas = 0
    try:
        configurado = AppSetting.query.first() is not None
    except Exception:
        configurado = False

    return {
        'empresas': empresas,
        'contador_configurado': configurado,
        'certificado_presente': certificado.exists(),
        'certificado_caminho': str(certificado),
        'banco_tamanho_kb': round(caminhos['banco'].stat().st_size / 1024, 1)
        if caminhos['banco'].exists() else 0,
    }


def _validar_banco(caminho: Path) -> str | None:
    """Devolve a mensagem de erro, ou None se o arquivo servir."""
    try:
        con = sqlite3.connect(f'file:{caminho}?mode=ro', uri=True)
    except sqlite3.Error:
        return 'O arquivo enviado não é um banco SQLite válido.'

    try:
        integridade = con.execute('PRAGMA integrity_check').fetchone()
        if not integridade or integridade[0] != 'ok':
            return 'O banco enviado está corrompido (falhou no teste de integridade).'

        tabelas = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        faltando = TABELAS_ESPERADAS - tabelas
        if faltando:
            return ('Este banco não parece ser o da Central e-CAC: faltam as tabelas '
                    + ', '.join(sorted(faltando)) + '.')

        empresas = con.execute('SELECT COUNT(*) FROM companies').fetchone()[0]
    except sqlite3.Error as exc:
        return f'Não foi possível ler o banco enviado: {exc}'
    finally:
        con.close()

    if empresas == 0:
        return ('O banco enviado não tem nenhuma empresa cadastrada. '
                'Confira se escolheu o arquivo certo.')
    return None


@restaurar_bp.get('/restaurar')
def tela_restaurar():
    return render_template_string(PAGINA, estado=_estado())


@restaurar_bp.get('/api/restaurar/estado')
def estado():
    return jsonify(_estado())


@restaurar_bp.post('/api/restaurar/banco')
def restaurar_banco():
    arquivo = request.files.get('banco')
    if not arquivo or not arquivo.filename:
        return jsonify({'success': False, 'message': 'Escolha o arquivo do banco.'}), 400

    caminhos = _dirs()
    caminhos['backups'].mkdir(parents=True, exist_ok=True)
    caminhos['banco'].parent.mkdir(parents=True, exist_ok=True)

    temporario = Path(tempfile.mkdtemp(prefix='restaurar_')) / 'enviado.db'
    arquivo.save(temporario)

    erro = _validar_banco(temporario)
    if erro:
        shutil.rmtree(temporario.parent, ignore_errors=True)
        return jsonify({'success': False, 'message': erro}), 400

    # Backup do que está no ar antes de qualquer troca.
    backup = None
    if caminhos['banco'].exists():
        carimbo = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = caminhos['backups'] / f'integra_contador-antes-{carimbo}.db'
        try:
            origem = sqlite3.connect(caminhos['banco'])
            destino = sqlite3.connect(backup)
            with destino:
                origem.backup(destino)
            origem.close()
            destino.close()
        except sqlite3.Error:
            shutil.copy2(caminhos['banco'], backup)

    # Solta as conexões antes de trocar o arquivo debaixo do SQLAlchemy.
    db.session.remove()
    db.engine.dispose()

    shutil.move(str(temporario), str(caminhos['banco']))
    shutil.rmtree(temporario.parent, ignore_errors=True)

    # Banco de versão anterior: coloca no schema atual e CONFERE se dá para usar.
    # Validar só a existência das tabelas não basta — um banco antigo pode ter as
    # tabelas e faltar coluna, e aí o sistema quebraria em todas as telas. Se a prova
    # falhar, voltamos o backup: melhor recusar do que deixar o sistema inutilizável.
    from app.migrations import run_migrations
    try:
        db.create_all()
        run_migrations()
        Company.query.count()
        AppSetting.query.first()
    except Exception as exc:
        db.session.remove()
        db.engine.dispose()
        if backup and backup.exists():
            shutil.copy2(backup, caminhos['banco'])
            recuperacao = 'O banco anterior foi restaurado; nada foi perdido.'
        else:
            caminhos['banco'].unlink(missing_ok=True)
            db.create_all()
            recuperacao = 'O sistema voltou ao banco vazio.'
        return jsonify({
            'success': False,
            'message': (f'O banco enviado não é compatível com esta versão do sistema '
                        f'({exc.__class__.__name__}). {recuperacao}'),
        }), 400

    depois = _estado()
    return jsonify({
        'success': True,
        'message': f'Banco restaurado: {depois["empresas"]} empresas.',
        'backup': str(backup) if backup else None,
        'estado': depois,
    })


@restaurar_bp.post('/api/restaurar/certificado')
def restaurar_certificado():
    arquivo = request.files.get('certificado')
    if not arquivo or not arquivo.filename:
        return jsonify({'success': False, 'message': 'Escolha o arquivo .pfx.'}), 400

    if not arquivo.filename.lower().endswith(('.pfx', '.p12')):
        return jsonify({'success': False,
                        'message': 'O certificado A1 tem extensão .pfx ou .p12.'}), 400

    caminhos = _dirs()
    caminhos['certificados'].mkdir(parents=True, exist_ok=True)
    destino = caminhos['certificados'] / NOME_CERTIFICADO
    arquivo.save(destino)

    # O caminho gravado no banco costuma apontar para a máquina antiga (Windows).
    # Existe fallback em report_service, mas deixar o valor certo evita confusão.
    setting = AppSetting.query.first()
    atualizado = False
    if setting and setting.certificado_path != str(destino):
        setting.certificado_path = str(destino)
        db.session.commit()
        atualizado = True

    return jsonify({
        'success': True,
        'message': 'Certificado enviado.' + (' Caminho atualizado nas Configurações.'
                                             if atualizado else ''),
        'caminho': str(destino),
        'estado': _estado(),
    })


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Restaurar dados — Central Pendências e-CAC</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, "Segoe UI", sans-serif; margin:0; padding:24px;
         background:#f5f6f8; color:#1c1e21; }
  @media (prefers-color-scheme: dark) {
    body { background:#15171a; color:#e8eaed; }
    .card { background:#1e2125 !important; border-color:#2c3036 !important; } }
  .wrap { max-width:720px; margin:0 auto; }
  h1 { font-size:20px; margin:0 0 4px; }
  p.sub { margin:0 0 20px; color:#6b7280; font-size:14px; line-height:1.55; }
  .card { background:#fff; border:1px solid #e3e5e8; border-radius:10px; padding:18px;
          margin-bottom:16px; }
  h2 { font-size:15px; margin:0 0 6px; }
  .dica { color:#6b7280; font-size:13px; margin:0 0 14px; line-height:1.5; }
  .tiles { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
  .tile { flex:1 1 150px; background:#fff; border:1px solid #e3e5e8; border-radius:10px;
          padding:12px 16px; }
  .tile b { display:block; font-size:22px; line-height:1.3; }
  .tile span { font-size:11px; color:#6b7280; text-transform:uppercase;
               letter-spacing:.04em; }
  @media (prefers-color-scheme: dark) { .tile { background:#1e2125; border-color:#2c3036; } }
  input[type=file] { display:block; width:100%; font:inherit; margin-bottom:12px; }
  button { font:inherit; font-weight:600; padding:9px 16px; border:0; border-radius:8px;
           background:#1a73e8; color:#fff; cursor:pointer; }
  button:hover { background:#1666d0; }
  button:disabled { background:#9aa0a6; cursor:progress; }
  .msg { margin-top:12px; padding:10px 12px; border-radius:8px; font-size:13px;
         line-height:1.5; }
  .ok { background:#e6f4ea; color:#137333; }
  .bad { background:#fce8e6; color:#b3261e; }
  .caminho { font-size:12px; color:#6b7280; word-break:break-all; }
  .rodape { font-size:13px; color:#6b7280; text-align:center; margin-top:20px; }
  a { color:#1a73e8; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Restaurar dados</h1>
  <p class="sub">
    Envie aqui o <b>banco de dados</b> e o <b>certificado A1</b> que estão no seu
    computador. O sistema grava os dois no volume do servidor, com as permissões certas —
    não é preciso terminal, SSH nem comando nenhum.
  </p>

  <div class="tiles">
    <div class="tile"><b id="t-empresas">{{ estado.empresas }}</b><span>empresas</span></div>
    <div class="tile"><b id="t-banco">{{ estado.banco_tamanho_kb }} KB</b><span>banco</span></div>
    <div class="tile"><b id="t-cert">{{ 'sim' if estado.certificado_presente else 'não' }}</b><span>certificado</span></div>
  </div>

  <div class="card">
    <h2>1. Banco de dados</h2>
    <p class="dica">
      No seu PC:<br>
      <span class="caminho">…\\Central Pendencias Ecac\\Central eCac\\instance\\integra_contador.db</span><br><br>
      O banco atual é copiado para <code>backups/</code> antes da troca, e o arquivo
      enviado é conferido antes de entrar no lugar. Se não for um banco válido da Central,
      ele é recusado e nada muda.
    </p>
    <form id="f-banco">
      <input type="file" name="banco" accept=".db,.sqlite3" required>
      <button type="submit">Enviar banco</button>
    </form>
    <div id="m-banco"></div>
  </div>

  <div class="card">
    <h2>2. Certificado A1 (.pfx)</h2>
    <p class="dica">
      No seu PC:<br>
      <span class="caminho">…\\Central Pendencias Ecac\\Central eCac\\certificates\\contador_certificado.pfx</span><br><br>
      A senha do certificado não é pedida aqui — ela já vem dentro do banco.
    </p>
    <form id="f-cert">
      <input type="file" name="certificado" accept=".pfx,.p12" required>
      <button type="submit">Enviar certificado</button>
    </form>
    <div id="m-cert"></div>
  </div>

  <p class="rodape">
    Terminou? <a href="/">Voltar ao painel</a> — o menu destrava assim que o banco entrar.
  </p>
</div>

<script>
function enviar(idForm, idMsg, url, campo) {
  const form = document.getElementById(idForm);
  const msg = document.getElementById(idMsg);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const botao = form.querySelector('button');
    const arquivo = form.querySelector('input[type=file]').files[0];
    if (!arquivo) return;

    botao.disabled = true;
    botao.textContent = 'Enviando…';
    msg.className = '';
    msg.textContent = '';

    const dados = new FormData();
    dados.append(campo, arquivo);
    try {
      const resp = await fetch(url, { method: 'POST', body: dados });
      const json = await resp.json();
      msg.className = 'msg ' + (json.success ? 'ok' : 'bad');
      msg.textContent = json.message || (json.success ? 'Pronto.' : 'Falhou.');
      if (json.estado) {
        document.getElementById('t-empresas').textContent = json.estado.empresas;
        document.getElementById('t-banco').textContent = json.estado.banco_tamanho_kb + ' KB';
        document.getElementById('t-cert').textContent = json.estado.certificado_presente ? 'sim' : 'não';
      }
    } catch (e) {
      msg.className = 'msg bad';
      msg.textContent = 'Erro de rede: ' + e.message;
    } finally {
      botao.disabled = false;
      botao.textContent = form.id === 'f-banco' ? 'Enviar banco' : 'Enviar certificado';
    }
  });
}
enviar('f-banco', 'm-banco', '/api/restaurar/banco', 'banco');
enviar('f-cert', 'm-cert', '/api/restaurar/certificado', 'certificado');
</script>
</body>
</html>
"""
