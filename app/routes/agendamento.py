"""Tela e API do agendamento automático + teto de gasto.

⚠️ DESVIO INTENCIONAL (8o) — NÃO existe no exe. Pedido do Jean (02/08/2026).

A tela de Configurações do sistema é parte da SPA React compilada (não temos o fonte),
então a configuração da automação fica nesta página própria, servida pelo Flask —
mesmo padrão de `/procuracoes`.

Rotas: `/agendamento` (página) e `/api/agendamento*` (API).
"""

from flask import Blueprint, jsonify, render_template_string, request

from app.services.agendamento_service import (
    MODULOS,
    executar_modulo,
    atualizar_modulo,
    resumo,
)
from app.services.limite_gasto_service import LimiteGastoService

agendamento_bp = Blueprint('agendamento', __name__)


@agendamento_bp.get('/api/agendamento')
def listar_agendamento():
    dados = resumo()
    dados['limite_gasto'] = LimiteGastoService.resumo()
    from app.scheduler import habilitado
    dados['scheduler_ligado'] = habilitado()
    return jsonify(dados)


@agendamento_bp.post('/api/agendamento/<modulo>')
def salvar_agendamento(modulo: str):
    if modulo not in MODULOS:
        return jsonify({'success': False, 'message': 'Módulo desconhecido.'}), 404

    payload = request.get_json(silent=True) or {}
    try:
        config = atualizar_modulo(modulo, payload)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    return jsonify({'success': True, 'config': config})


@agendamento_bp.post('/api/agendamento/<modulo>/executar')
def executar_agora(modulo: str):
    """Dispara a rotina na hora. ⚠️ consome API paga (menos o indicador da caixa postal)."""
    if modulo not in MODULOS:
        return jsonify({'success': False, 'message': 'Módulo desconhecido.'}), 404
    return jsonify(executar_modulo(modulo))


