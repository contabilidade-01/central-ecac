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
</style>
</head>
<body>
""" + lateral('filtros') + """
<div class="wrap">
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
        <button class="primario" onclick="imprimir()" title="Imprimir lista filtrada">🖨️ Imprimir</button>
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

function imprimir() {
  const pend = document.getElementById('f-pendencia');
  const deb = document.getElementById('f-debito-tipo');
  const rec = document.getElementById('f-receita');
  const qtd = document.getElementById('qtd').textContent;

  // Montar descrição do filtro ativo
  let filtroDesc = [];
  if (pend.value) filtroDesc.push('Pendência: ' + pend.value);
  if (deb.value) filtroDesc.push('Débito: ' + deb.value);
  if (rec.value) filtroDesc.push('Receita: ' + rec.value);
  const filtroTexto = filtroDesc.length ? filtroDesc.join(' | ') : 'Sem filtros (todas as empresas)';

  // Pegar dados da tabela
  const linhas = document.querySelectorAll('#corpo tr');
  let tabelaHTML = '';
  linhas.forEach(tr => {
    const tds = tr.querySelectorAll('td');
    if (tds.length < 6) return;
    tabelaHTML += '<tr>';
    // Pegar só as 6 primeiras colunas (sem PDF)
    for (let i = 0; i < 6; i++) {
      tabelaHTML += '<td>' + tds[i].textContent.trim() + '</td>';
    }
    tabelaHTML += '</tr>';
  });

  const agora = new Date().toLocaleString('pt-BR');

  const html = `<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Filtros - Central e-CAC</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", Arial, sans-serif; margin: 0; padding: 20px 30px; color: #1a1a1a; font-size: 12px; }
  h1 { font-size: 16px; margin: 0 0 4px; }
  .sub { color: #555; font-size: 11px; margin: 0 0 12px; }
  .filtro { background: #f4f7fa; border: 1px solid #ddd; border-radius: 6px; padding: 8px 14px; margin-bottom: 12px; font-size: 11px; }
  .filtro b { color: #1d4ed8; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; }
  th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; }
  th { background: #f0f4f8; font-weight: 600; font-size: 10px; text-transform: uppercase; }
  td.valor { text-align: right; font-variant-numeric: tabular-nums; }
  tr:nth-child(even) { background: #fafbfc; }
  .rodape { margin-top: 16px; font-size: 10px; color: #888; text-align: center; border-top: 1px solid #eee; padding-top: 8px; }
  @media print {
    body { padding: 10px; }
    .no-print { display: none; }
  }
</style>
</head>
<body>
<h1>Central Pendências e-CAC — Filtros</h1>
<p class="sub">${qtd} • Gerado em ${agora}</p>
<div class="filtro">Filtros aplicados: <b>${filtroTexto}</b></div>
<table>
  <thead>
    <tr>
      <th>Empresa</th>
      <th>CNPJ</th>
      <th>Situação</th>
      <th>Pend.</th>
      <th>Déb.</th>
      <th class="valor">Valor total débitos</th>
    </tr>
  </thead>
  <tbody>${tabelaHTML}</tbody>
</table>
<div class="rodape">Nescon Serviços Empresariais • Central Pendências e-CAC</div>
<script>window.onload = function() { window.print(); }<\/script>
</body>
</html>`;

  const janela = window.open('', '_blank');
  janela.document.write(html);
  janela.document.close();
}

carregarFiltros();
filtrar();
</script>
""" + FIM + """
</body>
</html>
"""
