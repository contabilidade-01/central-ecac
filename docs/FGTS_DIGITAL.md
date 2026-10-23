# Robô do FGTS Digital — recálculo de guia pelo link sem senha (desvio 22)

> Pedido do Jean (23/10/2026). O cliente pede pelo AtendeChat (link `/g/<token>`), o robô
> faz todo o trabalho no FGTS Digital e o cliente acompanha as etapas na tela. Se o gov.br
> pedir a verificação de imagens (hCaptcha), **o cliente resolve na hora** pela própria
> página e o fluxo continua sozinho.

## Fluxo (mapeado na tela em 23/10/2026, perfil Procurador da 36.298.150/0001-70)

| # | Tela | O que o robô faz |
|---|---|---|
| 1 | `portal/servicos` → `portal/login` | "Entrar com GOV.BR" |
| 2 | `sso.acesso.gov.br/login` | "Seu certificado digital". O gov.br **às vezes** abre um hCaptcha de imagens (decide por risco). |
| 3 | `certificado.sso.acesso.gov.br` | o Chromium apresenta o `.pfx` da Nescon direto no TLS (`client_certificates`) — **não existe** a janela "Selecione um certificado" |
| 4 | `portal/escolhaPerfil` (modal "Definir Perfil", `ng-select`) | Perfil **Procurador** + CNPJ do link → Definir. Sem procuração: "Não existe procuração para o Número de Inscrição selecionado." |
| 5 | `cobranca/#/gestao-guias/emissao-guia-rapida` | lê **todas** as competências da lista "Competência de Apuração" e, para cada uma, Pesquisar → Total Devedor, grupos (Mensal/Rescisório…, vencimento da guia) e composição (FGTS mensal, rescisório, indenização, processo trabalhista, encargos) |
| 6 | idem | o cliente marca um, vários ou todos os meses → "Emitir guia" de cada grupo → GFD em PDF (vencimento **hoje**, valor atualizado até hoje) |

Tudo numa sessão só: consulta → cliente escolhe (até `FGTS_ESCOLHA_TIMEOUT_S`) → emite →
**fecha o navegador** (regra do Jean: sempre fecha, deu certo ou não). Se o cliente não
escolher a tempo, a lista fica na página e "Gerar guia(s)" abre um pedido novo (login novo).

## Captcha — como o cliente resolve

1. O robô detecta o iframe grande do hCaptcha e põe o pedido em `aguardando_captcha`.
2. A página do link mostra o **recorte do desafio** (JPEG atualizado a cada 1 s).
3. Cada toque do cliente vai como fração (0..1) da imagem → o robô move o mouse até o
   ponto e clica no navegador do servidor.
4. Saiu o desafio → o fluxo continua sozinho. Sem resolver em `FGTS_CAPTCHA_TIMEOUT_S`
   → erro "A verificação de segurança não foi concluída a tempo. Peça de novo."

O robô **nunca** tenta resolver o captcha sozinho.

## Segurança

* Página e API só aceitam o **token do link**; pedido, captcha e PDF de outra empresa = 404.
* Antes de pesquisar e de emitir, o robô confere na tela `Empregador: <CNPJ do link>`.
* Depois de baixar, confere a **raiz do CNPJ no texto do PDF**; a rota confere de novo
  ao servir. PDF fica em `<DATA_DIR>/fgts/<cnpj>/` e a rota nunca sai dessa pasta.
* Um pedido aberto por empresa; `FGTS_MAX_PEDIDOS_DIA` pedidos por link por dia.
* A escolha só aceita meses que o **próprio FGTS Digital** listou naquele pedido.
* Diálogos JS são fechados; o contexto nasce sem permissão de localização; banner de
  cookies é dispensado. Print da tela em erro vai para `<DATA_DIR>/fgts/_diagnostico/`
  (nunca para o cliente).

## Variáveis

| Variável | Padrão | Para quê |
|---|---|---|
| `FGTS_DIGITAL_HABILITADO` | `0` | liga a seção FGTS na página do link |
| `FGTS_MAX_PEDIDOS_DIA` | `6` | pedidos por link por dia |
| `FGTS_CAPTCHA_TIMEOUT_S` | `300` | tempo para o cliente resolver o captcha |
| `FGTS_ESCOLHA_TIMEOUT_S` | `240` | tempo da sessão aberta esperando o cliente marcar os meses |
| `FGTS_LOGIN_TIMEOUT_S` | `60` | tempo máximo do login (fora o captcha) |
| `FGTS_MAX_COMPETENCIAS` | `0` | 0 = todas as competências em aberto |
| `FGTS_CERTIFICADO_PATH` / `FGTS_CERTIFICADO_SENHA` | — | só se for usar outro A1; por padrão usa o da tela Configurações |
| `FGTS_HEADLESS` | `1` | `0` para ver o navegador (só em máquina com tela) |
| `FGTS_CHROMIUM_PATH` | — | outro executável do Chromium |

## Na VPS

* O `Dockerfile` instala o Chromium do Playwright (`playwright install --with-deps chromium`).
* 1 worker do Gunicorn (já é assim): o robô é **um** thread dono do navegador; pedidos
  em fila, um por vez. Pedido que estava andando quando o container caiu volta para a fila.
* Memória: o Chromium só existe durante o pedido (≈300 MB) e fecha no fim.

## Rotas

| Rota | O quê |
|---|---|
| `POST /api/acesso/<token>/fgts/pedidos` `{acao: consultar}` ou `{acao: emitir, competencias: [AAAAMM…]}` | cria (ou retoma o aberto) |
| `GET /api/acesso/<token>/fgts/pedidos/<id>` | etapas, meses, guias |
| `POST /api/acesso/<token>/fgts/pedidos/<id>/escolha` `{competencias: [...]}` | meses escolhidos na sessão aberta |
| `GET /api/acesso/<token>/fgts/pedidos/<id>/captcha.jpg` | recorte do desafio |
| `POST /api/acesso/<token>/fgts/pedidos/<id>/captcha/toque` `{x, y}` | toque do cliente (0..1) |
| `GET /api/acesso/<token>/fgts/pedidos/<id>/pdf/<n>` | GFD |
| `GET /api/interno/fgts/estado`, `GET /api/interno/fgts/pedidos?cnpj=` | para o AtendeChat/escritório |

## Antes de ligar em produção

1. `FGTS_DIGITAL_HABILITADO=1` e implantar.
2. Abrir o link de **uma** empresa com procuração (ex.: J F DA SILVA), consultar, emitir
   um mês e conferir o PDF com o que o portal mostra.
3. Se o login falhar, olhar `<DATA_DIR>/fgts/_diagnostico/` (print da tela).
