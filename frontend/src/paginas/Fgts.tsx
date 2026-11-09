import { useCallback, useEffect, useMemo, useState } from "react";
import { brl, cnpj, dataBr } from "../formatos";

// Usa a API do FGTS da Central (/api/fgts/*, só administrador). Configurações e agentes
// seguem na tela /fgts até ganharem versão nova.

interface EmpresaFgts { company_id: number; razao_social: string; cnpj: string; aplicavel: boolean; motivo: string;
  consultado_em: string | null; vencidas: number; total_aberto: number; pedido_aberto: Pedido | null }
interface Competencia { competencia: string; total: number | null; vencimento: string | null; situacao: string;
  tem_guia?: boolean; tem_consignado?: boolean }
interface Pedido { id: number; acao: string; status: string; mensagem: string | null; concluido_em: string | null;
  competencias: Competencia[] }
interface Guia { id: number; competencia: string; numero: string; valor: number | null; vencimento: string | null; tem_pix: boolean }
interface Estado { habilitado: boolean; executor: string; agentes_online: number; agentes_com_login: number; pronto: boolean }

const ROTULO_STATUS: Record<string, string> = { fila: "Na fila", executando: "Executando", aguardando_captcha:
  "Aguardando verificação do gov.br", concluido: "Concluído", erro: "Erro", verificar_portal: "Conferir no portal" };
const comp = (c: string) => `${c.slice(4)}/${c.slice(0, 4)}`;

