# Revisão do plano "Notificação de Pendências e-CAC (Email + WhatsApp + Portal)"

**Documento revisado:** `STATUS_NOTIFICACOES_ECAC.md` (datado 30/09/2026)
**Data da revisão:** 29/09/2026
**Base da revisão:** código atual de `central-ecac` e `nescon-clientes` (branch `main` e todas as branches remotas)

> **Implementado em 29/09/2026** conforme as indicações abaixo (fases 1 a 3 e parte da 4):
> ver `docs/INTEGRACAO_NESCON.md` (central-ecac) e `docs/IMPOSTOS-ECAC.md` (nescon-clientes).
> O texto a seguir é a revisão original, mantida como registro do porquê de cada decisão.

---

## 0. Veredito em cinco linhas

1. **O documento é um plano, não um status.** Os commits `b6a7f9a` e `2ef644c` não existem em nenhuma branch dos dois repositórios, e nenhum dos arquivos listados (`email_service.py`, `calendario_util.py`, `portal_routes.py`, `interno.js`, `ecac.js`, migration) existe. A data do documento é amanhã. Tudo abaixo trata o texto como proposta.
2. **Como plano, cobre só o disparo inicial** (um e-mail, um WhatsApp, botão de gerar guia). Não cobre recálculo com registro, regeração do relatório 5 dias depois, monitoramento de abertura, lembrete sem regerar, cobrança escalonada nem o encerramento quando o débito some. Isso é a maior parte do que você pediu.
3. **Três decisões do plano conflitam com regras já escritas nos repositórios:** alterar `models.py` (regra 3 do `ARQUITETURA.md`), migration SQL manual (nescon exige `ensure*.js` no boot) e envio de WhatsApp disparado de fora do portal (cabeçalho de `fiscalIngest.js`, desligado em set/2026 justamente para não haver envio paralelo na mesma instância uazapi).
4. **A arquitetura está de cabeça para baixo.** O plano copia contatos do cliente para o central-ecac e faz o central-ecac mandar e-mail. O dono do cliente, do contato, do canal, da janela de envio, da lista de permitidos e do histórico é o nescon-clientes. O central-ecac deve ser só a fonte das pendências e o emissor de guias.
5. **Factível, sim, mas em fases.** A base existe: relatório de situação fiscal mensal com histórico versionado, teto de gasto, `GERARDAS12` com data de consolidação, uazapi com fila e retentativa, JWT por empresa no portal. O que falta é a "máquina de estados" da cobrança e os eventos que a alimentam.

---

## 1. O que existe hoje (fatos conferidos no código)

### central-ecac
| Item | Situação real |
|---|---|
| Contato do cliente | `companies` não tem e-mail, telefone nem WhatsApp (`app/models.py:34-60`). `ARQUITETURA.md` regra 3 proíbe alterar `models.py`; tabelas novas vão em `escritorio_models.py`. |
| Pendências | Vêm **só** do PDF do Relatório de Situação Fiscal, lido por regex (`pdf_parser.py:268-327`). Cada débito tem tipo (SN/MEI/INSS/MAED/IRRF/Outros), período, vencimento, valor original, saldo, multa, juros, saldo total, situação. **Não há código de barras.** Parcelamento e PGFN são texto livre, sem valor (`report_service.py:444-491`). |
| Histórico | Cada execução cria um `relatorios_sitfiscal` novo; as telas leem `max(id)` por empresa. Reprocessar PDF salvo é grátis (`scripts/reprocessar_pendencias.py`). |
| Agendador | Thread no processo web, tick de 5 min, **1 worker gunicorn** obrigatório. Frequências: semanal, quinzenal, mensal (dia 1 a 28). Situação fiscal ligada por padrão: dia 25, 03:00. Não existe dia útil nem feriado. Loop sequencial, `time.sleep` bloqueante entre protocolo e relatório, checkpoint só para teto de gasto. |
| Emissão de guias | `PGDASD/GERARDAS12` aceita `dataConsolidacao` (data futura obrigatória, `serpro_das_service.py:97-121`). `PGMEI/GERARDASPDF21`: o código atual **ignora** a data. `DCTFWEB/GERARGUIA31`: sem data de consolidação. As rotas `/api/das/*` **não guardam o PDF**; só o fluxo Escritório guarda e reaproveita (`escritorio_pgdasd.py:569-590`). |
| Autenticação | Sessão Flask ou HTTP Basic, um `before_request` único (`security.py`). **Não existe token máquina-a-máquina.** CORS `*` em `/api/*`. Rate limit só no login. |
| Migração | `app/migrations.py` já faz `add_column_if_not_exists`; não é preciso SQL manual. |
| Testes | Dois arquivos, nenhum para agendador, parser ou `/api/das`. `pytest` não está no `requirements.txt`. |

