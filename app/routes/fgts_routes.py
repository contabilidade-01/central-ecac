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


@fgts_bp.get('/api/fgts/empresas')
def listar_empresas():
    """Empresas ativas com a última posição lida do FGTS (custo zero, sem ir ao portal)."""
    from app.models import Company
    from app.services import fgts_pedidos_service as servico
    from app.services.fgts import empresas as fgts_empresas
    fgts_empresas.sincronizar_se_velho()
    busca = (request.args.get('q') or '').strip().lower()
    todas = request.args.get('todas') == '1'
    saida = []
    for c in Company.query.filter_by(ativo=True).order_by(Company.razao_social).all():
        if busca and busca not in c.razao_social.lower() and busca not in c.cnpj:
            continue
        sit = fgts_empresas.situacao(c.id)
        if not todas and not sit['aplicavel']:
            continue
        consulta = servico.ultima_consulta_hoje(c.cnpj)
        aberto = servico.pedido_aberto(c.cnpj)
        comps = servico.resultado(consulta).get('competencias', []) if consulta else []
        saida.append({**sit, 'company_id': c.id, 'razao_social': c.razao_social, 'cnpj': c.cnpj,
                      'consultado_em': consulta.concluido_em.isoformat() if consulta and consulta.concluido_em else None,
                      'vencidas': sum(1 for x in comps if x.get('situacao') == 'vencida'),
                      'total_aberto': round(sum(x.get('total') or 0 for x in comps), 2),
                      'pedido_aberto': servico.serializar(aberto) if aberto else None})
        if len(saida) >= 200:
            break
    return jsonify({'success': True, 'empresas': saida})


@fgts_bp.post('/api/fgts/empresas/sincronizar')
def sincronizar_empresas():
    from app.services.fgts import empresas as fgts_empresas
    r = fgts_empresas.sincronizar()
    return (jsonify({'success': True, **r}) if r['ok'] else _erro(r['motivo'], 502))


@fgts_bp.put('/api/fgts/empresas/<int:company_id>/excecao')
def excecao(company_id: int):
    """`{valor: true|false|null}`: liga, desliga ou volta a seguir o portal."""
    from app.models import Company
    from app.services.fgts import empresas as fgts_empresas
    c = db.session.get(Company, company_id)
    if not c:
        return _erro('Empresa não encontrada.', 404)
    valor = (request.get_json(silent=True) or {}).get('valor')
    if valor not in (True, False, None):
        return _erro('Use true, false ou null.')
    fgts_empresas.definir_excecao(c.id, c.cnpj, valor, _quem())
    return jsonify({'success': True, **fgts_empresas.situacao(c.id)})


@fgts_bp.get('/api/fgts/empresas/<int:company_id>')
def ver_empresa(company_id: int):
    from app.models import Company
    from app.services import fgts_pedidos_service as servico
    c = db.session.get(Company, company_id)
    if not c:
        return _erro('Empresa não encontrada.', 404)
    consulta = servico.ultima_consulta_hoje(c.cnpj)
    aberto = servico.pedido_aberto(c.cnpj)
    recentes = FgtsGuia.query.filter_by(cnpj=c.cnpj).order_by(FgtsGuia.id.desc()).limit(20).all()
    from app.services.fgts import empresas as fgts_empresas
    return jsonify({'success': True, 'empresa': {'company_id': c.id, 'razao_social': c.razao_social, 'cnpj': c.cnpj,
                                                 **fgts_empresas.situacao(c.id)},
                    'consulta': servico.serializar(consulta) if consulta else None,
                    'pedido_aberto': servico.serializar(aberto) if aberto else None,
                    'guias': [guias.serializar(g) for g in recentes]})


@fgts_bp.post('/api/fgts/empresas/<int:company_id>/pedidos')
def pedir(company_id: int):
    """Admin pede pela tela: `{acao: consultar}` ou `{acao: emitir, competencias, data_pagamento?}`."""
    from app.models import Company
    from app.services import fgts_pedidos_service as servico
    c = db.session.get(Company, company_id)
    if not c or not c.ativo:
        return _erro('Empresa não encontrada ou inativa.', 404)
    corpo = request.get_json(silent=True) or {}
    try:
        pedido = servico.criar(company_id=c.id, cnpj=c.cnpj, acao=str(corpo.get('acao') or 'consultar'),
                               competencia=corpo.get('competencias'), link_id=None, origem='central',
                               solicitado_por=f'central:{_quem()}', data_pagamento=corpo.get('data_pagamento'))
    except servico.PedidoRecusado as exc:
        return _erro(str(exc), exc.status)
    return jsonify({'success': True, 'pedido': servico.serializar(pedido)})


