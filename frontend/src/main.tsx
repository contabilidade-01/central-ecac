import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, type Eu, type Status } from "./api";
import { Paleta } from "./componentes/Paleta";
import { Empresa } from "./paginas/Empresa";
import { Empresas } from "./paginas/Empresas";
import { Fgts } from "./paginas/Fgts";
import { Mesa } from "./paginas/Mesa";
import "./estilo.css";

function temaInicial(): "escuro" | "claro" {
  try { return (localStorage.getItem("central_tema") as "escuro" | "claro") || "escuro"; } catch { return "escuro"; }
}

function destinoAntigo(item: { url?: string; chave: string }) {
  return item.url || `/?aba=${item.chave}`;
}

function Contador({ n }: { n?: number }) {
  return n ? <span className="contador" aria-label={`${n} pendente(s)`}>{n}</span> : null;
}

function Luz({ cor, texto, href }: { cor: string; texto: string; href?: string }) {
  const corpo = <><i style={{ background: cor }} />{texto}</>;
  return href ? <a className="luz" href={href}>{corpo}</a> : <span className="luz">{corpo}</span>;
}

function App() {
  const [eu, setEu] = useState<Eu | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [paleta, setPaleta] = useState(false);
  const [tema, setTema] = useState(temaInicial);
  const local = useLocation();
  // A ficha da empresa pertence à tela Empresas: o menu continua mostrando onde você está.
  const naEmpresas = local.pathname.startsWith("/empresas");

  useEffect(() => { api.eu().then(setEu).catch(() => undefined); }, []);
  useEffect(() => { api.status().then(setStatus).catch(() => undefined); }, [local.pathname]);
  useEffect(() => {
    document.documentElement.dataset.tema = tema;
    try { localStorage.setItem("central_tema", tema); } catch { /* sem armazenamento */ }
  }, [tema]);
  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaleta(true); }
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, []);

  const contadorDe = (chave: string) =>
    chave === "fgts" ? status?.fgts_empresas_vencidas : chave === "caixa_postal" ? status?.caixa_postal : undefined;
  const f = status?.fgts;

  return (
    <div className="app">
      <nav className="menu" aria-label="Menu principal">
        <div className="marca"><i>eC</i><div>Central Ecac<small>Nescon Contabilidade</small></div></div>
        <NavLink to="/" end className={({ isActive }) => `item${isActive ? " ativo" : ""}`}>
          <span>Mesa do dia</span><Contador n={status?.mesa} /></NavLink>
        <NavLink to="/empresas" className={`item${naEmpresas ? " ativo" : ""}`} aria-current={naEmpresas ? "page" : undefined}>
          <span>Empresas</span><span className="novo">novo</span></NavLink>
        {eu?.menu.map((g) => (
          <div key={g.titulo}>
            <div className="grupo">{g.titulo}</div>
            {g.itens.filter((i) => i.chave !== "dashboard").map((i) => i.chave === "fgts" ? (
              <NavLink key={i.chave} to="/fgts" className={({ isActive }) => `item${isActive ? " ativo" : ""}`}>
                <span>{i.rotulo}</span><Contador n={contadorDe(i.chave)} /></NavLink>
            ) : (
              <a key={i.chave} className="item" href={destinoAntigo(i)}>
                <span>{i.rotulo}</span><Contador n={contadorDe(i.chave)} /></a>
            ))}
          </div>
        ))}
        <div className="grupo">Sessão</div>
        <a className="item" href="/?antiga=1">Tela antiga</a>
        <a className="item" href="/logout">Sair</a>
      </nav>
      <div className="principal">
        <header className="topo">
          <button className="busca" type="button" onClick={() => setPaleta(true)}>
            <span>Buscar empresa, CNPJ ou tela</span><kbd>Ctrl K</kbd>
          </button>
          <div className="luzes">
            {f && f.executor === "agente" && <Luz href="/fgts" cor={f.agentes_online ? "var(--ok)" : "var(--critico)"}
              texto={f.agentes_online ? "Agente FGTS online" : "Agente FGTS offline"} />}
            {f && f.executor === "agente" && <Luz href="/fgts" cor={f.agentes_com_login ? "var(--ok)" : "var(--alerta)"}
              texto={f.agentes_com_login ? "Login gov.br ativo" : "Sem login gov.br"} />}
            {status && <Luz href="/procuracoes" cor={status.procuracoes_problema ? "var(--alerta)" : "var(--ok)"}
              texto={status.procuracoes_problema ? `Procurações: ${status.procuracoes_problema} com problema`
                : "Procurações em ordem"} />}
          </div>
          <button className="botao" type="button" onClick={() => setTema(tema === "escuro" ? "claro" : "escuro")}
            aria-label="Alternar tema">{tema === "escuro" ? "Tema claro" : "Tema escuro"}</button>
          {eu?.nome && <span className="suave pequeno">{eu.nome}</span>}
        </header>
        <main className="conteudo">
          <Routes>
            <Route path="/" element={<Mesa />} />
            <Route path="/empresas" element={<Empresas />} />
            <Route path="/empresas/:id" element={<Empresa />} />
            <Route path="/fgts" element={<Fgts />} />
            <Route path="*" element={<div className="vazio">Tela não encontrada.</div>} />
          </Routes>
        </main>
      </div>
      <Paleta aberta={paleta} fechar={() => setPaleta(false)} />
    </div>
  );
}

createRoot(document.getElementById("raiz")!).render(
  <StrictMode><BrowserRouter basename="/app"><App /></BrowserRouter></StrictMode>,
);
