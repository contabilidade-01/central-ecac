/* Transmitir PGDAS-D → /escritorio/api/pgdasd/* (Calcular, Enviar, Retificar, Consultar, DAS).
 *
 * Regras de custo aplicadas na TELA (o servidor repete todas):
 *  - antes de qualquer chamada paga, busca o estado (grátis) e mostra os bloqueios;
 *  - só envia depois do modal com empresa, competência, valores e custo estimado;
 *  - Enviar exige marcar "conferi os valores" e manda o hash do cálculo exibido;
 *  - DAS já guardado abre direto do Central (sem chamar a SERPRO);
 *  - um clique por vez (botões ficam ocupados até a resposta). */
(function () {
  'use strict';
  var CFG = window.PGDASD || {};
  var API = CFG.api || '/escritorio/api/pgdasd';
  var COMP = CFG.competencia || '';
  var CUSTOS = CFG.custos || {};
  var ocupado = false;

  var TIPOS = { rascunho: 'Não calculada', calculada: 'Calculada', transmitida: 'Transmitida',
    incerta: 'INCERTA (sem resposta da SERPRO)', erro: 'Erro' };
  var NOMES = { pre_visualizar: 'Pré-visualizar declaração', calcular: 'Calcular declaração',
    transmitir: 'Enviar declaração', retificar: 'Retificar declaração',
    consultar: 'Consultar na Receita', gerar_das: 'Gerar DAS', recuperar: 'Buscar declaração e recibo' };

  function $(s, el) { return (el || document).querySelector(s); }
  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function moeda(v) {
    return 'R$ ' + Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function dataBr(aaaammdd) {
    var d = String(aaaammdd || '').replace(/\D/g, '');
    return d.length === 8 ? d.slice(6) + '/' + d.slice(4, 6) + '/' + d.slice(0, 4) : '';
  }
  function hojeAaaammdd() {
    var d = new Date();
    return '' + d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
  }

  function log(msg) {
    var el = $('#logExecucao');
    if (!el) return;
    var linha = '[' + new Date().toLocaleTimeString('pt-BR') + '] ' + msg;
    el.textContent = (el.textContent.indexOf('Aguardando') === 0 || !el.textContent.trim()) ? linha : el.textContent + '\n' + linha;
    el.scrollTop = el.scrollHeight;
  }

  function setBusy(on) {
    ocupado = !!on;
    document.querySelectorAll('.js-pg, .js-lote').forEach(function (b) {
      if (on) b.setAttribute('data-busy', '1'); else b.removeAttribute('data-busy');
    });
  }

  function linhaInfo(tr) {
    return { cnpj: tr.dataset.cnpj, company_id: tr.dataset.companyId ? parseInt(tr.dataset.companyId, 10) : null,
      razao: ($('.col-empresa strong', tr) || {}).textContent || tr.dataset.cnpj };
  }

  function query(info) {
    var q = 'cnpj=' + encodeURIComponent(info.cnpj) + '&competencia=' + encodeURIComponent(COMP);
    if (info.company_id) q += '&company_id=' + info.company_id;
    return q;
  }

  async function lerJson(resp) {
    try { return await resp.json(); } catch (e) { return { ok: false, mensagem: 'HTTP ' + resp.status }; }
  }

  async function obterEstado(info) {
    var resp = await fetch(API + '/estado?' + query(info), { credentials: 'same-origin' });
    var j = await lerJson(resp);
    if (!resp.ok || !j.ok) throw new Error(j.mensagem || j.message || ('HTTP ' + resp.status));
    return j;
  }

  async function postar(op, corpo) {
    var rota = { calcular: 'calcular', transmitir: 'transmitir', retificar: 'transmitir', consultar: 'consultar',
      gerar_das: 'gerar-das', recuperar: 'recuperar-documentos' }[op];
    var resp = await fetch(API + '/' + rota, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(corpo)
    });
    var j = await lerJson(resp);
    j._status = resp.status;
    return j;
  }

  // ------------------------------------------------------------------ modal
  function confirmar(opcoes) {
    return new Promise(function (resolve) {
      var ov = $('#confirmaOverlay');
      $('#confirmaTitulo').textContent = opcoes.titulo;
      $('#confirmaSub').textContent = opcoes.sub || '';
      $('#confirmaCorpo').innerHTML = opcoes.html;
      var ok = $('#confirmaOk'), cancelar = $('#confirmaCancelar');
      ok.textContent = opcoes.botao || 'Confirmar';
      ok.hidden = !!opcoes.somenteLeitura;
      cancelar.textContent = opcoes.somenteLeitura ? 'Fechar' : 'Cancelar';
      var check = $('#confirmaConferi');
      function atualizar() { ok.disabled = !!(check && !check.checked); }
      if (check) check.addEventListener('change', atualizar);
      atualizar();
      function fechar(valor) {
        ov.classList.remove('show');
        ok.onclick = null; cancelar.onclick = null; document.removeEventListener('keydown', tecla);
        var extra = {};
        document.querySelectorAll('#confirmaCorpo [data-campo]').forEach(function (el) {
          extra[el.getAttribute('data-campo')] = el.type === 'checkbox' ? el.checked : el.value;
        });
        resolve(valor ? extra : null);
      }
      function tecla(e) { if (e.key === 'Escape') fechar(false); }
      ok.onclick = function () { fechar(true); };
      cancelar.onclick = function () { fechar(false); };
      document.addEventListener('keydown', tecla);
      ov.classList.add('show');
      (opcoes.somenteLeitura ? cancelar : ok).focus();
    });
  }

  function listaHtml(itens, classe) {
    if (!itens || !itens.length) return '';
    return '<ul class="confirma-lista ' + classe + '">' + itens.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') + '</ul>';
  }

  function valoresHtml(valores) {
    if (!valores || !valores.length) return '<p class="custo-nota">Sem valor devido calculado.</p>';
    var total = valores.reduce(function (s, v) { return s + Number(v.valor || 0); }, 0);
    return '<table class="confirma-tabela">' + valores.map(function (v) {
      return '<tr><td>' + esc(v.tributo || v.codigoTributo) + '</td><td>' + moeda(v.valor) + '</td></tr>';
    }).join('') + '<tr><td><b>Total</b></td><td><b>' + moeda(total) + '</b></td></tr></table>';
  }

  function custoOp(op, est) {
    if (op === 'consultar' || op === 'recuperar') return CUSTOS.consultar || 0;
    if (op === 'gerar_das') return CUSTOS.emitir || 0;
    var c = CUSTOS.declarar || 0;
    var consultaRecente = est.consultado_em && (Date.now() - Date.parse(est.consultado_em + 'Z') < 12 * 3600 * 1000);
    if (op === 'transmitir' && !consultaRecente) c += CUSTOS.consultar || 0;
    return c;
  }

  // ----------------------------------------------------------- linha da tabela
  function atualizarLinha(tr, est) {
    if (!tr || !est) return;
    var sit = est.situacao;
    var enviado = sit === 'transmitida' || est.lancamento_transmitido > 0;
    var status = sit === 'incerta' ? 'incerto' : (est.lancamento_transmitido === 2 ? 'manual'
      : (enviado ? 'enviado' : (est.ultimo_erro ? 'erro' : 'pendente')));
    tr.dataset.status = status;
    var badge = { enviado: '<span class="badge badge-ok">enviado</span>', manual: '<span class="badge badge-manual">Env. Manual</span>',
      incerto: '<span class="badge badge-incerto" title="Clique em Consultar antes de repetir">incerto</span>',
      erro: '<span class="badge badge-erro" title="' + esc(est.ultimo_erro || '') + '">erro</span>',
      pendente: '<span class="badge badge-pend">pendente</span>' }[status];
    var st = $('.col-status', tr); if (st) st.innerHTML = badge;
    var valor = (est.das && est.das.valor) || est.total_devido || 0;
    var receita = parseFloat(String(($('td.money', tr) || {}).textContent || '0').replace(/[^\d,]/g, '').replace(',', '.')) || 0;
    var cd = $('.col-das', tr); if (cd) cd.innerHTML = valor ? '<span class="money">' + moeda(valor) + '</span>' : '—';
    var ca = $('.col-aliq', tr);
    if (ca) ca.innerHTML = (valor && receita) ? '<span class="perc-pill">' + (valor / receita * 100).toFixed(2).replace('.', ',') + '%</span>' : '—';
    var ok = tr.dataset.ok === 'OK';
    function hab(sel, on) { tr.querySelectorAll(sel).forEach(function (b) { b.disabled = !on; }); }
    hab('[data-op="pre_visualizar"]', ok);
    hab('[data-op="calcular"]', ok && !enviado && sit !== 'incerta');
    hab('[data-op="transmitir"]', ok && !enviado && sit !== 'incerta');
    hab('[data-op="retificar"]', enviado);
    hab('[data-arq="das"]', est.das && est.das.tem_pdf);
    ['declaracao', 'recibo'].forEach(function (tipo) {
      var tem = !!(est.arquivos && est.arquivos[tipo]);
      tr.querySelectorAll('[data-arq="' + tipo + '"]').forEach(function (b) {
        b.dataset.tem = tem ? '1' : '0';
        b.disabled = !(tem || enviado);
        b.title = tem ? (tipo === 'recibo' ? 'Visualizar Recibo' : 'Visualizar Declaração')
          : 'Buscar ' + (tipo === 'recibo' ? 'recibo' : 'declaração') + ' na Receita (pago)';
      });
    });
  }

  function registrarResultado(info, op, r) {
    var s = r.serpro || {};
    var custo = s.custo_estimado ? ' • custo est. ' + moeda(s.custo_estimado) : (r.reuso ? ' • sem custo (PDF guardado)' : '');
    var texto = r.mensagem || s.mensagem || (r.bloqueios || []).join(' ') || (r.ok ? 'OK' : 'Falhou');
    log((r.ok ? '✔ ' : '✖ ') + NOMES[op] + ' — ' + info.razao + ' (' + info.cnpj + '): ' + texto + custo);
    return texto;
  }

  async function preVisualizar(tr, info) {
    log('… Pré-visualizar (grátis) — ' + info.razao + ' (' + info.cnpj + ')');
    var resp = await fetch(API + '/pre-visualizar', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cnpj: info.cnpj, competencia: COMP, company_id: info.company_id })
    });
    var r = await lerJson(resp);
    if (r.estado) atualizarLinha(tr, r.estado);
    if (!resp.ok || !r.ok) throw new Error(r.mensagem || ('HTTP ' + resp.status));
    var html = '<dl class="confirma-grid"><dt>Empresa</dt><dd>' + esc(info.razao) + '</dd><dt>CNPJ</dt><dd>' +
      esc(info.cnpj) + '</dd><dt>Competência</dt><dd>' + esc(COMP) + '</dd>';
    if (r.hash) html += '<dt>Hash</dt><dd><code>' + esc(r.hash) + '</code></dd>';
    html += '</dl>';
    html += listaHtml(r.bloqueios || [], 'bloq') + listaHtml(r.avisos || [], 'aviso');
    html += '<p class="custo-nota">JSON exato que seria enviado à SERPRO. Sem custo — nada sai do servidor.</p>';
    html += '<pre class="confirma-json">' + esc(JSON.stringify(r.dados || {}, null, 2)) + '</pre>';
    await confirmar({ titulo: NOMES.pre_visualizar, sub: 'Grátis — sem chamar a SERPRO',
      html: html, somenteLeitura: true });
    log('✔ Pré-visualizar — ' + info.razao + (r.hash ? ' • hash ' + r.hash.slice(0, 12) + '…' : '') +
      ((r.bloqueios || []).length ? ' • ' + r.bloqueios.length + ' bloqueio(s)' : ''));
  }

  // ------------------------------------------------------------- ação por linha
  async function executar(tr, op) {
    if (ocupado) return;
    var info = linhaInfo(tr);
    setBusy(true);
    try {
      if (op === 'pre_visualizar') {
        await preVisualizar(tr, info);
        return;
      }
      var j = await obterEstado(info);
      var est = j.estado, cfg = j.configuracao || {};
      atualizarLinha(tr, est);
      if (est.operacao) { mostrarAlerta('warning', 'Aguarde', '"' + est.operacao + '" já está em andamento nesta empresa.'); return; }

      // DAS guardado e dentro do vencimento: abre sem custo.
      if (op === 'gerar_das' && est.das && est.das.tem_pdf && (!est.das.vencimento || est.das.vencimento >= hojeAaaammdd())) {
        var reemitir = await confirmar({
          titulo: 'DAS já emitido', sub: info.razao + ' • ' + COMP, botao: 'Gerar nova guia (paga)',
          html: '<dl class="confirma-grid"><dt>Documento</dt><dd>' + esc(est.das.numero || '—') + '</dd><dt>Vencimento</dt><dd>' +
            esc(dataBr(est.das.vencimento)) + '</dd><dt>Valor</dt><dd>' + moeda(est.das.valor) + '</dd></dl>' +
            '<p class="custo-nota">O PDF está guardado no Central — use os botões 👁 / ⬇ da linha (sem custo). Só gere outra guia se precisar mudar a data de pagamento.</p>' +
            '<label class="confirma-extra">Data de pagamento (consolidação)<input type="date" data-campo="data_consolidacao"></label>' +
            '<div class="confirma-custo"><span>Custo estimado da nova emissão</span><b>' + moeda(CUSTOS.emitir) + '</b></div>'
        });
        if (!reemitir) return;
        if (!reemitir.data_consolidacao) { mostrarAlerta('warning', 'Informe a data', 'Para uma nova guia, informe a data de pagamento.'); return; }
        return await enviar(tr, info, op, { forcar: true, data_consolidacao: reemitir.data_consolidacao });
      }

      var bloqueios = (cfg.bloqueios || []).slice();
      if (op === 'transmitir' && !est.calculo_valido) bloqueios.push('Calcule antes de enviar (o valor calculado é conferido na transmissão).');
      if (op === 'retificar' && !est.calculo_valido) bloqueios.push('Ajuste o lançamento e clique em Calcular antes de retificar.');
      if ((op === 'calcular' || op === 'transmitir' || op === 'retificar') && est.situacao === 'incerta')
        bloqueios.push('O último envio ficou INCERTO. Clique em Consultar primeiro.');
      if (op === 'gerar_das' && est.situacao === 'incerta') bloqueios.push('Envio INCERTO: consulte a Receita antes de gerar o DAS.');
      var avisosTela = (cfg.avisos || []).slice();
      if (op === 'gerar_das' && est.das && est.das.tem_pdf && est.das.vencimento && est.das.vencimento < hojeAaaammdd())
        avisosTela.unshift('A guia guardada venceu em ' + dataBr(est.das.vencimento) + '. Informe a data de pagamento abaixo.');

      var html = '<dl class="confirma-grid"><dt>Empresa</dt><dd>' + esc(info.razao) + '</dd><dt>CNPJ</dt><dd>' + esc(info.cnpj) +
        '</dd><dt>Competência</dt><dd>' + esc(COMP) + '</dd><dt>Situação</dt><dd>' + esc(TIPOS[est.situacao] || est.situacao) + '</dd>';
      if (est.id_declaracao) html += '<dt>Declaração</dt><dd>' + esc(est.id_declaracao) + '</dd>';
      html += '</dl>';
      var extraCorpo = {}, somenteLeitura = false, externa = false;

      if (op === 'transmitir' || op === 'retificar') {
        html += '<strong>Valores calculados pela Receita</strong>' + valoresHtml(est.valores_devidos) +
          '<p class="custo-nota">Com "comparação" ligada: se a Receita apurar valor diferente, ela NÃO transmite.</p>';
        extraCorpo = { retificar: op === 'retificar', hash_confirmado: est.hash_calculo };
      } else if (op === 'calcular') {
        html += '<p class="custo-nota">A Receita devolve os valores devidos SEM transmitir. Confira com a apuração antes de enviar.</p>';
      } else if (op === 'recuperar') {
        html += '<p class="custo-nota">Baixa da Receita a declaração e o recibo da ÚLTIMA declaração do período ' +
          '(ex.: entregue no PGDAS-D web) e guarda no Central. Depois abrem sem custo.</p>';
      } else if (op === 'consultar') {
        html += '<p class="custo-nota">Mostra se já existe declaração/DAS do período (inclusive feitos no PGDAS-D web).</p>';
      } else if (op === 'gerar_das') {
        var naReceita = est.consulta && est.consulta.declaracoes && est.consulta.declaracoes.length;
        if (est.situacao !== 'transmitida' && !naReceita) {
          externa = true;
          html += '<label class="confirma-check"><input type="checkbox" id="confirmaConferi" data-campo="confirmar_externa"> ' +
            'Confirmo que a declaração deste período JÁ FOI transmitida (fora do Central). Sem declaração a SERPRO recusa e cobra.</label>' +
            '<p class="custo-nota">Na dúvida, cancele e use Consultar (mais barato).</p>';
        }
        html += '<label class="confirma-extra">Data de pagamento (opcional; em branco = vencimento normal)<input type="date" data-campo="data_consolidacao"></label>';
      }
      if (bloqueios.length) { somenteLeitura = true; html = listaHtml(bloqueios, 'bloq') + html; }
      html += listaHtml(avisosTela, 'aviso');
      if (!somenteLeitura) {
        html += '<div class="confirma-custo"><span>Custo estimado SERPRO</span><b>' + moeda(custoOp(op, est)) + '</b></div>';
        if ((op === 'transmitir' || op === 'retificar') && !externa)
          html += '<label class="confirma-check"><input type="checkbox" id="confirmaConferi"> Conferi empresa, competência e valores.</label>';
      }
      var resposta = await confirmar({ titulo: NOMES[op], sub: somenteLeitura ? 'Nada foi enviado — resolva os itens abaixo.' : 'Chamada paga à SERPRO',
        botao: NOMES[op], html: html, somenteLeitura: somenteLeitura });
      if (!resposta) return;
      var corpo = Object.assign({}, extraCorpo);
      if (resposta.data_consolidacao) corpo.data_consolidacao = resposta.data_consolidacao;
      if (resposta.confirmar_externa) corpo.confirmar_externa = true;
      await enviar(tr, info, op, corpo);
    } catch (e) {
      log('✖ ' + NOMES[op] + ' — ' + info.razao + ': ' + e.message);
      mostrarAlerta('error', 'Não foi possível continuar', e.message);
    } finally {
      setBusy(false);
    }
  }

  async function enviar(tr, info, op, extra) {
    log('… ' + NOMES[op] + ' — ' + info.razao + ' (' + info.cnpj + ')');
    var corpo = Object.assign({ cnpj: info.cnpj, competencia: COMP, company_id: info.company_id, confirmar_custo: true }, extra || {});
    var r = await postar(op, corpo);
    if (r.estado) atualizarLinha(tr, r.estado);
    var texto = registrarResultado(info, op, r);
    if (r.ok) {
      var det = '';
      if (op === 'calcular' && r.estado) det = 'Total devido: ' + moeda(r.estado.total_devido) + '. Confira e clique em Enviar.';
      if (op === 'transmitir' || op === 'retificar') det = 'Declaração ' + ((r.estado || {}).id_declaracao || '') + ' transmitida. Recibo guardado.';
      if (op === 'gerar_das') det = 'DAS guardado no Central.';
      if (op === 'recuperar') det = r.mensagem || 'Documentos guardados.';
      if (op === 'consultar' && r.estado) det = (r.estado.consulta.declaracoes || []).length ? 'Há declaração na Receita para o período.' : 'A Receita não tem declaração deste período.';
      mostrarAlerta('success', NOMES[op], det || texto);
      if (op === 'gerar_das') abrirArquivo(info, 'das', false);
    } else {
      var incerto = r.serpro && r.serpro.incerto;
      mostrarAlerta(incerto ? 'warning' : 'error', incerto ? 'Resultado incerto' : NOMES[op] + ' não concluído',
        texto + (incerto ? '\nNão repita: clique em Consultar para saber se foi recebido.' : '') +
        ((r.avisos || []).length ? '\n\n' + r.avisos.join('\n') : ''));
      (r.avisos || []).forEach(function (a) { log('⚠ ' + a); });
    }
  }

  function abrirArquivo(info, tipo, download) {
    var url = API + '/arquivo/' + tipo + '?' + query(info) + (download ? '&download=1' : '');
    if (download) { window.location.href = url; } else { window.open(url, '_blank', 'noopener'); }
  }

  // ------------------------------------------------------------------- lotes
  function selecionadas() {
    var itens = [];
    document.querySelectorAll('.chkEmpresa:checked').forEach(function (chk) {
      var tr = chk.closest('tr');
      if (tr && tr.style.display !== 'none' && chk.value) itens.push(linhaInfo(tr));
    });
    return itens;
  }

  async function lote(acao) {
    if (ocupado) return;
    var itens = selecionadas();
    if (!itens.length) { mostrarAlerta('warning', 'Selecione empresas', 'Marque ao menos uma empresa cadastrada.'); return; }
    if (acao === 'zip') {
      window.location.href = API + '/das-zip?competencia=' + encodeURIComponent(COMP) + '&cnpjs=' +
        itens.map(function (i) { return i.cnpj; }).join(',') + '&company_ids=' + itens.map(function (i) { return i.company_id; }).join(',');
      log('ZIP dos DAS guardados (' + itens.length + ' selecionada(s)) — sem custo.');
      return;
    }
    var unit = acao === 'gerar_das' ? CUSTOS.emitir : acao === 'consultar' ? CUSTOS.consultar : CUSTOS.declarar;
    var nomes = { calcular: 'Calcular selecionadas', transmitir: 'Enviar lote', gerar_das: 'Gerar DAS lote' };
    var html = '<p>' + itens.length + ' empresa(s) em <b>' + esc(COMP) + '</b>.</p>' +
      '<ul class="confirma-lista">' + itens.slice(0, 12).map(function (i) { return '<li>' + esc(i.razao) + '</li>'; }).join('') +
      (itens.length > 12 ? '<li>… e mais ' + (itens.length - 12) + '</li>' : '') + '</ul>' +
      '<p class="custo-nota">Cada empresa passa pelo pré-voo: quem não estiver apta é PULADA sem custo' +
      (acao === 'transmitir' ? ' (só envia quem tem cálculo válido; o valor calculado é comparado)' : '') +
      (acao === 'gerar_das' ? ' (quem já tem PDF guardado reaproveita sem custo; sem declaração conhecida é pulada)' : '') +
      '. O lote para sozinho após 2 falhas seguidas da SERPRO.</p>' +
      '<div class="confirma-custo"><span>Custo máximo estimado</span><b>' + moeda(unit * itens.length) + '</b></div>' +
      (acao === 'transmitir' ? '<label class="confirma-check"><input type="checkbox" id="confirmaConferi"> Conferi os valores calculados das empresas selecionadas.</label>' : '');
    var resp = await confirmar({ titulo: nomes[acao], sub: 'Chamadas pagas à SERPRO', botao: nomes[acao], html: html });
    if (!resp) return;
    setBusy(true);
    try {
      var r = await fetch(API + '/lote/iniciar', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ acao: acao, competencia: COMP, confirmar_custo: true,
          company_ids: itens.map(function (i) { return i.company_id; }),
          itens: itens.map(function (i) { return { cnpj: i.cnpj }; }) })
      });
      var j = await lerJson(r);
      if (!r.ok || !j.ok) throw new Error(j.mensagem || ('HTTP ' + r.status));
      log('Lote ' + j.lote.id + ' (' + nomes[acao] + ') iniciado com ' + itens.length + ' empresa(s).');
      var vistos = 0, estado = j.lote;
      while (estado.status === 'rodando') {
        await new Promise(function (ok) { setTimeout(ok, 1500); });
        var s = await fetch(API + '/lote/' + encodeURIComponent(estado.id), { credentials: 'same-origin' });
        var js = await lerJson(s);
        if (!s.ok || !js.ok) throw new Error(js.mensagem || ('HTTP ' + s.status));
        estado = js.lote;
        estado.itens.slice(vistos).forEach(function (it) {
          log((it.ok ? '✔ ' : (it.pulado ? '⏭ ' : '✖ ')) + (it.razao || it.cnpj) + ': ' + (it.mensagem || (it.ok ? 'OK' : '')) +
            (it.custo ? ' • custo est. ' + moeda(it.custo) : ''));
        });
        vistos = estado.itens.length;
      }
      log('Lote ' + estado.id + ': ' + estado.mensagem + ' OK ' + estado.ok + ' • erros ' + estado.erros + ' • pulados ' +
        estado.pulados + ' • custo est. ' + moeda(estado.custo_estimado));
      mostrarAlerta(estado.status === 'concluido' ? 'success' : 'warning', nomes[acao], estado.mensagem +
        '\nOK: ' + estado.ok + ' • Erros: ' + estado.erros + ' • Pulados: ' + estado.pulados + '\nRecarregue a página para ver a situação de cada empresa.');
    } catch (e) {
      log('✖ Lote: ' + e.message);
      mostrarAlerta('error', 'Lote não concluído', e.message);
    } finally {
      setBusy(false);
    }
  }

  document.addEventListener('click', function (e) {
    var b = e.target.closest('.js-pg');
    if (b && !b.disabled) { e.preventDefault(); executar(b.closest('tr'), b.getAttribute('data-op')); return; }
    var a = e.target.closest('.js-arq');
    if (a && !a.disabled && a.dataset.tem === '0') { e.preventDefault(); executar(a.closest('tr'), 'recuperar'); return; }
    if (a && !a.disabled) { e.preventDefault(); abrirArquivo(linhaInfo(a.closest('tr')), a.getAttribute('data-arq'), a.getAttribute('data-download') === '1'); return; }
    var l = e.target.closest('.js-lote');
    if (l) { e.preventDefault(); lote(l.getAttribute('data-lote')); }
  });
})();
