# Automação das rotinas, teto de gasto e retomada

Tela: **`/agendamento`** (a de Configurações é da SPA compilada, que não temos o fonte).

---

## 1. O que roda sozinho

| Módulo | Padrão de fábrica | Custo |
|---|---|---|
| **Situação fiscal** (pendências e débitos) | **LIGADO — mensal, dia 25, 03:00** | pago |
| Caixa postal | desligado (sugestão: semanal) | indicador **grátis**; lista/detalhe pagos |
| Parcelamentos | desligado | pago |

A situação fiscal vem ligada porque é ela que atualiza as pendências — é o que faz um
**DAS pago sumir do painel**. Os demais começam desligados de propósito: nada gasta API
sem você decidir.

**Frequências disponíveis por módulo:** `semanal` (escolhe o dia da semana), `quinzenal`
(a cada 15 dias contados da última execução) ou `mensal` (escolhe o dia, 1 a 28 — acima
de 28 não existe em todo mês). O horário também é configurável.

## 2. Como funciona por dentro

Uma thread do próprio processo (`app/scheduler.py`) acorda a cada 5 minutos e pergunta
quais rotinas estão vencidas. Não há APScheduler, Celery nem broker: como o sistema roda
com **1 worker**, uma thread resolve sem dependência nova e sem risco de duas instâncias
dispararem a mesma rotina.

* Liga/desliga: `SCHEDULER_ENABLED=1` (já é o padrão na imagem Docker).
* Fora do container fica **desligado**, para que rodar o sistema na sua máquina não
  dispare chamada paga sem você pedir.
* A configuração fica em `<DATA_DIR>/instance/agendamento.json` — sobrevive a redeploy
  porque está no volume.

## 3. As três travas antes de gastar

1. **Mapa de procurações** — empresa recusada pela SERPRO ou com 2 erros seguidos é
   pulada (ver `/procuracoes`).
2. **Teto mensal de gasto** — verificado **antes de cada empresa**, não só no início.
3. **Execução única** — um lock impede duas rodadas simultâneas do mesmo módulo.

## 4. Teto de gasto: o que acontece ao atingir

Definido na tela `/agendamento` ou por `LIMITE_GASTO_MENSAL` (a variável tem
prioridade). `0` = sem teto. O gasto do mês vem de `api_usage_logs`, a mesma fonte da
tela "Custos API".

Ao atingir o teto **no meio de um lote**:

1. O lote **para na hora** — a empresa seguinte não chega a ser consultada.
2. As que faltam são gravadas num **checkpoint** (`agendamento.json`).
3. A tela mostra o aviso:
   *"⚠ Lote interrompido pelo teto de gasto — 3 empresas já processadas · 7 pendentes.
   A próxima execução continua de onde parou."*
4. Na execução seguinte (automática ou pelo botão "Executar agora"), o lote **retoma
   pelas pendentes**. As já processadas **não são consultadas nem cobradas de novo**.
5. Terminando todas, o checkpoint é apagado sozinho.

Comprovado em teste (10 empresas, teto estourando na 4ª): rodou 1–3, depois 4–7, depois
8–10, **sem nenhuma repetição**.

Para destravar: aumente o teto na tela (ou zere para "sem teto") — na próxima passagem
do agendador ele continua. Ou clique em "Executar agora".

## 5. Rodar na hora

Botão **"Executar agora"** em cada módulo. Ele avisa o custo e pede confirmação. Vale as
mesmas travas.

## 6. Onde acompanhar

| O quê | Onde |
|---|---|
| Última execução e resultado | `/agendamento`, no rodapé de cada módulo |
| Próxima execução | idem |
| Lote interrompido e quantas faltam | aviso amarelo no card do módulo |
| Detalhe do que aconteceu | log do container (`docker logs`), prefixo `[AGENDA]` / `[SCHEDULER]` |
| Gasto acumulado | `/agendamento` (topo) e a tela "Custos API" da SPA |

## 7. Recomendação de configuração inicial

1. Deixe **situação fiscal** como está (mensal, dia 25) — é o que mantém as pendências
   em dia.
2. Ligue a **caixa postal** em **semanal**: o indicador é grátis e só gera custo nas
   empresas que realmente receberam mensagem.
3. Defina um **teto** compatível com o mês (`scripts/estimar_custo_caixa_postal.py` dá a
   ordem de grandeza). Com teto, um engano não vira prejuízo — vira aviso.
4. **Parcelamentos**: deixe desligado até decidir a frequência; é consulta paga por tipo
   habilitado, por empresa.
