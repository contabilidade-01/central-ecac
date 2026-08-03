"""Estilo compartilhado das telas servidas pelo Flask.

As páginas novas (`/procuracoes`, `/agendamento`, `/restaurar`, `/usuarios`, login)
não existem na SPA compilada, então cada uma nasceu com o seu próprio `<style>`. Ao
padronizar num único lugar, todas passam a ter a mesma cara — e mudar a identidade
visual vira uma edição só.

Nada aqui altera o comportamento: é exclusivamente aparência.
"""

# Paleta alinhada ao painel: fundo claro, cartões brancos, barra escura em ardósia.
CSS = """
  :root {
    color-scheme: light;
    --fundo:      #f1f5f9;
    --superficie: #ffffff;
    --borda:      #e2e8f0;
    --texto:      #0f172a;
    --suave:      #64748b;
    --escuro:     #1e293b;
    --escuro-2:   #0f172a;
    --primaria:   #1d4ed8;
    --primaria-h: #1e40af;
    --ok-bg:      #dcfce7;  --ok-tx:   #15803d;
    --alerta-bg:  #fef3c7;  --alerta-tx: #b45309;
    --erro-bg:    #fee2e2;  --erro-tx: #b91c1c;
    --neutro-bg:  #f1f5f9;  --neutro-tx: #475569;
    --info-bg:    #dbeafe;  --info-tx: #1e40af;
    --raio:       14px;
    --sombra:     0 1px 2px rgba(15,23,42,.04), 0 4px 12px rgba(15,23,42,.05);
  }
  * { box-sizing: border-box; }
  body { font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
         margin:0; background: var(--fundo); color: var(--texto);
         -webkit-font-smoothing: antialiased; }


  /* ============================================================ layout ==== */
  .app { display:flex; min-height:100vh; }

  .lateral { width:252px; flex:0 0 252px; background:var(--escuro-2); color:#cbd5e1;
             display:flex; flex-direction:column; position:sticky; top:0; height:100vh;
             transition:margin-left .18s ease, opacity .18s ease; overflow-y:auto; }
  .lateral::-webkit-scrollbar { width:6px; }
  .lateral::-webkit-scrollbar-thumb { background:#334155; border-radius:3px; }

  .marca-topo { display:flex; align-items:center; gap:11px; padding:18px 18px 14px; }
  .marca { width:36px; height:36px; border-radius:10px; flex:0 0 auto;
           background:linear-gradient(135deg,#2563eb,#1e40af); color:#fff;
           display:flex; align-items:center; justify-content:center;
           font-weight:700; font-size:13px; }
  .marca-topo b { display:block; font-size:14px; color:#fff; line-height:1.25; }
  .marca-topo span { display:block; font-size:11px; color:#94a3b8; }

  .lateral nav { padding:4px 10px 16px; flex:1; }
  .secao { font-size:10px; font-weight:700; color:#64748b; text-transform:uppercase;
           letter-spacing:.09em; padding:14px 10px 6px; }
  .item { display:flex; align-items:center; gap:11px; padding:9px 10px; border-radius:9px;
          color:#cbd5e1; text-decoration:none; font-size:13.5px; cursor:pointer;
          border:0; background:transparent; width:100%; text-align:left;
          font-family:inherit; margin-bottom:1px; }
  .item svg { width:17px; height:17px; flex:0 0 auto; stroke:currentColor; fill:none;
              stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .item:hover { background:#1e293b; color:#fff; }
  .item.ativo { background:#1d4ed8; color:#fff; font-weight:600; }
  .item.sair { color:#fca5a5; }
  .item.sair:hover { background:#7f1d1d; color:#fff; }

  .lateral-rodape { padding:14px 18px 18px; font-size:11px; color:#64748b;
                    border-top:1px solid #1e293b; }

  .conteudo { flex:1; min-width:0; display:flex; flex-direction:column;
              /* a SPA compilada foi desenhada para largura cheia; contendo a rolagem
                 aqui, ela nunca empurra a barra lateral para fora da tela */
              overflow-x:auto; }
  .barra { background:var(--superficie); border-bottom:1px solid var(--borda);
           padding:12px 22px; display:flex; align-items:center; gap:14px;
           position:sticky; top:0; z-index:20; }
  .barra b { font-size:14px; font-weight:600; }
  .barra .fim { margin-left:auto; display:flex; align-items:center; gap:10px; }
  .alternar { background:transparent; border:1px solid var(--borda); border-radius:8px;
              padding:6px 9px; cursor:pointer; color:var(--suave); line-height:0;
              margin:0; }
  .alternar:hover { background:#f1f5f9; color:var(--texto); }
  .alternar svg { width:17px; height:17px; stroke:currentColor; fill:none;
                  stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .pilula { display:inline-flex; align-items:center; gap:7px; font-size:12px;
            font-weight:500; color:var(--ok-tx); background:var(--ok-bg);
            padding:5px 12px; border-radius:999px; }
  .pilula i { width:7px; height:7px; border-radius:50%; background:currentColor;
              display:block; }

  /* retraída */
  .app.retraida .lateral { margin-left:-252px; opacity:0; pointer-events:none; }

  @media (max-width: 760px) {
    .lateral { position:fixed; z-index:40; box-shadow:0 0 40px rgba(0,0,0,.4); }
    .app:not(.retraida) .lateral { margin-left:0; }
    .app .lateral { margin-left:-252px; opacity:0; pointer-events:none; }
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
  h1 { font-size:26px; font-weight:700; margin:0 0 6px; letter-spacing:-.02em; }
  p.sub { margin:0 0 24px; color: var(--suave); font-size:14px; line-height:1.6;
          max-width:80ch; }
  h2 { font-size:15px; font-weight:600; margin:0 0 14px; }

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
  th { background:#f8fafc; font-weight:600; color:var(--suave); font-size:11px;
       text-transform:uppercase; letter-spacing:.05em; white-space:nowrap; }
  tbody tr:hover { background:#f8fafc; }
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
           border-radius:9px; border:1px solid var(--borda);
           background: var(--superficie); color: var(--texto); cursor:pointer;
           margin:0 6px 6px 0; transition:background .12s, border-color .12s; }
  button:hover { background:#f1f5f9; border-color:#cbd5e1; }
  button:disabled { opacity:.55; cursor:progress; }
  button.primario { background: var(--escuro); color:#fff; border-color: var(--escuro);
                    font-weight:600; }
  button.primario:hover { background: var(--escuro-2); border-color: var(--escuro-2); }
  button.azul { background: var(--primaria); color:#fff; border-color: var(--primaria);
                font-weight:600; }
  button.azul:hover { background: var(--primaria-h); border-color: var(--primaria-h); }
  button.perigo { color: var(--erro-tx); border-color:#fecaca; }
  button.perigo:hover { background: var(--erro-bg); }

  input, select { font:inherit; font-size:14px; padding:9px 12px;
                  border:1px solid #cbd5e1; border-radius:9px;
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
           max-width:660px; width:92%; box-shadow:0 20px 50px rgba(15,23,42,.22); }
  dialog::backdrop { background: rgba(15,23,42,.45); }

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
                 background: linear-gradient(135deg,#2563eb,#1e40af); color:#fff;
                 display:flex; align-items:center; justify-content:center;
                 font-weight:700; font-size:14px; }
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

    blocos = []
    for grupo in permissoes.menu_do_usuario(usuario):
        linhas = [f'<div class="secao">{grupo["titulo"]}</div>']
        for item in grupo['itens']:
            destino = item.get('url') or ('/?aba=' + item['chave'])
            marca = ' ativo' if item['chave'] == ativo else ''
            linhas.append(
                f'<a class="item{marca}" href="{destino}">'
                f'{_icone(item.get("icone", ""))}<span>{item["rotulo"]}</span></a>')
        blocos.append('\n'.join(linhas))

    nome = (usuario.get('nome') or usuario.get('usuario') or '') if usuario else ''

    return f"""