@fgts_bp.get('/api/fgts/guias/<int:guia_id>/pdf')
def baixar_guia(guia_id: int):
    from pathlib import Path
    from flask import send_file
    from app.services import fgts_pedidos_service as servico
    g = db.session.get(FgtsGuia, guia_id)
    if not g or not Path(g.pdf_path).is_file():
        return _erro('PDF não encontrado.', 404)
    if Path(g.pdf_path).resolve().parent != servico.pasta_pdfs(g.cnpj).resolve():
        return _erro('PDF fora da pasta da empresa.', 403)
    return send_file(g.pdf_path, mimetype='application/pdf', download_name=f'FGTS_{g.cnpj}_{g.competencia}.pdf')


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
<div class="abas"><button class="ativa" data-aba="empresas">Empresas</button><button data-aba="parametros">Configurações</button><button data-aba="agentes">Agentes</button><button data-aba="pedidos">Pedidos</button></div>
<div id="msg"></div>
<section class="card" id="aba-empresas"></section>
<section class="card" id="aba-parametros" hidden></section>
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
 ['empresas','parametros','agentes','pedidos'].forEach(a=>$('#aba-'+a).hidden=a!==b.dataset.aba);({empresas:carregarEmpresas,parametros:carregarParametros,agentes:carregarAgentes,pedidos:carregarPedidos})[b.dataset.aba]()});
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

const brl=v=>v==null?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
const comp=c=>c?c.slice(4)+'/'+c.slice(0,4):'';
let EMP=null,TIMER=null,TODAS=false;
async function sincronizar(){aviso('Consultando o portal…');const r=await api('POST','/api/fgts/empresas/sincronizar');aviso(r.success?`${r.empresas_com_funcionarios} empresa(s) com funcionários.`:r.message,!r.success);carregarEmpresas()}
async function excecao(v){const r=await api('PUT',`/api/fgts/empresas/${EMP}/excecao`,{valor:v});aviso(r.success?'Exceção registrada.':r.message,!r.success);abrirEmpresa(EMP)}
async function carregarEmpresas(){const q=($('#buscaEmp')||{}).value||'';const r=await api('GET','/api/fgts/empresas?q='+encodeURIComponent(q)+(TODAS?'&todas=1':''));if(!r.success){aviso(r.message,true);return}
 $('#aba-empresas').innerHTML=`<div class="grade"><input id="buscaEmp" placeholder="Buscar empresa ou CNPJ" value="${esc(q)}" onkeydown="if(event.key==='Enter')carregarEmpresas()"><button onclick="carregarEmpresas()">Buscar</button><label style="font-size:13px"><input type="checkbox" id="todasEmp" ${TODAS?'checked':''} onchange="TODAS=this.checked;carregarEmpresas()"> mostrar também empresas sem funcionário</label><button onclick="sincronizar()">Atualizar funcionários do portal</button></div>
 <div class="table-wrap" style="margin-top:12px"><table><tr><th>Empresa</th><th>Última consulta</th><th>Vencidas</th><th>Em aberto</th><th></th></tr>
 ${r.empresas.map(e=>`<tr><td><b>${esc(e.razao_social)}</b><br><small>${esc(e.cnpj)} · ${esc(e.motivo)}</small></td><td>${e.consultado_em?esc(new Date(e.consultado_em+'Z').toLocaleString('pt-BR')):'—'}</td>
 <td>${e.vencidas?`<span class="badge bad">${e.vencidas}</span>`:'—'}</td><td>${e.consultado_em?brl(e.total_aberto):'—'}</td><td><button class="azul" onclick="abrirEmpresa(${e.company_id})">Abrir</button></td></tr>`).join('')||'<tr><td colspan=5>Nenhuma empresa.</td></tr>'}</table></div><div id="painelEmp"></div>`}
