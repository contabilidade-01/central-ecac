"""Situação de procuração por empresa e trava de chamadas PAGAS.

⚠️ DESVIO INTENCIONAL (5o) — este módulo NÃO existe no exe. Foi pedido pelo Jean
(31/07/2026) com a justificativa de não gastar API repetindo chamada em empresa que a
SERPRO recusa. Regra que ele fixou: **guardar o erro EXATO**, porque pode haver erro
mesmo com procuração válida e não se pode confundir os dois casos.

## Por que dá para mapear de graça

O próprio frontend do exe declara:

  * "Essa função é gratuita na API Integra Contador."      -> monitorar (INNOVAMSG63)
  * "As funções de buscar mensagens e detalhes são pagas." -> MSGCONTRIBUINTE61 / MSGDETALHAMENTO62

Ou seja: o **indicador é a sonda gratuita**. Varrer as 72 empresas com ele custa R$ 0,00
e revela quem a SERPRO recusa. Só depois disso é que se gasta na lista/detalhe.

## Como a classificação funciona (e o que ela NÃO faz)

Nós ainda não temos amostra do erro que a SERPRO devolve quando falta procuração —
**por isso o padrão de detecção não foi inventado**. O serviço trabalha em dois níveis:

1. `erro` — QUALQUER falha. Guarda status HTTP, código, texto e o corpo bruto, conta
   erros seguidos e, a partir de `MAX_ERROS_SEGUIDOS`, **trava as chamadas pagas** por
   `BACKOFF_HORAS`. Isso já cumpre "não ficar gastando" sem adivinhar nada.
2. `sem_procuracao` — só quando o código/texto casar com um padrão de
   `padroes_sem_procuracao` (lista guardada no próprio JSON, **começa vazia**) ou quando
   o Jean marcar na mão. Aí a trava é permanente até alguém liberar.

Quando o erro real aparecer numa varredura, basta acrescentar o trecho identificador em
`padroes_sem_procuracao` — nenhum código muda.

Arquivo: `<DATA_DIR>/procuracoes.json` (mesma pasta do banco, sincroniza pelo OneDrive).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.config import DB_PATH

logger = logging.getLogger(__name__)

#: fica ao lado do banco (`instance/`), que pelo DESVIO 2 mora na pasta do projeto e
#: sincroniza pelo OneDrive. NAO usar `app_data_dir()`: ele continua apontando para
#: %LOCALAPPDATA% e a marcacao nao chegaria na outra maquina do Jean.
ARQUIVO = "procuracoes.json"
VERSAO = 1

#: a partir de quantas falhas seguidas as chamadas PAGAS ficam travadas
MAX_ERROS_SEGUIDOS = 2
#: por quantas horas a trava por erro vale (a sonda gratuita continua rodando)
BACKOFF_HORAS = 24

SITUACAO_OK = "ok"
SITUACAO_SEM_PROCURACAO = "sem_procuracao"
SITUACAO_ERRO = "erro"
SITUACAO_DESCONHECIDA = "desconhecida"

_LOCK = threading.Lock()


def _agora() -> str:
    return datetime.utcnow().isoformat()


def _only_digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


class ProcuracaoService:
    """Leitura/escrita do mapa de procurações. Todos os métodos são estáticos."""

    # ------------------------------------------------------------------ arquivo

    @staticmethod
    def caminho() -> Path:
        return Path(DB_PATH).parent / ARQUIVO

    @staticmethod
    def carregar() -> Dict[str, Any]:
        caminho = ProcuracaoService.caminho()
        if caminho.exists():
            try:
                dados = json.loads(caminho.read_text(encoding="utf-8"))
                dados.setdefault("empresas", {})
                dados.setdefault("padroes_sem_procuracao", [])
                return dados
            except Exception:
                logger.exception("procuracoes.json ilegível — recomeçando do zero: %s", caminho)
        return {
            "versao": VERSAO,
            "atualizado_em": _agora(),
            "padroes_sem_procuracao": [],
            "empresas": {},
        }

    @staticmethod
    def salvar(dados: Dict[str, Any]) -> None:
        dados["versao"] = VERSAO
        dados["atualizado_em"] = _agora()
        caminho = ProcuracaoService.caminho()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(
            json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _registro(dados: Dict[str, Any], company) -> Dict[str, Any]:
        cnpj = _only_digits(getattr(company, "cnpj", company))
        registro = dados["empresas"].setdefault(cnpj, {
            "situacao": SITUACAO_DESCONHECIDA,
            "erros_seguidos": 0,
            "marcado_manualmente": False,
        })
        registro["company_id"] = getattr(company, "id", registro.get("company_id"))
        registro["razao_social"] = getattr(
            company, "razao_social", registro.get("razao_social"))
        return registro

    # ------------------------------------------------------- extração do erro

    @staticmethod
    def detalhar_erro(erro: Any) -> Dict[str, Any]:
        """Extrai status/código/texto da exceção levantada por `_post`.

        A mensagem do exe é
        `Erro SERPRO <idServico>. Status: <n>. Resposta: <corpo>` — daí dá para tirar o
        status e, quando o corpo é JSON, o `mensagens[0].codigo/texto` da SERPRO.
        O corpo bruto é guardado SEMPRE, mesmo quando não dá para interpretar.
        """
        bruto = str(erro)
        detalhe: Dict[str, Any] = {"bruto": bruto[:4000], "codigo": None,
                                   "texto": None, "http_status": None}

        status = re.search(r"Status:\s*(\d{3})", bruto)
        if status:
            detalhe["http_status"] = int(status.group(1))

        corpo = re.search(r"Resposta:\s*(.*)", bruto, re.S)
        if corpo:
            try:
                payload = json.loads(corpo.group(1))
            except Exception:
                payload = None
            if isinstance(payload, dict):
                mensagens = payload.get("mensagens")
                if isinstance(mensagens, list) and mensagens:
                    primeira = mensagens[0]
                    if isinstance(primeira, dict):
                        detalhe["codigo"] = primeira.get("codigo")
                        detalhe["texto"] = primeira.get("texto")
        return detalhe

    @staticmethod
    def _casa_sem_procuracao(detalhe: Dict[str, Any], padroes) -> bool:
        alvo = " ".join(str(detalhe.get(campo) or "")
                        for campo in ("codigo", "texto", "bruto")).lower()
        return any(str(p).lower() in alvo for p in padroes if str(p).strip())

    # --------------------------------------------------------------- registro

    @staticmethod
    def registrar_sucesso(company, servico: str) -> None:
        """Chamada deu certo: zera o contador e libera as chamadas pagas.

        Não mexe em empresa marcada NA MÃO como sem procuração — só o Jean tira essa.
        """
        with _LOCK:
            dados = ProcuracaoService.carregar()
            registro = ProcuracaoService._registro(dados, company)
            if not (registro.get("marcado_manualmente")
                    and registro.get("situacao") == SITUACAO_SEM_PROCURACAO):
                registro["situacao"] = SITUACAO_OK
                registro["ultimo_erro"] = None
            registro["erros_seguidos"] = 0
            registro["ultimo_ok_em"] = _agora()
            registro["ultimo_ok_servico"] = servico
            ProcuracaoService.salvar(dados)

    @staticmethod
    def registrar_erro(company, servico: str, erro: Any) -> Dict[str, Any]:
        """Guarda o erro EXATO e decide se trava as chamadas pagas."""
        with _LOCK:
            dados = ProcuracaoService.carregar()
            registro = ProcuracaoService._registro(dados, company)
            detalhe = ProcuracaoService.detalhar_erro(erro)
            detalhe["servico"] = servico
            detalhe["em"] = _agora()

            registro["ultimo_erro"] = detalhe
            registro["ultimo_erro_em"] = detalhe["em"]
            registro["erros_seguidos"] = int(registro.get("erros_seguidos") or 0) + 1

            if ProcuracaoService._casa_sem_procuracao(
                    detalhe, dados.get("padroes_sem_procuracao", [])):
                registro["situacao"] = SITUACAO_SEM_PROCURACAO
            elif not registro.get("marcado_manualmente"):
                registro["situacao"] = SITUACAO_ERRO

            ProcuracaoService.salvar(dados)
            return detalhe

    @staticmethod
    def marcar(cnpj: str, situacao: str, observacao: str = "") -> Dict[str, Any]:
        """Marcação manual do Jean (`sem_procuracao` trava; `ok` libera)."""
        with _LOCK:
            dados = ProcuracaoService.carregar()
            registro = ProcuracaoService._registro(dados, _only_digits(cnpj))
            registro["situacao"] = situacao
            registro["observacao"] = observacao
            registro["marcado_manualmente"] = situacao == SITUACAO_SEM_PROCURACAO
            registro["marcado_em"] = _agora()
            if situacao == SITUACAO_OK:
                registro["erros_seguidos"] = 0
                registro["ultimo_erro"] = None
            ProcuracaoService.salvar(dados)
            return registro

    # ------------------------------------------------------------------ trava

    @staticmethod
    def pode_gastar(company) -> Tuple[bool, Optional[str]]:
        """(pode chamar serviço PAGO?, motivo do bloqueio).

        A sonda gratuita (indicador) NUNCA é bloqueada — é ela que reabilita a empresa.
        """
        dados = ProcuracaoService.carregar()
        cnpj = _only_digits(getattr(company, "cnpj", company))
        registro = dados["empresas"].get(cnpj)
        if not registro:
            return True, None

        if registro.get("situacao") == SITUACAO_SEM_PROCURACAO:
            origem = "marcada manualmente" if registro.get("marcado_manualmente") \
                else "recusada pela SERPRO"
            return False, f"sem procuração ({origem})"

        erros = int(registro.get("erros_seguidos") or 0)
        if erros >= MAX_ERROS_SEGUIDOS:
            ultimo = registro.get("ultimo_erro_em")
            if ultimo:
                try:
                    quando = datetime.fromisoformat(ultimo)
                except ValueError:
                    return False, f"{erros} erros seguidos"
                if datetime.utcnow() - quando < timedelta(hours=BACKOFF_HORAS):
                    texto = (registro.get("ultimo_erro") or {}).get("texto") \
                        or (registro.get("ultimo_erro") or {}).get("codigo") or "sem detalhe"
                    return False, f"{erros} erros seguidos em {BACKOFF_HORAS}h ({texto})"
        return True, None

    # ----------------------------------------------------------------- leitura

    @staticmethod
    def situacao(company) -> Dict[str, Any]:
        dados = ProcuracaoService.carregar()
        cnpj = _only_digits(getattr(company, "cnpj", company))
        return dados["empresas"].get(cnpj, {"situacao": SITUACAO_DESCONHECIDA})

    @staticmethod
    def resumo() -> Dict[str, Any]:
        """Contagem por situação + a lista de quem está travado para chamadas pagas."""
        dados = ProcuracaoService.carregar()
        contagem: Dict[str, int] = {}
        bloqueadas = []
        for cnpj, registro in dados["empresas"].items():
            situacao = registro.get("situacao", SITUACAO_DESCONHECIDA)
            contagem[situacao] = contagem.get(situacao, 0) + 1
            if situacao in (SITUACAO_SEM_PROCURACAO, SITUACAO_ERRO):
                ultimo = registro.get("ultimo_erro") or {}
                bloqueadas.append({
                    "cnpj": cnpj,
                    "company_id": registro.get("company_id"),
                    "razao_social": registro.get("razao_social"),
                    "situacao": situacao,
                    "erros_seguidos": registro.get("erros_seguidos", 0),
                    "codigo": ultimo.get("codigo"),
                    "texto": ultimo.get("texto"),
                    "http_status": ultimo.get("http_status"),
                    "servico": ultimo.get("servico"),
                    "em": registro.get("ultimo_erro_em"),
                    "marcado_manualmente": registro.get("marcado_manualmente", False),
                })
        bloqueadas.sort(key=lambda r: (r["situacao"] != SITUACAO_SEM_PROCURACAO,
                                       r["razao_social"] or ""))
        return {
            "atualizado_em": dados.get("atualizado_em"),
            "arquivo": str(ProcuracaoService.caminho()),
            "padroes_sem_procuracao": dados.get("padroes_sem_procuracao", []),
            "total": len(dados["empresas"]),
            "contagem": contagem,
            "bloqueadas": bloqueadas,
        }