<div class="app" id="app">
  <aside class="lateral">
    <div class="marca-topo">
      <div class="marca">eC</div>
      <div><b>Central e-CAC</b><span>Pend&ecirc;ncias &amp; d&eacute;bitos</span></div>
    </div>
    <nav>
      {''.join(blocos)}
      <div class="secao">Sess&atilde;o</div>
      <a class="item sair" href="/logout">{_icone(permissoes.ICONES['sair'])}<span>Sair</span></a>
    </nav>
    <div class="lateral-rodape">{nome}<br>Direitos reservados @Warley.contador</div>
  </aside>
  <div class="conteudo">
    <div class="barra">
      <button class="alternar" onclick="alternarLateral()" title="Recolher menu">
        <svg viewBox="0 0 24 24"><path d="M3 4h18v16H3zM9 4v16"/></svg>
      </button>
      <b>Central Pend&ecirc;ncias e-CAC</b>
      <div class="fim"><span class="pilula"><i></i>Sistema no ar</span></div>
    </div>
"""


FIM = """
  </div>
</div>
<script>
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
    <div><b>Central e-CAC</b><span>Pendências &amp; débitos</span></div>
    <div class="fim">{link}</div>
  </div>"""


SELO = """
    <div class="selo">
      <div class="marca">eC</div>
      <div><b style="display:block;font-size:14px">Central e-CAC</b>
      <span style="display:block;font-size:11px;color:#64748b">Pendências &amp; débitos</span></div>
    </div>"""
