import { describe, expect, it } from "vitest";
import { brl, cnpj, dataBr, dataCurta, destinoDaAcao } from "./formatos";

describe("formatos", () => {
  it("formata CNPJ e preserva o que não é CNPJ", () => {
    expect(cnpj("36298150000170")).toBe("36.298.150/0001-70");
    expect(cnpj("123")).toBe("123");
  });
  it("data sem mudar de dia por fuso", () => {
    expect(dataBr("2026-11-01")).toBe("01/11/2026");
    expect(dataBr("2026-11-01T23:59:00")).toBe("01/11/2026");
    expect(dataBr(null)).toBe("—");
    expect(dataCurta("2026-11-20")).toBe("20/11");
  });
  it("moeda e vazio", () => {
    expect(brl(null)).toBe("—");
    expect(brl(1234.5)).toContain("1.234,50");
  });
  it("ações levam à tela certa", () => {
    expect(destinoDaAcao("caixa_postal", 3)).toBe("/?aba=caixa_postal");
    expect(destinoDaAcao("gerar_guia", 3)).toBe("/app/empresas/3");
  });
});