### nescon-clientes
| Item | Situação real |
|---|---|
| Contato | `companies.contact_email`, `phone`, `whatsapp` (override manual). Um contato por empresa. Espelho G-Click tem outro `email`/`phone`. |
| Número usado no WhatsApp | `whatsappNumero.celularSql()`: primeiro celular válido entre `whatsapp`, `phone`, `gclick.phone`. Qualquer outro critério vai mandar para número errado. |
| Lista de permitidos | Global, não por empresa: só números de empresa não arquivada/excluída, ou do escritório (`whatsappDestino.js`). |
| Opt-out | Booleans em `companies` (`alertas_ativos`, `avisos_gerais_ativos` etc.). |
| Envio WhatsApp | uazapi; janela 08:00-19:00 em dia útil (`janelaEnvio.js`); teto de 180 por hora; `alert_outbox` com backoff até 5 tentativas. **Nenhuma tabela guarda id da mensagem nem status de entrega/leitura.** Webhook só trata mensagem recebida (bot DP). |
| E-mail | nodemailer, sem retentativa, sem log, sem pixel, sem bounce. Usado em reset de senha, backup e chat. |
| Portal | JWT por empresa (`company_id`, `company_cnpj`); matriz/filial troca de token. Padrão de escopo: `AND company_id = $n` quando não é admin. **Não existe página de pendências nem nada com "ecac".** |
| Integração externa | `/api/fiscal/*` responde 410 com o aviso de que avisos saem **somente** do portal. Não há `INTERNAL_*_TOKEN` ativo. CORS aceita chamada sem `Origin` (servidor a servidor). |
| Migração | Sempre `ensure*.js` idempotente no boot. Nunca SQL manual. |
| Eventos | `portal_eventos` (login/uso, com CHECK que limita `tipo`), `deliverable_accesses`. Sem rastreio de abertura de e-mail ou clique. |

---

## 2. Problemas do plano, por tema

### 2.1 Segurança

- **Vazamento entre clientes no portal.** O plano expõe `GET /api/portal/pendencias/<cnpj>` via proxy. O proxy **não pode** aceitar CNPJ vindo do navegador. O CNPJ tem de sair de `req.company.cnpj` do JWT, sempre. O mesmo vale para gerar DAS e INSS. Isso é o ponto único de "coisa de um cliente para outro" e o plano não o declara.
- **Botão "Recalcular guia" no e-mail.** Se o link carregar CNPJ e competência e gerar a guia sem login, qualquer pessoa com o e-mail encaminhado gera guia paga em nome do cliente e vê o valor. O link deve levar ao portal autenticado; no máximo carregar um token de ação assinado, de uso único, com validade curta, que só abre a tela.
- **Chamada paga disparada pelo cliente.** Cada clique em "gerar DAS" custa R$ 0,29-0,32 e a regra 5 do `ARQUITETURA.md` proíbe disparo automático sem freio. Precisa: reaproveitar PDF válido (padrão do Escritório), limite por empresa/PA/dia, e o teto mensal já existente contando essas chamadas.
- **`/api/interno/empresas-contatos` devolve todos os contatos da carteira.** É PII em massa atrás de um token estático. Se mantiver, restringir à rede interna do Docker e não logar o payload. Melhor ainda: não existir (ver 2.2).
- **Tokens compartilhados.** Funciona, mas: comparação em tempo constante, rotação documentada, nunca em log, HTTPS obrigatório se os dois composes estiverem em projetos Easypanel separados (o plano usa `localhost:3001` e `localhost:8000`, que não funciona entre containers de projetos distintos).
- **`PORTAL_API_TOKEN` no central-ecac não tem onde encaixar.** `security.py` só conhece sessão e Basic. É preciso criar um caminho `/api/interno/*` com token próprio, fora da lista de caminhos públicos gerais, e com escopo restrito (só leitura de pendências e emissão de DAS de um CNPJ por vez).
- **WhatsApp disparado de fora do portal** contraria a decisão registrada em `fiscalIngest.js`. Mesmo passando por um endpoint do nescon, o padrão certo é o nescon **enfileirar** (como `docNotify`), não enviar síncrono no meio do lote do central-ecac.

### 2.2 Arquitetura: inverter o sentido

