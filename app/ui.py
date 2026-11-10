"""Estilo compartilhado das telas servidas pelo Flask.

As páginas novas (`/procuracoes`, `/agendamento`, `/restaurar`, `/usuarios`, login)
não existem na SPA compilada, então cada uma nasceu com o seu próprio `<style>`. Ao
padronizar num único lugar, todas passam a ter a mesma cara — e mudar a identidade
visual vira uma edição só.

Nada aqui altera o comportamento: é exclusivamente aparência.
"""

# Paleta do novo frontend (desvio 28): escuro com destaque azul, claro opcional. A chave
# `central_tema` no localStorage é a mesma do /app, então o tema acompanha o usuário.
CSS = """
  :root {
    color-scheme: dark;
    --fundo:      #070b14;
    --superficie: #0e1421;
    --superficie-2: #151d2e;
    --borda:      #26324a;
    --texto:      #f2f5fa;
    --suave:      #a3afc4;
    --escuro:     #151d2e;
    --escuro-2:   #0b111c;
    --primaria:   #4d8dff;
    --primaria-h: #6aa0ff;
    --primaria-tx:#06122b;
    --ok-bg:      rgba(61,220,132,.14);  --ok-tx:   #3ddc84;
    --alerta-bg:  rgba(255,159,67,.14);  --alerta-tx: #ff9f43;
    --erro-bg:    rgba(255,123,123,.14); --erro-tx: #ff7b7b;
    --neutro-bg:  rgba(163,175,196,.12); --neutro-tx: #a3afc4;
    --info-bg:    rgba(77,141,255,.16);  --info-tx: #8db6ff;
    --raio:       13px;
    --sombra:     none;
    --fonte: Manrope, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  :root[data-tema="claro"] {
    color-scheme: light;
    --fundo:#f4f6fb; --superficie:#ffffff; --superficie-2:#eef2f9; --borda:#d7deea;
    --texto:#0f172a; --suave:#55627a; --escuro:#eef2f9; --escuro-2:#e6ebf4;
    --primaria:#1f5fd6; --primaria-h:#1a4fb3; --primaria-tx:#ffffff;
    --ok-bg:#dcfce7; --ok-tx:#15803d; --alerta-bg:#fef3c7; --alerta-tx:#b45309;
    --erro-bg:#fee2e2; --erro-tx:#b91c1c; --neutro-bg:#eef2f9; --neutro-tx:#475569;
    --info-bg:#dbeafe; --info-tx:#1e40af;
    --sombra: 0 1px 2px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.05);
  }
  * { box-sizing: border-box; }
  body { font-family: var(--fonte); font-weight:500;
         margin:0; background: var(--fundo); color: var(--texto);
         -webkit-font-smoothing: antialiased; }
  :focus-visible { outline:2px solid var(--primaria); outline-offset:2px; }

  /* ============================================================ layout ==== */
  .app { display:flex; min-height:100vh; }

  .lateral { width:236px; flex:0 0 236px; color:var(--texto); border-right:1px solid var(--borda);
             background:color-mix(in srgb, var(--fundo) 70%, var(--superficie));
             display:flex; flex-direction:column; position:sticky; top:0; height:100vh;
             transition:margin-left .18s ease, opacity .18s ease; overflow-y:auto; }
  .lateral::-webkit-scrollbar { width:6px; }
  .lateral::-webkit-scrollbar-thumb { background:var(--borda); border-radius:3px; }

  .marca-topo { display:flex; align-items:center; gap:11px; padding:18px 18px 14px; }
  .marca { width:32px; height:32px; border-radius:9px; flex:0 0 auto;
           background:var(--primaria); color:var(--primaria-tx);
           display:flex; align-items:center; justify-content:center;
           font-weight:700; font-size:13px; }
  .marca-topo b { display:block; font-size:15px; font-weight:800; color:var(--texto); line-height:1.25; }
  .marca-topo span { display:block; font-size:11px; color:var(--suave); }

  .lateral nav { padding:4px 10px 16px; flex:1; }
  .secao { font-size:11px; font-weight:700; color:var(--suave); padding:14px 10px 5px; }
  .item { display:flex; align-items:center; gap:11px; padding:9px 10px; border-radius:9px;
          color:var(--texto); text-decoration:none; font-size:13.5px; font-weight:600; cursor:pointer;
          position:relative; transition:background .15s, color .15s;
          border:0; background:transparent; width:100%; text-align:left;
          font-family:inherit; margin-bottom:1px; }
  .item svg { width:17px; height:17px; flex:0 0 auto; stroke:currentColor; fill:none;
              stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .item:hover { background:var(--superficie-2); }
  .item.ativo { background:var(--primaria); color:var(--primaria-tx); font-weight:800;
                box-shadow:0 0 0 1px color-mix(in srgb, var(--primaria) 60%, transparent),
                           0 6px 18px color-mix(in srgb, var(--primaria) 35%, transparent); }
  .item.ativo::before { content:""; position:absolute; left:-10px; top:8px; bottom:8px; width:4px;
                        border-radius:0 4px 4px 0; background:var(--primaria); }
  .item .novo { margin-left:auto; font-size:10px; border:1px solid currentColor; border-radius:99px;
                padding:0 6px; opacity:.8; }
  .item.sair { color:var(--erro-tx); }
  .item.sair:hover { background:var(--erro-bg); }

  .lateral-rodape { padding:14px 18px 18px; font-size:11px; color:var(--suave);
                    border-top:1px solid var(--borda); }

  .conteudo { flex:1; min-width:0; display:flex; flex-direction:column;
              /* a SPA compilada foi desenhada para largura cheia; contendo a rolagem
                 aqui, ela nunca empurra a barra lateral para fora da tela */
              overflow-x:auto; }
  .barra { background:color-mix(in srgb, var(--fundo) 70%, var(--superficie)); border-bottom:1px solid var(--borda);
           padding:12px 22px; display:flex; align-items:center; gap:14px;
           position:sticky; top:0; z-index:20; }
  .barra b { font-size:14px; font-weight:600; }
  .barra .fim { margin-left:auto; display:flex; align-items:center; gap:10px; }
  .alternar { background:transparent; border:1px solid var(--borda); border-radius:8px;
              padding:6px 9px; cursor:pointer; color:var(--suave); line-height:0;
              margin:0; }
  .alternar:hover { background:var(--superficie-2); color:var(--texto); }
  .alternar svg { width:17px; height:17px; stroke:currentColor; fill:none;
                  stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .busca-topo { flex:0 1 420px; display:flex; justify-content:space-between; align-items:center; gap:10px;
                border:1px solid var(--borda); background:var(--superficie); border-radius:10px;
                padding:8px 12px; color:var(--suave); text-decoration:none; font-size:13px; }
  .busca-topo:hover { border-color:var(--primaria); }
  .busca-topo kbd { font:700 11px var(--fonte); border:1px solid var(--borda); border-radius:5px;
                    padding:1px 6px; color:var(--texto); }
  .alternar-tema { margin:0; }
  .nome-usuario { font-size:12px; color:var(--suave); }
  .pilula { display:inline-flex; align-items:center; gap:7px; font-size:12px;
            font-weight:500; color:var(--ok-tx); background:var(--ok-bg);
            padding:5px 12px; border-radius:999px; }
  .pilula i { width:7px; height:7px; border-radius:50%; background:currentColor;
              display:block; }

  /* retraída */
  .app.retraida .lateral { margin-left:-236px; opacity:0; pointer-events:none; }

  @media (max-width: 760px) {
    .lateral { position:fixed; z-index:40; box-shadow:0 0 40px rgba(0,0,0,.4); }
    .app:not(.retraida) .lateral { margin-left:0; }
    .app .lateral { margin-left:-236px; opacity:0; pointer-events:none; }
    .app.aberta .lateral { margin-left:0; opacity:1; pointer-events:auto; }
  }

  /* ------------------------------------------------------------ topo antigo */
  .topo { background: var(--escuro-2); color:#e2e8f0; padding:14px 24px;
          display:flex; align-items:center; gap:14px; }
  .topo .marca { width:34px; height:34px; border-radius:10px; flex:0 0 auto;
                 background: linear-gradient(135deg,#2563eb,#1e40af); color:#fff;
                 display:flex; align-items:center; justify-content:center;
                 font-weight:700; font-size:13px; letter-spacing:-.02em; }
  .topo b { display:block; font-size:14px; line-height:1.25; color:#fff; }
  .topo span { display:block; font-size:11px; color:#94a3b8; }
  .topo .fim { margin-left:auto; display:flex; align-items:center; gap:10px; }
  .topo a { color:#cbd5e1; text-decoration:none; font-size:13px; padding:6px 12px;
            border:1px solid #334155; border-radius:8px; }
  .topo a:hover { background:#1e293b; color:#fff; }

  /* ------------------------------------------------------------ conteúdo */
  /* `width:100%` é obrigatório: dentro de um flex column, `margin:0 auto` desliga o
     stretch no eixo transversal e o bloco passa a se dimensionar pelo CONTEÚDO,
     estourando a largura da área e criando rolagem horizontal. */
  .wrap { width:100%; max-width:1120px; margin:0 auto; padding:28px 24px 48px; }
  .conteudo > * { min-width:0; }
  h1 { font-size:24px; font-weight:800; margin:0 0 6px; letter-spacing:-.02em; }
  p.sub { margin:0 0 24px; color: var(--suave); font-size:14px; line-height:1.6;
          max-width:80ch; }
  h2 { font-size:15px; font-weight:800; margin:0 0 14px; }

  .card { background: var(--superficie); border:1px solid var(--borda);
          border-radius: var(--raio); padding:20px; margin-bottom:18px;
          box-shadow: var(--sombra); }

  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
           gap:14px; margin-bottom:18px; }
  .tile { background: var(--superficie); border:1px solid var(--borda);
          border-radius: var(--raio); padding:16px 18px; box-shadow: var(--sombra); }
  .tile b { display:block; font-size:28px; font-weight:700; line-height:1.2;
            letter-spacing:-.02em; }
  .tile span { display:block; font-size:11px; color:var(--suave); font-weight:600;
               text-transform:uppercase; letter-spacing:.06em; margin-top:2px; }

  /* -------------------------------------------------------------- tabela */
  .table-wrap { overflow-x:auto; border-radius:10px; }
  table { border-collapse:collapse; width:100%; font-size:13px; }
  th, td { border-bottom:1px solid var(--borda); padding:11px 12px; text-align:left;
           vertical-align:middle; }
  th { background:transparent; font-weight:700; color:var(--suave); font-size:11.5px;
       white-space:nowrap; }
  tbody tr:hover { background:var(--superficie-2); }
  tbody tr:last-child td { border-bottom:0; }

  .badge { display:inline-block; padding:3px 10px; border-radius:999px; font-size:11px;
           font-weight:600; white-space:nowrap; }
  .ok    { background:var(--ok-bg);     color:var(--ok-tx); }
  .warn  { background:var(--alerta-bg); color:var(--alerta-tx); }
  .bad   { background:var(--erro-bg);   color:var(--erro-tx); }
  .mute  { background:var(--neutro-bg); color:var(--neutro-tx); }
  .adm   { background:var(--info-bg);   color:var(--info-tx); }

  /* ------------------------------------------------------------ controles */
  button { font:inherit; font-size:13px; font-weight:500; padding:8px 14px;
           border-radius:9px; border:1px solid var(--borda); font-weight:700;
           background: var(--superficie-2); color: var(--texto); cursor:pointer;
           margin:0 6px 6px 0; transition:background .12s, border-color .12s; }
  button:hover { border-color:var(--primaria); }
  button:disabled { opacity:.55; cursor:progress; }
  button.primario { background: var(--primaria); color:var(--primaria-tx); border-color: var(--primaria);
                    font-weight:800; }
  button.primario:hover { background: var(--primaria-h); border-color: var(--primaria-h); }
  button.azul { background: var(--primaria); color:var(--primaria-tx); border-color: var(--primaria);
                font-weight:800; }
  button.azul:hover { background: var(--primaria-h); border-color: var(--primaria-h); }
  button.perigo { color: var(--erro-tx); border-color:var(--erro-tx); }
  button.perigo:hover { background: var(--erro-bg); }

  input, select { font:inherit; font-size:14px; padding:9px 12px;
                  border:1px solid var(--borda); border-radius:9px;
                  background: var(--superficie); color: var(--texto); }
  input:focus, select:focus { outline:2px solid var(--primaria); outline-offset:1px;
                              border-color: var(--primaria); }
  label.linha { display:block; font-size:12px; font-weight:600; margin:0 0 6px;
                color:var(--suave); text-transform:uppercase; letter-spacing:.04em; }
  .grade { display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end; }
  .caixas { display:flex; flex-wrap:wrap; gap:8px 18px; margin-top:10px; }
  .caixas label { font-size:13px; font-weight:400; display:flex; gap:7px;
                  align-items:center; text-transform:none; letter-spacing:0; }

  /* --------------------------------------------------------------- avisos */
  .msg { margin-top:14px; padding:11px 14px; border-radius:10px; font-size:13px;
         line-height:1.55; }
  .msg.ok  { background:var(--ok-bg);  color:var(--ok-tx); }
  .msg.bad { background:var(--erro-bg); color:var(--erro-tx); }
  .erro   { background:var(--erro-bg); color:var(--erro-tx); border-radius:10px;
            padding:11px 14px; font-size:13px; margin-bottom:16px; }
  .aviso  { background:var(--alerta-bg); color:var(--alerta-tx); border-radius:10px;
            padding:11px 14px; font-size:13px; margin-bottom:16px; line-height:1.55; }
  .dica   { color:var(--suave); font-size:13px; margin:0 0 14px; line-height:1.6; }

  .rodape { font-size:13px; color:var(--suave); text-align:center; margin-top:24px; }
  a { color: var(--primaria); }
  code { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size:12px; }

  dialog { border:1px solid var(--borda); border-radius:16px; padding:24px;
           background:var(--superficie); color:var(--texto);
           max-width:660px; width:92%; box-shadow:0 20px 50px rgba(15,23,42,.22); }
  dialog::backdrop { background: rgba(2,6,23,.6); }

  /* ------------------------------------------------ telas de autenticação */
  body.centro { display:flex; align-items:center; justify-content:center;
                min-height:100vh; padding:24px; }
  body.centro .card { width:100%; max-width:400px; padding:30px; margin:0; }
  body.centro h1 { font-size:20px; }
  body.centro input { width:100%; margin-bottom:16px; }
  body.centro label { display:block; font-size:13px; font-weight:600; margin:0 0 6px; }
  body.centro button { width:100%; margin:0; padding:11px; }
  .selo { display:flex; align-items:center; gap:11px; margin-bottom:20px; }
  .selo .marca { width:38px; height:38px; border-radius:11px;
                 background: var(--primaria); color:var(--primaria-tx);
                 display:flex; align-items:center; justify-content:center;
                 font-weight:800; font-size:14px; }
"""


