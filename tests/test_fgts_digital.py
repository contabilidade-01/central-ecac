"""Robô do FGTS Digital (desvio 22) — SEM rede e SEM certificado.

Duas partes:
* rotas do link (`/api/acesso/<token>/fgts/*`) com o robô desligado da fila: recorte por
  CNPJ do token, um pedido aberto por empresa, limite por link, escolha só de
  competência que o FGTS Digital listou, PDF só se trouxer o CNPJ da empresa;
* o robô DE VERDADE (Playwright/Chromium) contra um FGTS Digital + gov.br FALSOS servidos
  por `context.route` — com a mesma estrutura mapeada na tela em 23/10/2026 (modal
  "Definir Perfil" com ng-select, Guia Rápida com ng-select de competências, botão
  "Emitir guia" que baixa a GFD). Cobre: login sem captcha, login COM captcha resolvido
  por "toques do cliente", sem procuração, e o navegador sempre fechado no fim.

Rodar:  python -m pytest tests/test_fgts_digital.py -q
(o teste do navegador é pulado se o Chromium do Playwright não estiver instalado;
 `FGTS_CHROMIUM_PATH` aponta outro executável)
"""
import json
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest

_TMP = tempfile.mkdtemp(prefix='fgts_teste_')
os.environ['DATA_DIR'] = _TMP
os.environ.setdefault('AUTH_USER', 'teste')
os.environ.setdefault('AUTH_PASSWORD', 'teste')
os.environ['LIMITE_GASTO_MENSAL'] = '0'
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['INTEGRACAO_TOKEN'] = 'token-de-teste-com-mais-de-trinta-e-dois-caracteres-0001'
os.environ['FGTS_DIGITAL_HABILITADO'] = '1'
os.environ['FGTS_MAX_PEDIDOS_DIA'] = '3'
os.environ['FGTS_ESCOLHA_TIMEOUT_S'] = '20'
os.environ['FGTS_CAPTCHA_TIMEOUT_S'] = '20'
os.environ['FGTS_LOGIN_TIMEOUT_S'] = '20'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

TOKEN = {'X-Integracao-Token': os.environ['INTEGRACAO_TOKEN']}
CNPJ_A = '36298150000170'
CNPJ_B = '11444777000161'


def pdf_com_texto(texto: str) -> bytes:
    """PDF mínimo com texto extraível (o PyPDF2 lê)."""
    conteudo = f'BT /F1 12 Tf 50 750 Td ({texto}) Tj ET'.encode('latin-1')
    objs = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length ' + str(len(conteudo)).encode() + b' >>\nstream\n' + conteudo + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    saida = b'%PDF-1.4\n'
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(saida))
        saida += f'{i} 0 obj\n'.encode() + o + b'\nendobj\n'
    xref = len(saida)
    saida += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    for off in offsets:
        saida += f'{off:010d} 00000 n \n'.encode()
    saida += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return saida


GFD_A = pdf_com_texto('GFD - Guia do FGTS Digital 36.298.150 J F DA SILVA 359,93')
GFD_OUTRA = pdf_com_texto('GFD - Guia do FGTS Digital 11.444.777 BETA 10,00')


@pytest.fixture()
def ambiente():
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        from app import security
        from app.integracao_models import AcessoLink, FgtsPedido
        from app.models import Company
        for modelo in (AcessoLink, FgtsPedido, Company):
            modelo.query.delete()
        security._TENTATIVAS.clear()
        a = Company(razao_social='J F DA SILVA CLINICA VETERINARIA', cnpj=CNPJ_A, ativo=True)
        b = Company(razao_social='BETA LTDA', cnpj=CNPJ_B, ativo=True)
        db.session.add_all([a, b])
        db.session.commit()
        yield app, app.test_client(), {'A': a.id, 'B': b.id}


@pytest.fixture()
def sem_fila(monkeypatch):
    """Rotas sem disparar o navegador: o pedido só entra na fila."""
    from app.services import fgts_digital_robo
    enfileirados = []
    monkeypatch.setattr(fgts_digital_robo.RoboFgts, 'enfileirar', lambda self, pid: enfileirados.append(pid))
    return enfileirados


def _link(http, cnpj=CNPJ_A):
    r = http.post('/api/interno/acesso-links', json={'cnpj': cnpj, 'solicitado_por': 'atendechat:jean'},
                  headers=TOKEN)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['link']['token']


# ------------------------------------------------------------------- rotas
def test_pagina_mostra_fgts_so_quando_ligado(ambiente, monkeypatch):
    _app, http, _ = ambiente
    token = _link(http)
    r = http.post('/api/interno/acesso-links', json={'cnpj': CNPJ_A}, headers=TOKEN).get_json()
    assert r['fgts'] is True
    assert http.get(f'/api/acesso/{token}').get_json()['fgts']['habilitado'] is True
    # Piloto com outro CNPJ: esta empresa não vê nem consegue pedir.
    monkeypatch.setenv('FGTS_DIGITAL_CNPJS', CNPJ_B)
    assert http.get(f'/api/acesso/{token}').get_json()['fgts'] == {'habilitado': False}
    assert http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).status_code == 503
    assert http.post('/api/interno/acesso-links', json={'cnpj': CNPJ_A}, headers=TOKEN).get_json()['fgts'] is False
    monkeypatch.setenv('FGTS_DIGITAL_CNPJS', f'{CNPJ_B}, 36.298.150/0001-70')
    assert http.get(f'/api/acesso/{token}').get_json()['fgts']['habilitado'] is True
    monkeypatch.delenv('FGTS_DIGITAL_CNPJS')
    monkeypatch.setenv('FGTS_DIGITAL_HABILITADO', '0')
    assert http.get(f'/api/acesso/{token}').get_json()['fgts'] == {'habilitado': False}
    r = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'})
    assert r.status_code == 503


