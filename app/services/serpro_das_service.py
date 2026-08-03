"""
Servico para emissao de DAS / DARF via SERPRO Integra Contador.

Endpoints (base .../integra-contador/v1):
  - Emitir    -> PGDASD/GERARDAS12  (DAS Simples)
                 PGMEI/GERARDASPDF21 (DAS MEI)
                 DCTFWEB/GERARGUIA31  (DARF DCTFWeb)

Payloads adaptados exatamente do exe original.
Reconstruida fielmente do bytecode: lines 87-1304.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from app.models import AppSetting
from app.services.serpro_logging import serpro_post
from app.services.serpro_service import SerproService
from app.services.serpro_procurador_service import SerproProcuradorService


logger = logging.getLogger(__name__)


class SerproDasService:
    """Servico para emissao de DAS / DARF via SERPRO Integra Contador."""

    # Constantes do bytecode (linhas 92-109)
    EMITIR_URL = 'https://gateway.apiserpro.serpro.gov.br/integra-contador/v1/Emitir'

    ID_SISTEMA_SIMPLES = 'PGDASD'
    ID_SERVICO_SIMPLES = 'GERARDAS12'
    VERSAO_SISTEMA_SIMPLES = '1.0'

    ID_SISTEMA_MEI = 'PGMEI'
    ID_SERVICO_MEI = 'GERARDASPDF21'

    ID_SISTEMA_DCTFWEB = 'DCTFWEB'
    ID_SERVICO_DCTFWEB = 'GERARGUIA31'
    VERSAO_SISTEMA_DCTFWEB = '1.0'

    def __init__(self, timeout: int = 120):
        """Initialize com timeout padrao de 120s."""
        self.timeout = timeout

    @staticmethod
    def _only_digits(value: Any) -> str:
        """Extrai apenas digitos de uma string."""
        return ''.join(ch for ch in str(value or '') if ch.isdigit())

    @staticmethod
    def _tipo_pessoa(numero: str) -> int:
        """
        Retorna tipo de pessoa: 1=CPF (11 digitos), 2=CNPJ (14 digitos).
        Raise ValueError se invalido.
        """
        digits = SerproDasService._only_digits(numero)
        if len(digits) == 11:
            return 1
        elif len(digits) == 14:
            return 2
        else:
            raise ValueError('CPF/CNPJ inválido para autenticação/consulta na SERPRO')

    @staticmethod
    def _decode_json_if_needed(value: Any) -> Any:
        """
        Se value eh string, tenta json.loads; se falha, retorna value como-eh.
        Else retorna value como-eh.
        """
        if isinstance(value, str):
            value = value.strip()
            if value:
                try:
                    return json.loads(value)
                except Exception:
                    return value
            return value
        return value

    def _get_headers(self, setting: AppSetting, contribuinte_numero: str | None = None) -> Dict[str, str]:
        """
        Constroi headers com autenticacao.
        Se procurador habilitado, usa SerproProcuradorService.auth_headers.
        Senao, carrega certificado A1 direto e cria headers.

        Bytecode linhas 462-615.
        """
        # Caminho 1: Se procurador habilitado, usar auth_headers do SerproProcuradorService
        if SerproProcuradorService.enabled(setting):
            return SerproProcuradorService(setting).auth_headers(
                accept='application/json',
                contribuinte_numero=contribuinte_numero
            )

        # Caminho 2: Certificado A1 direto
        certificado_path = (setting.certificado_path or '').strip()
        certificado_password = (setting.certificado_password or '').strip()
        consumer_key = (setting.serpro_consumer_key or '').strip()
        consumer_secret = (setting.serpro_consumer_secret or '').strip()
        contador_cnpj = self._only_digits(setting.contador_cnpj or '')

        # Validacoes
        if not certificado_path:
            raise ValueError('Certificado não configurado em Configurações')
        if not certificado_password:
            raise ValueError('Senha do certificado não configurada em Configurações')
        if not (consumer_key and consumer_secret):
            raise ValueError('Consumer key/secret da SERPRO não configurados em Configurações')
        if not contador_cnpj:
            raise ValueError('CNPJ/CPF do contador não configurado em Configurações')

        cert_file = Path(certificado_path)
        if not cert_file.exists():
            raise ValueError('Arquivo do certificado não encontrado nas Configurações')

        certificate_content = cert_file.read_bytes()
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
            raise Exception('Falha ao autenticar na API da SERPRO')

        access_token = auth_tokens.get('access_token')
        jwt_token = auth_tokens.get('jwt_token')
        if not (access_token and jwt_token):
            raise Exception('Tokens da SERPRO não retornados corretamente')

        return {
            'jwt_token': jwt_token,
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'accept': 'application/json',
        }

    def montar_payload_emitir(
        self,
        setting: AppSetting,
        contribuinte_numero: str,
        id_sistema: str,
        id_servico: str,
        dados: Dict[str, Any],
        versao_sistema: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Monta payload generico para emissao (SIMPLES, MEI, DCTFWEB).

        Se procurador habilitado, usa SerproProcuradorService.build_payload.
        Senao, monta manualmente com contratante + contribuinte + pedidoDados.

        Bytecode linhas 618-711.
        """
        # Caminho 1: Se procurador habilitado
        if SerproProcuradorService.enabled(setting):
            return SerproProcuradorService(setting).build_payload(
                contribuinte_numero=contribuinte_numero,
                id_sistema=id_sistema,
                id_servico=id_servico,
                versao_sistema=versao_sistema,
                dados=dados,
            )

        # Caminho 2: Manual (sem procurador)
        contratante_numero = self._only_digits(setting.contador_cnpj or '')
        contribuinte_numero = self._only_digits(contribuinte_numero or '')

        if not contratante_numero:
            raise ValueError('CPF/CNPJ do contratante não configurado')
        if not contribuinte_numero:
            raise ValueError('CPF/CNPJ do contribuinte não informado')

        # Construir pedido_dados
        pedido_dados = {
            'idSistema': id_sistema,
            'idServico': id_servico,
            'dados': json.dumps(dados, ensure_ascii=False),
        }

        if versao_sistema:
            pedido_dados['versaoSistema'] = versao_sistema

        # Montar payload completo (contratante, autorPedidoDados, contribuinte, pedidoDados)
        return {
            'contratante': {
                'numero': contratante_numero,
                'tipo': self._tipo_pessoa(contratante_numero),
            },
            'autorPedidoDados': {
                'numero': contratante_numero,
                'tipo': self._tipo_pessoa(contratante_numero),
            },
            'contribuinte': {
                'numero': contribuinte_numero,
                'tipo': self._tipo_pessoa(contribuinte_numero),
            },
            'pedidoDados': pedido_dados,
        }

    def montar_payload_simples(
        self,
        setting: AppSetting,
        contribuinte_numero: str,
        periodo_apuracao: str,
        data_consolidacao: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Monta payload para DAS Simples.

        Bytecode linhas 714-781.
        """
        periodo_apuracao = self._only_digits(periodo_apuracao or '')
        data_consolidacao = self._only_digits(data_consolidacao or '')

        if len(periodo_apuracao) != 6:
            raise ValueError('Período de apuração inválido. Use o formato AAAAMM, exemplo: 202405')

        if data_consolidacao and len(data_consolidacao) != 8:
            raise ValueError('Data de consolidação inválida. Use o formato AAAAMMDD, exemplo: 20240531')

        dados: Dict[str, Any] = {'periodoApuracao': periodo_apuracao}
        if data_consolidacao:
            dados['dataConsolidacao'] = data_consolidacao

        return self.montar_payload_emitir(
            setting=setting,
            contribuinte_numero=contribuinte_numero,
            id_sistema=self.ID_SISTEMA_SIMPLES,
            id_servico=self.ID_SERVICO_SIMPLES,
            versao_sistema=self.VERSAO_SISTEMA_SIMPLES,
            dados=dados,
        )

    def montar_payload_mei(
        self,
        setting: AppSetting,
        contribuinte_numero: str,
        periodo_apuracao: str,
        data_consolidacao: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Monta payload para DAS MEI.
        Obs: data_consolidacao nao eh utilizado para MEI (seguindo exe).

        Bytecode linhas 784-822.
        """
        periodo_apuracao = self._only_digits(periodo_apuracao or '')

        if len(periodo_apuracao) != 6:
            raise ValueError('Período de apuração inválido. Use o formato AAAAMM, exemplo: 202405')

        dados = {'periodoApuracao': periodo_apuracao}

        return self.montar_payload_emitir(
            setting=setting,
            contribuinte_numero=contribuinte_numero,
            id_sistema=self.ID_SISTEMA_MEI,
            id_servico=self.ID_SERVICO_MEI,
            dados=dados,
        )

    def emitir(self, setting: AppSetting, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Faz POST para EMITIR_URL com payload.
        Se procurador e status 401/403, invalida token e retenta.
        Levanta Exception se status != 200.

        Bytecode linhas 825-975.
        """
        contribuinte_numero = (payload.get('contribuinte') or {}).get('numero', '')
        headers = self._get_headers(setting, contribuinte_numero=contribuinte_numero)

        # Primeira tentativa
        response = serpro_post(
            self.EMITIR_URL,
            headers=headers,
            json_payload=payload,
            context='das_emitir',
            timeout=self.timeout,
        )

        # Retry se procurador e 401/403
        if SerproProcuradorService.enabled(setting) and response.status_code in (401, 403):
            SerproProcuradorService(setting).invalidate_authorization_token()
            headers = self._get_headers(setting, contribuinte_numero=contribuinte_numero)
            response = serpro_post(
                self.EMITIR_URL,
                headers=headers,
                json_payload=payload,
                context='das_emitir_retry',
                timeout=self.timeout,
            )

        response_text = response.text or ''

        if response.status_code != 200:
            logger.error('Erro ao emitir DAS SERPRO. Status=%s Body=%s',
                        response.status_code, response_text[:3000])
            raise Exception(f'Erro ao emitir DAS na SERPRO. Status: {response.status_code}. '
                          f'Resposta: {response_text[:1000]}')

        # Parse JSON
        try:
            response_json = response.json()
        except Exception:
            logger.error('Resposta da SERPRO não é JSON válido: %s', response_text[:3000])
            raise Exception('Resposta da SERPRO não está em JSON válido')

        # Se 'dados' vem como string JSON, decodifica
        if 'dados' in response_json:
            response_json['dados'] = self._decode_json_if_needed(response_json['dados'])

        return response_json

    def extrair_pdf_base64(self, response_json: Dict[str, Any]) -> str | None:
        """
        Extrai PDF base64 da resposta SERPRO.
        Busca em multiplos paths: dados.arquivo, dados.pdf, conteudo[0].arquivo, etc.

        Bytecode linhas 978-1100.
        """
        dados = response_json.get('dados')

        if not dados:
            mensagens = response_json.get('mensagens') or []
            raise Exception(f'Resposta da SERPRO não retornou dados. Mensagens: {mensagens}')

        # Se dados eh string, tenta decodificar JSON
        if isinstance(dados, str):
            try:
                dados = json.loads(dados)
            except Exception:
                pass

        # Extrair item (pode ser list[0] ou dict direto)
        item = None
        if isinstance(dados, list) and dados:
            item = dados[0]
        elif isinstance(dados, dict):
            item = dados

        if not isinstance(item, dict):
            mensagens = response_json.get('mensagens') or []
            raise Exception(f'Resposta da SERPRO não retornou item válido. Mensagens: {mensagens}')

        # Buscar PDF em item: 'pdf' ou 'PDFByteArrayBase64'
        pdf_base64 = item.get('pdf') or item.get('PDFByteArrayBase64')

        if not pdf_base64:
            mensagens = response_json.get('mensagens') or []
            raise Exception(f'PDF da guia não encontrado na resposta da SERPRO. Mensagens: {mensagens}')

        return pdf_base64

    def emitir_pdf(
        self,
        contribuinte_numero: str,
        periodo_apuracao: str,
        tipo_das: str = 'simples',
        data_consolidacao: Optional[str] = None,
    ) -> bytes | None:
        """
        Orquestra emissao completa: get_setting, montar_payload, emitir, extrair_pdf, b64decode.

        Bytecode linhas 1103-1182.
        """
        setting = AppSetting.query.first()
        if not setting:
            raise ValueError('Configurações da aplicação não encontradas')

        tipo_das = str(tipo_das or 'simples').strip().lower()

        if tipo_das == 'mei':
            payload = self.montar_payload_mei(
                setting=setting,
                contribuinte_numero=contribuinte_numero,
                periodo_apuracao=periodo_apuracao,
            )
        else:
            payload = self.montar_payload_simples(
                setting=setting,
                contribuinte_numero=contribuinte_numero,
                periodo_apuracao=periodo_apuracao,
                data_consolidacao=data_consolidacao,
            )

        response_json = self.emitir(setting=setting, payload=payload)
        pdf_base64 = self.extrair_pdf_base64(response_json)

        try:
            return base64.b64decode(pdf_base64)
        except Exception:
            raise Exception('PDF retornado pela SERPRO não está em base64 válido')

    def montar_payload_dctfweb(
        self,
        setting: AppSetting,
        contribuinte_numero: str,
        categoria: str,
        competencia: Optional[str] = None,
        ano_pa: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Monta payload para DCTFWEB (DARF).

        Bytecode linhas 1185-1303.
        """
        categoria = str(categoria or '').strip()

        if categoria not in ('GERAL_MENSAL', 'GERAL_13o_SALARIO'):
            raise ValueError('Categoria inválida para DCTFWeb.')

        dados: Dict[str, Any] = {'categoria': categoria}

        if categoria == 'GERAL_MENSAL':
            competencia_limpa = self._only_digits(competencia or '')
            if len(competencia_limpa) != 6:
                raise ValueError('Competência inválida. Use o formato AAAAMM, exemplo: 202405')

            dados['anoPA'] = competencia_limpa[:4]
            dados['mesPA'] = competencia_limpa[-2:]

        elif categoria == 'GERAL_13o_SALARIO':
            ano_limpo = self._only_digits(ano_pa or competencia or '')
            if len(ano_limpo) == 6:
                ano_limpo = ano_limpo[:4]
            if len(ano_limpo) != 4:
                raise ValueError('Ano PA inválido para 13º salário. Use o formato AAAA, exemplo: 2024')

            dados['anoPA'] = ano_limpo

        return self.montar_payload_emitir(
            setting=setting,
            contribuinte_numero=contribuinte_numero,
            id_sistema=self.ID_SISTEMA_DCTFWEB,
            id_servico=self.ID_SERVICO_DCTFWEB,
            versao_sistema=self.VERSAO_SISTEMA_DCTFWEB,
            dados=dados,
        )

    def emitir_pdf_dctfweb(
        self,
        contribuinte_numero: str,
        categoria: str,
        competencia: Optional[str] = None,
        ano_pa: Optional[str] = None,
    ) -> bytes | None:
        """
        Orquestra emissao DCTFWEB (DARF): get_setting, montar_payload, emitir, extrair_pdf, b64decode.

        Bytecode linhas 1306-1361.
        """
        setting = AppSetting.query.first()
        if not setting:
            raise ValueError('Configurações da aplicação não encontradas')

        payload = self.montar_payload_dctfweb(
            setting=setting,
            contribuinte_numero=contribuinte_numero,
            categoria=categoria,
            competencia=competencia,
            ano_pa=ano_pa,
        )

        response_json = self.emitir(setting=setting, payload=payload)
        pdf_base64 = self.extrair_pdf_base64(response_json)

        try:
            return base64.b64decode(pdf_base64)
        except Exception:
            raise Exception('PDF retornado pela SERPRO não está em base64 válido')
