"""Serviço para consulta de pagamentos de tributos via SERPRO.

Reconstituído fielmente do bytecode do exe.
Fonte da verdade: dis/services/serpro_pagamentos_service.txt (1153 linhas)

Constantes do exe:
- CONSULTAR_URL = 'https://gateway.apiserpro.serpro.gov.br/integra-contador/v1/Consultar'
- ID_SISTEMA = 'PAGTOWEB'
- ID_SERVICO = 'PAGAMENTOS71'
- VERSAO_SISTEMA = '1.0'
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.models import AppSetting
from app.services import certificado
from app.services.serpro_logging import serpro_post
from app.services.serpro_service import SerproService
from app.services.serpro_procurador_service import SerproProcuradorService


logger = logging.getLogger(__name__)


class SerproPagamentosService:
    """Serviço para consulta de pagamentos via SERPRO Integra Contador.

    Reconstruído fielmente do bytecode.
    """

    CONSULTAR_URL = "https://gateway.apiserpro.serpro.gov.br/integra-contador/v1/Consultar"
    ID_SISTEMA = "PAGTOWEB"
    ID_SERVICO = "PAGAMENTOS71"
    VERSAO_SISTEMA = "1.0"

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    @staticmethod
    def _tipo_pessoa(numero: str) -> int:
        """Retorna tipo pessoa: 1 (CPF/11 dígitos) ou 2 (CNPJ/14 dígitos)."""
        digits = "".join(ch for ch in str(numero or "") if ch.isdigit())
        if len(digits) == 11:
            return 1
        if len(digits) == 14:
            return 2
        raise ValueError("CPF/CNPJ inválido para autenticação/consulta na SERPRO")

    @staticmethod
    def _decode_json_if_needed(value: Any) -> Any:
        """Decodifica JSON string se necessário."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return value
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def _normalize_response(self, response_json: Dict[str, Any]) -> Dict[str, Any]:
        """Normaliza response decodificando 'dados' e 'items' se forem strings JSON."""
        normalized = dict(response_json or {})
        if "dados" in normalized:
            normalized["dados"] = self._decode_json_if_needed(normalized["dados"])
        if "items" in normalized:
            normalized["items"] = self._decode_json_if_needed(normalized["items"])
        return normalized

    def _get_headers(
        self, setting: AppSetting, contribuinte_numero: str | None = None
    ) -> Dict[str, str]:
        """Gera headers de autenticação SERPRO.

        Se procurador habilitado, usa SerproProcuradorService.auth_headers.
        Senão, autentica via SerproService (cert A1 direto).
        """
        # Tenta procurador primeiro
        if SerproProcuradorService.enabled(setting):
            return SerproProcuradorService(setting).auth_headers(
                accept="application/json",
                contribuinte_numero=contribuinte_numero,
            )

        # Fluxo direto (cert A1)
        certificado_path = (setting.certificado_path or "").strip()
        certificado_password = (setting.certificado_password or "").strip()
        consumer_key = (setting.serpro_consumer_key or "").strip()
        consumer_secret = (setting.serpro_consumer_secret or "").strip()
        contador_cnpj = (setting.contador_cnpj or "").strip()

        if not certificado_path:
            raise ValueError("Certificado não configurado em Configurações")
        if not certificado_password:
            raise ValueError("Senha do certificado não configurada em Configurações")
        if not consumer_key or not consumer_secret:
            raise ValueError(
                "Consumer key/secret da SERPRO não configurados em Configurações"
            )
        if not contador_cnpj:
            raise ValueError("CNPJ/CPF do contador não configurado em Configurações")

        # DESVIO 3o: resolve o caminho — o gravado pode ser de outra máquina.
        certificate_content = certificado.carregar(certificado_path)
        auth_service = SerproService(
            certificate_content=certificate_content,
            certificate_password=certificado_password,
            contratante_cnpj=contador_cnpj,
            contador_cnpj=contador_cnpj,
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
        )

        auth_tokens = auth_service.get_auth_token()
        if not auth_tokens:
            raise Exception("Falha ao autenticar na API da SERPRO")

        access_token = auth_tokens.get("access_token")
        jwt_token = auth_tokens.get("jwt_token")
        if not access_token or not jwt_token:
            raise Exception("Tokens da SERPRO não retornados corretamente")

        return {
            "jwt_token": jwt_token,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }

    def montar_payload(
        self,
        setting: AppSetting,
        contribuinte_numero: str,
        primeiro_da_pagina: int = 0,
        tamanho_da_pagina: int = 100,
        data_inicial: Optional[str] = None,
        data_final: Optional[str] = None,
        numero_documento_lista: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Constrói payload para consulta de pagamentos.

        Se procurador habilitado, usa SerproProcuradorService.build_payload.
        Senão, monta payload manual com contratante/contribuinte/pedidoDados.
        """
        setting_obj = setting

        # Monta dados da consulta
        if SerproProcuradorService.enabled(setting_obj):
            dados = {
                "primeiroDaPagina": int(primeiro_da_pagina),
                "tamanhoDaPagina": int(tamanho_da_pagina),
            }

            if numero_documento_lista:
                dados["numeroDocumentoLista"] = [
                    str(x).strip() for x in numero_documento_lista if str(x).strip()
                ]
            else:
                if not data_inicial or not data_final:
                    raise ValueError(
                        "Informe data inicial e data final para a consulta"
                    )
                dados["intervaloDataArrecadacao"] = {
                    "dataInicial": data_inicial,
                    "dataFinal": data_final,
                }

            return SerproProcuradorService(setting_obj).build_payload(
                contribuinte_numero=contribuinte_numero,
                id_sistema=self.ID_SISTEMA,
                id_servico=self.ID_SERVICO,
                versao_sistema=self.VERSAO_SISTEMA,
                dados=dados,
            )

        # Fluxo direto (sem procurador)
        contratante_numero = "".join(
            ch for ch in str(setting_obj.contador_cnpj or "") if ch.isdigit()
        )
        contribuinte_numero = "".join(
            ch for ch in str(contribuinte_numero or "") if ch.isdigit()
        )

        if not contratante_numero or not contribuinte_numero:
            raise ValueError("CPF/CNPJ do contratante ou contribuinte inválido")

        dados = {
            "primeiroDaPagina": int(primeiro_da_pagina),
            "tamanhoDaPagina": int(tamanho_da_pagina),
        }

        if numero_documento_lista:
            dados["numeroDocumentoLista"] = [
                str(x).strip() for x in numero_documento_lista if str(x).strip()
            ]
        else:
            if not data_inicial or not data_final:
                raise ValueError(
                    "Informe data inicial e data final para a consulta"
                )
            dados["intervaloDataArrecadacao"] = {
                "dataInicial": data_inicial,
                "dataFinal": data_final,
            }

        payload = {
            "contratante": {
                "numero": contratante_numero,
                "tipo": self._tipo_pessoa(contratante_numero),
            },
            "autorPedidoDados": {
                "numero": contratante_numero,
                "tipo": self._tipo_pessoa(contratante_numero),
            },
            "contribuinte": {
                "numero": contribuinte_numero,
                "tipo": self._tipo_pessoa(contribuinte_numero),
            },
            "pedidoDados": {
                "idSistema": self.ID_SISTEMA,
                "idServico": self.ID_SERVICO,
                "versaoSistema": self.VERSAO_SISTEMA,
                "dados": json.dumps(dados, ensure_ascii=False),
            },
        }

        return payload

    def consultar(
        self, setting: AppSetting, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Envia POST de consulta para SERPRO e retorna response normalizado."""
        contribuinte_numero = (
            payload.get("contribuinte") or {}
        ).get("numero") or ""

        headers = self._get_headers(setting, contribuinte_numero=contribuinte_numero)

        response = serpro_post(
            self.CONSULTAR_URL,
            headers=headers,
            json_payload=payload,
            context="pagamentos_consultar",
            timeout=self.timeout,
        )

        # Retry se 401/403 com procurador (token expirado)
        if (
            SerproProcuradorService.enabled(setting)
            and response.status_code in (401, 403)
        ):
            SerproProcuradorService(setting).invalidate_authorization_token()
            headers = self._get_headers(setting, contribuinte_numero=contribuinte_numero)
            response = serpro_post(
                self.CONSULTAR_URL,
                headers=headers,
                json_payload=payload,
                context="pagamentos_consultar_retry",
                timeout=self.timeout,
            )

        response_text = response.text or ""

        if response.status_code != 200:
            logger.error(
                "Erro na consulta de pagamentos SERPRO. Status=%s Body=%s",
                response.status_code,
                response_text[:2000],
            )
            raise Exception(
                f"Erro ao consultar pagamentos na SERPRO. Status: "
                f"{response.status_code}. Resposta: {response_text[:1000]}"
            )

        try:
            response_json = response.json()
        except Exception:
            logger.error(
                "Resposta da SERPRO não é JSON válido: %s", response_text[:2000]
            )
            raise Exception("Resposta da SERPRO não está em JSON válido")

        return self._normalize_response(response_json)

    def extrair_items(self, response_json: Dict[str, Any]) -> list:
        """Extrai items de pagamento do response.

        Busca em vários paths: response['items'], dados['items'],
        dados['lista'], dados['pagamentos'].
        """
        response_json = self._normalize_response(response_json)

        if isinstance(response_json.get("items"), list):
            return response_json["items"]

        dados = response_json.get("dados")
        if isinstance(dados, list):
            return dados
        if isinstance(dados, dict):
            if isinstance(dados.get("items"), list):
                return dados["items"]
            if isinstance(dados.get("lista"), list):
                return dados["lista"]
            if isinstance(dados.get("pagamentos"), list):
                return dados["pagamentos"]
            return [dados]

        return []

    def buscar_pagamentos(
        self,
        company,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        numero_documento_lista: Optional[List[str]] = None,
        tamanho_da_pagina: int = 100,
    ) -> Dict[str, Any]:
        """Busca pagamentos de uma empresa com paginação automática."""
        setting = AppSetting.query.first()
        if not setting:
            raise ValueError("Configurações da aplicação não encontradas")

        all_items: List[Dict[str, Any]] = []
        primeiro = 0
        ultima_resposta: Dict[str, Any] = {}

        while True:
            payload = self.montar_payload(
                setting=setting,
                contribuinte_numero=getattr(company, "cnpj", ""),
                primeiro_da_pagina=primeiro,
                tamanho_da_pagina=tamanho_da_pagina,
                data_inicial=data_inicio,
                data_final=data_fim,
                numero_documento_lista=numero_documento_lista,
            )

            response = self.consultar(setting=setting, payload=payload)
            items = self.extrair_items(response)
            ultima_resposta = response

            if not items:
                break

            all_items.extend(items)

            # Se buscando por lista de documentos, não pagina
            if numero_documento_lista:
                break
            # Se retornou menos que o tamanho da página, acabou
            if len(items) < tamanho_da_pagina:
                break

            primeiro += tamanho_da_pagina

        normalized = self._normalize_response(ultima_resposta)
        normalized["items"] = all_items
        return normalized