def test_um_pedido_aberto_por_empresa_e_limite_do_link(ambiente, sem_fila):
    _app, http, _ = ambiente
    token = _link(http)
    p1 = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']
    p2 = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']
    assert p1['id'] == p2['id'] and sem_fila == [p1['id']]          # retoma o mesmo
    assert p1['status'] == 'fila' and p1['etapas'][0]['situacao'] == 'agora'
    pagina = http.get(f'/api/acesso/{token}').get_json()
    assert pagina['fgts']['pedido_aberto'] == p1['id']

    from app.integracao_models import FgtsPedido
    for _ in range(2):   # fecha e pede de novo até o limite (3/dia)
        p = db.session.get(FgtsPedido, sem_fila[-1])
        p.status = 'erro'
        db.session.commit()
        http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'})
    p = db.session.get(FgtsPedido, sem_fila[-1])
    p.status = 'erro'
    db.session.commit()
    r = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'})
    assert r.status_code == 429 and len(sem_fila) == 3


def test_recorte_por_cnpj_do_token(ambiente, sem_fila):
    _app, http, _ = ambiente
    token_a, token_b = _link(http, CNPJ_A), _link(http, CNPJ_B)
    pid = http.post(f'/api/acesso/{token_a}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    assert http.get(f'/api/acesso/{token_b}/fgts/pedidos/{pid}').status_code == 404
    assert http.post(f'/api/acesso/{token_b}/fgts/pedidos/{pid}/escolha',
                     json={'competencias': ['202602']}).status_code == 404
    assert http.get(f'/api/acesso/{token_b}/fgts/pedidos/{pid}/pdf/0').status_code == 404
    assert http.get(f'/api/acesso/{token_a}/fgts/pedidos/{pid}').status_code == 200
    # token inexistente
    assert http.get('/api/acesso/' + 'f' * 24 + f'/fgts/pedidos/{pid}').status_code == 404


def test_escolha_so_da_lista_e_emitir_exige_consulta(ambiente, sem_fila, monkeypatch):
    _app, http, _ = ambiente
    token = _link(http)
    r = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'emitir', 'competencia': '202602'})
    assert r.status_code == 409                       # sem consulta de hoje
    pid = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    from app.integracao_models import FgtsPedido
    from app.services import fgts_digital_robo, fgts_pedidos_service as svc
    p = db.session.get(FgtsPedido, pid)
    svc.gravar_resultado(p, {'competencias': [{'competencia': '202602', 'rotulo': '02/2026', 'total': 359.93}]})
    assert http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/escolha',
                     json={'competencias': ['202602']}).status_code == 409   # ainda não espera escolha
    p.status = 'aguardando_escolha'
    db.session.commit()
    escolhas = []
    monkeypatch.setattr(fgts_digital_robo.RoboFgts, 'escolher', lambda self, i, c, d=None: escolhas.append((i, c)))
    assert http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/escolha',
                     json={'competencias': ['202603']}).status_code == 400
    assert escolhas == []
    r = http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/escolha', json={'competencias': ['02/2026']})
    assert r.status_code == 200 and escolhas == [(pid, ['202602'])]


def test_detalhar_le_texto_real_da_tela():
    """Texto copiado da Guia Rápida em 23/10/2026 (J F DA SILVA, 02/2026)."""
    from app.services.fgts_digital_robo import RoboFgts
    texto = ('Resumo da Pesquisa\n\nCompetência de Apuração\n02/2026\nTotal Devedor\nR$ 359,93\n'
             'Mensal - Vencimento da Guia: 23/10/2026\nEmitir guia\nFGTS\n\nTodos os valores exibidos estão '
             'expressos em reais (R$).\nCompetência\nde Referência\nVencimento dos Débitos\nQuantidade de\n'
             'Trabalhadores\nFGTS\nMensal\nFGTS\nRescisório\nIndenização\nCompensatória\nFGTS Processo\n'
             'Trabalhista\nEncargos\nFGTS\nTotal\n20/03/2026\n2\n312,00\n0,00\n0,00\n0,00\n47,93\n359,93\n'
             'Total\n312,00\n0,00\n0,00\n0,00\n47,93\n359,93')
    d = RoboFgts._detalhar('202602', texto)
    assert d['total'] == 359.93 and d['rotulo'] == '02/2026'
    assert d['grupos'] == [{'grupo': 'Mensal', 'vencimento': '23/10/2026', 'valor': 359.93}]
    assert d['composicao'] == {'fgts_mensal': 312.0, 'fgts_rescisorio': 0.0, 'indenizacao': 0.0,
                               'processo_trabalhista': 0.0, 'encargos': 47.93, 'total': 359.93}


