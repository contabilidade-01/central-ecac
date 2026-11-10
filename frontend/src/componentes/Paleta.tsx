import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { cnpj } from "../formatos";

interface Opcao { rotulo: string; detalhe: string; ir: () => void }

const ATALHOS: { rotulo: string; detalhe: string; destino: string }[] = [
  { rotulo: "Mesa do dia", detalhe: "Tela", destino: "/app" },
  { rotulo: "Empresas", detalhe: "Tela", destino: "/app/empresas" },
  { rotulo: "FGTS Digital", detalhe: "Tela", destino: "/app/fgts" },
  { rotulo: "Caixa Postal", detalhe: "Tela antiga", destino: "/?aba=caixa_postal" },
  { rotulo: "Procurações", detalhe: "Tela", destino: "/procuracoes" },
  { rotulo: "Agendamento", detalhe: "Tela", destino: "/agendamento" },
];

/** Busca Ctrl K: empresas por nome ou CNPJ e atalhos para as telas. */
export function Paleta({ aberta, fechar }: { aberta: boolean; fechar: () => void }) {
  const [termo, setTermo] = useState("");
  const [opcoes, setOpcoes] = useState<Opcao[]>([]);
  const [indice, setIndice] = useState(0);
  const campo = useRef<HTMLInputElement>(null);
  const navegar = useNavigate();

  const ir = (destino: string) => {
    fechar();
    if (destino.startsWith("/app")) navegar(destino.replace(/^\/app/, "") || "/");
    else window.location.href = destino;
  };

  useEffect(() => { if (aberta) { setTermo(""); setIndice(0); setTimeout(() => campo.current?.focus(), 0); } }, [aberta]);

  useEffect(() => {
    if (!aberta) return;
    const t = termo.trim().toLowerCase();
    const atalhos = ATALHOS.filter((a) => !t || a.rotulo.toLowerCase().includes(t))
      .map((a) => ({ rotulo: a.rotulo, detalhe: a.detalhe, ir: () => ir(a.destino) }));
    setOpcoes(atalhos);
    if (t.length < 2) return;
    let vivo = true;
    const espera = setTimeout(() => {
      api.busca(t).then((r) => {
        if (!vivo) return;
        const empresas = r.empresas.map((e) => ({ rotulo: e.razao_social, detalhe: cnpj(e.cnpj),
          ir: () => ir(`/app/empresas/${e.company_id}`) }));
        setOpcoes([...empresas, ...atalhos]);
        setIndice(0);
      }).catch(() => undefined);
    }, 180);
    return () => { vivo = false; clearTimeout(espera); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [termo, aberta]);

  if (!aberta) return null;
  return (
    <div className="paleta-fundo" onMouseDown={fechar}>
      <div className="paleta" role="dialog" aria-label="Buscar" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={campo} value={termo} placeholder="Empresa, CNPJ ou tela"
          aria-label="Buscar empresa, CNPJ ou tela"
          onChange={(e) => setTermo(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") fechar();
            if (e.key === "ArrowDown") { e.preventDefault(); setIndice((i) => Math.min(i + 1, opcoes.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setIndice((i) => Math.max(i - 1, 0)); }
            if (e.key === "Enter" && opcoes[indice]) opcoes[indice].ir();
          }} />
        <ul role="listbox">
          {opcoes.map((o, i) => (
            <li key={o.rotulo + o.detalhe}>
              <button type="button" role="option" aria-selected={i === indice} onMouseEnter={() => setIndice(i)}
                onClick={o.ir}>
                <span>{o.rotulo}</span><span className="suave pequeno">{o.detalhe}</span>
              </button>
            </li>
          ))}
          {!opcoes.length && <li className="vazio">Nada encontrado.</li>}
        </ul>
      </div>
    </div>
  );
}
