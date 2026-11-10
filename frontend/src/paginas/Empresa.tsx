import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Contato, type Ficha, type Pendencia } from "../api";
import { Consultas } from "../componentes/Consultas";
import { brl, cnpj, COR_FAIXA, dataBr, dataCurta, ROTULO_FAIXA } from "../formatos";

const COR_EVENTO: Record<string, string> = { relatorio: "c-info", guia: "c-ok", mensagem: "c-alerta" };

// Como cada peso aparece em "Ver a regra" (mesmos números do backend, painel_service.PESOS).
const REGRA: [string, (p: Record<string, number>) => string][] = [
  ["Tributo vencido", (p) => `-${p.atraso_por_tributo} por tributo, até -${p.atraso_maximo}`],
  ["FGTS vencido", (p) => `-${p.fgts_vencido}`],
  ["Declaração omissa", (p) => `-${p.omissao} cada, até -${p.omissao_maximo}`],
  ["Dívida ativa (PGFN)", (p) => `-${p.pgfn}`],
  ["Procuração com problema", (p) => `-${p.sem_procuracao}`],
  ["Mensagem não lida", (p) => `-${p.mensagem_nao_lida} cada, até -${p.mensagem_maximo}`],
  ["Situação fiscal com mais de 40 dias", (p) => `-${p.relatorio_velho}`],
];

function Selo({ cor, texto }: { cor: string; texto: string }) {
  return <span className={`selo s-${cor}`}>{texto}</span>;
}

function situacao(p: Pendencia) {
  if (p.em_atraso) return <Selo cor="critico" texto="Vencida" />;
  return p.origem === "fgts" ? <Selo cor="ok" texto="Em dia" /> : <Selo cor="alerta" texto="A vencer" />;
}