def test_emitir_em_sessao_nova_aceita_varios_meses(ambiente, sem_fila):
    _app, http, _ = ambiente
    token = _link(http)
    from app.integracao_models import FgtsPedido
    from app.services import fgts_pedidos_service as svc
    consulta = FgtsPedido(company_id=1, cnpj=CNPJ_A, acao='consultar', status='concluido',
                          concluido_em=datetime.utcnow())
    svc.gravar_resultado(consulta, {'competencias': [{'competencia': '202602'}, {'competencia': '202609'}]})
    db.session.add(consulta)
    db.session.commit()
    r = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'emitir', 'competencias': ['202602', '202612']})
    assert r.status_code == 409                      # 12/2026 não está na lista
    r = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'emitir', 'competencias': ['202609', '02/2026']})
    assert r.status_code == 200
    p = db.session.get(FgtsPedido, r.get_json()['pedido']['id'])
    assert svc.resultado(p)['escolha'] == ['202609', '202602'] and p.competencia == '202609'


def test_pdf_so_sai_com_cnpj_da_empresa(ambiente, sem_fila):
    _app, http, _ = ambiente
    token = _link(http)
    pid = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    from app.integracao_models import FgtsPedido
    from app.services import fgts_pedidos_service as svc
    pasta = svc.pasta_pdfs(CNPJ_A)
    (pasta / 'boa.pdf').write_bytes(GFD_A)
    (pasta / 'errada.pdf').write_bytes(GFD_OUTRA)
    p = db.session.get(FgtsPedido, pid)
    svc.gravar_resultado(p, {'guias': [{'arquivo': 'boa.pdf'}, {'arquivo': 'errada.pdf'},
                                       {'arquivo': '../../instance/integra_contador.db'}]})
    p.status = 'concluido'
    db.session.commit()
    r = http.get(f'/api/acesso/{token}/fgts/pedidos/{pid}/pdf/0')
    assert r.status_code == 200 and r.data.startswith(b'%PDF')
    assert http.get(f'/api/acesso/{token}/fgts/pedidos/{pid}/pdf/1').status_code == 404
    assert http.get(f'/api/acesso/{token}/fgts/pedidos/{pid}/pdf/2').status_code == 410   # não sai da pasta
    assert http.get(f'/api/acesso/{token}/fgts/pedidos/{pid}/pdf/9').status_code == 410


def test_captcha_rotas_so_com_pedido_esperando(ambiente, sem_fila):
    _app, http, _ = ambiente
    token = _link(http)
    pid = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    assert http.get(f'/api/acesso/{token}/fgts/pedidos/{pid}/captcha.jpg').status_code == 404
    assert http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/captcha/toque', json={'x': .5, 'y': .5}).status_code == 404


# ------------------------------------------------------------- robô (navegador)
CHROMIUM = os.getenv('FGTS_CHROMIUM_PATH') or ('/opt/pw-browsers/chromium' if Path('/opt/pw-browsers/chromium').exists() else '')


def _tem_chromium() -> bool:
    if CHROMIUM:
        return True
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False


ESTILO = '<style>[role=dialog]{position:fixed;top:40px;left:300px;background:#fff;border:1px solid #999;padding:20px}' \
         '.ng-option{padding:6px;cursor:pointer} iframe{border:0}</style>'


def _dias_uteis(n):
    from datetime import date, timedelta
    d, saida = date.today(), []
    while len(saida) < n:
        if d.weekday() < 5:
            saida.append(d.isoformat())
        d += timedelta(days=1)
    return saida


# Dias que a Parametrizada falsa libera: os próximos 12 dias úteis (como no portal em 23/10/2026).
DATAS_PARAM = _dias_uteis(12)

