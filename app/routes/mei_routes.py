"""Seção MEI: quem é MEI, quais guias o empresário quer pagar e a geração só dessas.

Tela `/mei` e API `/api/mei/*`. Só gera guia (chamada paga) do que foi marcado, com a data
de pagamento escolhida; guia guardada e ainda válida é reaproveitada sem custo.
"""
import re
from datetime import date, datetime

from flask import Blueprint, jsonify, render_template_string, request

from app.extensions import db
from app.integracao_models import DasEmissao, MeiEnvio, MeiGuiaSelecionada
from app.models import Company
from app.services import mei_service, nescon_service
from app.services.das_emissao_service import EmissaoBloqueada, emitir
from app.ui import CSS, FIM, lateral

mei_bp = Blueprint('mei', __name__)

_COMPETENCIA = re.compile(r'^\d{6}$')


def _admin_e_nome():
    from app.routes.escritorio import _e_admin, _nome_usuario
    return _e_admin(), _nome_usuario()


def _competencia_ok(valor) -> str:
    valor = re.sub(r'\D', '', str(valor or ''))
    if not _COMPETENCIA.match(valor) or not (1 <= int(valor[4:]) <= 12):
        raise ValueError('Competência inválida (use AAAAMM).')
    return valor


def _data_ok(valor):
    """AAAAMMDD ou AAAA-MM-DD → AAAAMMDD; vazio → None. Não aceita data no passado."""
    valor = re.sub(r'\D', '', str(valor or ''))
    if not valor:
        return None
    try:
        data = datetime.strptime(valor, '%Y%m%d').date()
    except ValueError:
        raise ValueError('Data de pagamento inválida.')
    if data < date.today():
        raise ValueError('A data de pagamento não pode estar no passado.')
    return valor


def _linhas(competencia: str):
    ids = mei_service.ids_mei()
    if not ids:
        return []
    marcas = {m.company_id: m for m in MeiGuiaSelecionada.query.filter_by(
        competencia=competencia).all()}
    envios = {e.company_id: e for e in MeiEnvio.query.filter_by(competencia=competencia).all()}
    linhas = []
    for c in Company.query.filter(Company.id.in_(ids)).order_by(Company.razao_social.asc()):
        m = marcas.get(c.id)
        linhas.append({
            'company_id': c.id, 'razao_social': c.razao_social, 'cnpj': c.cnpj,
            'ativo': bool(c.ativo),
            'pagar': bool(m), 'status': m.status if m else None,
            'erro': m.erro if m else None, 'data_pagamento': m.data_pagamento if m else None,
            'das_emissao_id': m.das_emissao_id if m else None,
            'envio': envios[c.id].status if c.id in envios else None,
            'envio_motivo': envios[c.id].motivo if c.id in envios else None,
        })
    return linhas


@mei_bp.get('/api/mei/empresas')
def api_empresas():
    try:
        competencia = _competencia_ok(request.args.get('competencia'))
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    candidatas = [{'company_id': c.id, 'razao_social': c.razao_social, 'cnpj': c.cnpj}
                  for c in Company.query.filter_by(ativo=True).order_by(Company.razao_social)
                  if c.id not in mei_service.ids_mei()]
    return jsonify({'success': True, 'competencia': competencia,
                    'empresas': _linhas(competencia), 'candidatas': candidatas})


@mei_bp.put('/api/mei/selecao')
def api_selecao():
    """Marca/desmarca a guia que o empresário quer pagar. Corpo: {company_id, competencia,
    pagar, data_pagamento?}."""
    dados = request.get_json(silent=True) or {}
    try:
        competencia = _competencia_ok(dados.get('competencia'))
        data = _data_ok(dados.get('data_pagamento'))
        company_id = int(dados.get('company_id'))
    except (ValueError, TypeError) as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    if not mei_service.e_mei(company_id):
        return jsonify({'success': False, 'message': 'Empresa não está marcada como MEI.'}), 409
    _, nome = _admin_e_nome()
    marca = MeiGuiaSelecionada.query.filter_by(company_id=company_id,
                                               competencia=competencia).first()
    if not dados.get('pagar'):
        if marca:
            db.session.delete(marca)
            db.session.commit()
        return jsonify({'success': True, 'pagar': False})
    if not marca:
        marca = MeiGuiaSelecionada(company_id=company_id, competencia=competencia)
        db.session.add(marca)
    marca.data_pagamento = data
    if marca.status != 'gerada':
        marca.status, marca.erro = 'pendente', None
    marca.marcado_por = nome[:120] or None
    db.session.commit()
    return jsonify({'success': True, 'pagar': True, 'status': marca.status})