O plano: central-ecac puxa contatos → central-ecac envia e-mail → central-ecac pede WhatsApp ao nescon.

Recomendado: **central-ecac publica pendências; nescon consome, notifica e acompanha.**

| Responsabilidade | Onde |
|---|---|
| Puxar situação fiscal mensal, guardar histórico, teto de gasto | central-ecac (já existe) |
| Expor por CNPJ: `relatorio_id`, `data_hora`, débitos classificados (em atraso vs. a vencer), omissões, parcelamento/PGFN em texto | central-ecac (novo, JSON) |
| Emitir DAS SN com data de consolidação, guardar PDF e registrar evento | central-ecac (adaptar `/api/das` ao padrão do Escritório) |
| Fila "reprocessar empresa X em data D" | central-ecac (novo) |
| Contato, canal, opt-out, janela, lista de permitidos, fila de envio, log | nescon (já existe) |
| Espelho das pendências por empresa, estado da cobrança, eventos (clicou, logou, gerou guia) | nescon (novo) |
| Página "Impostos pendentes" no portal | nescon (novo) |

Ganhos: some a cópia de contatos, some o SMTP em Python, some a alteração em `models.py`, some o anti-flood por coluna, e o histórico de notificação fica no mesmo lugar dos outros avisos.

### 2.3 Acertividade dos dados (não mandar coisa errada)

- **Chave de junção:** CNPJ com 14 dígitos normalizado nos dois lados. Os dois têm `cnpj` único. Cuidados: clientes pessoa física (CPF) do nescon não existem no e-CAC; empresas arquivadas ou excluídas no nescon podem seguir ativas no central-ecac e **não podem** ser notificadas; matriz e filial são CNPJs distintos com tokens distintos.
- **Relatório do mês, não relatório velho.** Se a empresa foi pulada no lote (procuração travada, dois erros seguidos, teto), o "último relatório" pode ter 30 ou 60 dias. O plano notifica em cima do último relatório sem checar `data_hora`. Regra: só notificar se `data_hora` for posterior ao início do lote; caso contrário, fila de revisão do escritório.
- **"Em atraso" precisa de definição.** O parser captura `DEVEDOR` e `A ANALISAR-A VENCER`. Só `DEVEDOR` com vencimento anterior a hoje é atraso. `A VENCER` não pode virar cobrança.
- **Fantasmas do parser.** O desvio 16 mostra que já houve linhas fantasma (`DCTFWeb 0001`). Antes de enviar: valor maior que zero, período no formato `MM/AAAA` com ano plausível, vencimento plausível. PDF salvo com parse vazio ou com erro **não** vira "sem pendências"; vira revisão manual.
- **Valores.** O saldo do relatório é da data do relatório. A guia recalculada terá multa e juros até a data escolhida. A mensagem deve dizer isso, ou o cliente vai reclamar que "o valor do e-mail é outro".
- **Parcelamento e PGFN não têm valor** no dado atual. A mensagem só pode dizer "há parcelamento com parcela em aberto / inscrição na dívida ativa, fale com o escritório".
- **Idempotência.** Anti-flood de 24 h em coluna da empresa não resolve retomada de lote dias depois nem reexecução manual. O certo é uma tabela de notificações com chave única `(company_id, relatorio_id, etapa, canal)`.

### 2.4 Agendamento e execução mensal do "puxar relatórios"

- **"3º dia útil após o 20º dia útil"** dá por volta do 23º dia útil, ou seja, entre o dia 31 e o dia 3 do mês seguinte. Provavelmente a intenção era "3º dia útil após o dia 20" (vencimento do DAS), que cai entre 23 e 25. O padrão atual (dia 25) já cobre isso. Sugestão: manter "dia fixo" e acrescentar "ajustar para o próximo dia útil", que é mais simples de explicar e de testar do que contar dias úteis. A biblioteca `holidays` serve, com `TZ=America/Sao_Paulo`.
- **Robustez do lote.** Thread no processo web com `sleep` bloqueante: um redeploy no meio do lote mata a execução. O checkpoint só existe para o teto. É preciso confirmar que, se o processo cai no meio, o tick seguinte **não repete** as empresas já cobradas; hoje isso não está garantido.
- **Regerar 5 dias após o recálculo, só para quem tem débito.** O agendador atual só tem três módulos mensais. Isso exige uma **fila por empresa** (`company_id`, `motivo`, `agendado_para`, `executado_em`), drenada a cada tick, com as mesmas travas (procuração, teto). O pagamento de DAS costuma aparecer na situação fiscal em 2 a 4 dias úteis; 5 dias corridos pode ser pouco em semana com feriado. Sugerir 5 **dias úteis**.
- **Custo.** Cada regeração é uma consulta paga. Com 72 empresas e, digamos, 20 devedoras, são 20 regerações a mais por ciclo, mais as repetições de quem recalcula de novo. Precisa entrar no teto e num limite de regerações por empresa por mês (sugestão: 2).