# Guia Parametrizada falsa, com a estrutura mapeada na tela real em 23/10/2026.
PARAM_JS = """<h1>Emissão de Guia Parametrizada</h1><div id=passo></div>
<script>
var LIVRES = %s, esc = {Inicial: '09/2026', Final: '09/2026'}, resumo = 0, passo = 1, venc = null;
var MESES = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
function br(iso){ return iso.slice(8)+'/'+iso.slice(5,7)+'/'+iso.slice(0,4); }
function iso(br){ return br.slice(6)+'-'+br.slice(3,5)+'-'+br.slice(0,2); }
function hojeIso(){ var d=new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
venc = hojeIso();
function render(){
  var el = document.getElementById('passo'), h = '';
  // botões dos passos (como no portal: "Emitir Guia" também é nome de passo)
  h += '<div><button type=button>Selecionar Débitos FGTS</button><button type=button>Emitir Guia</button></div>';
  if (passo === 1) {
    h += '<label>Inicial</label><ng-select data-r=Inicial style="display:block;border:1px solid #888;width:150px;height:24px">'+esc.Inicial+'</ng-select>'
      +  '<label>Final</label><ng-select data-r=Final style="display:block;border:1px solid #888;width:150px;height:24px">'+esc.Final+'</ng-select>'
      +  '<div id=ops></div><button type=button id=pesq>Pesquisar</button><div id=tab></div>'
      +  '<div id=res>'+(resumo ? 'Quantidade de Débitos de FGTS '+resumo : '')+'</div><button type=button id=av1>Avançar</button>';
  } else if (passo === 2) {
    h += '<p>Selecionar Débitos Consignado</p><button type=button id=av2>Avançar</button>';
  } else if (passo === 3) {
    h += '<p>Definir Vencimento</p><label>Vencimento da Guia</label><input id=dt value="'+br(venc)+'">'
      +  '<i class="fas fa-calendar-alt" style="display:inline-block;width:20px;height:20px;background:#ccc"></i>'
      +  '<div id=cal></div><button type=button id=av3>Avançar</button>';
  } else {
    var t = esc.Inicial === '02/2026' ? ['312,00','47,99','359,99'] : ['200,00','11,10','211,10'];
    h += '<p>Vencimento da Guia: '+br(venc)+' Tag: x</p><table><tr><td>Total</td><td>'+t[0]+'</td><td>0,00</td><td>0,00</td><td>0,00</td><td>'
      +  t[1]+'</td><td>'+t[2]+'</td></tr></table><button type=button id=emit>Emitir Guia</button>';
  }
  el.innerHTML = h;
}
var mesCal = null;
function cal(){
  var c = document.getElementById('cal'); var y = mesCal[0], m = mesCal[1];
  var h = '<bs-datepicker-container><div class=bs-datepicker-head><button class=previous>‹</button><button>'+MESES[m-1]
        + '</button><button>'+y+'</button><button class=next>›</button></div>';
  var dias = new Date(y, m, 0).getDate();
  for (var d=1; d<=dias; d++){ var i=y+'-'+String(m).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    h += '<span bsdatepickerdaydecorator class="'+(LIVRES.indexOf(i)>=0?'':'disabled')+'">'+d+'</span> '; }
  c.innerHTML = h + '</bs-datepicker-container>';
}
document.addEventListener('click', function(e){
  var t = e.target, r = t.getAttribute && t.getAttribute('data-r');
  if (r) { window._r = r; document.getElementById('ops').innerHTML =
    '<div class=ng-option>09/2026</div><div class=ng-option>02/2026</div>'; return; }
  if (t.classList && t.classList.contains('ng-option')) { esc[window._r] = t.innerText.trim(); document.getElementById('ops').innerHTML='';
    document.querySelector('[data-r='+window._r+']').innerText = esc[window._r]; return; }
  if (t.id === 'pesq') { var a = esc.Inicial.slice(3)+esc.Inicial.slice(0,2);
    fetch('/cobranca/api/guia-parametrizada/debitos?competenciaApuracaoInicial='+a).then(function(){
      document.getElementById('tab').innerHTML = '<input type=checkbox id=selecionar-todos><label for=selecionar-todos>todos</label>'
        + '<div>1-2 de 2 itens</div><button type=button id=add>Adicionar à guia</button><button id=btn-next-page disabled>›</button>'; }); return; }
  if (t.id === 'add') { if (document.getElementById('selecionar-todos').checked) resumo = 2;
    document.getElementById('res').innerText = 'Quantidade de Débitos de FGTS ' + resumo; return; }
  if (t.id === 'av1' && resumo) { passo = 2; render(); return; }
  if (t.id === 'av2') { passo = 3; render(); return; }
  if (t.classList && t.classList.contains('fa-calendar-alt')) { var d = new Date(); mesCal = [d.getFullYear(), d.getMonth()+1]; cal(); return; }
  if (t.classList && t.classList.contains('next')) { mesCal = mesCal[1]===12 ? [mesCal[0]+1,1] : [mesCal[0], mesCal[1]+1]; cal(); return; }
  if (t.id === 'av3') { passo = 4; render(); return; }
  if (t.id === 'emit') { fetch('/cobranca/api/gfd.pdf',{method:'POST'}).then(function(x){return x.blob()}).then(function(b){
      var l=document.createElement('a'); l.href=URL.createObjectURL(b); l.download='gfd.pdf'; document.body.appendChild(l); l.click();
      document.body.insertAdjacentHTML('beforeend','<span>0126102399999999-1</span>'); }); return; }
});
document.addEventListener('keydown', function(e){ if (e.key==='Escape') { var c=document.getElementById('cal'); if (c) c.innerHTML=''; } });
// Data fora do limite: o portal RECUSA EM SILÊNCIO (o campo volta ao valor anterior).
document.addEventListener('change', function(e){ if (e.target.id !== 'dt') return;
  var v = e.target.value; if (/^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(v) && LIVRES.indexOf(iso(v)) >= 0) { venc = iso(v);
    fetch('/cobranca/api/guia-parametrizada/debitos-revisao?dtPagamento='+venc); } else { e.target.value = br(venc); } });
render();
</script>"""


