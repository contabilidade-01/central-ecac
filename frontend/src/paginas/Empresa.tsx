import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Ficha } from "../api";
import { brl, cnpj, COR_FAIXA, dataBr, ROTULO_FAIXA } from "../formatos";

const COR_EVENTO: Record<string, string> = { relatorio: "c-info", guia: "c-ok", mensagem: "c-alerta" };

function Selo({ cor, texto }: { cor: string; texto: string }) {
  return <span className={`selo s-${cor}`}>{texto}</span>;
}

export function Empresa() {
  const { id } = useParams();
  const [f, setF] = useState<Ficha | null>(null);
  const [erro, setErro] = useState("");

  useEffect(() => {
    setF(null); setErro("");
    api.ficha(Number(id)).then(setF).catch((e) => setErro(e.message));
  }, [id]);

  if (erro) return <div className="erro">{erro}</div>;
  if (!f) return <div className="vazio">Carregando…</div>;
  const proc = f.procuracao.situacao;

  return (
    <>
      <div className="cabecalho">
        <div>
          <span className="suave pequeno"><Link to="/">Mesa do dia</Link> / Empresa</span>
          <h1>{f.empresa.razao_social}</h1>
          <p className="sub">{cnpj(f.empresa.cnpj)} · Situação fiscal de {dataBr(f.relatorio?.data_hora)}
            {f.relatorio?.situacao ? ` · ${f.relatorio.situacao}` : ""}</p>
        </div>
        <div className="linha">
          <a className="botao" href="/?aba=das">Gerar DAS</a>
          <Link className="botao" to="/fgts">FGTS Digital</Link>
        </div>
      </div>
      <div className="duas">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="cartao">
            <h2>Pendências do último relatório</h2>
            {!f.pendencias.length && <div className="vazio">Sem débitos no último relatório.</div>}
            {!!f.pendencias.length && (
              <div className="tabela"><table>
                <thead><tr><th>Tributo</th><th>Competência</th><th>Vencimento</th><th>Situação</th>
                  <th className="num">Valor</th></tr></thead>
                <tbody>{f.pendencias.map((p) => (
                  <tr key={p.tipo + p.competencia}>
                    <td><b>{p.rotulo}</b></td><td>{p.competencia}</td><td>{dataBr(p.vencimento)}</td>
                    <td>{p.em_atraso ? <Selo cor="critico" texto="Vencida" /> : <Selo cor="alerta" texto="A vencer" />}</td>
                    <td className="num">{brl(p.valor)}</td>
                  </tr>))}
                </tbody>
              </table></div>
            )}
            {f.omissoes.length > 0 && <p className="suave pequeno">Declarações omissas:{" "}
              {f.omissoes.map((o) => `${o.tipo} ${o.ano}`).join("; ")}</p>}
            {f.parcelamento && <p className="pequeno"><b>Parcelamento:</b> {f.parcelamento}</p>}
            {f.pgfn && <p className="pequeno"><b>PGFN:</b> {f.pgfn}</p>}
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
                <div key={n}><span className="suave">{dataBr(e.em)}</span><i className={COR_EVENTO[e.tipo] || "c-neutro"} />
                  <span>{e.texto}</span></div>))}
              {!f.linha_do_tempo.length && <span className="suave">Sem eventos.</span>}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="cartao" style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
            <div className="medidor" style={{ ["--v" as string]: f.saude.nota, ["--cor" as string]: COR_FAIXA[f.saude.faixa] }}
              role="img" aria-label={`Saúde fiscal ${f.saude.nota} de 100`}>
              <div><span><b>{f.saude.nota}</b><small className="suave">{ROTULO_FAIXA[f.saude.faixa]}</small></span></div>
            </div>
            <div className="pequeno" style={{ flex: "1 1 140px" }}>
              <b>Saúde fiscal</b>
              {!f.saude.itens.length && <div className="suave">Nada reduz a nota.</div>}
              {f.saude.itens.map((i) => <div key={i.motivo} className="suave">{i.motivo}: {i.pontos}</div>)}
            </div>
          </div>
          <div className="cartao">
            <h2>Situação por área</h2>
            <table style={{ marginTop: 6 }}><tbody>
              <tr><td>Situação fiscal</td><td>{f.resumo.qtd_atraso ? <Selo cor="critico" texto={`${f.resumo.qtd_atraso} vencido(s)`} />
                : <Selo cor="ok" texto="Sem vencidos" />}</td></tr>
              <tr><td>FGTS Digital</td><td>{!f.fgts.aplicavel ? <Selo cor="neutro" texto="Não se aplica" />
                : f.fgts.vencidas ? <Selo cor="critico" texto={`${f.fgts.vencidas} vencida(s)`} />
                : f.fgts.consultado_em ? <Selo cor="ok" texto="Em dia" /> : <Selo cor="neutro" texto="Sem consulta" />}</td></tr>
              <tr><td>Caixa Postal</td><td>{f.caixa_postal.nao_lidas ? <Selo cor="info" texto={`${f.caixa_postal.nao_lidas} não lida(s)`} />
                : <Selo cor="ok" texto="Em dia" />}</td></tr>
              <tr><td>Procuração SERPRO</td><td>{proc === "ok" ? <Selo cor="ok" texto="Válida" />
                : proc === "desconhecida" ? <Selo cor="neutro" texto="Sem registro" /> : <Selo cor="critico" texto="Com problema" />}</td></tr>
            </tbody></table>
          </div>
        </div>
      </div>
    </>
  );
}
