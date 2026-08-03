"""
Servico de PROCURADOR ELETRONICO via SERPRO (Integra Contador).

Compõe com SerproService para autenticacao e mTLS (NAO alterada).
Ajusta apenas:
  - _validate_common / _validate_procurador
  - get_auth_tokens (chama _get_auth_token do SerproService)
  - save_authorization_token (envia XML assinado)
  - Assinatura XML (RSA-SHA256 com Canonicalizacao c14n20010315)

Endpoint Apoiar: POST .../integra-contador/v1/Apoiar
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

try:
    from cryptography import x509  # type: ignore
    from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
    from cryptography.hazmat.primitives.serialization import pkcs12  # type: ignore
    from cryptography.x509.oid import NameOID  # type: ignore
    from signxml import DigestAlgorithm, SignatureAlgorithm, SignatureBuilder  # type: ignore
    HAS_SIGNXML = True
except Exception:  # pragma: no cover - import opcional
    HAS_SIGNXML = False

try:
    from app.services.xml_signer import (  # type: ignore
        assinar_xml_enveloped as _xml_assinar_enveloped,
        carregar_certificado as _xml_carregar_certificado,
    )
    HAS_XML_SIGNER = True
except Exception:  # pragma: no cover
    HAS_XML_SIGNER = False
    _xml_assinar_enveloped = None  # type: ignore
    _xml_carregar_certificado = None  # type: ignore

try:
    from app.services.serpro_logging import (  # type: ignore
        log_serpro_request,
        log_serpro_response,
        log_serpro_exception,
    )
except Exception:  # pragma: no cover
    def log_serpro_request(*args, **kwargs):  # type: ignore
        pass

    def log_serpro_response(*args, **kwargs):  # type: ignore
        pass

    def log_serpro_exception(*args, **kwargs):  # type: ignore
        pass

# Import lazy para evitar circular import (serpro_service importa este modulo)
SerproService = None  # sera resolvido em _get_serpro_service_class()


def only_digits(value):
    """Linha 27 do exe."""
    return re.sub(r"\D+", "", str(value or ""))


def _get_serpro_service_class():
    """Import lazy do SerproService para evitar circular import."""
    global SerproService
    if SerproService is None:
        try:
            from app.services.serpro_service import SerproService as _cls
            SerproService = _cls
        except Exception:
            pass
    return SerproService


logger = logging.getLogger(__name__)


def tipo_pessoa(numero: str) -> int:
    """Tipo do Integra Contador: 1 = CPF, 2 = CNPJ (linha 31 do exe).

    ATENCAO: a versao anterior devolvia 'FISICA'/'JURIDICA' e a `tipo_pessoa_xml`
    devolvia '1'/'2' — as duas estavam trocadas em relacao ao exe.
    """
    digits = only_digits(numero)
    if len(digits) == 11:
        return 1
    if len(digits) in (8, 14):
        return 2
    raise ValueError(f"CPF/CNPJ inválido para Integra Contador: {numero}")


def tipo_pessoa_xml(numero: str) -> str:
    """'PF' ou 'PJ' para o XML da procuracao (linha 40 do exe)."""
    return "PF" if len(only_digits(numero)) == 11 else "PJ"


class SerproProcuradorService:
    """Servico para PROCURADOR ELETRONICO via SERPRO (Integra Contador)."""

    ENDPOINT_APOIAR = "https://gateway.apiserpro.serpro.gov.br/integra-contador/v1/Apoiar"
    VERSAO_SISTEMA_AUTENTICA = "1.0"

    @staticmethod
    def enabled(setting) -> bool:
        """Indica se o procurador PF esta habilitado nas configuracoes.

        Reconstruida do bytecode do exe (linha 54):
            return bool(setting and setting.procurador_pf_habilitado)

        O restante deste modulo AINDA NAO foi convertido (modulo 5 do plano), mas este
        metodo e chamado por serpro_service, parcelamentos_serpro_service,
        caixa_postal_service, serpro_das_service e serpro_pagamentos_service — sem ele,
        todas essas operacoes estouram AttributeError.
        """
        return bool(setting and setting.procurador_pf_habilitado)

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Mantido para retrocompatibilidade com callers existentes
                     (a autenticacao real usa consumer_key/secret do banco).
        """
        self.api_key = api_key
        # Reutiliza toda a infra de autenticacao + mTLS
        self._serpro = None
        SvcClass = _get_serpro_service_class()
        if SvcClass is not None:
            try:
                self._serpro = SvcClass(api_key=api_key)
            except Exception as e:
                logger.warning("Falha ao inicializar SerproService base: %s", e)
                self._serpro = None

        # Cache do autenticar_procurador_token
        self._procurador_token: Optional[str] = None
        self._procurador_token_expires_at: float = 0.0
        self._procurador_token_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Carga de credenciais
    # ------------------------------------------------------------------
    def _load_credentials(self) -> Dict[str, str]:
        try:
            from app.models import AppSetting  # type: ignore
            settings = {s.key: (s.value or "") for s in AppSetting.query.all()}
        except Exception as e:
            logger.warning("Nao foi possivel ler AppSetting: %s", e)
            settings = {}

        return {
            "consumer_key": settings.get("serpro_consumer_key", "") or (self.api_key or ""),
            "consumer_secret": settings.get("serpro_consumer_secret", ""),
            "certificado_path": settings.get("certificado_path", ""),
            "certificado_password": settings.get("certificado_password", ""),
            "contador_cnpj": settings.get("contador_cnpj", ""),
            "procurador_cpf": settings.get("procurador_cpf", ""),
            "procurador_nome": settings.get("procurador_nome", ""),
            "procurador_certificado_password": settings.get("procurador_certificado_password", ""),
        }

    # ------------------------------------------------------------------
    # Validacoes
    # ------------------------------------------------------------------
    def _validate_common(self, cnpj: str, procurador: Optional[str] = None) -> Optional[str]:
        """
        Valida campos obrigatorios comuns (CNPJ do contratante e
        CPF do procurador). Retorna mensagem de erro ou None se ok.
        """
        cnpj_clean = only_digits(cnpj or "")
        if not cnpj_clean:
            return "CNPJ do contratante obrigatorio"
        if len(cnpj_clean) != 14:
            return "CNPJ do contratante invalido (deve ter 14 digitos)"

        if procurador is not None:
            proc_clean = only_digits(procurador or "")
            if not proc_clean:
                return "CPF do procurador obrigatorio"
            if len(proc_clean) != 11:
                return "CPF do procurador invalido (deve ter 11 digitos)"
        return None

    def _validate_procurador(self, procurador_cpf: str, procurador_nome: str) -> Optional[str]:
        """
        Valida campos do procurador PF.
        """
        proc_clean = only_digits(procurador_cpf or "")
        if not proc_clean:
            return "procurador_cpf obrigatorio"
        if len(proc_clean) != 11:
            return "procurador_cpf invalido (deve ter 11 digitos)"
        if not (procurador_nome or "").strip():
            return "procurador_nome obrigatorio"
        return None

    # ------------------------------------------------------------------
    # Tokens / autenticacao
    # ------------------------------------------------------------------
    def get_auth_tokens(self) -> Dict[str, Any]:
        """
        Retorna access_token e jwt_token usando o _get_auth_token do
        SerproService base (NAO alterado).
        """
        if self._serpro is None:
            return {
                "success": False,
                "error": "SerproService nao inicializado",
            }
        try:
            access_token = self._serpro._get_auth_token()  # type: ignore[attr-defined]
            if not access_token:
                return {"success": False, "error": "Falha na autenticacao SERPRO"}
            return {
                "success": True,
                "access_token": access_token,
                "jwt_token": getattr(self._serpro, "_jwt_token", None),
            }
        except Exception as e:
            logger.exception("Erro obter tokens: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Extrai chave/cert do .pfx
    # ------------------------------------------------------------------
    def _carregar_certificado(self, pfx_path: str, pfx_password: str):
        """Carrega (private_key, certificate) do .pfx."""
        try:
            from cryptography.hazmat.primitives import serialization  # type: ignore
            from cryptography.hazmat.primitives.serialization import pkcs12  # type: ignore
        except Exception:
            return None, None
        try:
            with open(pfx_path, "rb") as f:
                pfx_data = f.read()
            pw = pfx_password.encode("utf-8") if isinstance(pfx_password, str) else pfx_password
            private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, pw)
            return private_key, certificate
        except Exception as e:
            logger.exception("Falha ao carregar .pfx: %s", e)
            return None, None

    def _gerar_xml_procuracao(
        self,
        outorgante_doc: str,
        procurador_doc: str,
        data_inicio: datetime,
        data_fim: datetime,
    ) -> str:
        """Monta o XML de procuração no formato aceito pelo SERPRO."""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<procuracao xmlns="http://www.serpro.gov.br/integra-contador/procurador">'
            "<identificacao>"
            f"<id>{uuid.uuid4()}</id>"
            f"<dataInicio>{data_inicio.strftime('%Y-%m-%d')}</dataInicio>"
            f"<dataFim>{data_fim.strftime('%Y-%m-%d')}</dataFim>"
            "</identificacao>"
            "<outorgante>"
            f"<tipoDocumento>{tipo_pessoa_xml(outorgante_doc)}</tipoDocumento>"
            f"<numeroDocumento>{outorgante_doc}</numeroDocumento>"
            "</outorgante>"
            "<procurador>"
            f"<tipoDocumento>{tipo_pessoa_xml(procurador_doc)}</tipoDocumento>"
            f"<numeroDocumento>{procurador_doc}</numeroDocumento>"
            "</procurador>"
            "<poderes>"
            "<poder>consultar</poder>"
            "<poder>emitir</poder>"
            "</poderes>"
            "</procuracao>"
        )

    def _assinar_xml(self, xml_str: str, pfx_path: str, pfx_password: str) -> Optional[str]:
        """
        Assina o XML com o certificado A1 do procurador.
        Assinatura RSA-SHA256 com canonicalizacao c14n20010315.
        Retorna XML assinado ou None.

        Tenta usar signxml quando disponivel; usa implementacao manual
        (XMLDSig enveloped + C14N 1.0 + RSA-SHA256) via xml_signer
        quando signxml nao esta instalado.
        """
        private_key, certificate = self._carregar_certificado(pfx_path, pfx_password)
        if private_key is None or certificate is None:
            return None

        if HAS_SIGNXML:
            try:
                signed = SignatureBuilder().sign(
                    data=xml_str.encode("utf-8"),
                    key=private_key,
                    cert=certificate,
                    signature_algorithm=SignatureAlgorithm.RSA_SHA256,
                    digest_algorithm=DigestAlgorithm.SHA256,
                    c14n_algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315",
                )
                return signed.decode("utf-8") if isinstance(signed, (bytes, bytearray)) else str(signed)
            except Exception as e:
                logger.exception("Falha ao assinar XML (signxml): %s", e)
                # tenta fallback manual
                if HAS_XML_SIGNER and _xml_assinar_enveloped is not None:
                    logger.info("Caindo no assinador XML manual (xml_signer).")
                else:
                    return None

        if HAS_XML_SIGNER and _xml_assinar_enveloped is not None:
            try:
                signed = _xml_assinar_enveloped(xml_str, pfx_path, pfx_password)
                return signed
            except Exception as e:
                logger.exception("Falha ao assinar XML (xml_signer): %s", e)
                return None

        logger.error("Nenhum assinador XML disponivel (signxml/xml_signer)")
        return None

    # ------------------------------------------------------------------
    # Extrai autenticar_procurador_token da resposta
    # ------------------------------------------------------------------
    def _extract_procurador_token(self, resp_headers: Dict[str, str], body_text: str) -> Optional[str]:
        """
        Procura o token em:
        - header no formato "autenticar_procurador_token:<valor>"
        - body JSON nos campos dados.autenticar_procurador_token
          ou autenticar_procurador_token
        """
        # 1) header
        if resp_headers:
            for value in resp_headers.values():
                if not value:
                    continue
                m = re.search(r"autenticar_procurador_token:([^,\s\"]+)", str(value))
                if m:
                    return m.group(1)

        # 2) body JSON
        try:
            data = json.loads(body_text) if body_text else {}
        except Exception:
            data = {}
        if isinstance(data, dict):
            dados = data.get("dados")
            if isinstance(dados, dict):
                tok = dados.get("autenticar_procurador_token")
                if tok:
                    return str(tok)
            tok = data.get("autenticar_procurador_token") or data.get("autenticarProcuradorToken")
            if tok:
                return str(tok)
        return None

    # ------------------------------------------------------------------
    # Envio do XML assinado via AUTENTICAPROCURADOR
    # ------------------------------------------------------------------
    def save_authorization_token(
        self,
        outorgante_cnpj: str,
        procurador_cpf: str,
        procurador_nome: str,
        xml_assinado: str,
        *,
        vigencia: int = 365,
    ) -> Dict[str, Any]:
        """
        Envia o XML assinado para o SERPRO via AUTENTICAPROCURADOR/ENVIOXMLASSINADO81
        e retorna o autenticar_procurador_token.

        Endpoint: POST .../Apoiar
        Headers:
          Authorization: Bearer <access_token>
          jwt_token: <jwt_token>
          Content-Type: application/json
          accept: text/plain

        Body:
        {
          "idSistema": "AUTENTICAPROCURADOR",
          "idServico": "ENVIOXMLASSINADO81",
          "versaoSistema": "1.0",
          "dados": {
            "termoDeAutorizacao": {
              "sistema": "API Integra Contador",
              "termo": {"texto": "Autorizo a empresa..."},
              "avisoLegal": "O acesso...",
              "finalidade": "A finalidade...",
              "dataAssinatura": {"data": "<AAAAMMDD>"},
              "vigencia": "365",
              "destinatario": {"numero": "<CNPJ>", "nome": "<NOME>", "tipo": "CONTRATANTE", "papel": "contratante"},
              "assinadoPor": {"numero": "<CPF>", "nome": "<NOME>", "papel": "autor pedido de dados"}
            },
            "xml": "<XML_ASSINADO>"
          }
        }
        """
        if self._serpro is None:
            return {"success": False, "error": "SerproService nao inicializado"}

        # Validacoes
        err = self._validate_common(outorgante_cnpj, procurador_cpf)
        if err:
            return {"success": False, "error": err}
        err = self._validate_procurador(procurador_cpf, procurador_nome)
        if err:
            return {"success": False, "error": err}

        # Obter tokens (via _get_auth_token pai, NAO alterado)
        tokens = self.get_auth_tokens()
        if not tokens.get("success"):
            return {"success": False, "error": tokens.get("error")}

        access_token = tokens.get("access_token")
        jwt_token = tokens.get("jwt_token")

        cnpj_clean = only_digits(outorgante_cnpj)
        cpf_clean = only_digits(procurador_cpf)
        agora = datetime.now()
        data_assinatura = agora.strftime("%Y%m%d")

        # Busca nome do contratante (pode estar em AppSetting)
        creds = self._load_credentials()
        contador_cnpj = only_digits(creds.get("contador_cnpj") or "")
        # Se nao tiver nome do contratante, usa texto generico
        nome_contratante = creds.get("contador_nome", "") or "Contador/Contratante"

        body = {
            "idSistema": "AUTENTICAPROCURADOR",
            "idServico": "ENVIOXMLASSINADO81",
            "versaoSistema": self.VERSAO_SISTEMA_AUTENTICA,
            "dados": {
                "termoDeAutorizacao": {
                    "sistema": "API Integra Contador",
                    "termo": {
                        "texto": (
                            "Autorizo a empresa CONTRATANTE, identificada neste "
                            "termo de autorizacao como DESTINATARIO, a executar as "
                            "requisicoes dos servicos web disponibilizados pela "
                            "API INTEGRA CONTADOR, onde terei o papel de AUTOR "
                            "PEDIDO DE DADOS no corpo da mensagem enviada pela "
                            "API INTEGRA CONTADOR."
                        )
                    },
                    "avisoLegal": (
                        "O acesso a estas informacoes foi autorizado pelo proprio "
                        "PROCURADOR ou OUTORGADO DO CONTRIBUINTE, responsavel pela "
                        "informacao, via assinatura digital. E dever do destinatario "
                        "da autorizacao e consumidor deste acesso observar a adocao "
                        "de base legal para o tratamento dos dados."
                    ),
                    "finalidade": (
                        "A finalidade unica e exclusiva desse TERMO DE AUTORIZACAO, "
                        "e garantir que o CONTRATANTE apresente a API INTEGRA "
                        "CONTADOR esse consentimento do PROCURADOR ou OUTORGADO DO "
                        "CONTRIBUINTE assinado digitalmente, para que possa realizar "
                        "as requisicoes dos servicos."
                    ),
                    "dataAssinatura": {"data": data_assinatura},
                    "vigencia": str(int(vigencia)),
                    "destinatario": {
                        "numero": cnpj_clean,
                        "nome": nome_contratante,
                        "tipo": "CONTRATANTE",
                        "papel": "contratante",
                    },
                    "assinadoPor": {
                        "numero": cpf_clean,
                        "nome": procurador_nome,
                        "papel": "autor pedido de dados",
                    },
                },
                "xml": xml_assinado,
            },
        }

        # Chamada HTTP direta para preservar response headers (regex do token)
        session = self._serpro._build_session()  # type: ignore[attr-defined]
        if session is None:
            return {"success": False, "error": "Sessao mTLS nao disponivel"}

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "accept": "text/plain",
        }
        if jwt_token:
            headers["jwt_token"] = str(jwt_token)

        contexto = {
            "outorgante": cnpj_clean,
            "procurador": cpf_clean,
            "idServico": "ENVIOXMLASSINADO81",
        }
        t0 = time.time()
        try:
            log_serpro_request("POST", self.ENDPOINT_APOIAR, contexto, headers, body)
            resp = session.post(
                self.ENDPOINT_APOIAR,
                headers=headers,
                json=body,
                timeout=self._serpro.timeout,
                allow_redirects=False,
            )
            elapsed = (time.time() - t0) * 1000.0
            log_serpro_response(
                "POST",
                resp.status_code,
                elapsed,
                contexto,
                dict(resp.headers),
                resp.text,
            )

            # 304 Not Modified tambem e valido (cached pelo SERPRO)
            if resp.status_code not in (200, 201, 202, 204, 304):
                log_serpro_exception(contexto, elapsed, None, f"HTTP {resp.status_code}")
                return {
                    "success": False,
                    "status": resp.status_code,
                    "error": f"HTTP {resp.status_code}: {resp.text[:500]}",
                }

            token = self._extract_procurador_token(dict(resp.headers), resp.text)

            # Se veio 304, tenta extrair token do ETag header
            if not token and resp.status_code == 304:
                etag = resp.headers.get("ETag") or resp.headers.get("etag") or ""
                m = re.search(r"autenticar_procurador_token:([^\"\s,]+)", etag)
                if m:
                    token = m.group(1)

            # tenta extrair data_hora_expiracao do body
            try:
                data = json.loads(resp.text) if resp.text else {}
            except Exception:
                data = {}
            expiracao = None
            expires_seconds: Optional[float] = None
            if isinstance(data, dict):
                dados = data.get("dados") or {}
                if isinstance(dados, dict):
                    expiracao = (
                        dados.get("data_hora_expiracao")
                        or dados.get("dataHoraExpiracao")
                    )
                    # Campo "expires" em segundos
                    exp_val = dados.get("expires")
                    if exp_val is not None:
                        try:
                            expires_seconds = float(exp_val)
                        except (TypeError, ValueError):
                            pass
                expiracao = expiracao or data.get("data_hora_expiracao")

            # Cache do token com expiracao
            if token:
                with self._procurador_token_lock:
                    self._procurador_token = token
                    if expires_seconds:
                        self._procurador_token_expires_at = time.time() + expires_seconds
                    elif expiracao:
                        # tenta parsear data_hora_expiracao (formato SP)
                        self._procurador_token_expires_at = (
                            self._parse_expiracao_to_epoch(expiracao)
                        )
                    else:
                        # Default: 55 minutos
                        self._procurador_token_expires_at = time.time() + 3300

            return {
                "success": True,
                "autenticar_procurador_token": token,
                "data_hora_expiracao": expiracao,
                "data": data,
                "message": "Procurador autenticado",
            }
        except Exception as e:
            elapsed = (time.time() - t0) * 1000.0
            log_serpro_exception(contexto, elapsed, None, e)
            logger.exception("Erro save_authorization_token: %s", e)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Parse de data_hora_expiracao para epoch
    # ------------------------------------------------------------------
    def _parse_expiracao_to_epoch(self, expiracao_str: str) -> float:
        """
        Converte data_hora_expiracao (timezone America/Sao_Paulo) para
        epoch UTC. Suporta formatos comuns do SERPRO.
        """
        if not expiracao_str:
            return time.time() + 3300  # fallback 55min

        # Offset SP padrao: UTC-3 (sem horario de verao desde 2019)
        SP_OFFSET = timedelta(hours=-3)

        formatos = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%Y%m%d%H%M%S",
        ]
        for fmt in formatos:
            try:
                dt_naive = datetime.strptime(str(expiracao_str).strip(), fmt)
                # Assume timezone SP
                dt_utc = dt_naive - SP_OFFSET
                # Converte para epoch
                epoch = (dt_utc - datetime(1970, 1, 1)).total_seconds()
                return epoch
            except (ValueError, TypeError):
                continue

        # Se nao conseguiu parsear, default 55 minutos
        logger.warning(
            "Nao foi possivel parsear data_hora_expiracao: %s", expiracao_str
        )
        return time.time() + 3300

    # ------------------------------------------------------------------
    # Metodo principal com CACHE para uso pelo SerproService
    # ------------------------------------------------------------------
    def autorizar_procurador_para_consulta(
        self,
        *,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        Retorna o autenticar_procurador_token necessario para chamadas
        ao Apoiar (ex: SOLICITARPROTOCOLO91).

        Fluxo:
        1. Verifica cache (retorna se nao expirado e force_refresh=False)
        2. Carrega credenciais do banco (AppSetting)
        3. Monta XML do termo de autorizacao
        4. Assina o XML com certificado A1
        5. Envia via AUTENTICAPROCURADOR/ENVIOXMLASSINADO81
        6. Extrai e cacheia o token

        Returns:
            Dict com:
              - success: bool
              - autenticar_procurador_token: str (se success)
              - error: str (se falha)
        """
        # 1. Cache check
        if not force_refresh:
            with self._procurador_token_lock:
                if (
                    self._procurador_token
                    and self._procurador_token_expires_at > (time.time() + 60)
                ):
                    logger.info("Token procurador retornado do cache.")
                    return {
                        "success": True,
                        "autenticar_procurador_token": self._procurador_token,
                        "from_cache": True,
                    }

        # 2. Carregar credenciais
        creds = self._load_credentials()
        contador_cnpj = only_digits(creds.get("contador_cnpj") or "")
        procurador_cpf = only_digits(creds.get("procurador_cpf") or "")
        procurador_nome = (creds.get("procurador_nome") or "").strip()
        pfx_path = creds.get("certificado_path", "")
        pfx_password = (
            creds.get("procurador_certificado_password")
            or creds.get("certificado_password", "")
        )

        # Validacoes
        if not contador_cnpj:
            return {"success": False, "error": "contador_cnpj nao configurado"}
        if not procurador_cpf:
            return {"success": False, "error": "procurador_cpf nao configurado"}
        if not procurador_nome:
            return {"success": False, "error": "procurador_nome nao configurado"}
        if not pfx_path:
            return {"success": False, "error": "certificado_path nao configurado"}

        # 3. Monta XML
        data_inicio = datetime.now()
        data_fim = data_inicio + timedelta(days=365)
        xml = self._gerar_xml_procuracao(
            contador_cnpj, procurador_cpf, data_inicio, data_fim
        )

        # 4. Assina XML
        xml_assinado = self._assinar_xml(xml, pfx_path, pfx_password)
        if not xml_assinado:
            logger.warning(
                "Assinatura XML falhou, enviando XML nao-assinado (pode falhar no SERPRO)."
            )
            xml_assinado = xml

        # 5. Envia
        result = self.save_authorization_token(
            outorgante_cnpj=contador_cnpj,
            procurador_cpf=procurador_cpf,
            procurador_nome=procurador_nome,
            xml_assinado=xml_assinado,
            vigencia=365,
        )

        # 6. Resultado
        if result.get("success") and result.get("autenticar_procurador_token"):
            return {
                "success": True,
                "autenticar_procurador_token": result["autenticar_procurador_token"],
                "from_cache": False,
            }

        return {
            "success": False,
            "error": result.get("error") or "Token de procurador nao obtido na resposta",
            "data": result.get("data"),
        }

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------
    def consultar_cadastro(self, cnpj: str) -> Dict[str, Any]:
        """
        Mantido por compatibilidade. Apenas valida o CNPJ e devolve
        um payload. Para buscar relatorio de situacao fiscal, use
        SerproService.request_protocol().
        """
        logger.info("Consultando cadastro SERPRO para %s", cnpj)
        err = self._validate_common(cnpj)
        if err:
            return {"success": False, "cnpj": only_digits(cnpj), "error": err}

        cnpj_clean = only_digits(cnpj)
        creds = self._load_credentials()
        contador_cnpj = only_digits(creds.get("contador_cnpj") or "")
        procurador_cpf = only_digits(creds.get("procurador_cpf") or "")
        tipo = tipo_pessoa_xml(cnpj_clean)

        return {
            "success": True,
            "cnpj": cnpj_clean,
            "tipo": "JURIDICA" if tipo == "2" else "FISICA",
            "situacao": "INDEFINIDA",
            "message": "Use SerproService.request_protocol() para obter a situacao fiscal.",
            "data": {
                "contribuinte": cnpj_clean,
                "intermediario": procurador_cpf or contador_cnpj,
            },
        }

    def gerar_procuracao(
        self,
        outorgante: str,
        procurador: str,
        prazo_dias: int = 365,
    ) -> str:
        """
        Gera XML de procuracao eletronica (util para testes).
        NAO assina o XML - isso deve ser feito com o certificado A1
        do procurador antes de submeter via save_authorization_token.
        """
        outorgante_clean = only_digits(outorgante)
        procurador_clean = only_digits(procurador)
        data_inicio = datetime.now()
        data_fim = data_inicio + timedelta(days=prazo_dias)
        return self._gerar_xml_procuracao(outorgante_clean, procurador_clean, data_inicio, data_fim)

    def autenticar_procurador(
        self,
        outorgante: str,
        procurador: str,
        prazo_dias: int = 365,
    ) -> Dict[str, Any]:
        """
        Fluxo completo: monta XML, assina com o .pfx do procurador e
        submete via AUTENTICAPROCURADOR. Retorna o token de procurador.
        """
        creds = self._load_credentials()
        pfx_path = creds.get("certificado_path", "")
        pfx_password = (
            creds.get("procurador_certificado_password")
            or creds.get("certificado_password", "")
        )
        procurador_nome = creds.get("procurador_nome", "")

        outorgante_clean = only_digits(outorgante)
        procurador_clean = only_digits(procurador)

        # Validacoes
        err = self._validate_common(outorgante, procurador)
        if err:
            return {"success": False, "error": err}
        if not pfx_path:
            return {"success": False, "error": "certificado_path nao configurado"}
        if not procurador_nome:
            return {"success": False, "error": "procurador_nome nao configurado"}

        data_inicio = datetime.now()
        data_fim = data_inicio + timedelta(days=prazo_dias)

        xml = self._gerar_xml_procuracao(outorgante_clean, procurador_clean, data_inicio, data_fim)
        signed_xml = self._assinar_xml(xml, pfx_path, pfx_password) if pfx_path else None
        xml_final = signed_xml or xml

        return self.save_authorization_token(
            outorgante_cnpj=outorgante_clean,
            procurador_cpf=procurador_clean,
            procurador_nome=procurador_nome,
            xml_assinado=xml_final,
            vigencia=prazo_dias,
        )