def _site_falso(captcha: bool, procuracoes=(CNPJ_A,)):
    """Handler de `context.route` imitando gov.br + FGTS Digital (estrutura real)."""
    estado = {'downloads': 0}

    def html(corpo):
        return {'status': 200, 'content_type': 'text/html; charset=utf-8',
                'body': f'<!doctype html><html><head><meta charset=utf-8>{ESTILO}</head><body>{corpo}</body></html>'}

    cabecalho = """<div id=cab></div><script>
      function ck(n){var m=document.cookie.match(new RegExp(n+'=([^;]+)'));return m?m[1]:''}
      function fmt(c){return c.slice(0,2)+'.'+c.slice(2,5)+'.'+c.slice(5,8)+'/'+c.slice(8,12)+'-'+c.slice(12)}
      if(ck('perfil')) document.getElementById('cab').innerText='Empregador: '+fmt(ck('perfil'))+' | EMPRESA';
    </script>"""

    def handler(route):
        url = route.request.url
        u = urlparse(url)
        host, path = u.netloc, u.path
        if 'hcaptcha' in host:
            return route.fulfill(**html(
                '<div style="height:520px;background:#ddd">Selecione os objetos</div>'
                '<button style="width:100%;height:50px" onclick="parent.postMessage(\'ok\',\'*\')">Verificar</button>'))
        if host == 'sso.acesso.gov.br':
            alvo = 'https://fgtsdigital.sistema.gov.br/portal/acessogov?code=x'
            js = (f"var f=document.createElement('iframe');f.src='https://newassets.hcaptcha.com/c';"
                  f"f.style.cssText='position:absolute;top:60px;left:400px;width:400px;height:580px';"
                  f"document.body.appendChild(f);window.addEventListener('message',function(){{f.remove();"
                  f"location='{alvo}'}});" if captcha else f"location='{alvo}';")
            return route.fulfill(**html('<h1>gov.br</h1><a href="#" onclick="' + js.replace('"', '&quot;')
                                        + 'return false">Seu certificado digital</a>'))
        if host != 'fgtsdigital.sistema.gov.br':
            return route.fulfill(status=404, body='')
        if path == '/portal/acessogov':
            return route.fulfill(**html("<script>document.cookie='logado=1;path=/';location='/portal/escolhaPerfil'</script>"))
        if path == '/portal/login':
            return route.fulfill(**html("<button type=button onclick=\"location='https://sso.acesso.gov.br/login?client_id=x'\">"
                                        "Entrar com GOV.BR</button>"))
        if path in ('/portal/servicos', '/portal/escolhaPerfil'):
            perfil = path.endswith('escolhaPerfil')
            corpo = cabecalho + """<script>if(!ck('logado')) location='/portal/login';</script>
              <button type=button>Trocar Perfil</button><h1>Serviços</h1>"""
            if perfil:
                corpo += """<div role=dialog><h3>Definir Perfil</h3><div id=err></div>
                  <label>Perfil (Obrigatório)</label>
                  <ng-select id=sel style="display:block;border:1px solid #888;width:300px;height:30px">Meu Perfil</ng-select>
                  <div id=ops></div><div id=ni></div>
                  <button type=button id=def>Definir</button></div>
                  <script>
                  var OK=%s;
                  document.getElementById('sel').onclick=function(){document.getElementById('ops').innerHTML=
                    '<div class=ng-option>Meu Perfil</div><div class=ng-option>Procurador</div>';};
                  document.getElementById('ops').onclick=function(e){if(e.target.innerText.trim()==='Procurador'){
                    document.getElementById('sel').innerText='Procurador';this.innerHTML='';
                    document.getElementById('ni').innerHTML='<label>Empregador</label><input placeholder="Informe CNPJ ou CPF">';}};
                  document.getElementById('def').onclick=function(){var v=document.querySelector('#ni input').value.replace(/\\D/g,'');
                    setTimeout(function(){ if(OK.indexOf(v)<0){document.getElementById('err').innerText=
                      'Erro na operação Não existe procuração para o Número de Inscrição selecionado.';return;}
                      document.cookie='perfil='+v+';path=/';location='/portal/servicos';},300);};
                  </script>""" % (list(procuracoes),)
            return route.fulfill(**html(corpo))
        if path.startswith('/cobranca/api/guia-rapida/v2/'):
            return route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
        if path == '/cobranca/api/gfd.pdf':
            estado['downloads'] += 1
            return route.fulfill(status=200, body=GFD_A, headers={'Content-Type': 'application/pdf'})
        if path == '/cobranca/param.html':
            return route.fulfill(**html(cabecalho + PARAM_JS % (json.dumps(DATAS_PARAM),)))
        if path == '/cobranca/api/guia-parametrizada/debitos' or path == '/cobranca/api/guia-parametrizada/debitos-revisao':
            return route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
        if path == '/cobranca/':
            corpo = cabecalho + """<script>
              // Angular de verdade troca de tela pelo hash; aqui a Parametrizada é outra página.
              function rota(){ if (location.hash.indexOf('parametrizada') >= 0) location.replace('/cobranca/param.html' + location.hash); }
              rota(); window.addEventListener('hashchange', rota);
              </script><h1>Emissão de Guia Rápida</h1>
              <label>Competência de Apuração</label>
              <ng-select id=comp style="display:block;border:1px solid #888;width:200px;height:30px">09/2026</ng-select>
              <div id=ops></div><button type=button id=pesq>Pesquisar</button><div id=res></div>
              <script>
              var esc='09/2026';
              document.getElementById('comp').onclick=function(){document.getElementById('ops').innerHTML=
                 '<div class=ng-option>09/2026</div><div class=ng-option>02/2026</div>';};
              document.getElementById('ops').onclick=function(e){esc=e.target.innerText.trim();
                 document.getElementById('comp').innerText=esc;this.innerHTML='';};
              document.addEventListener('keydown',function(e){if(e.key==='Escape')document.getElementById('ops').innerHTML='';});
              document.getElementById('pesq').onclick=function(){var a=esc.slice(3)+esc.slice(0,2);
                 fetch('/cobranca/api/guia-rapida/v2/'+a+'?mensal=true').then(function(){
                  var t=a==='202602'?'359,93':'211,05', m=a==='202602'?'312,00':'200,00', e=a==='202602'?'47,93':'11,05';
                  document.getElementById('res').innerHTML='<div>Total Devedor</div><div>R$ '+t+'</div>'
                   +'<div>Mensal - Vencimento da Guia: 23/10/2026 <button type=button id=emit> Emitir guia </button></div>'
                   +'<table><tr><td>Competência</td><td>Total</td></tr><tr><td>20/03/2026</td><td>2</td><td>'+m+'</td><td>0,00</td>'
                   +'<td>0,00</td><td>0,00</td><td>'+e+'</td><td>'+t+'</td></tr>'
                   +'<tr><td>Total</td><td>'+m+'</td><td>0,00</td><td>0,00</td><td>0,00</td><td>'+e+'</td><td>'+t+'</td></tr></table>';
                  // Como o portal real: POST gerar-guia → blob → <a download> (vira .tmp no Chrome).
                  document.getElementById('emit').onclick=function(){var b=this;
                    fetch('/cobranca/api/gfd.pdf',{method:'POST'}).then(function(r){return r.blob()}).then(function(bl){
                    var l=document.createElement('a');l.href=URL.createObjectURL(bl);l.download='gfd.pdf';
                    document.body.appendChild(l);l.click();b.outerHTML='<span>0126102368549700-5</span>';});};
                 });};
              </script>"""
            return route.fulfill(**html(corpo))
        return route.fulfill(status=404, body='')

    return handler, estado


