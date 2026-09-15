{% raw %}
/* =========================================================================
   Leitor de XML de NF-e / NFC-e — Relatório de Notas de Comércio
   (Central e-CAC • Escritório • 17o desvio)

   Mesmo comportamento do leitor do Integra Contador:
   - lê XMLs soltos, pastas e ZIP no navegador (os XMLs não vão para o servidor);
   - ignora duplicados (mesma chave) e notas canceladas (cStat 101 / evento 110111);
   - considera só a competência (mês/ano) e o CNPJ base informados;
   - empresa EMITENTE: todos os itens entram; empresa DESTINATÁRIA: só devoluções
     (5201/5202/5411/5210 e 6xxx viram 1201/1202/1411/1210 e 2xxx);
   - valor do item = (vUnTrib×qTrib | vProd | vItem) − desconto (do item ou rateio do
     desconto total) + vOutro + frete total rateado + ICMS-ST;
   - CST de PIS/COFINS pelo NCM (tabela do servidor; sem regra = CST 1);
   - totais por CFOP×CST, cards gerais, simulador PGDAS-D, PIS/COFINS do Lucro
     Presumido, duplicidades, PDF/Excel e gravação do lançamento.
   ========================================================================= */
(function () {
  'use strict';

  const CFG = window.LEITOR_NFE || {};
  const $ = (s) => document.querySelector(s);
  const brl = (n) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(n || 0);
  const semRS = (n) => brl(n).replace(/^R\$\s?/, '');
  const esc = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const digitos = (t) => String(t || '').replace(/\D/g, '');
  // número vindo do XML (ponto decimal); tolera vírgula
  const num = (s) => { if (!s) return 0; const t = String(s).replace(',', '.').replace(/[^0-9.\-]/g, ''); return parseFloat(t) || 0; };
  const guardar = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  };

  /* ------------------------------------------------------------- CFOPs */
  const RECEITA_PADRAO = ['5102','6102','5403','5405','6403','5104','5655','5656','6655','6656','5101','6101','5124','5108','6108','6124','6404','5404','5117','5106','6106','5103','5105','6107','5402','6103','6104'];
  const OUTRAS_PADRAO = ['5949','6949','5201','5202','5411','5915','5910','6411','6603','6202','5929','6929','5905','6905','6915','5909','6909','5555','5152','5914','6914','5908','6908','5933','6933','5661','6661','5911','6911','5551','6551'];
  const DEVOLUCAO = { '5201': '1201', '5202': '1202', '5411': '1411', '6201': '2201', '6202': '2202', '6411': '2411', '5210': '1210', '6210': '2210' };
  const DEVOL_CONVERTIDOS = new Set(Object.values(DEVOLUCAO));

  // Simulador PGDAS-D (mesmos grupos do modelo)
  const PG_NORMAIS = ['5102','6102','5101','6101','5124','5108','6108','6124','5117','5106','6106','5103','5104','5105','6107','5402','6103','6104'];
  const PG_ST = ['5403','5405','6403','5655','5656','6655','6656','6404','5404'];
  const PG_DEV_NORMAIS = ['1201','1202','2201','2202','1210','2210'];
  const PG_DEV_ST = ['1411','2411'];

  const cfgR = guardar.get('cfg_receita', null), cfgO = guardar.get('cfg_outras', null);
  let receitaCFOPs = new Set(Array.isArray(cfgR) && cfgR.length ? cfgR : RECEITA_PADRAO);
  let outrasCFOPs = new Set(Array.isArray(cfgO) && cfgO.length ? cfgO : OUTRAS_PADRAO);

  /* ------------------------------------------------------------ estado */
  let arquivos = [];               // [{name, content}]
  const cacheCst = new Map();      // ncm -> cst
  let U = null;                    // último relatório

  function limparEstado() {
    U = { totais: null, linhas: [], nome: '', cnpj: '', mes: '', ano: '', companyId: null,
          emit: { total: 0 }, devo: { total: 0 }, rece: { total: 0 }, outr: { total: 0 }, cfops: new Set(), pg: null };
  }
  limparEstado();

  /* ------------------------------------------------------------- UI refs */
  const drop = $('#dropzone'), inputPasta = $('#fileInput'), inputSoltos = $('#fileInputSoltos');
  const progress = $('#progress'), bar = $('#progressBar'), status = $('#statusInfo');
  const mesInput = $('#mes'), anoInput = $('#ano'), baseInput = $('#cnpjbase');
  const btnAnalisar = $('#analisar'), exportBtns = $('#exportBtns'), headerEmp = $('#headerEmpresa');
  const totaisEl = $('#totaisGerais'), cardsEl = $('#cardsContainer');
  const tbody = $('#itemsTable tbody'), resumo = $('#detailsResume'), details = $('#details'), toggleDet = $('#toggleDetails');

  function abrir(el) { el.classList.add('show'); }
  function fechar(el) { el.classList.remove('show'); }
  document.querySelectorAll('.jan-fundo').forEach((f) => f.addEventListener('click', (e) => { if (e.target === f) fechar(f); }));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') document.querySelectorAll('.jan-fundo.show').forEach(fechar); });

  const overlay = $('#loadingOverlay');
  function loading(titulo, passo) { $('#loadingTitle').textContent = titulo; $('#loadingStep').textContent = passo || ''; progresso(0); overlay.classList.add('show'); }
  function progresso(p) { const v = Math.max(0, Math.min(100, Math.round(p || 0))); $('#loadingPercent').textContent = v + '%'; $('#loadingBarInner').style.width = v + '%'; }
  function fimLoading() { overlay.classList.remove('show'); }
  const pausa = () => new Promise((r) => setTimeout(r, 0));

  const tip = $('#copyTip');
  function copiar(texto, el) {
    try { navigator.clipboard.writeText(texto); } catch (e) {}
    const r = el.getBoundingClientRect();
    tip.style.left = (r.left + window.scrollX + 10) + 'px';
    tip.style.top = (r.top + window.scrollY - 12) + 'px';
    tip.style.display = 'block';
    setTimeout(() => { tip.style.display = 'none'; }, 900);
  }
  const avisar = (titulo, msg, tipo) => (window.mostrarAlerta ? window.mostrarAlerta(tipo || 'warning', titulo, msg) : alert(titulo + '\n\n' + msg));

  /* ------------------------------------------------------------ XML utils */
  // remove namespaces e prefixos para permitir querySelector simples
  function normalizarXML(t) {
    return t.replace(/\sxmlns(:\w+)?="[^"]*"/g, '')
            .replace(/<\/?\w+:(\w+)/g, (m) => m.replace(/:\w+/, ''))
            .replace(/(\s)\w+:(\w+)=/g, '$1$2=');
  }
  const parse = (conteudo) => new DOMParser().parseFromString(normalizarXML(conteudo), 'application/xml');
  const txt = (raiz, sel) => { const el = raiz && raiz.querySelector(sel); return el ? (el.textContent || '') : ''; };

  function chaveDaNota(doc) {
    const inf = doc.querySelector('infNFe');
    let ch = digitos(((inf && inf.getAttribute('Id')) || '').replace(/^NFe/, ''));
    if (!ch) ch = digitos(txt(doc, 'protNFe > infProt > chNFe'));
    if (!ch) ch = digitos(txt(doc, 'chNFe'));
    return ch;
  }

  function notaCancelada(doc) {
    const cStat = (txt(doc, 'protNFe > infProt > cStat') || txt(doc, 'infProt > cStat')).trim();
    const xMotivo = (txt(doc, 'protNFe > infProt > xMotivo') || txt(doc, 'infProt > xMotivo')).toLowerCase();
    const descEvento = (txt(doc, 'detEvento > descEvento') || txt(doc, 'descEvento')).toLowerCase();
    const tpEvento = (txt(doc, 'evento > infEvento > tpEvento') || txt(doc, 'retEvento > infEvento > tpEvento') || txt(doc, 'tpEvento')).trim();
    return cStat === '101' || tpEvento === '110111' || descEvento.includes('cancelamento') || xMotivo.includes('cancelamento');
  }

  // Tipo do XML pela raiz. Só NF-e/NFC-e e eventos de NF-e entram no relatório; CT-e,
  // inutilizações e outros documentos são contados e informados, sem somar receita.
  function tipoXML(conteudo) {
    const c = String(conteudo || '').slice(0, 3000);
    if (/<(\w+:)?(cteProc|CTe|cteOSProc|CTeOS)[\s>]/.test(c)) return 'cte';
    if (/<(\w+:)?(procInutNFe|inutNFe|retInutNFe)[\s>]/.test(c)) return 'inut';
    if (/<(\w+:)?(procEventoNFe|evento|retEnvEvento)[\s>]/.test(c)) return 'evento';
    if (/<(\w+:)?(nfeProc|NFe|infNFe)[\s>]/.test(c)) return 'nfe';
    return 'outro';
  }

  // Um arquivo por chave. O XML "principal" (infNFe Id="NFe...") vence o de evento;
  // chave só com eventos é descartada (não tem itens).
  function removerDuplicados(lista) {
    const mapa = new Map();
    for (const f of lista) {
      const c = normalizarXML(f.content || '');
      const principal = c.match(/<infNFe\b[^>]*\bId=["']NFe(\d{44})["']/i);
      const evento = c.match(/<chNFe>\s*(\d{44})\s*<\/chNFe>/i);
      const chave = principal ? principal[1] : (evento ? evento[1] : '');
      if (!chave) continue;
      const atual = mapa.get(chave);
      const item = { file: f, principal: !!principal };
      if (!atual || (!atual.principal && item.principal)) mapa.set(chave, item);
    }
    const unicos = [...mapa.values()].filter((x) => x.principal).map((x) => x.file);
    return { unicos, ignorados: lista.length - unicos.length };
  }

  /* ------------------------------------------------------------- upload */
  drop.addEventListener('click', (e) => { if (e.target.id === 'lnkArquivos') return; inputPasta.click(); });
  drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputPasta.click(); } });
  $('#lnkArquivos').addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); inputSoltos.click(); });
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('dragover'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', async (e) => { e.preventDefault(); drop.classList.remove('dragover'); await carregar(e.dataTransfer.files); });
  inputPasta.addEventListener('change', async (e) => { await carregar(e.target.files); inputPasta.value = ''; });
  inputSoltos.addEventListener('change', async (e) => { await carregar(e.target.files); inputSoltos.value = ''; });

  async function carregar(files) {
    arquivos = [];
    btnAnalisar.classList.remove('btn-pulse');
    if (!files || !files.length) return;
    progress.style.display = 'block'; bar.style.width = '0%';
    loading('Carregando XMLs...', 'Lendo arquivos XML/ZIP...');
    let i = 0, erros = 0;
    for (const f of files) {
      i++;
      const pct = (i / files.length) * 100; bar.style.width = pct + '%'; progresso(pct);
      const nome = (f.name || '').toLowerCase();
      try {
        if (nome.endsWith('.zip')) {
          const zip = await JSZip.loadAsync(f);
          for (const n of Object.keys(zip.files)) {
            if (n.toLowerCase().endsWith('.xml') && !zip.files[n].dir) {
              const conteudo = await zip.files[n].async('text');
              arquivos.push({ name: n.split('/').pop(), content: conteudo, tipo: tipoXML(conteudo) });
            }
          }
        } else if (nome.endsWith('.xml')) {
          const conteudo = await f.text();
          arquivos.push({ name: f.name, content: conteudo, tipo: tipoXML(conteudo) });
        }
      } catch (err) { erros++; console.error('Falha ao ler', f.name, err); }
      if (i % 50 === 0) await pausa();
    }
    progress.style.display = 'none';
    fimLoading();
    if (arquivos.length) {
      const cont = arquivos.reduce((a, f) => { a[f.tipo] = (a[f.tipo] || 0) + 1; return a; }, {});
      const partes = [['nfe', 'NF-e/NFC-e'], ['evento', 'evento(s)'], ['cte', 'CT-e'], ['inut', 'inutilização(ões)'], ['outro', 'outro(s)']]
        .filter(([k]) => cont[k]).map(([k, rot]) => `${cont[k]} ${rot}`);
      status.textContent = `XMLs carregados: ${arquivos.length} (${partes.join(', ')})` + (erros ? ` • ${erros} arquivo(s) não puderam ser lidos` : '');
      if (!cont.nfe) avisar('Nenhuma NF-e nos arquivos', 'Os arquivos carregados não têm XML de NF-e/NFC-e (ex.: só CT-e). Carregue os XMLs de venda.');
      btnAnalisar.classList.add('btn-pulse');
    } else {
      status.textContent = 'Nenhum XML válido encontrado.';
    }
  }

  /* ------------------------------------------------------------ CST por NCM */
  async function carregarCsts(ncms) {
    const faltam = [...new Set(ncms.map(digitos).filter((n) => n && !cacheCst.has(n)))];
    for (let i = 0; i < faltam.length; i += 200) {
      const lote = faltam.slice(i, i + 200);
      try {
        const r = await fetch(CFG.urlCst + '?ncms=' + encodeURIComponent(lote.join(',')), { credentials: 'same-origin' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const j = await r.json();
        lote.forEach((n) => cacheCst.set(n, parseInt((j.cst || {})[n], 10) || 1));
      } catch (e) {
        console.error('Falha ao consultar CST', e);
        lote.forEach((n) => cacheCst.set(n, 1));   // mesmo fallback do modelo
        U.falhaCst = true;
      }
    }
  }
  const cstDe = (ncm) => (ncm ? (cacheCst.get(digitos(ncm)) || 1) : 1);

  /* ------------------------------------------------------------- análise */
  btnAnalisar.addEventListener('click', gerarRelatorio);

  async function gerarRelatorio() {
    if (!arquivos.length) { avisar('Nenhum XML', 'Carregue os XMLs (pasta, arquivos ou ZIP) antes de gerar o relatório.'); return; }
    const mes = String(mesInput.value).trim().padStart(2, '0');
    const ano = String(anoInput.value).trim();
    const base = digitos(baseInput.value);
    if (!mesInput.value || +mes < 1 || +mes > 12 || !/^\d{4}$/.test(ano) || base.length !== 8) {
      avisar('Dados incompletos', 'Preencha o mês (1 a 12), o ano (4 dígitos) e o CNPJ base (8 dígitos).'); return;
    }
    btnAnalisar.classList.remove('btn-pulse');
    loading('Gerando relatório...', 'Identificando notas canceladas e duplicadas...');
    await pausa();
    U.falhaCst = false;

    const totalCarregados = arquivos.length;
    const porTipo = { nfe: [], evento: [], inut: [], cte: [], outro: [] };
    arquivos.forEach((f) => porTipo[f.tipo || tipoXML(f.content)].push(f));
    const docsNfe = porTipo.nfe.concat(porTipo.evento);

    // 0) canceladas — antes de remover duplicados, para enxergar os XMLs de evento
    const canceladas = new Set();
    for (const { content } of porTipo.evento.concat(porTipo.nfe)) {
      const d = parse(content);
      if (notaCancelada(d)) { const ch = chaveDaNota(d); if (ch) canceladas.add(ch); }
    }
    progresso(10);

    // 1) duplicados
    // (a lista carregada NÃO é substituída: gerar de novo para outro mês precisa dos eventos)
    const { unicos } = removerDuplicados(docsNfe);
    const duplicados = porTipo.nfe.length - unicos.length;

    // 2) leitura das notas da competência/empresa
    $('#loadingStep').textContent = 'Lendo notas e itens...';
    const notas = [];
    const chavesLidas = new Set();
    let n = 0;
    for (const { name, content } of unicos) {
      n++;
      if (n % 40 === 0) { progresso(10 + (n / unicos.length) * 50); await pausa(); }
      const doc = parse(content);
      const chave = chaveDaNota(doc);
      if (!chave || chave.length !== 44 || chavesLidas.has(chave)) continue;
      chavesLidas.add(chave);
      if (canceladas.has(chave) || notaCancelada(doc)) continue;

      const m = txt(doc, 'dhEmi, dEmi').match(/^(\d{4})-(\d{2})-(\d{2})/);
      if (!m || m[1] !== ano || m[2] !== mes) continue;

      const emit = doc.querySelector('emit');
      if (!emit) continue;
      const dest = doc.querySelector('dest');
      const cnpjEmit = txt(emit, 'CNPJ'), cnpjDest = dest ? txt(dest, 'CNPJ') : '';
      const ehEmit = cnpjEmit.slice(0, 8) === base, ehDest = cnpjDest.slice(0, 8) === base;
      if (!ehEmit && !ehDest) continue;

      notas.push({
        name, chave, data: m[0], ehEmit, ehDest,
        nomeEmp: ehEmit ? txt(emit, 'xNome') : (dest ? txt(dest, 'xNome') : ''),
        cnpjEmp: ehEmit ? cnpjEmit : cnpjDest,
        destId: digitos(dest ? (txt(dest, 'CNPJ') || txt(dest, 'CPF')) : ''),
        mod: txt(doc, 'ide > mod') || '-',
        nNF: txt(doc, 'ide > nNF') || '-',
        vDescTotal: num(txt(doc, 'ICMSTot > vDesc, total > vDesc')),
        vFreteTotal: num(txt(doc, 'ICMSTot > vFrete, total > vFrete')),
        itens: [...doc.querySelectorAll('det')].map((d) => ({
          cfop: txt(d, 'CFOP') || '0000', ncm: txt(d, 'NCM'),
          vUnTrib: num(txt(d, 'vUnTrib')), qTrib: num(txt(d, 'qTrib')),
          vProd: num(txt(d, 'vProd')), vItem: num(txt(d, 'vItem')),
          vDesc: num(txt(d, 'vDesc')), vICMSST: num(txt(d, 'vICMSST')), vOutro: num(txt(d, 'vOutro'))
        }))
      });
    }

    // 3) CST de todos os NCMs de uma vez
    $('#loadingStep').textContent = 'Consultando CST de PIS/COFINS por NCM...';
    progresso(65);
    await carregarCsts(notas.flatMap((nt) => nt.itens.map((it) => it.ncm)));
    progresso(80);

    // 4) cálculo
    const totais = {}, linhas = [];
    let nomeExib = '', cnpjExib = '', validos = 0;
    const baseDe = (it) => (it.vUnTrib && it.qTrib) ? it.vUnTrib * it.qTrib : (it.vProd || it.vItem || 0);
    // Nome e CNPJ do cabeçalho: de preferência os da nota em que a empresa é emitente
    // (no destinatário o cliente/fornecedor às vezes grava a razão social abreviada).
    const notaEmit = notas.find((nt) => nt.ehEmit && nt.nomeEmp);
    for (const nt of notas) {
      if (!notaEmit && nt.nomeEmp) nomeExib = nt.nomeEmp;
      if (!notaEmit && nt.cnpjEmp) cnpjExib = nt.cnpjEmp;

      let somaBase = 0, temDescItem = false;
      nt.itens.forEach((it) => { if (it.vDesc > 0) temDescItem = true; somaBase += Math.max(0, baseDe(it)); });
      const descFora = temDescItem ? 0 : nt.vDescTotal;
      const coefFrete = (nt.vFreteTotal > 0 && somaBase > 0) ? nt.vFreteTotal / somaBase : 0;

      let houve = false;
      for (const it of nt.itens) {
        const b = baseDe(it);
        const desconto = it.vDesc > 0 ? it.vDesc : ((descFora > 0 && somaBase > 0) ? descFora * (b / somaBase) : 0);
        let v = Math.max(0, b - desconto);
        if (it.vOutro > 0) v += it.vOutro;
        if (coefFrete > 0) v += b * coefFrete;
        if (it.vICMSST > 0) v += it.vICMSST;
        if (v <= 0) continue;

        let cfop = it.cfop;
        if (nt.ehEmit) { /* emitidas sempre entram */ }
        else if (nt.ehDest && DEVOLUCAO[cfop]) { cfop = DEVOLUCAO[cfop]; }
        else continue;

        const cst = cstDe(it.ncm);
        const t = totais[cfop] || (totais[cfop] = { total: 0, cst1: 0, cst4: 0, cst5: 0, cst6: 0 });
        if (cst === 4) t.cst4 += v; else if (cst === 5) t.cst5 += v; else if (cst === 6) t.cst6 += v; else t.cst1 += v;
        t.total = t.cst1 + t.cst4 + t.cst5 + t.cst6;

        linhas.push({ data: nt.data, mod: nt.mod, nNF: nt.nNF, cfop, ncm: it.ncm, cst, val: v, desc: desconto,
                      arquivo: nt.name, destCNPJ: nt.destId, emitFlag: nt.ehEmit, chave: nt.chave });
        houve = true;
      }
      if (houve) validos++;
    }

    if (notaEmit) { nomeExib = notaEmit.nomeEmp; cnpjExib = notaEmit.cnpjEmp; }
    U.totais = totais; U.linhas = linhas; U.nome = nomeExib; U.cnpj = cnpjExib; U.mes = mes; U.ano = ano;
    U.cfops = new Set(Object.keys(totais)); U.pg = null;

    const fora = [];
    if (porTipo.cte.length) fora.push(`${porTipo.cte.length} CT-e`);
    if (porTipo.inut.length) fora.push(`${porTipo.inut.length} inutilização(ões)`);
    if (porTipo.outro.length) fora.push(`${porTipo.outro.length} outro(s)`);
    status.textContent = `XMLs carregados: ${totalCarregados} • NF-e/NFC-e únicas: ${unicos.length} • duplicados ignorados: ${Math.max(0, duplicados)} • cancelados: ${canceladas.size} • válidos: ${validos}` +
      (fora.length ? ` • fora do relatório: ${fora.join(', ')}` : '');
    U.cte = resumirCte(porTipo.cte, base, ano, mes);
    await mostrarEmpresa();
    renderTotais(); renderCfops(); renderLinhas();
    exportBtns.style.display = 'flex';
    progresso(100);
    setTimeout(fimLoading, 200);
    if (U.falhaCst) {
      avisar('CST não consultado', 'Não foi possível consultar a tabela NCM × CST no servidor. Todos os itens foram considerados CST 1 (tributável). Gere o relatório de novo.', 'error');
    } else if (!linhas.length) {
      avisar('Nenhuma nota na competência', `Nenhum item de ${mes}/${ano} com o CNPJ base ${base} foi encontrado nos XMLs carregados.`);
    }
  }

  // CT-e não é receita de venda. Só informamos os de frete em que a empresa é a tomadora
  // (paga o frete) na competência — útil para conferência de custos com frete.
  function resumirCte(lista, base, ano, mes) {
    const r = { qtd: 0, valor: 0, outros: 0, cancelados: 0 };
    for (const { content } of lista) {
      const doc = parse(content);
      const m = txt(doc, 'ide > dhEmi').match(/^(\d{4})-(\d{2})/);
      if (!m || m[1] !== ano || m[2] !== mes) continue;
      if (txt(doc, 'protCTe > infProt > cStat').trim() === '101') { r.cancelados++; continue; }
      const toma = txt(doc, 'toma3 > toma, toma03 > toma').trim();
      const papel = { '0': 'rem', '1': 'exped', '2': 'receb', '3': 'dest' }[toma];
      let cnpjToma = '';
      if (papel) { const el = doc.querySelector(papel); cnpjToma = el ? (txt(el, 'CNPJ') || txt(el, 'CPF')) : ''; }
      else { const t4 = doc.querySelector('toma4'); cnpjToma = t4 ? (txt(t4, 'CNPJ') || txt(t4, 'CPF')) : ''; }
      if (digitos(cnpjToma).slice(0, 8) === base) { r.qtd++; r.valor += num(txt(doc, 'vPrest > vTPrest')); }
      else r.outros++;
    }
    return r;
  }

  async function mostrarEmpresa() {
    headerEmp.style.display = 'block';
    let extra = '';
    U.companyId = null;
    if (digitos(U.cnpj).length === 14) {
      try {
        const r = await fetch(CFG.urlEmpresa + '?cnpj=' + digitos(U.cnpj), { credentials: 'same-origin' });
        const j = await r.json();
        if (j.encontrada) U.companyId = j.company_id;
        else if (j.ok) extra = 'CNPJ não cadastrado no Central — o lançamento pode ser gravado, mas sem vínculo com a empresa.';
        else extra = j.msg || '';
      } catch (e) { /* sem rede: segue sem vínculo */ }
    }
    const cnpjFmt = digitos(U.cnpj).replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5') || '-';
    headerEmp.innerHTML = `<h3>${esc(U.nome || '-')}</h3><p>CNPJ ${esc(cnpjFmt)} • ${esc(U.mes)}/${esc(U.ano)}</p>` +
      (extra ? `<div class="aviso-emp">⚠ ${esc(extra)}</div>` : '') +
      (U.cte && (U.cte.qtd || U.cte.outros) ? `<div class="aviso-emp">🚚 CT-e na competência: ${U.cte.qtd} de frete tomado pela empresa (${brl(U.cte.valor)})` +
        (U.cte.outros ? ` • ${U.cte.outros} de outros tomadores` : '') + ' — não entram na receita.</div>' : '');
  }

  /* ------------------------------------------------------------- render */
  function somar(filtro) {
    const a = { total: 0, cst1: 0, cst4: 0, cst5: 0, cst6: 0 };
    for (const [cf, t] of Object.entries(U.totais || {})) {
      if (!filtro(cf)) continue;
      a.cst1 += t.cst1; a.cst4 += t.cst4; a.cst5 += t.cst5; a.cst6 += t.cst6;
    }
    a.total = a.cst1 + a.cst4 + a.cst5 + a.cst6;
    return a;
  }
  const blocoCst = (b) => `<p>CST 1 - ${brl(b.cst1)}</p><p>CST 4 - ${brl(b.cst4)}</p><p>CST 5 - ${brl(b.cst5)}</p><p>CST 6 - ${brl(b.cst6)}</p>` +
    `<p class="monof">MONOFÁSICOS: ${brl(b.cst4 + b.cst5)}</p><p class="nmonof">NÃO MONOFÁSICOS: ${brl(b.cst1 + b.cst6)}</p>`;

  function renderTotais() {
    U.emit = somar((c) => !DEVOL_CONVERTIDOS.has(c));
    U.devo = somar((c) => DEVOL_CONVERTIDOS.has(c));
    U.rece = somar((c) => receitaCFOPs.has(c));
    U.outr = somar((c) => outrasCFOPs.has(c));
    const card = (titulo, b, cls) => `<div class="ncard sum ${cls}"><h3>${titulo}</h3><p class="total">Total: ${brl(b.total)}</p>${blocoCst(b)}</div>`;
    totaisEl.innerHTML = card('TOTAL NOTAS EMITIDAS', U.emit, 'emit') + card('TOTAL DEVOLUÇÕES', U.devo, 'devo') +
                         card('TOTAL RECEITA', U.rece, 'rece') + card('TOTAL OUTRAS SAÍDAS', U.outr, 'outr');
  }

  function renderCfops() {
    const chaves = Object.keys(U.totais || {}).sort();
    if (!chaves.length) { cardsEl.innerHTML = '<p class="vazio">Nenhum XML válido.</p>'; return; }
    cardsEl.innerHTML = chaves.map((cf) => {
      const t = U.totais[cf];
      return `<div class="ncard ${DEVOL_CONVERTIDOS.has(cf) ? 'devol' : ''}"><h3>CFOP ${esc(cf)}</h3><p class="total">Total: ${brl(t.total)}</p>${blocoCst(t)}</div>`;
    }).join('');
  }

  const ordenarLinhas = (l) => l.slice().sort((a, b) => a.data.localeCompare(b.data) || a.cfop.localeCompare(b.cfop) ||
    String(a.nNF).localeCompare(String(b.nNF), undefined, { numeric: true }));

  function renderLinhas() {
    let soma = 0;
    tbody.innerHTML = ordenarLinhas(U.linhas).map((r) => {
      soma += r.val;
      return `<tr><td>${esc(r.data)}</td><td>${esc(r.mod)}</td><td>${esc(r.nNF)}</td><td>${esc(r.cfop)}</td><td>${esc(r.ncm)}</td><td>${r.cst}</td>` +
             `<td class="right">${brl(r.val)}</td><td class="right">${brl(r.desc)}</td><td>${esc(r.arquivo)}</td></tr>`;
    }).join('');
    resumo.textContent = `Itens: ${U.linhas.length} • Soma: ${brl(soma)}`;
  }

  toggleDet.addEventListener('click', () => {
    details.classList.toggle('aberto');
    toggleDet.textContent = details.classList.contains('aberto') ? 'Ocultar' : 'Ver detalhes';
  });

  const exigirRelatorio = () => {
    if (!U.totais) { avisar('Gere o relatório primeiro', 'Carregue os XMLs e clique em "Gerar Relatório".'); return false; }
    return true;
  };

  /* ------------------------------------------------------ PDF / Excel */
  // Os blocos são redesenhados em fundo branco só para o PDF.
  function cardParaPdf(b, titulo, cor) {
    const p = (t, extra) => `<p style="margin:3px 0;font-size:12.5px;${extra || ''}">${t}</p>`;
    return `<div style="border:1px solid #e5e7eb;border-left:8px solid ${cor};border-radius:10px;padding:12px 14px;background:#fff">` +
      `<h3 style="margin:0 0 6px;font-size:14px;color:#111827">${titulo}</h3>` +
      p('Total: ' + brl(b.total), `font-weight:800;color:${cor}`) +
      p('CST 1 - ' + brl(b.cst1)) + p('CST 4 - ' + brl(b.cst4)) + p('CST 5 - ' + brl(b.cst5)) + p('CST 6 - ' + brl(b.cst6)) +
      p('MONOFÁSICOS: ' + brl(b.cst4 + b.cst5), 'font-weight:700;color:#0f9f45;border-top:1px dashed #ccc;padding-top:6px;margin-top:6px') +
      p('NÃO MONOFÁSICOS: ' + brl(b.cst1 + b.cst6), 'font-weight:700;color:#dc2626') + '</div>';
  }

  async function htmlParaPdf(html, arquivo) {
    if (!window.html2canvas || !window.jspdf) { avisar('PDF indisponível', 'As bibliotecas de PDF não carregaram (sem internet?).', 'error'); return; }
    const alvo = document.createElement('div');
    alvo.style.cssText = 'position:fixed;left:-10000px;top:0;width:900px;padding:24px;background:#fff;color:#111827;font-family:Segoe UI,Arial,sans-serif';
    alvo.innerHTML = html;
    document.body.appendChild(alvo);
    try {
      const canvas = await html2canvas(alvo, { scale: 2, backgroundColor: '#ffffff' });
      const pdf = new window.jspdf.jsPDF({ unit: 'pt', format: 'a4' });
      const w = pdf.internal.pageSize.getWidth(), ph = pdf.internal.pageSize.getHeight();
      const escala = w / canvas.width, alturaPagina = Math.floor(ph / escala);
      const pedaco = document.createElement('canvas'), ctx = pedaco.getContext('2d');
      pedaco.width = canvas.width;
      for (let y = 0, pagina = 0; y < canvas.height; y += alturaPagina, pagina++) {
        const h = Math.min(alturaPagina, canvas.height - y);
        pedaco.height = h;
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, pedaco.width, h);
        ctx.drawImage(canvas, 0, y, canvas.width, h, 0, 0, canvas.width, h);
        if (pagina > 0) pdf.addPage();
        pdf.addImage(pedaco.toDataURL('image/png'), 'PNG', 0, 0, w, h * escala);
      }
      pdf.save(arquivo);
    } finally { document.body.removeChild(alvo); }
  }

  const cnpjFormatado = () => digitos(U.cnpj).replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5') || '-';
  const cabecalhoPdf = (titulo) => `<h2 style="margin:0 0 6px;font-size:18px">${titulo}</h2>` +
    `<div style="margin:0 0 14px;font-size:13px"><b>${esc(U.nome || '-')}</b><br>CNPJ ${esc(cnpjFormatado())} • Competência ${esc(U.mes)}/${esc(U.ano)}</div>`;
  const sufixo = () => `${digitos(U.cnpj)}_${U.ano}${U.mes}`;

  $('#btnPDF').addEventListener('click', async () => {
    if (!exigirRelatorio()) return;
    const grade = '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px">' +
      cardParaPdf(U.emit, 'TOTAL NOTAS EMITIDAS', '#00a383') + cardParaPdf(U.devo, 'TOTAL DEVOLUÇÕES', '#dc2626') +
      cardParaPdf(U.rece, 'TOTAL RECEITA', '#0984e3') + cardParaPdf(U.outr, 'TOTAL OUTRAS SAÍDAS', '#b7791f') + '</div>';
    await htmlParaPdf(cabecalhoPdf('RELATÓRIO XML MONOFÁSICOS E POR CST') + '<h3 style="font-size:15px;margin:0 0 8px">Totais Gerais</h3>' + grade,
      `Relatorio_Cards_${sufixo()}.pdf`);
  });

  function baixarExcel(dados, aba, arquivo) {
    if (!window.XLSX) { avisar('Excel indisponível', 'A biblioteca de planilhas não carregou (sem internet?).', 'error'); return; }
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(dados), aba);
    XLSX.writeFile(wb, arquivo);
  }

  $('#btnExcel').addEventListener('click', () => {
    if (!exigirRelatorio()) return;
    const dados = [['Data', 'Modelo', 'Nº NF', 'CFOP', 'NCM', 'CST', 'Valor líquido', 'Desconto', 'Arquivo']];
    ordenarLinhas(U.linhas).forEach((r) => dados.push([r.data, r.mod, r.nNF, r.cfop, r.ncm, r.cst, +r.val.toFixed(2), +r.desc.toFixed(2), r.arquivo]));
    baixarExcel(dados, 'Relatório', `Relatorio_Monofasicos_${sufixo()}.xlsx`);
  });

  /* --------------------------------------------------- Configurar CFOPs */
  const modalCfop = $('#modalCfop'), gridR = $('#gridReceita'), gridO = $('#gridOutras');
  $('#btnConfig').addEventListener('click', () => {
    // CFOPs do relatório + os já configurados (sem as devoluções convertidas). "•" = apareceu nos XMLs.
    const todos = [...new Set([...U.cfops, ...receitaCFOPs, ...outrasCFOPs])].filter((c) => !DEVOL_CONVERTIDOS.has(c)).sort();
    const marca = (conj) => todos.map((cf) => `<label><input type="checkbox" value="${esc(cf)}" ${conj.has(cf) ? 'checked' : ''}> ${esc(cf)}${U.cfops.has(cf) ? ' •' : ''}</label>`).join('') || '<small>Nenhum CFOP.</small>';
    gridR.innerHTML = marca(receitaCFOPs);
    gridO.innerHTML = marca(outrasCFOPs);
    atualizarBadges();
    abrir(modalCfop);
  });
  function exclusivo(origem, outro) {
    origem.addEventListener('change', (e) => {
      if (!e.target.matches('input[type="checkbox"]')) return;
      if (e.target.checked) { const par = outro.querySelector(`input[value="${e.target.value}"]`); if (par) par.checked = false; }
      atualizarBadges();
    });
  }
  exclusivo(gridR, gridO); exclusivo(gridO, gridR);
  const marcados = (grid) => [...grid.querySelectorAll('input:checked')].map((i) => i.value).sort();
  function atualizarBadges() {
    const r = marcados(gridR), o = marcados(gridO);
    $('#badgeReceita').textContent = r.length ? r.join(', ') : '—';
    $('#badgeOutras').textContent = o.length ? o.join(', ') : '—';
  }
  $('#modalCancel').addEventListener('click', () => fechar(modalCfop));
  $('#modalSave').addEventListener('click', () => {
    const r = marcados(gridR), o = marcados(gridO);
    receitaCFOPs = new Set(r); outrasCFOPs = new Set(o);
    guardar.set('cfg_receita', r); guardar.set('cfg_outras', o);
    if (U.totais) renderTotais();
    fechar(modalCfop);
  });

  /* --------------------------------------------------------- PGDAS-D */
  function somaPor(cfops, tipo) {
    let s = 0;
    cfops.forEach((c) => {
      const t = (U.totais || {})[c]; if (!t) return;
      if (tipo === 'mono') s += t.cst4 + t.cst5; else if (tipo === 'nao') s += t.cst1 + t.cst6; else s += t.total;
    });
    return s;
  }
  function calcularPgdas() {
    const vals = [
      somaPor(PG_NORMAIS, 'nao') - somaPor(PG_DEV_NORMAIS, 'nao'),   // sem ST e sem monofásico
      somaPor(PG_ST, 'mono') - somaPor(PG_DEV_ST, 'mono'),           // ST + monofásico
      somaPor(PG_NORMAIS, 'mono') - somaPor(PG_DEV_NORMAIS, 'mono'), // monofásico sem ST
      somaPor(PG_ST, 'nao') - somaPor(PG_DEV_ST, 'nao')              // ST sem monofásico
    ].map((v) => Math.round(v * 100) / 100);
    U.pg = { vals, total: Math.round(vals.reduce((a, b) => a + b, 0) * 100) / 100 };
    return U.pg;
  }
  function preencherPgdas() {
    const pg = calcularPgdas();
    ['#pg1', '#pg2', '#pg3', '#pg4'].forEach((id, i) => { $(id).value = semRS(pg.vals[i]); });
    $('#pgTotal').textContent = brl(pg.total);
    $('#pgRece span').textContent = brl(U.rece.total);
    $('#pgDevo span').textContent = brl(U.devo.total);
    $('#pgOutr span').textContent = brl(U.outr.total);
    $('#pgEmit span').textContent = brl(U.emit.total);
  }
  const pgBack = $('#pgdasBack');
  $('#btnPGDAS').addEventListener('click', () => { if (!exigirRelatorio()) return; preencherPgdas(); abrir(pgBack); });
  $('#pgClose').addEventListener('click', () => fechar(pgBack));
  ['#pg1', '#pg2', '#pg3', '#pg4'].forEach((id) => $(id).addEventListener('click', (e) => copiar(e.target.value, e.target)));
  $('#pgTotal').addEventListener('click', (e) => copiar(semRS(U.pg ? U.pg.total : 0), e.target));
  [['#pgRece', 'rece'], ['#pgDevo', 'devo'], ['#pgOutr', 'outr'], ['#pgEmit', 'emit']].forEach(([id, k]) =>
    $(id).addEventListener('click', (e) => copiar(semRS(U[k].total), e.currentTarget)));
  $('#pgCopyAll').addEventListener('click', (e) => {
    const pg = U.pg || calcularPgdas();
    const partes = pg.vals.map((v) => brl(v)).concat(['Total PGDAS: ' + brl(pg.total), 'Emitidas: ' + brl(U.emit.total),
      'Devoluções: ' + brl(U.devo.total), 'Receita: ' + brl(U.rece.total), 'Outras: ' + brl(U.outr.total)]);
    copiar(partes.join(' | '), e.currentTarget);
  });
  $('#pgPdf').addEventListener('click', async () => {
    const pg = U.pg || calcularPgdas();
    const td = 'padding:6px 8px;border:1px solid #ddd';
    const linha = (rot, v, tags) => `<tr><td style="${td}">${rot}</td><td style="${td};text-align:right;font-weight:700">${brl(v)}</td><td style="${td}">${tags}</td></tr>`;
    const html = cabecalhoPdf('Simulador PGDAS-D') +
      '<div style="background:#fffbe6;border:1px solid #f2c94c;color:#7a6200;font-weight:700;text-align:center;padding:8px;border-radius:6px;font-size:12px;margin-bottom:10px">ESTE SISTEMA NÃO APURA ISENÇÃO OU REDUÇÃO. VERIFIQUE AS REGRAS DO SEU ESTADO NESTES CASOS.</div>' +
      '<table style="border-collapse:collapse;width:100%;font-size:12.5px">' +
      `<tr style="background:#e9f6e3"><th style="${td};text-align:left">Revenda de mercadorias (exceto exterior)</th><th style="${td}">Receita</th><th style="${td};text-align:left">Marcações no PGDAS-D</th></tr>` +
      linha('Sem ST/monofásica/antecipação', pg.vals[0], '—') +
      linha('Com ST/monofásica — ST + monofásico', pg.vals[1], 'COFINS e PIS: monofásico • ICMS: substituição tributária') +
      linha('Com ST/monofásica — monofásico', pg.vals[2], 'COFINS e PIS: monofásico') +
      linha('Com ST/monofásica — ST', pg.vals[3], 'ICMS: substituição tributária') +
      `<tr style="background:#f0f7ff;font-weight:800"><td style="${td}">Total da Receita a Declarar</td><td style="${td};text-align:right">${brl(pg.total)}</td><td style="${td}"></td></tr></table>` +
      `<div style="margin-top:12px;font-size:12.5px;line-height:1.7">TOTAL RECEITA: <b>${brl(U.rece.total)}</b><br>TOTAL DEVOLUÇÕES: <b>${brl(U.devo.total)}</b><br>TOTAL OUTRAS SAÍDAS: <b>${brl(U.outr.total)}</b><br>TOTAL NOTAS EMITIDAS: <b>${brl(U.emit.total)}</b></div>`;
    await htmlParaPdf(html, `Simulador_PGDAS_${sufixo()}.pdf`);
  });

  /* ------------------------------------------------ PIS/COFINS presumido */
  const pisBack = $('#pisBack'), pisBody = $('#pisTable tbody');
  $('#btnPisCofins').addEventListener('click', () => {
    if (!U.linhas.length) { avisar('Gere o relatório primeiro', 'Não há itens lidos para montar a tabela de PIS/COFINS.'); return; }
    const grupos = new Map();
    for (const r of U.linhas) {
      const tipo = receitaCFOPs.has(r.cfop) ? 'rec' : (DEVOL_CONVERTIDOS.has(r.cfop) ? 'devo' : null);
      if (!tipo) continue;
      const cst = String(r.cst).padStart(2, '0');
      const chave = `${tipo}|${r.cfop}|${r.mod}|${cst}`;
      const g = grupos.get(chave) || { tipo, cfop: r.cfop, modelo: String(r.mod), cst, receita: 0, pis: 0, cof: 0 };
      g.receita += r.val;
      if (cst === '01') { g.pis += r.val * 0.0065; g.cof += r.val * 0.03; }
      grupos.set(chave, g);
    }
    const linhas = [...grupos.values()].sort((a, b) => a.cfop.localeCompare(b.cfop) || a.modelo.localeCompare(b.modelo) ||
      a.cst.localeCompare(b.cst) || ((a.tipo === 'devo') - (b.tipo === 'devo')));
    let tRec = 0, tPis = 0, tCof = 0;
    pisBody.innerHTML = linhas.map((g) => {
      const s = g.tipo === 'devo' ? -1 : 1;
      tRec += s * g.receita; tPis += s * g.pis; tCof += s * g.cof;
      return `<tr class="${g.tipo === 'devo' ? 'linha-devo' : ''}"><td>${esc(g.cfop)}</td><td>${esc(g.modelo)}</td><td>${g.cst}</td><td>${brl(g.receita)}</td><td>${brl(g.pis)}</td><td>${brl(g.cof)}</td></tr>`;
    }).join('') || '<tr><td colspan="6">Nenhum item nos CFOPs de receita ou devolução.</td></tr>';
    $('#pisTotReceita').textContent = brl(tRec); $('#pisTotPis').textContent = brl(tPis); $('#pisTotCofins').textContent = brl(tCof);
    abrir(pisBack);
  });
  $('#pisClose').addEventListener('click', () => fechar(pisBack));
  $('#pisTable').addEventListener('click', (e) => { const td = e.target.closest('td'); if (td) copiar(td.textContent.trim(), td); });
  $('#pisCopyAll').addEventListener('click', (e) => {
    const out = [...pisBody.querySelectorAll('tr')].map((tr) => [...tr.children].map((td) => td.textContent.trim()).join(' | '));
    out.push(`Totais líquidos => Receita: ${$('#pisTotReceita').textContent} | PIS: ${$('#pisTotPis').textContent} | COFINS: ${$('#pisTotCofins').textContent}`);
    copiar(out.join('\n'), e.currentTarget);
  });

  /* ------------------------------------------------------- duplicidades */
  const dupBack = $('#dupBack'), dupLista = $('#dupCfopList'), dupBody = $('#dupTable tbody');
  let filtroDup = new Set(), cfopsEmitidos = [], dupEncontradas = [];
  const chaveFiltroDup = () => `dup_cfop_${digitos(U.cnpj) || 'cnpj'}_${U.ano || 'ano'}_${U.mes || 'mes'}`;

  function desenharFiltroDup() {
    dupLista.innerHTML = cfopsEmitidos.map((cf) => `<label><input type="checkbox" value="${esc(cf)}" ${filtroDup.has(cf) ? 'checked' : ''}> ${esc(cf)}</label>`).join('') ||
      '<small>Nenhum CFOP encontrado.</small>';
  }
  function montarDuplicadas() {
    const grupos = new Map(), chaves = new Set(), nfs = new Set();
    for (const r of U.linhas) {
      if (!r.emitFlag) continue;                                   // só emitidas
      if (filtroDup.size > 0 && !filtroDup.has(r.cfop)) continue;  // filtro de CFOP
      const dest = digitos(r.destCNPJ);
      if (!dest) continue;                                         // sem destinatário
      const chave = digitos(r.chave);
      if (chave) { if (chaves.has(chave)) continue; chaves.add(chave); }   // mesma chave
      const nf = String(r.nNF || '').trim();
      if (nf) { const assinatura = `${r.data}|${dest}|${nf}`; if (nfs.has(assinatura)) continue; nfs.add(assinatura); }
      const k = `${r.data}|${dest}|${r.cfop}|${(Math.round((r.val || 0) * 100) / 100).toFixed(2)}`;
      if (!grupos.has(k)) grupos.set(k, []);
      grupos.get(k).push(r);
    }
    dupEncontradas = [...grupos.values()].filter((l) => l.length > 1).flat();
    dupBody.innerHTML = dupEncontradas.map((r) => `<tr class="dup-tr"><td>${esc(r.data)}</td><td>${esc(r.nNF)}</td><td>${esc(digitos(r.destCNPJ))}</td><td>${esc(r.cfop)}</td><td>${brl(r.val)}</td><td>${esc(r.arquivo)}</td></tr>`).join('') ||
      '<tr><td colspan="6" style="text-align:center">Nenhuma duplicidade encontrada.</td></tr>';
    $('#dupCount').textContent = dupEncontradas.length ? `⚠️ ${dupEncontradas.length} possíveis duplicidades encontradas` : '✅ Nenhuma duplicidade encontrada';
  }
  $('#btnDuplicadas').addEventListener('click', () => {
    if (!U.linhas.length) { avisar('Gere o relatório primeiro', 'Não há itens lidos para procurar duplicidades.'); return; }
    $('#dupHeadInfo').innerHTML = `<b>${esc(U.nome || '-')}</b><br>CNPJ: ${esc(cnpjFormatado())}`;
    cfopsEmitidos = [...new Set(U.linhas.filter((r) => r.emitFlag).map((r) => r.cfop))].filter(Boolean).sort();
    const salvo = guardar.get(chaveFiltroDup(), null);
    filtroDup = new Set(Array.isArray(salvo) ? salvo.filter((c) => cfopsEmitidos.includes(c)) : cfopsEmitidos);
    if (!filtroDup.size) filtroDup = new Set(cfopsEmitidos);
    desenharFiltroDup(); montarDuplicadas(); abrir(dupBack);
  });
  $('#dupSelAll').addEventListener('click', () => { filtroDup = new Set(cfopsEmitidos); desenharFiltroDup(); guardar.set(chaveFiltroDup(), [...filtroDup]); montarDuplicadas(); });
  $('#dupClear').addEventListener('click', () => { filtroDup = new Set(); desenharFiltroDup(); guardar.set(chaveFiltroDup(), []); montarDuplicadas(); });
  $('#dupApply').addEventListener('click', () => {
    filtroDup = new Set([...dupLista.querySelectorAll('input:checked')].map((i) => i.value));
    if (!filtroDup.size) { avisar('Filtro vazio', 'Selecione ao menos um CFOP.'); return; }
    guardar.set(chaveFiltroDup(), [...filtroDup]); montarDuplicadas();
  });
  $('#dupClose').addEventListener('click', () => fechar(dupBack));
  $('#dupPdf').addEventListener('click', async () => {
    const td = 'padding:5px 7px;border-bottom:1px solid #eee;font-size:11.5px';
    const th = 'padding:6px 7px;background:#f4f6ff;text-align:left;font-size:12px';
    const linhas = dupEncontradas.map((r) => `<tr style="color:#c0392b;font-weight:600"><td style="${td}">${esc(r.data)}</td><td style="${td}">${esc(r.nNF)}</td><td style="${td}">${esc(digitos(r.destCNPJ))}</td><td style="${td}">${esc(r.cfop)}</td><td style="${td}">${brl(r.val)}</td><td style="${td}">${esc(r.arquivo)}</td></tr>`).join('') ||
      `<tr><td colspan="6" style="${td};text-align:center">Nenhuma duplicidade encontrada.</td></tr>`;
    await htmlParaPdf(cabecalhoPdf('Relatório de Notas Duplicadas') +
      `<div style="margin:0 0 10px;padding:6px 8px;background:#fff8e6;border:1px solid #ffd38a;border-radius:8px;font-size:13px;color:#7a5900;font-weight:700">${esc($('#dupCount').textContent)}</div>` +
      `<table style="width:100%;border-collapse:collapse"><tr><th style="${th}">Data</th><th style="${th}">Nº NF</th><th style="${th}">Destinatário</th><th style="${th}">CFOP</th><th style="${th}">Valor</th><th style="${th}">Arquivo</th></tr>${linhas}</table>`,
      `Notas_Duplicadas_${sufixo()}.pdf`);
  });
  $('#dupExcel').addEventListener('click', () => {
    const dados = [['Data', 'Nº NF', 'Destinatário (CNPJ/CPF)', 'CFOP', 'Valor', 'Arquivo']];
    dupEncontradas.forEach((r) => dados.push([r.data, r.nNF, digitos(r.destCNPJ), r.cfop, +r.val.toFixed(2), r.arquivo]));
    baixarExcel(dados, 'Duplicadas', `Notas_Duplicadas_${sufixo()}.xlsx`);
  });

  /* ---------------------------------------------------- salvar lançamento */
  $('#btnMemoria').addEventListener('click', async () => {
    if (!exigirRelatorio()) return;
    if (digitos(U.cnpj).length !== 14) { avisar('CNPJ não identificado', 'Não foi possível identificar o CNPJ completo da empresa nos XMLs.'); return; }
    const pg = calcularPgdas();
    const corpo = {
      cnpj: digitos(U.cnpj), mes: parseInt(U.mes, 10), ano: parseInt(U.ano, 10),
      total_receita: pg.total,
      devolucoes: Math.round(U.devo.total * 100) / 100,
      outras_receitas: Math.round(U.outr.total * 100) / 100,
      rec_sem_st: pg.vals[0], rec_com_st_mono: pg.vals[1], rec_monofasica: pg.vals[2], rec_com_st: pg.vals[3],
      detalhe: { totais_cfop: U.totais, emitidas: U.emit, devolucoes: U.devo, receita: U.rece, outras: U.outr,
                 cfops_receita: [...receitaCFOPs], cfops_outras: [...outrasCFOPs] }
    };
    if (U.companyId) corpo.company_id = U.companyId;
    if ([corpo.total_receita, corpo.rec_sem_st, corpo.rec_com_st_mono, corpo.rec_monofasica, corpo.rec_com_st].every((v) => v === 0)) {
      avisar('Nada para salvar', 'Nenhum valor foi apurado. Gere o relatório antes de salvar.'); return;
    }
    const botao = $('#btnMemoria'); botao.disabled = true;
    try {
      const r = await fetch(CFG.urlSalvar, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo) });
      let d = null; try { d = await r.json(); } catch (e) { /* resposta não JSON */ }
      if (d && d.status === 'ok') {
        let det = `${d.empresa || U.nome || ''} • ${U.mes}/${U.ano} • ${d.novo ? 'novo lançamento' : 'lançamento atualizado'}`;
        if (d.aviso) det += ` — ${d.aviso}`;
        if (d.url_lancamentos) det += ' • disponível em Lançamentos';
        $('#toastDetalhe').textContent = det;
        const t = $('#toastSucesso'); t.classList.add('show');
        t.onclick = () => t.classList.remove('show');
        setTimeout(() => t.classList.remove('show'), 4000);
      } else {
        avisar('Não foi possível salvar', (d && (d.msg || d.message)) || `O servidor respondeu ${r.status}.`, 'error');
      }
    } catch (e) {
      avisar('Sem comunicação', 'Não foi possível falar com o servidor. Verifique a conexão e tente de novo.', 'error');
    } finally { botao.disabled = false; }
  });

  /* ---------------------------------------------------- gerar outra empresa */
  $('#novaEmpresa').addEventListener('click', () => {
    arquivos = []; cacheCst.clear(); limparEstado();
    mesInput.value = ''; anoInput.value = ''; baseInput.value = '';
    progress.style.display = 'none'; bar.style.width = '0%';
    status.textContent = 'Nenhum XML carregado.';
    exportBtns.style.display = 'none';
    headerEmp.style.display = 'none'; headerEmp.innerHTML = '';
    totaisEl.innerHTML = ''; cardsEl.innerHTML = ''; tbody.innerHTML = ''; resumo.textContent = '';
    details.classList.remove('aberto'); toggleDet.textContent = 'Ver detalhes';
    document.querySelectorAll('.jan-fundo.show').forEach(fechar);
    fimLoading(); btnAnalisar.classList.remove('btn-pulse');
  });

  baseInput.addEventListener('input', () => { baseInput.value = digitos(baseInput.value).slice(0, 8); });

  // pré-preenche mês/ano/CNPJ base (vindo da tela de Lançamentos ou da URL)
  (function preencherInicial() {
    const q = new URLSearchParams(location.search);
    const mes = digitos(CFG.mesInicial || q.get('mes') || '').slice(0, 2);
    const ano = digitos(CFG.anoInicial || q.get('ano') || '').slice(0, 4);
    const base = digitos(CFG.cnpjBaseInicial || q.get('cnpj') || '').slice(0, 8);
    if (mes) mesInput.value = String(parseInt(mes, 10));
    if (ano) anoInput.value = ano;
    if (base) baseInput.value = base;
  })();

  // ganchos para teste automatizado
  window.__leitorNfe = { estado: () => U, carregar, gerarRelatorio, calcularPgdas };
})();
{% endraw %}