def _icone(d: str) -> str:
    return f'<svg viewBox="0 0 24 24"><path d="{d}"/></svg>' if d else ''


def lateral(ativo: str = '', usuario=None) -> str:
    """Barra lateral + barra superior, já abertas para receber o conteúdo da página.

    `ativo` é a chave da rotina da tela atual, para o destaque.
    Fecha com a constante FIM.
    """
    from app.services import permissoes

    if usuario is None:
        try:
            from app.security import usuario_atual
            usuario = usuario_atual()
        except Exception:
            usuario = None
    usuario = usuario or {'papel': 'admin', 'rotinas': 'todas', 'empresas': 'todas'}

    # Mesmo menu do novo frontend (/app): "Mesa do dia" no topo no lugar do Dashboard
    # antigo, e o FGTS abrindo na tela nova.
    menu = permissoes.menu_do_usuario(usuario)
    tem_mesa = any(i['chave'] == 'dashboard' for g in menu for i in g['itens'])
    blocos = []
    if tem_mesa:
        blocos.append('<a class="item" href="/app"><span>Mesa do dia</span><span class="novo">novo</span></a>')
    for grupo in menu:
        itens = [i for i in grupo['itens'] if i['chave'] != 'dashboard']
        if not itens:
            continue
        linhas = [f'<div class="secao">{grupo["titulo"]}</div>']
        for item in itens:
            destino = item.get('url') or ('/?aba=' + item['chave'])
            novo = ''
            if item['chave'] == 'fgts':
                destino, novo = '/app/fgts', '<span class="novo">novo</span>'
            marca = ' ativo' if item['chave'] == ativo else ''
            atual = ' aria-current="page"' if marca else ''
            linhas.append(
                f'<a class="item{marca}" href="{destino}"{atual}>'
                f'{_icone(item.get("icone", ""))}<span>{item["rotulo"]}</span>{novo}</a>')
        blocos.append('\n'.join(linhas))

    # Só mostra o nome de usuário de verdade; o pseudo-usuário do fallback por
    # variável de ambiente não tem nome que faça sentido exibir.
    nome = ''
    if usuario and usuario.get('id') not in (None, 'ambiente'):
        nome = usuario.get('nome') or usuario.get('usuario') or ''

    return f"""
<script>try{{if(localStorage.getItem('central_tema')==='claro')document.documentElement.dataset.tema='claro';}}catch(e){{}}</script>
<div class="app" id="app">
  <aside class="lateral">
    <div class="marca-topo">
      <div class="marca">eC</div>
      <div><b>Central Ecac</b><span>Nescon Contabilidade</span></div>
    </div>
    <nav>
      {''.join(blocos)}
      <div class="secao">Sess&atilde;o</div>
      <a class="item sair" href="/logout">{_icone(permissoes.ICONES['sair'])}<span>Sair</span></a>
    </nav>
    <div class="lateral-rodape">{(nome + '<br>') if nome else ''}Sistema da Nescon Contabilidade</div>
  </aside>
  <div class="conteudo">
    <div class="barra">
      <button class="alternar" onclick="alternarLateral()" title="Recolher menu">
        <svg viewBox="0 0 24 24"><path d="M3 4h18v16H3zM9 4v16"/></svg>
      </button>
      <a class="busca-topo" href="/app" title="Abrir a busca na Mesa do dia">Buscar empresa, CNPJ ou tela <kbd>Ctrl K</kbd></a>
      <div class="fim">
        <button type="button" class="alternar-tema" onclick="alternarTema()" id="botaoTema">Tema claro</button>
        {('<span class="nome-usuario">' + nome + '</span>') if nome else ''}
      </div>
    </div>
"""