def _robo_com_site(app, monkeypatch, captcha=False, procuracoes=(CNPJ_A,)):
    from app.services import fgts_digital_robo as mod
    monkeypatch.setenv('FGTS_CHROMIUM_PATH', CHROMIUM)
    handler, estado = _site_falso(captcha, procuracoes)
    robo = mod.RoboFgts(app)
    monkeypatch.setattr(robo, '_certificado', lambda: (None, None))
    monkeypatch.setattr(robo, '_ao_criar_contexto', lambda ctx: ctx.route('**/*', handler))
    monkeypatch.setattr(mod, '_robo', robo)
    return robo, estado


def _rodar(app, robo, pid):
    def alvo():
        with app.app_context():
            try:
                robo._processar(pid)
            finally:
                robo._fechar()
                db.session.remove()
    t = threading.Thread(target=alvo)
    t.start()
    return t


def _esperar(http, token, pid, condicao, segundos=40):
    fim = time.time() + segundos
    while time.time() < fim:
        p = http.get(f'/api/acesso/{token}/fgts/pedidos/{pid}').get_json()['pedido']
        if condicao(p):
            return p
        time.sleep(0.3)
    raise AssertionError(f'tempo esgotado; último estado: {p}')


navegador = pytest.mark.skipif(not _tem_chromium(), reason='Chromium do Playwright não instalado')


@navegador
def test_robo_consulta_escolhe_e_emite_sem_captcha(ambiente, sem_fila, monkeypatch):
    app, http, _ = ambiente
    token = _link(http)
    robo, estado = _robo_com_site(app, monkeypatch, captcha=False)
    pid = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    t = _rodar(app, robo, pid)
    p = _esperar(http, token, pid, lambda p: p['status'] in ('aguardando_escolha', 'erro'))
    assert p['status'] == 'aguardando_escolha', p
    assert [c['competencia'] for c in p['competencias']] == ['202609', '202602']
    c = p['competencias'][1]
    assert c['total'] == 359.93 and c['grupos'] == [{'grupo': 'Mensal', 'vencimento': '23/10/2026', 'valor': 359.93}]
    assert c['composicao']['fgts_mensal'] == 312.0 and c['composicao']['encargos'] == 47.93
    assert p['total_em_aberto'] == 570.98
    assert p['datas_permitidas'] == DATAS_PARAM          # lidas do calendário da Parametrizada
    # O cliente marca os DOIS meses: duas GFDs na mesma sessão.
    assert http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/escolha',
                     json={'competencias': ['202602', '09/2026']}).status_code == 200
    p = _esperar(http, token, pid, lambda p: p['status'] in ('concluido', 'erro'))
    t.join(30)
    assert p['status'] == 'concluido', p['mensagem']
    assert estado['downloads'] == 2 and len(p['guias']) == 2
    g = p['guias'][0]
    assert g['competencia'] == '202602' and g['vencimento'] == '23/10/2026'
    assert g['identificador'] == '0126102368549700-5' and g['valor'] == 359.93
    assert p['guias'][1]['competencia'] == '202609' and p['guias'][1]['valor'] == 211.05
    for g in p['guias']:
        r = http.get(g['pdf_url'])
        assert r.status_code == 200 and b'36.298.150' in r.data
    assert robo._browser is None                      # sempre fecha
    assert all(e['situacao'] == 'feito' for e in p['etapas'])


