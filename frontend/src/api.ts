// Cliente da API da Central. A sessão é o cookie do login do próprio sistema.

export interface ItemFila {
  company_id: number; razao_social: string; cnpj: string;
  tipo: "vencido" | "vence_logo" | "mensagem" | "procuracao" | "sem_dado";
  prioridade: number; titulo: string; detalhe: string; valor: number | null; data: string | null;
  acao: "gerar_guia" | "ver" | "caixa_postal" | "procuracoes";
}
export interface Mesa {
  hoje: string; gerado_em: string; total_fila: number; fila: ItemFila[];
  kpi: { vence_logo: number; em_atraso_empresas: number; em_atraso_total: number; mensagens_nao_lidas: number;
         sem_dado_recente: number; empresas: number };
}
export interface Pendencia { tipo: string; rotulo: string; competencia: string; vencimento: string | null;
  valor: number; em_atraso: boolean; guia: string | null; origem?: "fgts" }
export interface Ficha {
  empresa: { company_id: number; razao_social: string; cnpj: string; ativo: boolean };
  portal_url: string | null; pesos: Record<string, number>;
  pendencias: Pendencia[]; omissoes: { tipo: string; ano: string; meses: string[] }[];
  parcelamento: string | null; pgfn: string | null;
  relatorio: { data_hora: string | null; situacao: string | null } | null;
  resumo: { qtd_atraso: number; total_atraso: number; qtd_a_vencer: number; total_a_vencer: number;
            qtd_invalidos: number; qtd_omissoes: number };
  procuracao: { situacao: string }; caixa_postal: { nao_lidas: number };
  fgts: { aplicavel: boolean; motivo: string; consultado_em: string | null; vencidas: number; total_aberto: number };
  guias: { id: number; tipo: string; periodo_apuracao: string; vencimento: string | null; valor_total: number | null;
           origem: string; emitido_em: string; reuso: boolean }[];
  saude: { nota: number; faixa: "boa" | "atencao" | "critica"; itens: { motivo: string; pontos: number }[] };
  linha_do_tempo: { em: string; tipo: string; texto: string }[];
}
export interface LinhaEmpresa { company_id: number; razao_social: string; cnpj: string; nota: number;
  faixa: "boa" | "atencao" | "critica"; motivo_principal: string; vencidos: number; total_vencido: number;
  fgts: { aplicavel: boolean; vencidas: number; consultado: boolean }; nao_lidas: number; procuracao: string; relatorio_em: string | null }
export interface Empresas { empresas: LinhaEmpresa[]; faixas: { critica: number; atencao: number; boa: number };
  pesos: Record<string, number> }
export interface Status { mesa: number; caixa_postal: number; fgts_empresas_vencidas: number; procuracoes_problema: number;
  fgts: { habilitado: boolean; executor: string; agentes_online: number; agentes_com_login: number } | null }
export interface Contato { disponivel: boolean; vinculada?: boolean; whatsapp_valido?: boolean; motivo?: string }
export interface Eu { nome: string; admin: boolean;
  menu: { titulo: string; itens: { chave: string; rotulo: string; url?: string; spa?: string }[] }[] }

export class ErroApi extends Error {
  constructor(public status: number, mensagem: string) { super(mensagem); }
}

async function get<T>(caminho: string): Promise<T> {
  const r = await fetch(caminho, { credentials: "same-origin", headers: { Accept: "application/json" } });
  if (r.status === 401) { window.location.href = "/login?proximo=" + encodeURIComponent(location.pathname); }
  let corpo: any = null;
  try { corpo = await r.json(); } catch { /* corpo vazio */ }
  if (!r.ok) throw new ErroApi(r.status, corpo?.message || `Erro ${r.status}`);
  return corpo as T;
}

async function post<T>(caminho: string, corpo: unknown): Promise<T> {
  const r = await fetch(caminho, { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(corpo) });
  let dados: any = null;
  try { dados = await r.json(); } catch { /* corpo vazio */ }
  if (!r.ok || dados?.success === false) throw new ErroApi(r.status, dados?.message || `Erro ${r.status}`);
  return dados as T;
}

export interface Consultas { em_andamento: number; na_fila: number;
  empresas: { company_id: number; razao_social: string; status: "processing" | "error"; etapa: string | null;
              progresso: number; mensagem: string | null }[] }

export const api = {
  mesa: () => get<Mesa>("/api/painel/mesa"),
  ficha: (id: number) => get<Ficha>(`/api/painel/empresas/${id}`),
  busca: (q: string) => get<{ empresas: { company_id: number; razao_social: string; cnpj: string }[] }>(
    `/api/painel/busca?q=${encodeURIComponent(q)}`),
  eu: () => get<Eu>("/api/painel/eu"),
  empresas: () => get<Empresas>("/api/painel/empresas"),
  status: () => get<Status>("/api/painel/status"),
  consultar: (corpo: { company_ids?: number[]; todas?: boolean }) =>
    post<{ quantidade: number }>("/api/painel/consultar", corpo),
  consultas: () => get<Consultas>("/api/painel/consultas"),
  contato: (id: number) => get<Contato>(`/api/painel/empresas/${id}/contato`),
};
