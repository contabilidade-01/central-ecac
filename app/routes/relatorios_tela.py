"""Tela server-side para navegação e visualização dos PDFs armazenados.

Permite ver quantos PDFs existem, quais empresas estão cobertas, e abrir
qualquer PDF direto no navegador sem precisar de SSH ou acesso ao volume.
"""

from flask import Blueprint, render_template_string

from app.ui import CSS, FIM, lateral

relatorios_tela_bp = Blueprint('relatorios_tela', __name__)


@relatorios_tela_bp.get('/relatorios')
def tela_relatorios():
    return render_template_string(PAGINA)


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatórios / PDFs — Central Pendências e-CAC</title>
<style>""" + CSS + """
.resumo-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:18px; }
.resumo-item { background:var(--superficie); border:1px solid var(--borda); border-radius:10px; padding:14px 16px; }
.resumo-item b { display:block; font-size:22px; font-weight:700; }
.resumo-item span { font-size:11px; color:var(--suave); text-transform:uppercase; }
.empresa-row { cursor:pointer; }
.empresa-row:hover { background:#f1f5f9; }
.pdfs-detalhe { display:none; }
.pdfs-detalhe.aberto { display:table-row; }
.pdfs-detalhe td { padding:8px 12px 16px 40px; }
.pdf-lista { list-style:none; padding:0; margin:0; }
.pdf-lista li { display:flex; align-items:center; gap:10px; padding:4px 0; font-size:13px; }
.pdf-lista a { color:var(--primaria); text-decoration:none; }
.pdf-lista a:hover { text-decoration:underline; }
.pdf-lista .meta { color:var(--suave); font-size:11px; }
</style>
</head>
<body>
""" + lateral('relatorios') + """
<div class="wrap">
  <h1>Relatórios / PDFs</h1>
  <p class="sub">
    Visualize e navegue pelos PDFs de Situação Fiscal armazenados no sistema.
    Clique no nome da empresa para expandir e ver todos os PDFs disponíveis.
  </p>

  <div class="resumo-grid" id="resumo">
    <div class="resumo-item"><b id="r-total">—</b><span>PDFs no sistema</span></div>
    <div class="resumo-item"><b id="r-size">—</b><span>Tamanho total</span></div>
    <div class="resumo-item"><b id="r-com">—</b><span>Empresas com PDF</span></div>
    <div class="resumo-item"><b id="r-sem">—</b><span>Empresas sem PDF</span></div>
  </div>

  <div class="card">
    <h2>Empresas</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Empresa</th>
            <th>CNPJ</th>
            <th>PDFs</th>
            <th>Último</th>
          </tr>
        </thead>
        <tbody id="corpo">
          <tr><td colspan="4">Carregando...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
async function carregarResumo() {
  try {
    const r = await fetch('/api/reports/storage-info');
    const j = await r.json();
    document.getElementById('r-total').textContent = j.total_pdfs;
    document.getElementById('r-size').textContent = j.total_size_mb + ' MB';
    document.getElementById('r-com').textContent = j.empresas_com_pdf;
    document.getElementById('r-sem').textContent = j.empresas_sem_pdf;
  } catch(e) {}
}

async function carregarEmpresas() {
  const corpo = document.getElementById('corpo');
  try {
    // Buscar todas as empresas com seus relatórios
    const r = await fetch('/api/dashboard/companies');
    const empresas = await r.json();

    // Filtrar só as que têm relatório
    const comRelatorio = empresas.filter(e => e.report_id);
    if (!comRelatorio.length) {
      corpo.innerHTML = '<tr><td colspan="4">Nenhum relatório processado ainda.</td></tr>';
      return;
    }

    corpo.innerHTML = comRelatorio.map(e => {
      const cnpj = e.cnpj.replace(/(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})/, '$1.$2.$3/$4-$5');
      const data = e.data_hora ? new Date(e.data_hora).toLocaleString('pt-BR') : '—';
      return `<tr class="empresa-row" onclick="togglePdfs(this, ${e.id}, '${e.razao_social.replace(/'/g, "\\'")}')"
                  data-id="${e.id}">
        <td><b>${e.razao_social}</b></td>
        <td><code>${cnpj}</code></td>
        <td>—</td>
        <td>${data}</td>
      </tr>
      <tr class="pdfs-detalhe" id="det-${e.id}"><td colspan="4"></td></tr>`;
    }).join('');
  } catch(e) {
    corpo.innerHTML = '<tr><td colspan="4">Erro: ' + e.message + '</td></tr>';
  }
}

async function togglePdfs(row, companyId, nome) {
  const detalhe = document.getElementById('det-' + companyId);
  if (detalhe.classList.contains('aberto')) {
    detalhe.classList.remove('aberto');
    return;
  }

  const td = detalhe.querySelector('td');
  td.innerHTML = '<em>Carregando PDFs...</em>';
  detalhe.classList.add('aberto');

  try {
    const r = await fetch('/api/reports/company/' + companyId + '/pdfs');
    const pdfs = await r.json();

    // Atualizar contagem na linha
    row.querySelectorAll('td')[2].textContent = pdfs.length;

    if (!pdfs.length) {
      td.innerHTML = '<em>Nenhum PDF em disco para esta empresa.</em>';
      return;
    }

    td.innerHTML = '<ul class="pdf-lista">' + pdfs.map(p => {
      const data = p.data_hora ? new Date(p.data_hora).toLocaleString('pt-BR') : '—';
      return `<li>
        <a href="/api/reports/view-pdf/${p.report_id}" target="_blank"
           title="Abrir PDF no navegador">📄 ${p.filename}</a>
        <span class="meta">${data} · ${p.tamanho_kb} KB</span>
      </li>`;
    }).join('') + '</ul>';
  } catch(e) {
    td.innerHTML = '<em>Erro ao carregar: ' + e.message + '</em>';
  }
}

carregarResumo();
carregarEmpresas();
</script>
""" + FIM + """
</body>
</html>
"""