@navegador
def test_robo_captcha_resolvido_pelo_toque_do_cliente(ambiente, sem_fila, monkeypatch):
    app, http, _ = ambiente
    token = _link(http)
    robo, _ = _robo_com_site(app, monkeypatch, captcha=True)
    pid = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    t = _rodar(app, robo, pid)
    p = _esperar(http, token, pid, lambda p: p['aguardando_captcha'] or p['status'] == 'erro')
    assert p['aguardando_captcha'] and any(e['chave'] == 'captcha' and e['situacao'] == 'agora' for e in p['etapas'])
    fim = time.time() + 10
    while time.time() < fim:
        r = http.get(f'/api/acesso/{token}/fgts/pedidos/{pid}/captcha.jpg')
        if r.status_code == 200:
            break
        time.sleep(0.3)
    assert r.status_code == 200 and r.mimetype == 'image/jpeg' and r.data[:2] == b'\xff\xd8'
    # Toque fora da imagem é recusado; o botão "Verificar" fica no rodapé do recorte.
    assert http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/captcha/toque', json={'x': 2, 'y': .5}).status_code == 400
    assert http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/captcha/toque', json={'x': .5, 'y': .95}).status_code == 200
    p = _esperar(http, token, pid, lambda p: p['status'] in ('aguardando_escolha', 'erro'))
    assert p['status'] == 'aguardando_escolha', p['mensagem']
    assert any(e['chave'] == 'captcha' and e['situacao'] == 'feito' for e in p['etapas'])
    # Cliente não escolhe: depois do tempo, conclui só com a lista e FECHA o navegador.
    monkeypatch.setenv('FGTS_ESCOLHA_TIMEOUT_S', '1')
    p = _esperar(http, token, pid, lambda p: p['status'] in ('concluido', 'erro'), segundos=40)
    t.join(30)
    assert p['status'] == 'concluido' and p['guias'] == [] and len(p['competencias']) == 2
    assert robo._browser is None


@navegador
def test_robo_sem_procuracao_da_erro_claro(ambiente, sem_fila, monkeypatch):
    app, http, _ = ambiente
    token = _link(http)
    robo, estado = _robo_com_site(app, monkeypatch, procuracoes=(CNPJ_B,))
    pid = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    t = _rodar(app, robo, pid)
    p = _esperar(http, token, pid, lambda p: p['status'] in ('concluido', 'erro'))
    t.join(30)
    assert p['status'] == 'erro' and 'procuração' in p['mensagem']
    assert any(e['situacao'] == 'erro' and e['chave'] == 'perfil' for e in p['etapas'])
    assert estado['downloads'] == 0 and robo._browser is None


# ------------------------------------------------- rotas internas (AtendeChat/admin)
def test_interno_escritorio_pede_acompanha_e_baixa_com_cnpj_conferido(ambiente, sem_fila, monkeypatch):
    _app, http, _ = ambiente
    # Piloto do link restrito a outra empresa: o escritório continua podendo pedir.
    monkeypatch.setenv('FGTS_DIGITAL_CNPJS', CNPJ_B)
    assert http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ_A}).status_code == 401   # sem token
    r = http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ_A, 'acao': 'consultar',
                                                     'solicitado_por': 'atendechat:jean'}, headers=TOKEN)
    assert r.status_code == 200, r.get_json()
    p = r.get_json()['pedido']
    assert p['origem'] == 'atendechat' and p['status'] == 'fila' and sem_fila == [p['id']]
    pid = p['id']
    # CNPJ obrigatório e conferido
    assert http.get(f'/api/interno/fgts/pedidos/{pid}', headers=TOKEN).status_code == 404
    assert http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ_B}', headers=TOKEN).status_code == 404
    assert http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ_A}', headers=TOKEN).status_code == 200
    assert http.get(f'/api/interno/fgts/pedidos/{pid}/captcha.jpg?cnpj={CNPJ_A}', headers=TOKEN).status_code == 404

    from app.integracao_models import FgtsPedido
    from app.services import fgts_digital_robo, fgts_pedidos_service as svc
    pedido = db.session.get(FgtsPedido, pid)
    svc.gravar_resultado(pedido, {'competencias': [{'competencia': '202602', 'total': 359.93}]})
    pedido.status = 'aguardando_escolha'
    db.session.commit()
    escolhas = []
    monkeypatch.setattr(fgts_digital_robo.RoboFgts, 'escolher', lambda self, i, c, d=None: escolhas.append((i, c)))
    assert http.post(f'/api/interno/fgts/pedidos/{pid}/escolha', json={'cnpj': CNPJ_B, 'competencias': ['202602']},
                     headers=TOKEN).status_code == 404
    assert http.post(f'/api/interno/fgts/pedidos/{pid}/escolha', json={'cnpj': CNPJ_A, 'competencias': ['202602']},
                     headers=TOKEN).status_code == 200
    assert escolhas == [(pid, ['202602'])]

    (svc.pasta_pdfs(CNPJ_A) / 'g.pdf').write_bytes(GFD_A)
    svc.gravar_resultado(pedido, {'guias': [{'arquivo': 'g.pdf', 'competencia': '202602', 'valor': 359.93}]})
    pedido.status = 'concluido'
    db.session.commit()
    j = http.get(f'/api/interno/fgts/pedidos/{pid}?cnpj={CNPJ_A}', headers=TOKEN).get_json()['pedido']
    assert j['guias'][0]['pdf_url'] == f'/api/interno/fgts/pedidos/{pid}/pdf/0'
    r = http.get(f'/api/interno/fgts/pedidos/{pid}/pdf/0?cnpj={CNPJ_A}', headers=TOKEN)
    assert r.status_code == 200 and r.data.startswith(b'%PDF')
    assert http.get(f'/api/interno/fgts/pedidos/{pid}/pdf/0?cnpj={CNPJ_B}', headers=TOKEN).status_code == 404
    # robô desligado: escritório também não pede
    monkeypatch.setenv('FGTS_DIGITAL_HABILITADO', '0')
    pedido.status = 'erro'
    db.session.commit()
    assert http.post('/api/interno/fgts/pedidos', json={'cnpj': CNPJ_A}, headers=TOKEN).status_code == 503