### 2.5 Fluxo de e-mail

- Enviar pelo nescon (nodemailer já configurado), não portar SMTP para Python.
- **Rastrear abertura por pixel é pouco confiável:** Gmail e Apple pré-carregam imagens (marca "aberto" sem abrir), Outlook bloqueia (nunca marca). Use o pixel como sinal fraco e trate como "abriu" qualquer um destes: clique no link rastreado (redirect com token), login no portal após o envio (`portal_eventos`), geração de guia.
- **Bounce e e-mail inválido.** Não há tratamento. No mínimo: registrar falha de envio, marcar a empresa em "contato a corrigir" e avisar o escritório. Sem isso, o cliente com e-mail errado nunca é cobrado e ninguém sabe.
- **Retentativa.** O mailer lança erro e pronto. Colocar o e-mail na mesma lógica de fila com backoff que o WhatsApp já tem.
- **Conteúdo:** tabela por tributo (tipo, competência, vencimento, valor original, saldo na data do relatório), data do relatório, aviso de que a guia recalculada atualiza multa e juros, botão "Ver e gerar guia" para o portal, e o canal de contato do escritório.

### 2.6 Fluxo de WhatsApp

- Sair **somente do nescon**, pela composição já usada em `docNotify`: buscar número por `celularSql`, respeitar opt-out, janela, teto por hora, `owner`, `enviarComRetry`, `marcarEnviado`, fila fora da janela. O plano diz "reutiliza throttle e whitelist", mas não existe função genérica pronta; é preciso compô-la.
- **Status de entrega/leitura.** A uazapi manda eventos de status pelo webhook, mas o webhook atual ignora tudo que não é mensagem recebida. Para "monitorar se leu", é preciso guardar o id da mensagem e tratar os eventos de status. Trabalho novo, factível.
- **Resposta do cliente.** Quem responde "já paguei" cai no bot de DP (`whatsappDp.js`). Precisa rotear resposta de quem recebeu cobrança fiscal para o chat/atendimento, ou ao menos não responder com o bot.
- **Texto curto:** tributo, competência, valor, quantidade de itens se houver mais de um, link. Sem tabela.

### 2.7 Recalcular e baixar guias

| Tipo | Hoje | O que falta |
|---|---|---|
| DAS Simples Nacional | `GERARDAS12` com `dataConsolidacao` funciona | Guardar PDF, número, vencimento e valor (copiar o padrão do Escritório), registrar evento "recalculada por cliente/escritório", limite por PA/dia |
| DAS MEI | `GERARDASPDF21` chamado sem data | Verificar na documentação SERPRO se o serviço aceita `dataConsolidacao`; se sim, passar; se não, MEI vencido fica fora do recálculo automático |
| INSS / DCTFWeb | `GERARGUIA31` só por ano/mês | Verificar se a API aceita recálculo de DARF vencido; hoje o código não passa data. Se não houver, a mensagem para INSS em atraso termina em "fale com o escritório" |
| Parcelas de parcelamento | `parcelamentos_serpro_service` já baixa PDF da parcela | Incluir parcela vencida na notificação e no portal |
| PGFN | Não coberto pela API | Só texto orientando contato |

Sem o evento "guia recalculada" registrado, **não existe** como decidir entre "regerar em 5 dias" e "mandar lembrete". Esse evento é o coração do fluxo e o plano não o tem.

### 2.8 A máquina de estados que falta

Por `(empresa, relatorio_id)`:

| Estado | Entrada | Saída |
|---|---|---|
| `NOTIFICADO` | relatório do mês com débito em atraso | e-mail + WhatsApp no dia 0 |
| `LEMBRETE` | 5 dias úteis sem clique, sem login e sem guia gerada | e-mail + WhatsApp curtos, **sem regerar** relatório |
| `RECALCULOU` | evento de emissão de DAS | agenda regeração para 5 dias úteis depois |
| `REGERADO_QUITADO` | novo relatório sem o débito | e-mail de agradecimento, encerra |
| `REGERADO_EM_ABERTO` | novo relatório ainda com o débito | cobrança 1 (e-mail), cobrança 2 (WhatsApp, 3 dias úteis depois) |
| `ESCALADO` | 2 cobranças sem mudança | tarefa para o escritório, para a automação |
| `PAUSADO` | flag manual do escritório (em negociação, parcelando, erro de cadastro) | nada sai até despausar |

