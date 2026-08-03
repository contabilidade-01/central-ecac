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

  /* ---------------------------------------------------------------- topo */
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
  .wrap { max-width:1120px; margin:0 auto; padding:28px 24px 48px; }
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