@navegador
def test_robo_emite_com_data_escolhida_pela_guia_parametrizada(ambiente, sem_fila, monkeypatch):
    app, http, _ = ambiente
    token = _link(http)
    robo, estado = _robo_com_site(app, monkeypatch)
    pid = http.post(f'/api/acesso/{token}/fgts/pedidos', json={'acao': 'consultar'}).get_json()['pedido']['id']
    t = _rodar(app, robo, pid)
    p = _esperar(http, token, pid, lambda p: p['status'] in ('aguardando_escolha', 'erro'))
    assert p['status'] == 'aguardando_escolha', p['mensagem']
    # Data que o FGTS Digital não libera: recusada antes de chegar ao robô.
    r = http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/escolha',
                  json={'competencias': ['202602'], 'data_pagamento': '2099-01-02'})
    assert r.status_code == 400 and 'data' in r.get_json()['message']
    data = DATAS_PARAM[-1]
    br = f'{data[8:]}/{data[5:7]}/{data[:4]}'
    r = http.post(f'/api/acesso/{token}/fgts/pedidos/{pid}/escolha',
                  json={'competencias': ['202602'], 'data_pagamento': br})
    assert r.status_code == 200 and r.get_json()['data_pagamento'] == data
    p = _esperar(http, token, pid, lambda p: p['status'] in ('concluido', 'erro'))
    t.join(30)
    assert p['status'] == 'concluido', p['mensagem']
    assert p['data_pagamento'] == data and estado['downloads'] == 1
    g = p['guias'][0]
    assert g['vencimento'] == br and g['valor'] == 359.99 and g['identificador'] == '0126102399999999-1'
    assert http.get(g['pdf_url']).status_code == 200
    assert robo._browser is None


def test_emitir_em_sessao_nova_valida_data_da_ultima_consulta(ambiente, sem_fila):
    _app, http, _ = ambiente
    token = _link(http)
    from app.integracao_models import FgtsPedido
    from app.services import fgts_pedidos_service as svc
    consulta = FgtsPedido(company_id=1, cnpj=CNPJ_A, acao='consultar', status='concluido',
                          concluido_em=datetime.utcnow())
    svc.gravar_resultado(consulta, {'competencias': [{'competencia': '202602'}],
                                    'datas_permitidas': ['2099-12-30']})
    db.session.add(consulta)
    db.session.commit()
    r = http.post(f'/api/acesso/{token}/fgts/pedidos',
                  json={'acao': 'emitir', 'competencias': ['202602'], 'data_pagamento': '2099-12-29'})
    assert r.status_code == 400
    r = http.post(f'/api/acesso/{token}/fgts/pedidos',
                  json={'acao': 'emitir', 'competencias': ['202602'], 'data_pagamento': '30/12/2099'})
    assert r.status_code == 200
    p = db.session.get(FgtsPedido, r.get_json()['pedido']['id'])
    assert svc.resultado(p)['data_pagamento'] == '2099-12-30'


def test_certificado_pfx_vira_pem_em_memoria():
    """O .pfx das ACs vem com criptografia legada; o robô entrega PEM ao navegador."""
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    from app.services.fgts_digital_robo import FalhaRobo, pfx_para_pem
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'NESCON TESTE:35736034000123')])
    c = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
         .serial_number(1).not_valid_before(dt.datetime(2025, 1, 1)).not_valid_after(dt.datetime(2030, 1, 1))
         .sign(k, hashes.SHA256()))
    enc = (serialization.PrivateFormat.PKCS12.encryption_builder().kdf_rounds(2048)
           .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC).hmac_hash(hashes.SHA1()).build(b'1234'))
    pfx = pkcs12.serialize_key_and_certificates(b't', k, c, None, enc)
    pem = pfx_para_pem(pfx, '1234')
    assert pem['cert'].startswith(b'-----BEGIN CERTIFICATE-----')
    assert pem['key'].startswith(b'-----BEGIN PRIVATE KEY-----')
    with pytest.raises(FalhaRobo):
        pfx_para_pem(pfx, 'errada')
