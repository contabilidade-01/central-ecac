/* Leitor PGDAS-D (PDF + OCR) — adaptado do Integra Contador impext/imp.php v16
 * para Central e-CAC. Parsing no browser; gravacao via /escritorio/api/pgdas/importar.
 */

// =====================================================
// LEITURA DO PDF + OCR
// =====================================================

async function extrairTextoPDF(file){
  const arrayBuffer = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({data: arrayBuffer}).promise;
  const total = pdf.numPages;

  let paginas = [];
  let textoNormal = "";
  let usouOCR = false;

  for(let i = 1; i <= total; i++){
    const page = await pdf.getPage(i);
    const content = await page.getTextContent();
    const textoPagina = content.items.map(item => item.str).join(" ");
    paginas.push(textoPagina);
    textoNormal += "\n" + textoPagina;

    document.getElementById("progress").style.width = ((i / total) * 20) + "%";
  }

  const textoLimpo = normalizarTextoCompacto(textoNormal);

  // PDFs escaneados devem ser lidos primeiro na imagem ORIGINAL.
  // A versão anterior aplicava contraste antes do primeiro OCR e isso podia
  // unir números de células diferentes, transformando, por exemplo,
  // 20.012,17 em 1.209.012,17.
  if(textoLimpo.length < 120 || !/(Receita|Simples|PGDAS|Apura[çc][aã]o|Extrato)/i.test(textoLimpo)){
    usouOCR = true;
    paginas = [];

    const totalEtapasBase = Math.max(1, total * 2);

    for(let i = 1; i <= total; i++){
      const page = await pdf.getPage(i);
      const viewport = page.getViewport({scale: 3.2});
      const canvasOriginal = document.createElement("canvas");
      const ctxOriginal = canvasOriginal.getContext("2d", {willReadFrequently:true});

      canvasOriginal.width = Math.ceil(viewport.width);
      canvasOriginal.height = Math.ceil(viewport.height);

      await page.render({canvasContext:ctxOriginal, viewport}).promise;

      const textosOCR = [];
      const modos = [
        {nome:"original tabela", psm:"4", canvas:canvasOriginal},
        {nome:"original linhas", psm:"6", canvas:canvasOriginal}
      ];

      for(let j = 0; j < modos.length; j++){
        const modo = modos[j];

        dropzone.innerHTML = `🔎 PDF escaneado/imagem detectado.<br><br><small>OCR ${modo.nome} na página ${i} de ${total}. Aguarde...</small>`;

        const ret = await Tesseract.recognize(modo.canvas, "por", {
          tessedit_pageseg_mode: modo.psm,
          preserve_interword_spaces: "1",
          logger: m => {
            if(m.status === "recognizing text" && typeof m.progress === "number"){
              const base = 20;
              const faixa = 80 / totalEtapasBase;
              const etapa = ((i - 1) * 2) + j;
              const pct = base + (etapa * faixa) + (m.progress * faixa);
              document.getElementById("progress").style.width = Math.min(98, pct) + "%";
            }
          }
        });

        textosOCR.push(ret.data.text || "");
      }

      // Reforço somente como último recurso e em uma cópia do canvas.
      // Nunca altera a imagem usada nas duas leituras principais.
      const textoBase = normalizarTextoCompacto(textosOCR.join(" "));
      if(textoBase.length < 30){
        const canvasReforcado = document.createElement("canvas");
        canvasReforcado.width = canvasOriginal.width;
        canvasReforcado.height = canvasOriginal.height;

        const ctxReforcado = canvasReforcado.getContext("2d", {willReadFrequently:true});
        ctxReforcado.drawImage(canvasOriginal, 0, 0);
        reforcarImagemParaOCR(ctxReforcado, canvasReforcado.width, canvasReforcado.height);

        dropzone.innerHTML = `🔎 PDF escaneado/imagem detectado.<br><br><small>OCR reforçado na página ${i} de ${total}. Aguarde...</small>`;

        const retReforcado = await Tesseract.recognize(canvasReforcado, "por", {
          tessedit_pageseg_mode: "4",
          preserve_interword_spaces: "1"
        });

        textosOCR.push(retReforcado.data.text || "");
      }

      paginas.push(textosOCR.join("\n\n--- OCR LEITURA ---\n\n"));
    }
  }

  document.getElementById("progress").style.width = "100%";
  paginas.usouOCR = usouOCR;
  return paginas;
}

function reforcarImagemParaOCR(ctx, w, h){
  try{
    const img = ctx.getImageData(0, 0, w, h);
    const data = img.data;

    for(let i = 0; i < data.length; i += 4){
      const r = data[i];
      const g = data[i + 1];
      const b = data[i + 2];
      let cinza = Math.round((r * 0.299) + (g * 0.587) + (b * 0.114));

      // Aumenta contraste mantendo anti-aliasing.
      cinza = cinza < 185 ? Math.max(0, cinza - 35) : 255;

      data[i] = cinza;
      data[i + 1] = cinza;
      data[i + 2] = cinza;
    }

    ctx.putImageData(img, 0, 0);
  }catch(e){
    console.warn("Não foi possível reforçar a imagem para OCR:", e);
  }
}

// =====================================================
// FUNÇÕES AUXILIARES
// =====================================================

function normalizarTextoLinhas(txt){
  return String(txt || "")
    .replace(/\u00a0/g, " ")
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/\r/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/\n[ \t]+/g, "\n")
    .trim();
}

function normalizarTextoCompacto(txt){
  return normalizarTextoLinhas(txt)
    .replace(/\s+/g, " ")
    .trim();
}

function corrigirOCR(txt){
  return String(txt || "")
    .replace(/C\.\s*N\.\s*P\.\s*J\./gi, "CNPJ")
    .replace(/Período/g, "Período")
    .replace(/Apuracao/gi, "Apuração")
    .replace(/Salarios/gi, "Salários");
}

function escapeHtml(str){
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function limparNomeArquivo(nome){
  return String(nome || "")
    .replace(/\.pdf$/i, "")
    .replace(/^\d{2}\s+\d{4}\s+/i, "")
    .replace(/\s+EXTRATO.*$/i, "")
    .replace(/[\-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toUpperCase();
}

function converterMesAno(str){
  const m = String(str || "").match(/(\d{2})\s*\/\s*([12]\s*\d\s*\d\s*\d)/);

  if(m){
    const mes = m[1].padStart(2, "0");
    const ano = m[2].replace(/\s+/g, "");
    return `${ano}-${mes}`;
  }

  const p = String(str || "").trim().split("/");
  return p.length === 2 ? `${p[1]}-${p[0]}` : str;
}

function aaaammParaMMAAAA(aaaamm){
  const [a, m] = String(aaaamm || "").split("-");
  return `${m}/${a}`;
}

function somarMes(aaaamm, delta){
  const [a, m] = String(aaaamm || "").split("-").map(Number);
  if(!a || !m) return "";
  const d = new Date(a, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function gerarMesesEsperados(paAAAAMM, qtd = 13){
  const meses = [];
  for(let i = 0; i < qtd; i++){
    meses.push(somarMes(paAAAAMM, -i));
  }
  return meses.filter(Boolean);
}

function limparValorOCR(v){
  let s = String(v || "")
    .replace(/[Oo]/g, "0")
    .replace(/[IiLl|!]/g, "1")
    .replace(/[Ss]/g, "5")
    .replace(/[Bb]/g, "8")
    .replace(/\s+/g, "")
    .replace(/[^0-9.,-]/g, "");

  if(!s) return "0,00";

  // Se houver mais de uma vírgula, mantém a última como decimal.
  const ultimaVirgula = s.lastIndexOf(",");
  if(ultimaVirgula >= 0){
    let inteiro = s.slice(0, ultimaVirgula).replace(/,/g, "");
    let dec = s.slice(ultimaVirgula + 1).replace(/[^0-9]/g, "");

    if(dec.length === 0) dec = "00";
    if(dec.length === 1) dec = dec + "0";
    if(dec.length > 2) dec = dec.slice(0, 2);

    inteiro = inteiro || "0";
    return `${inteiro},${dec}`;
  }

  // Sem vírgula: tenta tratar últimos 2 dígitos como centavos.
  s = s.replace(/\./g, "");
  if(s.length <= 2) return `0,${s.padStart(2, "0")}`;
  return `${s.slice(0, -2)},${s.slice(-2)}`;
}

function moedaParaNumero(v){
  const limpo = limparValorOCR(v);
  return parseFloat(String(limpo || "0").replace(/\./g, "").replace(",", ".")) || 0;
}

function numeroParaMoeda(n){
  return (Number(n) || 0).toLocaleString("pt-BR", {minimumFractionDigits:2, maximumFractionDigits:2});
}

function detectarTipoPDF(txt){
  if(/CNPJ\s+Matriz:/i.test(txt) || /Declarat[óo]rio/i.test(txt) || /N[ºo]\s+da\s+Declara[çc][aã]o/i.test(txt)){
    return "Declaração";
  }

  if(/Apura[çc][aã]o\s+do\s+Simples\s+Nacional/i.test(txt) && !/Extrato\s+do\s+Simples\s+Nacional/i.test(txt)){
    return "Apuração";
  }

  if(/Extrato\s+do\s+Simples\s+Nacional/i.test(txt)){
    return "Extrato";
  }

  return "PGDAS-D";
}

function blocoEntre(txt, inicioRegex, fimRegex){
  const inicio = txt.search(inicioRegex);
  if(inicio < 0) return "";

  const sub = txt.slice(inicio);
  const fim = sub.search(fimRegex);
  return fim >= 0 ? sub.slice(0, fim) : sub;
}

function blocoReceitasMercadoInterno(txt){
  // Modelo com o subtítulo normal "2.2.1 Mercado Interno"
  let bloco = blocoEntre(
    txt,
    /2\s*[\.)]?\s*2\s*[\.)]?\s*1\s*[\.)]?\s*Mercado\s+Interno/i,
    /2\s*[\.)]?\s*2\s*[\.)]?\s*2\s*[\.)]?\s*Mercado\s+Externo|2\s*[\.)]?\s*3\s*[\.)]?\s*Folha|Folha\s+de\s+Sal[áa]rios\s+Anteriores/i
  );

  if(bloco) return bloco;

  // OCR de alguns PDFs escaneados apaga a linha "Mercado Interno".
  // Nesse caso pega a partir de "Receitas Brutas Anteriores", mas NUNCA
  // deixa o bloco interno avançar sobre o bloco de Mercado Externo.
  const titulo = txt.search(/Receitas?\s+Brutas\s+Anteriores/i);
  if(titulo >= 0){
    const sub = txt.slice(titulo);
    const mi = sub.search(/Mercado\s+Interno/i);

    if(mi >= 0){
      const subMi = sub.slice(mi);
      const fim = subMi.search(/Mercado\s+Externo|Folha\s+de\s+Sal[áa]rios\s+Anteriores|2\s*[\.)]?\s*3/i);
      return fim >= 0 ? subMi.slice(0, fim) : subMi;
    }

    // Se o OCR perdeu apenas o título de Mercado Interno, mas preservou
    // Mercado Externo, tudo antes do título externo pertence ao bloco interno.
    const me = sub.search(/2\s*[\.)]?\s*2\s*[\.)]?\s*2\s*[\.)]?\s*Mercado\s+Externo|Mercado\s+Externo/i);
    if(me >= 0){
      return sub.slice(0, me);
    }

    const fim = sub.search(/Folha\s+de\s+Sal[áa]rios\s+Anteriores|2\s*[\.)]?\s*3\s*[\.)]?\s*Folha/i);
    return fim >= 0 ? sub.slice(0, fim) : sub;
  }

  return "";
}

