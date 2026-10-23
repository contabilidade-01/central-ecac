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

Outro consumidor da mesma API: o **AtendeChat** (envio da guia dentro da conversa do
WhatsApp, sob demanda do atendente — `origem: atendechat`). Não muda nada do portal.

## Rotas (`/api/interno`, todas exigem o token)

| Método | Rota | Custo | O que faz |
|---|---|---|---|
| GET | `/ping` | 0 | saúde + teto de gasto |
| GET | `/empresas?busca=` | 0 | até 20 empresas por razão social ou CNPJ (vínculo contato → empresa no AtendeChat) |
| GET | `/empresa/<cnpj>` | 0 | cadastro: razão social, ativa, é MEI, parcelamentos acompanhados |
| GET | `/pendencias?desde=AAAA-MM-DD` | 0 | carteira ativa: último relatório de cada empresa, débitos classificados |
| GET | `/pendencias/<cnpj>` | 0 | uma empresa |
| POST | `/das/emitir` | **pago** (ou 0 se reaproveitar) | `{cnpj, tipo: SN\|MEI\|DCTFWEB, periodo_apuracao: AAAAMM, data_consolidacao?: AAAAMMDD, categoria?, origem, solicitado_por, forcar?, apenas_reuso?}` — `apenas_reuso: true` só devolve guia já guardada e válida; sem ela, **402 sem chamar a SERPRO** |
| GET | `/das/emissoes?cnpj=&desde=` | 0 | eventos de emissão (o portal lê para saber que o cliente recalculou) |
| GET | `/das/<id>/pdf?cnpj=` | 0 | PDF guardado; `cnpj` diferente do da emissão → 403 |
| POST | `/reprocessar` | agenda (pago quando rodar) | `{cnpj, dias_uteis: 5, motivo, origem}` |
| GET | `/reprocessamentos?cnpj=&pendentes=1` | 0 | fila |
| GET | `/parcelas/<cnpj>?tipo=PARC_SN` | 0 | última consulta de parcelas |
| POST | `/situacao/confirmar` | **pago** (~R$ 0,32; um por vez; `INTEGRACAO_MAX_CONFIRMACOES_DIA`=2) | relatório de situação fiscal NA HORA para uma empresa ("Confirmar pendências", desvio 20) — roda em thread |
| GET | `/situacao/<cnpj>/confirmacao` | 0 | status da última confirmação (`em_andamento`/`concluida`/`erro`, `relatorio_data`) |
| POST | `/parcelas/consultar` | **pago** (ou 0 se já consultado hoje) | `{cnpj, tipo, forcar?, solicitado_por?}` |
| POST | `/acesso-links` | 0 | `{cnpj, origem: atendechat, solicitado_por?, dias?}` → `{url, link}`: link de guias **sem senha servido por este sistema** (desvio 21). Reaproveita link válido por ≥ 3 dias |
| GET | `/acesso-links?cnpj=` | 0 | últimos links da empresa (acessos, guias geradas, validade) |
| POST | `/acesso-links/revogar` | 0 | `{cnpj}` → revoga os links ativos da empresa |

## Página pública de guias sem senha — `/g/<token>` (desvio 21)

Pedido do Jean (23/10/2026): o AtendeChat manda o link na conversa e o cliente gera a
guia recalculada **aqui**, sem o portal do cliente no caminho. O portal continua com o
`/g/` dele, no domínio dele, com a tabela dele — nada do que o portal faz é tocado.

| Rota | Faz |
|---|---|
| `GET /g/<token>` | página para celular: empresa, data do relatório, débitos **válidos e em atraso** com "vou pagar em [data]" + **Gerar guia atualizada**; a vencer com data opcional; **Outra competência** (tipo + mês + data) para a guia que ainda não está no relatório — venceu depois dele ou foi declarada depois; avisos de parcelamento/PGFN/omissão |
| `GET /api/acesso/<token>` | JSON da página |
| `POST /api/acesso/<token>/parcelas/consultar` `{tipo}` | consulta paga das parcelas (a do dia é reaproveitada); tipos: os marcados no cadastro + os que o relatório cita |
| `POST /api/acesso/<token>/guias` `{debito_id, data_pagamento?}`, `{tipo: SN\|MEI\|DCTFWEB, competencia: AAAAMM, data_pagamento?}` ou `{tipo: PARC_SN…, parcela: AAAAMM}` | emite pelo `emitir` (mesmas travas: procuração, teto, reuso); `origem='link'`, `solicitado_por='link:<token curto>'`. Tipos livres: MEI → só DAS MEI; demais → SN e INSS (inclusive 13º); competência do mês atual até 36 meses atrás |
| `GET /api/acesso/<token>/guias/<id>/pdf` | PDF inline; só emissão da empresa do token |

Segurança: token de 12 bytes (24 hex — o de 64 chegava cortado no WhatsApp do celular; os antigos seguem válidos), escopo só guias daquela empresa, vence sozinho
(`LINK_GUIAS_DIAS`, 35), revogável; a página nunca recebe CNPJ nem id de emissão de fora
e só emite débito que consta no último relatório; token errado conta como tentativa do
IP (5 → bloqueio de 5 min, como o login). Data de pagamento: hoje a +30 dias, dia útil.
Limite por token: `LINK_GUIAS_MAX_GERACOES_DIA` (2) emissões **pagas** por competência
por dia — reuso do PDF não conta. As emissões do link **não contam** no limite diário do
portal (`INTEGRACAO_MAX_EMISSOES_DIA`): o que o cliente gera por aqui nunca bloqueia o
portal nem o escritório. Rodapé: `ESCRITORIO_NOME` e `ESCRITORIO_WHATSAPP` (opcionais).

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
| DARF DCTFWeb (`DCTFWEB`) | `DCTFWEB/GERARGUIA31` por competência + `dataAcolhimentoProposta` (desvio 19): **uma DARF com todos os códigos** (1082, 1099…) | sim — data de pagamento ≥ hoje; ⚠️ validar em produção com uma competência vencida |
| Parcela de parcelamento (`PARC_SN`, `PARC_MEI`, `PERT_*`, `RELP_*`) | `POST /parcelas/consultar` (`PARCELASPARAGERAR`, paga, 1×/dia reaproveitada) e depois `/das/emitir` com `periodo_apuracao` = parcela (`GERARDAS16x…`) | a Receita calcula para o mês da emissão; só emite parcela que consta na última consulta |
| PGFN (SISPAR) | não existe no Integra Contador | emitir no Regularize |

DARF DCTFWeb e parcela: a resposta da SERPRO não traz vencimento nem valor; o
Central e-CAC lê "Pagar este documento até" e "Valor Total do Documento" do próprio PDF
(melhor esforço; PDF ilegível sai sem esses campos).

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
python -m pytest tests/test_integracao_interno.py tests/test_acesso_links.py -q
```

Antes de ligar em produção: emitir **uma** guia SN vencida e **uma** MEI vencida pela
rota interna e conferir o PDF; só então liberar a tela no portal.
