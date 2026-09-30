"""Tela `/contatos` — check de contatos (WhatsApp) e status da instância uazapi.

⚠️ DESVIO INTENCIONAL (19o) — NÃO existe no exe. Pedido do Jean (30/09/2026).

Rotas:
  GET  /contatos                         tela
  GET  /api/contatos                     lista + resumo (+ ?inativas=1)
  POST /api/contatos/<company_id>        salvar contato
  POST /api/contatos/importar            lote [{cnpj, whatsapp, whatsapp_2, email, responsavel}]
  POST /api/contatos/verificar           {company_ids?: [], online?: true}
  POST /api/contatos/teste               {company_id}  → mensagem de teste (fora da janela também)
  GET  /api/contatos/whatsapp/status     instância, teto/hora, configuração
  GET  /api/contatos/historico?company_id=
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, render_template_string, request

from app.services import contatos_service as svc
from app.services import uazapi_service as uazapi
from app.services.janela_envio import descricao_janela, pode_enviar_agora
from app.ui import CSS, FIM, lateral

contatos_bp = Blueprint('contatos', __name__)


def _quem() -> str:
    try:
        from app.security import usuario_atual
        u = usuario_atual()
        return (u or {}).get('usuario') or 'sistema'
    except Exception:
        return 'sistema'


@contatos_bp.get('/api/contatos')
def listar():
    itens = svc.listar(apenas_ativas=request.args.get('inativas') not in ('1', 'true'))
    return jsonify({'success': True, 'itens': itens, 'resumo': svc.resumo(itens)})


@contatos_bp.post('/api/contatos/<int:company_id>')
def salvar(company_id: int):
    try:
        item = svc.salvar(company_id, request.get_json(silent=True) or {}, quem=_quem())
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 404
    return jsonify({'success': True, 'item': item})


@contatos_bp.post('/api/contatos/importar')
def importar():
    corpo = request.get_json(silent=True) or {}
    itens = corpo.get('itens') if isinstance(corpo, dict) else corpo
    if not isinstance(itens, list):
        return jsonify({'success': False, 'message': 'Envie {"itens": [{cnpj, whatsapp, ...}]}.'}), 400
    return jsonify({'success': True, **svc.importar(itens, quem=_quem())})


@contatos_bp.post('/api/contatos/verificar')
def verificar():
    corpo = request.get_json(silent=True) or {}
    ids = corpo.get('company_ids')
    ids = [int(i) for i in ids] if isinstance(ids, list) else None
    return jsonify({'success': True, **svc.verificar(ids, online=corpo.get('online', True) is not False)})


@contatos_bp.post('/api/contatos/teste')
def teste():
    corpo = request.get_json(silent=True) or {}
    try:
        company_id = int(corpo.get('company_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'company_id obrigatório.'}), 400
    r = svc.enviar_teste(company_id, quem=_quem(), texto=(corpo.get('texto') or None))
    return jsonify({'success': bool(r.get('ok')), **r}), (200 if r.get('ok') else 502)


@contatos_bp.get('/api/contatos/whatsapp/status')
def status_whatsapp():
    return jsonify({
        'success': True,
        'instancia': uazapi.status_instancia(),
        'configurada': uazapi.configurado(),
        'subdominio': (os.getenv('UAZAPI_SUBDOMAIN') or '').strip() or None,
        'trava_so_clientes': uazapi.trava_ligada(),
        'numeros_escritorio': uazapi.numeros_do_escritorio(),
        'janela': descricao_janela(),
        'pode_enviar_agora': pode_enviar_agora(),
        'teto_por_hora': uazapi.max_por_hora(),
        'enviados_ultima_hora': uazapi.enviados_na_ultima_hora(),
        'throttle_s': uazapi.throttle_s(),
        'delay_ms': uazapi.delay_digitando_ms(),
        'webhook': uazapi.ler_webhook_cadastrado() if uazapi.configurado() else None,
    })


@contatos_bp.get('/api/contatos/historico')
def historico():
    cid = request.args.get('company_id')
    return jsonify({'success': True, 'itens': svc.historico(int(cid) if cid else None)})


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contatos (WhatsApp) — Central Pendências e-CAC</title>
<style>""" + CSS + """
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:14px 0; }
  .tile { background:var(--superficie); border:1px solid var(--borda); border-radius:10px; padding:12px 14px; }
  .tile b { font-size:22px; display:block; }
  .tile span { color:var(--suave); font-size:12px; }
  input.cel { width:150px; font:inherit; font-size:13px; padding:6px 8px; border:1px solid var(--borda); border-radius:8px; }
  input.mail { width:190px; font:inherit; font-size:13px; padding:6px 8px; border:1px solid var(--borda); border-radius:8px; }
  td small { color:var(--suave); }
  .linha-cmd { display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
</style>
</head>
<body>
""" + lateral('contatos') + """
<div class="wrap">
  <h1>Contatos e WhatsApp</h1>
  <p class="sub">
    Número de WhatsApp de cada empresa, conferido no formato e (quando a uazapi responde)
    na existência. <b>Só número cadastrado aqui recebe mensagem</b> — é a mesma trava do
    portal do cliente. O envio usa o primeiro celular válido entre os dois campos.
  </p>

  <div class="card" id="card-instancia">Carregando status da instância…</div>

  <div class="tiles" id="tiles"></div>

  <div class="card">
    <div class="linha-cmd">
      <button class="primario" onclick="verificarTodos()">Verificar todos (formato + WhatsApp)</button>
      <button onclick="mostrarImportar()">Importar lote (JSON)</button>
      <label style="margin-left:auto"><input type="checkbox" id="inativas" onchange="carregar()"> mostrar empresas inativas</label>
      <input class="mail" id="filtro" placeholder="filtrar por nome ou CNPJ" oninput="render()">
    </div>
    <div id="importar" style="display:none;margin-top:10px">
      <p class="desc">Cole um JSON no formato <code>[{"cnpj":"...","whatsapp":"34 99999-8888","whatsapp_2":"","email":"","responsavel":""}]</code>.
      Casa pelo CNPJ; empresa não cadastrada aqui é listada e ignorada.</p>
      <textarea id="json-lote" rows="6" style="width:100%;font:12px monospace"></textarea>
      <button class="primario" onclick="importar()">Importar</button>
    </div>
  </div>

  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Empresa</th><th>WhatsApp</th><th>WhatsApp 2</th><th>E-mail</th>
          <th>Situação</th><th>Último envio</th><th>Ações</th>
        </tr></thead>
        <tbody id="corpo"><tr><td colspan="7">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>

<script>
let DADOS = { itens: [], resumo: {} };
const BADGE = { ok: 'ok', invalido: 'bad', inexistente: 'bad', pendente: 'mute' };
const ROTULO = { ok: 'OK', invalido: 'formato inválido', inexistente: 'sem WhatsApp', pendente: 'não verificado' };

function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function texto(v) { return (v === null || v === undefined || v === '') ? '—' : String(v); }
function dataBR(iso) { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString('pt-BR'); }

async function carregarStatus() {
  const s = await (await fetch('/api/contatos/whatsapp/status')).json();
  const i = s.instancia || {};
  document.getElementById('card-instancia').innerHTML = `
    <h3>Instância uazapi <span class="badge ${i.ok ? 'ok' : 'bad'}">${i.ok ? 'conectada' : (i.categoria || 'indisponível')}</span></h3>
    <p class="desc">${esc(i.mensagem || '')}</p>
    <div class="rodape">
      ${s.configurada ? `subdomínio <code>${esc(s.subdominio)}</code>` : '<b>UAZAPI_SUBDOMAIN/UAZAPI_TOKEN não configurados</b>'}
      · trava "só clientes": <b>${s.trava_so_clientes ? 'ligada' : 'DESLIGADA'}</b>
      · números do escritório: ${(s.numeros_escritorio || []).map(esc).join(', ') || 'nenhum (ADMIN_WHATSAPP)'}
      <br>janela ${esc(s.janela)} em dia útil — ${s.pode_enviar_agora ? 'pode enviar agora' : 'fora da janela agora'}
      · teto ${s.enviados_ultima_hora}/${s.teto_por_hora} na última hora · pausa ${s.throttle_s}s · "digitando" ${s.delay_ms}ms
      ${s.webhook && s.webhook.url_mascarada ? `<br>webhook cadastrado: <code>${esc(s.webhook.url_mascarada)}</code>` : ''}
    </div>`;
}

async function carregar() {
  const inativas = document.getElementById('inativas').checked ? '?inativas=1' : '';
  DADOS = await (await fetch('/api/contatos' + inativas)).json();
  const r = DADOS.resumo || {};
  document.getElementById('tiles').innerHTML = [
    ['empresas', 'empresas'], ['com_numero_valido', 'com número válido'], ['verificados_ok', 'verificados OK'],
    ['sem_contato', 'sem contato'], ['invalidos', 'formato inválido'], ['inexistentes', 'sem WhatsApp'],
    ['desativados', 'envio desativado'],
  ].map(([k, l]) => `<div class="tile"><b>${r[k] ?? 0}</b><span>${l}</span></div>`).join('');
  render();
}

function render() {
  const f = (document.getElementById('filtro').value || '').toLowerCase();
  const itens = DADOS.itens.filter(i => !f || (i.razao_social || '').toLowerCase().includes(f) || (i.cnpj || '').includes(f.replace(/\\D/g, '')));
  if (!itens.length) { document.getElementById('corpo').innerHTML = '<tr><td colspan="7">Nenhuma empresa.</td></tr>'; return; }
  document.getElementById('corpo').innerHTML = itens.map(i => `<tr data-id="${i.company_id}">
    <td><b>${esc(i.razao_social)}</b><br><small>${esc(i.cnpj)}${i.empresa_ativa ? '' : ' · inativa'}</small>
        ${i.ativo ? '' : '<br><span class="badge warn">envio desativado</span>'}</td>
    <td><input class="cel" id="w1-${i.company_id}" value="${esc(i.whatsapp || '')}" placeholder="DDD 9xxxx-xxxx"></td>
    <td><input class="cel" id="w2-${i.company_id}" value="${esc(i.whatsapp_2 || '')}" placeholder="sócio (opcional)"></td>
    <td><input class="mail" id="em-${i.company_id}" value="${esc(i.email || '')}" placeholder="e-mail"></td>
    <td><span class="badge ${BADGE[i.verificado_status] || 'mute'}">${ROTULO[i.verificado_status] || i.verificado_status}</span>
        <br><small>${esc(i.verificado_motivo || '')}</small>
        ${i.numero_envio ? `<br><small>envia para ${esc(i.numero_envio)}</small>` : ''}</td>
    <td>${i.ultimo_envio ? `<span class="badge ${i.ultimo_envio.status === 'enviado' ? 'ok' : 'bad'}">${esc(i.ultimo_envio.status)}</span>
        <br><small>${dataBR(i.ultimo_envio.criado_em)} · ${esc(i.ultimo_envio.contexto)}${i.ultimo_envio.erro ? ' · ' + esc(i.ultimo_envio.erro) : ''}</small>` : '—'}</td>
    <td>
      <button class="primario" onclick="salvar(${i.company_id})">Salvar</button>
      <button onclick="verificar(${i.company_id})">Verificar</button>
      <button onclick="testar(${i.company_id})" ${i.numero_envio_ok ? '' : 'disabled'}>Teste</button>
      <button onclick="alternar(${i.company_id}, ${i.ativo ? 'false' : 'true'})">${i.ativo ? 'Desativar' : 'Ativar'}</button>
    </td>
  </tr>`).join('');
}

async function salvar(id, extra) {
  const corpo = Object.assign({
    whatsapp: document.getElementById(`w1-${id}`).value,
    whatsapp_2: document.getElementById(`w2-${id}`).value,
    email: document.getElementById(`em-${id}`).value,
  }, extra || {});
  const r = await fetch(`/api/contatos/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
  const d = await r.json();
  if (!d.success) { alert(d.message); return; }
  await carregar();
}

function alternar(id, ativo) { salvar(id, { ativo }); }

async function verificar(id) {
  await salvar(id);
  const r = await fetch('/api/contatos/verificar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ company_ids: [id] }) });
  const d = await r.json();
  if (d.online_erro) alert('Checagem online não aconteceu: ' + d.online_erro);
  await carregar();
}

async function verificarTodos() {
  if (!confirm('Conferir o formato de todos os números e consultar a uazapi para saber quais têm WhatsApp?')) return;
  const r = await fetch('/api/contatos/verificar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  const d = await r.json();
  alert(`${d.verificados} contato(s) verificado(s).` + (d.online ? ' Checagem online feita.' : '') + (d.online_erro ? `\\nChecagem online: ${d.online_erro}` : ''));
  await carregar();
}

async function testar(id) {
  const i = DADOS.itens.find(x => x.company_id === id);
  if (!confirm(`Enviar uma MENSAGEM DE TESTE real para ${i.numero_envio} (${i.razao_social})?`)) return;
  const r = await fetch('/api/contatos/teste', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ company_id: id }) });
  const d = await r.json();
  alert(d.ok ? `Enviado para ${d.numero}.` : `Não enviado: ${d.motivo}`);
  await carregar(); carregarStatus();
}

function mostrarImportar() { const el = document.getElementById('importar'); el.style.display = el.style.display === 'none' ? '' : 'none'; }

async function importar() {
  let itens;
  try { itens = JSON.parse(document.getElementById('json-lote').value); } catch (e) { alert('JSON inválido: ' + e.message); return; }
  const r = await fetch('/api/contatos/importar', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ itens }) });
  const d = await r.json();
  alert(d.success ? `${d.importados} importado(s). Sem cadastro: ${d.sem_cadastro.length}` + (d.sem_cadastro.length ? `\\n${d.sem_cadastro.join(', ')}` : '') : d.message);
  await carregar();
}

carregarStatus(); carregar();
</script>
</div>
""" + FIM + """
</body>
</html>
"""


@contatos_bp.get('/contatos')
def pagina_contatos():
    return render_template_string(PAGINA)
