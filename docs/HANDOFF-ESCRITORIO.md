# HANDOFF — Área Escritório (desvio 17)

Documento para o próximo agente (Claude) retomar o trabalho **sem perder contexto**.
Atualizado em 15/09/2026.

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

## Fluxo que já funciona (usuário real)

1. Cadastrar empresa (ex.: Rafael Anderson CNPJ `50.006.293/0001-92`)
2. **Escritório → Ler XML NFe** (`/escritorio/xml/nfe`)
3. Carregar ZIPs (venda + CT-e), informar mês/ano/CNPJ base (8 dígitos)
4. Gerar relatório → Simular PGDAS-D / PIS-COFINS / duplicidades / PDF / Excel
5. **Salvar na Memória** → grava em `escritorio_lancamentos` com `company_id` se CNPJ cadastrado
6. **Lançamentos** (`/escritorio/simples/lancamentos`) lê a memória, edita, marca OK / transmitido manual, **LER XML** reabre o leitor com mês/ano/CNPJ
7. **Transmitir** (`/escritorio/simples/transmitir`) **lista** a mesma memória (receita real).  
   **Calcular DAS / Enviar lote / etc. ainda NÃO estão ligados** (próxima etapa SERPRO)

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
| NCM × CST `/escritorio/ncm` | **Funcional** (admin edita; ~89 regras carga inicial — Jean deve revisar; autopeças faltando) |
| Lançamentos `/escritorio/simples/lancamentos` | **Funcional** sobre a memória |
| Transmitir `/escritorio/simples/transmitir` | Lista real; **ações SERPRO pendentes** |
| Caminhos / ÚTEIS / Config / RBT12 / NFS-e | UI sem mock de empresa; ações ainda “Ação pendente” |
| Cards sem rota (Saldo Sefaz, Folha, Regime, etc.) | Só toast “ainda não modelada” |
| DEFIS / MIT / Situação Fiscal / LUCROS | Bloqueados (como no Integra) |

## Arquivos principais (não alterar `app/models.py`)

```text
app/routes/escritorio.py          # blueprint /escritorio + APIs
app/escritorio_models.py          # EscritorioNcmCst, EscritorioLancamento
app/services/escritorio_ncm.py    # CST por NCM + carga inicial
app/services/escritorio_lancamentos.py  # listar/salvar conferência
app/templates/escritorio/         # painel + telas + ler_nfe.js
app/services/permissoes.py        # rotina + item de menu
app/__init__.py                   # registro do blueprint
docs/ARQUITETURA.md               # desvio 17
README.md
```

### Modelo `EscritorioLancamento` (campos relevantes)

- Chave: `cnpj` + `competencia` (AAAA-MM) + `perfil` (hoje `comercio`)
- Valores PGDAS: `rec_sem_st`, `rec_sem_st_isencao`, `rec_com_st`, `rec_monofasica`, `rec_com_st_mono`, `outras_receitas`, `devolucoes`, `saldo_sefaz`, `total_receita`
- Flags: `ok` (bool), `transmitido` (0 / 1 SERPRO / 2 manual)
- `company_id`, `origem` (`xml_nfe`), `detalhe_json`
- Colunas novas migradas em `_preparar_tabelas` do blueprint (não mexer em `models.py`)

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

## Decisões / regras de produto

1. **Proibido mock** nas listas (Padaria etc. removidos). `_contexto()` usa `Company` + memória.
2. Tabelas do Escritório **fora** de `app/models.py` (regra 3 da arquitetura).
3. XMLs de NF-e são lidos **no navegador** (JSZip); servidor só CST + gravação.
4. Canceladas (cStat 101 / evento 110111) **não** são receita.
5. Avisos de botão pendente: título “Ação pendente” — **não** dizer “dados de exemplo”.
6. Em Transmitir, mensagem específica: lista real; DAS/envio = SERPRO (próxima etapa).

## Próximo passo pedido pelo Jean

**Etapa 2 — Transmitir / Calcular DAS / Enviar lote**  
Ele vai passar as diretrizes do que depende do servidor (SERPRO / Integra).  
Não inventar chamada SERPRO sem a especificação dele.

Ordem natural sugerida depois disso:

1. Calcular DAS + log real (sem protocolo fake)
2. Enviar lote / retificar / emitir DAS / ZIP
3. Ler XML NFS-e (serviços / Fator R)
4. Upload PGDAS-D RBT12 → alimentar `rbt12` / folha
5. Caminhos XML persistidos
6. Saldos / Folha / Regime (cards ainda sem tela)

## Dados locais já gravados nesta sessão (não vão no git)

- Empresa: `RAFAEL ANDERSON DA ROCHA FERRARI` — `50006293000192`
- Lançamentos: `2026-07` = 16000.89 e `2026-08` = 6049.52 (perfil comércio)
- Usuário admin local: `admin` / `admin123` (arquivo de usuários fora do git, tipicamente em instance/data)

## Git

Commit desta entrega na `main` com a área Escritório completa até Lançamentos + lista real em Transmitir.
Se aparecer `.git/index.lock` / `.git/index.lock.remover`, apagar o lock vazio e seguir.

## Teste rápido pós-pull

```text
1. main_production.py → login admin
2. /escritorio → Lançamentos → competência 2026-08 → Rafael 6.049,52
3. /escritorio/simples/transmitir?competencia=2026-08 → mesma receita; sem Padaria
4. Calcular DAS → alerta “Ação pendente” (SERPRO ainda não ligado)
```
