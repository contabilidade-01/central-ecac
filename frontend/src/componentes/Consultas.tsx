import { useEffect, useRef, useState } from "react";
import { api, type Consultas as Dados } from "../api";

/** Andamento das consultas de situação fiscal. Atualiza a cada 3 s enquanto houver consulta. */
export function Consultas({ gatilho, aoTerminar }: { gatilho: number; aoTerminar?: () => void }) {
  const [dados, setDados] = useState<Dados | null>(null);
  const ativo = useRef(false);

  useEffect(() => {
    let vivo = true;
    let espera: ReturnType<typeof setTimeout>;
    const ler = () => api.consultas().then((d) => {
      if (!vivo) return;
      setDados(d);
      const rodando = d.em_andamento > 0 || d.na_fila > 0;
      if (ativo.current && !rodando) aoTerminar?.();
      ativo.current = rodando;
      if (rodando) espera = setTimeout(ler, 3000);
    }).catch(() => undefined);
    ler();
    return () => { vivo = false; clearTimeout(espera); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gatilho]);

  if (!dados) return null;
  const rodando = dados.empresas.filter((e) => e.status === "processing");
  const erros = dados.empresas.filter((e) => e.status === "error");
  if (!rodando.length && !dados.na_fila && !erros.length) return null;

  return (
    <div className="cartao" aria-live="polite">
      <div className="linha">
        <h2>Consultas da situação fiscal</h2>
        <span className="suave pequeno">
          {rodando.length || dados.na_fila ? `${rodando.length} em andamento · ${dados.na_fila} na fila` : "Nenhuma em andamento"}
        </span>
      </div>
      {rodando.map((e) => (
        <div key={e.company_id} className="progresso">
          <div className="linha"><b className="pequeno">{e.razao_social}</b>
            <span className="suave pequeno">{e.etapa || "Consultando"} · {e.progresso}%</span></div>
          <div className="barra-progresso"><div style={{ width: `${e.progresso}%` }} /></div>
        </div>))}
      {!!erros.length && (
        <details className="regra"><summary>{erros.length} empresa(s) com erro na última consulta</summary>
          <ul>{erros.map((e) => <li key={e.company_id}><b>{e.razao_social}</b>: {e.mensagem || "erro"}</li>)}</ul>
        </details>)}
    </div>
  );
}
