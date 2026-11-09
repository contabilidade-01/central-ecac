import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, type Eu } from "./api";
import { Paleta } from "./componentes/Paleta";
import { Empresa } from "./paginas/Empresa";
import { Fgts } from "./paginas/Fgts";
import { Mesa } from "./paginas/Mesa";
import "./estilo.css";

function temaInicial(): "escuro" | "claro" {
  try { return (localStorage.getItem("central_tema") as "escuro" | "claro") || "escuro"; } catch { return "escuro"; }
}

function destinoAntigo(item: { url?: string; chave: string }) {
  return item.url || `/?aba=${item.chave}`;
}

function App() {
  const [eu, setEu] = useState<Eu | null>(null);
  const [paleta, setPaleta] = useState(false);
  const [tema, setTema] = useState(temaInicial);
  const local = useLocation();
  // A ficha da empresa é aberta a partir da Mesa do dia: o menu continua mostrando onde você está.
  const naMesa = local.pathname === "/" || local.pathname.startsWith("/empresas");

  useEffect(() => { api.eu().then(setEu).catch(() => undefined); }, []);
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

  return (
    <div className="app">
      <nav className="menu" aria-label="Menu principal">
        <div className="marca"><i>eC</i><div>Central Ecac<small>Nescon Contabilidade</small></div></div>
        <NavLink to="/" end className={`item${naMesa ? " ativo" : ""}`} aria-current={naMesa ? "page" : undefined}>
          <span>Mesa do dia</span><span className="novo">novo</span></NavLink>
        {eu?.menu.map((g) => (
          <div key={g.titulo}>
            <div className="grupo">{g.titulo}</div>
            {g.itens.filter((i) => i.chave !== "dashboard").map((i) => i.chave === "fgts" ? (
              <NavLink key={i.chave} to="/fgts" className={({ isActive }) => `item${isActive ? " ativo" : ""}`}>
                <span>{i.rotulo}</span><span className="novo">novo</span></NavLink>
            ) : (
              <a key={i.chave} className="item" href={destinoAntigo(i)}><span>{i.rotulo}</span></a>
            ))}
          </div>
        ))}
        <div className="grupo">Sessão</div>
        <a className="item" href="/">Tela antiga</a>
        <a className="item" href="/logout">Sair</a>
      </nav>
      <div className="principal">
        <header className="topo">
          <button className="busca" type="button" onClick={() => setPaleta(true)}>
            <span>Buscar empresa, CNPJ ou tela</span><kbd>Ctrl K</kbd>
          </button>
          <button className="botao" type="button" onClick={() => setTema(tema === "escuro" ? "claro" : "escuro")}
            aria-label="Alternar tema">{tema === "escuro" ? "Tema claro" : "Tema escuro"}</button>
          {eu?.nome && <span className="suave pequeno">{eu.nome}</span>}
        </header>
        <main className="conteudo">
          <Routes>
            <Route path="/" element={<Mesa />} />
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
