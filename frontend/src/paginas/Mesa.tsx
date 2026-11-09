import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ItemFila, type Mesa as MesaDados } from "../api";
import { brl, COR_DO_TIPO, dataBr, destinoDaAcao } from "../formatos";

const FILTROS: { chave: string; rotulo: string; tipos: ItemFila["tipo"][] }[] = [
  { chave: "tudo", rotulo: "Tudo", tipos: ["vencido", "vence_logo", "mensagem", "procuracao", "sem_dado"] },
  { chave: "vencido", rotulo: "Vencidos", tipos: ["vencido"] },
  { chave: "vence", rotulo: "Vencem logo", tipos: ["vence_logo"] },
  { chave: "mensagem", rotulo: "Caixa Postal", tipos: ["mensagem"] },
  { chave: "outros", rotulo: "Cadastro", tipos: ["procuracao", "sem_dado"] },
];

const ROTULO_ACAO: Record<string, string> = { gerar_guia: "Abrir", ver: "Abrir", caixa_postal: "Ler", procuracoes: "Ver" };

export function Mesa() {
  const [dados, setDados] = useState<MesaDados | null>(null);
  const [erro, setErro] = useState("");
  const [filtro, setFiltro] = useState("tudo");

  const carregar = () => { setErro(""); api.mesa().then(setDados).catch((e) => setErro(e.message)); };
  useEffect(carregar, []);

  const itens = useMemo(() => {
    const tipos = FILTROS.find((f) => f.chave === filtro)!.tipos;
    return (dados?.fila || []).filter((i) => tipos.includes(i.tipo));
  }, [dados, filtro]);
  const contagem = (chave: string) =>
    (dados?.fila || []).filter((i) => FILTROS.find((f) => f.chave === chave)!.tipos.includes(i.tipo)).length;

  return (
    <>
      <div className="cabecalho">
        <div>
          <h1>Mesa do dia</h1>
          <p className="sub">O que precisa de você hoje, em ordem de prazo e risco.</p>
        </div>
        <button className="botao" type="button" onClick={carregar}>Atualizar</button>
      </div>
      {erro && <div className="erro">{erro}</div>}
      <div className="indicadores">
        <div className="cartao indicador"><span>Vencem em até 5 dias</span><strong>{dados?.kpi.vence_logo ?? "—"}</strong>
          <small className="suave">guias a vencer</small></div>
        <div className="cartao indicador"><span>Empresas com tributo vencido</span>
          <strong>{dados?.kpi.em_atraso_empresas ?? "—"}</strong>
          <small className="suave">{dados ? brl(dados.kpi.em_atraso_total) : ""} no último relatório</small></div>
        <div className="cartao indicador"><span>Caixa Postal</span><strong>{dados?.kpi.mensagens_nao_lidas ?? "—"}</strong>
          <small className="suave">mensagens não lidas</small></div>
        <div className="cartao indicador"><span>Sem consulta recente</span><strong>{dados?.kpi.sem_dado_recente ?? "—"}</strong>
          <small className="suave">de {dados?.kpi.empresas ?? "—"} empresas</small></div>
      </div>
      <div className="cartao">
        <div className="linha">
          <h2>Fila de trabalho</h2>
          <div className="abas" role="tablist">
            {FILTROS.map((f) => (
              <button key={f.chave} type="button" role="tab" aria-selected={filtro === f.chave}
                className={filtro === f.chave ? "ativo" : ""} onClick={() => setFiltro(f.chave)}>
                {f.rotulo} {dados ? contagem(f.chave) : ""}
              </button>
            ))}
          </div>
        </div>
        {!dados && !erro && <div className="vazio">Carregando…</div>}
        {dados && !itens.length && <div className="vazio">Nada aqui. Bom trabalho.</div>}
        {itens.slice(0, 200).map((i, n) => (
          <div className="fila-item" key={`${i.company_id}-${i.tipo}-${i.detalhe}-${n}`}>
            <span className={`faixa c-${COR_DO_TIPO[i.tipo]}`} />
            <div>
              <b>{i.titulo}</b> · <Link to={`/empresas/${i.company_id}`}>{i.razao_social}</Link>
              <div className="suave pequeno">
                {i.detalhe}{i.valor != null ? ` · ${brl(i.valor)}` : ""}{i.data ? ` · vence ${dataBr(i.data)}` : ""}
              </div>
            </div>
            {destinoDaAcao(i.acao, i.company_id).startsWith("/app")
              ? <Link className="botao pequeno" to={`/empresas/${i.company_id}`}>{ROTULO_ACAO[i.acao]}</Link>
              : <a className="botao pequeno" href={destinoDaAcao(i.acao, i.company_id)}>{ROTULO_ACAO[i.acao]}</a>}
          </div>
        ))}
        {itens.length > 200 && <p className="suave pequeno">Mostrando 200 de {itens.length}. Use a busca (Ctrl K).</p>}
      </div>
    </>
  );
}