@agendamento_bp.post('/api/agendamento/limite')
def salvar_limite():
    payload = request.get_json(silent=True) or {}
    try:
        valor = float(payload.get('limite', 0))
        LimiteGastoService.definir_limite(valor)
    except (TypeError, ValueError) as exc:
        return jsonify({'success': False, 'message': f'valor inválido: {exc}'}), 400
    return jsonify({'success': True, 'limite_gasto': LimiteGastoService.resumo()})


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Automação — Central Pendências e-CAC</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, "Segoe UI", sans-serif; margin:0; padding:24px;
         background:#f5f6f8; color:#1c1e21; }
  @media (prefers-color-scheme: dark) { body { background:#15171a; color:#e8eaed; }
    .card { background:#1e2125 !important; border-color:#2c3036 !important; }
    input, select { background:#15171a !important; color:#e8eaed !important;
                    border-color:#2c3036 !important; } }
  h1 { font-size:20px; margin:0 0 4px; }
  p.sub { margin:0 0 20px; color:#6b7280; font-size:14px; }
  .card { background:#fff; border:1px solid #e3e5e8; border-radius:10px; padding:16px;
          margin-bottom:16px; }
  .card h3 { margin:0 0 4px; font-size:16px; }
  .card .desc { color:#6b7280; font-size:13px; margin:0 0 12px; }
  .linha { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end; }
  label { display:block; font-size:12px; color:#6b7280; margin-bottom:4px; }
  input, select { font:inherit; padding:6px 8px; border:1px solid #c7cad1;
                  border-radius:6px; background:#fff; }
  button { font:inherit; padding:6px 14px; border-radius:6px; border:1px solid #c7cad1;
           background:#fff; cursor:pointer; }
  button.primario { background:#1a73e8; color:#fff; border-color:#1a73e8; }
  button.perigo { background:#fff; color:#b3261e; border-color:#e6b4b0; }
  button:hover { filter:brightness(0.97); }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
           font-weight:600; }
  .on { background:#e6f4ea; color:#137333; }
  .off { background:#eceff1; color:#5f6368; }
  .aviso { background:#fef7e0; color:#8a6116; padding:10px 12px; border-radius:8px;
           font-size:13px; margin-bottom:16px; }
  .custo { font-size:12px; color:#8a6116; }
  .rodape { font-size:12px; color:#6b7280; margin-top:8px; }
</style>
</head>
<body>
  <h1>Automação das rotinas</h1>
  <p class="sub">
    Cada módulo roda sozinho na frequência escolhida. Antes de gastar, o sistema respeita
    o mapa de <a href="/procuracoes">procurações</a> e o teto mensal abaixo.
  </p>

  <div id="aviso-scheduler"></div>

  <div class="card">
    <h3>Teto mensal de gasto</h3>
    <p class="desc">Ao atingir o teto, as rotinas automáticas param de gastar.
       <b>0 = sem teto.</b> O indicador da caixa postal é gratuito e nunca é bloqueado.</p>
    <div class="linha">
      <div>
        <label>Limite (R$)</label>
        <input type="number" id="limite" step="0.01" min="0" style="width:120px">
      </div>
      <button class="primario" onclick="salvarLimite()">Salvar teto</button>
      <div id="gasto" class="rodape"></div>
    </div>
  </div>

  <div id="modulos"></div>

<script>
let DADOS = null;

function texto(v) { return (v === null || v === undefined || v === '') ? '—' : String(v); }
function dataBR(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('pt-BR');
}

async function carregar() {
  DADOS = await (await fetch('/api/agendamento')).json();

  document.getElementById('aviso-scheduler').innerHTML = DADOS.scheduler_ligado ? '' :
    `<div class="aviso"><b>O agendador está desligado neste ambiente.</b>
     As configurações abaixo ficam salvas, mas nada dispara sozinho até
     <code>SCHEDULER_ENABLED=1</code> estar definido (é o padrão no servidor).</div>`;

  const lg = DADOS.limite_gasto;
  document.getElementById('limite').value = lg.limite;
  document.getElementById('gasto').textContent =
    `Gasto no mês: R$ ${lg.gasto_mes.toFixed(2)}` +
    (lg.sem_teto ? ' · sem teto definido' : ` de R$ ${lg.limite.toFixed(2)} (restam R$ ${lg.restante.toFixed(2)})`);

  document.getElementById('modulos').innerHTML = DADOS.modulos.map(m => `
    <div class="card">
      <h3>${m.titulo} <span class="badge ${m.ativo ? 'on' : 'off'}">${m.ativo ? 'automático' : 'manual'}</span></h3>
      <p class="desc">${m.descricao}<br><span class="custo">Custo: ${m.custo}</span></p>
      <div class="linha">
        <div>
          <label>Automático</label>
          <select id="ativo-${m.modulo}">
            <option value="1" ${m.ativo ? 'selected' : ''}>Sim</option>
            <option value="0" ${m.ativo ? '' : 'selected'}>Não</option>
          </select>
        </div>
        <div>
          <label>Frequência</label>
          <select id="freq-${m.modulo}" onchange="alternar('${m.modulo}')">
            ${DADOS.frequencias.map(f =>
              `<option value="${f}" ${m.frequencia === f ? 'selected' : ''}>${f}</option>`).join('')}
          </select>
        </div>
        <div id="campo-mes-${m.modulo}">
          <label>Dia do mês (1–28)</label>
          <input type="number" id="dia-mes-${m.modulo}" min="1" max="28"
                 value="${m.dia_mes || 25}" style="width:90px">
        </div>
        <div id="campo-semana-${m.modulo}">
          <label>Dia da semana</label>
          <select id="dia-semana-${m.modulo}">
            ${DADOS.dias_semana.map((d, i) =>
              `<option value="${i}" ${m.dia_semana === i ? 'selected' : ''}>${d}</option>`).join('')}
          </select>
        </div>
        <div>
          <label>Hora</label>
          <input type="time" id="hora-${m.modulo}" value="${m.hora || '03:00'}">
        </div>
        <button class="primario" onclick="salvar('${m.modulo}')">Salvar</button>
        <button class="perigo" onclick="executar('${m.modulo}')"
                ${m.em_execucao ? 'disabled' : ''}>Executar agora</button>
      </div>
      ${m.checkpoint ? `
        <div class="aviso" style="margin-top:12px">
          <b>⚠ Lote interrompido pelo teto de gasto</b> em ${dataBR(m.checkpoint.em)}.<br>
          ${m.checkpoint.concluidas.length} empresa(s) já processada(s) ·
          <b>${m.checkpoint.pendentes.length} pendente(s)</b>.<br>
          A próxima execução <b>continua de onde parou</b> — as já feitas não são
          consultadas (nem cobradas) de novo. Aumente o teto acima ou aguarde a virada
          do mês.
        </div>` : ''}
      <div class="rodape">
        Última execução: ${dataBR(m.ultima_execucao)}
        ${m.ultimo_resultado ? ` · resultado: <code>${texto(JSON.stringify(m.ultimo_resultado))}</code>` : ''}
        <br>Próxima: ${m.ativo ? dataBR(m.proxima_execucao) : 'desativado'}
      </div>
    </div>`).join('');

  DADOS.modulos.forEach(m => alternar(m.modulo));
}

function alternar(modulo) {
  const freq = document.getElementById(`freq-${modulo}`).value;
  document.getElementById(`campo-mes-${modulo}`).style.display = freq === 'mensal' ? '' : 'none';
  document.getElementById(`campo-semana-${modulo}`).style.display = freq === 'semanal' ? '' : 'none';
}

async function salvar(modulo) {
  const corpo = {
    ativo: document.getElementById(`ativo-${modulo}`).value === '1',
    frequencia: document.getElementById(`freq-${modulo}`).value,
    dia_mes: parseInt(document.getElementById(`dia-mes-${modulo}`).value, 10),
    dia_semana: parseInt(document.getElementById(`dia-semana-${modulo}`).value, 10),
    hora: document.getElementById(`hora-${modulo}`).value,
  };
  const r = await fetch(`/api/agendamento/${modulo}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corpo),
  });
  const d = await r.json();
  if (!d.success) { alert(d.message); return; }
  carregar();
}

async function salvarLimite() {
  const limite = parseFloat(document.getElementById('limite').value || '0');
  await fetch('/api/agendamento/limite', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limite }),
  });
  carregar();
}

async function executar(modulo) {
  const m = DADOS.modulos.find(x => x.modulo === modulo);
  if (!confirm(`Executar "${m.titulo}" agora?\\n\\nCusto: ${m.custo}\\n\\n` +
               `As empresas travadas por procuração são puladas.`)) return;
  const r = await fetch(`/api/agendamento/${modulo}/executar`, { method: 'POST' });
  const d = await r.json();
  alert(JSON.stringify(d, null, 2));
  carregar();
}

carregar();
</script>
</body>
</html>
"""


@agendamento_bp.get('/agendamento')
def pagina_agendamento():
    return render_template_string(PAGINA)
