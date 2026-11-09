// Formatação pura, testada em formatos.test.ts.

export const brl = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });

export function cnpj(v: string): string {
  const d = (v || "").replace(/\D/g, "");
  return d.length === 14 ? d.replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, "$1.$2.$3/$4-$5") : v;
}

/** 'AAAA-MM-DD' (ou ISO com hora) → 'dd/mm/aaaa', sem passar por Date (evita o dia anterior no fuso). */
export function dataBr(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

export function dataCurta(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${m[3]}/${m[2]}` : "—";
}

export const COR_DO_TIPO: Record<string, string> = {
  vencido: "critico", vence_logo: "alerta", mensagem: "info", procuracao: "neutro", sem_dado: "neutro",
};

export const ROTULO_FAIXA = { boa: "Boa", atencao: "Atenção", critica: "Crítica" } as const;
export const COR_FAIXA = { boa: "var(--ok)", atencao: "var(--alerta)", critica: "var(--critico)" } as const;

/** Para onde leva cada ação da fila: as telas antigas continuam sendo onde a ação acontece. */
export function destinoDaAcao(acao: string, companyId: number): string {
  switch (acao) {
    case "caixa_postal": return "/?aba=caixa_postal";
    case "procuracoes": return "/procuracoes";
    case "gerar_guia":
    case "ver":
    default: return `/app/empresas/${companyId}`;
  }
}