async function chamar<T>(metodo: string, url: string, corpo?: unknown): Promise<T> {
  const r = await fetch(url, { method: metodo, credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: corpo ? JSON.stringify(corpo) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.success === false) throw new Error(j.message || `Erro ${r.status}`);
  return j as T;
}

function Luz({ ok, alerta, texto }: { ok: boolean; alerta?: boolean; texto: string }) {
  return <span className={`selo s-${ok ? "ok" : alerta ? "alerta" : "critico"}`}>{texto}</span>;
}

export function Fgts() {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [empresas, setEmpresas] = useState<EmpresaFgts[]>([]);
  const [busca, setBusca] = useState("");
  const [escolhida, setEscolhida] = useState<number | null>(null);
  const [ficha, setFicha] = useState<{ consulta: Pedido | null; pedido_aberto: Pedido | null; guias: Guia[];
    empresa: EmpresaFgts } | null>(null);
  const [marcadas, setMarcadas] = useState<string[]>([]);
  const [aviso, setAviso] = useState<{ texto: string; erro?: boolean } | null>(null);

  const carregar = useCallback(() => {
    chamar<Estado>("GET", "/api/fgts/estado").then(setEstado).catch(() => undefined);
    chamar<{ empresas: EmpresaFgts[] }>("GET", `/api/fgts/empresas?q=${encodeURIComponent(busca)}`)
      .then((r) => setEmpresas(r.empresas)).catch((e) => setAviso({ texto: e.message, erro: true }));
  }, [busca]);
  useEffect(carregar, [carregar]);

  const abrir = useCallback((id: number) => {
    setEscolhida(id);
    chamar<any>("GET", `/api/fgts/empresas/${id}`).then((r) => { setFicha(r); }).catch((e) => setAviso({ texto: e.message, erro: true }));
  }, []);
  useEffect(() => {
    if (!escolhida || !ficha?.pedido_aberto) return;
    const t = setTimeout(() => abrir(escolhida), 4000);
    return () => clearTimeout(t);
  }, [escolhida, ficha, abrir]);

  const pedir = async (acao: "consultar" | "emitir") => {
    if (!escolhida) return;
    if (acao === "emitir" && !window.confirm(`Gerar ${marcadas.length} guia(s) REAL(IS) para pagar hoje: ${marcadas.map(comp).join(", ")}?`)) return;
    try {
      await chamar("POST", `/api/fgts/empresas/${escolhida}/pedidos`, { acao, competencias: marcadas });
      setAviso({ texto: acao === "consultar" ? "Busca enviada ao agente." : "Emissão enviada ao agente." });
      setMarcadas([]); abrir(escolhida);
    } catch (e: any) { setAviso({ texto: e.message, erro: true }); }
  };

  const meses = useMemo(() => [...(ficha?.consulta?.competencias || [])].sort((a, b) => a.competencia.localeCompare(b.competencia)), [ficha]);

  return (
    <>
      <div className="cabecalho">
        <div><h1>FGTS Digital</h1><p className="sub">Busque as pendências, marque as competências e gere as guias.</p></div>
        <a className="botao" href="/fgts">Configurações e agentes</a>
      </div>
      {estado && (
        <div className="linha" style={{ justifyContent: "flex-start" }}>
          <Luz ok={estado.habilitado} texto={estado.habilitado ? "Módulo ligado" : "Módulo desligado"} />
          {estado.executor === "agente" && <Luz ok={estado.agentes_online > 0} texto={`${estado.agentes_online} agente(s) online`} />}
          {estado.executor === "agente" && <Luz ok={estado.agentes_com_login > 0} alerta texto={estado.agentes_com_login ? "Login gov.br ativo" : "Sem login no gov.br"} />}
        </div>
      )}
      {aviso && <div className={aviso.erro ? "erro" : "cartao"}>{aviso.texto}</div>}
      <div className="duas">
        <div className="cartao">
          <div className="linha">
            <h2>Empresas com funcionários</h2>
            <input className="busca" style={{ flex: "0 1 260px", cursor: "text" }} value={busca} aria-label="Buscar empresa"
              placeholder="Buscar empresa ou CNPJ" onChange={(e) => setBusca(e.target.value)} />
          </div>
          <div className="tabela"><table>
            <thead><tr><th>Empresa</th><th>Última consulta</th><th>Vencidas</th><th className="num">Em aberto</th></tr></thead>
            <tbody>{empresas.map((e) => (
              <tr key={e.company_id} onClick={() => abrir(e.company_id)} style={{ cursor: "pointer",
                background: e.company_id === escolhida ? "var(--painel-2)" : undefined }}>
                <td><b>{e.razao_social}</b><div className="suave pequeno">{cnpj(e.cnpj)}</div></td>
                <td>{e.consultado_em ? dataBr(e.consultado_em) : "—"}</td>
                <td>{e.vencidas ? <span className="selo s-critico">{e.vencidas}</span> : "—"}</td>
                <td className="num">{e.consultado_em ? brl(e.total_aberto) : "—"}</td>
              </tr>))}
            </tbody>
          </table></div>
          {!empresas.length && <div className="vazio">Nenhuma empresa com funcionários encontrada.</div>}
        </div>
        <div className="cartao" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {!ficha && <div className="vazio">Escolha uma empresa na lista.</div>}
          {ficha && (<>
            <div><h2>{ficha.empresa.razao_social}</h2><div className="suave pequeno">{cnpj(ficha.empresa.cnpj)} · {ficha.empresa.motivo}</div></div>
            {ficha.pedido_aberto
              ? <div className="selo s-alerta" style={{ whiteSpace: "normal" }}>{ficha.pedido_aberto.acao === "emitir" ? "Emitindo" : "Buscando"} ·{" "}
                  {ROTULO_STATUS[ficha.pedido_aberto.status] || ficha.pedido_aberto.status}{ficha.pedido_aberto.mensagem ? ` · ${ficha.pedido_aberto.mensagem}` : ""}</div>
              : <button className="botao primario" type="button" onClick={() => pedir("consultar")}>Buscar pendências no FGTS Digital</button>}
            {meses.length > 0 && (<>
              <b className="pequeno">Competências em aberto · consulta de {dataBr(ficha.consulta?.concluido_em)}</b>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(118px, 1fr))", gap: 8 }}>
                {meses.map((m) => {
                  const on = marcadas.includes(m.competencia);
                  const cor = m.situacao === "vencida" ? "var(--critico)" : "var(--alerta)";
                  return (
                    <button key={m.competencia} type="button" aria-pressed={on}
                      onClick={() => setMarcadas((x) => on ? x.filter((c) => c !== m.competencia) : [...x, m.competencia])}
                      style={{ textAlign: "left", borderRadius: 10, padding: 10, cursor: "pointer", color: "var(--texto)",
                        background: on ? "color-mix(in srgb, var(--destaque) 22%, var(--painel))" : "var(--painel-2)",
                        border: `1px solid ${on ? "var(--destaque)" : "var(--linha)"}`, borderLeft: `4px solid ${cor}` }}>
                      <b>{comp(m.competencia)}</b>
                      <div className="pequeno">{brl(m.total)}</div>
                      <div className="suave pequeno">{m.situacao === "vencida" ? "vencida" : "em dia"}{m.tem_guia ? " · tem guia" : ""}</div>
                    </button>);
                })}
              </div>
              {!ficha.pedido_aberto && <button className="botao primario" type="button" disabled={!marcadas.length}
                onClick={() => pedir("emitir")}>Gerar {marcadas.length || ""} guia(s) para pagar hoje</button>}
            </>)}
            {ficha.consulta && !meses.length && <div className="vazio">Nenhum débito em aberto na última consulta.</div>}
            {ficha.guias.length > 0 && (<>
              <b className="pequeno">Guias emitidas</b>
              <table><tbody>{ficha.guias.map((g) => (
                <tr key={g.id}><td>{comp(g.competencia)}</td><td className="num">{brl(g.valor)}</td>
                  <td>{g.tem_pix ? <span className="selo s-ok">Pix</span> : null}</td>
                  <td><a className="botao pequeno" href={`/api/fgts/guias/${g.id}/pdf`} target="_blank" rel="noreferrer">PDF</a></td></tr>))}
              </tbody></table>
            </>)}
          </>)}
        </div>
      </div>
    </>
  );
}
