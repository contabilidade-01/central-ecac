"""
Serviço para consulta e gerenciamento de parcelamentos via SERPRO.

Reconstituído fielmente do bytecode do exe.
Fonte da verdade: dis/services/parcelamentos_serpro_service.txt (2942 linhas)
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import certifi
import pycurl
from flask import current_app
from app.extensions import db
from app.models import AppSetting, Company, ParcelamentoParcela, ParcelamentoPedido
from app.services.serpro_service import SerproService
from app.services.serpro_procurador_service import SerproProcuradorService
from app.services.api_usage_service import ApiUsageService
from app.services.serpro_logging import (
    log_serpro_request,
    log_serpro_response,
    log_serpro_exception,
)
from app.utils.paths import app_data_dir


logger = logging.getLogger(__name__)


# Timestamp de progresso de processamento (por empresa)
def update_parcelamento_progress(
    company_id: int,
    status: str | None = None,
    step: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    finished: bool = False,
) -> None:
    """Atualiza status de processamento em tempo real."""
    company = db.session.get(Company, company_id)
    if not company:
        return

    if status is not None:
        company.processing_status = status
    if step is not None:
        company.processing_step = step
    if progress is not None:
        company.processing_progress = progress
    if message is not None:
        company.processing_message = message

    if status == "processing" and not company.processing_started_at:
        company.processing_started_at = datetime.now()

    if finished:
        company.last_processed_at = datetime.now()

    db.session.commit()


# Lock para serializar emissão de PDF
PDF_EMISSION_LOCK = threading.Lock()


# CONSTANTE: PARCELAMENTO_TYPES (do exe)
PARCELAMENTO_TYPES = {
    "PARC_SN": {
        "label": "Parcelamento Simples Nacional",
        "flag": "consultar_parc_sn",
        "pedido": {"idSistema": "PARCSN", "idServico": "PEDIDOSPARC163", "versaoSistema": "1.0"},
        "parcelas": {"idSistema": "PARCSN", "idServico": "PARCELASPARAGERAR162", "versaoSistema": "1.0"},
        "pdf": {"idSistema": "PARCSN", "idServico": "GERARDAS161", "versaoSistema": "1.0"},
    },
    "PARC_MEI": {
        "label": "Parcelamento MEI",
        "flag": "consultar_parc_mei",
        "pedido": {"idSistema": "PARCMEI", "idServico": "PEDIDOSPARC203", "versaoSistema": "1.0"},
        "parcelas": {"idSistema": "PARCMEI", "idServico": "PARCELASPARAGERAR202", "versaoSistema": "1.0"},
        "pdf": {"idSistema": "PARCMEI", "idServico": "GERARDAS201", "versaoSistema": "1.0"},
    },
    "PERT_SN": {
        "label": "PERT Simples Nacional",
        "flag": "consultar_pert_sn",
        "pedido": {"idSistema": "PERTSN", "idServico": "PEDIDOSPARC183", "versaoSistema": "1.0"},
        "parcelas": {"idSistema": "PERTSN", "idServico": "PARCELASPARAGERAR182", "versaoSistema": "1.0"},
        "pdf": {"idSistema": "PERTSN", "idServico": "GERARDAS181", "versaoSistema": "1.0"},
    },
    "PERT_MEI": {
        "label": "PERT MEI",
        "flag": "consultar_pert_mei",
        "pedido": {"idSistema": "PERTMEI", "idServico": "PEDIDOSPARC223", "versaoSistema": "1.0"},
        "parcelas": {"idSistema": "PERTMEI", "idServico": "PARCELASPARAGERAR222", "versaoSistema": "1.0"},
        "pdf": {"idSistema": "PERTMEI", "idServico": "GERARDAS221", "versaoSistema": "1.0"},
    },
    "RELP_SN": {
        "label": "RELP Simples Nacional",
        "flag": "consultar_relp_sn",
        "pedido": {"idSistema": "RELPSN", "idServico": "PEDIDOSPARC173", "versaoSistema": "1.0"},
        "parcelas": {"idSistema": "RELPSN", "idServico": "PARCELASPARAGERAR192", "versaoSistema": "1.0"},
        "pdf": {"idSistema": "RELPSN", "idServico": "GERARDAS191", "versaoSistema": "1.0"},
    },
    "RELP_MEI": {
        "label": "RELP MEI",
        "flag": "consultar_relp_mei",
        "pedido": {"idSistema": "RELPMEI", "idServico": "PEDIDOSPARC233", "versaoSistema": "1.0"},
        "parcelas": {"idSistema": "RELPMEI", "idServico": "PARCELASPARAGERAR232", "versaoSistema": "1.0"},
        "pdf": {"idSistema": "RELPMEI", "idServico": "GERARDAS231", "versaoSistema": "1.0"},
    },
}


class ParcelamentosSerproService:
    """Serviço para consulta e gerenciamento de parcelamentos via SERPRO.

    Reconstituído fielmente do bytecode do exe original.
    Não alterar sem validar contra dis/services/parcelamentos_serpro_service.txt.
    """

    PROCESSING_STALE_AFTER = 3600  # 1 hora em segundos

    # O exe nao define __init__ nesta classe — nao ha estado de instancia a inicializar.

    def _parse_serpro_error(self, status_code: int, response_text: str) -> Dict[str, Any]:
        """Parse erro da SERPRO para mensagem amigável."""
        try:
            resp = json.loads(response_text)
            erros = resp.get("errors", [])
            if erros:
                msgs = [e.get("message", str(e)) for e in erros]
                return {"erro": "; ".join(msgs)}
            return {"erro": response_text[:200]}
        except Exception:
            return {"erro": response_text[:200]}

    def _get_setting(self) -> AppSetting | None:
        """Obtém configurações globais."""
        return db.session.query(AppSetting).first()

    def _load_certificate(self, certificado_path: str) -> tuple[bytes, str] | None:
        """Carrega certificado A1 do disco."""
        try:
            setting = self._get_setting()
            if not setting or not setting.certificado_password:
                return None
            if not Path(certificado_path).exists():
                return None
            with open(certificado_path, "rb") as f:
                cert_bytes = f.read()
            return (cert_bytes, setting.certificado_password)
        except Exception as e:
            logger.exception(f"Erro ao carregar certificado: {e}")
            return None

    def _create_serpro_service(self) -> SerproService | None:
        """Cria cliente SERPRO com certificado.

        O construtor do exe exige 6 argumentos: (certificate_content,
        certificate_password, contratante_cnpj, contador_cnpj, consumer_key,
        consumer_secret) — mesmo padrão de ReportService.process_company().
        """
        try:
            setting = self._get_setting()
            if not setting:
                return None

            certificado = self._load_certificate(setting.certificado_path)
            if not certificado:
                logger.error('Certificado não carregado: %s', setting.certificado_path)
                return None

            cert_bytes, cert_password = certificado

            return SerproService(
                certificate_content=cert_bytes,
                certificate_password=cert_password,
                contratante_cnpj=setting.contador_cnpj,
                contador_cnpj=setting.contador_cnpj,
                consumer_key=setting.serpro_consumer_key,
                consumer_secret=setting.serpro_consumer_secret,
            )
        except Exception as e:
            logger.exception(f"Erro ao criar SerproService: {e}")
            return None

    def _post_serpro(
        self,
        serpro_service: SerproService,
        endpoint: str,
        cnpj: str,
        pedido_config: Dict[str, Any],
        dados: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        """POST para a SERPRO — reconstruido do bytecode (linha 149 do exe).

        O exe faz o POST com **pycurl**, no mesmo padrao de
        SerproService.request_protocol(). NAO existe `SerproService.post()`.
        """
        try:
            # Caminho com procurador PF: monta o payload pelo proprio procurador
            if isinstance(serpro_service, SerproProcuradorService):
                payload = serpro_service.build_payload(
                    contribuinte_numero=cnpj,
                    id_sistema=pedido_config["idSistema"],
                    id_servico=pedido_config["idServico"],
                    versao_sistema=pedido_config.get("versaoSistema", "1.0"),
                    dados=json.dumps(dados) if dados else "",
                )
                return self._post_serpro_payload(serpro_service, endpoint, payload)

            auth_tokens = serpro_service._get_auth_token()
            if not auth_tokens:
                return {"success": False, "error": "Falha ao autenticar na Serpro"}

            tipo_contador = 1 if len(serpro_service.contador_cnpj) == 11 else 2

            payload = {
                "contratante": {"numero": serpro_service.contratante_cnpj, "tipo": 2},
                "autorPedidoDados": {"numero": serpro_service.contador_cnpj,
                                     "tipo": tipo_contador},
                "contribuinte": {"numero": cnpj, "tipo": 2},
                "pedidoDados": {
                    "idSistema": pedido_config["idSistema"],
                    "idServico": pedido_config["idServico"],
                    "versaoSistema": pedido_config.get("versaoSistema", "1.0"),
                    "dados": json.dumps(dados) if dados else "",
                },
            }

            buffer = BytesIO()
            url = 'https://gateway.apiserpro.serpro.gov.br/integra-contador/v1/' + endpoint

            headers = [
                f"jwt_token:{auth_tokens['jwt_token']}",
                f"Authorization: Bearer {auth_tokens['access_token']}",
                'Content-Type: application/json',
                'accept: text/plain',
            ]

            c = pycurl.Curl()
            c.setopt(c.URL, url)
            c.setopt(c.POSTFIELDS, json.dumps(payload))
            c.setopt(c.HTTPHEADER, headers)
            c.setopt(c.WRITEDATA, buffer)
            c.setopt(c.SSLVERSION, pycurl.SSLVERSION_TLSv1_2)
            try:
                c.setopt(pycurl.CAINFO, certifi.where())
            except Exception:
                pass
            c.setopt(c.TIMEOUT, 120)

            try:
                started_at = log_serpro_request(
                    'POST', url, headers=headers, payload=payload,
                    context=f"parcelamentos_{pedido_config['idServico']}")
                c.perform()
                status_code = c.getinfo(pycurl.HTTP_CODE)
                response_text = buffer.getvalue().decode('utf-8', errors='replace')
                log_serpro_response(
                    url, status_code, response_text, started_at=started_at,
                    context=f"parcelamentos_{pedido_config['idServico']}")
            finally:
                c.close()

            ApiUsageService.register_usage(
                route_type='consultar',
                endpoint=pedido_config['idServico'],
            )

            if status_code != 200:
                return self._parse_serpro_error(status_code, response_text)

            return json.loads(response_text)
        except Exception as e:
            logger.exception(f"Erro em _post_serpro: {e}")
            return None

    def _post_serpro_payload(
        self,
        procurador_service: SerproProcuradorService,
        endpoint: str,
        payload: Dict[str, Any],
        retried: bool = False,
    ) -> Dict[str, Any] | None:
        """POST com procurador PF."""
        try:
            # Implementar assinatura XML e POST
            # TODO: validar contra bytecode
            return None
        except Exception as e:
            logger.exception(f"Erro em _post_serpro_payload: {e}")
            return None

    def _parse_yyyymmdd(self, value):
        """Converte o formato numerico da SERPRO (ex.: 20221205) em date.

        A API devolve as datas como INTEIRO no formato YYYYMMDD — as colunas do modelo
        (`data_pedido`, `data_situacao`, `data_vencimento`) sao db.Date, portanto o
        retorno tem de ser um objeto date, nao string.
        """
        if not value:
            return None
        texto = str(value).strip()
        if len(texto) != 8 or not texto.isdigit():
            return None
        try:
            return datetime.strptime(texto, '%Y%m%d').date()
        except ValueError:
            return None

    def _parse_iso_date(self, value):
        """Parse de data ISO (YYYY-MM-DD) para date."""
        if not value:
            return None
        try:
            return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
        except ValueError:
            return None

    def _decimal(self, value: str | float | None) -> Decimal:
        """Parse valor para Decimal."""
        if value is None:
            return Decimal("0.00")
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0.00")

    def _parcela_key(self, parcela: Dict[str, Any]) -> str:
        """Gera chave única para parcela (numero)."""
        return str(parcela.get("numeroParcela", parcela.get("numero", "")))

    def _is_ativo(self, situacao: str | None) -> bool:
        """Determina se o pedido de parcelamento esta ativo.

        Reconstruido do bytecode (linha 308):
            return str(situacao or '').strip().lower() == 'em parcelamento'

        Atencao: NAO e busca por 'VIGENTE'/'ATIVO' — a SERPRO devolve exatamente
        'Em parcelamento' (visto na Sandra Isidio, pedido nº 3).
        """
        return str(situacao or '').strip().lower() == 'em parcelamento'

    def _selected_types_for_company(self, company: Company) -> List[str]:
        """Retorna tipos selecionados para empresa."""
        tipos = []
        for tipo_key, cfg in PARCELAMENTO_TYPES.items():
            flag = cfg["flag"]
            if hasattr(company, flag) and getattr(company, flag):
                tipos.append(tipo_key)
        return tipos

    def _save_pdf_local(
        self, company: Company, tipo: str, parcela_key: str, pdf_base64: str
    ) -> str | None:
        """Salva PDF em disco local."""
        try:
            pdf_dir = Path(app_data_dir()) / "parcelas" / str(company.id)
            pdf_dir.mkdir(parents=True, exist_ok=True)

            pdf_bytes = base64.b64decode(pdf_base64)
            pdf_filename = f"{tipo}_{parcela_key}.pdf"
            pdf_path = pdf_dir / pdf_filename

            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)

            return str(pdf_path)
        except Exception as e:
            logger.exception(f"Erro ao salvar PDF local: {e}")
            return None

    def buscar_pedidos_tipo(self, company: Company, tipo: str) -> Dict[str, Any] | None:
        """Busca pedidos de um tipo específico."""
        try:
            if tipo not in PARCELAMENTO_TYPES:
                return {"erro": f"Tipo inválido: {tipo}"}

            cfg = PARCELAMENTO_TYPES[tipo]
            serpro_service = self._create_serpro_service()
            if not serpro_service:
                return {"erro": "Erro ao criar SerproService"}

            # POST para obter pedidos
            endpoint = "Consultar"   # o exe usa o sufixo do gateway, nao o caminho inteiro
            response = self._post_serpro(
                serpro_service,
                endpoint,
                company.cnpj,
                cfg["pedido"],
                {},
            )

            if not response:
                return {"erro": "Erro ao consultar SERPRO"}
            if isinstance(response, dict) and response.get("erro"):
                return response

            # A SERPRO devolve `dados` como STRING JSON — precisa de um segundo parse.
            # A chave dos itens é 'parcelamentos' (confirmado nas constantes do exe).
            dados_raw = response.get("dados")
            if isinstance(dados_raw, str):
                dados_json = json.loads(dados_raw) if dados_raw.strip() else {}
            else:
                dados_json = dados_raw or {}

            pedidos = dados_json.get("parcelamentos", []) or []

            ativos = 0
            for item in pedidos:
                numero = str(item.get("numero", "")).strip()
                if not numero:
                    continue

                situacao = item.get("situacao")
                ativo = self._is_ativo(situacao)
                if ativo:
                    ativos += 1

                pedido = (
                    db.session.query(ParcelamentoPedido)
                    .filter_by(company_id=company.id, tipo=tipo, numero=numero)
                    .first()
                )
                if not pedido:
                    pedido = ParcelamentoPedido(
                        company_id=company.id, tipo=tipo, numero=numero)
                    db.session.add(pedido)

                pedido.data_pedido = self._parse_yyyymmdd(item.get("dataDoPedido"))
                pedido.situacao = situacao
                pedido.data_situacao = self._parse_yyyymmdd(item.get("dataDaSituacao"))
                pedido.ativo = ativo
                pedido.source_json = item

            db.session.commit()

            return {
                "success": True,
                "tipo": tipo,
                "pedidos": pedidos,
                "message": f"{len(pedidos)} pedido(s), {ativos} ativo(s).",
            }
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Erro em buscar_pedidos_tipo: {e}")
            return {"erro": str(e)}

    def buscar_pedidos_empresa(
        self, company_id: int, tipos: List[str] | None = None
    ) -> Dict[str, Any]:
        """Busca pedidos de todos os tipos (thread)."""
        company = db.session.get(Company, company_id)
        if not company:
            return {"erro": "Empresa não encontrada"}

        if not tipos:
            tipos = self._selected_types_for_company(company)

        result = {"pedidos": {}}
        for tipo in tipos:
            response = self.buscar_pedidos_tipo(company, tipo)
            if response and "pedidos" in response:
                result["pedidos"][tipo] = response["pedidos"]

        return result

    def buscar_pedidos_todas(self) -> Dict[str, Any]:
        """Busca pedidos de todas as empresas com tipos habilitados."""
        companies = db.session.query(Company).all()
        result = {"empresas": {}}
        for company in companies:
            pedidos = self.buscar_pedidos_empresa(company.id)
            if pedidos.get("pedidos"):
                result["empresas"][company.cnpj] = pedidos

        return result

    def empresa_tem_pedido_ativo(self, company_id: int, tipo: str) -> bool:
        """Verifica se empresa tem pedido ativo de um tipo."""
        try:
            pedido = (
                db.session.query(ParcelamentoPedido)
                .filter_by(company_id=company_id, tipo=tipo, ativo=True)
                .first()
            )
            return pedido is not None
        except Exception:
            return False

    def buscar_parcelas_tipo(
        self,
        company: Company,
        tipo: str,
        emitir_pdfs: bool = False,
        progress_callback: callable | None = None,
    ) -> Dict[str, Any] | None:
        """Busca parcelas de um tipo específico."""
        try:
            if tipo not in PARCELAMENTO_TYPES:
                return {"erro": f"Tipo inválido: {tipo}"}

            cfg = PARCELAMENTO_TYPES[tipo]
            serpro_service = self._create_serpro_service()
            if not serpro_service:
                return {"erro": "Erro ao criar SerproService"}

            # POST para obter parcelas
            endpoint = "Consultar"   # o exe usa o sufixo do gateway, nao o caminho inteiro
            response = self._post_serpro(
                serpro_service,
                endpoint,
                company.cnpj,
                cfg["parcelas"],
                {"parcelaParaEmitir": emitir_pdfs},
            )

            if not response:
                return {"erro": "Erro ao consultar SERPRO"}

            # Parse resposta
            parcelas_data = response.get("parcelamentos", [])
            parcelas_salvas = []

            for i, parc in enumerate(parcelas_data):
                parcela_key = self._parcela_key(parc)

                # Salvar em BD
                parcela_obj = ParcelamentoParcela(
                    company_id=company.id,
                    tipo=tipo,
                    parcela=parcela_key,
                    descricao=parc.get("descricao", ""),
                    data_vencimento=self._parse_yyyymmdd(parc.get("dataVencimento")),
                    valor_total=self._decimal(parc.get("valorTotal")),
                    source_json=parc,
                )

                # Salvar PDF se presente
                if emitir_pdfs and "pdf" in parc and parc["pdf"]:
                    with PDF_EMISSION_LOCK:
                        pdf_path = self._save_pdf_local(
                            company, tipo, parcela_key, parc["pdf"]
                        )
                        parcela_obj.pdf_local_path = pdf_path

                db.session.merge(parcela_obj)
                parcelas_salvas.append(parcela_obj)

                # Callback progress
                if progress_callback:
                    progress_callback(
                        len(parcelas_salvas),
                        len(parcelas_data),
                        parcela_key,
                        f"Processando parcela {i+1}/{len(parcelas_data)}",
                    )

            db.session.commit()
            return {"tipo": tipo, "parcelas": parcelas_salvas}

        except Exception as e:
            logger.exception(f"Erro em buscar_parcelas_tipo: {e}")
            return {"erro": str(e)}

    def buscar_parcelas_empresa(
        self, company_id: int, tipos: List[str] | None = None, emitir_pdfs: bool = False
    ) -> Dict[str, Any]:
        """Busca parcelas de todos os tipos (thread)."""
        company = db.session.get(Company, company_id)
        if not company:
            return {"erro": "Empresa não encontrada"}

        if not tipos:
            tipos = self._selected_types_for_company(company)

        result = {"parcelas": {}}
        for tipo in tipos:
            response = self.buscar_parcelas_tipo(company, tipo, emitir_pdfs)
            if response and "parcelas" in response:
                result["parcelas"][tipo] = response["parcelas"]

        return result

    def buscar_parcelas_todas_com_pedido_ativo(
        self, emitir_pdfs: bool = False
    ) -> Dict[str, Any]:
        """Busca parcelas de todas as empresas com pedidos ativos."""
        companies = db.session.query(Company).all()
        result = {"empresas": {}}
        for company in companies:
            parcelas = self.buscar_parcelas_empresa(company.id, emitir_pdfs=emitir_pdfs)
            if parcelas.get("parcelas"):
                result["empresas"][company.cnpj] = parcelas

        return result