export function Empresa() {
  const { id } = useParams();
  const [f, setF] = useState<Ficha | null>(null);
  const [contato, setContato] = useState<Contato | null>(null);
  const [erro, setErro] = useState("");
  const [gatilho, setGatilho] = useState(0);

  const consultar = async () => {
    if (!f || !window.confirm(`Consultar a situação fiscal de ${f.empresa.razao_social} no e-CAC agora? É uma consulta paga na SERPRO.`)) return;
    try { await api.consultar({ company_ids: [f.empresa.company_id] }); setGatilho((g) => g + 1); }
    catch (e: any) { window.alert(e.message); }
  };

  useEffect(() => {
    setF(null); setErro(""); setContato(null);
    api.ficha(Number(id)).then(setF).catch((e) => setErro(e.message));
    api.contato(Number(id)).then(setContato).catch(() => setContato({ disponivel: false }));
  }, [id]);

  if (erro) return <div className="erro">{erro}</div>;
  if (!f) return <div className="vazio">Carregando…</div>;
  const proc = f.procuracao.situacao;
  const textoProc = proc === "ok" ? "Procuração SERPRO válida" : proc === "desconhecida" ? "Procuração SERPRO sem registro"
    : "Procuração SERPRO com problema";

  return (
    <>
      <div className="cabecalho">
        <div>
          <span className="suave pequeno"><Link to="/empresas">Empresas</Link> / Ficha</span>
          <h1>{f.empresa.razao_social}</h1>
          <p className="sub">{cnpj(f.empresa.cnpj)} · {textoProc}
            {f.fgts.aplicavel ? " · FGTS Digital ativo" : ""}
            {f.relatorio?.situacao ? ` · ${f.relatorio.situacao}` : ""}</p>
        </div>
        <div className="linha">
          {f.portal_url && <a className="botao" href={f.portal_url} target="_blank" rel="noreferrer">Abrir no Portal</a>}
          {f.fgts.aplicavel && <Link className="botao" to={`/fgts?empresa=${f.empresa.company_id}`}>FGTS Digital</Link>}
          <button className="botao" type="button" onClick={consultar}>Consultar no e-CAC</button>
          <a className="botao primario" href="/?aba=das">Gerar DAS</a>
        </div>
      </div>
      <Consultas gatilho={gatilho} aoTerminar={() => api.ficha(Number(id)).then(setF).catch(() => undefined)} />
      <div className="duas">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="cartao">
            <h2>Pendências abertas</h2>
            {!f.pendencias.length && <div className="vazio">Sem débitos no último relatório.</div>}
            {!!f.pendencias.length && (
              <div className="tabela"><table>
                <thead><tr><th>Tributo</th><th>Competência</th><th>Vencimento</th><th>Situação</th>
                  <th className="num">Valor</th></tr></thead>
                <tbody>{f.pendencias.map((p) => (
                  <tr key={p.tipo + p.competencia}>
                    <td><b>{p.rotulo}</b></td><td>{p.competencia}</td><td>{dataBr(p.vencimento)}</td>
                    <td>{situacao(p)}</td>
                    <td className="num">{brl(p.valor)}</td>
                  </tr>))}
                </tbody>
              </table></div>
            )}
            {f.omissoes.length > 0 && <p className="suave pequeno">Declarações omissas:{" "}
              {f.omissoes.map((o) => `${o.tipo} ${o.ano}`).join("; ")}</p>}
            {f.parcelamento && <p className="pequeno"><b>Parcelamento:</b> {f.parcelamento}</p>}
            {f.pgfn && <p className="pequeno"><b>PGFN:</b> {f.pgfn}</p>}
            <p className="suave pequeno">Receita: situação fiscal de {dataBr(f.relatorio?.data_hora)}.
              {f.fgts.aplicavel ? ` FGTS: consulta de ${dataBr(f.fgts.consultado_em)}.` : ""}</p>
          </div>
          <div className="cartao">
            <h2>Guias emitidas</h2>
            {!f.guias.length && <div className="vazio">Nenhuma guia emitida pela Central.</div>}
            {!!f.guias.length && (
              <div className="tabela"><table>
                <thead><tr><th>Guia</th><th>Competência</th><th>Pagar até</th><th>Origem</th><th className="num">Valor</th></tr></thead>
                <tbody>{f.guias.map((g) => (
                  <tr key={g.id}><td>{g.tipo}{g.reuso ? " (reaproveitada)" : ""}</td><td>{g.periodo_apuracao}</td>
                    <td>{g.vencimento ? dataBr(`${g.vencimento.slice(0, 4)}-${g.vencimento.slice(4, 6)}-${g.vencimento.slice(6, 8)}`) : "—"}</td>
                    <td>{g.origem}</td><td className="num">{brl(g.valor_total)}</td></tr>))}
                </tbody>
              </table></div>
            )}
          </div>
          <div className="cartao">
            <h2>Linha do tempo</h2>
            <div className="tempo" style={{ marginTop: 10 }}>
              {f.linha_do_tempo.map((e, n) => (
                <div key={n}><span className="suave">{dataCurta(e.em)}</span><i className={COR_EVENTO[e.tipo] || "c-neutro"} />
                  <span>{e.texto}</span></div>))}
              {!f.linha_do_tempo.length && <span className="suave">Sem eventos.</span>}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="cartao" style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
            <div className="medidor" style={{ ["--v" as string]: f.saude.nota, ["--cor" as string]: COR_FAIXA[f.saude.faixa] }}
              role="img" aria-label={`Saúde fiscal ${f.saude.nota} de 100`}>
              <div><span><b>{f.saude.nota}</b><small className="suave">Saúde fiscal<br />{ROTULO_FAIXA[f.saude.faixa]}</small></span></div>
            </div>
            <div className="pequeno" style={{ flex: "1 1 140px" }}>
              <b>O que reduz a nota</b>
              {!f.saude.itens.length && <div className="suave">Nada reduz a nota.</div>}
              {f.saude.itens.map((i) => <div key={i.motivo} className="suave">{i.motivo}: {i.pontos}</div>)}
              <details className="regra"><summary>Ver a regra</summary>
                <ul>{REGRA.map(([nome, txt]) => <li key={nome}>{nome}: {txt(f.pesos)}</li>)}</ul>
                <div className="suave pequeno">A nota parte de 100. Boa a partir de 80, crítica abaixo de 50.</div>
              </details>
            </div>
          </div>
          <div className="cartao">
            <h2>Situação por área</h2>
            <table style={{ marginTop: 6 }}><tbody>
              <tr><td>Situação fiscal</td><td>{f.resumo.qtd_atraso ? <Selo cor="critico" texto="Irregular" />
                : <Selo cor="ok" texto="Sem vencidos" />}</td></tr>
              <tr><td>FGTS Digital</td><td>{!f.fgts.aplicavel ? <Selo cor="neutro" texto="Não se aplica" />
                : f.fgts.vencidas ? <Selo cor="critico" texto={`${f.fgts.vencidas} vencida(s)`} />
                : f.fgts.consultado_em ? <Selo cor="ok" texto="Em dia" /> : <Selo cor="neutro" texto="Sem consulta" />}</td></tr>
              <tr><td>Caixa Postal</td><td>{f.caixa_postal.nao_lidas ? <Selo cor="info" texto={`${f.caixa_postal.nao_lidas} não lida(s)`} />
                : <Selo cor="ok" texto="Sem pendência" />}</td></tr>
              <tr><td>Procuração SERPRO</td><td>{proc === "ok" ? <Selo cor="ok" texto="Válida" />
                : proc === "desconhecida" ? <Selo cor="neutro" texto="Sem registro" /> : <Selo cor="critico" texto="Com problema" />}</td></tr>
            </tbody></table>
          </div>
          <div className="cartao">
            <h2>Contato</h2>
            <p className="suave pequeno" style={{ marginTop: 4 }}>Vem do cadastro do Portal. A Central não copia o contato.</p>
            {!contato && <div className="suave pequeno">Consultando o Portal…</div>}
            {contato && !contato.disponivel && <div className="suave pequeno">Portal indisponível agora.</div>}
            {contato?.disponivel && !contato.vinculada && <Selo cor="alerta" texto="Empresa não encontrada no Portal" />}
            {contato?.disponivel && contato.vinculada && (
              <div className="linha" style={{ justifyContent: "flex-start" }}>
                <Selo cor="ok" texto="Cadastrada no Portal" />
                {contato.whatsapp_valido ? <Selo cor="ok" texto="WhatsApp validado" />
                  : <Selo cor="alerta" texto="Sem WhatsApp válido" />}
              </div>)}
          </div>
        </div>
      </div>
    </>
  );
}