@mei_bp.post('/api/mei/gerar')
def api_gerar():
    """Gera o DASMEI só das guias marcadas e ainda não geradas. Corpo: {competencia,
    data_pagamento?}. A data do corpo vale para quem não tem data própria."""
    dados = request.get_json(silent=True) or {}
    try:
        competencia = _competencia_ok(dados.get('competencia'))
        data_padrao = _data_ok(dados.get('data_pagamento'))
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    _, nome = _admin_e_nome()
    resultados = []
    marcas = MeiGuiaSelecionada.query.filter_by(competencia=competencia).filter(
        MeiGuiaSelecionada.status != 'gerada').all()
    for marca in marcas:
        company = db.session.get(Company, marca.company_id)
        if not company or not mei_service.e_mei(company.id):
            continue
        try:
            emissao, reuso = emitir(company.cnpj, 'MEI', competencia,
                                    data_consolidacao=marca.data_pagamento or data_padrao,
                                    origem='mei', solicitado_por=nome or None)
            marca.status, marca.erro, marca.das_emissao_id = 'gerada', None, emissao.id
            resultados.append({'company_id': company.id, 'status': 'gerada', 'reuso': reuso})
        except EmissaoBloqueada as exc:
            marca.status, marca.erro = 'erro', str(exc)[:300]
            resultados.append({'company_id': company.id, 'status': 'erro', 'erro': str(exc)})
        except Exception as exc:  # falha de rede/SERPRO não derruba o lote
            db.session.rollback()
            marca = db.session.get(MeiGuiaSelecionada, marca.id)
            marca.status, marca.erro = 'erro', f'{type(exc).__name__}: {exc}'[:300]
            resultados.append({'company_id': company.id, 'status': 'erro', 'erro': marca.erro})
        db.session.commit()
    return jsonify({'success': True, 'resultados': resultados})


@mei_bp.get('/api/mei/vinculo')
def api_vinculo():
    """Cada MEI está cadastrado no Nescon Clientes e tem WhatsApp válido? Sem custo SERPRO."""
    if not nescon_service.configurado():
        return jsonify({'success': False,
                        'message': 'Nescon não configurado (NESCON_API_URL e NESCON_INTERNAL_TOKEN).'}), 503
    saida = []
    for c in Company.query.filter(Company.id.in_(mei_service.ids_mei())).order_by(Company.razao_social):
        v = nescon_service.vinculo(c.cnpj)
        saida.append({'company_id': c.id, 'vinculada': bool(v.get('vinculada')),
                      'whatsapp_valido': bool(v.get('whatsapp_valido')),
                      'motivo': v.get('motivo') if v.get('status') != 'erro_rede' else v.get('motivo')})
    return jsonify({'success': True, 'vinculos': saida})


@mei_bp.post('/api/mei/enviar')
def api_enviar():
    """Envia pelo WhatsApp (via Nescon) as guias já geradas. Corpo: {competencia, company_ids?,
    forcar?}. Quem já foi enviada não vai de novo, a não ser com `forcar` (reenvio de falha)."""
    from app.services.das_emissao_service import ler_pdf
    dados = request.get_json(silent=True) or {}
    try:
        competencia = _competencia_ok(dados.get('competencia'))
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    if not nescon_service.configurado():
        return jsonify({'success': False,
                        'message': 'Nescon não configurado (NESCON_API_URL e NESCON_INTERNAL_TOKEN).'}), 503
    _, nome = _admin_e_nome()
    forcar = bool(dados.get('forcar'))
    ids = {int(i) for i in (dados.get('company_ids') or []) if str(i).isdigit()}
    resultados = []
    q = MeiGuiaSelecionada.query.filter_by(competencia=competencia, status='gerada')
    for marca in q.all():
        if ids and marca.company_id not in ids:
            continue
        company = db.session.get(Company, marca.company_id)
        emissao = db.session.get(DasEmissao, marca.das_emissao_id) if marca.das_emissao_id else None
        if not company or not emissao or not mei_service.e_mei(company.id):
            continue
        envio = MeiEnvio.query.filter_by(company_id=company.id, competencia=competencia).first()
        if envio and envio.status in ('enviada', 'na_fila') and not forcar:
            continue
        pdf = ler_pdf(emissao)
        if not pdf:
            r = {'status': 'falhou', 'motivo': 'PDF da guia indisponível no disco'}
        else:
            ref = f'mei:{company.cnpj}:{competencia}:{emissao.id}'
            r = nescon_service.enviar_guia(
                company.cnpj, competencia, pdf, ref, vencimento=emissao.vencimento,
                valor=emissao.valor_total, forcar=forcar)
        status = r.get('status') or ('enviada' if r.get('ok') else 'falhou')
        if not envio:
            envio = MeiEnvio(company_id=company.id, competencia=competencia,
                             external_ref=f'mei:{company.cnpj}:{competencia}:{emissao.id}')
            db.session.add(envio)
        envio.external_ref = f'mei:{company.cnpj}:{competencia}:{emissao.id}'
        envio.status, envio.motivo = status, (r.get('motivo') or None) and str(r.get('motivo'))[:300]
        envio.enviado_por = nome[:120] or None
        db.session.commit()
        resultados.append({'company_id': company.id, 'status': status, 'motivo': envio.motivo})
    return jsonify({'success': True, 'resultados': resultados})


