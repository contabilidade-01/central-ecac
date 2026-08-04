"""
Serviço para Caixa Postal do e-CAC via SERPRO.

Reconstituído fielmente do bytecode do exe.
Fonte da verdade: dis/services/caixa_postal_service.txt (3569 linhas)

Constantes do exe:
- ID_SISTEMA = 'CAIXAPOSTAL'
- ID_SERVICO_INDICADOR = 'INNOVAMSG63'
- ID_SERVICO_LISTA = 'MSGCONTRIBUINTE61'
- ID_SERVICO_DETALHE = 'MSGDETALHAMENTO62'
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.extensions import db
from app.models import AppSetting, CaixaPostalMensagem, CaixaPostalMonitoramento, Company
from app.services.api_usage_service import ApiUsageService
from app.services.procuracao_service import ProcuracaoService  # DESVIO INTENCIONAL (5o)
from app.services.serpro_logging import serpro_post
from app.services.serpro_procurador_service import (
    SerproProcuradorService,
    only_digits,
    tipo_pessoa,
)
from app.services import certificado
from app.services.serpro_service import SerproService
from app.utils.paths import app_data_dir


logger = logging.getLogger(__name__)


# ========== MONITOR_STATUS (estado global em memória) ==========

MONITOR_STATUS: Dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "message": "",
    "current_company_id": None,
    "current_company_name": "",
    "total": 0,
    "checked": 0,
    "downloaded": 0,
    "errors": 0,
    "started_at": None,
    "finished_at": None,
}


def get_monitor_status() -> Dict[str, Any]:
    """Retorna cópia do status global do monitor."""
    return dict(MONITOR_STATUS)


def update_monitor_status(**kwargs) -> None:
    """Atualiza status global do monitor."""
    MONITOR_STATUS.update(kwargs)


# `only_digits` e `tipo_pessoa` sao IMPORTADAS de serpro_procurador_service, como no exe.


# ========== CLASSE PRINCIPAL ==========


class CaixaPostalService:
    """Serviço para Caixa Postal do e-CAC via SERPRO.

    Reconstruído fielmente do bytecode do exe.
    """

    BASE_URL = "https://gateway.apiserpro.serpro.gov.br/integra-contador/v1"
    ID_SISTEMA = "CAIXAPOSTAL"
    VERSAO_SISTEMA = "1.0"
    ID_SERVICO_INDICADOR = "INNOVAMSG63"
    ID_SERVICO_LISTA = "MSGCONTRIBUINTE61"
    ID_SERVICO_DETALHE = "MSGDETALHAMENTO62"

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def _get_setting(self) -> AppSetting:
        """Obtém configuração da aplicação."""
        setting = db.session.query(AppSetting).first()
        if not setting:
            raise ValueError("Configurações não encontradas")
        return setting

    @staticmethod
    def _decode_json_if_needed(value: Any) -> Any:
        """Decodifica JSON string se necessário."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    @staticmethod
    def _first_conteudo_item(dados: Any) -> Dict[str, Any]:
        """Extrai primeiro item de conteúdo."""
        if isinstance(dados, dict):
            conteudo = dados.get("conteudo")
            if isinstance(conteudo, list) and conteudo:
                return conteudo[0] if isinstance(conteudo[0], dict) else {}
            elif isinstance(conteudo, dict):
                return conteudo
            return dados
        return {}

    @staticmethod
    def _conteudo_items(dados: Any) -> List[Dict[str, Any]]:
        """Extrai lista de itens de conteúdo."""
        if not isinstance(dados, dict):
            return []
        conteudo = dados.get("conteudo")
        if isinstance(conteudo, list):
            return [item for item in conteudo if isinstance(item, dict)]
        elif isinstance(conteudo, dict):
            return [conteudo]
        return []

    def _extract_lista_mensagens(self, dados: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrai lista de mensagens do response."""
        first = self._first_conteudo_item(dados)
        if isinstance(first, dict):
            mensagens = first.get("listaMensagens")
            if isinstance(mensagens, list):
                return [m for m in mensagens if isinstance(m, dict)]
        items = self._conteudo_items(dados)
        if items and any("isn" in item for item in items):
            return items
        return []

    def _extract_detalhes_mensagem(self, dados: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extrai detalhes de mensagem do response."""
        first = self._first_conteudo_item(dados)
        if isinstance(first, dict):
            for key in ("detalheMensagem", "mensagem", "conteudo"):
                value = first.get(key)
                if isinstance(value, list):
                    return [v for v in value if isinstance(v, dict)]
                elif isinstance(value, dict):
                    return [value]
            if first:
                return [first]
        return []

    @staticmethod
    def _parse_yyyymmdd(value: Any) -> Optional[date]:
        """Parse data YYYYMMDD para date."""
        try:
            text = only_digits(value)
            if len(text) == 8:
                return datetime.strptime(text, "%Y%m%d").date()
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _format_hhmmss(value: Any) -> str:
        """Formata hora HHMMSS para HH:MM:SS."""
        text = only_digits(value)
        if len(text) == 6:
            return f"{text[0:2]}:{text[2:4]}:{text[4:6]}"
        return str(value) if value else ""

    @staticmethod
    def _apply_placeholders(text: Any, variables: Any) -> str:
        """Substitui placeholders ++1++, ++2++ com valores."""
        result = str(text) if text else ""
        if isinstance(variables, str):
            try:
                variables = json.loads(variables)
            except Exception:
                variables = [variables]
        if not isinstance(variables, list):
            return result
        for idx, value in enumerate(variables, start=1):
            result = result.replace(f"++{idx}++", str(value))
        return result

    def _manual_headers(self, setting: AppSetting) -> Dict[str, str]:
        """Headers de autenticacao direta (linha 171 do exe).

        SerproService exige os 6 argumentos do construtor; o token vem de
        `get_auth_token()` (devolve access_token + jwt_token).
        """
        auth_service = SerproService(
            # DESVIO 3o: resolve o caminho — o gravado aponta para a máquina onde
            # o certificado foi cadastrado e não existe no servidor.
            certificate_content=certificado.carregar(setting.certificado_path),
            certificate_password=(setting.certificado_password or "").strip(),
            contratante_cnpj=only_digits(setting.contador_cnpj),
            contador_cnpj=only_digits(setting.contador_cnpj),
            consumer_key=(setting.serpro_consumer_key or "").strip(),
            consumer_secret=(setting.serpro_consumer_secret or "").strip(),
        )

        auth_tokens = auth_service.get_auth_token()
        if not auth_tokens:
            raise Exception("Falha ao autenticar na API da SERPRO")

        return {
            "jwt_token": auth_tokens["jwt_token"],
            "Authorization": f"Bearer {auth_tokens['access_token']}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }

    def _build_payload(
        self,
        setting: AppSetting,
        contribuinte_numero: str,
        id_servico: str,
        dados: Any,
    ) -> Dict[str, Any]:
        """Corpo do pedido ao Integra Contador (linha 190 do exe).

        O envelope tem QUATRO chaves de primeiro nivel — contratante, autorPedidoDados,
        contribuinte e pedidoDados. Enviar `idSistema`/`idServico` na raiz faz a SERPRO
        devolver 500 com os quatro campos nulos.

        `dados` vai como STRING dentro de pedidoDados (json.dumps quando nao for str).
        """
        if SerproProcuradorService.enabled(setting):
            return SerproProcuradorService(setting).build_payload(
                contribuinte_numero=contribuinte_numero,
                id_sistema=self.ID_SISTEMA,
                id_servico=id_servico,
                versao_sistema=self.VERSAO_SISTEMA,
                dados=dados,
            )

        contratante = only_digits(setting.contador_cnpj)
        contribuinte = only_digits(contribuinte_numero)
        return {
            "contratante": {"numero": contratante, "tipo": tipo_pessoa(contratante)},
            "autorPedidoDados": {"numero": contratante, "tipo": tipo_pessoa(contratante)},
            "contribuinte": {"numero": contribuinte, "tipo": tipo_pessoa(contribuinte)},
            "pedidoDados": {
                "idSistema": self.ID_SISTEMA,
                "idServico": id_servico,
                "versaoSistema": self.VERSAO_SISTEMA,
                "dados": dados if isinstance(dados, str) else json.dumps(dados, ensure_ascii=False),
            },
        }

    def _headers(self, setting: AppSetting, contribuinte_numero: str) -> Dict[str, str]:
        """Headers da requisicao (linha 214 do exe)."""
        if SerproProcuradorService.enabled(setting):
            return SerproProcuradorService(setting).auth_headers(
                accept="application/json",
                contribuinte_numero=contribuinte_numero,
            )
        return self._manual_headers(setting)

    def _post(
        self,
        endpoint: str,
        setting: AppSetting,
        company: Company,
        id_servico: str,
        dados: Any,
    ) -> Dict[str, Any]:
        """POST no gateway do Integra Contador (linha 222 do exe).

        `endpoint` e o SUFIXO do gateway ('Monitorar', 'Consultar', 'Apoiar', 'Emitir');
        o caminho completo duplicaria a URL e devolveria 404.

        O custo de API NAO e registrado aqui — o exe chama
        `ApiUsageService.register_usage()` em cada chamador (lista e detalhe).
        """
        payload = self._build_payload(setting, company.cnpj, id_servico, dados)
        safe_payload = dict(payload)
        logger.info(
            '[CAIXA_POSTAL][SERPRO] POST endpoint=%s empresa_id=%s cnpj=%s idServico=%s payload=%s',
            endpoint,
            company.id,
            company.cnpj,
            id_servico,
            json.dumps(safe_payload, ensure_ascii=False)[:3000],
        )

        headers = self._headers(setting, company.cnpj)
        response = serpro_post(
            f"{self.BASE_URL}/{endpoint}",
            headers=headers,
            json_payload=payload,
            context=f"caixa_postal_{endpoint}_{id_servico}",
            timeout=self.timeout,
        )
        logger.info(
            '[CAIXA_POSTAL][SERPRO] HTTP endpoint=%s empresa_id=%s idServico=%s status=%s body=%s',
            endpoint,
            company.id,
            id_servico,
            response.status_code,
            (response.text or '')[:4000],
        )

        if SerproProcuradorService.enabled(setting) and response.status_code in (401, 403):
            SerproProcuradorService(setting).invalidate_authorization_token()
            headers = self._headers(setting, company.cnpj)
            response = serpro_post(
                f"{self.BASE_URL}/{endpoint}",
                headers=headers,
                json_payload=payload,
                context=f"caixa_postal_{endpoint}_{id_servico}_retry",
                timeout=self.timeout,
            )

        response_text = response.text or ''
        if response.status_code != 200:
            raise Exception(
                f"Erro SERPRO {id_servico}. Status: {response.status_code}. "
                f"Resposta: {response_text[:1000]}"
            )

        try:
            response_json = response.json()
        except Exception:
            raise Exception(
                f"Resposta da SERPRO não está em JSON válido: {response_text[:1000]}"
            )

        # A SERPRO devolve `dados` como string JSON — precisa do 2o json.loads
        if 'dados' in response_json:
            response_json['dados'] = self._decode_json_if_needed(response_json['dados'])
        return response_json

    def monitorar_empresa(self, company: Company, setting: AppSetting = None) -> CaixaPostalMonitoramento:
        """Indicador de mensagens novas da empresa (linha 274 do exe).

        O endpoint do indicador e **Monitorar** (nao 'Consultar') e o `dados` vai vazio.
        """
        setting = setting or self._get_setting()
        now = datetime.utcnow()
        monitor = CaixaPostalMonitoramento.query.filter_by(company_id=company.id).first()
        if not monitor:
            monitor = CaixaPostalMonitoramento(
                company_id=company.id, created_at=now, updated_at=now)

        try:
            logger.info(
                '[CAIXA_POSTAL][INDICADOR] Monitorando empresa_id=%s nome=%s cnpj=%s servico=%s',
                company.id, company.razao_social, company.cnpj, self.ID_SERVICO_INDICADOR,
            )
            # O indicador NAO e cobrado — o proprio frontend do exe diz "Essa funcao e
            # gratuita na API Integra Contador" e "indicador gratuito da SERPRO". Por
            # isso o exe nao chama register_usage aqui; so na lista e no detalhe
            # ("As funcoes de buscar mensagens e detalhes sao pagas").
            response = self._post(
                'Monitorar', setting, company, self.ID_SERVICO_INDICADOR, '')

            # DESVIO INTENCIONAL (5o) — a sonda gratuita reabilita a empresa para as
            # chamadas pagas. Ver procuracao_service.py.
            ProcuracaoService.registrar_sucesso(company, self.ID_SERVICO_INDICADOR)

            dados = response.get('dados') if isinstance(response.get('dados'), dict) else {}
            indicador_dados = self._first_conteudo_item(dados)
            logger.info(
                '[CAIXA_POSTAL][INDICADOR] Resposta empresa_id=%s cnpj=%s dados=%s raw=%s',
                company.id,
                company.cnpj,
                json.dumps(dados, ensure_ascii=False)[:2000],
                json.dumps(response, ensure_ascii=False)[:4000],
            )
            indicador = int(indicador_dados.get('indicadorMensagensNovas') or 0)
            logger.info(
                '[CAIXA_POSTAL][INDICADOR] Resultado empresa_id=%s cnpj=%s indicadorMensagensNovas=%s',
                company.id, company.cnpj, indicador,
            )

            monitor.indicador_mensagens_novas = indicador
            monitor.possui_mensagens_novas = indicador > 0
            if indicador > 0:
                monitor.mensagens_baixadas = False
            monitor.ultima_consulta_monitoramento = now
            monitor.raw_json = response
            monitor.erro = None
        except Exception as exc:
            logger.exception('Erro ao monitorar caixa postal da empresa %s', company.id)
            monitor.ultima_consulta_monitoramento = now
            monitor.erro = str(exc)

            # DESVIO INTENCIONAL (5o) — guarda o erro EXATO (status, codigo, texto e
            # corpo bruto) e trava as chamadas pagas desta empresa.
            ProcuracaoService.registrar_erro(company, self.ID_SERVICO_INDICADOR, exc)

        monitor.updated_at = now
        db.session.add(monitor)
        db.session.commit()
        return monitor

    def monitorar_todas_empresas(
        self,
        only_if_due: bool = True,
        baixar_mensagens_quando_houver: bool = True,
        company_ids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Monitora as empresas ativas em lote (linha 324 do exe).

        `only_if_due=True` PULA quem já foi consultado nas últimas **20 horas**
        (`due_threshold`) — é o freio que evita repetir chamada paga no mesmo dia.
        A lista/detalhe só é baixada quando o indicador apontou mensagem nova E ela
        ainda não foi baixada (`possui_mensagens_novas and not mensagens_baixadas`).
        """
        setting = self._get_setting()
        now = datetime.utcnow()
        due_threshold = now - timedelta(hours=20)

        query = Company.query.filter_by(ativo=True)
        if company_ids:
            query = query.filter(Company.id.in_(
                [int(company_id) for company_id in company_ids if company_id]))
        companies = query.order_by(Company.razao_social.asc()).all()

        update_monitor_status(
            running=True,
            stage='starting',
            message='Iniciando monitoramento da caixa postal.',
            current_company_id=None,
            current_company_name='',
            total=len(companies),
            checked=0,
            downloaded=0,
            errors=0,
            started_at=now.isoformat(),
            finished_at=None,
        )

        checked = 0
        skipped = 0
        errors = 0
        downloaded = 0
        bloqueados = 0   # DESVIO INTENCIONAL (5o) — empresas travadas para chamadas pagas

        for company in companies:
            update_monitor_status(
                stage='monitoring',
                message='Verificando indicador de novas mensagens.',
                current_company_id=company.id,
                current_company_name=company.razao_social,
                checked=checked,
                downloaded=downloaded,
                errors=errors,
            )

            monitor = CaixaPostalMonitoramento.query.filter_by(
                company_id=company.id).first()
            if (only_if_due and monitor and monitor.ultima_consulta_monitoramento
                    and monitor.ultima_consulta_monitoramento > due_threshold):
                skipped += 1
                continue

            result = self.monitorar_empresa(company, setting=setting)
            checked += 1
            if result.erro:
                errors += 1
                continue

            if (baixar_mensagens_quando_houver and result.possui_mensagens_novas
                    and not result.mensagens_baixadas):
                # DESVIO INTENCIONAL (5o/8o) — a partir daqui as chamadas são PAGAS:
                # valem o mapa de procurações e o teto mensal de gasto.
                from app.services.limite_gasto_service import LimiteGastoService

                pode, motivo = ProcuracaoService.pode_gastar(company)
                if pode:
                    pode, motivo = LimiteGastoService.pode_gastar()
                if not pode:
                    bloqueados += 1
                    logger.warning(
                        '[CAIXA_POSTAL][BLOQUEIO] empresa_id=%s cnpj=%s — '
                        'chamadas pagas puladas: %s', company.id, company.cnpj, motivo)
                    update_monitor_status(
                        stage='monitoring',
                        message=f'{company.razao_social}: chamadas pagas puladas — {motivo}.',
                        current_company_id=company.id,
                        current_company_name=company.razao_social,
                        checked=checked,
                        downloaded=downloaded,
                        errors=errors,
                    )
                    continue

                try:
                    update_monitor_status(
                        stage='downloading',
                        message='Indicador apontou mensagens novas. Baixando lista e detalhes.',
                        current_company_id=company.id,
                        current_company_name=company.razao_social,
                        checked=checked,
                        downloaded=downloaded,
                        errors=errors,
                    )
                    self.consultar_mensagens_empresa(
                        company.id, status_leitura=0, baixar_detalhes=True)
                    downloaded += 1
                except Exception as exc:
                    logger.exception(
                        'Erro ao baixar mensagens da caixa postal da empresa %s', company.id)
                    result.erro = str(exc)
                    result.updated_at = datetime.utcnow()
                    db.session.add(result)
                    db.session.commit()
                    errors += 1

            update_monitor_status(
                stage='monitoring',
                message='Continuando monitoramento.',
                current_company_id=company.id,
                current_company_name=company.razao_social,
                checked=checked,
                downloaded=downloaded,
                errors=errors,
            )

        update_monitor_status(
            running=False,
            stage='finished',
            message='Monitoramento concluído.',
            current_company_id=None,
            current_company_name='',
            checked=checked,
            downloaded=downloaded,
            errors=errors,
            finished_at=datetime.utcnow().isoformat(),
        )

        return {
            "success": True,
            "checked": checked,
            "skipped": skipped,
            "errors": errors,
            "downloaded": downloaded,
            # chave aditiva do DESVIO INTENCIONAL (5o)
            "bloqueados": bloqueados,
        }

    def _salvar_mensagem_lista(self, company: Company, item: Dict[str, Any]) -> CaixaPostalMensagem:
        """Grava (ou ATUALIZA) a mensagem vinda da lista (linha 418 do exe).

        O assunto vem de `assuntoModelo` com o marcador `++VARIAVEL++` trocado por
        `valorParametroAssunto` — nao dos placeholders `++1++` do corpo.
        As colunas `*_raw_json` sao `db.JSON`: recebem o dict, nao `json.dumps`.
        Sem commit — quem chama faz o flush/commit.
        """
        now = datetime.utcnow()
        isn = str(item.get('isn') or '').strip()
        if not isn:
            raise ValueError('Mensagem sem ISN retornada pela SERPRO')

        mensagem = CaixaPostalMensagem.query.filter_by(
            company_id=company.id, isn=isn).first()
        if not mensagem:
            mensagem = CaixaPostalMensagem(
                company_id=company.id, isn=isn, created_at=now)

        assunto_modelo = item.get('assuntoModelo') or ''
        valor_parametro = item.get('valorParametroAssunto') or ''
        assunto = str(assunto_modelo).replace('++VARIAVEL++', str(valor_parametro or ''))

        mensagem.numero_controle = item.get('numeroControle') or mensagem.numero_controle
        mensagem.assunto = assunto or mensagem.assunto
        mensagem.data_envio = self._parse_yyyymmdd(item.get('dataEnvio')) or mensagem.data_envio
        mensagem.hora_envio = self._format_hhmmss(item.get('horaEnvio')) or mensagem.hora_envio
        mensagem.data_leitura = self._parse_yyyymmdd(item.get('dataLeitura')) or mensagem.data_leitura
        mensagem.data_ciencia = self._parse_yyyymmdd(item.get('dataCiencia')) or mensagem.data_ciencia
        mensagem.data_validade = self._parse_yyyymmdd(item.get('dataValidade')) or mensagem.data_validade
        mensagem.data_expiracao = self._parse_yyyymmdd(item.get('dataExpiracao')) or mensagem.data_expiracao
        mensagem.codigo_sistema_remetente = str(
            item.get('codigoSistemaRemetente') or mensagem.codigo_sistema_remetente or '')
        mensagem.codigo_modelo = str(item.get('codigoModelo') or mensagem.codigo_modelo or '')
        mensagem.origem_modelo = str(item.get('origemModelo') or mensagem.origem_modelo or '')
        mensagem.tipo_origem = str(item.get('tipoOrigem') or mensagem.tipo_origem or '')
        mensagem.descricao_origem = item.get('descricaoOrigem') or mensagem.descricao_origem
        mensagem.indicador_leitura = str(
            item.get('indicadorLeitura') or mensagem.indicador_leitura or '')
        mensagem.indicador_favorito = str(
            item.get('indicadorFavorito') or item.get('indFavorito')
            or mensagem.indicador_favorito or '')
        mensagem.relevancia = str(item.get('relevancia') or mensagem.relevancia or '')
        mensagem.valor_parametro_assunto = str(
            valor_parametro or mensagem.valor_parametro_assunto or '')
        mensagem.lista_raw_json = item
        mensagem.updated_at = now

        db.session.add(mensagem)
        return mensagem

    def _salvar_detalhe(self, mensagem: CaixaPostalMensagem, detail: Dict[str, Any]) -> None:
        """Grava o detalhe da mensagem (linha 454 do exe).

        Recebe UM detalhe (`detalhes[0]`), nao a resposta inteira: alem de marcar
        `detalhe_baixado`, atualiza assunto, datas, codigos e o corpo (`corpoModelo`
        com os placeholders `++N++` substituidos pelas `variaveis`).
        """
        now = datetime.utcnow()
        variaveis = detail.get('variaveis') or []
        assunto_modelo = detail.get('assuntoModelo')
        valor_parametro = detail.get('valorParametroAssunto')
        if assunto_modelo:
            mensagem.assunto = str(assunto_modelo).replace(
                '++VARIAVEL++', str(valor_parametro or ''))

        mensagem.numero_controle = detail.get('numeroControle') or mensagem.numero_controle
        mensagem.data_envio = self._parse_yyyymmdd(detail.get('dataEnvio')) or mensagem.data_envio
        mensagem.data_leitura = self._parse_yyyymmdd(detail.get('dataLeitura')) or mensagem.data_leitura
        mensagem.data_ciencia = self._parse_yyyymmdd(detail.get('dataCiencia')) or mensagem.data_ciencia
        mensagem.data_expiracao = self._parse_yyyymmdd(detail.get('dataExpiracao')) or mensagem.data_expiracao
        mensagem.codigo_sistema_remetente = str(
            detail.get('codigoSistemaRemetente') or mensagem.codigo_sistema_remetente or '')
        mensagem.codigo_modelo = str(detail.get('codigoModelo') or mensagem.codigo_modelo or '')
        mensagem.origem_modelo = str(detail.get('origemModelo') or mensagem.origem_modelo or '')
        mensagem.tipo_origem = str(detail.get('tipoOrigem') or mensagem.tipo_origem or '')
        mensagem.descricao_origem = detail.get('descricaoOrigem') or mensagem.descricao_origem
        mensagem.indicador_favorito = str(
            detail.get('indFavorito') or detail.get('indicadorFavorito')
            or mensagem.indicador_favorito or '')
        mensagem.relevancia = str(
            detail.get('indRelevanciaMsg') or detail.get('relevancia')
            or mensagem.relevancia or '')
        mensagem.valor_parametro_assunto = str(
            valor_parametro or mensagem.valor_parametro_assunto or '')
        mensagem.corpo = self._apply_placeholders(
            detail.get('corpoModelo') or mensagem.corpo or '', variaveis)
        mensagem.variaveis_json = variaveis if isinstance(variaveis, list) else [variaveis]
        mensagem.detalhe_raw_json = detail
        mensagem.detalhe_baixado = True
        mensagem.downloaded_at = now
        mensagem.updated_at = now

        db.session.add(mensagem)
        db.session.flush()

    def _recovery_path(self, company: Company, isn: str) -> Path:
        """Caminho para recuperação local de detalhe."""
        return Path(app_data_dir()) / "caixa_postal" / str(company.id) / f"{isn}.json"

    def _salvar_recuperacao_detalhe(
        self,
        company: Company,
        mensagem: CaixaPostalMensagem,
        detail_response: Dict[str, Any],
        detalhes: List[Dict[str, Any]],
    ) -> Path:
        """Salva detalhe em disco para recuperação."""
        path = self._recovery_path(company, mensagem.isn)
        path.parent.mkdir(parents=True, exist_ok=True)
        recovery_data = {
            "isn": mensagem.isn,
            "company_id": company.id,
            "detail_response": detail_response,
            "detalhes": detalhes,
            "timestamp": datetime.utcnow().isoformat(),
        }
        path.write_text(json.dumps(recovery_data, indent=2), encoding="utf-8")
        return path

    def _remover_recuperacao_detalhe(self, company: Company, isn: str) -> None:
        """Remove arquivo de recuperação."""
        path = self._recovery_path(company, isn)
        if path.exists():
            path.unlink()

    def _carregar_recuperacao_detalhe(
        self, company: Company, mensagem: CaixaPostalMensagem
    ) -> Optional[List[Dict[str, Any]]]:
        """Carrega detalhe de recuperação local."""
        path = self._recovery_path(company, mensagem.isn)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("detalhes")
            except Exception as e:
                logger.error(f"Erro ao carregar recuperação {path}: {e}")
        return None

    def baixar_detalhe_mensagem(self, message_id: int) -> Dict[str, Any]:
        """Baixa o detalhe de uma mensagem (linha 568 do exe).

        O `dados` do servico de detalhe e `{'isn': ...}` — nao um objeto `mensagem`.
        """
        setting = self._get_setting()
        mensagem = db.session.get(CaixaPostalMensagem, message_id)
        if not mensagem:
            return {"success": False, "message": "Mensagem não encontrada"}
        if not mensagem.company:
            return {"success": False, "message": "Empresa da mensagem não encontrada"}
        if not mensagem.isn:
            return {"success": False, "message": "Mensagem sem ISN para consultar detalhe"}

        company = mensagem.company
        detalhes_recuperados = self._carregar_recuperacao_detalhe(company, mensagem)
        if detalhes_recuperados:
            self._salvar_detalhe(mensagem, detalhes_recuperados[0])
            db.session.commit()
            self._remover_recuperacao_detalhe(company, mensagem.isn)
            return {
                "success": True,
                "message": "Detalhe recuperado do arquivo local e gravado com sucesso",
                "message_id": mensagem.id,
            }

        detail_response = self._post(
            'Consultar',
            setting,
            company,
            self.ID_SERVICO_DETALHE,
            {'isn': mensagem.isn},
        )
        ApiUsageService.register_usage(
            route_type='consultar',
            endpoint=self.ID_SERVICO_DETALHE,
            company_id=company.id,
        )

        detail_dados = (
            detail_response.get('dados')
            if isinstance(detail_response.get('dados'), dict)
            else {}
        )
        detalhes = self._extract_detalhes_mensagem(detail_dados)
        logger.info(
            '[CAIXA_POSTAL][DETALHE] empresa_id=%s cnpj=%s isn=%s total_extraido=%s dados=%s raw=%s',
            company.id,
            company.cnpj,
            mensagem.isn,
            len(detalhes),
            json.dumps(detail_dados, ensure_ascii=False)[:3000],
            json.dumps(detail_response, ensure_ascii=False)[:5000],
        )

        if not detalhes:
            return {"success": False, "message": "SERPRO não retornou detalhe para esta mensagem"}

        self._salvar_recuperacao_detalhe(company, mensagem, detail_response, detalhes)
        self._salvar_detalhe(mensagem, detalhes[0])
        db.session.commit()
        self._remover_recuperacao_detalhe(company, mensagem.isn)
        return {
            "success": True,
            "message": "Detalhe baixado com sucesso",
            "message_id": mensagem.id,
        }

    def consultar_mensagens_empresa(
        self,
        company_id: int,
        status_leitura: int = 0,
        baixar_detalhes: bool = True,
    ) -> Dict[str, Any]:
        """Lista as mensagens da empresa e baixa os detalhes (linha 622 do exe).

        O `dados` da lista leva `statusLeitura` e `indicadorPagina` como STRING, mais
        `ponteiroPagina` a partir da 2a pagina — a paginacao para quando
        `indicadorUltimaPagina == 'S'` ou quando nao vem ponteiro.
        """
        setting = self._get_setting()
        company = db.session.get(Company, company_id)
        if not company:
            return {"success": False, "message": "Empresa não encontrada"}

        saved: List[CaixaPostalMensagem] = []
        precisa_detalhe: set = set()   # DESVIO INTENCIONAL (6o) — ver laço da lista
        indicador_pagina = 0
        ponteiro = ''

        while True:
            dados = {
                'statusLeitura': str(status_leitura),
                'indicadorPagina': str(indicador_pagina),
            }
            if ponteiro:
                dados['ponteiroPagina'] = str(ponteiro)

            response = self._post(
                'Consultar', setting, company, self.ID_SERVICO_LISTA, dados)
            ApiUsageService.register_usage(
                route_type='consultar',
                endpoint=self.ID_SERVICO_LISTA,
                company_id=company.id,
            )

            response_dados = (
                response.get('dados')
                if isinstance(response.get('dados'), dict)
                else {}
            )
            response_conteudo = self._first_conteudo_item(response_dados)
            lista = self._extract_lista_mensagens(response_dados)
            logger.info(
                '[CAIXA_POSTAL][LISTA] empresa_id=%s cnpj=%s total_extraido=%s dados=%s raw=%s',
                company.id,
                company.cnpj,
                len(lista),
                json.dumps(response_dados, ensure_ascii=False)[:3000],
                json.dumps(response, ensure_ascii=False)[:5000],
            )

            for item in lista:
                if not isinstance(item, dict):
                    continue

                # DESVIO INTENCIONAL (6o) — decide ANTES de gravar se o detalhe precisa
                # ser (re)baixado. O exe rebaixa o detalhe de TODAS as mensagens da
                # caixa a cada consulta; com 16 mensagens isso são 16 chamadas pagas
                # repetidas. Gatilho para baixar: nunca foi baixado OU o item da lista
                # mudou em relação ao `lista_raw_json` guardado (dataLeitura,
                # dataCiencia, indicadorLeitura… — qualquer alteração na SERPRO).
                isn_item = str(item.get('isn') or '').strip()
                anterior = None
                if isn_item:
                    anterior = CaixaPostalMensagem.query.filter_by(
                        company_id=company.id, isn=isn_item).first()
                if (anterior is None or not anterior.detalhe_baixado
                        or anterior.lista_raw_json != item):
                    precisa_detalhe.add(isn_item)

                saved.append(self._salvar_mensagem_lista(company, item))

            db.session.flush()

            ultima = str(
                response_dados.get('indicadorUltimaPagina')
                or response_conteudo.get('indicadorUltimaPagina')
                or 'S'
            ).upper() == 'S'
            ponteiro = (
                response_dados.get('ponteiroProximaPagina')
                or response_conteudo.get('ponteiroProximaPagina')
                or ''
            )
            if ultima or not ponteiro:
                break
            indicador_pagina = 1

        db.session.commit()

        detalhes_baixados = 0
        detalhes_erros = 0
        detalhes_pulados = 0
        if baixar_detalhes:
            for mensagem in saved:
                if not mensagem.isn:
                    continue
                if mensagem.isn not in precisa_detalhe:
                    # DESVIO INTENCIONAL (6o) — detalhe já baixado e mensagem inalterada
                    detalhes_pulados += 1
                    continue
                try:
                    detail_response = self._post(
                        'Consultar',
                        setting,
                        company,
                        self.ID_SERVICO_DETALHE,
                        {'isn': mensagem.isn},
                    )
                    ApiUsageService.register_usage(
                        route_type='consultar',
                        endpoint=self.ID_SERVICO_DETALHE,
                        company_id=company.id,
                    )

                    detail_dados = (
                        detail_response.get('dados')
                        if isinstance(detail_response.get('dados'), dict)
                        else {}
                    )
                    detalhes = self._extract_detalhes_mensagem(detail_dados)
                    logger.info(
                        '[CAIXA_POSTAL][DETALHE] empresa_id=%s cnpj=%s isn=%s '
                        'total_extraido=%s dados=%s raw=%s',
                        company.id,
                        company.cnpj,
                        mensagem.isn,
                        len(detalhes),
                        json.dumps(detail_dados, ensure_ascii=False)[:3000],
                        json.dumps(detail_response, ensure_ascii=False)[:5000],
                    )

                    if detalhes:
                        self._salvar_recuperacao_detalhe(
                            company, mensagem, detail_response, detalhes)
                        self._salvar_detalhe(mensagem, detalhes[0])
                        db.session.commit()
                        self._remover_recuperacao_detalhe(company, mensagem.isn)
                        detalhes_baixados += 1
                except Exception:
                    db.session.rollback()
                    detalhes_erros += 1
                    logger.exception(
                        '[CAIXA_POSTAL][DETALHE] Erro ao baixar/gravar detalhe '
                        'empresa_id=%s cnpj=%s isn=%s',
                        company.id, company.cnpj, mensagem.isn,
                    )

        monitor = CaixaPostalMonitoramento.query.filter_by(company_id=company.id).first()
        if not monitor:
            monitor = CaixaPostalMonitoramento(
                company_id=company.id, created_at=datetime.utcnow())

        detalhes_pendentes = CaixaPostalMensagem.query.filter_by(
            company_id=company.id,
            detalhe_baixado=False,
        ).count()
        baixa_concluida = detalhes_erros == 0 and detalhes_pendentes == 0
        monitor.mensagens_baixadas = baixa_concluida
        monitor.possui_mensagens_novas = not baixa_concluida
        if baixa_concluida:
            monitor.indicador_mensagens_novas = 0
        monitor.ultima_baixa_mensagens = datetime.utcnow()
        monitor.updated_at = datetime.utcnow()
        db.session.add(monitor)
        db.session.commit()

        return {
            "success": True,
            "message": (
                f"{len(saved)} mensagem(ns) gravada(s), "
                f"{detalhes_baixados} detalhe(s) baixado(s)."
                + (f" {detalhes_pulados} já estavam em dia (não cobrados)."
                   if detalhes_pulados else "")
            ),
            "total_mensagens": len(saved),
            "total_detalhes": detalhes_baixados,
            "total_erros_detalhes": detalhes_erros,
            "total_detalhes_pendentes": detalhes_pendentes,
            # chave aditiva do DESVIO INTENCIONAL (5o) — o frontend ignora o que não conhece
            "total_detalhes_pulados": detalhes_pulados,
        }
