# WhatsApp (uazapi) e tela de contatos — 19º desvio

**Decisão (30/09/2026):** o central-ecac passa a mandar WhatsApp **pela mesma instância
uazapi do portal nescon-clientes, com as mesmas regras**, portadas linha a linha de
`api/src/uazapi.js`, `whatsappNumero.js`, `whatsappDestino.js`, `janelaEnvio.js` e do
limitador de `alertasEnvio.js`. A tela `/contatos` é onde o número de cada empresa é
cadastrado e conferido — e **só número cadastrado ali recebe mensagem**.

| Portal (Node) | Aqui (Python) |
|---|---|
| `whatsappNumero.js` | `app/services/whatsapp_numero.py` |
| `janelaEnvio.js` + `diasBancarios` | `app/services/janela_envio.py` (+ `calendario_util.py`) |
| `uazapi.js`, `whatsappDestino.js`, teto/retry de `alertasEnvio.js` | `app/services/uazapi_service.py` |
| `companies.whatsapp/phone` + espelho G-Click | tabela `contatos_empresa` (`app/integracao_models.py`) |
| `alert_sends` / `alert_failures` | tabela `whatsapp_envios` |
| `/admin/whatsapp` (status) + `/admin/enviar-acesso` | tela `/contatos` (`app/routes/contatos.py`) |

## As regras (iguais ao portal)

1. **Só cliente cadastrado.** `exigir_destino_permitido` dentro de todo envio: número de
   empresa ativa com contato ativo, ou número do escritório (`ADMIN_WHATSAPP`,
   `ESCRITORIO_WHATSAPP`). Chave de comparação = DDD + 8 últimos dígitos. Cache de 1 min.
   Desligar só em emergência: `WHATSAPP_SO_CLIENTES=false`.
2. **Só celular válido** (55 + DDD real + 9 + 8 dígitos). Fixo é recusado com mensagem
   própria para o operador corrigir o cadastro.
3. **Janela diurna** 08:00–19:00 de Brasília, em dia útil (aqui com feriado nacional).
   O teste manual da tela ignora a janela de propósito.
4. **Teto por hora** (`ALERTAS_MAX_POR_HORA`), **pausa entre envios**
   (`ALERTAS_THROTTLE_S`) e **"digitando…"** (`ALERTAS_DELAY_MS`).
5. **Não manda para o próprio número** da instância.
6. **Uma retentativa** em falha transitória; token inválido e destino bloqueado não se
   repetem.
7. **Tudo registrado** em `whatsapp_envios` (enviado / falhou / bloqueado, motivo, id).

⚠️ **O teto por hora é por processo.** O portal tem o dele e a instância é uma só.
Padrão aqui: 90/h (o portal usa 180). Ajuste para a soma ficar dentro do que o número
aguenta.

## Diferença única

`enviar_documento` aceita **bytes** além de URL pública: o PDF de DAS/parcela mora no
volume deste sistema. Vai como `data:application/pdf;base64,…` no campo `file`.
⚠️ Validar em produção com um PDF pequeno antes de usar em lote.

## Tela `/contatos`

* Status da instância (conectada / desconectada / token inválido), trava, janela, teto
  da última hora, webhook cadastrado (token mascarado).
* Uma linha por empresa: WhatsApp, WhatsApp 2 (sócio), e-mail, situação da verificação,
  último envio. O envio usa o **primeiro celular válido** entre os dois campos.
* **Verificar**: formato (sempre) e existência no WhatsApp via `POST /chat/check` da
  uazapi (quando responde). Estados: OK · formato inválido · sem WhatsApp · não verificado.
* **Teste**: manda uma mensagem real marcada como TESTE para o número da empresa.
* **Importar lote (JSON)**: `[{"cnpj","whatsapp","whatsapp_2","email","responsavel"}]`,
  casado por CNPJ — serve para trazer os contatos do portal.
* **Desativar** tira a empresa da lista de permitidos sem apagar o cadastro.

## API

| Método | Rota | O que faz |
|---|---|---|
| GET | `/api/contatos?inativas=1` | lista + resumo |
| POST | `/api/contatos/<company_id>` | salva `{whatsapp, whatsapp_2, email, responsavel, observacao, ativo}` |
| POST | `/api/contatos/importar` | `{itens: [...]}` |
| POST | `/api/contatos/verificar` | `{company_ids?, online?}` |
| POST | `/api/contatos/teste` | `{company_id, texto?}` |
| GET | `/api/contatos/whatsapp/status` | instância, trava, janela, teto |
| GET | `/api/contatos/historico?company_id=` | `whatsapp_envios` |

Permissão: rotina `contatos` (`permissoes.py`); usuário restrito só mexe nas empresas dele.

## Como usar no código

```python
from app.services import uazapi_service as uazapi

r = uazapi.enviar('34 99999-8888', 'texto', company_id=12, contexto='das_mei')
r = uazapi.enviar('34 99999-8888', 'segue a guia', company_id=12, contexto='das_mei',
                  nome_arquivo='DAS_202609.pdf', conteudo=pdf_bytes)
# r = {'ok': True, 'numero': '5534999998888', 'mensagem_id': ...} ou {'ok': False, 'motivo': ...}
```

`enviar` nunca lança: aplica todas as travas, registra e devolve o motivo.

## Variáveis

`UAZAPI_SUBDOMAIN`, `UAZAPI_TOKEN` (as mesmas do portal), `WHATSAPP_SO_CLIENTES`,
`ADMIN_WHATSAPP`, `ESCRITORIO_WHATSAPP`, `ALERTAS_THROTTLE_S`, `ALERTAS_MAX_POR_HORA`,
`ALERTAS_DELAY_MS`. Ver `.env.example`.

## Testes

```bash
python -m pytest tests/test_whatsapp_contatos.py -q
```

## Avisos internos ao escritório (o uso deste módulo)

`app/services/avisos_internos.py`: ao fim de cada lote (situação fiscal, parcelamentos)
o escritório recebe um WhatsApp com o resultado — empresas ok, falhas (com nomes),
puladas por procuração, lote interrompido pelo teto — e a fila de reprocessamento avisa
falha definitiva ou teto. O aviso entra numa fila em arquivo
(`<DATA_DIR>/instance/avisos_internos.json`) e sai na próxima passagem do agendador
**dentro da janela diurna**: o lote roda de madrugada, o aviso chega de manhã.
Destino: `ESCRITORIO_WHATSAPP`, senão `ADMIN_WHATSAPP`. Sem os dois, fica só no log.
