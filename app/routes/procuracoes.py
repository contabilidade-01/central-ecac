"""Tela e API da situação de procuração por empresa.

⚠️ DESVIO INTENCIONAL (5o) — NÃO existe no exe. Pedido do Jean (31/07/2026).

Por que uma tela separada em vez de mexer na existente: o React do exe é bundle
compilado e não temos o fonte (`app/static/app/assets/index-*.js`). A tabela
"Acompanhamento" nem exibe o campo `erro`. Esta página é servida direto pelo Flask e
não toca em nada do exe.

Rotas (prefixo `/api/procuracoes` + a página em `/procuracoes`) — não aparecem na
auditoria de rotas porque ela só compara os prefixos que existem no exe.
"""

from flask import Blueprint, jsonify, render_template_string, request

from app.models import Company
from app.services.procuracao_service import (
    SITUACAO_DESCONHECIDA,
    SITUACAO_OK,
    SITUACAO_SEM_PROCURACAO,
    ProcuracaoService,
)

procuracoes_bp = Blueprint('procuracoes', __name__)


def _linhas():
    """Junta as empresas do banco com o que o mapa de procurações sabe delas."""
    dados = ProcuracaoService.carregar()
    registros = dados.get('empresas', {})
    linhas = []
    for company in Company.query.order_by(Company.razao_social.asc()).all():
        registro = registros.get(company.cnpj, {})
        ultimo = registro.get('ultimo_erro') or {}
        pode, motivo = ProcuracaoService.pode_gastar(company)
        linhas.append({
            'company_id': company.id,
            'razao_social': company.razao_social,
            'cnpj': company.cnpj,
            'ativo': bool(company.ativo),
            'situacao': registro.get('situacao', SITUACAO_DESCONHECIDA),
            'marcado_manualmente': bool(registro.get('marcado_manualmente')),
            'erros_seguidos': registro.get('erros_seguidos', 0),
            'ultimo_ok_em': registro.get('ultimo_ok_em'),
            'ultimo_erro_em': registro.get('ultimo_erro_em'),
            'erro_codigo': ultimo.get('codigo'),
            'erro_texto': ultimo.get('texto'),
            'erro_http': ultimo.get('http_status'),
            'erro_servico': ultimo.get('servico'),
            'erro_bruto': ultimo.get('bruto'),
            'observacao': registro.get('observacao'),
            'pode_gastar': pode,
            'motivo_bloqueio': motivo,
        })
    return linhas


@procuracoes_bp.get('/api/procuracoes')
def listar_procuracoes():
    resumo = ProcuracaoService.resumo()
    resumo['empresas'] = _linhas()
    return jsonify(resumo)


