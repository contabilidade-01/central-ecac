"""Testes do FGTS local SEM rede e SEM gov.br: um Chrome de verdade na porta de depuração
servindo um site falso (já logado) com a estrutura real do FGTS Digital. Exercita a
conexão (`conexao.py`) e os passos pós-login (`fgts_portal.py`).

Rodar:  cd fgts_local && python -m pytest test_local.py -q
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.request
from datetime import date, timedelta

import pytest

import conexao
from fgts_portal import FalhaFgts

CNPJ_A = '36298150000170'
PORTA = int(os.getenv('FGTS_TEST_PORTA', '9444'))
CHROMIUM = os.getenv('FGTS_CHROMIUM_PATH') or '/opt/pw-browsers/chromium'

ESTILO = ('<style>[role=dialog]{position:fixed;top:40px;left:300px;background:#fff;border:1px solid #999;'
          'padding:20px}.ng-option{padding:6px;cursor:pointer}</style>')


def pdf_com_texto(texto: str) -> bytes:
    conteudo = f'BT /F1 12 Tf 50 750 Td ({texto}) Tj ET'.encode('latin-1')
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R '
            b'/Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length ' + str(len(conteudo)).encode() + b' >>\nstream\n' + conteudo + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
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


def _dias_uteis(n):
    d, saida = date.today(), []
    while len(saida) < n:
        if d.weekday() < 5:
            saida.append(d.isoformat())
        d += timedelta(days=1)
    return saida


DATAS_PARAM = _dias_uteis(12)

CABECALHO = """<div id=cab></div><script>
  function ck(n){var m=document.cookie.match(new RegExp(n+'=([^;]+)'));return m?m[1]:''}
  function fmt(c){return c.slice(0,2)+'.'+c.slice(2,5)+'.'+c.slice(5,8)+'/'+c.slice(8,12)+'-'+c.slice(12)}
  if(ck('perfil')) document.getElementById('cab').innerText='Empregador: '+fmt(ck('perfil'))+' | EMPRESA';