@mei_bp.get('/api/mei/<int:emissao_id>/pdf')
def api_pdf(emissao_id: int):
    from flask import Response
    from app.services.das_emissao_service import ler_pdf
    emissao = db.session.get(DasEmissao, emissao_id)
    if not emissao or emissao.tipo != 'MEI':
        return jsonify({'success': False, 'message': 'Guia não encontrada.'}), 404
    pdf = ler_pdf(emissao)
    if not pdf:
        return jsonify({'success': False, 'message': 'PDF indisponível.'}), 404
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'inline; filename=DAS_MEI_{emissao.cnpj}_{emissao.periodo_apuracao}.pdf'})


@mei_bp.put('/api/mei/empresa/<int:company_id>')
def api_marcar_mei(company_id: int):
    """Admin: inclui/retira a empresa da seção MEI."""
    from app.routes.escritorio import _e_admin
    if not _e_admin():
        return jsonify({'success': False, 'message': 'Só o administrador marca MEI.'}), 403
    if not db.session.get(Company, company_id):
        return jsonify({'success': False, 'message': 'Empresa não encontrada.'}), 404
    dados = request.get_json(silent=True) or {}
    _, nome = _admin_e_nome()
    mei_service.definir(company_id, bool(dados.get('eh_mei')), nome)
    return jsonify({'success': True})


