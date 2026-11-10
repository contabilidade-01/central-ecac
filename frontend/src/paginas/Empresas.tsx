import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Empresas as Dados, type LinhaEmpresa } from "../api";
import { Consultas } from "../componentes/Consultas";
import { brl, cnpj, COR_FAIXA, dataBr, ROTULO_FAIXA } from "../formatos";

const FILTROS = [
  { chave: "todas", rotulo: "Todas" },
  { chave: "critica", rotulo: "Críticas" },
  { chave: "atencao", rotulo: "Atenção" },
  { chave: "boa", rotulo: "Boas" },
] as const;

function Nota({ e }: { e: LinhaEmpresa }) {
  return (
    <span className="nota" style={{ ["--v" as string]: e.nota, ["--cor" as string]: COR_FAIXA[e.faixa] }}
      role="img" aria-label={`Saúde fiscal ${e.nota} de 100, ${ROTULO_FAIXA[e.faixa]}`}>
      <span>{e.nota}</span>
    </span>
  );
}

/** Análise das empresas: todas com a nota de saúde fiscal, da pior para a melhor. */
export function Empresas() {
  const [dados, setDados] = useState<Dados | null>(null);
  const [erro, setErro] = useState("");
  const [filtro, setFiltro] = useState<string>("todas");
  const [busca, setBusca] = useState("");
  const [marcadas, setMarcadas] = useState<Set<number>>(new Set());
  const [aviso, setAviso] = useState("");
  const [gatilho, setGatilho] = useState(0);
  const ir = useNavigate();

  const carregar = () => { api.empresas().then(setDados).catch((e) => setErro(e.message)); };
  useEffect(carregar, []);

  const marcar = (id: number) => setMarcadas((m) => { const n = new Set(m); n.has(id) ? n.delete(id) : n.add(id); return n; });

  // Cada empresa é uma consulta paga na SERPRO: confirma a quantidade antes.
  const consultar = async (todas: boolean) => {
    const qtd = todas ? (dados?.empresas.length ?? 0) : marcadas.size;
    if (!qtd) return;
    if (!window.confirm(`Consultar a situação fiscal de ${qtd} empresa(s) no e-CAC?

Cada empresa é uma consulta paga na SERPRO. `
      + "As consultas rodam uma de cada vez; você pode sair desta tela.")) return;
    setAviso(""); setErro("");
    try {
      const r = await api.consultar(todas ? { todas: true } : { company_ids: [...marcadas] });
      setAviso(`${r.quantidade} empresa(s) na fila de consulta.`);
      setMarcadas(new Set()); setGatilho((g) => g + 1);
    } catch (e: any) { setErro(e.message); }
  };

  const linhas = useMemo(() => {
    const t = busca.trim().toLowerCase();
    const d = t.replace(/\D/g, "");
    return (dados?.empresas || []).filter((e) => (filtro === "todas" || e.faixa === filtro)
      && (!t || e.razao_social.toLowerCase().includes(t) || (d.length >= 3 && e.cnpj.includes(d))));
  }, [dados, filtro, busca]);

  const total = dados?.empresas.length ?? 0;
  const media = total ? Math.round(dados!.empresas.reduce((s, e) => s + e.nota, 0) / total) : 0;

  return (
    <>
      <div className="cabecalho">
        <div>
          <h1>Empresas</h1>
          <p className="sub">Saúde fiscal de cada empresa, da pior para a melhor. Clique para abrir a ficha.</p>
        </div>
        <div className="linha">
          <button className="botao" type="button" disabled={!marcadas.size} onClick={() => consultar(false)}>
            Consultar selecionadas{marcadas.size ? ` (${marcadas.size})` : ""}</button>
          <button className="botao primario" type="button" disabled={!dados} onClick={() => consultar(true)}>
            Consultar todas no e-CAC</button>
        </div>
      </div>
      {erro && <div className="erro">{erro}</div>}
      {aviso && <div className="cartao">{aviso}</div>}
      <Consultas gatilho={gatilho} aoTerminar={carregar} />
      <div className="indicadores">
        <div className="cartao indicador"><span>Empresas</span><strong>{dados ? total : "—"}</strong>
          <small className="suave">nota média {dados ? media : "—"}</small></div>
        <div className="cartao indicador"><span>Críticas</span>
          <strong style={{ color: "var(--critico)" }}>{dados?.faixas.critica ?? "—"}</strong>
          <small className="suave">nota abaixo de 50</small></div>
        <div className="cartao indicador"><span>Atenção</span>
          <strong style={{ color: "var(--alerta)" }}>{dados?.faixas.atencao ?? "—"}</strong>
          <small className="suave">nota de 50 a 79</small></div>
        <div className="cartao indicador"><span>Boas</span>
          <strong style={{ color: "var(--ok)" }}>{dados?.faixas.boa ?? "—"}</strong>
          <small className="suave">nota 80 ou mais</small></div>
      </div>
      <div className="cartao">
        <div className="linha">
          <div className="abas" role="tablist">
            {FILTROS.map((f) => (
              <button key={f.chave} type="button" role="tab" aria-selected={filtro === f.chave}
                className={filtro === f.chave ? "ativo" : ""} onClick={() => setFiltro(f.chave)}>
                {f.rotulo}{dados && f.chave !== "todas" ? ` ${dados.faixas[f.chave as "critica"]}` : ""}
              </button>))}
          </div>
          <input className="campo" value={busca} onChange={(e) => setBusca(e.target.value)}
            placeholder="Filtrar por nome ou CNPJ" aria-label="Filtrar empresas" />
        </div>
        {!dados && !erro && <div className="vazio">Calculando a saúde de cada empresa…</div>}
        {dados && !linhas.length && <div className="vazio">Nenhuma empresa neste filtro.</div>}
        {!!linhas.length && (
          <div className="tabela"><table>
            <thead><tr><th><input type="checkbox" aria-label="Selecionar todas as empresas do filtro"
              checked={linhas.length > 0 && linhas.every((e) => marcadas.has(e.company_id))}
              onChange={(ev) => setMarcadas(ev.target.checked ? new Set(linhas.map((e) => e.company_id)) : new Set())} /></th>
              <th>Saúde</th><th>Empresa</th><th>O que mais pesa</th><th>Vencidos</th><th>FGTS</th>
              <th>Caixa Postal</th><th>Procuração</th><th>Situação fiscal de</th></tr></thead>
            <tbody>{linhas.map((e) => (
              <tr key={e.company_id} className="clicavel" tabIndex={0}
                onClick={() => ir(`/empresas/${e.company_id}`)}
                onKeyDown={(k) => { if (k.key === "Enter") ir(`/empresas/${e.company_id}`); }}>
                <td onClick={(ev) => ev.stopPropagation()}>
                  <input type="checkbox" checked={marcadas.has(e.company_id)} onChange={() => marcar(e.company_id)}
                    aria-label={`Selecionar ${e.razao_social}`} /></td>
                <td><Nota e={e} /></td>
                <td><b>{e.razao_social}</b><div className="suave pequeno">{cnpj(e.cnpj)}</div></td>
                <td className="pequeno">{e.motivo_principal || <span className="suave">Nada reduz a nota</span>}</td>
                <td>{e.vencidos ? <><span className="selo s-critico">{e.vencidos}</span>
                  <div className="pequeno">{brl(e.total_vencido)}</div></> : <span className="suave">—</span>}</td>
                <td>{!e.fgts.aplicavel ? <span className="suave">Não se aplica</span>
                  : e.fgts.vencidas ? <span className="selo s-critico">{e.fgts.vencidas} vencida(s)</span>
                  : e.fgts.consultado ? <span className="selo s-ok">Em dia</span>
                  : <span className="suave">Sem consulta</span>}</td>
                <td>{e.nao_lidas ? <span className="selo s-info">{e.nao_lidas} não lida(s)</span>
                  : <span className="suave">—</span>}</td>
                <td>{e.procuracao === "ok" ? <span className="selo s-ok">Válida</span>
                  : e.procuracao === "desconhecida" ? <span className="suave">Sem registro</span>
                  : <span className="selo s-critico">Com problema</span>}</td>
                <td className="pequeno">{dataBr(e.relatorio_em)}</td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </div>
    </>
  );
}