FIM = """
  </div>
</div>
<script>
function alternarTema() {
  var claro = document.documentElement.dataset.tema !== 'claro';
  if (claro) document.documentElement.dataset.tema = 'claro'; else delete document.documentElement.dataset.tema;
  try { localStorage.setItem('central_tema', claro ? 'claro' : 'escuro'); } catch (e) {}
  var b = document.getElementById('botaoTema'); if (b) b.textContent = claro ? 'Tema escuro' : 'Tema claro';
}
(function () {
  var b = document.getElementById('botaoTema');
  if (b && document.documentElement.dataset.tema === 'claro') b.textContent = 'Tema escuro';
})();
function alternarLateral() {
  var app = document.getElementById('app');
  var estreito = window.matchMedia('(max-width: 760px)').matches;
  if (estreito) { app.classList.toggle('aberta'); return; }
  app.classList.toggle('retraida');
  try { localStorage.setItem('ecac_lateral', app.classList.contains('retraida') ? '1' : '0'); } catch (e) {}
}
(function () {
  try {
    if (localStorage.getItem('ecac_lateral') === '1') {
      document.getElementById('app').classList.add('retraida');
    }
  } catch (e) {}
})();
</script>
"""


def topo(titulo: str = 'Central Pendências e-CAC', voltar: bool = True) -> str:
    """Barra superior comum às telas internas."""
    link = '<a href="/">Voltar ao painel</a>' if voltar else ''
    return f"""
  <div class="topo">
    <div class="marca">eC</div>
    <div><b>Central Ecac</b><span>Nescon Contabilidade</span></div>
    <div class="fim">{link}</div>
  </div>"""


SELO = """
    <div class="selo">
      <div class="marca">eC</div>
      <div><b style="display:block;font-size:14px">Central Ecac</b>
      <span style="display:block;font-size:11px;color:var(--suave)">Nescon Contabilidade</span></div>
    </div>"""