@procuracoes_bp.post('/api/procuracoes/marcar')
def marcar_procuracao():
    payload = request.get_json(silent=True) or {}
    cnpj = str(payload.get('cnpj') or '').strip()
    situacao = str(payload.get('situacao') or '').strip()
    if not cnpj:
        return jsonify({'success': False, 'message': 'Informe o CNPJ.'}), 400
    if situacao not in (SITUACAO_OK, SITUACAO_SEM_PROCURACAO):
        return jsonify({
            'success': False,
            'message': f"situacao deve ser '{SITUACAO_OK}' ou '{SITUACAO_SEM_PROCURACAO}'.",
        }), 400

    registro = ProcuracaoService.marcar(cnpj, situacao, payload.get('observacao', ''))
    return jsonify({'success': True, 'registro': registro})


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Procurações — Central Pendências e-CAC</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, "Segoe UI", sans-serif; margin: 0; padding: 24px;
         background: #f5f6f8; color: #1c1e21; }
  @media (prefers-color-scheme: dark) { body { background:#15171a; color:#e8eaed; }
    .card { background:#1e2125 !important; border-color:#2c3036 !important; }
    th { background:#23262b !important; } td, th { border-color:#2c3036 !important; } }
  h1 { font-size: 20px; margin: 0 0 4px; }
  p.sub { margin: 0 0 20px; color: #6b7280; font-size: 14px; }
  .card { background:#fff; border:1px solid #e3e5e8; border-radius:10px; padding:16px;
          margin-bottom:16px; }
  .tiles { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
  .tile { flex:1 1 150px; background:#fff; border:1px solid #e3e5e8; border-radius:10px;
          padding:12px 16px; }
  .tile b { display:block; font-size:26px; line-height:1.2; }
  .tile span { font-size:12px; color:#6b7280; text-transform:uppercase; letter-spacing:.04em; }
  .table-wrap { overflow-x:auto; }
  table { border-collapse: collapse; width:100%; font-size:13px; }
  th, td { border-bottom:1px solid #e3e5e8; padding:8px 10px; text-align:left;
           vertical-align:top; }
  th { background:#f0f1f3; font-weight:600; position:sticky; top:0; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
           font-weight:600; white-space:nowrap; }
  .ok   { background:#e6f4ea; color:#137333; }
  .warn { background:#fef7e0; color:#8a6116; }
  .bad  { background:#fce8e6; color:#b3261e; }
  .mute { background:#eceff1; color:#5f6368; }
  button { font: inherit; padding:4px 10px; border-radius:6px; border:1px solid #c7cad1;
           background:#fff; cursor:pointer; }
  button:hover { background:#f0f1f3; }
  code { font-size:12px; word-break:break-word; }
  .erro { color:#b3261e; }
</style>
</head>
<body>
  <h1>Procurações e bloqueio de chamadas pagas</h1>
  <p class="sub">
    O indicador da caixa postal é <b>gratuito</b> e serve de sonda: quem a SERPRO recusa
    fica marcado aqui e deixa de consumir as chamadas <b>pagas</b> (lista e detalhe).
    Esta tela não faz nenhuma chamada à SERPRO.
  </p>

  <div class="tiles" id="tiles"></div>

  <div class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Empresa</th><th>CNPJ</th><th>Situação</th><th>Chamadas pagas</th>
            <th>Último erro da SERPRO</th><th>Ação</th>
          </tr>
        </thead>
        <tbody id="corpo"><tr><td colspan="6">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>

<script>
const BADGE = { ok:'ok', sem_procuracao:'bad', erro:'warn', desconhecida:'mute' };
const ROTULO = { ok:'Com procuração', sem_procuracao:'SEM procuração',
                 erro:'Erro', desconhecida:'Não verificada' };

function texto(v) { return (v === null || v === undefined || v === '') ? '—' : String(v); }

async function carregar() {
  const r = await fetch('/api/procuracoes');
  const d = await r.json();
  const c = d.contagem || {};
  document.getElementById('tiles').innerHTML = `
    <div class="tile"><span>Empresas</span><b>${d.empresas.length}</b></div>
    <div class="tile"><span>Com procuração</span><b>${c.ok || 0}</b></div>
    <div class="tile"><span>Sem procuração</span><b>${c.sem_procuracao || 0}</b></div>
    <div class="tile"><span>Com erro</span><b>${c.erro || 0}</b></div>
    <div class="tile"><span>Não verificadas</span><b>${c.desconhecida || 0}</b></div>`;

  document.getElementById('corpo').innerHTML = d.empresas.map(e => {
    const erro = e.erro_texto || e.erro_codigo
      ? `<code>${texto(e.erro_codigo)}</code> ${texto(e.erro_texto)}
         <br><small>${texto(e.erro_servico)} · HTTP ${texto(e.erro_http)} · ${texto(e.erro_erro_em || e.ultimo_erro_em)}</small>`
      : '—';
    const acao = e.situacao === 'sem_procuracao'
      ? `<button onclick="marcar('${e.cnpj}','ok')">Liberar</button>`
      : `<button onclick="marcar('${e.cnpj}','sem_procuracao')">Marcar sem procuração</button>`;
    return `<tr>
      <td>${texto(e.razao_social)}</td>
      <td>${texto(e.cnpj)}</td>
      <td><span class="badge ${BADGE[e.situacao] || 'mute'}">${ROTULO[e.situacao] || e.situacao}</span>
          ${e.marcado_manualmente ? '<br><small>marcada na mão</small>' : ''}</td>
      <td>${e.pode_gastar ? '<span class="badge ok">liberadas</span>'
                          : `<span class="badge bad">travadas</span><br><small>${texto(e.motivo_bloqueio)}</small>`}</td>
      <td class="${e.erro_texto ? 'erro' : ''}">${erro}</td>
      <td>${acao}</td>
    </tr>`;
  }).join('');
}

async function marcar(cnpj, situacao) {
  await fetch('/api/procuracoes/marcar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cnpj, situacao }),
  });
  carregar();
}

carregar();
</script>
</body>
</html>
"""


@procuracoes_bp.get('/procuracoes')
def pagina_procuracoes():
    return render_template_string(PAGINA)
