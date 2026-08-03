# Arquitetura e regras do projeto

## O que este sistema é

Flask servindo uma SPA React já compilada + integração com a **API Integra Contador da
SERPRO** (autenticação por certificado digital A1 + consumer key/secret).

O código-fonte Python original foi **perdido**. O `app/` que existia era uma
reimplementação por aproximação, com schema de banco e contratos de API inventados. A
reconstrução foi feita a partir do **bytecode do executável** `IntegraContadorDesktop.exe`
(Python 3.12), porque o bundle React em `app/static/app/assets/` é **byte a byte idêntico
ao do exe** — logo o frontend é o original e espera exatamente o contrato do exe.

**A fonte da verdade é o bytecode**, não a documentação antiga.

---

## As 5 regras invioláveis

1. **Fidelidade ao exe.** Reconstrua a lógica do bytecode. Não "melhore", não simplifique.
2. **Não conserte trocando o nome da coluna.** É tentador (os erros costumam ser
   `AttributeError`), mas isso deixa a lógica inventada no lugar. Reconstrua o módulo.
3. **Não altere `app/models.py`.** O schema já está fiel ao exe e validado (14 tabelas).
   Se um módulo parece precisar de coluna que não existe, o módulo é que está errado.
4. **Não crie desvios sem perguntar.** Os que existem estão listados abaixo e marcados no
   código com o comentário `DESVIO INTENCIONAL`.
5. **Nunca dispare POST em varredura ou teste automático.** É chamada paga a órgão
   público em nome do cliente. Exceção: o indicador da caixa postal (`INNOVAMSG63`) é
   gratuito.

E a lição que custou caro: **auditoria estática não prova que funciona.** Rota certa +
função certa + status 200 + shape certo já passaram em três módulos que, testados de
verdade, não funcionavam.

---

## Os 14 desvios intencionais

| # | O quê | Por quê | Onde |
|---|---|---|---|
| 1 | Hook `before_request` da SPA | com `static_url_path='/'`, abrir `/dashboard` direto devolvia 404 | `app/__init__.py` |
| 2 | `DATA_DIR` fora do `%LOCALAPPDATA%` | banco/PDFs compartilhados (OneDrive) e, no servidor, volume persistente | `app/config.py` |
| 3 | Fallback do caminho do `.pfx` | consequência do 2: caminho absoluto de uma máquina não existe na outra | `report_service._resolver_certificado()` |
| 4 | Parcelamento/PGFN nas Pendências | a PGFN não é coberta pela API, mas está no texto do PDF (pedido do Jean) | `pdf_parser`, `report_service` |
| 5 | Mapa de procurações + trava de chamadas pagas | não repetir chamada paga em empresa que a SERPRO recusa (pedido do Jean) | `services/procuracao_service.py`, `routes/procuracoes.py` |
| 6 | Não rebaixar detalhe já baixado e inalterado | o exe rebaixava a caixa inteira a cada consulta — 17 chamadas onde bastavam 2 | `caixa_postal_service.consultar_mensagens_empresa()` |
| 7 | HTTP Basic + `/healthz` + erro 500 neutro | o exe era desktop em localhost; na internet as mesmas rotas expõem dados fiscais e botões que gastam dinheiro | `app/security.py` |
| 8 | Agendamento automático + teto de gasto | o exe dependia de alguém clicar em cada botão; sem teto, um lote grande gasta sem freio (pedido do Jean) | `services/agendamento_service.py`, `services/limite_gasto_service.py`, `scheduler.py`, `routes/agendamento.py` |
| 9 | `config.json` dinâmico + `ProxyFix` | a SPA compilada usa `server_url` do `config.json` como baseURL do axios; o arquivo traz `http://localhost:5847`, que **quebra em qualquer domínio** (e é conteúdo misto sob HTTPS). Sem o fonte do React, a correção tem de ser no servidor | `app/__init__.py` |
| 10 | Tela de login com sessão | o Basic do 7º dependia de variável de ambiente; se o painel não aplicasse, o sistema **abria sem senha e ninguém percebia** — aconteceu em 03/08/2026. Agora, sem credencial, o sistema não abre | `app/security.py`, `services/usuarios_service.py` |
| 11 | Tela `/restaurar` | levar banco e `.pfx` para o volume exigia SSH + `scp` + `chown` (o container roda como uid 10001; arquivo enviado por root deixa o SQLite somente leitura). Enviando pelo navegador, quem grava é o app — o dono já sai certo | `app/routes/restaurar.py` |
| 12 | Seção "Administração" no menu da SPA | as telas do Flask não existiam no menu, e o bundle React é compilado sem fonte. Injetado por script no `index.html` (que já não era idêntico ao do exe); os arquivos em `/assets` seguem intocados. Leva junto o **Sair**, que a SPA não tinha | `app/static/app/index.html` |
| 13 | Vários usuários, com rotinas e empresas por usuário | o exe tinha um operador só, dono da máquina. Na VPS, o segundo usuário não pode ver dado fiscal de cliente que não é dele nem disparar lote pago | `services/usuarios_service.py`, `services/permissoes.py`, `security.py`, `routes/usuarios.py` |
| 14 | Barra lateral por cima da SPA | o layout de cabeçalho horizontal não comportava as telas novas nem a navegação por seções. Como o bundle não tem fonte, a barra é montada por fora: o cabeçalho original é escondido por CSS e **cada item clica no botão original**, que segue no DOM | `app/static/app/index.html`, `app/ui.py`, `services/permissoes.py` |