PAGINA = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>MEI · Central e-CAC</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>{{ css|safe }}
 .linha{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
 table{width:100%;border-collapse:collapse;background:var(--superficie);border-radius:var(--raio);overflow:hidden;box-shadow:var(--sombra)}
 th,td{padding:10px 12px;border-bottom:1px solid var(--borda);text-align:left;font-size:13.5px}
 .tag{padding:2px 8px;border-radius:99px;font-size:12px}
 .gerada{background:var(--ok-bg);color:var(--ok-tx)}.erro{background:var(--erro-bg);color:var(--erro-tx)}
 .pendente{background:var(--alerta-bg);color:var(--alerta-tx)}
 button{padding:8px 14px;border-radius:9px;border:0;background:var(--primaria);color:#fff;cursor:pointer}
 input,select{padding:7px;border:1px solid var(--borda);border-radius:8px}
</style></head><body><div class="app">{{ menu|safe }}
<div style="padding:22px">
<h2>MEI</h2>
<p style="color:var(--suave)">Marque as guias que o empresário quer pagar. Só elas são geradas (chamada paga). Guia já guardada e válida é reaproveitada sem custo.</p>
<div class="linha">
 <label>Competência <input id="comp" type="month"></label>
 <label>Data de pagamento <input id="data" type="date"></label>
 <button onclick="gerar()">Gerar guias marcadas</button>
 <button onclick="enviar(false)">Enviar por WhatsApp</button>
 <button onclick="enviar(true)" title="Reenvia as que falharam">Reenviar falhas</button>
 <button onclick="vinculo()">Verificar vínculo no Nescon</button>
 <span id="msg" style="color:var(--suave)"></span>
</div>
<table><thead><tr><th>Pagar</th><th>Empresa</th><th>CNPJ</th><th>Situação</th><th>Guia</th><th>Envio</th><th>Vínculo</th></tr></thead><tbody id="corpo"></tbody></table>
<div class="linha" id="admin" style="margin-top:18px">
 <select id="cand"></select><button onclick="incluir()">Incluir como MEI (admin)</button>
</div>
</div></div>
<script>
const $=id=>document.getElementById(id);
const esc=t=>String(t==null?'':t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const comp=()=> $('comp').value.replace('-','');
async function api(m,u,b){
 try{
  const r=await fetch(u,{method:m,headers:{'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined});
  const t=await r.text();let j;
  try{j=JSON.parse(t);}catch(e){return {success:false,message:'Resposta inesperada do servidor (HTTP '+r.status+'). Entre de novo no sistema ou avise o suporte.'};}
  if(j.success===undefined)j.success=r.ok;
  if(!r.ok&&!j.message)j.message='Erro HTTP '+r.status;
  return j;
 }catch(e){return {success:false,message:'Sem conexão com o servidor: '+e.message};}
}
function aviso(t,erro){const m=$('msg');m.textContent=t||'';m.style.cssText=erro?'color:var(--erro-tx);background:var(--erro-bg);padding:6px 10px;border-radius:8px':'color:var(--suave)';}
const ROTULO={pendente:'marcada, falta gerar',gerada:'gerada',erro:'erro',enviada:'enviada',na_fila:'na fila',falhou:'falhou',sem_whatsapp:'sem WhatsApp',ignorada:'ignorada',sem_cadastro:'sem cadastro',erro_rede:'sem rede'};
async function carregar(){
 if(!comp()){return;}
 const d=await api('GET','/api/mei/empresas?competencia='+comp());
 if(!d.success){aviso(d.message,true);return;}
 $('corpo').innerHTML=d.empresas.map(e=>`<tr><td><input type="checkbox" ${e.pagar?'checked':''} onchange="marcar(${e.company_id},this.checked)"></td>
 <td>${esc(e.razao_social)}</td><td>${esc(e.cnpj)}</td>
 <td>${e.status?`<span class="tag ${e.status}">${ROTULO[e.status]||esc(e.status)}</span>`:''} ${esc(e.erro)}</td>
 <td>${e.das_emissao_id?`<a href="/api/mei/${e.das_emissao_id}/pdf" target="_blank">PDF</a>`:''}</td>
 <td>${e.envio?`<span class="tag ${e.envio=='enviada'?'gerada':(e.envio=='na_fila'?'pendente':'erro')}">${ROTULO[e.envio]||esc(e.envio)}</span> ${esc(e.envio_motivo)}`:''}</td>
 <td id="v${e.company_id}"></td></tr>`).join('')||'<tr><td colspan=7>Nenhum MEI cadastrado.</td></tr>';
 $('cand').innerHTML=d.candidatas.map(c=>`<option value="${c.company_id}">${esc(c.razao_social)}</option>`).join('');
}
async function marcar(id,pagar){const r=await api('PUT','/api/mei/selecao',{company_id:id,competencia:comp(),pagar,data_pagamento:$('data').value});aviso(r.success?'':r.message,!r.success);carregar();}
async function gerar(){aviso('Gerando…');const r=await api('POST','/api/mei/gerar',{competencia:comp(),data_pagamento:$('data').value});aviso(r.success?`${r.resultados.length} guia(s) processada(s).`:r.message,!r.success);carregar();}
async function enviar(forcar){aviso('Enviando…');const r=await api('POST','/api/mei/enviar',{competencia:comp(),forcar});aviso(r.success?`${r.resultados.length} envio(s) tentado(s).`:r.message,!r.success);carregar();}
async function vinculo(){aviso('Consultando o Nescon…');const r=await api('GET','/api/mei/vinculo');if(!r.success){aviso(r.message,true);return;}aviso(r.vinculos.length?'Vínculo verificado.':'Nenhum MEI para verificar: inclua a empresa como MEI primeiro.');r.vinculos.forEach(v=>{const el=$('v'+v.company_id);if(el)el.innerHTML=v.vinculada&&v.whatsapp_valido?'<span class="tag gerada">ok</span>':'<span class="tag erro">'+esc(v.vinculada?'sem WhatsApp válido':'não cadastrado')+'</span> '+esc(v.motivo);});}
async function incluir(){const id=$('cand').value;if(!id)return;const r=await api('PUT','/api/mei/empresa/'+id,{eh_mei:true});aviso(r.success?'':r.message,!r.success);carregar();}
const h=new Date();$('comp').value=h.getFullYear()+'-'+String(h.getMonth()+1).padStart(2,'0');$('comp').onchange=carregar;carregar();
</script></body></html>"""


@mei_bp.get('/mei')
def pagina_mei():
    return render_template_string(PAGINA, css=CSS, menu=lateral('mei'))
