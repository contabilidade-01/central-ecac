# -*- coding: utf-8 -*-
"""Adapta o parser PGDAS do Integra para o Central e-CAC."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "templates" / "escritorio" / "_pgdas_parser_raw.js"
OUT = ROOT / "app" / "templates" / "escritorio" / "pgdas_leitor.js"

text = SRC.read_text(encoding="utf-8")

text = re.sub(
    r"pdfjsLib\.GlobalWorkerOptions\.workerSrc\s*=\s*[^\n]+;\s*",
    "",
    text,
    count=1,
)
text = text.replace('getElementById("pdfFile")', 'getElementById("fileInput")')

NEW_IMPORT = r'''btnImportar.addEventListener("click", async () => {
  try{
    const dados = JSON.parse(btnImportar.dataset.json || "{}");

    delete dados.tipoDocumento;
    delete dados.debug;
    delete dados.resumido;
    delete dados.modoParser;
    delete dados.validacaoReceitas;
    delete dados.folhaDeclaradaNenhuma;

    statusDiv.style.display = "block";
    statusDiv.className = "aviso";
    statusDiv.innerHTML = "Enviando lancamentos...";

    const url = (window.RBT12_CFG && window.RBT12_CFG.urlImportar) || "/escritorio/api/pgdas/importar";

    if (!dados.company_id && dados.cnpj) {
      try {
        const er = await fetch("/escritorio/api/empresa?cnpj=" + encodeURIComponent(String(dados.cnpj).replace(/\D/g,"")), {credentials:"same-origin"});
        const ej = await er.json();
        if (ej && ej.ok && ej.encontrada && ej.company_id) dados.company_id = ej.company_id;
      } catch (e) {}
    }

    const resp = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "Accept": "application/json"},
      body: JSON.stringify(dados)
    });

    let data = null;
    const raw = await resp.text();
    try { data = JSON.parse(raw); } catch(e) { data = null; }

    if (!resp.ok || !data || !data.ok) {
      const msg = (data && (data.msg || data.message)) || raw || ("HTTP " + resp.status);
      throw new Error(msg);
    }

    const moedaFmt = (n) => Number(n||0).toLocaleString("pt-BR",{minimumFractionDigits:2,maximumFractionDigits:2});
    statusDiv.className = "aviso ok";
    statusDiv.innerHTML = "Importado: <b>" + escapeHtml(data.razao || data.cnpj) +
      "</b> · PA " + escapeHtml(data.pa) +
      " · " + data.linhas + " mes(es) · RBT12 R$ " + moedaFmt(data.rbt12) +
      (data.pronto_transmitir ? " · pronto para Transmitir" : "");

    if (typeof mostrarAlerta === "function") {
      mostrarAlerta("success", "Historico importado",
        "RBT12 R$ " + moedaFmt(data.rbt12) + " gravado. Abra Transmitir na competencia " + data.pa + ".");
    }

  }catch(err){
    console.error(err);
    statusDiv.style.display = "block";
    statusDiv.className = "aviso err";
    statusDiv.innerHTML = "Erro ao importar lancamentos: " + escapeHtml(err.message || "erro inesperado");
    if (typeof mostrarAlerta === "function") {
      mostrarAlerta("error", "Falha na importacao", err.message || String(err));
    }
  }
});'''

m = re.search(r"btnImportar\.addEventListener\(\"click\".*?\n\}\);", text, re.S)
if not m:
    raise SystemExit("bloco btnImportar nao encontrado")
text = text[: m.start()] + NEW_IMPORT + text[m.end() :]

text = re.sub(
    r"// =+\s*\n// THEME TOGGLE\s*\n// =+\s*\n.*?themeBtn\.textContent = isLight \? \"🌙\" : \"☀️\";\s*\n\}\);\s*",
    "",
    text,
    flags=re.S,
)

# statusDiv starts hidden in our template; show on use
# Also ensure statusDiv gets className reset when processarArquivo clears it
text = text.replace(
    'statusDiv.innerHTML = "";',
    'statusDiv.innerHTML = ""; statusDiv.style.display = "none"; statusDiv.className = "aviso";',
)

# After successful parse, show status area only when importing
# btnImportar.style.display = "block" — keep; also enable button
text = text.replace(
    'btnImportar.style.display = "block";\n    btnImportar.dataset.json = JSON.stringify(d);',
    'btnImportar.style.display = "block";\n    btnImportar.disabled = false;\n    btnImportar.dataset.json = JSON.stringify(d);',
)
text = text.replace(
    'btnImportar.style.display = "none";\n    btnImportar.dataset.json = "";',
    'btnImportar.style.display = "none";\n    btnImportar.disabled = true;\n    btnImportar.dataset.json = "";',
)

header = """/* Leitor PGDAS-D (PDF + OCR) — adaptado do Integra Contador impext/imp.php v16
 * para Central e-CAC. Parsing no browser; gravacao via /escritorio/api/pgdas/importar.
 */
"""

OUT.write_text(header + text, encoding="utf-8")
print("wrote", OUT, "bytes", OUT.stat().st_size)
print("importar_lancamentos.php?", "importar_lancamentos.php" in OUT.read_text(encoding="utf-8"))
print("urlImportar?", "urlImportar" in OUT.read_text(encoding="utf-8"))
print("themeBtn?", "themeBtn" in OUT.read_text(encoding="utf-8"))
