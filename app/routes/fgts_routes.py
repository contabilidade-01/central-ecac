"""Tela FGTS Digital da Central: prontidão, parâmetros, agentes e pedidos. Só administrador.

Desvio 27. A tela é provisória no estilo atual (`app/ui.py`); o painel definitivo entra no
novo frontend (fase 4 do plano), consumindo estas mesmas rotas.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template_string, request

from app.extensions import db
from app.fgts_models import FgtsAgente, FgtsEvento, FgtsGuia
from app.integracao_models import FgtsPedido
from app.services.fgts import agentes, guias, parametros
from app.ui import CSS, FIM, lateral

fgts_bp = Blueprint('fgts', __name__)


def _quem() -> str:
    try:
        from app.routes.escritorio import _nome_usuario
        return _nome_usuario() or 'admin'
    except Exception:
        return 'admin'


def _erro(mensagem: str, status: int = 400):
    return jsonify({'success': False, 'message': mensagem}), status


@fgts_bp.get('/api/fgts/estado')
def estado():
    return jsonify({'success': True, **agentes.prontidao()})


@fgts_bp.get('/api/fgts/parametros')
def ler_parametros():
    return jsonify({'success': True, 'parametros': parametros.todos(), 'historico': parametros.historico(30)})


@fgts_bp.put('/api/fgts/parametros')
def gravar_parametros():
    corpo = request.get_json(silent=True) or {}
    try:
        mudaram = parametros.salvar(corpo.get('valores') or {}, _quem())
    except parametros.ParametroInvalido as exc:
        return _erro(str(exc))
    return jsonify({'success': True, 'mudaram': mudaram})


@fgts_bp.get('/api/fgts/agentes')
def listar_agentes():
    itens = FgtsAgente.query.order_by(FgtsAgente.revogado_em.isnot(None), FgtsAgente.id.desc()).all()
    return jsonify({'success': True, 'agentes': [agentes.serializar(a) for a in itens]})


@fgts_bp.post('/api/fgts/agentes')
def criar_agente():
    try:
        agente, token = agentes.criar((request.get_json(silent=True) or {}).get('nome'), _quem())
    except agentes.RecusaAgente as exc:
        return _erro(str(exc), exc.status)
    return jsonify({'success': True, 'agente': agentes.serializar(agente), 'token': token,
                    'aviso': 'Copie o token agora. Ele não será mostrado de novo.'}), 201


@fgts_bp.delete('/api/fgts/agentes/<int:agente_id>')
def revogar_agente(agente_id: int):
    if not agentes.revogar(agente_id, _quem()):
        return _erro('Agente não encontrado ou já revogado.', 404)
    return jsonify({'success': True})


@fgts_bp.get('/api/fgts/pedidos')
def listar_pedidos():
    from app.services import fgts_pedidos_service as servico
    consulta = FgtsPedido.query
    if request.args.get('status'):
        consulta = consulta.filter_by(status=request.args['status'][:20])
    itens = consulta.order_by(FgtsPedido.id.desc()).limit(100).all()
    return jsonify({'success': True, 'pedidos': [
        {**servico.serializar(p), 'cnpj': p.cnpj, 'origem': p.origem, 'executor': p.executor,
         'agente_id': p.agente_id, 'solicitado_por': p.solicitado_por} for p in itens]})


@fgts_bp.post('/api/fgts/pedidos/<int:pedido_id>/conferido')
def conferido(pedido_id: int):
    corpo = request.get_json(silent=True) or {}
    if 'guia_saiu' not in corpo:
        return _erro('Informe se a guia saiu no portal.')
    try:
        agentes.marcar_verificado(pedido_id, bool(corpo['guia_saiu']), _quem())
    except agentes.RecusaAgente as exc:
        return _erro(str(exc), exc.status)
    return jsonify({'success': True})


@fgts_bp.get('/api/fgts/guias')
def listar_guias():
    consulta = FgtsGuia.query
    cnpj = ''.join(ch for ch in (request.args.get('cnpj') or '') if ch.isdigit())
    if cnpj:
        consulta = consulta.filter_by(cnpj=cnpj)
    return jsonify({'success': True, 'guias': [guias.serializar(x) for x in
                                               consulta.order_by(FgtsGuia.id.desc()).limit(200).all()]})


@fgts_bp.get('/api/fgts/eventos')
def listar_eventos():
    itens = FgtsEvento.query.order_by(FgtsEvento.id.desc()).limit(100).all()
    return jsonify({'success': True, 'eventos': [
        {'tipo': e.tipo, 'pedido_id': e.pedido_id, 'agente_id': e.agente_id, 'cnpj': e.cnpj,
         'mensagem': e.mensagem, 'em': e.em.isoformat()} for e in itens]})


PAGINA = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>FGTS Digital · Central e-CAC</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>{{ css|safe }}
 .luzes{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px}
 .luz{display:flex;gap:8px;align-items:center;background:var(--superficie);border:1px solid var(--borda);border-radius:12px;padding:10px 14px;font-size:13px}
 .luz i{width:10px;height:10px;border-radius:50%;background:#94a3b8}.luz i.ok{background:#16a34a}.luz i.alerta{background:#d97706}.luz i.erro{background:#dc2626}
 .abas{display:flex;gap:6px;margin-bottom:14px}.abas button.ativa{background:var(--primaria);color:#fff;border-color:var(--primaria)}
 .grupo{margin-bottom:18px}.grupo h3{font-size:13px;margin:0 0 8px;color:var(--suave);text-transform:uppercase;letter-spacing:.05em}
 .campo{display:grid;grid-template-columns:minmax(220px,1fr) 220px minmax(120px,auto);gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--borda);font-size:13px}
 .campo small{color:var(--suave)}.token{font-family:ui-monospace,Consolas,monospace;background:#0f172a;color:#e2e8f0;padding:10px;border-radius:8px;word-break:break-all}
 @media(max-width:760px){.campo{grid-template-columns:1fr}}
</style></head><body>{{ menu|safe }}
<div class="wrap">
<h1>FGTS Digital</h1><p class="sub">Prontidão da automação, parâmetros, agentes e pedidos. Datas, valores e encargos vêm sempre do portal.</p>
<div class="luzes" id="luzes"></div>
<div class="abas"><button class="ativa" data-aba="parametros">Configurações</button><button data-aba="agentes">Agentes</button><button data-aba="pedidos">Pedidos</button></div>
<div id="msg"></div>
<section class="card" id="aba-parametros"></section>
<section class="card" id="aba-agentes" hidden></section>
<section class="card" id="aba-pedidos" hidden></section>
</div>
<script>
const $=s=>document.querySelector(s);
const esc=t=>String(t==null?'':t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(m,u,b){try{const r=await fetch(u,{method:m,headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});
 const t=await r.text();let j;try{j=JSON.parse(t)}catch(e){return{success:r.ok,message:'HTTP '+r.status}}return j}catch(e){return{success:false,message:'Sem conexão: '+e.message}}}
function aviso(t,erro){$('#msg').innerHTML=t?`<div class="msg ${erro?'bad':'ok'}">${esc(t)}</div>`:''}
document.querySelectorAll('.abas button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.abas button').forEach(x=>x.classList.toggle('ativa',x===b));
 ['parametros','agentes','pedidos'].forEach(a=>$('#aba-'+a).hidden=a!==b.dataset.aba);({parametros:carregarParametros,agentes:carregarAgentes,pedidos:carregarPedidos})[b.dataset.aba]()});
async function luzes(){const e=await api('GET','/api/fgts/estado');if(!e.success)return;
 const l=(c,t)=>`<span class="luz"><i class="${c}"></i>${esc(t)}</span>`;
 $('#luzes').innerHTML=l(e.habilitado?'ok':'erro',e.habilitado?'Módulo ligado':'Módulo desligado')+l('ok','Executor: '+(e.executor==='agente'?'agente no PC':'robô na VPS'))
 +(e.executor==='agente'?l(e.agentes_online?'ok':'erro',e.agentes_online+' agente(s) online')+l(e.agentes_com_login?'ok':'alerta',e.agentes_com_login?'Login gov.br ativo':'Nenhum agente com login'):'')}
let PARAMS=[];
async function carregarParametros(){const r=await api('GET','/api/fgts/parametros');if(!r.success){aviso(r.message,true);return}PARAMS=r.parametros;
 const grupos={};PARAMS.forEach(p=>(grupos[p.grupo]=grupos[p.grupo]||[]).push(p));
 const campo=p=>{const id='p_'+p.chave;let c;
  if(p.tipo==='bool')c=`<input type="checkbox" id="${id}" ${p.valor?'checked':''}>`;
  else if(p.tipo==='escolha')c=`<select id="${id}">${p.opcoes.map(o=>`<option ${o===p.valor?'selected':''}>${esc(o)}</option>`).join('')}</select>`;
  else if(p.tipo==='lista_cnpj')c=`<input id="${id}" value="${esc((p.valor||[]).join(', '))}">`;
  else c=`<input id="${id}" value="${esc(p.valor)}" ${p.tipo==='int'?`type="number" min="${p.minimo??''}" max="${p.maximo??''}"`:''}>`;
  return `<div class="campo"><label for="${id}">${esc(p.rotulo)}${p.ajuda?`<br><small>${esc(p.ajuda)}</small>`:''}</label>${c}<small>origem: ${esc(p.origem)}</small></div>`};
 $('#aba-parametros').innerHTML=Object.entries(grupos).map(([g,ps])=>`<div class="grupo"><h3>${esc(g)}</h3>${ps.map(campo).join('')}</div>`).join('')
  +`<button class="azul" onclick="salvarParametros()">Salvar alterações</button>`
  +`<h2 style="margin-top:22px">Histórico</h2><div class="table-wrap"><table><tr><th>Quando</th><th>Parâmetro</th><th>De</th><th>Para</th><th>Por</th></tr>${r.historico.map(h=>`<tr><td>${esc(new Date(h.em+'Z').toLocaleString('pt-BR'))}</td><td>${esc(h.rotulo)}</td><td>${esc(JSON.stringify(h.anterior))}</td><td>${esc(JSON.stringify(h.novo))}</td><td>${esc(h.por)}</td></tr>`).join('')||'<tr><td colspan=5>Nenhuma alteração ainda.</td></tr>'}</table></div>`}
async function salvarParametros(){const valores={};PARAMS.forEach(p=>{const el=$('#p_'+p.chave);if(!el)return;
 const v=p.tipo==='bool'?el.checked:el.value;const atual=p.tipo==='lista_cnpj'?(p.valor||[]).join(', '):p.valor;if(String(v)!==String(atual))valores[p.chave]=v});
 if(!Object.keys(valores).length){aviso('Nada mudou.');return}
 const r=await api('PUT','/api/fgts/parametros',{valores});aviso(r.success?`${r.mudaram.length} parâmetro(s) salvo(s).`:r.message,!r.success);if(r.success){carregarParametros();luzes()}}
async function carregarAgentes(){const r=await api('GET','/api/fgts/agentes');if(!r.success){aviso(r.message,true);return}
 $('#aba-agentes').innerHTML=`<p class="dica">Cada PC do escritório que roda o agente precisa de um token. O token aparece uma única vez.</p>
 <div class="grade"><input id="nomeAgente" placeholder="Nome do PC, por exemplo Recepção"><button class="azul" onclick="criarAgente()">Gerar token</button></div><div id="novoToken"></div>
 <div class="table-wrap" style="margin-top:14px"><table><tr><th>Agente</th><th>Situação</th><th>Login gov.br</th><th>Versão</th><th>Último sinal</th><th></th></tr>
 ${r.agentes.map(a=>`<tr><td><b>${esc(a.nome)}</b><br><small>${esc(a.maquina||'')}</small></td><td><span class="badge ${a.revogado?'mute':a.online?'ok':'bad'}">${a.revogado?'revogado':a.online?'online':'offline'}</span></td>
 <td>${a.login_ativo?'<span class="badge ok">ativo</span>':'<span class="badge mute">sem login</span>'}</td><td>${esc(a.versao||'—')}</td><td>${a.ultimo_sinal_em?esc(new Date(a.ultimo_sinal_em+'Z').toLocaleString('pt-BR')):'—'}</td>
 <td>${a.revogado?'':`<button class="perigo" onclick="revogar(${a.id})">Revogar</button>`}</td></tr>`).join('')||'<tr><td colspan=6>Nenhum agente cadastrado.</td></tr>'}</table></div>`}
async function criarAgente(){const r=await api('POST','/api/fgts/agentes',{nome:$('#nomeAgente').value});if(!r.success){aviso(r.message,true);return}
 await carregarAgentes();$('#novoToken').innerHTML=`<div class="aviso" style="margin-top:12px">${esc(r.aviso)}</div><div class="token">${esc(r.token)}</div>`}
async function revogar(id){const r=await api('DELETE','/api/fgts/agentes/'+id);aviso(r.success?'Agente revogado.':r.message,!r.success);carregarAgentes();luzes()}
const ROT={fila:'Na fila',executando:'Executando',aguardando_captcha:'Aguardando verificação',aguardando_escolha:'Aguardando escolha',concluido:'Concluído',erro:'Erro',verificar_portal:'Conferir no portal'};
async function carregarPedidos(){const r=await api('GET','/api/fgts/pedidos');if(!r.success){aviso(r.message,true);return}
 $('#aba-pedidos').innerHTML=`<div class="table-wrap"><table><tr><th>#</th><th>CNPJ</th><th>Ação</th><th>Situação</th><th>Executor</th><th>Origem</th><th>Criado</th><th></th></tr>
 ${r.pedidos.map(p=>`<tr><td>${p.id}</td><td>${esc(p.cnpj)}</td><td>${esc(p.acao)}${p.competencia?' '+esc(p.competencia):''}</td>
 <td><span class="badge ${p.status==='concluido'?'ok':p.status==='erro'?'bad':p.status==='verificar_portal'?'warn':'mute'}">${esc(ROT[p.status]||p.status)}</span><br><small>${esc(p.mensagem||'')}</small></td>
 <td>${esc(p.executor||'vps')}</td><td>${esc(p.origem)}</td><td>${p.criado_em?esc(new Date(p.criado_em+'Z').toLocaleString('pt-BR')):''}</td>
 <td>${p.status==='verificar_portal'?`<button onclick="conferir(${p.id},true)">A guia saiu</button><button onclick="conferir(${p.id},false)">Não saiu</button>`:''}</td></tr>`).join('')||'<tr><td colspan=8>Nenhum pedido.</td></tr>'}</table></div>`}
async function conferir(id,saiu){const r=await api('POST',`/api/fgts/pedidos/${id}/conferido`,{guia_saiu:saiu});aviso(r.success?'Registrado.':r.message,!r.success);carregarPedidos()}
luzes();carregarParametros();setInterval(luzes,30000);
</script>{{ fim|safe }}</body></html>"""


@fgts_bp.get('/fgts')
def pagina():
    return render_template_string(PAGINA, css=CSS, menu=lateral('fgts'), fim=FIM)
