"""Cliente SERPRO Integra Contador — serviços PGDASD usados pela área Escritório.

⚠️ DESVIO INTENCIONAL (17o) — não existe no exe. Pedido do Jean em 15/09/2026:
"cada erro custa dinheiro". Por isso este cliente é deliberadamente conservador.

Contrato (documentação SERPRO, idSistema PGDASD, versaoSistema "1.0")
--------------------------------------------------------------------
| idServico           | Endpoint   | Custo (tipo) | Idempotente? |
|---------------------|------------|--------------|--------------|
| TRANSDECLARACAO11   | /Declarar  | declarar     | NÃO          |
| GERARDAS12          | /Emitir    | emitir       | NÃO (gera DAS novo) |
| CONSDECLARACAO13    | /Consultar | consultar    | sim          |
| CONSULTIMADECREC14  | /Consultar | consultar    | sim          |

Regras de segurança financeira implementadas AQUI
-------------------------------------------------
1. **Sem retry automático de negócio.** Só há UMA repetição, e apenas quando o gateway
   recusa a autenticação (401/403) — o pedido nem chegou ao PGDAS-D.
2. **Timeout depois de enviar = resultado INCERTO**, nunca "erro". Quem chama tem de
   Consultar antes de tentar de novo (senão arrisca duas transmissões/duas cobranças).
3. **Token reaproveitado** por alguns minutos (o fluxo Consultar→Declarar→Emitir não
   autentica 3 vezes).
4. **Toda chamada é auditada** (`escritorio_serpro_chamadas`) e entra no custo do mês
   (`api_usage_logs`), que é a mesma base do teto de gasto.
5. O corpo das mensagens da SERPRO é classificado por prefixo do código
   (`Sucesso`/`Aviso` = ok; `Erro`/`EntradaIncorreta` = falha), porque o HTTP 200 NÃO
   garante sucesso de negócio (ex.: GERARDAS12 → `Aviso-PGDASD-MSG_ISN_005`).

O transporte é injetável (`transporte=`) para os testes rodarem SEM rede e SEM custo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = os.getenv('SERPRO_INTEGRA_BASE_URL',
                     'https://gateway.apiserpro.serpro.gov.br/integra-contador/v1')


@dataclass(frozen=True)
class Servico:
    id_sistema: str
    id_servico: str
    versao: str
    endpoint: str          # Declarar | Emitir | Consultar
    tipo_custo: str        # declarar | emitir | consultar
    timeout_leitura: int   # segundos


SERVICOS: Dict[str, Servico] = {
    'TRANSDECLARACAO11': Servico('PGDASD', 'TRANSDECLARACAO11', '1.0', 'Declarar', 'declarar', 180),
    'GERARDAS12': Servico('PGDASD', 'GERARDAS12', '1.0', 'Emitir', 'emitir', 120),
    'CONSDECLARACAO13': Servico('PGDASD', 'CONSDECLARACAO13', '1.0', 'Consultar', 'consultar', 60),
    'CONSULTIMADECREC14': Servico('PGDASD', 'CONSULTIMADECREC14', '1.0', 'Consultar', 'consultar', 90),
}


def _decimal_env(nome: str, padrao: str) -> Decimal:
    try:
        return Decimal(os.getenv(nome, padrao))
    except Exception:
        return Decimal(padrao)


#: Estimativa por chamada (1ª faixa). Consultar/Emitir = valores já usados no sistema
#: (README). Declarar: CONFIRMAR NA TABELA DO CONTRATO — ajustável por variável de
#: ambiente sem mexer no código.
CUSTO_ESTIMADO: Dict[str, Decimal] = {
    'consultar': _decimal_env('SERPRO_CUSTO_CONSULTAR', '0.24'),
    'emitir': _decimal_env('SERPRO_CUSTO_EMITIR', '0.32'),
    'declarar': _decimal_env('SERPRO_CUSTO_DECLARAR', '0.40'),
}

#: Status em que consideramos que o PGDAS-D não chegou a processar (não cobrável).
STATUS_NAO_COBRAVEL = {401, 403, 407, 429, 502, 503, 504}

#: Códigos que indicam falha SISTÊMICA (do lado da SERPRO) — interrompem lotes.
CODIGOS_SISTEMICOS = ('MSG_ISN_001', 'MSG_ISN_012', 'MSG_ISN_022', 'MSG_ISN_031')

TOKEN_TTL_S = int(os.getenv('SERPRO_TOKEN_TTL_S', '1200'))   # 20 min


def custo_de(id_servico: str) -> Decimal:
    return CUSTO_ESTIMADO[SERVICOS[id_servico].tipo_custo]


def hash_dados(dados: Any) -> str:
    """SHA-256 do JSON canônico (chaves ordenadas) — identifica o conteúdo enviado."""
    texto = json.dumps(dados, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------- resultado
@dataclass
class Mensagem:
    codigo: str
    texto: str

    @property
    def tipo(self) -> str:
        cod = (self.codigo or '').strip().strip('[]')
        return cod.split('-', 1)[0] if cod else ''

    @property
    def falha(self) -> bool:
        return self.tipo in ('Erro', 'EntradaIncorreta')

    def tem(self, trecho: str) -> bool:
        return trecho in (self.codigo or '')


@dataclass
class Resultado:
    servico: str
    ok: bool = False
    http_status: Optional[int] = None
    mensagens: List[Mensagem] = field(default_factory=list)
    dados: Any = None
    erro_rede: bool = False
    incerto: bool = False
    erro: str = ''
    duracao_ms: int = 0
    cobravel: bool = False
    custo_estimado: float = 0.0
    chamada_id: Optional[int] = None

    @property
    def codigos(self) -> List[str]:
        return [m.codigo for m in self.mensagens if m.codigo]

    def tem_codigo(self, trecho: str) -> bool:
        return any(m.tem(trecho) for m in self.mensagens)

    @property
    def sistemico(self) -> bool:
        """Falha que não é do contribuinte (rede, gateway, SERPRO fora) — para lotes."""
        if self.erro_rede or self.incerto:
            return True
        if self.http_status and (self.http_status >= 500 or self.http_status in (401, 403, 429)):
            return True
        return any(self.tem_codigo(c) for c in CODIGOS_SISTEMICOS)

    @property
    def texto(self) -> str:
        if self.erro:
            return self.erro
        textos = [m.texto for m in self.mensagens if m.texto]
        return ' '.join(textos) or (f'HTTP {self.http_status}' if self.http_status else '')

    def resumo(self) -> Dict[str, Any]:
        return {
            'ok': self.ok, 'http_status': self.http_status, 'incerto': self.incerto,
            'erro_rede': self.erro_rede, 'sistemico': self.sistemico, 'mensagem': self.texto,
            'mensagens': [{'codigo': m.codigo, 'texto': m.texto} for m in self.mensagens],
            'custo_estimado': self.custo_estimado, 'cobravel': self.cobravel,
            'chamada_id': self.chamada_id,
        }


def _parse_mensagens(corpo: Any) -> List[Mensagem]:
    saida = []
    if isinstance(corpo, dict):
        for item in corpo.get('mensagens') or []:
            if isinstance(item, dict):
                saida.append(Mensagem(str(item.get('codigo') or ''), str(item.get('texto') or '')))
            elif isinstance(item, str) and item.strip():
                saida.append(Mensagem('', item.strip()))
        if not saida:
            for chave in ('description', 'message', 'mensagem'):
                if corpo.get(chave):
                    saida.append(Mensagem('', str(corpo[chave])))
                    break
    return saida


def _parse_dados(valor: Any) -> Any:
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None
        try:
            return json.loads(texto)
        except Exception:
            return texto
    return valor


# -------------------------------------------------------------- token cache
class _CacheToken:
    def __init__(self):
        self._lock = threading.Lock()
        self._chave = None
        self._headers = None
        self._expira = 0.0

    def obter(self, chave: str, gerar: Callable[[], Dict[str, str]]) -> Dict[str, str]:
        with self._lock:
            agora = time.monotonic()
            if self._headers and self._chave == chave and agora < self._expira:
                return dict(self._headers)
            headers = gerar()
            self._chave, self._headers, self._expira = chave, dict(headers), agora + TOKEN_TTL_S
            return dict(headers)

    def invalidar(self):
        with self._lock:
            self._headers, self._expira = None, 0.0


CACHE_TOKEN = _CacheToken()


def chave_token(setting) -> str:
    """Identifica as credenciais: trocou chave/certificado/contador → token novo."""
    return hashlib.sha256(
        f'{setting.serpro_consumer_key}|{setting.contador_cnpj}|{setting.certificado_path}'
        .encode('utf-8')).hexdigest()


def transporte_requests(url: str, headers: Dict[str, str], payload: Dict[str, Any],
                        timeout, contexto: str):
    """Transporte real (com o log sanitizado já usado pelo sistema)."""
    from app.services.serpro_logging import serpro_post
    return serpro_post(url, headers=headers, json_payload=payload, context=contexto,
                       timeout=timeout)


class SerproPgdasdClient:
    """Uma chamada = uma requisição auditada. Não decide regra de negócio."""

    def __init__(self, transporte: Optional[Callable] = None,
                 gerar_headers: Optional[Callable] = None,
                 montar_payload: Optional[Callable] = None):
        self._transporte = transporte or transporte_requests
        self._gerar_headers = gerar_headers
        self._montar_payload = montar_payload

    # -- dependências do sistema (lazy para permitir testes isolados) --------
    @staticmethod
    def _setting():
        from app.models import AppSetting
        return AppSetting.query.first()

    def _headers(self, setting) -> Dict[str, str]:
        if self._gerar_headers:
            return self._gerar_headers(setting)
        from app.services.serpro_das_service import SerproDasService
        return SerproDasService()._get_headers(setting)     # já usa CACHE_TOKEN

    def _payload(self, setting, cnpj: str, servico: Servico, dados: Dict[str, Any]):
        if self._montar_payload:
            return self._montar_payload(setting, cnpj, servico, dados)
        from app.services.serpro_das_service import SerproDasService
        return SerproDasService().montar_payload_emitir(
            setting=setting, contribuinte_numero=cnpj, id_sistema=servico.id_sistema,
            id_servico=servico.id_servico, dados=dados, versao_sistema=servico.versao)

    # -- chamada ------------------------------------------------------------
    def chamar(self, id_servico: str, cnpj: str, dados: Dict[str, Any], *,
               operacao: str, company=None, pa: str = '', usuario: str = '') -> Resultado:
        servico = SERVICOS[id_servico]
        resultado = Resultado(servico=id_servico)
        setting = self._setting()
        if not setting:
            resultado.erro = 'Configurações do sistema não encontradas.'
            return resultado

        try:
            payload = self._payload(setting, cnpj, servico, dados)
        except Exception as exc:        # erro local: nada saiu, nada custou
            resultado.erro = f'Não foi possível montar o pedido: {exc}'
            return resultado

        url = f'{BASE_URL}/{servico.endpoint}'
        hash_pedido = hash_dados(dados)
        for tentativa in (1, 2):
            try:
                headers = self._headers(setting)
            except Exception as exc:
                from app.services.serpro_erros import ErroAntesDoEnvio
                local = isinstance(exc, ErroAntesDoEnvio)
                resultado.erro = str(exc) if local else f'Falha ao autenticar na SERPRO: {exc}'
                resultado.erro_rede = not local
                self._auditar(resultado, servico, operacao, company, cnpj, pa, usuario,
                              hash_pedido, tentativa, cobravel=False)
                return resultado

            inicio = time.perf_counter()
            try:
                resposta = self._transporte(
                    url, headers, payload, (15, servico.timeout_leitura),
                    f'escritorio_{operacao}_{id_servico}')
            except requests.exceptions.ConnectTimeout as exc:
                resultado.duracao_ms = int((time.perf_counter() - inicio) * 1000)
                resultado.erro_rede = True
                resultado.erro = f'Sem conexão com a SERPRO (o pedido não saiu): {exc}'
                self._auditar(resultado, servico, operacao, company, cnpj, pa, usuario,
                              hash_pedido, tentativa, cobravel=False)
                return resultado
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError) as exc:
                # O pedido pode ter sido processado. NUNCA tratar como "não aconteceu".
                resultado.duracao_ms = int((time.perf_counter() - inicio) * 1000)
                resultado.erro_rede = True
                resultado.incerto = True
                resultado.erro = ('A SERPRO não respondeu a tempo. O pedido PODE ter sido '
                                  f'processado — consulte antes de repetir. ({exc.__class__.__name__})')
                self._auditar(resultado, servico, operacao, company, cnpj, pa, usuario,
                              hash_pedido, tentativa, cobravel=True)
                return resultado
            except Exception as exc:
                resultado.duracao_ms = int((time.perf_counter() - inicio) * 1000)
                resultado.erro_rede = True
                resultado.incerto = True
                resultado.erro = f'Falha inesperada na chamada à SERPRO: {exc}'
                self._auditar(resultado, servico, operacao, company, cnpj, pa, usuario,
                              hash_pedido, tentativa, cobravel=True)
                return resultado

            resultado.duracao_ms = int((time.perf_counter() - inicio) * 1000)
            resultado.http_status = int(getattr(resposta, 'status_code', 0) or 0)

            if resultado.http_status in (401, 403) and tentativa == 1:
                # Token vencido/recusado no gateway: renova e tenta UMA vez.
                self._auditar(resultado, servico, operacao, company, cnpj, pa, usuario,
                              hash_pedido, tentativa, cobravel=False,
                              mensagem='Token recusado pelo gateway; renovando.')
                CACHE_TOKEN.invalidar()
                resultado = Resultado(servico=id_servico)
                continue
            break

        texto = getattr(resposta, 'text', '') or ''
        try:
            corpo = json.loads(texto) if texto else {}
        except Exception:
            corpo = {}
        resultado.mensagens = _parse_mensagens(corpo)
        resultado.dados = _parse_dados(corpo.get('dados')) if isinstance(corpo, dict) else None
        falha_negocio = any(m.falha for m in resultado.mensagens)
        resultado.ok = resultado.http_status == 200 and not falha_negocio
        if not resultado.ok and not resultado.mensagens:
            resultado.erro = f'SERPRO respondeu HTTP {resultado.http_status} sem mensagem.'
        cobravel = resultado.http_status not in STATUS_NAO_COBRAVEL
        self._auditar(resultado, servico, operacao, company, cnpj, pa, usuario,
                      hash_pedido, tentativa, cobravel=cobravel)
        return resultado

    # -- auditoria ------------------------------------------------------------
    def _auditar(self, resultado: Resultado, servico: Servico, operacao: str, company,
                 cnpj: str, pa: str, usuario: str, hash_pedido: str, tentativa: int,
                 cobravel: bool, mensagem: str = '') -> None:
        from app.escritorio_models import EscritorioSerproChamada
        from app.extensions import db
        from app.models import ApiUsageLog

        custo = CUSTO_ESTIMADO[servico.tipo_custo] if cobravel else Decimal('0')
        resultado.cobravel = cobravel
        resultado.custo_estimado = float(custo)
        company_id = getattr(company, 'id', None)
        try:
            linha = EscritorioSerproChamada(
                criado_em=datetime.utcnow(), usuario=(usuario or '')[:120],
                company_id=company_id, cnpj=cnpj, pa=pa or None, operacao=operacao,
                id_sistema=servico.id_sistema, id_servico=servico.id_servico,
                endpoint=servico.endpoint, http_status=resultado.http_status,
                sucesso=bool(resultado.ok), incerto=bool(resultado.incerto),
                codigos=','.join(resultado.codigos)[:500] or None,
                mensagem=(mensagem or resultado.texto or '')[:4000] or None,
                duracao_ms=resultado.duracao_ms, custo_estimado=float(custo),
                cobravel=cobravel, hash_dados=hash_pedido, tentativa=tentativa)
            db.session.add(linha)
            if cobravel:
                db.session.add(ApiUsageLog(
                    company_id=company_id, route_type=servico.tipo_custo,
                    endpoint=f'{servico.id_sistema}/{servico.id_servico}', quantity=1,
                    estimated_cost=custo, success=bool(resultado.ok)))
            db.session.commit()
            resultado.chamada_id = linha.id
        except Exception:
            db.session.rollback()
            logger.exception('Falha ao auditar chamada SERPRO %s', servico.id_servico)

        if company is None or mensagem:
            return
        try:
            from app.services.procuracao_service import ProcuracaoService
            if resultado.ok:
                ProcuracaoService.registrar_sucesso(company, servico.id_servico)
            elif (resultado.http_status and not resultado.sistemico
                  and not any(m.tipo == 'EntradaIncorreta' for m in resultado.mensagens)):
                # Erro de preenchimento (EntradaIncorreta) é do nosso JSON, não da
                # procuração — não entra na contagem que trava a empresa.
                corpo = json.dumps({'mensagens': [{'codigo': m.codigo, 'texto': m.texto}
                                                  for m in resultado.mensagens]},
                                   ensure_ascii=False)
                ProcuracaoService.registrar_erro(
                    company, servico.id_servico,
                    f'Erro SERPRO {servico.id_servico}. Status: {resultado.http_status}. '
                    f'Resposta: {corpo}')
        except Exception:
            logger.exception('Falha ao atualizar mapa de procurações')
