# HANDOFF — Área Escritório (desvio 17)

Documento para o próximo agente (Claude) retomar o trabalho **sem perder contexto**.
Atualizado em 15/09/2026 (transmissão PGDAS-D + alinhamento HANDOFF × código + correções de custo SERPRO e estado do git).

## Objetivo do Jean

Replicar no **Central e-CAC** (`central-ecac`) as telas do **Integra Contador**
(`integracontador.api.br/.../office/`), com o mesmo visual e o mesmo fluxo de uso.
**Nada de dados mockados** nas listas: cadastro + memória reais.
Ações que dependem de SERPRO / servidor do Integra ficam para a etapa seguinte,
com diretrizes do Jean.

Referência ao vivo (precisa login do Jean):
`https://integracontador.api.br/sg3/integra_multi/office/dashboard.php`

## Como rodar local

```text
cd C:\Users\Jeandson\Documents\GitHub\central-ecac
.venv\Scripts\python.exe main_production.py
```

- URL: http://127.0.0.1:5847  
- Login local de teste (criado nesta sessão): `admin` / `admin123`  
- Banco: `instance/integra_contador.db`  
- Menu: Painel → **Escritório** (`/escritorio`)  
- Permissão: rotina `escritorio` (admin já vê; demais usuários precisam receber em Usuários)
- **Waitress não recarrega templates/Python** — reiniciar o processo após mudanças

## Fluxo que já funciona (usuário real)

1. Cadastrar empresa (ex.: Rafael Anderson CNPJ `50.006.293/0001-92`)
2. **Escritório → Ler XML NFe** (`/escritorio/xml/nfe`)
3. Carregar ZIPs (venda + CT-e), informar mês/ano/CNPJ base (8 dígitos)
4. Gerar relatório → Simular PGDAS-D / PIS-COFINS / duplicidades / PDF / Excel
5. **Salvar na Memória** → grava em `escritorio_lancamentos` com `company_id` se CNPJ cadastrado
6. **Lançamentos** (`/escritorio/simples/lancamentos`) lê a memória, edita, marca OK / transmitido manual, **LER XML** reabre o leitor com mês/ano/CNPJ
7. **Transmitir** (`/escritorio/simples/transmitir`) lista a memória + **RBT12** do histórico e faz
   **Pré-visualizar (grátis) → Calcular → Enviar/Retificar → Consultar → Gerar DAS (PDF guardado)**
   pela SERPRO (`/escritorio/api/pgdasd/*`). Ver seção **Transmissão PGDAS-D**.
   **Não** usa `/api/das` (ponte Escritório→DAS Lote foi removida).
8. **Upload PGDAS-D** (`/escritorio/simples/rbt12`) — PDF+OCR → grava histórico (vai em `receitasBrutasAnteriores`)

### Prova com arquivos do Jean (Rafael Anderson)

