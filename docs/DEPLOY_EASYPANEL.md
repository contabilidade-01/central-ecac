# Deploy — GitHub → EasyPanel → subdomínio com HTTPS

Passo a passo completo, do repositório vazio ao sistema no ar. Cada passo diz **onde**
você clica e **o que** preencher.

---

## Antes de começar — 3 decisões já tomadas (e por quê)

**1. Docker: sim.** O sistema depende de `pycurl` compilado contra libcurl+OpenSSL e de
`cryptography`/`signxml` para o certificado A1. O `Dockerfile` congela **exatamente o
ambiente testado** — Python 3.13 e as versões do `requirements.txt`, as mesmas que rodam
na máquina do Jean. Sem container, uma atualização da VPS pode trocar essas versões e
quebrar a leitura do `.pfx`, que é o que autentica na SERPRO. O EasyPanel também aceita
Nixpacks, mas ele escolhe a versão do Python sozinho: **não use**.

> Verificado no build: dentro da imagem ficam Python 3.13.14, pycurl 7.45.7 com
> **OpenSSL 3.5.3** (no Linux o pycurl usa OpenSSL, não o Schannel do Windows — que é
> justamente onde o certificado A1 costuma dar 403).

**2. Banco: SQLite em volume.** Continua SQLite, agora num volume Docker com **um único
processo** escrevendo. O problema antigo (corrupção) vinha do OneDrive sincronizando o
arquivo entre duas máquinas — na VPS isso deixa de existir. Migrar para PostgreSQL é
possível e está na [lista de melhorias](MELHORIAS.md), mas exige recriar as 14 tabelas e
migrar os dados; não é pré-requisito para subir.

**3. Um worker só.** `MONITOR_STATUS` e o progresso dos parcelamentos ficam em memória.
Com 2 workers a barra de progresso ficaria pulando entre processos. Ver `wsgi.py`.

---

## Passo 1 — Repositório local (JÁ FEITO em 03/08/2026)

O repositório **não fica no OneDrive**. Motivos: o OneDrive sincroniza a pasta `.git`
inteira (conflitos e lentidão — chegou a levar minutos por leitura de arquivo durante a
sincronização) e o GitHub Desktop já trabalha em `Documents\GitHub`.

| | |
|---|---|
| **Repositório (código)** | `C:\Users\Jeandson\Documents\GitHub\central-ecac` |
| **Pasta operacional (banco, .pfx, PDFs)** | `…\00_PROJETOS\Central Pendencias Ecac\Central eCac` |

O commit inicial já existe (74 arquivos, só código). A conferência de segredos foi feita:
nem consumer key/secret, nem senha de certificado, nem `.pfx`, nem banco entraram.

Para repetir a conferência a qualquer momento:

```bash
git status --porcelain --ignored | grep -E "instance/|certificates/|reports/|\.env$|\.pfx"
```

Todos devem aparecer como ignorados (`!!`). Se algum aparecer como adicionado (`A`),
**pare** e remova antes de publicar.

### Publicar no GitHub

Crie o repositório em <https://github.com/new> — **marque "Private"**, sem README nem
.gitignore (já existem aqui) — e envie:

```bash
git remote add origin https://github.com/contabilidade-01/central-ecac.git
git push -u origin main
```

Pelo GitHub Desktop é equivalente: *File → Add local repository* → aponte para
`Documents\GitHub\central-ecac` → *Publish repository* → marque **Keep this code private**.

> ⚠️ O repositório **tem de ser privado**. Mesmo sem segredos, o código expõe a estrutura
> de integração com a SERPRO e as regras fiscais do escritório.

---

## Passo 2 — Criar o serviço no EasyPanel

1. No painel: **Project → Create Service → App**.
2. Nome: `central-ecac`.
3. Aba **Source**: escolha **GitHub**, autorize o EasyPanel e selecione o repositório.
   Branch: `main`.
4. Aba **Build**: selecione **Dockerfile**. Caminho: `Dockerfile` (raiz).

---

## Passo 3 — Variáveis de ambiente