function blocoReceitasMercadoExterno(txt){
  // Espelho do Mercado Interno: lê exclusivamente o bloco 2.2.2.
  let bloco = blocoEntre(
    txt,
    /2\s*[\.)]?\s*2\s*[\.)]?\s*2\s*[\.)]?\s*Mercado\s+Externo/i,
    /2\s*[\.)]?\s*3\s*[\.)]?\s*Folha|Folha\s+de\s+Sal[áa]rios\s+Anteriores|2\s*[\.)]?\s*4\s*[\.)]?\s*Fator/i
  );

  if(bloco) return bloco;

  // Fallback para OCR: somente começa a ler quando encontra explicitamente
  // "Mercado Externo", evitando duplicar Mercado Interno.
  const titulo = txt.search(/Receitas?\s+Brutas\s+Anteriores/i);
  if(titulo >= 0){
    const sub = txt.slice(titulo);
    const me = sub.search(/Mercado\s+Externo/i);

    if(me >= 0){
      const subMe = sub.slice(me);
      const fim = subMe.search(/Folha\s+de\s+Sal[áa]rios\s+Anteriores|2\s*[\.)]?\s*3\s*[\.)]?\s*Folha|2\s*[\.)]?\s*4\s*[\.)]?\s*Fator/i);
      return fim >= 0 ? subMe.slice(0, fim) : subMe;
    }
  }

  return "";
}

function blocoFolhas(txt){
  // v7:
  // Não pode parar em "Total de Folhas".
  // Em PDF escaneado/OCR, às vezes o Tesseract joga a linha "2.3.1 Total..."
  // antes da última linha da tabela mensal. Quando parava nesse texto,
  // os meses 12/2025, 01/2026, 02/2026 e 03/2026 ficavam fora do bloco.
  // Agora o corte só acontece no próximo bloco real: Fator r / Valores Fixos / Estabelecimentos.
  let bloco = blocoEntre(
    txt,
    /2\s*[\.)]?\s*3\s*[\.)]?\s*Folha\s+de\s+Sal[áa]rios\s+Anteriores|Folha\s+de\s+Sal[áa]rios\s+Anteriores/i,
    /2\s*[\.)]?\s*4\s*[\.)]?\s*Fator|Fator\s*r|2\s*[\.)]?\s*5\s*[\.)]?\s*Valores\s+Fixos|3\s*[\.)]?\s*Informa[çc][õo]es\s+dos\s+Estabelecimentos|CNPJ\s+Estabelecimento/i
  );

  if(bloco){
    return bloco;
  }

  // Fallback mais amplo: pega uma janela grande após o título da folha.
  // Isso evita perder meses quando o OCR desordena linhas da tabela.
  const t = String(txt || "");
  const ini = t.search(/Folha\s+de\s+Sal[áa]rios\s+Anteriores/i);
  if(ini >= 0){
    return t.slice(ini, ini + 1800);
  }

  return "";
}

function extrairPrimeiro(txt, regex, padrao = "Não encontrado"){
  const m = txt.match(regex);
  return m && m[1] ? m[1].trim() : padrao;
}

function extrairNome(txtCompacto, fileName){
  const bruto = String(txtCompacto || "");
  const candidatos = [];

  function limparCandidato(nome){
    let n = String(nome || "")
      .replace(/\s+(?:Data\s+de\s+Abertura|Data\s+de\s+abertura\s+no\s+CNPJ|Regime\s+de\s+Apura[çc][aã]o|Optante\s+pelo\s+Simples|N[ºo]\s+da\s+Declara[çc][aã]o|Per[ií]odo\s+de\s+Apura[çc][aã]o|CNPJ|Página|Pagina|\d\)|Informa[çc][õo]es).*/i, "")
      .replace(/[|\[\]]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();

    // Erro comum do OCR em razão social terminada por LTDA.
    n = n.replace(/\bL[\s\.]*[I1][\s\.]*D[\s\.]*A\b$/i, "LTDA");

    if(n.length < 3) return "";
    if(/^(Data|Regime|Optante|Per[ií]odo|CNPJ|P[áa]gina|Extrato|Apura[çc][aã]o)$/i.test(n)) return "";
    return n;
  }

  // Reúne todos os candidatos das diferentes passadas do OCR e escolhe o mais completo.
  const linhas = bruto.split(/\n+/);
  for(const linha of linhas){
    const mLinha = linha.match(/Nome\s+Empresarial\s*[:\-]?\s*(.+)$/i);
    if(mLinha && mLinha[1]){
      const candidato = limparCandidato(mLinha[1]);
      if(candidato) candidatos.push(candidato);
    }
  }

  const rxGlobal = /Nome\s+Empresarial\s*[:\-]?\s*([A-Z0-9ÁÀÂÃÉÊÍÓÔÕÚÜÇ&\.\-\/\s]{3,180}?)(?=\s+(?:Data\s+de\s+Abertura|Data\s+de\s+abertura\s+no\s+CNPJ|Regime\s+de\s+Apura[çc][aã]o|Optante\s+pelo\s+Simples|N[ºo]\s+da\s+Declara[çc][aã]o|Per[ií]odo\s+de\s+Apura[çc][aã]o|CNPJ|Página|Pagina|\d\)|Informa[çc][õo]es))/ig;
  let mg;
  while((mg = rxGlobal.exec(bruto)) !== null){
    const candidato = limparCandidato(mg[1]);
    if(candidato) candidatos.push(candidato);
  }

  if(candidatos.length){
    // Evita devolver apenas a primeira palavra quando outra leitura trouxe a razão social completa.
    candidatos.sort((a, b) => {
      const palavrasA = a.split(/\s+/).length;
      const palavrasB = b.split(/\s+/).length;
      return (palavrasB - palavrasA) || (b.length - a.length);
    });
    return candidatos[0];
  }

  // Modelo Apuração: nome aparece no início antes de "Página".
  const nomeInicio = extrairPrimeiro(
    bruto,
    /^\s*([A-Z0-9ÁÀÂÃÉÊÍÓÔÕÚÜÇ&\.\-\/\s]{5,150}?)\s+P[áa]gina\s*[:\d]/i,
    "Não encontrado"
  );

  if(nomeInicio !== "Não encontrado" && !/^(Extrato|Apura[çc][aã]o|PGDAS)/i.test(nomeInicio)){
    return limparCandidato(nomeInicio) || nomeInicio.replace(/\s+/g, " ").trim();
  }

  return limparNomeArquivo(fileName) || "Não encontrado";
}

function somenteDigitosOCR(v){
  return String(v || "")
    .replace(/[Oo]/g, "0")
    .replace(/[IiLl|!]/g, "1")
    .replace(/[Ss]/g, "5")
    .replace(/[Bb]/g, "8")
    .replace(/[^0-9]/g, "");
}

function calcularDigitosCnpj(base12){
  const n = String(base12 || "").replace(/\D/g, "");
  if(n.length !== 12) return "";

  function dv(parcial, pesos){
    let soma = 0;
    for(let i = 0; i < parcial.length; i++){
      soma += Number(parcial[i]) * pesos[i];
    }
    const resto = soma % 11;
    return resto < 2 ? 0 : 11 - resto;
  }

  const d1 = dv(n, [5,4,3,2,9,8,7,6,5,4,3,2]);
  const d2 = dv(n + d1, [6,5,4,3,2,9,8,7,6,5,4,3,2]);
  return String(d1) + String(d2);
}

function montarCnpjMatrizPorBase8(base8){
  const b = somenteDigitosOCR(base8).slice(0, 8);
  if(b.length !== 8) return "";

  const base12 = b + "0001";
  const dv = calcularDigitosCnpj(base12);
  const cnpj = base12 + dv;

  return validarCnpj(cnpj) ? formatarCnpj(cnpj) : "";
}

function extrairCnpjPorJanela(txt, regexLabel){
  const t = String(txt || "");
  const m = regexLabel.exec(t);
  if(!m) return "";

  const trecho = t.slice(m.index, m.index + 140);
  const digitos = somenteDigitosOCR(trecho);

  // Depois do rótulo pode vir CNPJ com pontuação quebrada pelo OCR.
  // Procura um grupo de 14 dígitos válido dentro da janela.
  for(let i = 0; i <= digitos.length - 14; i++){
    const cand = digitos.slice(i, i + 14);
    if(validarCnpj(cand)) return cand;
  }

  return "";
}

function validarCnpj(cnpj){
  const n = String(cnpj || "").replace(/\D/g, "");
  if(n.length !== 14) return false;
  if(/^(\d)\1{13}$/.test(n)) return false;
  return calcularDigitosCnpj(n.slice(0, 12)) === n.slice(12);
}

