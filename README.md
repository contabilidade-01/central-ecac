# Central Pendências e-CAC

Sistema web (Flask + SPA React) que consulta a **API Integra Contador da SERPRO** para
acompanhar a situação fiscal de uma carteira de clientes: situação fiscal, débitos,
pendências, parcelamentos, caixa postal do e-CAC, emissão de DAS/DARF e pagamentos.

> **Origem:** o código-fonte Python original foi perdido e reconstruído a partir do
> **bytecode do executável** `IntegraContadorDesktop.exe`. O bundle React em
> `app/static/app/assets/` é byte a byte idêntico ao do exe — por isso o contrato de
> API é o do exe, e é ele a fonte da verdade. Ver [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

---

## Índice

| Documento | Para quê |
|---|---|
| [docs/ARQUITETURA.md](docs/ARQUITETURA.md) | Como o sistema é organizado, o que é fiel ao exe e os **12 desvios intencionais** |
| [docs/DEPLOY_EASYPANEL.md](docs/DEPLOY_EASYPANEL.md) | **Passo a passo do deploy**: GitHub → EasyPanel → subdomínio → HTTPS |
| [docs/OPERACAO.md](docs/OPERACAO.md) | Rotina do dia a dia: custos da API, backup, restauração, o que é grátis e o que é pago |
| [docs/MELHORIAS.md](docs/MELHORIAS.md) | Lista priorizada de melhorias — o que já foi feito e o que falta |
| [docs/AUTOMACAO.md](docs/AUTOMACAO.md) | Agendamento das rotinas, teto de gasto e retomada |
| `../AUDITORIA_31072026.md` | Histórico técnico da reconstrução (fora do repositório) |

---

## Telas próprias (fora da SPA)

A SPA React é a compilada do exe e não temos o fonte, então as funções novas ganharam
páginas próprias servidas pelo Flask. Todas ficam no menu **Administração**, injetado no
cabeçalho da SPA pelo `index.html` (12º desvio):

| Tela | Para quê |
|---|---|
| `/procuracoes` | Situação de procuração por empresa, erro exato da SERPRO e trava de chamadas pagas |
| `/agendamento` | Frequência de cada rotina automática, teto mensal de gasto e execução manual |
| `/login` · `/logout` | Entrada do sistema (sessão) [desvio 10] |
| `/primeiro-acesso` | Definição da senha na primeira vez |
| `/restaurar` | Envio do banco e do certificado A1 pelo navegador [desvio 11] |
| `/healthz` | Healthcheck (sem login) |

## Rodando localmente (Windows)

```bash
.venv\Scripts\python.exe main_production.py
```

Abre em <http://localhost:5847>. Sem `AUTH_USER`/`AUTH_PASSWORD` no ambiente o sistema
não pede senha — é o comportamento do aplicativo desktop original.

## Rodando com Docker

```bash
cp .env.example .env    # e edite SECRET_KEY, AUTH_USER, AUTH_PASSWORD
docker compose up -d --build
```

---

## Estrutura

```
Central eCac/
├── app/
│   ├── __init__.py           app factory, blueprints, hook da SPA,
│   │                         config.json dinamico + ProxyFix [desvio 9]
│   ├── config.py             DATA_DIR / banco / diretórios (lê variáveis de ambiente)
│   ├── models.py             14 tabelas — schema idêntico ao do exe (NÃO alterar)
│   ├── migrations.py         migrações idempotentes
│   ├── security.py           login/sessão + /healthz + erro 500 [desvios 7 e 10]
│   ├── scheduler.py          thread do agendamento automático [desvio 8]
│   ├── routes/               10 blueprints do exe
│   │                         + procuracoes [5] + agendamento [8]
│   ├── services/             regra de negócio e integração SERPRO
│   ├── static/app/           SPA React compilada (idêntica à do exe)
│   └── utils/
├── scripts/                  utilitários operacionais e auditorias de fidelidade
├── docs/                     documentação
├── Dockerfile                imagem de produção
├── docker-compose.yml        execução local / VPS manual
├── wsgi.py                   entrypoint do Gunicorn (1 worker — leia o arquivo)
├── main_production.py        execução local no Windows (Waitress)
└── requirements.txt
```

**Dados nunca ficam no repositório.** Banco, PDFs, certificado A1 e logs vivem em
`DATA_DIR` (localmente a própria pasta; no servidor, o volume `/data`).

---

## Scripts

| Script | O que faz |
|---|---|
| `scripts/backup_dados.py` | Backup consistente do banco + PDFs (`sqlite3.backup()`) |
| `scripts/expurgar_relatorios.py` | Remove relatórios antigos mantendo os N mais recentes por empresa |
| `scripts/estimar_custo_caixa_postal.py` | Estimativa de custo da API antes de rodar em lote |
| `scripts/varrer_endpoints.py` | Testa todos os GET (nunca POST) e aponta erro 500 |
| `scripts/auditar_rotas.py` | Compara as rotas do app com as do bytecode do exe |
| `scripts/auditar_estrutura.py` | Compara as funções de cada módulo com as do exe |
| `scripts/auditar_shape.py` | Compara o formato do corpo das respostas com o do exe |
| `scripts/configurar_certificado.py` | Copia o `.pfx` para `certificates/` e grava o caminho |
| `scripts/migrar_banco_legado.py` | Migração do banco antigo (uso único, já executado) |
| `scripts/reprocessar_parcelamentos_pgfn.py` | Reprocessa PDFs já salvos, sem chamar a SERPRO |

As auditorias precisam do bytecode extraído do exe, que fica **fora do repositório** em
`../_ARQUIVO/engenharia_reversa/` — ver [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

---

## Custos da API (importante)

| Serviço | Situação |
|---|---|
| Indicador da caixa postal (`INNOVAMSG63`) | **GRATUITO** |
| Lista e detalhe de mensagens | **PAGO** — R$ 0,24 (até 300/mês) ou R$ 0,21 |
| Consultas em geral | **PAGO** — R$ 0,24 / R$ 0,21 |
| Emissões (DAS, DARF) | **PAGO** — R$ 0,32 (até 500/mês) ou R$ 0,29 |

Toda chamada é feita a um órgão público **em nome do cliente**. Antes de qualquer rodada
em lote, rode `scripts/estimar_custo_caixa_postal.py`. Detalhes em
[docs/OPERACAO.md](docs/OPERACAO.md).