Aba **Environment**, cole (trocando os valores):

```
DATA_DIR=/data
PORT=5847
TZ=America/Sao_Paulo
SECRET_KEY=<cole aqui a chave gerada>
SESSION_COOKIE_SECURE=1
SCHEDULER_ENABLED=1
LIMITE_GASTO_MENSAL=100
```

**A senha de acesso não vem daqui.** Ela é definida na tela `/primeiro-acesso`, no
primeiro acesso ao sistema, e fica em hash no volume. `SECRET_KEY` continua obrigatória:
é ela que assina o cookie de sessão do login.

`SCHEDULER_ENABLED=1` liga a automação (já é o padrão da imagem).
`LIMITE_GASTO_MENSAL` é o teto mensal em reais — `0` = sem teto. Ele também pode ser
definido pela tela `/agendamento`; a variável de ambiente tem prioridade.

Gere a `SECRET_KEY` com:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### O que NÃO vai aqui

As **credenciais da SERPRO** (consumer key/secret), o **CNPJ do contador** e a **senha do
certificado** não são variáveis de ambiente — ficam no banco, cadastradas pela tela de
**Configurações** do próprio sistema. O certificado `.pfx` é um arquivo e vai para o
volume (passo 6).

### ⚠️ Defina a senha ANTES de carregar os dados

Assim que o domínio responder, abra `https://ecac.gestaoempresa.com` — o sistema leva
para **`/primeiro-acesso`**, onde você define usuário e senha. Faça isso **na hora**:
até a senha existir, quem souber o endereço pode cadastrá-la.

Enquanto não houver credencial, nenhuma tela e nenhuma rota `/api/` abre — o sistema
fica travado de propósito. Confirme com `/healthz`: tem de mostrar `"auth":"ligada"`.

Esqueceu a senha? No terminal do container: `python scripts/definir_senha.py`.

---

## Passo 4 — Volume persistente (sem isto você perde tudo)

Aba **Volumes** → **Add Volume**:

| Campo | Valor |
|---|---|
| Type | Volume |
| Name | `dados` |
| Mount path | `/data` |

É esse volume que guarda **banco, PDFs, certificados e logs**. Sem ele, cada redeploy
recria o container do zero e apaga o histórico de todos os clientes.

---

## Passo 5 — Domínio e HTTPS

Aba **Domains** → **Add Domain**:

| Campo | Valor |
|---|---|
| Host | `ecac.gestaoempresa.com` |
| Port | `5847` |
| HTTPS | ligado |

O EasyPanel emite o certificado Let's Encrypt sozinho, desde que o DNS já esteja
apontando (passo seguinte).

### Criando o subdomínio e apontando o DNS

No painel do seu provedor de domínio (Registro.br, Cloudflare, GoDaddy…), crie **um
registro A**:

| Tipo | Nome | Valor | TTL |
|---|---|---|---|
| A | `ecac` | `<IP público da VPS>` | 3600 (ou automático) |

- O **Nome** é só a parte da esquerda: digitando `ecac` no domínio `gestaoempresa.com`
  você cria `ecac.gestaoempresa.com`. Alguns painéis exigem o nome completo — siga o
  formato que o seu mostrar nos registros existentes.
- Use **A** (aponta para IP), não CNAME.
- **Cloudflare:** deixe a nuvenzinha **cinza (DNS only)** até o HTTPS ser emitido. Com a
  nuvem laranja, o Let's Encrypt pode falhar na validação. Depois de emitido, você pode
  ligar o proxy se quiser.
- Propagação leva de minutos a algumas horas. Confira com:

```bash
nslookup ecac.gestaoempresa.com
```

Quando o IP retornado for o da VPS, clique em **Deploy** e o HTTPS é emitido.

### ⚠️ Timeout do proxy — o botão "Monitorar" é síncrono

Com 72 empresas o "Monitorar" mantém a requisição aberta por **2–3 minutos**. O Gunicorn
já está com `--timeout 300`, mas quem pode cortar antes é o proxy na frente:

- **Traefik do EasyPanel:** sem limite por padrão — costuma passar.
- **Cloudflare com proxy laranja:** corta em **100 s** (erro 524). Se você ligar o proxy,
  o botão vai falhar em lote grande mesmo com o servidor ainda trabalhando.

Enquanto a melhoria #2 (tornar assíncrono) não for feita, as saídas são: deixar o
Cloudflare em **DNS only**, monitorar em **grupos pequenos** de empresas, ou deixar o
**agendador** fazer o lote (ele roda dentro do servidor, sem passar por proxy).

---

## Passo 6 — Subir o certificado A1 e restaurar os dados

O certificado **não está no repositório** (e não deve estar). Duas formas de colocá-lo no
volume:

**Pelo terminal da VPS** (aba *Terminal* do EasyPanel ou SSH):

```bash
docker cp NESCON.pfx central-ecac:/data/certificates/contador_certificado.pfx
```

**Restaurando o banco atual** (para levar as 72 empresas e o histórico já processado):

```bash
docker cp integra_contador.db central-ecac:/data/instance/integra_contador.db
docker restart central-ecac
```

Use o backup mais recente gerado por `scripts/backup_dados.py`. Se preferir começar do
zero, pule — o sistema cria o banco vazio sozinho e você cadastra pela tela.

Depois, entre em **Configurações** no sistema e confirme: CNPJ do contador, consumer
key/secret da SERPRO, caminho do certificado (`/data/certificates/contador_certificado.pfx`)
e a senha do certificado.

---

## Passo 7 — Validar

```bash
curl -u nescon:SUA_SENHA https://ecac.gestaoempresa.com/healthz
```

Esperado:

```json
{"status": "ok", "banco": "ok", "auth": "ligada"}
```

Se vier `"auth": "DESLIGADA"`, as variáveis não foram aplicadas — **não use o sistema
assim**. Confira o passo 3 e faça o redeploy.

Depois, no navegador: abra `https://ecac.gestaoempresa.com`, informe usuário e senha, e
verifique se o painel lista as empresas. Abra também `/procuracoes`.

---

## Passo 8 — Atualizações (o fluxo do dia a dia)

```bash
git add . && git commit -m "descricao da mudanca" && git push
```

No EasyPanel, clique em **Deploy** (ou ative *Auto Deploy* na aba Source para que cada
push suba sozinho). O volume `/data` **não é tocado** no redeploy — banco e PDFs
permanecem.

Migrações de banco rodam sozinhas: `run_migrations()` é chamado no start e é idempotente.

---

## Passo 9 — Backup automático

O volume protege contra redeploy, **não** contra erro humano ou perda da VPS. Agende:

```bash
0 3 * * * docker exec central-ecac python scripts/backup_dados.py --manter 14
```

Os arquivos ficam em `/data/backups/backup_AAAAMMDD_HHMMSS.zip`. **Leve uma cópia para
fora da VPS** periodicamente:

```bash
docker cp central-ecac:/data/backups ./backups-vps
```

Para restaurar:

```bash
unzip backup_20260802_030000.zip
docker cp instance/integra_contador.db central-ecac:/data/instance/
docker restart central-ecac
```

---

## Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Build falha em `pycurl` | falta lib de compilação | confirme que está buildando pelo **Dockerfile**, não Nixpacks |
| `502` no domínio | porta errada no Domains | tem de ser **5847**, a mesma do `PORT` |
| HTTPS não emite | DNS ainda não propagou / Cloudflare laranja | `nslookup`; deixe a nuvem cinza |
| Dados sumiram no redeploy | volume não montado | confira mount path `/data` (passo 4) |
| `Falha ao autenticar na API da SERPRO` | `.pfx` ausente ou senha errada | passo 6 e tela de Configurações |
| Barra de progresso pula | mais de 1 worker | mantenha `--workers 1` (ver `wsgi.py`) |
| Monitoramento "trava" no navegador | requisição síncrona de 2–3 min | é esperado; acompanhe por `/api/caixa-postal/monitorar/status` |