Limites: no máximo 2 regerações por empresa por mês, no máximo 4 mensagens por canal por ciclo, sempre dentro da janela de envio, e sempre lendo o opt-out.

### 2.9 Deploy e migração

- central-ecac: usar `app/migrations.py` para colunas e um módulo de modelos novo para tabelas (regra 3). Sem SQL manual.
- nescon: `ensureEcacSchema.js` no boot, com `CREATE TABLE IF NOT EXISTS` para o espelho de pendências, notificações e eventos. Adicionar o `tipo` novo em `portal_eventos` exige recriar o CHECK.
- nginx: `/api/ecac` cai em `/api/` (timeout 180 s), suficiente.
- Rede: dois composes em projetos Easypanel separados não se veem por `localhost`. Usar URL pública com HTTPS ou colocar os dois na mesma rede.
- Adicionar `pytest` ao `requirements.txt` e testes para: seleção de "em atraso", validação anti-fantasma, fila de reprocessamento e escopo por CNPJ no proxy.

---

## 3. Ordem recomendada

1. **Fase 0, decisão:** inverter o sentido (central-ecac fornece, nescon notifica). Sem isso, o resto acumula duplicação.
2. **Fase 1, central-ecac:** token interno com escopo; `GET /api/interno/pendencias/<cnpj>` em JSON classificado; `/api/das/emitir` guardando PDF e evento; fila de reprocessamento por empresa; "ajustar para dia útil" no agendamento.
3. **Fase 2, nescon:** `ensureEcacSchema.js`; job mensal que lê as pendências de todas as empresas ativas e grava o espelho; e-mail e WhatsApp pelos padrões existentes; página "Impostos pendentes" no portal com botão de gerar guia (proxy usando o CNPJ do JWT); link rastreado.
4. **Fase 3:** máquina de estados, lembretes, cobranças, tela no `/admin` para acompanhar e pausar por empresa.
5. **Fase 4:** status de entrega do WhatsApp via webhook, tratamento de bounce, roteamento da resposta do cliente para o atendimento.

---

## 4. Antes de ligar em produção

- Modo "só escritório": toda mensagem vai para `ADMIN_EMAIL` e `ADMIN_WHATSAPP` até o escritório liberar por empresa.
- Teste com duas empresas reais, conferindo à mão que cada payload tem só o CNPJ certo.
- Teste do parser com um PDF que tinha fantasma (há PDFs salvos em `reports/`).
- Teste de retomada: derrubar o container no meio do lote e confirmar que não repete chamada paga.
- Teto mensal definido e contando as emissões feitas pelo portal.

---

## 5. Tom das mensagens (proposta)

**E-mail inicial**

> Olá, {razão social}.
> Nosso trabalho é manter a sua empresa em dia com o Fisco, e por isso avisamos: o relatório da Receita Federal de {data} mostra {n} guia(s) em aberto.
> {tabela: tributo, competência, vencimento original, valor no relatório}
> Os valores acima são da data do relatório. Ao gerar a guia, o sistema atualiza multa e juros até a data que você escolher para pagar.
> [Ver e gerar a guia atualizada]
> Se já pagou, desconsidere: o pagamento leva alguns dias para aparecer na Receita. Se preferir parcelar ou tiver dúvida, fale com a gente por aqui ou pelo WhatsApp {número}.

**Lembrete (5 dias úteis, sem regerar)**

> Olá, {razão social}. Só passando para lembrar que {n} guia(s) segue(m) em aberto na Receita: {tributo} {competência}, {valor}. É rápido gerar a guia atualizada pelo portal: [link]. Qualquer dúvida, estamos por aqui.

**Cobrança após regeração com débito ainda em aberto**

> Olá, {razão social}. Conferimos novamente na Receita e a guia {tributo} {competência} ({valor}) ainda consta em aberto. Juros e multa continuam correndo. Se quiser, geramos a guia com a data que for melhor para você, ou conversamos sobre parcelamento. [link] · WhatsApp {número}.

**WhatsApp (curto)**

> {razão social}, aqui é a {escritório}. Consta em aberto na Receita: {tributo} {competência}, R$ {valor}{ e mais n item(ns)}. Gere a guia atualizada aqui: {link}. Se já pagou, ignore. Dúvidas, é só responder.