function extrairCnpj(txt){
  const t = String(txt || "");

  // =====================================================
  // CNPJ - ORDEM SEGURA
  // =====================================================
  // Importante:
  // Nos PDFs escaneados/OCR, datas e horas podem formar 14 dígitos
  // que passam no dígito verificador por coincidência, por exemplo:
  // 10.100.705/2026-08.
  // Por isso NÃO podemos procurar qualquer sequência de 14 dígitos
  // antes de tentar os rótulos reais de CNPJ.
  // =====================================================

  function cnpjPorTextoCompletoProximo(labelRegex){
    const mLabel = labelRegex.exec(t);
    if(!mLabel) return "";

    const trecho = t.slice(mLabel.index, mLabel.index + 180);

    // 1) CNPJ completo com barra, aceitando espaços/pontos quebrados pelo OCR.
    const rxComBarra = /([0-9OoIiLl|!SsBb]{2}\s*\.?\s*[0-9OoIiLl|!SsBb]{3}\s*\.?\s*[0-9OoIiLl|!SsBb]{3}\s*[/\\|]\s*[0-9OoIiLl|!SsBb]{4}\s*[-–—\s]?\s*[0-9OoIiLl|!SsBb]{2})/;
    const mCnpj = trecho.match(rxComBarra);

    if(mCnpj && mCnpj[1]){
      const n = somenteDigitosOCR(mCnpj[1]);
      if(validarCnpj(n)) return formatarCnpj(n);
    }

    // 2) CNPJ completo sem separador, mas ainda dentro da janela do rótulo.
    // Usa somente a janela de rótulos confiáveis, não o PDF inteiro.
    const digitos = somenteDigitosOCR(trecho);
    for(let i = 0; i <= digitos.length - 14; i++){
      const cand = digitos.slice(i, i + 14);
      if(validarCnpj(cand)) return formatarCnpj(cand);
    }

    return "";
  }

  function cnpjPorNumeroApuracao(){
    // Fallback v12 para PDFs escaneados/OCR.
    // O número da apuração vem assim: 19748516202604001
    // Estrutura: CNPJ básico (8) + ano (4) + mês (2) + sequencial (3).
    // Quando o OCR falha no CNPJ Estabelecimento ou no CNPJ Básico,
    // os 8 primeiros dígitos desse número recuperam o CNPJ matriz.
    const candidatos = [];

    const fontes = [
      /Informa[çc][õo]es\s+da\s+Apura[çc][aã]o\s*[:\-]?\s*([0-9OoIiLl|!SsBb\s\.\-]{14,28})/ig,
      /Gerado\s+na\s+apura[çc][aã]o\s*[:\-]?\s*([0-9OoIiLl|!SsBb\s\.\-]{14,28})/ig,
      /apuração\s*[:\-]?\s*([0-9OoIiLl|!SsBb\s\.\-]{14,28})/ig
    ];

    for(const rx of fontes){
      let m;
      while((m = rx.exec(t)) !== null){
        const n = somenteDigitosOCR(m[1]);
        if(n.length >= 14){
          candidatos.push(n);
        }
      }
    }

    // Também procura um número de apuração solto perto do texto "Informações da Apuração".
    const idx = t.search(/Informa[çc][õo]es\s+da\s+Apura[çc][aã]o/i);
    if(idx >= 0){
      const trecho = t.slice(idx, idx + 220);
      const digitos = somenteDigitosOCR(trecho);
      for(let i = 0; i <= digitos.length - 17; i++){
        candidatos.push(digitos.slice(i, i + 17));
      }
    }

    for(const n of candidatos){
      // Precisa conter ano/mês de apuração plausível logo após o CNPJ básico.
      // Ex.: base8 + 202604 + 001.
      for(let i = 0; i <= n.length - 17; i++){
        const cand = n.slice(i, i + 17);
        const base8 = cand.slice(0, 8);
        const ano = Number(cand.slice(8, 12));
        const mes = Number(cand.slice(12, 14));

        if(ano >= 2000 && ano <= 2099 && mes >= 1 && mes <= 12){
          const montado = montarCnpjMatrizPorBase8(base8);
          if(montado) return montado;
        }
      }
    }

    return "";
  }

  function cnpjPorBasico(){
    // Exemplo OCR:
    // CNPJ Básico: 53.290.808 Nome Empresarial: ...
    // Como os extratos enviados são matriz, completa com 0001 e calcula DV.
    const mBasico = t.match(/CNPJ\s+B[áa]sico\s*[:\-]?\s*([0-9OoIiLl|!SsBb\s\.\-\/]{8,40})/i);

    if(mBasico && mBasico[1]){
      const montado = montarCnpjMatrizPorBase8(mBasico[1]);
      if(montado) return montado;
    }

    return "";
  }

  // 1) Mais confiável: CNPJ Estabelecimento/Matriz/Número do CNPJ.
  const labelsCompletos = [
    /CNPJ\s+Estabelecimento\s*[:\-]?/i,
    /CNPJ\s+Matriz\s*[:\-]?/i,
    /N[úu]mero\s+do\s+CNPJ\s*[:\-]?/i,
    /C\.\s*N\.\s*P\.\s*J\.\s*[:\.]?/i,
    /CNPJ\s+da\s+Matriz\s*[:\-]?/i
  ];

  for(const label of labelsCompletos){
    const achado = cnpjPorTextoCompletoProximo(label);
    if(achado) return achado;
  }

  // 2) Depois tenta CNPJ Básico, antes de qualquer varredura global.
  // Isso evita pegar datas/horas como se fossem CNPJ.
  const peloBasico = cnpjPorBasico();
  if(peloBasico) return peloBasico;

  // 2.1) Fallback específico para OCR: usa o número da apuração.
  const pelaApuracao = cnpjPorNumeroApuracao();
  if(pelaApuracao) return pelaApuracao;

  // 3) Procura CNPJ completo com barra no PDF inteiro.
  // Ainda é seguro porque exige o padrão de CNPJ com /0001-XX.
  const regexCnpjComBarra = /[0-9OoIiLl|!SsBb]{2}\s*\.?\s*[0-9OoIiLl|!SsBb]{3}\s*\.?\s*[0-9OoIiLl|!SsBb]{3}\s*[/\\|]\s*[0-9OoIiLl|!SsBb]{4}\s*[-–—\s]?\s*[0-9OoIiLl|!SsBb]{2}/g;
  let m;
  while((m = regexCnpjComBarra.exec(t)) !== null){
    const n = somenteDigitosOCR(m[0]);
    if(validarCnpj(n)) return formatarCnpj(n);
  }

  // 4) Último fallback: sequência de 14 dígitos somente quando estiver
  // muito próxima de um rótulo de CNPJ. Nunca no texto inteiro puro.
  const labelsGerais = [
    /CNPJ\s*[:\.]?/ig,
    /C\.\s*N\.\s*P\.\s*J\.\s*[:\.]?/ig
  ];

  for(const rx of labelsGerais){
    let ml;
    while((ml = rx.exec(t)) !== null){
      const trecho = t.slice(ml.index, ml.index + 120);
      const digitos = somenteDigitosOCR(trecho);

      for(let i = 0; i <= digitos.length - 14; i++){
        const cand = digitos.slice(i, i + 14);
        if(validarCnpj(cand)) return formatarCnpj(cand);
      }
    }
  }

  return "Não encontrado";
}

function formatarCnpj(v){
  const n = somenteDigitosOCR(v);
  if(n.length === 14){
    return `${n.slice(0,2)}.${n.slice(2,5)}.${n.slice(5,8)}/${n.slice(8,12)}-${n.slice(12,14)}`;
  }
  return String(v || "").trim();
}

function extrairPA(txt){
  let m = txt.match(/Per[ií]odo\s+de\s+Apura[çc][aã]o\s*\(\s*PA\s*\)\s*[:\-]?\s*(\d{2}\s*\/\s*[12]\s*\d\s*\d\s*\d)/i);
  if(m && m[1]) return m[1].replace(/\s+/g, "").trim();

  m = txt.match(/Per[ií]odo\s+de\s+Apura[çc][aã]o\s*[:\-]?\s*\d{2}\s*\/\s*(\d{2})\s*\/\s*([12]\s*\d\s*\d\s*\d)\s*a\s*\d{2}\s*\/\s*\d{2}\s*\/\s*[12]\s*\d\s*\d\s*\d/i);
  if(m && m[1] && m[2]) return `${m[1]}/${m[2].replace(/\s+/g, "")}`;

  m = txt.match(/Per[ií]odo\s*[:\-]?\s*\d{2}\s*\/\s*(\d{2})\s*\/\s*([12]\s*\d\s*\d\s*\d)\s*a\s*\d{2}\s*\/\s*\d{2}\s*\/\s*[12]\s*\d\s*\d\s*\d/i);
  if(m && m[1] && m[2]) return `${m[1]}/${m[2].replace(/\s+/g, "")}`;

  m = txt.match(/Per[ií]odo\s+de\s+Apura[çc][aã]o\s*[:\-]?\s*(\d{2}\s*\/\s*[12]\s*\d\s*\d\s*\d)/i);
  if(m && m[1]) return m[1].replace(/\s+/g, "").trim();

  return "Não encontrado";
}

function extrairValoresMoeda(trecho){
  const valores = [];
  const moeda = /[0-9OoIiLl|!SsBb]{1,3}(?:[\.\s]*[0-9OoIiLl|!SsBb]{3})*,\s*[0-9OoIiLl|!SsBb]{1,2}|[0-9OoIiLl|!SsBb]+,\s*[0-9OoIiLl|!SsBb]{1,2}/g;
  let m;

  while((m = moeda.exec(String(trecho || ""))) !== null){
    const valor = limparValorOCR(m[0]);
    valores.push({texto:valor, numero:moedaParaNumero(valor)});
  }

  return valores;
}

function maiorValorMoeda(trecho){
  const valores = extrairValoresMoeda(trecho).filter(v => v.numero >= 0);
  if(!valores.length) return "Não encontrado";
  valores.sort((a, b) => b.numero - a.numero);
  return valores[0].texto;
}

function extrairRPA(txt){
  const t = String(txt || "");
  const linhas = t.split(/\n+/);
  const grupos = new Map();

  function adicionar(valorObj, score){
    if(!valorObj || !Number.isFinite(valorObj.numero)) return;
    if(valorObj.numero < 0 || valorObj.numero > 1000000000) return;

    const chave = Math.round(valorObj.numero * 100);
    const atual = grupos.get(chave) || {
      valor: numeroParaMoeda(valorObj.numero),
      numero: valorObj.numero,
      score: 0,
      ocorrencias: 0
    };

    atual.score += score;
    atual.ocorrencias++;
    grupos.set(chave, atual);
  }

  for(let i = 0; i < linhas.length; i++){
    const linha = linhas[i];

    if(/Receita\s+Bruta\s+do\s+PA\s*\(\s*RPA\s*\)/i.test(linha)){
      // Não junta automaticamente a próxima linha, pois ela normalmente é o RBT12.
      // Primeiro usa somente a linha da RPA; a próxima linha entra apenas se a atual
      // não contiver nenhum valor monetário.
      let valores = extrairValoresMoeda(linha);
      if(!valores.length){
        valores = extrairValoresMoeda(linhas[i + 1] || "");
      }

      if(valores.length){
        // A última moeda é a coluna TOTAL.
        const ultimo = valores[valores.length - 1];
        let score = 30;

        if(valores.length >= 2){
          const primeiro = valores[0];
          if(Math.abs(primeiro.numero - ultimo.numero) <= 0.01) score += 25;
          if(valores.some(v => Math.abs(v.numero) <= 0.005)) score += 5;
        }

        adicionar(ultimo, score);
      }
    }
  }

  // Fallback quando o OCR quebra o rótulo e os valores em linhas diferentes.
  const rx = /Receita\s+Bruta\s+do\s+PA\s*\(\s*RPA\s*\)/ig;
  let m;
  while((m = rx.exec(t)) !== null){
    let trecho = t.slice(m.index, m.index + 260);
    const corte = trecho.search(/Receita\s+bruta\s+acumulada|RBT12|2\s*[\.)]?\s*2\s*[\.)]?/i);
    if(corte > 20) trecho = trecho.slice(0, corte);

    const valores = extrairValoresMoeda(trecho);
    if(valores.length){
      adicionar(valores[valores.length - 1], 12);
    }
  }

  if(grupos.size){
    const candidatos = [...grupos.values()];
    candidatos.sort((a, b) =>
      (b.score - a.score) ||
      (b.ocorrencias - a.ocorrencias) ||
      (a.numero - b.numero)
    );
    return candidatos[0].valor;
  }

  // Modelo Apuração resumido.
  const ma = t.match(/TOTAL\s+RECEITA\s+E\s+SIMPLES\s+NACIONAL\s+NO\s+PER[ÍI]ODO\s*[:\-]?\s*([0-9OoIl|SsBb\.\s]+,\s*[0-9OoIiLl|!SsBb]{1,2})/i);
  if(ma && ma[1]) return limparValorOCR(ma[1]);

  return "Não encontrado";
}