</script>"""

PARAM_JS = r"""<h1>Emissão de Guia Parametrizada</h1><div id=passo></div>
<script>
var LIVRES = %s, esc = {Inicial: '09/2026', Final: '09/2026'}, resumo = 0, passo = 1, venc = null;
var MESES = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
function br(iso){ return iso.slice(8)+'/'+iso.slice(5,7)+'/'+iso.slice(0,4); }
function iso(br){ return br.slice(6)+'-'+br.slice(3,5)+'-'+br.slice(0,2); }
function hojeIso(){ var d=new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
venc = hojeIso();
function render(){
  var el = document.getElementById('passo'), h = '';
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
document.addEventListener('change', function(e){ if (e.target.id !== 'dt') return;
  var v = e.target.value; if (/^\d{2}\/\d{2}\/\d{4}$/.test(v) && LIVRES.indexOf(iso(v)) >= 0) { venc = iso(v);
    fetch('/cobranca/api/guia-parametrizada/debitos-revisao?dtPagamento='+venc); } else { e.target.value = br(venc); } });
render();
</script>"""


def _html(corpo):
    return {'status': 200, 'content_type': 'text/html; charset=utf-8',
            'body': f'<!doctype html><html><head><meta charset=utf-8>{ESTILO}</head><body>{corpo}</body></html>'}


def _handler(procuracoes=(CNPJ_A,)):
    def handler(route):
        from urllib.parse import urlparse
        u = urlparse(route.request.url)
        host, path = u.netloc, u.path
        if host != 'fgtsdigital.sistema.gov.br':
            return route.fulfill(status=404, body='')
        if path in ('/portal/servicos', '/portal/escolhaPerfil'):
            perfil = path.endswith('escolhaPerfil')
            corpo = CABECALHO + """<script>if(!ck('logado')) location='/portal/login';</script>
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
            return route.fulfill(**_html(corpo))
        if path == '/portal/login':
            return route.fulfill(**_html('<h1>Login gov.br</h1>'))
        if path.startswith('/cobranca/api/guia-rapida/v2/'):
            return route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
        if path == '/cobranca/api/gfd.pdf':
            return route.fulfill(status=200, body=GFD_A, headers={'Content-Type': 'application/pdf'})
        if path == '/cobranca/param.html':
            return route.fulfill(**_html(CABECALHO + PARAM_JS % (json.dumps(DATAS_PARAM),)))
        if path in ('/cobranca/api/guia-parametrizada/debitos', '/cobranca/api/guia-parametrizada/debitos-revisao'):
            return route.fulfill(status=200, content_type='application/json', body='{"ok":true}')
        if path == '/cobranca/':
            corpo = CABECALHO + """<script>
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
                  document.getElementById('emit').onclick=function(){var b=this;
                    fetch('/cobranca/api/gfd.pdf',{method:'POST'}).then(function(r){return r.blob()}).then(function(bl){
                    var l=document.createElement('a');l.href=URL.createObjectURL(bl);l.download='gfd.pdf';
                    document.body.appendChild(l);l.click();b.outerHTML='<span>0126102368549700-5</span>';});};
                 });};
              </script>"""
            return route.fulfill(**_html(corpo))
        return route.fulfill(status=404, body='')
    return handler


def _matar_porta(porta):
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            cmd = open(f'/proc/{pid}/cmdline', 'rb').read().split(b'\0')
        except Exception:
            continue
        if cmd and b'chrom' in cmd[0].lower() and any(f'--remote-debugging-port={porta}'.encode() == a for a in cmd):
            try:
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass


@pytest.fixture
def sessao():
    """Chrome real na porta + AutomacaoLocal ligada, com o site falso roteado e já logado."""
    if not os.path.exists(CHROMIUM):
        pytest.skip('sem Chromium')
    _matar_porta(PORTA)
    perfil = tempfile.mkdtemp(prefix='fgts_perfil_')
    proc = subprocess.Popen([CHROMIUM, '--headless=new', f'--remote-debugging-port={PORTA}',
                             f'--user-data-dir={perfil}', '--no-first-run', '--no-sandbox', 'about:blank'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(150):
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{PORTA}/json/version', timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    sess = conexao.AutomacaoLocal(PORTA)
    sess.conectar()
    ctx = sess._browser.contexts[0]
    ctx.route('**/*', _handler())
    ctx.add_cookies([{'name': 'logado', 'value': '1', 'domain': 'fgtsdigital.sistema.gov.br', 'path': '/'}])
    try:
        yield sess
    finally:
        try:
            sess.fechar()
        except Exception:
            pass
        proc.terminate()
        _matar_porta(PORTA)


def test_sem_chrome_erro_claro():
    _matar_porta(PORTA)
    sess = conexao.AutomacaoLocal(PORTA + 1)
    with pytest.raises(conexao.SemChrome):
        sess.conectar()


def test_logado_detecta_sessao(sessao):
    assert sessao.logado() is True
    sessao.exigir_login()           # não levanta


def test_sem_login_avisa(sessao):
    # Tira o cookie: /portal/servicos cai para /portal/login.
    sessao._browser.contexts[0].clear_cookies()
    assert sessao.logado() is False
    with pytest.raises(conexao.SemLogin):
        sessao.exigir_login()


def test_consultar_lista_meses_com_valores(sessao):
    sessao.exigir_login()
    sessao.definir_perfil(CNPJ_A)
    comps = sessao.consultar(CNPJ_A)
    por_mes = {c['competencia']: c for c in comps}
    assert set(por_mes) == {'202609', '202602'}
    assert por_mes['202602']['total'] == 359.93
    assert por_mes['202602']['grupos'][0]['vencimento'] == '23/10/2026'
    assert por_mes['202602']['composicao']['fgts_mensal'] == 312.0


def test_perfil_sem_procuracao_erro(sessao):
    sessao.exigir_login()
    with pytest.raises(FalhaFgts, match='procuração'):
        sessao.definir_perfil('11444777000161')


def test_emitir_rapida_pega_pdf_e_confere_cnpj(sessao):
    sessao.exigir_login()
    sessao.definir_perfil(CNPJ_A)
    guias = sessao.emitir(CNPJ_A, '202602')          # data = hoje → Guia Rápida
    assert len(guias) == 1 and guias[0]['valor'] == 359.93
    assert guias[0]['identificador'] == '0126102368549700-5'
    assert sessao.pdfs[guias[0]['arquivo']].startswith(b'%PDF')


def test_datas_permitidas_le_calendario(sessao):
    sessao.exigir_login()
    sessao.definir_perfil(CNPJ_A)
    datas = sessao.datas_permitidas(CNPJ_A, '202609')
    assert datas and set(datas).issubset(set(DATAS_PARAM))


def test_emitir_parametrizada_com_vencimento(sessao):
    sessao.exigir_login()
    sessao.definir_perfil(CNPJ_A)
    alvo = DATAS_PARAM[5]
    guias = sessao.emitir(CNPJ_A, '202609', alvo)
    assert len(guias) == 1
    assert guias[0]['vencimento'] == f'{alvo[8:]}/{alvo[5:7]}/{alvo[:4]}'
    assert sessao.pdfs[guias[0]['arquivo']].startswith(b'%PDF')


def test_pdf_de_outra_empresa_barrado(sessao):
    sessao.exigir_login()
    with pytest.raises(FalhaFgts, match='confere'):
        sessao._receber_pdf(CNPJ_A, 'x.pdf', GFD_OUTRA)