### Detalhe do 14º — a barra lateral

O menu tem **uma fonte só**: `permissoes.MENU`. As telas do Flask montam a barra pelo
servidor (`app/ui.py`); a SPA recebe a mesma estrutura, já filtrada pela permissão, em
`/api/me`. Assim as duas nunca divergem.

Como a navegação funciona sem o fonte do React: o cabeçalho azul é escondido com
`display:none` (os botões continuam no DOM) e o item da barra chama `.click()` no botão
correspondente, casando pelo rótulo. Vindo de uma tela do Flask, o link é `/?aba=<chave>`
e o script clica no botão certo assim que a SPA carrega.

Recolhimento: botão na barra superior, estado guardado em `localStorage`, então
atravessa recarregamento e navegação entre SPA e telas do Flask. Abaixo de 760px a barra
vira gaveta sobreposta.

Duas armadilhas de CSS resolvidas no caminho:
* `margin:0 auto` dentro de um flex *column* **desliga o stretch** no eixo transversal, e
  o bloco passa a se dimensionar pelo conteúdo — daí `width:100%` no `.wrap`;
* a SPA foi desenhada para largura cheia, então `overflow-x:auto` fica no `.conteudo`:
  a rolagem se resolve lá dentro em vez de empurrar a barra para fora da tela.

### Detalhe do 13º — como a permissão é aplicada

Tudo passa por **um único `before_request`** em `app/security.py` mais um filtro de
resposta. **Nenhuma rota do exe foi alterada** — a regra vive fora delas, então a
fidelidade ao bytecode continua intacta.

| Camada | O que faz |
|---|---|
| Rotina | o prefixo da URL vira uma chave (`caixa_postal`, `das`…) e é comparado com o que o usuário pode abrir. `/api/das/dctfweb` casa antes de `/api/das` |
| Empresa (entrada) | `company_id` na URL, no corpo ou na query é conferido contra a lista do usuário |
| Lote | escrita **sem** empresa explícita é recusada para usuário restrito — é exatamente o caso dos lotes, que rodam sobre a carteira inteira e gastam dinheiro |
| Empresa (saída) | as listas devolvidas pela API são filtradas, para que linha de empresa não liberada não chegue ao navegador |

O menu esconde o que o usuário não pode abrir, mas isso é **conforto, não segurança**:
quem decide é o servidor, que recusa mesmo se a URL for digitada na mão.

⚠️ **Limitação conhecida:** os **totais do Dashboard** são somados no servidor sobre a
carteira inteira; filtrar linha a linha não recalcula agregado. Se o operador não puder
ver números globais, **não libere a rotina `dashboard`** para ele.

Senha e convites seguem o padrão do portal `queijeiros`: guarda-se o **sha256 do token**,
nunca o token; uso único, com validade; um convite novo invalida o anterior. A entrega do
link é feita pelo próprio admin (sem SMTP).

### Detalhe do 9º — por que era bloqueador de deploy

No boot, o bundle faz um `XMLHttpRequest` **síncrono** em `./config.json` e usa
`server_url + '/api'`; sem resposta válida cai no fallback `http://127.0.0.1:5847/api`.
Publicado em `https://ecac.gestaoempresa.com`, o navegador tentaria falar com o
localhost **do usuário** — nenhuma tela carregaria dado.

A rota devolve `request.host_url`, então serve local e servidor sem configuração e
sobrevive a troca de domínio. O `ProxyFix` é o par necessário: atrás do Traefik do
EasyPanel quem termina o TLS é o proxy, e sem honrar `X-Forwarded-Proto` o Flask
responderia `http://…` — bloqueado pelo navegador como conteúdo misto.

O `endswith('/config.json')` cobre a rota profunda: aberto direto em `/parcelamentos`,
o pedido relativo vira `/parcelamentos/config.json`.