function extrairValorDas(txt){
  const t = String(txt || "");
  const linhas = t.split(/\n+/);
  const grupos = new Map();

  function adicionar(valorObj, score){
    if(!valorObj || !Number.isFinite(valorObj.numero)) return;
    if(valorObj.numero <= 0 || valorObj.numero > 1000000000) return;

    const chave = Math.round(valorObj.numero * 100);
    const atual = grupos.get(chave) || {
      valor: numeroParaMoeda(valorObj.numero),
      numero: valorObj.numero,
      score: 0,
      ocorrencias: 0
    };

    atual.score += score;
    atual.ocorrencias++;
    grupos.set(chave, atual);
  }

  for(let i = 0; i < linhas.length; i++){
    const linha = linhas[i];
    const contextoAnterior = [
      linhas[i - 3] || "",
      linhas[i - 2] || "",
      linhas[i - 1] || ""
    ].join(" ");

    const valores = extrairValoresMoeda(linha);

    // Linha oficial do DAS: Principal ... Total ...
    if(/Principal/i.test(linha) && /\bTotal\b/i.test(linha) && valores.length){
      const primeiro = valores[0];
      const ultimo = valores[valores.length - 1];
      let score = 60;

      if(Math.abs(primeiro.numero - ultimo.numero) <= 0.01) score += 40;
      adicionar(ultimo, score);
      adicionar(primeiro, 25);
      continue;
    }

    // Linhas de totais tributários geralmente têm 8/9 tributos e o total no final.
    if(valores.length >= 5){
      const ultimo = valores[valores.length - 1];
      let score = 12;

      if(/Total\s+do\s+D[ée]bito\s+(?:Declarado|Exig[íi]vel)/i.test(contextoAnterior)) score += 18;
      if(/IRPJ/i.test(contextoAnterior) && /ISS/i.test(contextoAnterior) && /Total/i.test(contextoAnterior)) score += 10;

      adicionar(ultimo, score);
    }

    // Total explícito em uma linha curta.
    if(/\bTotal\b/i.test(linha) && valores.length){
      adicionar(valores[valores.length - 1], 8);
    }
  }

  // Fallback em janela da seção "DAS Gerado".
  const idxDas = t.search(/Informa[çc][õo]es\s+sobre\s+DAS\s+Gerado/i);
  if(idxDas >= 0){
    const trecho = t.slice(idxDas, idxDas + 1600);
    const valores = extrairValoresMoeda(trecho);
    if(valores.length){
      // Valores repetidos recebem força por consenso.
      valores.forEach(v => adicionar(v, 1));
    }
  }

  if(grupos.size){
    const candidatos = [...grupos.values()];
    candidatos.sort((a, b) =>
      (b.score - a.score) ||
      (b.ocorrencias - a.ocorrencias) ||
      (b.numero - a.numero)
    );
    return candidatos[0].valor;
  }

  // Modelo Apuração.
  const m = t.match(/TOTAL\s+RECEITA\s+E\s+SIMPLES\s+NACIONAL\s+NO\s+PER[ÍI]ODO\s*[:\-]?\s*[0-9OoIlISB\.\s]+,\s*[0-9OoIlISB]{1,2}\s+([0-9OoIlISB\.\s]+,\s*[0-9OoIlISB]{1,2})/i);
  if(m && m[1]) return limparValorOCR(m[1]);

  return "Não encontrado";
}

function extrairRBT12(txt){
  const t = String(txt || "");
  const linhas = t.split(/\n+/);
  const candidatos = [];

  function adicionar(trecho, score){
    if(/RBT12p|proporcionalizada/i.test(trecho)) return;
    const valores = extrairValoresMoeda(trecho).filter(v => v.numero >= 0);
    if(!valores.length) return;
    const ultimo = valores[valores.length - 1];
    if(ultimo.numero >= 0) candidatos.push({valor: ultimo.texto, numero: ultimo.numero, score});
  }

  for(let i = 0; i < linhas.length; i++){
    const linha = linhas[i];

    if(/\(\s*RBT12\s*\)/i.test(linha) && !/RBT12p/i.test(linha)){
      adicionar([linhas[i - 2] || "", linhas[i - 1] || "", linha].join(" "), 12);
    }

    if(/doze\s+meses\s+anteriores\s+ao\s+PA/i.test(linha) && !/proporcionalizada|RBT12p/i.test(linha)){
      adicionar(linha + " " + (linhas[i + 1] || ""), 10);
    }

    if(/Receita\s+Bruta\s+Acumulada\s+dos\s+[úu]ltimos\s+12\s+meses/i.test(linha)){
      adicionar(linha, 9);
    }
  }

  if(candidatos.length){
    candidatos.sort((a, b) => (b.score - a.score) || (b.numero - a.numero));
    return candidatos[0].valor;
  }

  return "Não encontrado";
}

function extrairFolha12(txt){
  const t = String(txt || "");

  let m = t.match(/Folha\s+de\s+Sal[áa]rios\s+e\s+Encargos\s+dos\s+[úu]ltimos\s+12\s+meses\s*[:\-]?\s*([0-9OoIlISB\.\s]+,\s*[0-9OoIlISB]{1,2})/i);
  if(m && m[1]) return limparValorOCR(m[1]);

  m = t.match(/Total\s+de\s+Folhas\s+de\s+Sal[áa]rios\s+Anteriores.*?R\$\s*([0-9OoIlISB\.\s]+,\s*[0-9OoIlISB]{1,2})/i);
  if(m && m[1]) return limparValorOCR(m[1]);

  return "Não encontrado";
}

function corrigirMesAnoOCR(rawMes, rawAno){
  let mes = somenteDigitosOCR(rawMes).slice(-2).padStart(2, "0");
  let ano = somenteDigitosOCR(rawAno);

  // OCR às vezes lê 2026 como 2O26, 20Z6 ou com espaços.
  if(ano.length > 4) ano = ano.slice(0, 4);

  if(ano.length !== 4 && /[12]/.test(String(rawAno || ""))){
    ano = String(rawAno || "")
      .replace(/[Oo]/g, "0")
      .replace(/[IiLl|!]/g, "1")
      .replace(/[Ss]/g, "5")
      .replace(/[Bb]/g, "8")
      .replace(/[^0-9]/g, "")
      .slice(0, 4);
  }

  const nMes = Number(mes);
  const nAno = Number(ano);

  if(!nAno || ano.length !== 4 || nAno < 2000 || nAno > 2099) return "";
  if(!nMes || nMes < 1 || nMes > 12) return "";

  return `${ano}-${mes}`;
}

function extrairParesMesValor(bloco){
  const pares = [];
  const mapa = new Map();

  if(!bloco) return pares;

  const b = String(bloco || "")
    // junta anos com espaços do OCR: 2 026 => 2026 / 2 O26 => 2026
    .replace(/([0-9OoIiLl|!SsBb]{2})\s*\/\s*([12IiLl|!])\s*([0-9OoIiLl|!SsBb])\s*([0-9OoIiLl|!SsBb])\s*([0-9OoIiLl|!SsBb])/g, "$1/$2$3$4$5")
    // alguns OCRs separam os milhares: 6 . 794,84
    .replace(/([0-9OoIiLl|!SsBb])\s+\.\s+([0-9OoIiLl|!SsBb])/g, "$1.$2")
    // alguns OCRs grudam mês/valor com barra ou colchete
    .replace(/[\[\]\|]/g, " ")
    .replace(/\s+/g, " ");

  // Valor com 1 ou 2 casas decimais. Não deixa o regex atravessar para o próximo mês.
  const moeda = "[0-9OoIiLl|!SsBb]{1,3}(?:[\\.\\s]*[0-9OoIiLl|!SsBb]{3})*,\\s*[0-9OoIiLl|!SsBb]{1,2}|[0-9OoIiLl|!SsBb]+,\\s*[0-9OoIiLl|!SsBb]{1,2}";

  // v6: mês/ano tolerante a OCR.
  // Ex.: 12/2025 pode virar I2/2025, l2/2025 ou |2/2025.
  //      01/2026 pode virar O1/2026 ou 0I/2026.
  const mesOCR = "[0-9OoIiLl|!SsBb]{1,2}";
  const anoOCR = "[12IiLl|!][0-9OoIl|SsBb]{3}";
  const regex = new RegExp("(" + mesOCR + ")\\s*\\/\\s*(" + anoOCR + ")\\s+(" + moeda + ")", "g");
  let m;

  while((m = regex.exec(b)) !== null){
    const chave = corrigirMesAnoOCR(m[1], m[2]);
    const valor = limparValorOCR(m[3]);

    if(/^\d{4}-\d{2}$/.test(chave) && moedaParaNumero(valor) >= 0){
      // Quando o bloco de receitas vem junto com Mercado Externo, preserva o primeiro valor não zerado.
      if(!mapa.has(chave) || (moedaParaNumero(mapa.get(chave)) === 0 && moedaParaNumero(valor) > 0)){
        mapa.set(chave, valor);
      }
    }
  }

  for(const [mes, valor] of mapa.entries()){
    pares.push({mes, valor});
  }

  return pares;
}

function adicionarReceitaSemDuplicar(lista, mes, valor){
  if(!mes || !valor || mes === "Não encontrado" || valor === "Não encontrado") return;

  const idx = lista.findIndex(item => item.mes === mes);
  if(idx >= 0){
    lista[idx].valor = limparValorOCR(valor);
  }else{
    lista.push({mes, valor:limparValorOCR(valor)});
  }
}

function mapaMesValor(lista){
  const map = new Map();
  (lista || []).forEach(item => {
    if(item && item.mes){
      map.set(item.mes, limparValorOCR(item.valor));
    }
  });
  return map;
}


function somarReceitasMercados(receitasInterno, receitasExterno){
  // Mantém o formato antigo do importador (um único campo "receita"),
  // apenas somando MI + ME no mesmo mês. Assim o backend não precisa mudar.
  const mapa = new Map();

  function acumular(lista){
    (lista || []).forEach(item => {
      if(!item || !/^\d{4}-\d{2}$/.test(String(item.mes || ""))) return;
      const atual = mapa.get(item.mes) || 0;
      mapa.set(item.mes, atual + moedaParaNumero(item.valor));
    });
  }

  acumular(receitasInterno);
  acumular(receitasExterno);

  return [...mapa.entries()]
    .map(([mes, numero]) => ({mes, valor: numeroParaMoeda(numero)}))
    .sort((a, b) => a.mes.localeCompare(b.mes));
}

function extrairRBT12PorMercado(txt){
  // Na linha oficial do PGDAS a ordem é:
  // Mercado Interno | Mercado Externo | Total.
  const t = normalizarTextoCompacto(txt);
  // O PDF pesquisável pode posicionar as três células numéricas antes de
  // "ao PA (RBT12)". Por isso ancoramos no início do rótulo e lemos as
  // três primeiras moedas até o próximo indicador.
  const rx = /Receita\s+bruta\s+acumulada\s+nos\s+doze\s+meses\s+anteriores/i;
  const m = rx.exec(t);
  if(!m) return null;

  let trecho = t.slice(m.index + m[0].length, m.index + m[0].length + 320);
  const corte = trecho.search(/Receita\s+bruta\s+acumulada\s+nos\s+doze\s+meses\s+anteriores\s+ao\s+PA\s+proporcionalizada|RBT12p|Receita\s+bruta\s+acumulada\s+no\s+ano-calend[áa]rio/i);
  if(corte >= 0) trecho = trecho.slice(0, corte);

  const valores = extrairValoresMoeda(trecho);
  if(valores.length < 3) return null;

  return {
    interno: valores[0].texto,
    externo: valores[1].texto,
    total: valores[2].texto
  };
}



function separarLeiturasOCR(paginas){
  const leituras = [];

  (paginas || []).forEach((pagina, indicePagina) => {
    const partes = String(pagina || "").split(/\n\s*---\s*OCR\s+(?:LEITURA|REFORCADO)\s*---\s*\n/i);

    partes.forEach((parte, indiceLeitura) => {
      const textoLinhas = corrigirOCR(normalizarTextoLinhas(parte));
      if(!textoLinhas) return;

      leituras.push({
        pagina: indicePagina + 1,
        ordem: indiceLeitura,
        textoLinhas,
        textoCompacto: corrigirOCR(normalizarTextoCompacto(textoLinhas))
      });
    });
  });

  return leituras;
}