ZIPs em `C:\Users\Jeandson\Downloads\`:

- `venda_20260701_20260731.zip`
- `venda_20260801_20260831.zip`
- `50006293RAFAELA_CTE_20260701_20260731.zip`
- `50006293RAFAELA_CTE_20260801_20260831.zip`

| Competência | Receita (Central) | Notas | Observação |
|---|---|---|---|
| 08/2026 | **R$ 6.049,52** | 23 | bateu com Integra no centavo |
| 07/2026 | **R$ 16.000,89** | 42 | Integra mostrou R$ 16.235,98; diferença = 2 canceladas (R$ 235,09) que o nosso sistema descarta corretamente |

CT-e e inutilizações **não entram na receita** (só aviso no cabeçalho).

## O que está funcional vs pendente

| Tela / rota | Status |
|---|---|
| Painel `/escritorio` | OK — cards + modal iframe; card **NCM × CST** incluído |
| Ler XML NFe `/escritorio/xml/nfe` | **Funcional** (leitura no browser) |
| NCM × CST `/escritorio/ncm` | **Funcional** (admin edita; **88** regras na carga inicial — Jean deve revisar; autopeças faltando) |
| Lançamentos `/escritorio/simples/lancamentos` | **Funcional** sobre a memória |
| Transmitir `/escritorio/simples/transmitir` | **Código + 36 testes OK** (perfil comércio): Pré-visualizar, Calcular, Enviar, Retificar, Consultar, Gerar DAS, buscar declaração/recibo, lote, ZIP — `/escritorio/api/pgdasd/*`. **1º caso real SERPRO ainda não validado** |
| Empresas `/escritorio/empresas` | **Funcional** — ticar empresas do cadastro (`escritorio_empresas`); também checkbox **Escritório** nos cards de `/?aba=configuracoes`. Filtros (razão/CNPJ, Incluídas/Fora, Ativas/Inativas — lembrados no navegador) + **Marcar todos / Desmarcar todos** das visíveis, com confirmação (`tests/test_escritorio_empresas_lote.py`) |
| Upload PGDAS-D `/escritorio/simples/rbt12` | **Funcional** — PDF+OCR (`pgdas_leitor.js` v16) → `POST /escritorio/api/pgdas/importar` |
| Caminhos / ÚTEIS / NFS-e | UI sem mock de empresa; ações ainda “Ação pendente” |
| Configuração Escritório | **Removida** — usa Configurações do sistema (`AppSetting`); template/rota apagados |
| Cards sem rota (Saldo Sefaz, Folha, Regime, etc.) | Só toast “ainda não modelada” |
| DEFIS / MIT / Situação Fiscal / LUCROS | Bloqueados (como no Integra) |

## Arquivos principais (não alterar `app/models.py`)

```text
app/routes/escritorio.py                 # blueprint + APIs (pgdas + pgdasd)
app/escritorio_models.py                 # NcmCst, Lancamento, PgdasHistorico/Meta,
                                         # Declaracao, SerproChamada
app/services/escritorio_ncm.py
app/services/escritorio_lancamentos.py
app/services/escritorio_pgdas.py         # importar + rbt12_para
app/templates/escritorio/pgdas_leitor.js # leitor PDF+OCR (Integra v16)
app/services/serpro_pgdasd_client.py    # cliente SERPRO PGDASD: token cache, auditoria, custo, incerto
app/services/escritorio_pgdasd.py       # Calcular/Transmitir/Consultar/DAS + pré-voo + trava + lote
app/templates/escritorio/transmitir_pgdasd.js  # tela Transmitir (modal de custo + pré-visualizar)
tests/test_escritorio_pgdasd.py         # 27 testes com SERPRO simulada (sem rede/custo)
app/templates/escritorio/               # painel + telas + ler_nfe.js
app/services/permissoes.py
app/security.py                         # ponte escritorio→/api/das REMOVIDA (15/09)
docs/ARQUITETURA.md                     # desvio 17
README.md
```

**Removidos (órfãos):** `transmitir_das.js` (ponte antiga `/api/das`) e `configuracao.html`.

### Modelo `EscritorioLancamento` (campos relevantes)

- Chave: `cnpj` + `competencia` (AAAA-MM) + `perfil` (hoje `comercio`)
- Valores PGDAS: `rec_sem_st`, `rec_sem_st_isencao`, `rec_com_st`, `rec_monofasica`, `rec_com_st_mono`, `outras_receitas`, `devolucoes`, `saldo_sefaz`, `total_receita`
- Flags: `ok` (bool), `transmitido` (0 / 1 SERPRO / 2 manual)
- `company_id`, `origem` (`xml_nfe`), `detalhe_json`
- Colunas novas migradas em `_preparar_tabelas` do blueprint (não mexer em `models.py`)

### Modelos PGDAS histórico

- `EscritorioPgdasHistorico`: `cnpj` + `competencia` → `receita_bruta`, `folha_cpp`
- `EscritorioPgdasMeta`: `cnpj` + `pa` → `rbt12`, `folha12`, `rpa`, `valor_das`
- Serviço: `app/services/escritorio_pgdas.py`

### Modelos transmissão PGDAS-D

- `EscritorioDeclaracao` (`escritorio_declaracoes`): estado por CNPJ+PA (`rascunho` /
  `calculada` / `transmitida` / `incerta` / `erro`), hash do cálculo, valores devidos,
  nº declaração, PDFs (declaração/recibo/MAED/DAS), trava `operacao`+`operacao_desde`
- `EscritorioSerproChamada` (`escritorio_serpro_chamadas`): auditoria de **toda** chamada
  SERPRO do Escritório (status, códigos, duração, hash, custo estimado, cobrável?)

### Regra de diferença (comércio, igual Integra)

```text
total = rec_sem_st + rec_sem_st_isencao + rec_com_st + rec_monofasica + rec_com_st_mono
diferença = total + outras_receitas + devolucoes - saldo_sefaz
```

## APIs

| Método | Rota | Uso |
|---|---|---|
| GET | `/escritorio/api/ncm-cst?ncms=` | CST em lote para o leitor |
| GET/POST/excluir | `/escritorio/api/ncm`… | CRUD tabela NCM (POST só admin) |
| GET | `/escritorio/api/empresa?cnpj=` | Vínculo com `companies` |
| POST | `/escritorio/api/lancamentos/salvar` | Salvar na Memória (do leitor NFe) |
| POST | `/escritorio/api/lancamentos/linha` | Salvar linha da tela Lançamentos |
| POST | `/escritorio/api/empresas/toggle` | Ticar/desmarcar empresa no Escritório `{company_id, incluso}` |
| POST | `/escritorio/api/empresas/toggle-lote` | Marcar/desmarcar várias `{company_ids:[..], incluso}` — um commit; ao incluir pula inativas (`pulados_inativos`) |
| GET | `/escritorio/api/empresas` | Lista cadastro + flag `incluso` (Configurações injeta checkbox) |
| POST | `/escritorio/api/pgdas/importar` | Histórico RBT12 (payload do leitor PDF) |
| GET | `/escritorio/api/pgdas/rbt12?cnpj=&pa=` | Consulta RBT12 / suficiência |
| GET | `/escritorio/api/pgdasd/estado?cnpj=&competencia=` | **grátis** — situação, valores, pode{…}, bloqueios de configuração |
| POST | `/escritorio/api/pgdasd/pre-visualizar` | **grátis** — JSON exato da declaração + problemas (botão `</>` na UI) |
| POST | `/escritorio/api/pgdasd/calcular` | TRANSDECLARACAO11 `indicadorTransmissao=false` (pago) |
| POST | `/escritorio/api/pgdasd/transmitir` | `{retificar, hash_confirmado}` — consulta prévia + TRANSDECLARACAO11 com comparação (pago) |
| POST | `/escritorio/api/pgdasd/consultar` | CONSDECLARACAO13 (pago) — resolve envio incerto |
| POST | `/escritorio/api/pgdasd/recuperar-documentos` | `{forcar?}` — CONSULTIMADECREC14 (pago): baixa declaração/recibo/MAED da última declaração do PA (ex.: entregue no PGDAS-D web). Bloqueia se os PDFs já estão guardados |
| POST | `/escritorio/api/pgdasd/gerar-das` | `{data_consolidacao?, forcar?, confirmar_externa?}` — GERARDAS12 (pago; reaproveita PDF) |
| GET | `/escritorio/api/pgdasd/arquivo/<das\|declaracao\|recibo\|maed_notificacao\|maed_darf>` | **grátis** — PDF guardado |
| GET | `/escritorio/api/pgdasd/das-zip?competencia=&cnpjs=` | **grátis** — ZIP dos DAS guardados |
| POST/GET | `/escritorio/api/pgdasd/lote/iniciar` · `/lote/<id>` | lote em thread (para após 2 falhas sistêmicas) |

Todo POST **pago** exige `confirmar_custo: true` (senão 428). Pré-visualizar e estado **não**.
Bloqueio de pré-voo = 422 (`{ok:false, bloqueios, avisos}`), trava ocupada = 409.

## Decisões / regras de produto

1. **Proibido mock** nas listas (Padaria etc. removidos). `_contexto()` usa `Company` + memória.
2. Tabelas do Escritório **fora** de `app/models.py` (regra 3 da arquitetura).
3. XMLs de NF-e são lidos **no navegador** (JSZip); servidor só CST + gravação.
4. Canceladas (cStat 101 / evento 110111) **não** são receita.
5. Avisos de botão pendente: título “Ação pendente” — **não** dizer “dados de exemplo”.
6. Em Transmitir, toda ação paga abre modal com empresa, competência, valores e custo estimado.
7. **Sem Configuração própria no Escritório.** Card/rota/template removidos. Identidade
   (`office_name`), CNPJ do contador, certificado A1 e consumer key/secret vêm de
   **Configurações do sistema** (`AppSetting` / `/?aba=configuracoes`). Helper
   `_config_sistema()` no blueprint; painel e Transmitir mostram status SERPRO + link.
   O DAS Lote antigo (`/api/das/*`, fora do Escritório) lê o **mesmo** `AppSetting`;
   o Transmitir do Escritório **não** chama `/api/das`.
8. **RBT12 / histórico PGDAS**: vai em `receitasBrutasAnteriores`. Pela doc SERPRO a Receita
   **ignora** esse campo para períodos já declarados — ele só é obrigatório na **1ª declaração**
   da empresa no Simples. Por isso NÃO bloqueia o envio (só avisa quando há < 12 meses). Tabelas:
   `escritorio_pgdas_historico` (mês a mês) + `escritorio_pgdas_meta` (RBT12 oficial
   por PA). API: `POST /escritorio/api/pgdas/importar`.
   **Leitor PDF+OCR** em `pgdas_leitor.js` (v16, pdf.js + Tesseract) na tela
   `/escritorio/simples/rbt12` — mesmo fluxo do Integra `impext/imp.php`.

## Transmissão PGDAS-D pela SERPRO (feito 15/09/2026 — Claude)

Pedido do Jean: *"cada erro custa dinheiro"*. O desenho inteiro é para **só pagar quando
necessário** e **nunca pagar duas vezes pela mesma coisa**.

### Serviços usados (idSistema `PGDASD`, versão `1.0`)

| idServico | Endpoint | Uso no Central | Custo (tipo) |
|---|---|---|---|
| `TRANSDECLARACAO11` | `/Declarar` | Calcular (`indicadorTransmissao=false`) e Transmitir/Retificar | declarar |
| `CONSDECLARACAO13` | `/Consultar` | Consulta prévia (original × retificadora) e resolver envio incerto | consultar |
| `GERARDAS12` | `/Emitir` | PDF do DAS (guardado em `REPORTS_DIR/escritorio/pgdasd/<cnpj>/<pa>/`) | emitir |
| `CONSULTIMADECREC14` | `/Consultar` | mapeado no cliente, **ainda não ligado** (rebaixar recibo) | consultar |

Custo estimado por variável de ambiente: `SERPRO_CUSTO_CONSULTAR` (0.24),
`SERPRO_CUSTO_EMITIR` (0.32), `SERPRO_CUSTO_DECLARAR` (**0.40 = chute — confirmar no contrato**).
Tudo entra em `api_usage_logs` (mesma base do teto `LIMITE_GASTO_MENSAL`) e em
`escritorio_serpro_chamadas` (auditoria: status, códigos, duração, hash do pedido, cobrável?).

### Fluxo (tela Transmitir)

1. **Pré-visualizar** (grátis) → JSON exato + bloqueios/avisos, sem chamar a SERPRO.
2. **Estado** (grátis, automático ao abrir modal pago) → bloqueios de configuração/lançamento.
   Se houver bloqueio, NADA sai.
3. **Calcular** (1× declarar): Receita devolve `valoresDevidos`; guardamos valores + **hash do conteúdo**.
4. **Enviar** (1× declarar + 1× consultar se não houver consulta < 12 h):
   - só com o **mesmo hash** do cálculo (lançamento mudou → recalcular), cálculo < 72 h,
     checkbox "conferi" e o hash exibido na tela (`hash_confirmado`);
   - consulta antes: se a Receita já tem declaração do PA (ex.: feita no PGDAS-D web), **não transmite**
     e marca "Env. Manual" (`transmitido=2`);
   - envia com `indicadorComparacao=true` + `valoresParaComparacao` → se a Receita apurar 1 centavo
     diferente, **não transmite** (MSG_ISN_035) e o cálculo é invalidado;
   - sucesso: guarda nº da declaração, PDFs (declaração, recibo, MAED) e `transmitido=1`.
5. **Gerar DAS** (1× emitir): só com declaração transmitida/confirmada (ou o usuário confirma que
   declarou fora). PDF guardado; clicar de novo **reaproveita sem custo**. Guia vencida → pede data
   de pagamento (`dataConsolidacao`) e emite nova.
6. **Timeout depois de enviar = INCERTO**: bloqueia Calcular/Enviar/DAS até **Consultar** dizer se a
   declaração entrou. Nunca há retry automático de negócio (só 1 renovação de token em 401/403).

### Proteções técnicas

- Pré-voo grátis: configuração (procurador PF desligado, CNPJ contador, consumer key/secret,
  .pfx abre com a senha e não está vencido), DV do CNPJ, empresa cadastrada/ativa, PA ≥ 2018-01 e
  não futuro, trava de procuração (`ProcuracaoService`), teto de gasto, lançamento OK e fechado.
- Trava por empresa/PA no banco (UPDATE atômico, expira em 15 min) contra duplo clique/duas abas.
- Token SERPRO reaproveitado por 20 min (`SERPRO_TOKEN_TTL_S`).
- `EntradaIncorreta` (erro do nosso JSON) **não** conta como erro de procuração.
- Lote: checa o teto para o total antes; cada empresa passa pelo pré-voo (pulada sem custo);
  **para após 2 falhas sistêmicas seguidas** (rede/5xx/401/MSG_ISN_001/012/022/031).
- Rotas checam a empresa do usuário restrito; PDFs servidos só de dentro da pasta da empresa.
- Usuário só com rotina `escritorio` **não** acessa `/api/das` (só `pgdasd`) — esperado.

### Mapeamento memória → declaração (perfil comércio)

| Lançamento | PGDAS-D |
|---|---|
| `rec_sem_st` | atividade **1** |
| `rec_com_st_mono` | atividade **2**, `qualificacoesTributarias`: COFINS 1004 e PIS 1005 id **9** (monofásica) + ICMS 1007 id **8** (ST) |
| `rec_monofasica` | atividade **2**, `qualificacoesTributarias`: COFINS/PIS id **9** |
| `rec_com_st` | atividade **2**, `qualificacoesTributarias`: ICMS id **8** |
| `rec_sem_st_isencao` | **bloqueia** (isenção/redução de ICMS exige percentual) |

Formato: `{"codigoTributo": 1004, "id": 9}` dentro de `receitasAtividade[].qualificacoesTributarias`.
Ids: 1 Imunidade, 3 Lançamento de ofício, 8 ST, 9 Monofásica, 10 Antecipação, 11 Retenção ISS.

**Histórico (15/09/2026, 1º Calcular real no servidor):** a 1ª versão mandava ST/monofásica em
`isencoes` (`{codTributo, valor, identificador}`) e a SERPRO recusou:
`SN-Entregar: Campo 'isencao/identificacao' inválido.` Trocado para `qualificacoesTributarias`
(inferência pela mensagem + modelo de cliente público; a doc oficial bloqueia leitura automática).
**Próximo Calcular valida.** Se voltar erro de campo, conferir a tabela de domínio no apicenter
da SERPRO antes de repetir.

**Proteção nova:** pedido IDÊNTICO (mesmo hash) já recusado por erro de preenchimento
(`EntradaIncorreta` ou "Campo 'x' inválido") nos últimos 7 dias **não é reenviado** — o cliente
devolve o motivo sem chamar a SERPRO. Esse tipo de erro também não conta para a trava de
procuração. Mudou o lançamento → hash novo → pode enviar.

**Caso real 15/09/2026 17:55 (Rafael 08/2026):** Calcular OK com `qualificacoesTributarias`; o Enviar
voltou `SN-Entregar: Houve um problema na transmissão. Tente novamente mais tarde.` (HTTP normal,
cobrado). Tratamento: texto de instabilidade conta como **sistêmico** (lote para) e a declaração
perde a "consulta recente" → o próximo Enviar faz CONSDECLARACAO13 antes e só transmite se a Receita
não tiver declaração do PA. Situação continua `calculada` (a doc diz que erro não grava nada).

### Pendências / limitações conhecidas

- **Validar 1º caso real** (Rafael 08/2026): Consultar → Pré-visualizar → Calcular → conferir web → Enviar.
- Só perfil **comércio**; serviços/indústria/Fator R (folha, atividades 10–18 etc.) bloqueiam.
- Só o CNPJ do lançamento em `estabelecimentos` (filiais não entram) e só regime de **competência**.
- Procurador PF: fluxo real (`auth_headers`/`build_payload`) **continua não implementado** (módulo 5).
  Desde 15/09 ele falha ANTES do envio, com mensagem clara e sem travar procuração (ver correções abaixo).
- Caminhos / NFS-e / ÚTEIS / cards sem rota → só UI.
- `SERPRO_CUSTO_DECLARAR=0.40` = chute — confirmar no contrato.

### Correções de 15/09/2026 (tarde — Claude) — custo SERPRO fora do fluxo PGDAS-D

Pedido do Jean: "faça as correções" dos pontos levantados. Tudo coberto por testes
(`tests/test_escritorio_pgdasd.py`, 31 testes).

| Problema | Correção | Arquivo |
|---|---|---|
| Procurador PF ligado estourava `AttributeError` (`auth_headers`, `build_payload`, `invalidate_authorization_token` não existiam) em DAS, Caixa Postal, Pagamentos, Parcelamentos — e o erro contava para a trava de procuração | Métodos criados: falham com `ErroAntesDoEnvio` ("Procurador PF ligado, mas não implementado… desligue") **antes** de qualquer requisição | `services/serpro_procurador_service.py`, `services/serpro_erros.py` (novo) |
| Erro de configuração local travava a empresa por 24 h | `ProcuracaoService.registrar_erro` guarda o erro mas **não conta** `ErroAntesDoEnvio` | `services/procuracao_service.py` |
| `SerproDasService` autenticava a cada emissão (lote de N = N autenticações) e só renovava token no caminho procurador | Token A1 em cache (`CACHE_TOKEN`, 20 min, `SERPRO_TOKEN_TTL_S`), chave = consumer key + contador + certificado; em 401/403 invalida e tenta **1** vez. Validações de Configurações viram `ErroAntesDoEnvio` | `services/serpro_das_service.py` (`_get_headers` → `_gerar_headers_a1`), `services/serpro_pgdasd_client.py` (`chave_token`) |
| `/api/das/emitir`, `/api/das/mei/emitir`, `/api/das/dctfweb/emitir` não registravam custo nem checavam teto/procuração; lotes não checavam antes | Antes da chamada: `ProcuracaoService.pode_gastar` + `LimiteGastoService.pode_gastar` (409 sem custo). Avulsos registram `ApiUsageLog` (emitir). Lotes checam teto para o total e pulam empresa travada | `routes/das_routes.py` |
| `CONSULTIMADECREC14` mapeado mas não ligado | `recuperar_documentos()` + rota + botões 📄/🧾 da linha: se o PDF não está guardado e a declaração é conhecida, oferecem "Buscar na Receita (pago, R$ 0,24)" | `services/escritorio_pgdasd.py`, `routes/escritorio.py`, `transmitir.html`, `transmitir_pgdasd.js` |

Ainda **sem** proteção de teto/cache: `serpro_service.py` (situação fiscal), `caixa_postal_service.py`,
`serpro_pagamentos_service.py`, `parcelamentos_serpro_service.py` (autenticam por conta própria).

### Rodar os testes

```text
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest tests/test_escritorio_pgdasd.py -q   # 31 testes, sem rede
```

## Dados locais já gravados nesta sessão (não vão no git)

- Empresa: `RAFAEL ANDERSON DA ROCHA FERRARI` — `50006293000192`
- Lançamentos: `2026-07` = 16000.89 e `2026-08` = 6049.52 (perfil comércio)
- Usuário admin local: `admin` / `admin123` (arquivo de usuários fora do git, tipicamente em instance/data)

## Git

Repositório: `github.com/contabilidade-01/central-ecac`. **Deploy (EasyPanel) usa `main`.**

| Commit | Conteúdo | Onde |
|---|---|---|
| `08d6859` | Área Escritório: NFe, NCM, Lançamentos | `main` |
| `92e14be` | Transmitir PGDAS-D via SERPRO + RBT12 + pré-visualizar | `main` via PR #1 (`03a6584`) |
| `37a76d1` | Ticar empresa em Configurações para o Escritório | **só** `cursor/escritorio-pgdasd-serpro` (feito depois do merge do PR #1) |
| *(este)* | Correções de custo SERPRO (procurador, token, `/api/das`, recuperar documentos) + HANDOFF | commit local na branch + merge local em `main` — **falta push** |

- Commits locais feitos pela sessão Claude (15/09); o **push** tem de sair da máquina do Jean
  (a VM do Claude não tem credencial do GitHub): `git push origin cursor/escritorio-pgdasd-serpro main`.
- Arquivos com ` M` no `git status` que só mudam fim de linha (CRLF×LF) **não** são alteração real —
  conferir com `git diff --ignore-cr-at-eol --stat` antes de commitar.
- Se aparecer `.git/index.lock` ou `.git/objects/maintenance.lock` sem git rodando, apagar e seguir
  (há `index.lock.remover*` antigos na pasta `.git` que podem ser apagados).

## Teste rápido pós-pull

```text
1. main_production.py → login admin (reiniciar Waitress após mudanças de Python/template)
2. /escritorio → Lançamentos → 2026-08 → Rafael 6.049,52 → marcar OK
3. Upload PGDAS-D → PDF Extrato/Declaração → Importar (empresa cadastrada)
4. Transmitir?competencia=2026-08 → </> Pré-visualizar (grátis) → conferir JSON
5. 🔍 Consultar (R$ 0,24) → vê se a Receita já tem declaração
6. 🧮 Calcular → conferir valores com o PGDAS-D web ANTES de ✈ Enviar
7. Gerar DAS → PDF guardado; 👁/⬇ abrem sem custo
8. Declaração/recibo entregues fora do Central: 📄/🧾 "Buscar na Receita" (R$ 0,24) → guardados
```
