# Melhorias sugeridas

Lista priorizada. Os itens marcados **✅ FEITO (02/08/2026)** já estão no código e
testados; os demais seguem como sugestão.

---

## Prioridade alta

### 1. ✅ FEITO — Agendamento automático das rodadas
**Problema:** o débito pago só some do painel depois que alguém clica em processar. Hoje
tudo depende de lembrar de clicar.
**Proposta:** um agendador (APScheduler no próprio container, ou cron chamando um
endpoint) que roda o monitoramento da caixa postal diariamente (grátis) e a situação
fiscal de todas as empresas 1× por mês, escalonado.
**Ganho:** painel sempre atualizado, sem intervenção. **Esforço:** médio.
**Atenção:** a rodada de situação fiscal é paga — o agendador precisa respeitar o mapa de
procurações e ter um teto de gasto configurável.

### 2. Tirar as requisições longas do request HTTP
**Problema:** o botão "Monitorar" é síncrono: com 72 empresas a requisição fica 2–3
minutos aberta. Atrás de um proxy isso é candidato a timeout 502, e o usuário fica sem
retorno.
**Proposta:** disparar em thread (como já fazem os parcelamentos) e devolver na hora,
deixando a tela acompanhar por `/api/caixa-postal/monitorar/status`, que já existe.
**Ganho:** fim dos timeouts. **Esforço:** baixo. **Atenção:** é desvio do exe.

### 3. ✅ FEITO — Trocar HTTP Basic por login de verdade
**Problema:** Basic tem um usuário só, sem logout, sem trilha de quem fez o quê. Para um
operador é aceitável; para o escritório inteiro, não.
**Proposta:** tabela de usuários com hash (bcrypt/argon2), sessão Flask, e registrar
autor nas ações que gastam dinheiro.
**Ganho:** rastreabilidade de quem disparou chamada paga. **Esforço:** médio.

> Feito em 03/08/2026, em duas etapas: (1) tela de login com sessão, logout, bloqueio após 5 tentativas e senha em hash no volume; (2) **multiusuário** com papéis admin/operador, permissão por rotina e por empresa, convite de primeiro acesso e recuperação de senha por link de uso único. O log registra usuário e IP de cada login.

---

## Prioridade média

### 4. ✅ FEITO — Teto de gasto e alerta de custo
Um limite mensal configurável: ao ultrapassar, o sistema recusa novas chamadas pagas e
avisa na tela. Hoje `api_usage_logs` só registra depois do gasto — não há freio.
**Esforço:** baixo. **Ganho:** protege contra o clique errado num lote grande.

### 5. Migrar SQLite → PostgreSQL
SQLite atende bem 72 empresas com um operador. Passa a incomodar com vários usuários
simultâneos (escrita serializada) ou se quiser rodar 2 réplicas. O EasyPanel sobe um
Postgres em dois cliques.
**Esforço:** médio (recriar as 14 tabelas e migrar dados). **Faça só quando houver
motivo** — hoje não há.

### 6. ✅ FEITO — Backup automático e expurgo

> 03/08/2026: backup diário automático pelo agendador, botão de gerar/baixar na tela `/restaurar` e retenção dos 5 mais recentes. O expurgo de relatórios segue como script.

### 6b. Expurgo de relatórios antigos
Cada processamento guarda um PDF (30–160 KB) e um relatório. ~50 MB/ano com 72 empresas
mensais. Sugestão: manter os 12 últimos por empresa e apagar o resto (com aviso).
**Esforço:** baixo.

### 7. Padrões de erro da SERPRO alimentados automaticamente
Hoje `padroes_sem_procuracao` começa vazio e você preenche à mão quando o erro real
aparece. Poderia haver um botão em `/procuracoes`: "este erro significa falta de
procuração" — que grava o padrão sozinho.
**Esforço:** baixo. **Ganho:** classificação automática nas próximas rodadas.

### 8. ✅ FEITO — Unificar `app_data_dir()` com `DATA_DIR`
`app/utils/paths.py` ainda aponta para `%LOCALAPPDATA%`, enquanto `config.py` usa o
`DATA_DIR`. Quem usa `app_data_dir()` são os arquivos de recuperação de detalhe da caixa
postal — que hoje ficam fora do volume, ou seja, **não são preservados no servidor**.
**Esforço:** baixo. **Ganho:** consistência e recuperação funcionando no servidor.

---

## Prioridade baixa

### 9. Concluir o módulo 5 (procurador PF)
`serpro_procurador_service` continua inventado (18 funções do exe ausentes). Enquanto
`procurador_pf_habilitado` estiver desligado é código morto — mas `_build_payload`,
`_headers` e `_post` do caixa postal já chamam métodos que não existem lá. Se alguém
ligar o procurador, quebra com `AttributeError`.
**Esforço:** alto (2.144 linhas de disassembly).

### 10. Testes automatizados
Hoje a garantia vem dos três scripts de auditoria + teste manual. Um `pytest` cobrindo
parse do PDF, montagem de payload e as travas de custo evitaria regressão silenciosa.
**Esforço:** médio.

### 11. Conferir o shape das respostas de `das_routes`
As 9 rotas de DAS/DARF nunca foram comparadas com o bytecode. Foi exatamente essa classe
de bug (objeto onde o exe devolve lista) que deixou a tela de Parcelamentos em branco.
**Esforço:** baixo. Use `scripts/auditar_shape.py`.

### 12. ✅ FEITO — Página de erro amigável
Hoje uma exceção não tratada devolve o traceback padrão do Flask. Em produção, uma página
neutra + log estruturado é melhor (e não vaza caminho de arquivo do servidor).
**Esforço:** baixo.


---

## O que foi implementado em 02/08/2026 (detalhe)

| # | Entrega | Onde |
|---|---|---|
| 1 | Agendador em thread, frequências semanal/quinzenal/mensal por módulo, tela `/agendamento`. Situação fiscal já vem ligada, **mensal no dia 25**; os demais módulos começam desligados | `services/agendamento_service.py`, `scheduler.py`, `routes/agendamento.py` |
| 4 | Teto mensal de gasto, verificado **antes de cada empresa** — com **checkpoint e retomada**: interrompeu no meio, a próxima execução continua de onde parou, sem repetir (nem pagar) empresa já consultada | `services/limite_gasto_service.py`, `_lote_com_retomada()` |
| 6 | Script de expurgo com `--dry-run` por padrão; nunca toca no relatório mais recente | `scripts/expurgar_relatorios.py` |
| 8 | `app_data_dir()` passou a seguir o `DATA_DIR` — os arquivos de recuperação da caixa postal ficavam fora do volume e se perderiam a cada redeploy | `app/utils/paths.py` |
| 12 | Erro 500 devolve mensagem neutra (JSON nas rotas `/api/`, texto nas demais) e o traceback vai para o log | `app/security.py` |

**Não implementados** (e por quê): #2 tornar o monitoramento assíncrono mudaria o
contrato que a SPA compilada espera; #3 login multiusuário, #5 PostgreSQL, #9 módulo 5 e
#10 testes automatizados são trabalhos maiores, sem urgência para subir; #7 e #11 seguem
pendentes.
