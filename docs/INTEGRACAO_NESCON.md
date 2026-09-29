# Integração com o portal do cliente (nescon-clientes) — 18º desvio

**Decisão (revisão de 29/09/2026):** o central-ecac é a **fonte** das pendências e o
**emissor** de guias. Quem fala com o cliente — e-mail, WhatsApp, lembretes, cobrança —
é o portal `nescon-clientes`, que já é dono do contato, do opt-out, da janela de envio,
da lista de números permitidos e do histórico. Por isso aqui **não existe** e-mail,
telefone nem envio de mensagem.

```
┌──────────────────┐   GET /api/interno/pendencias          ┌──────────────────────┐
│  nescon-clientes │ ─────────────────────────────────────▶ │   central-ecac       │
│  (portal)        │   POST /api/interno/das/emitir         │   (SERPRO, SQLite)   │
│                  │ ─────────────────────────────────────▶ │                      │
│  e-mail/WhatsApp │   POST /api/interno/reprocessar        │  situação fiscal     │
│  cobrança, fila  │ ─────────────────────────────────────▶ │  guias, teto, fila   │
└──────────────────┘   cabeçalho X-Integracao-Token         └──────────────────────┘
```

## Variáveis

| Onde | Variável | Para quê |
|---|---|---|
| central-ecac | `INTEGRACAO_TOKEN` | 32+ caracteres; sem ele `/api/interno/*` responde 503 |
| central-ecac | `INTEGRACAO_MAX_EMISSOES_DIA` (3) | guias pagas por dia por empresa/competência |
| central-ecac | `INTEGRACAO_MAX_REPROC_MES` (2) | reprocessamentos por empresa por mês |
| nescon-clientes | `ECAC_API_URL` | ex.: `https://ecac.gestaoempresa.com` (sem `/api`) |
| nescon-clientes | `ECAC_INTEGRACAO_TOKEN` | o **mesmo** valor de `INTEGRACAO_TOKEN` |

Os dois sistemas rodam em projetos Easypanel separados: `localhost` de um não é o
`localhost` do outro. Use a URL pública com HTTPS (ou coloque os dois na mesma rede).

## Rotas (`/api/interno`, todas exigem o token)

| Método | Rota | Custo | O que faz |
|---|---|---|---|
| GET | `/ping` | 0 | saúde + teto de gasto |
| GET | `/pendencias?desde=AAAA-MM-DD` | 0 | carteira ativa: último relatório de cada empresa, débitos classificados |
| GET | `/pendencias/<cnpj>` | 0 | uma empresa |
| POST | `/das/emitir` | **pago** (ou 0 se reaproveitar) | `{cnpj, tipo: SN\|MEI\|DCTFWEB, periodo_apuracao: AAAAMM, data_consolidacao?: AAAAMMDD, categoria?, origem, solicitado_por, forcar?}` |
| GET | `/das/emissoes?cnpj=&desde=` | 0 | eventos de emissão (o portal lê para saber que o cliente recalculou) |
| GET | `/das/<id>/pdf?cnpj=` | 0 | PDF guardado; `cnpj` diferente do da emissão → 403 |
| POST | `/reprocessar` | agenda (pago quando rodar) | `{cnpj, dias_uteis: 5, motivo, origem}` |
| GET | `/reprocessamentos?cnpj=&pendentes=1` | 0 | fila |

### O que cada débito traz

```json
{"tipo": "SN", "receita": "SIMPLES NAC.", "periodo_apuracao": "07/2026",
 "periodo_aaaamm": "202607", "data_vencimento": "2026-08-20",
 "valor_original": 500.0, "saldo_devedor_total": 515.0, "situacao": "DEVEDOR",
 "em_atraso": true, "valido": true, "motivos": [],
 "guia": "SN", "recalculo_disponivel": true}
```

* **`em_atraso`** = `DEVEDOR` **e** vencimento anterior a hoje. `A ANALISAR-A VENCER`
  nunca é atraso.
* **`valido: false`** = linha fantasma do leitor de PDF (competência `0001`, valor zero,
  sem vencimento…). O portal não notifica; mostra ao escritório.
* **`relatorio_recente`** (por empresa) = `data_hora` do relatório ≥ `desde`. Empresa
  pulada no lote (procuração travada, teto) fica `false` — o portal não cobra em cima de
  relatório velho.
* **`parcelamento` / `pgfn`** são texto livre, sem valor: a mensagem ao cliente só pode
  orientar contato com o escritório.

### Recalcular guia por tipo

| Tipo | Como | Vencida? |
|---|---|---|
| DAS Simples (`SN`) | `PGDASD/GERARDAS12` + `dataConsolidacao` | sim |
| DAS MEI (`MEI`) | `PGMEI/GERARDASPDF21` + `dataConsolidacao` (opcional, desvio 18) | ⚠️ **validar em produção** com uma competência vencida antes de liberar no portal |
| DARF DCTFWeb (`DCTFWEB`) | `DCTFWEB/GERARGUIA31` por competência | **não** — a API não aceita data; a rota recusa `data_consolidacao` e o portal manda o cliente falar com o escritório |

### Travas antes de pagar

1. guia guardada e ainda válida (vencimento ≥ hoje, mesma data pedida) → serve do disco;
2. mapa de procurações + teto mensal (as mesmas do lote);
3. limite diário por empresa/competência.

## Fila de reprocessamento

O portal pede `POST /reprocessar {cnpj, dias_uteis: 5}` quando o cliente recalcula a
guia. O agendador (`scheduler.py`) drena a fila a cada ciclo, depois dos módulos:
* uma empresa tem no máximo **um** pedido pendente (pedido novo só antecipa);
* falha adia para o próximo dia útil, até 3 vezes;
* teto atingido → para e tenta no próximo ciclo;
* limite mensal por empresa (`INTEGRACAO_MAX_REPROC_MES`).

Acompanhe em `/agendamento` (seção "Reprocessamentos pedidos pelo portal").

## Correções que vieram junto (revisão do plano)

* **Retomada após queda** — o lote grava checkpoint **antes de cada empresa**; se o
  container cair no meio, a execução seguinte continua da empresa que faltava (antes,
  repetia a carteira inteira e pagava de novo).
* **Ajustar para dia útil** — opção no agendamento mensal (padrão ligado para situação
  fiscal): dia 25 num sábado roda na segunda. Feriados nacionais em
  `services/calendario_util.py`, sem dependência externa.
* **Rotas `/api/das/*` do exe** passam a guardar PDF + evento em `das_emissoes`
  (best-effort; a resposta continua a mesma).

## Teste

```bash
python -m pytest tests/test_integracao_interno.py -q
```

Antes de ligar em produção: emitir **uma** guia SN vencida e **uma** MEI vencida pela
rota interna e conferir o PDF; só então liberar a tela no portal.