function escolherValorPorConsensoDasLeituras(leituras, extrator){
  const grupos = new Map();

  for(const leitura of (leituras || [])){
    const valorTxt = extrator(leitura.textoLinhas);
    if(!valorTxt || valorTxt === "Não encontrado") continue;

    const numero = moedaParaNumero(valorTxt);
    if(!Number.isFinite(numero) || numero < 0) continue;

    const chave = Math.round(numero * 100);
    const atual = grupos.get(chave) || {
      valor: numeroParaMoeda(numero),
      numero,
      score: 0,
      ocorrencias: 0
    };

    // A primeira leitura de cada página é PSM 4, normalmente a mais fiel para tabelas.
    atual.score += leitura.ordem === 0 ? 10 : (leitura.ordem === 1 ? 4 : 2);
    atual.ocorrencias++;
    grupos.set(chave, atual);
  }

  if(!grupos.size) return "Não encontrado";

  const candidatos = [...grupos.values()];
  candidatos.sort((a, b) =>
    (b.score - a.score) ||
    (b.ocorrencias - a.ocorrencias) ||
    (a.numero - b.numero)
  );

  return candidatos[0].valor;
}

function selecionarNomeDasLeituras(leituras, fileName){
  const candidatos = [];

  for(const leitura of (leituras || [])){
    const nome = extrairNome(leitura.textoLinhas, "");
    if(!nome || nome === "Não encontrado") continue;

    const palavras = nome.split(/\s+/).filter(Boolean).length;
    let score = (leitura.ordem === 0 ? 20 : 8) + (palavras * 6) + Math.min(nome.length, 120);

    if(/\b(?:LTDA|EIRELI|S\/A|SA|ME|EPP)\b/i.test(nome)) score += 15;
    candidatos.push({nome, score, palavras});
  }

  if(candidatos.length){
    candidatos.sort((a, b) =>
      (b.score - a.score) ||
      (b.palavras - a.palavras) ||
      (b.nome.length - a.nome.length)
    );
    return candidatos[0].nome;
  }

  return limparNomeArquivo(fileName) || "Não encontrado";
}

function selecionarTextoSimplesDasLeituras(leituras, extrator, validador = null){
  const candidatos = [];

  for(const leitura of (leituras || [])){
    const valor = extrator(leitura.textoLinhas);
    if(!valor || valor === "Não encontrado") continue;
    if(validador && !validador(valor)) continue;

    candidatos.push({
      valor,
      score: leitura.ordem === 0 ? 10 : (leitura.ordem === 1 ? 4 : 2)
    });
  }

  if(!candidatos.length) return "Não encontrado";

  // Valores textuais iguais acumulam score.
  const grupos = new Map();
  for(const item of candidatos){
    const chave = String(item.valor).trim();
    const atual = grupos.get(chave) || {valor:chave, score:0, ocorrencias:0};
    atual.score += item.score;
    atual.ocorrencias++;
    grupos.set(chave, atual);
  }

  return [...grupos.values()]
    .sort((a, b) => (b.score - a.score) || (b.ocorrencias - a.ocorrencias))[0]
    .valor;
}

function extrairRBA(txt){
  const t = String(txt || "");
  const linhas = t.split(/\n+/);
  const candidatos = [];

  function adicionar(trecho, score){
    if(/RBAA|ano-calend[áa]rio\s+anterior/i.test(trecho)) return false;
    const valores = extrairValoresMoeda(trecho);
    if(!valores.length) return false;

    const ultimo = valores[valores.length - 1];
    if(ultimo.numero >= 0){
      candidatos.push({valor:ultimo.texto, numero:ultimo.numero, score});
      return true;
    }

    return false;
  }

  for(let i = 0; i < linhas.length; i++){
    const linha = linhas[i];

    if(/ano-calend[áa]rio\s+corrente\s*\(\s*RBA\s*\)/i.test(linha)){
      // Usa primeiro somente a linha atual. A próxima costuma ser a RBAA.
      if(!adicionar(linha, 20)){
        adicionar(linhas[i + 1] || "", 10);
      }
    }else if(/ano-calend[áa]rio\s+corrente/i.test(linha)){
      if(!adicionar(linha, 12)){
        adicionar(linhas[i + 1] || "", 6);
      }
    }
  }

  if(!candidatos.length) return "Não encontrado";

  candidatos.sort((a, b) => (b.score - a.score) || (a.numero - b.numero));
  return candidatos[0].valor;
}

function calcularRpaPelaRBA(pa, rba, receitas){
  const paAAAAMM = converterMesAno(pa);
  if(!/^\d{4}-\d{2}$/.test(paAAAAMM)) return null;
  if(!rba || rba === "Não encontrado") return null;

  const [ano, mes] = paAAAAMM.split("-").map(Number);
  const mapa = mapaMesValor(receitas || []);
  let somaAnterior = 0;

  for(let m = 1; m < mes; m++){
    const chave = `${ano}-${String(m).padStart(2, "0")}`;

    // Só calcula quando todos os meses anteriores do mesmo ano foram lidos,
    // inclusive os meses com valor zero.
    if(!mapa.has(chave)) return null;
    somaAnterior += moedaParaNumero(mapa.get(chave));
  }

  const acumuladoAno = moedaParaNumero(rba);
  const calculado = acumuladoAno - somaAnterior;

  if(!Number.isFinite(calculado) || calculado < -0.01 || calculado > 1000000000){
    return null;
  }

  return Math.max(0, calculado);
}

function extrairReceitaInformadaPA(txt){
  const t = String(txt || "");
  const linhas = t.split(/\n+/);
  const candidatos = [];

  function adicionarDaLinha(linha, score){
    const valores = extrairValoresMoeda(linha);
    if(!valores.length) return;

    // Estes rótulos possuem apenas o valor da receita do PA.
    // Se o OCR repetir a moeda, mantém o maior valor plausível da linha.
    const positivos = valores.filter(v => v.numero > 0);
    const escolhido = positivos.length
      ? positivos.sort((a, b) => b.numero - a.numero)[0]
      : valores[valores.length - 1];

    candidatos.push({
      valor: escolhido.texto,
      numero: escolhido.numero,
      score
    });
  }

  for(let i = 0; i < linhas.length; i++){
    const linha = linhas[i];

    if(/Receita\s+Bruta\s+Informada/i.test(linha)){
      adicionarDaLinha(linha, 35);
      continue;
    }

    if(/Valor\s+Informado\s*:/i.test(linha)){
      adicionarDaLinha(linha, 30);
      continue;
    }

    if(/Parcela\s*1\s*:/i.test(linha)){
      adicionarDaLinha(linha, 25);
    }
  }

  if(!candidatos.length) return "Não encontrado";

  const grupos = new Map();
  for(const item of candidatos){
    if(!Number.isFinite(item.numero) || item.numero < 0 || item.numero > 1000000000) continue;

    const chave = Math.round(item.numero * 100);
    const atual = grupos.get(chave) || {
      valor: numeroParaMoeda(item.numero),
      numero: item.numero,
      score: 0,
      ocorrencias: 0
    };

    atual.score += item.score;
    atual.ocorrencias++;
    grupos.set(chave, atual);
  }

  if(!grupos.size) return "Não encontrado";

  return [...grupos.values()]
    .sort((a, b) =>
      (b.score - a.score) ||
      (b.ocorrencias - a.ocorrencias) ||
      (b.numero - a.numero)
    )[0].valor;
}

function selecionarRpaDasLeituras(leituras, rpaCalculada = null, rbaLida = null){
  // v15: não aceita mais cegamente o valor calculado pela RBA.
  // O OCR pode ler a RBA de forma incompleta e produzir RPA = 0,00.
  // Agora são confrontadas três fontes independentes:
  // 1) linha Receita Bruta do PA (RPA);
  // 2) Receita Bruta Informada / Valor Informado / Parcela 1;
  // 3) cálculo RBA menos os meses anteriores do ano.
  const grupos = new Map();
  const rbaNumero = rbaLida && rbaLida !== "Não encontrado"
    ? moedaParaNumero(rbaLida)
    : null;

  function adicionar(valorTxt, score, origem){
    if(!valorTxt || valorTxt === "Não encontrado") return;

    const numero = moedaParaNumero(valorTxt);
    if(!Number.isFinite(numero) || numero < 0 || numero > 1000000000) return;

    const chave = Math.round(numero * 100);
    const atual = grupos.get(chave) || {
      valor: numeroParaMoeda(numero),
      numero,
      score: 0,
      ocorrencias: 0,
      origens: new Set()
    };

    atual.score += score;
    atual.ocorrencias++;
    atual.origens.add(origem);
    grupos.set(chave, atual);
  }

  for(const leitura of (leituras || [])){
    const pesoDireto = leitura.ordem === 0 ? 45 : (leitura.ordem === 1 ? 20 : 10);
    const pesoInformado = leitura.ordem === 0 ? 40 : (leitura.ordem === 1 ? 18 : 9);

    adicionar(extrairRPA(leitura.textoLinhas), pesoDireto, "rpa");
    adicionar(extrairReceitaInformadaPA(leitura.textoLinhas), pesoInformado, "informada");
  }

  if(Number.isFinite(rpaCalculada)){
    // Um zero calculado recebe peso menor, pois pode ser consequência de
    // uma RBA truncada. Quando as demais fontes também forem zero, os
    // pontos serão somados no mesmo candidato e ele continuará vencendo.
    adicionar(numeroParaMoeda(rpaCalculada), rpaCalculada > 0.005 ? 70 : 20, "rba");
  }

  if(!grupos.size) return "Não encontrado";

  const candidatos = [...grupos.values()].map(item => {
    let scoreFinal = item.score;

    // Confirmação entre fontes independentes.
    if(item.origens.size >= 2) scoreFinal += 35 * (item.origens.size - 1);
    scoreFinal += Math.min(item.ocorrencias, 5) * 3;

    // A RBA inclui o PA atual. Portanto, quando a RBA foi lida e é positiva,
    // um RPA superior à própria RBA é matematicamente impossível.
    if(Number.isFinite(rbaNumero) && rbaNumero > 0.005 && item.numero > rbaNumero + 0.01){
      scoreFinal -= 200;
    }

    return {...item, scoreFinal};
  });

  const existePositivoConfiavel = candidatos.some(item =>
    item.numero > 0.005 &&
    (item.origens.has("rpa") || item.origens.has("informada") || item.origens.size >= 2)
  );

  // Se existe leitura textual positiva, um zero isolado vindo apenas do
  // cálculo pela RBA não pode apagar a receita do próprio mês.
  if(existePositivoConfiavel){
    candidatos.forEach(item => {
      if(item.numero <= 0.005 && item.origens.size === 1 && item.origens.has("rba")){
        item.scoreFinal -= 100;
      }
    });
  }

  candidatos.sort((a, b) =>
    (b.scoreFinal - a.scoreFinal) ||
    (b.origens.size - a.origens.size) ||
    (b.ocorrencias - a.ocorrencias) ||
    (b.numero - a.numero)
  );

  return candidatos[0].valor;
}

