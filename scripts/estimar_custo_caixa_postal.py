"""Estimativa de custo da API SERPRO para o monitoramento da Caixa Postal.

## O que é grátis e o que é pago (fonte: frontend original do exe)

  "Essa função é gratuita na API Integra Contador."       -> Monitorar / INNOVAMSG63
  "As funções de buscar mensagens e detalhes são pagas."  -> MSGCONTRIBUINTE61 (lista)
                                                             MSGDETALHAMENTO62 (detalhe)

Por isso o exe NÃO chama `register_usage` no monitoramento — não era esquecimento.

## Modelo de chamadas por rodada do botão "Monitorar"

  1 indicador por empresa ativa .............................. GRÁTIS
  se indicadorMensagensNovas > 0 e as mensagens não foram baixadas:
      1 lista (+1 por página extra) ......................... PAGA
      1 detalhe por mensagem NOVA ou ALTERADA ............... PAGA

O detalhe de mensagem já baixada e inalterada é pulado (DESVIO INTENCIONAL 6o); no exe
puro seriam TODAS as mensagens da caixa a cada consulta.

## Preço (app/services/api_usage_service.py, valores do exe)

  consultar: R$ 0,24 nas primeiras 300 chamadas do mês; R$ 0,21 da 301ª em diante
  emitir   : R$ 0,32 nas primeiras 500;                 R$ 0,29 da 501ª em diante

Uso:
  .venv/Scripts/python.exe scripts/estimar_custo_caixa_postal.py
  .venv/Scripts/python.exe scripts/estimar_custo_caixa_postal.py --empresas 72 --mensagens 16
"""
import argparse

FAIXA_CONSULTA = 300
PRECO_ATE_FAIXA = 0.24
PRECO_ACIMA_FAIXA = 0.21


def custo_consultas(qtd: int, ja_gastas_no_mes: int = 0) -> float:
    """Custo de `qtd` consultas pagas, respeitando a virada de faixa no mês."""
    restante_na_faixa_cheia = max(0, FAIXA_CONSULTA - ja_gastas_no_mes)
    na_faixa = min(qtd, restante_na_faixa_cheia)
    return na_faixa * PRECO_ATE_FAIXA + (qtd - na_faixa) * PRECO_ACIMA_FAIXA


def chamadas_pagas(empresas: int, pct_com_novidade: float, mensagens_por_evento: int):
    """(empresas com novidade, chamadas de lista, chamadas de detalhe) — só as PAGAS."""
    com_novidade = round(empresas * pct_com_novidade)
    return com_novidade, com_novidade, com_novidade * mensagens_por_evento


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--empresas", type=int, default=72)
    ap.add_argument("--mensagens", type=int, default=1,
                    help="mensagens NOVAS/alteradas por empresa que teve novidade "
                         "(use 16 para simular a 1a carga, que baixa a caixa inteira)")
    args = ap.parse_args()

    print(f"empresas ativas: {args.empresas} | mensagens novas por evento: {args.mensagens}")
    print(f"indicador (INNOVAMSG63): GRATUITO — {args.empresas} chamadas por rodada custam R$ 0,00\n")

    print("CUSTO DE UMA RODADA (só as chamadas pagas)")
    print(f"{'% com msg nova':>14} | {'lista':>5} | {'detalhe':>7} | {'pagas':>6} | "
          f"{'a R$0,24':>9} | {'a R$0,21':>9}")
    print("-" * 70)
    for pct in (0.0, 0.05, 0.10, 0.25, 0.50, 1.0):
        _, lis, det = chamadas_pagas(args.empresas, pct, args.mensagens)
        total = lis + det
        print(f"{pct*100:>13.0f}% | {lis:>5} | {det:>7} | {total:>6} | "
              f"{'R$ ' + format(total * PRECO_ATE_FAIXA, '.2f'):>9} | "
              f"{'R$ ' + format(total * PRECO_ACIMA_FAIXA, '.2f'):>9}")

    print("\nCUSTO MENSAL por frequência (10% das empresas com mensagem nova por rodada)")
    print(f"{'frequência':>22} | {'rodadas/mês':>11} | {'pagas/mês':>9} | {'custo/mês':>10}")
    print("-" * 62)
    _, lis, det = chamadas_pagas(args.empresas, 0.10, args.mensagens)
    por_rodada = lis + det
    for nome, rodadas in (("1x por mês", 1), ("1x por semana", 4), ("2x por semana", 8),
                          ("dias úteis (22x)", 22), ("todo dia", 30)):
        total = por_rodada * rodadas
        print(f"{nome:>22} | {rodadas:>11} | {total:>9} | "
              f"{'R$ ' + format(custo_consultas(total), '.2f'):>10}")

    print("\nPRIMEIRA CARGA (a caixa inteira de quem nunca foi baixado):")
    for msgs in (16,):
        _, lis, det = chamadas_pagas(args.empresas, 1.0, msgs)
        total = lis + det
        print(f"  todas as {args.empresas} empresas × {msgs} mensagens = {total} chamadas pagas "
              f"-> {'R$ ' + format(custo_consultas(total), '.2f')}")
    print("  (acontece UMA vez; nas rodadas seguintes só as novas/alteradas são cobradas)")

    print("\nEmpresas travadas pelo mapa de procurações não geram NENHUMA chamada paga —")
    print("ver instance/procuracoes.json e a tela /procuracoes.")


if __name__ == "__main__":
    main()