**Testado:** local → `http://localhost:5847`; com `X-Forwarded-Proto: https` +
`X-Forwarded-Host: ecac.gestaoempresa.com` → `https://ecac.gestaoempresa.com`; rota
profunda idem; HTTP Basic seguiu 401/401/200 e `/healthz` livre.

O arquivo estático `app/static/app/config.json` foi mantido como último recurso (se
alguém servir a pasta sem o Flask).

Um desvio anterior (registrar custo de API no monitoramento) foi **revertido**: o
indicador é gratuito, conforme o próprio frontend do exe declara.

---

## Versão do Python (corrigindo um engano)

A aplicação **roda em Python 3.13** — é a versão do venv da máquina do Jean (3.13.12) e a
que foi testada contra a SERPRO real, com libs mais novas que as antes fixadas no
`requirements.txt` (Flask 3.1.3, lxml 6.1.1, cryptography 46.0.5, pycurl 7.45.7,
signxml 5.1.0). O `Dockerfile` usa 3.13 pelo mesmo motivo: **reproduzir o ambiente
testado**.

O **Python 3.12** só importa para as ferramentas de leitura do bytecode
(`marshal312.py` / `dis312.py`), porque o exe foi compilado nessa versão. Elas não fazem
parte da aplicação e não entram na imagem.

## Onde fica o bytecode (fora do repositório)

As ferramentas de engenharia reversa e o disassembly dos 33 módulos **não fazem parte do
sistema** e foram movidos para fora do repositório:

```
Central Pendencias Ecac/
├── Central eCac/               <- o repositório git (o sistema)
└── _ARQUIVO/                    <- NÃO versionado
    ├── engenharia_reversa/
    │   ├── exe_reverse/         marshal312.py, dis312.py, outline.py, schema.py,
    │   │                        rotas.py + dis/ (disassembly dos 33 módulos)
    │   └── dis_saida_*.txt      dumps avulsos
    ├── backups/                 bancos antigos e o app/ pré-rebuild
    ├── logs_antigos/
    └── handoffs_antigos/
```

O exe extraído continua em
`00_PROJETOS/Central Pendencias/centralpendencias24072026/IntegraContadorDesktop.exe_extracted/PYZ-00.pyz_extracted/app/`.

**Os scripts de auditoria (`auditar_*.py`) dependem desses caminhos.** Se mover as
pastas, ajuste a constante `EXT` no topo de cada script.

### Duas armadilhas já resolvidas

O exe é Python 3.12 e a máquina tem 3.13:
- a *property* `co_code` do 3.13 desespecializa o bytecode 3.12 e **corrompe o payload**
  → use `marshal312.py`, que lê o stream direto;
- o `dis` do 3.13 usa a tabela de opcodes errada → use `dis312.py`, com a tabela do 3.12
  extraída do próprio `opcode.pyc` empacotado no exe.

---

## Checklist dos 7 erros que se repetiram em todos os módulos

Antes de escrever qualquer chamada à SERPRO:

1. `SerproService` exige **6 argumentos**: `(certificate_content, certificate_password,
   contratante_cnpj, contador_cnpj, consumer_key, consumer_secret)`.
2. O token vem de `get_auth_token()` / `_get_auth_token()` — devolve `access_token` **e**
   `jwt_token`. **Não existe** `get_access_token()`.
3. **Não existe `SerproService.post()`.** O exe faz POST com **pycurl** (padrão de
   `request_protocol`) ou com `serpro_post` (requests) nos módulos que usam requests.
4. `endpoint` é só o **sufixo** do gateway: `Consultar` / `Emitir` / `Apoiar` /
   `Monitorar` — nunca o caminho completo (senão a URL duplica e dá 404).
5. Assinaturas de `serpro_logging`:
   `log_serpro_request(method, url, headers=, payload=, context=) -> started_at`;
   `log_serpro_response(url, status_code=, body=, headers=, started_at=, context=)`;
   `log_serpro_exception(url, exc, started_at=, context=)`.
6. Custo: `ApiUsageService.register_usage(route_type=, endpoint=, company_id=)`.
   **Não existe** `log_usage()`.
7. A SERPRO devolve `dados` como **string JSON** → precisa de um 2º `json.loads`. E as
   datas vêm como inteiro **`YYYYMMDD`**.

E o envelope do pedido tem **quatro** chaves de primeiro nível — `contratante`,
`autorPedidoDados`, `contribuinte`, `pedidoDados` — com `dados` em **string** dentro de
`pedidoDados`. Mandar `idSistema`/`idServico` na raiz faz a SERPRO devolver 500 com os
quatro campos nulos.