function selecionarValorDasDasLeituras(leituras){
  return escolherValorPorConsensoDasLeituras(leituras, extrairValorDas);
}

function folhaDeclaradaComoNenhuma(txt){
  const t = String(txt || "");
  const rx = /Folha\s+de\s+Sal[áa]rios\s+Anteriores/ig;
  let m;

  while((m = rx.exec(t)) !== null){
    let janela = t.slice(m.index, m.index + 700);
    const fim = janela.search(/2\s*[\.)]?\s*4\s*[\.)]?\s*Fator|Fator\s*r|2\s*[\.)]?\s*5\s*[\.)]?\s*Valores\s+Fixos/i);
    if(fim > 0) janela = janela.slice(0, fim);
    if(/\bNenhuma\b/i.test(janela)) return true;
  }

  return false;
}

function somaMapaNosMeses(mapa, meses){
  return (meses || []).reduce((total, mes) => total + moedaParaNumero(mapa.get(mes) || "0,00"), 0);
}

function selecionarReceitasDasLeituras(leituras, pa, rbt12, extratorBloco = blocoReceitasMercadoInterno){
  const paAAAAMM = converterMesAno(pa);
  const mesesAnteriores = /^\d{4}-\d{2}$/.test(paAAAAMM)
    ? gerarMesesEsperados(somarMes(paAAAAMM, -1), 12)
    : [];

  const alvoConhecido = rbt12 && rbt12 !== "Não encontrado";
  const alvo = alvoConhecido ? moedaParaNumero(rbt12) : 0;
  const opcoes = [];

  for(const leitura of (leituras || [])){
    let bloco = extratorBloco(leitura.textoLinhas);
    if(!bloco) bloco = extratorBloco(leitura.textoCompacto);
    if(!bloco) continue;

    const pares = extrairParesMesValor(bloco);
    const mapa = mapaMesValor(pares);
    const qtdEsperados = mesesAnteriores.filter(mes => mapa.has(mes)).length;
    const soma = somaMapaNosMeses(mapa, mesesAnteriores);
    const diferenca = alvoConhecido ? Math.abs(soma - alvo) : 0;

    opcoes.push({mapa, pares, qtdEsperados, soma, diferenca, ordem: leitura.ordem});
  }

  if(!opcoes.length) return [];

  opcoes.sort((a, b) => {
    if(alvoConhecido && Math.abs(a.diferenca - b.diferenca) > 0.009){
      return a.diferenca - b.diferenca;
    }
    return (b.qtdEsperados - a.qtdEsperados) || (a.ordem - b.ordem);
  });

  const escolhido = new Map(opcoes[0].mapa);

  // Completa meses ausentes usando as outras passadas do OCR.
  for(const mes of mesesAnteriores){
    if(escolhido.has(mes)) continue;
    for(const opcao of opcoes){
      if(opcao.mapa.has(mes)){
        escolhido.set(mes, opcao.mapa.get(mes));
        break;
      }
    }
  }

  // Se há RBT12, testa as alternativas mês a mês e mantém somente trocas
  // que aproximem a soma mensal do total oficial do extrato.
  if(alvoConhecido && mesesAnteriores.length){
    let somaAtual = somaMapaNosMeses(escolhido, mesesAnteriores);
    let melhorou = true;
    let voltas = 0;

    while(melhorou && voltas < 3){
      melhorou = false;
      voltas++;

      for(const mes of mesesAnteriores){
        const atual = moedaParaNumero(escolhido.get(mes) || "0,00");
        const alternativas = [];

        for(const opcao of opcoes){
          if(opcao.mapa.has(mes)){
            const valorTxt = opcao.mapa.get(mes);
            const valorNum = moedaParaNumero(valorTxt);
            if(!alternativas.some(a => Math.abs(a.numero - valorNum) < 0.005)){
              alternativas.push({texto: valorTxt, numero: valorNum});
            }
          }
        }

        let melhor = {texto: escolhido.get(mes) || "0,00", numero: atual};
        let melhorDiferenca = Math.abs(alvo - somaAtual);

        for(const alternativa of alternativas){
          const novaSoma = somaAtual - atual + alternativa.numero;
          const novaDiferenca = Math.abs(alvo - novaSoma);
          if(novaDiferenca + 0.009 < melhorDiferenca){
            melhor = alternativa;
            melhorDiferenca = novaDiferenca;
          }
        }

        if(Math.abs(melhor.numero - atual) > 0.005){
          escolhido.set(mes, melhor.texto);
          somaAtual = somaAtual - atual + melhor.numero;
          melhorou = true;
        }
      }
    }
  }

  return mesesAnteriores
    .filter(mes => escolhido.has(mes))
    .map(mes => ({mes, valor: escolhido.get(mes)}));
}

function selecionarFolhasDasLeituras(leituras, pa){
  const paAAAAMM = converterMesAno(pa);
  const mesesEsperados = /^\d{4}-\d{2}$/.test(paAAAAMM)
    ? gerarMesesEsperados(somarMes(paAAAAMM, -1), 12)
    : [];

  const opcoes = [];
  for(const leitura of (leituras || [])){
    let bloco = blocoFolhas(leitura.textoLinhas);
    if(!bloco) bloco = blocoFolhas(leitura.textoCompacto);
    if(!bloco || folhaDeclaradaComoNenhuma(bloco)) continue;

    const pares = extrairParesMesValor(bloco);
    const mapa = mapaMesValor(pares);
    const qtdEsperados = mesesEsperados.filter(mes => mapa.has(mes)).length;
    opcoes.push({mapa, qtdEsperados, ordem: leitura.ordem});
  }

  if(!opcoes.length) return [];
  opcoes.sort((a, b) => (b.qtdEsperados - a.qtdEsperados) || (a.ordem - b.ordem));

  const escolhido = new Map(opcoes[0].mapa);
  for(const mes of mesesEsperados){
    if(escolhido.has(mes)) continue;
    for(const opcao of opcoes){
      if(opcao.mapa.has(mes)){
        escolhido.set(mes, opcao.mapa.get(mes));
        break;
      }
    }
  }

  return mesesEsperados
    .filter(mes => escolhido.has(mes))
    .map(mes => ({mes, valor: escolhido.get(mes)}));
}

function completarFolhasPorJanelaAmpla(txtLinhas, txtCompacto, folhas, pa){
  // Fallback restrito ao bloco real da folha.
  // A versão anterior começava 250 caracteres antes do título e avançava 6.500,
  // o que podia capturar as receitas e copiá-las indevidamente para a folha.
  if(!pa || pa === "Não encontrado") return folhas || [];

  const paAAAAMM = converterMesAno(pa);
  if(!/^\d{4}-\d{2}$/.test(paAAAAMM)) return folhas || [];

  const mesesEsperados = gerarMesesEsperados(somarMes(paAAAAMM, -1), 12);
  const esperado = new Set(mesesEsperados);
  const mapa = mapaMesValor(folhas || []);
  const fontes = [String(txtLinhas || ""), String(txtCompacto || "")];

  for(const fonte of fontes){
    if(!fonte) continue;

    const padraoTitulo = /2\s*[\.)]?\s*3\s*[\.)]?\s*Folha\s+de\s+Sal[áa]rios\s+Anteriores|Folha\s+de\s+Sal[áa]rios\s+Anteriores/ig;
    let mt;

    while((mt = padraoTitulo.exec(fonte)) !== null){
      const inicio = mt.index;
      let janela = fonte.slice(inicio, inicio + 3200);
      const fim = janela.search(/2\s*[\.)]?\s*4\s*[\.)]?\s*Fator|Fator\s*r|2\s*[\.)]?\s*5\s*[\.)]?\s*Valores\s+Fixos|3\s*[\.)]?\s*Informa[çc][õo]es\s+dos\s+Estabelecimentos|CNPJ\s+Estabelecimento/i);
      if(fim > 0) janela = janela.slice(0, fim);

      // "Nenhuma" significa ausência total de folha; nunca procurar meses fora deste bloco.
      if(/\bNenhuma\b/i.test(janela)) return [];

      const encontrados = extrairParesMesValor(janela);
      for(const item of encontrados){
        if(!esperado.has(item.mes)) continue;

        const valorAtual = mapa.get(item.mes) || "0,00";
        const novoValor = limparValorOCR(item.valor);
        if(!mapa.has(item.mes) || (moedaParaNumero(valorAtual) === 0 && moedaParaNumero(novoValor) > 0)){
          mapa.set(item.mes, novoValor);
        }
      }
    }
  }

  return mesesEsperados
    .filter(mes => mapa.has(mes))
    .map(mes => ({mes, valor: mapa.get(mes)}));
}

// =====================================================
// PARSER PRINCIPAL
// Funciona para:
// 1) Extrato do Simples Nacional
// 2) Declaração PGDAS-D - Declaratório
// 3) Apuração do Simples Nacional
// 4) PDF escaneado/imagem com OCR
// =====================================================

