# Operação — atualização de dados, custos e backup

---

## 1. Como o sistema atualiza os dados (a pergunta do DAS pago)

> *"Empresa devia um DAS, mas no mês seguinte paga. Esse DAS já não está no relatório —
> ele some das pendências na interface?"*

**Sim. A lógica atual já faz isso corretamente** — conferido no código, não é suposição.

**Como funciona.** Cada processamento de situação fiscal **cria um novo registro** em
`relatorios_sitfiscal`, com os débitos e pendências daquele PDF pendurados nele
(`relatorio_id`). Nada é sobrescrito. E todas as telas leem **apenas o relatório mais
recente de cada empresa**:

| Onde | Como escolhe |
|---|---|
| Painel (`/api/dashboard/summary` e `/companies`) | `func.max(RelatorioSitFiscal.id)` agrupado por empresa |
| Pendências / débitos / diagnóstico de uma empresa | `order_by(RelatorioSitFiscal.id.desc()).first()` |

Então, ao reprocessar a empresa depois do pagamento, o PDF novo não traz mais aquele
débito → ele **desaparece do painel automaticamente**, sem precisar apagar nada. O
relatório antigo continua guardado, o que preserva o histórico ("em 15/07 ela devia X").

**A ressalva que importa:** o débito só some **depois de reprocessar aquela empresa**.
O sistema não adivinha pagamento. Por isso existe o agendamento automático: a rotina de
situação fiscal já vem ligada **mensalmente no dia 25** e mantém o painel em dia sem
ninguém clicar. Ver [AUTOMACAO.md](AUTOMACAO.md).

### Os outros módulos

| Módulo | Comportamento |
|---|---|
| **Situação fiscal** | histórico versionado; a tela mostra o último (acima) |
| **Parcelamentos** | *upsert*: procura `company_id + tipo + numero` e atualiza; não duplica |
| **Caixa postal** | acumulativo por `company_id + isn` (mensagem antiga não some — e nem deve) |
| **Pagamentos** | acumulativo, com marcação de `exportado` |

### Efeito colateral a vigiar

Relatórios e PDFs **acumulam**: cada processamento gera uma linha nova e um PDF de
30–160 KB em `/data/reports/`. Com 72 empresas processadas todo mês são ~900 PDFs/ano
(~50 MB/ano) — tranquilo por bastante tempo, mas sem rotina de expurgo. Ver melhoria #6.

---

## 2. O que é grátis e o que é pago na API

Direto do frontend original do exe:

| Serviço | Código | Custo |
|---|---|---|
| Indicador de mensagens novas | `INNOVAMSG63` | **GRATUITO** |
| Lista de mensagens | `MSGCONTRIBUINTE61` | pago |
| Detalhe da mensagem | `MSGDETALHAMENTO62` | pago |
| Situação fiscal, parcelamentos, pagamentos | vários | pago (consulta) |
| DAS / DARF | vários | pago (emissão) |

**Preço:** consulta R$ 0,24 nas primeiras 300 do mês, R$ 0,21 depois. Emissão R$ 0,32 nas
primeiras 500, R$ 0,29 depois. (`app/services/api_usage_service.py`)

### Antes de qualquer rodada em lote

```bash
python scripts/estimar_custo_caixa_postal.py
```

Com 72 empresas: o monitoramento inteiro custa **R$ 0,00**; só as empresas com mensagem
nova geram custo (~R$ 3,36 por rodada com 10% delas). A **primeira carga** é a cara —
baixa a caixa inteira de quem nunca foi lido (até ~R$ 266 se todas tiverem novidade).
Comece com uma empresa (`company_ids: [68]`), depois um grupo pequeno.

### As três travas de economia já implementadas

1. **Mapa de procurações** (`/procuracoes`): empresa recusada pela SERPRO ou com 2 erros
   seguidos fica **travada para chamadas pagas** por 24 h. A sonda gratuita continua e
   reabilita sozinha quando voltar a funcionar.
2. **Detalhe já baixado não é rebaixado**: só desce de novo se a mensagem for nova ou se
   algo mudou nela (data de leitura, ciência etc.).
3. **Teto mensal de gasto** (`/agendamento`): ao atingir, os lotes param, avisam e
   **retomam de onde pararam** na execução seguinte, sem repetir empresa já consultada.

---

## 3. Backup e restauração

```bash
# manual
python scripts/backup_dados.py --manter 14

# no servidor (cron diário, 3h)
0 3 * * * docker exec central-ecac python scripts/backup_dados.py --manter 14
```

O banco é copiado com `sqlite3.backup()` — snapshot consistente mesmo com o sistema em
uso; copiar o `.db` na mão durante uma escrita pode gerar arquivo corrompido.

**Tire cópia para fora da VPS periodicamente** — volume Docker não protege contra perda
do servidor:

```bash
docker cp central-ecac:/data/backups ./backups-vps
```

Restauração: descompacte o zip, copie `instance/integra_contador.db` de volta para o
volume e reinicie o container (passo 9 do [guia de deploy](DEPLOY_EASYPANEL.md)).

---

## 4. Verificações de rotina

| Quando | Comando | Esperado |
|---|---|---|
| Após cada deploy | `curl -u USER:SENHA https://.../healthz` | `status: ok`, `auth: ligada` |
| Após mexer no código | `python scripts/auditar_estrutura.py` | só os desvios conhecidos |
| Após mexer em rotas | `python scripts/auditar_rotas.py` | 0 FALTA / 0 SOBRA |
| Com o servidor no ar | `python scripts/varrer_endpoints.py` | 0 falhando |

As auditorias comparam o código com o bytecode do exe, que fica **fora do repositório**
em `../_ARQUIVO/engenharia_reversa/`.