async function abrirEmpresa(id){EMP=id;clearTimeout(TIMER);const r=await api('GET','/api/fgts/empresas/'+id);if(!r.success){aviso(r.message,true);return}
 const e=r.empresa,c=r.consulta,a=r.pedido_aberto;let h=`<h2 style="margin-top:20px">${esc(e.razao_social)} <small>${esc(e.cnpj)}</small></h2><p class="dica">FGTS ${e.aplicavel?'<span class="badge ok">se aplica</span>':'<span class="badge mute">não se aplica</span>'} · ${esc(e.motivo)}
 · Exceção: <button onclick="excecao(true)">ligar</button><button onclick="excecao(false)">desligar</button>${e.excecao!==null?'<button onclick="excecao(null)">seguir o portal</button>':''}</p>`;
 if(!e.aplicavel){$('#painelEmp').innerHTML=h;return}
 if(a){h+=`<div class="aviso">Pedido em andamento: <b>${esc(a.acao)}</b> · ${esc(ROT[a.status]||a.status)} ${a.mensagem?'· '+esc(a.mensagem):''}</div>`;TIMER=setTimeout(()=>abrirEmpresa(id),4000)}
 else h+=`<button class="azul" onclick="pedir('consultar')">Buscar pendências no FGTS Digital</button>`;
 if(c&&c.competencias.length){h+=`<p class="dica" style="margin-top:12px">Consulta de ${esc(new Date(c.concluido_em+'Z').toLocaleString('pt-BR'))}. Marque as competências e gere as guias para pagar hoje.</p>
  <div class="table-wrap"><table><tr><th></th><th>Competência</th><th>Vencimento</th><th>Situação</th><th>Total</th></tr>
  ${c.competencias.map(x=>`<tr><td><input type="checkbox" class="marca" value="${esc(x.competencia)}" ${x.tem_guia&&x.situacao==='em_dia'?'':''}></td><td>${comp(x.competencia)}</td><td>${x.vencimento?esc(x.vencimento.split('-').reverse().join('/')):'—'}</td>
  <td><span class="badge ${x.situacao==='vencida'?'bad':'ok'}">${x.situacao==='vencida'?'Vencida':'Em dia'}</span>${x.tem_guia?' <span class="badge mute">já tem guia</span>':''}</td><td>${brl(x.total)}</td></tr>`).join('')}</table></div>
  ${a?'':`<button class="azul" style="margin-top:10px" onclick="gerar()">Gerar guias das competências marcadas</button>`}`}
 else if(c)h+=`<p class="dica" style="margin-top:12px">Nenhum débito em aberto na última consulta.</p>`;
 if(r.guias.length)h+=`<h2 style="margin-top:18px">Guias emitidas</h2><div class="table-wrap"><table><tr><th>Competência</th><th>Número</th><th>Valor</th><th>Pagar até</th><th>Pix</th><th></th></tr>
  ${r.guias.map(g=>`<tr><td>${comp(g.competencia)}</td><td>${esc(g.numero)}</td><td>${brl(g.valor)}</td><td>${g.vencimento?esc(g.vencimento.split('-').reverse().join('/')):'—'}</td><td>${g.tem_pix?'<span class="badge ok">sim</span>':'<span class="badge mute">não</span>'}</td><td><a href="/api/fgts/guias/${g.id}/pdf" target="_blank">PDF</a></td></tr>`).join('')}</table></div>`;
 $('#painelEmp').innerHTML=h}
async function pedir(acao,competencias){const r=await api('POST',`/api/fgts/empresas/${EMP}/pedidos`,{acao,competencias});aviso(r.success?(acao==='consultar'?'Busca enviada ao agente.':'Emissão enviada ao agente.'):r.message,!r.success);abrirEmpresa(EMP)}
function gerar(){const m=[...document.querySelectorAll('.marca:checked')].map(x=>x.value);if(!m.length){aviso('Marque ao menos uma competência.',true);return}
 if(!window.confirm(`Gerar ${m.length} guia(s) REAL(IS) no FGTS Digital para pagar hoje: ${m.map(comp).join(', ')}?`))return;pedir('emitir',m)}
luzes();carregarEmpresas();setInterval(luzes,30000);
</script>{{ fim|safe }}</body></html>"""


@fgts_bp.get('/fgts')
def pagina():
    return render_template_string(PAGINA, css=CSS, menu=lateral('fgts'), fim=FIM)