function parseExtratoV9(paginas, fileName){
  const txtLinhas = corrigirOCR(normalizarTextoLinhas(paginas.join("\n")));
  const txt = corrigirOCR(normalizarTextoCompacto(txtLinhas));
  const leituras = separarLeiturasOCR(paginas);

  const r = {};

  r.tipoDocumento = detectarTipoPDF(txt);

  r.nome = selecionarNomeDasLeituras(leituras, fileName);
  if(r.nome === "Não encontrado"){
    r.nome = extrairNome(txtLinhas, fileName);
  }

  r.cnpj = selecionarTextoSimplesDasLeituras(
    leituras,
    extrairCnpj,
    valor => valor !== "Não encontrado" && validarCnpj(valor)
  );
  if(r.cnpj === "Não encontrado") r.cnpj = extrairCnpj(txt);

  r.pa = selecionarTextoSimplesDasLeituras(
    leituras,
    extrairPA,
    valor => /^\d{2}\/\d{4}$/.test(valor)
  );
  if(r.pa === "Não encontrado") r.pa = extrairPA(txt);

  r.rbt12 = escolherValorPorConsensoDasLeituras(leituras, extrairRBT12);
  if(r.rbt12 === "Não encontrado") r.rbt12 = extrairRBT12(txtLinhas);
  if(r.rbt12 === "Não encontrado") r.rbt12 = extrairRBT12(txt);

  const rbaAtual = escolherValorPorConsensoDasLeituras(leituras, extrairRBA);

  r.valorDas = selecionarValorDasDasLeituras(leituras);
  if(r.valorDas === "Não encontrado") r.valorDas = extrairValorDas(txtLinhas);
  if(r.valorDas === "Não encontrado") r.valorDas = extrairValorDas(txt);

  r.folha12 = extrairFolha12(txt);

  // -----------------------------------------------------
  // RECEITAS BRUTAS ANTERIORES - MERCADO INTERNO + EXTERNO
  // -----------------------------------------------------
  // v16: o leitor passa a importar o Mercado Externo exatamente no mesmo
  // padrão do Mercado Interno. O backend continua recebendo apenas "receita",
  // que agora corresponde ao TOTAL mensal (MI + ME), preservando compatibilidade.
  const rbt12Mercados = extrairRBT12PorMercado(txtLinhas) || extrairRBT12PorMercado(txt);
  if(rbt12Mercados && rbt12Mercados.total){
    r.rbt12 = rbt12Mercados.total;
  }

  const alvoInterno = rbt12Mercados && rbt12Mercados.interno
    ? rbt12Mercados.interno
    : r.rbt12;
  const alvoExterno = rbt12Mercados && rbt12Mercados.externo
    ? rbt12Mercados.externo
    : null;

  let receitasInterno = selecionarReceitasDasLeituras(
    leituras,
    r.pa,
    alvoInterno,
    blocoReceitasMercadoInterno
  );

  if(receitasInterno.length === 0){
    let blocoMI = blocoReceitasMercadoInterno(txtLinhas);
    if(!blocoMI) blocoMI = blocoReceitasMercadoInterno(txt);
    receitasInterno = extrairParesMesValor(blocoMI);
  }

  let receitasExterno = selecionarReceitasDasLeituras(
    leituras,
    r.pa,
    alvoExterno,
    blocoReceitasMercadoExterno
  );

  if(receitasExterno.length === 0){
    let blocoME = blocoReceitasMercadoExterno(txtLinhas);
    if(!blocoME) blocoME = blocoReceitasMercadoExterno(txt);
    receitasExterno = extrairParesMesValor(blocoME);
  }

  let receitas = somarReceitasMercados(receitasInterno, receitasExterno);

  // O RPA é validado pela RBA do ano corrente:
  // RPA = RBA - receitas de janeiro até o mês anterior.
  // Isso impede que células unidas pelo OCR criem valores milionários.
  const rpaCalculada = calcularRpaPelaRBA(r.pa, rbaAtual, receitas);
  r.rpa = selecionarRpaDasLeituras(leituras, rpaCalculada, rbaAtual);

  if(r.rpa === "Não encontrado"){
    r.rpa = extrairRPA(txtLinhas);
  }
  if(r.rpa === "Não encontrado"){
    r.rpa = extrairRPA(txt);
  }

  // Última proteção v15: em PDFs escaneados a linha principal pode perder
  // o valor, mas a página seguinte repete a receita em "Receita Bruta
  // Informada", "Valor Informado" e "Parcela 1".
  const rpaInformadaGeral = extrairReceitaInformadaPA(txtLinhas);
  if(
    rpaInformadaGeral !== "Não encontrado" &&
    moedaParaNumero(rpaInformadaGeral) > 0.005 &&
    (r.rpa === "Não encontrado" || moedaParaNumero(r.rpa) <= 0.005)
  ){
    r.rpa = rpaInformadaGeral;
  }

  // Inclui o mês atual do PA com o valor validado da RPA.
  if(r.pa && r.rpa && r.pa !== "Não encontrado" && r.rpa !== "Não encontrado"){
    adicionarReceitaSemDuplicar(receitas, converterMesAno(r.pa), r.rpa);
  }

  // -----------------------------------------------------
  // FOLHA DE SALÁRIOS ANTERIORES
  // -----------------------------------------------------
  const semFolhaDeclarada = leituras.some(leitura =>
    folhaDeclaradaComoNenhuma(leitura.textoLinhas) ||
    folhaDeclaradaComoNenhuma(leitura.textoCompacto)
  );

  let folhas = [];
  if(semFolhaDeclarada){
    r.folhaDeclaradaNenhuma = true;
  }else{
    folhas = selecionarFolhasDasLeituras(leituras, r.pa);

    if(folhas.length === 0){
      let blocoFolha = blocoFolhas(txtLinhas);
      if(!blocoFolha) blocoFolha = blocoFolhas(txt);
      folhas = extrairParesMesValor(blocoFolha);
    }

    if(folhas.length < 12){
      folhas = completarFolhasPorJanelaAmpla(txtLinhas, txt, folhas, r.pa);
    }
  }

  const mapReceitas = mapaMesValor(receitas);
  const mapFolhas = mapaMesValor(folhas);

  // -----------------------------------------------------
  // MONTA A TABELA FINAL
  // -----------------------------------------------------
  let tabela = [];

  if(r.pa !== "Não encontrado"){
    const paAAAAMM = converterMesAno(r.pa);
    const mesesEsperados = gerarMesesEsperados(paAAAAMM, 13);

    tabela = mesesEsperados.map(mes => {
      const mesFolhaAnterior = somarMes(mes, -1);
      return {
        mes,
        receita: mapReceitas.get(mes) || "0,00",
        folha: mapFolhas.get(mesFolhaAnterior) || "0,00"
      };
    });

    const mesesRbt12 = gerarMesesEsperados(somarMes(paAAAAMM, -1), 12);
    const totalMensalRbt12 = mesesRbt12.reduce(
      (total, mes) => total + moedaParaNumero(mapReceitas.get(mes) || "0,00"),
      0
    );

    if(r.rbt12 && r.rbt12 !== "Não encontrado"){
      const totalOficial = moedaParaNumero(r.rbt12);
      const diferenca = totalMensalRbt12 - totalOficial;
      r.validacaoReceitas = {
        ok: Math.abs(diferenca) <= 0.01,
        totalMensal: numeroParaMoeda(totalMensalRbt12),
        totalOficial: numeroParaMoeda(totalOficial),
        diferenca: numeroParaMoeda(diferenca)
      };
    }
  }

  if(tabela.length === 0 && r.pa !== "Não encontrado" && r.rpa !== "Não encontrado"){
    tabela = [{
      mes: converterMesAno(r.pa),
      receita: r.rpa,
      folha: "0,00"
    }];
    r.resumido = true;
  }

  r.tabela = tabela;
  r.debug = {
    qtdReceitas: receitas.length,
    qtdReceitasInterno: receitasInterno.length,
    qtdReceitasExterno: receitasExterno.length,
    qtdFolhas: folhas.length,
    folhaDeclaradaNenhuma: !!r.folhaDeclaradaNenhuma,
    validacaoRbt12: r.validacaoReceitas ? r.validacaoReceitas.ok : null,
    rpaCalculadaPelaRba: Number.isFinite(rpaCalculada),
    rbaLida: rbaAtual
  };

  return r;
}

// =====================================================
// PARSER LEGADO ORIGINAL - NÃO ALTERAR
// Usado somente quando o PDF tem texto pesquisável e é o
// modelo antigo que já funcionava no código enviado.
// =====================================================

function legacy_normalizarTexto(txt){
  return String(txt || "")
    .replace(/\u00a0/g, " ")
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function legacy_converterMesAno(str){
  const p = String(str || "").trim().split("/");
  return p.length === 2 ? `${p[1]}-${p[0]}` : str;
}

function legacy_somarMes(aaaamm, delta){
  const [a, m] = aaaamm.split("-").map(Number);
  const d = new Date(a, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function legacy_detectarTipoPDF(txt){
  if(/CNPJ\s+Matriz:/i.test(txt) || /Declarat[óo]rio/i.test(txt) || /N[ºo]\s+da\s+Declara[çc][aã]o/i.test(txt)){
    return "Declaração";
  }

  if(/Extrato\s+do\s+Simples\s+Nacional/i.test(txt)){
    return "Extrato";
  }

  return "PGDAS-D";
}

function legacy_blocoEntre(txt, inicioRegex, fimRegex){
  const inicio = txt.search(inicioRegex);

  if(inicio < 0){
    return "";
  }

  const sub = txt.slice(inicio);
  const fim = sub.search(fimRegex);

  return fim >= 0 ? sub.slice(0, fim) : sub;
}

function legacy_extrairPrimeiro(txt, regex, padrao = "Não encontrado"){
  const m = txt.match(regex);
  return m && m[1] ? m[1].trim() : padrao;
}

function legacy_extrairNome(txt){
  return legacy_extrairPrimeiro(
    txt,
    /Nome\s+Empresarial:\s*(.*?)(?:Data\s+de\s+Abertura:|Data\s+de\s+abertura\s+no\s+CNPJ:|Regime\s+de\s+Apura[çc][aã]o:|Optante\s+pelo\s+Simples|N[ºo]\s+da\s+Declara[çc][aã]o)/i
  );
}

function legacy_extrairCnpj(txt){
  const regexCnpjCompleto = [
    /CNPJ\s+Estabelecimento:\s*([0-9]{2}\.?[0-9]{3}\.?[0-9]{3}\/?[0-9]{4}-?[0-9]{2})/i,
    /CNPJ\s+Matriz:\s*([0-9]{2}\.?[0-9]{3}\.?[0-9]{3}\/?[0-9]{4}-?[0-9]{2})/i
  ];

  for(const regex of regexCnpjCompleto){
    const m = txt.match(regex);
    if(m && m[1]){
      return m[1].trim();
    }
  }

  const basico = txt.match(/CNPJ\s+B[áa]sico:\s*([0-9.\-\/]+)/i);

  if(basico && basico[1]){
    return basico[1].trim();
  }

  return "Não encontrado";
}

function legacy_extrairPA(txt){
  const paExtrato = txt.match(/Per[ií]odo\s+de\s+Apura[çc][aã]o\s*\(PA\):\s*(\d{2}\/\d{4})/i);

  if(paExtrato && paExtrato[1]){
    return paExtrato[1].trim();
  }

  const paDeclaracao = txt.match(/Per[ií]odo\s+de\s+Apura[çc][aã]o:\s*\d{2}\/(\d{2})\/(\d{4})\s*a\s*\d{2}\/\d{2}\/\d{4}/i);

  if(paDeclaracao && paDeclaracao[1] && paDeclaracao[2]){
    return `${paDeclaracao[1]}/${paDeclaracao[2]}`;
  }

  const paGenerico = txt.match(/Per[ií]odo\s+de\s+Apura[çc][aã]o:\s*(\d{2}\/\d{4})/i);

  if(paGenerico && paGenerico[1]){
    return paGenerico[1].trim();
  }

  return "Não encontrado";
}

function legacy_extrairRPA(txt){
  // v16: a linha oficial possui Mercado Interno | Mercado Externo | Total.
  // O importador precisa guardar a receita TOTAL do mês para formar o RBT12.
  // Antes o parser legado pegava sempre a primeira coluna (Mercado Interno),
  // fazendo uma exportadora pura virar RPA = 0,00.
  let rpa = txt.match(/Receita\s+Bruta\s+do\s+PA\s*\(RPA\)\s*-\s*Compet[êe]ncia\s+([0-9.]+,\d{2})\s+([0-9.]+,\d{2})\s+([0-9.]+,\d{2})/i);

  if(rpa && rpa[3]){
    return rpa[3].trim();
  }

  rpa = txt.match(/Receita\s+Bruta\s+do\s+PA\s*\(RPA\)\s*-\s*Compet[êe]ncia\s+([0-9.]+,\d{2})/i);

  if(rpa && rpa[1]){
    return rpa[1].trim();
  }

  rpa = txt.match(/Receita\s+Bruta\s+do\s+PA\s*\(RPA\).*?([0-9.]+,\d{2})/i);

  if(rpa && rpa[1]){
    return rpa[1].trim();
  }

  return "Não encontrado";
}

function legacy_extrairParesMesValor(bloco){
  const pares = [];

  if(!bloco){
    return pares;
  }

  const regex = /(\d{2}\/\d{4})\s+([0-9.]+,\d{2})/g;
  let m;

  while((m = regex.exec(bloco)) !== null){
    pares.push({
      mes: legacy_converterMesAno(m[1]),
      valor: m[2]
    });
  }

  return pares;
}

function legacy_adicionarReceitaSemDuplicar(lista, mes, valor){
  if(!mes || !valor || mes === "Não encontrado" || valor === "Não encontrado"){
    return;
  }

  const jaExiste = lista.some(item => item.mes === mes);

  if(!jaExiste){
    lista.unshift({
      mes,
      valor
    });
  }
}

function deveUsarParserLegado(paginas, txtLinhas){
  if(paginas && paginas.usouOCR){
    return false;
  }

  const txt = legacy_normalizarTexto(txtLinhas || "");

  if(txt.length < 500){
    return false;
  }

  // Esse é o formato que já funcionava no código antigo:
  // PDF com texto pesquisável + itens 2.2.1) e 2.2.2) exatamente no texto.
  const extratoClassico =
    /Extrato\s+do\s+Simples\s+Nacional/i.test(txt) &&
    /2\.2\)\s*Receitas\s+Brutas\s+Anteriores/i.test(txt) &&
    /2\.2\.1\)\s*Mercado\s+Interno/i.test(txt) &&
    /2\.2\.2\)\s*Mercado\s+Externo/i.test(txt);

  const declaracaoClassica =
    /CNPJ\s+Matriz:/i.test(txt) &&
    /2\.2\.1\)\s*Mercado\s+Interno/i.test(txt);

  return extratoClassico || declaracaoClassica;
}

