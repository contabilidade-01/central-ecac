"""Tela server-side de filtros por tipo de pendência/débito.

Permite ao usuário filtrar as empresas que têm determinado tipo de omissão
ou débito, sem depender do frontend React compilado.
"""

from flask import Blueprint, render_template_string, jsonify, request
from sqlalchemy import func

from app.extensions import db
from app.models import (
    Company, RelatorioSitFiscal, PendenciaRelatorio, DebitoRelatorio)
from app.ui import CSS, FIM, lateral

filtros_bp = Blueprint('filtros', __name__)


def _latest_report_ids():
    latest = (
        db.session.query(
            RelatorioSitFiscal.company_id,
            func.max(RelatorioSitFiscal.id).label('max_id'),
        )
        .group_by(RelatorioSitFiscal.company_id)
        .subquery()
    )
    return [r[0] for r in db.session.query(latest.c.max_id).all()]


@filtros_bp.get('/filtros')
def tela_filtros():
    return render_template_string(PAGINA)


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Filtros — Central Pendências e-CAC</title>
<style>""" + CSS + """
.filtro-row { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end; margin-bottom:18px; }
.filtro-row label { font-size:12px; font-weight:600; color:var(--suave); display:block; margin-bottom:4px; }
.filtro-row select, .filtro-row input { min-width:180px; }
.resultado { margin-top:14px; }
.resultado .qtd { font-size:13px; color:var(--suave); margin-bottom:10px; }
td.valor { text-align:right; font-variant-numeric:tabular-nums; }

@media print {
  .lateral, .barra, .filtro-row, .no-print, .app > .conteudo > .barra { display:none !important; }
  .app { display:block !important; }
  .conteudo { overflow:visible !important; }
  .wrap { max-width:100%; padding:0; margin:0; }
  .card { border:none; box-shadow:none; padding:0; }
  body { background:#fff; font-size:11px; }
  table { font-size:11px; }
  th, td { padding:5px 6px; }
  h1 { font-size:16px; margin-bottom:4px; }
  .sub { font-size:11px; margin-bottom:8px; }
  .resultado .qtd { font-size:11px; }
  .print-header { display:block !important; margin-bottom:10px; padding-bottom:8px; border-bottom:1px solid #ccc; }
}
.print-header { display:none; }
</style>
</head>
<body>
""" + lateral('filtros') + """
<div class="wrap">
  <div class="print-header">
    <strong>Central Pendências e-CAC — Nescon Serviços Empresariais</strong><br>
    <small id="print-info"></small>
  </div>
  <h1>Filtros</h1>
  <p class="sub">
    Filtre as empresas por tipo de pendência (omissão de declaração) ou tipo de débito.
    Resultados em tempo real.
  </p>

  <div class="card">
    <div class="filtro-row">
      <div>
        <label>Tipo de Pendência</label>
        <select id="f-pendencia" onchange="filtrar()">
          <option value="">— Todas —</option>
        </select>
      </div>
      <div>
        <label>Tipo de Débito</label>
        <select id="f-debito-tipo" onchange="filtrar()">
          <option value="">— Todos —</option>
        </select>
      </div>
      <div>
        <label>Receita (código ou texto)</label>
        <input id="f-receita" type="text" placeholder="ex: 4406 ou PGDAS" oninput="filtrar()">
      </div>
      <div>
        <button onclick="limpar()">Limpar filtros</button>
        <button class="primario no-print" onclick="window.print()" title="Imprimir lista filtrada">🖨️ Imprimir</button>
      </div>
    </div>
  </div>

  <div class="resultado">
    <div class="qtd" id="qtd"></div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Empresa</th>
            <th>CNPJ</th>
            <th>Situação</th>
            <th>Pendências</th>
            <th>Débitos</th>
            <th class="valor">Valor total débitos</th>
            <th>PDF</th>
          </tr>
        </thead>
        <tbody id="corpo">
          <tr><td colspan="7">Carregando...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
let _timeout = null;

async function carregarFiltros() {
  try {
    const r = await fetch('/api/dashboard/filtros-disponiveis');
    const j = await r.json();
    const selPend = document.getElementById('f-pendencia');
    const selDeb = document.getElementById('f-debito-tipo');
    j.tipos_pendencia.forEach(t => {
      selPend.innerHTML += `<option value="${t}">${t}</option>`;
    });
    j.tipos_debito.forEach(t => {
      selDeb.innerHTML += `<option value="${t}">${t}</option>`;
    });
  } catch(e) {}
}

function filtrar() {
  clearTimeout(_timeout);
  _timeout = setTimeout(_executarFiltro, 300);
}

async function _executarFiltro() {
  const pend = document.getElementById('f-pendencia').value;
  const deb = document.getElementById('f-debito-tipo').value;
  const rec = document.getElementById('f-receita').value.trim();

  let url = '/api/dashboard/companies?';
  if (pend) url += 'pendencia_tipo=' + encodeURIComponent(pend) + '&';
  if (deb) url += 'debito_tipo=' + encodeURIComponent(deb) + '&';
  if (rec) url += 'debito_receita=' + encodeURIComponent(rec) + '&';

  const corpo = document.getElementById('corpo');
  const qtd = document.getElementById('qtd');
  corpo.innerHTML = '<tr><td colspan="7">Buscando...</td></tr>';

  try {
    const r = await fetch(url);
    const empresas = await r.json();
    qtd.textContent = empresas.length + ' empresa(s) encontrada(s)';

    if (!empresas.length) {
      corpo.innerHTML = '<tr><td colspan="7">Nenhuma empresa com esse filtro.</td></tr>';
      return;
    }

    corpo.innerHTML = empresas.map(e => {
      const cnpj = e.cnpj.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, '$1.$2.$3/$4-$5');
      const valor = 'R$ ' + e.valor_total_debitos.toLocaleString('pt-BR', {minimumFractionDigits:2});
      const pdfLink = e.report_id
        ? `<a href="/api/reports/view-pdf/${e.report_id}" target="_blank" title="Ver PDF">📄</a>`
        : '—';
      return `<tr>
        <td>${e.razao_social}</td>
        <td><code>${cnpj}</code></td>
        <td>${e.situacao || '—'}</td>
        <td>${e.total_pendencias}</td>
        <td>${e.total_debitos}</td>
        <td class="valor">${valor}</td>
        <td>${pdfLink}</td>
      </tr>`;
    }).join('');
  } catch(e) {
    corpo.innerHTML = '<tr><td colspan="7">Erro: ' + e.message + '</td></tr>';
  }
}

function limpar() {
  document.getElementById('f-pendencia').value = '';
  document.getElementById('f-debito-tipo').value = '';
  document.getElementById('f-receita').value = '';
  filtrar();
}

// Antes de imprimir, atualiza o cabeçalho com filtros e data
window.onbeforeprint = function() {
  const pend = document.getElementById('f-pendencia').value;
  const deb = document.getElementById('f-debito-tipo').value;
  const rec = document.getElementById('f-receita').value;
  const qtd = document.getElementById('qtd').textContent;
  let filtros = [];
  if (pend) filtros.push('Pendencia: ' + pend);
  if (deb) filtros.push('Debito: ' + deb);
  if (rec) filtros.push('Receita: ' + rec);
  const txt = filtros.length ? filtros.join(' | ') : 'Todas as empresas';
  const agora = new Date().toLocaleString('pt-BR');
  document.getElementById('print-info').textContent = qtd + ' — Filtros: ' + txt + ' — Emitido em ' + agora;
};

carregarFiltros();
filtrar();
</script>
""" + FIM + """
</body>
</html>
"""