function parseExtratoLegado(paginas){
  const txt = legacy_normalizarTexto(paginas.join("\n"));

  const r = {};

  r.tipoDocumento = legacy_detectarTipoPDF(txt);
  r.nome = legacy_extrairNome(txt);
  r.cnpj = legacy_extrairCnpj(txt);
  r.pa = legacy_extrairPA(txt);
  r.rpa = legacy_extrairRPA(txt);
  const rbt12Mercados = extrairRBT12PorMercado(txt);
  r.rbt12 = rbt12Mercados && rbt12Mercados.total ? rbt12Mercados.total : extrairRBT12(txt);
  r.folha12 = "Não encontrado";
  r.valorDas = "Não encontrado";
  r.modoParser = "legado_original";

  const blocoMI = legacy_blocoEntre(
    txt,
    /2\.2\.1\)\s*Mercado\s+Interno/i,
    /2\.2\.2\)\s*Mercado\s+Externo/i
  );

  const receitasInterno = legacy_extrairParesMesValor(blocoMI);

  const blocoME = legacy_blocoEntre(
    txt,
    /2\.2\.2\)\s*Mercado\s+Externo/i,
    /2\.3\)\s*Folha\s+de\s+Sal[áa]rios\s+Anteriores|2\.4\)\s*Fator/i
  );

  const receitasExterno = legacy_extrairParesMesValor(blocoME);
  let receitas = somarReceitasMercados(receitasInterno, receitasExterno);

  if(r.pa && r.rpa && r.pa !== "Não encontrado" && r.rpa !== "Não encontrado"){
    adicionarReceitaSemDuplicar(receitas, legacy_converterMesAno(r.pa), r.rpa);
  }

  receitas.sort((a, b) => b.mes.localeCompare(a.mes));

  const blocoFolha = legacy_blocoEntre(
    txt,
    /2\.3\)\s*Folha\s+de\s+Sal[áa]rios\s+Anteriores/i,
    /2\.3\.1\)|2\.4\)\s*Fator/i
  );

  let folhas = legacy_extrairParesMesValor(blocoFolha);

  folhas.sort((a, b) => b.mes.localeCompare(a.mes));

  const tabela = receitas.map(itemReceita => {
    const mesFolhaAnterior = legacy_somarMes(itemReceita.mes, -1);
    const folha = folhas.find(f => f.mes === mesFolhaAnterior);

    return {
      mes: itemReceita.mes,
      receita: itemReceita.valor,
      folha: folha ? folha.valor : "0,00"
    };
  });

  r.tabela = tabela.slice(0, 13);
  r.debug = {
    qtdReceitas: receitas.length,
    qtdReceitasInterno: receitasInterno.length,
    qtdReceitasExterno: receitasExterno.length,
    qtdFolhas: folhas.length,
    modo: "legado_original_v16"
  };

  return r;
}

function resultadoLegadoValido(d){
  return !!(
    d &&
    Array.isArray(d.tabela) &&
    d.tabela.length > 0 &&
    d.cnpj && d.cnpj !== "Não encontrado" &&
    d.nome && d.nome !== "Não encontrado" &&
    d.pa && d.pa !== "Não encontrado"
  );
}

function parseExtrato(paginas, fileName){
  const txtLinhas = normalizarTextoLinhas(paginas.join("\n"));

  if(deveUsarParserLegado(paginas, txtLinhas)){
    try{
      const legado = parseExtratoLegado(paginas);
      if(resultadoLegadoValido(legado)){
        return legado;
      }
    }catch(e){
      console.warn("Parser legado falhou; usando parser reforçado:", e);
    }
  }

  return parseExtratoV9(paginas, fileName);
}

// =====================================================
// DRAG & DROP
// =====================================================

const dropzone = document.getElementById("dropzone");
const input = document.getElementById("fileInput");
const progress = document.getElementById("progress");
const resultado = document.getElementById("resultado");
const btnImportar = document.getElementById("btnImportar");
const statusDiv = document.getElementById("status");

dropzone.addEventListener("click", () => input.click());

dropzone.addEventListener("dragover", e => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", async e => {
  e.preventDefault();
  dropzone.classList.remove("dragover");

  const file = e.dataTransfer.files[0];
  if(file) await processarArquivo(file);
});

input.addEventListener("change", async e => {
  const file = e.target.files[0];
  if(file) await processarArquivo(file);
  input.value = "";
});

// =====================================================
// PROCESSAR ARQUIVO
// =====================================================

async function processarArquivo(file){
  try{
    progress.style.width = "0%";
    resultado.innerHTML = "";
    statusDiv.innerHTML = ""; statusDiv.style.display = "none"; statusDiv.className = "aviso";
    btnImportar.style.display = "none";
    btnImportar.disabled = true;
    btnImportar.dataset.json = "";

    dropzone.innerHTML = "🔄 Lendo PDF, aguarde...";

    const paginas = await extrairTextoPDF(file);
    const d = parseExtrato(paginas, file.name);

    if(!d.tabela || d.tabela.length === 0){
      throw new Error("Não foi possível localizar a tabela de receitas no PDF.");
    }

    if(d.cnpj === "Não encontrado"){
      throw new Error("Não foi possível localizar o CNPJ completo no PDF.");
    }

    if(d.nome === "Não encontrado"){
      throw new Error("Não foi possível localizar o nome empresarial no PDF.");
    }

    if(d.pa === "Não encontrado"){
      throw new Error("Não foi possível localizar o período de apuração no PDF.");
    }

    dropzone.innerHTML = "✅ Arquivo processado com sucesso!";
    progress.style.width = "100%";

    // Calcular totais
    let totalReceita = 0;
    let totalFolha = 0;

    d.tabela.forEach(l => {
      totalReceita += moedaParaNumero(l.receita);
      totalFolha += moedaParaNumero(l.folha);
    });

    const validacaoFalhou = !!(d.validacaoReceitas && !d.validacaoReceitas.ok);
    const avisoClasse = (d.resumido || validacaoFalhou) ? "aviso" : "aviso ok";
    let avisoTexto = d.resumido
      ? `Este PDF é um resumo/apuração e não trouxe a tabela mensal de Receitas Brutas Anteriores e Folha. Por isso será importado somente o PA atual. RBT12: ${escapeHtml(d.rbt12)} | Folha12: ${escapeHtml(d.folha12)} | DAS: ${escapeHtml(d.valorDas)}`
      : `Receitas Brutas e Folha dos últimos 13 meses. Antes de importar, cadastre a empresa.<br>O total da folha será importado na coluna CPP, conforme a regra atual do importador.`;

    if(d.folhaDeclaradaNenhuma){
      avisoTexto += `<br><strong>O extrato informa “Nenhuma” em Folha de Salários Anteriores; por isso todas as folhas foram mantidas em R$ 0,00.</strong>`;
    }

    if(validacaoFalhou){
      avisoTexto += `<br><strong>ATENÇÃO: a soma das receitas mensais (${escapeHtml(d.validacaoReceitas.totalMensal)}) não confere com o RBT12 do extrato (${escapeHtml(d.validacaoReceitas.totalOficial)}). Diferença: ${escapeHtml(d.validacaoReceitas.diferenca)}. Revise antes de importar.</strong>`;
    }else if(d.validacaoReceitas && d.validacaoReceitas.ok){
      avisoTexto += `<br><strong>Conferência concluída: a soma dos 12 meses fecha com o RBT12 de ${escapeHtml(d.validacaoReceitas.totalOficial)}.</strong>`;
    }

    let html = `
      <p><span class="highlight">Tipo:</span> ${escapeHtml(d.tipoDocumento)}</p>
      <p><span class="highlight">CNPJ:</span> ${escapeHtml(d.cnpj)}</p>
      <p><span class="highlight">Nome:</span> ${escapeHtml(d.nome)} ${/nome usado pelo nome/i.test(d.nome) ? "" : ""}</p>
      <p><span class="highlight">PA:</span> ${escapeHtml(d.pa)}</p>
      ${d.rbt12 && d.rbt12 !== "Não encontrado" ? `<p><span class="highlight">RBT12:</span> ${escapeHtml(d.rbt12)}</p>` : ""}
      ${d.folha12 && d.folha12 !== "Não encontrado" ? `<p><span class="highlight">Folha 12:</span> ${escapeHtml(d.folha12)}</p>` : ""}
      ${d.valorDas && d.valorDas !== "Não encontrado" ? `<p><span class="highlight">Valor DAS:</span> ${escapeHtml(d.valorDas)}</p>` : ""}

      <div class="${avisoClasse}">
        ${avisoTexto}
        <br><small>Leitura: ${(d.debug && d.debug.qtdReceitas) || 0} receitas mensais / ${(d.debug && d.debug.qtdFolhas) || 0} folhas mensais localizadas.</small>
      </div>

      <table>
        <thead>
          <tr>
            <th>Período</th>
            <th>Receita Bruta</th>
            <th>Folha do mês anterior</th>
          </tr>
        </thead>

        <tbody>
          ${d.tabela.map(l => `
            <tr>
              <td>${escapeHtml(l.mes)}</td>
              <td>${escapeHtml(l.receita)}</td>
              <td>${escapeHtml(l.folha)}</td>
            </tr>
          `).join("")}
        </tbody>

        <tfoot>
          <tr>
            <td>Total</td>
            <td>${numeroParaMoeda(totalReceita)}</td>
            <td>${numeroParaMoeda(totalFolha)}</td>
          </tr>
        </tfoot>
      </table>
    `;

    resultado.innerHTML = html;

    btnImportar.style.display = "block";
    btnImportar.disabled = false;
    btnImportar.dataset.json = JSON.stringify(d);

  }catch(err){
    console.error(err);

    progress.style.width = "0%";
    dropzone.innerHTML = `
      ❌ Não foi possível processar este PDF.
      <br><br>
      <small>Clique aqui para selecionar novamente</small>
    `;

    statusDiv.style.display = "block";
    statusDiv.className = "aviso err";
    statusDiv.innerHTML = `
      <span style="color:var(--danger,#ef4444);">
        ${escapeHtml(err.message || "Erro inesperado ao ler o PDF.")}
      </span>
    `;
  }
}

// =====================================================
// IMPORTAR LANÇAMENTOS
// =====================================================

btnImportar.addEventListener("click", async () => {
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
});

