import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.models import (
    AppSetting,
    Company,
    RelatorioSitFiscal,
    PendenciaRelatorio,
    DebitoRelatorio,
)
from app.services.pdf_parser import PDFParser
from app.services.serpro_service import SerproService
import time
from app.services.api_usage_service import ApiUsageService


def update_company_progress(
    company_id: int,
    *,
    status: str | None = None,
    step: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    finished: bool = False,
):
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

    if status == 'processing' and not company.processing_started_at:
        company.processing_started_at = datetime.now()

    if finished:
        company.last_processed_at = datetime.now()

    db.session.commit()


@dataclass
class ProcessResult:
    success: bool
    message: str
    company_id: int | None = None


class ReportService:

    def __init__(self):
        self.parser = PDFParser()

    def _get_setting(self) -> AppSetting:
        setting = AppSetting.query.first()
        if not setting:
            raise ValueError('Configuração do contador não encontrada')
        required = [
            setting.contador_cnpj,
            setting.certificado_path,
            setting.certificado_password,
            setting.serpro_consumer_key,
            setting.serpro_consumer_secret,
        ]
        if not all(required):
            raise ValueError('Configuração incompleta. Preencha CNPJ, certificado, '
                             'senha e keys da Serpro.')
        return setting

    def _load_certificate(self, certificado_path: str) -> bytes:
        with open(self._resolver_certificado(certificado_path), 'rb') as f:
            return f.read()

    @staticmethod
    def _resolver_certificado(certificado_path: str) -> str:
        """Resolve o caminho do .pfx tolerando troca de máquina.

        DESVIO INTENCIONAL do exe: `settings.certificado_path` é absoluto e contém o
        nome do usuário do Windows, então quebra ao abrir o banco compartilhado na
        outra máquina. Se o caminho gravado não existir, procura o mesmo arquivo
        (e depois o nome padrão) dentro do CERTS_DIR desta máquina.
        """
        # A lógica vive em `services/certificado.py` desde 03/08/2026, porque os
        # demais módulos (caixa postal, parcelamentos, DAS, pagamentos) liam o caminho
        # direto e quebravam no servidor.
        from app.services import certificado as _cert
        return _cert.resolver(certificado_path)

    def _tipo_from_key(self, key: str) -> str:
        if key.startswith('debitosSN'):
            return 'SN'
        if key.startswith('debitosINSS'):
            return 'INSS'
        if key.startswith('debitosMAED'):
            return 'MAED'
        if key.startswith('debitosIRRF'):
            return 'IRRF'
        return key.replace('debitos', '')

    def process_company(self, company_id: int) -> ProcessResult:
        company = db.session.get(Company, company_id)
        if not company:
            return ProcessResult(False, 'Empresa não encontrada', company_id)

        now = datetime.now()

        # Já existe um protocolo em espera para esta empresa
        if company.processing_status == 'processing' and company.current_protocol:
            wait_until = company.protocol_wait_until
            if wait_until and now < wait_until:
                remaining = int((wait_until - now).total_seconds())
                update_company_progress(
                    company_id,
                    status='processing',
                    step='Aguardando liberação do relatório',
                    progress=50,
                    message=f'Relatório já solicitado. Aguarde cerca de {remaining} segundos.',
                )
                return ProcessResult(
                    False,
                    f'Relatório já solicitado. Aguarde cerca de {remaining} segundos.',
                    company_id,
                )

        try:
            update_company_progress(
                company_id,
                status='processing',
                step='Iniciando',
                progress=5,
                message=None,
            )

            setting = self._get_setting()

            update_company_progress(
                company_id,
                status='processing',
                step='Carregando certificado',
                progress=10,
                message=None,
            )

            cert_content = self._load_certificate(setting.certificado_path)

            update_company_progress(
                company_id,
                status='processing',
                step='Autenticando na API',
                progress=20,
                message=None,
            )

            serpro = SerproService(
                certificate_content=cert_content,
                certificate_password=setting.certificado_password,
                contratante_cnpj=setting.contador_cnpj,
                contador_cnpj=setting.contador_cnpj,
                consumer_key=setting.serpro_consumer_key,
                consumer_secret=setting.serpro_consumer_secret,
            )

            protocol = company.current_protocol

            if not protocol:
                update_company_progress(
                    company_id,
                    status='processing',
                    step='Solicitando protocolo',
                    progress=40,
                    message=None,
                )

                protocol_response = serpro.request_protocol(company.cnpj)
                if not protocol_response['success']:
                    update_company_progress(
                        company_id,
                        status='error',
                        step='Erro ao solicitar protocolo',
                        progress=0,
                        message=f"Erro ao solicitar protocolo: {protocol_response.get('error')}",
                    )
                    return ProcessResult(
                        False,
                        f"Erro ao solicitar protocolo: {protocol_response.get('error')}",
                        company_id,
                    )

                protocol = protocol_response['protocol']
                wait_time_ms = int(protocol_response.get('wait_time', 30000) or 30000)
                wait_time_seconds = wait_time_ms / 1000

                company.current_protocol = protocol
                company.protocol_requested_at = now
                company.protocol_wait_until = now + timedelta(seconds=wait_time_seconds)
                db.session.commit()

                update_company_progress(
                    company_id,
                    status='processing',
                    step='Aguardando liberação do relatório',
                    progress=50,
                    message=f'Protocolo gerado com sucesso. Aguardando cerca de '
                            f'{wait_time_seconds} segundos antes da captura.',
                )

                time.sleep(wait_time_seconds)

                company = db.session.get(Company, company_id)
                protocol = company.current_protocol

            elif company.protocol_wait_until and now < company.protocol_wait_until:
                remaining = int((company.protocol_wait_until - now).total_seconds())

                update_company_progress(
                    company_id,
                    status='processing',
                    step='Aguardando liberação do relatório',
                    progress=50,
                    message=f'Relatório já solicitado. Aguarde cerca de {remaining} segundos.',
                )

                time.sleep(remaining)

                company = db.session.get(Company, company_id)
                protocol = company.current_protocol

            update_company_progress(
                company_id,
                status='processing',
                step='Capturando relatório',
                progress=60,
                message=None,
            )

            report_response = serpro.get_report(company.cnpj, protocol)

            if not report_response['success']:
                error_msg = str(report_response.get('error', ''))

                if ('202' in error_msg or '204' in error_msg
                        or 'not ready' in error_msg.lower()):
                    update_company_progress(
                        company_id,
                        status='processing',
                        step='Relatório ainda em processamento na Serpro',
                        progress=65,
                        message='O relatório ainda não está pronto. '
                                'Tente novamente em instantes.',
                    )
                    return ProcessResult(
                        False,
                        'Relatório ainda não está pronto. Tente novamente em instantes.',
                        company_id,
                    )

                update_company_progress(
                    company_id,
                    status='error',
                    step='Erro ao obter relatório',
                    progress=0,
                    message=f'Erro ao obter relatório: {error_msg}',
                )
                return ProcessResult(
                    False,
                    f'Erro ao obter relatório: {error_msg}',
                    company_id,
                )

            update_company_progress(
                company_id,
                status='processing',
                step='Salvando PDF',
                progress=75,
                message=None,
            )

            ApiUsageService.register_usage(
                route_type='emitir',
                endpoint='SITFISCAL_REPORT',
                company_id=company.id,
            )

            pdf_content = base64.b64decode(report_response['pdf_base64'])
            report_dir = Path(current_app.config['REPORTS_DIR']) / company.cnpj
            report_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = report_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            pdf_path.write_bytes(pdf_content)

            update_company_progress(
                company_id,
                status='processing',
                step='Lendo relatório PDF',
                progress=85,
                message=None,
            )

            parsed = self.parser.parse_pdf_content(pdf_content)

            update_company_progress(
                company_id,
                status='processing',
                step='Salvando dados',
                progress=95,
                message=None,
            )

            relatorio = RelatorioSitFiscal(
                company_id=company.id,
                data_hora=datetime.strptime(
                    parsed.get('datahora',
                               datetime.now().strftime('%d/%m/%Y %H:%M:%S')),
                    '%d/%m/%Y %H:%M:%S',
                ),
                situacao=parsed.get('situacao', ''),
                natureza_juridica_codigo=parsed.get('NaturezaJuridica', {}).get('codigo', ''),
                natureza_juridica_descricao=parsed.get('NaturezaJuridica', {}).get('natureza', ''),
                simples_nacional_inclusao=self._parse_date(
                    parsed.get('simples_nacional', {}).get('inclusao')),
                simples_nacional_exclusao=self._parse_date(
                    parsed.get('simples_nacional', {}).get('exclusao')),
                simei_inclusao=self._parse_date(parsed.get('SIMEI', {}).get('inclusao')),
                simei_exclusao=self._parse_date(parsed.get('SIMEI', {}).get('exclusao')),
                endereco=parsed.get('endereco', ''),
                responsavel_cpf=parsed.get('responsavel', {}).get('CPF', ''),
                responsavel_nome=parsed.get('responsavel', {}).get('nome', ''),
                pdf_local_path=str(pdf_path),
            )
            db.session.add(relatorio)
            db.session.flush()

            for tipo, pendencias in parsed.get('pendencias', {}).items():
                for pendencia in pendencias:
                    db.session.add(PendenciaRelatorio(
                        relatorio_id=relatorio.id,
                        tipo=tipo,
                        ano=pendencia.get('ano', ''),
                        meses_json=pendencia.get('meses', []),
                    ))

            self._gravar_pendencias_parcelamento_pgfn(relatorio, parsed)

            for key, debitos in parsed.items():
                if not key.startswith('debitos'):
                    continue
                if not isinstance(debitos, list):
                    continue
                for debito in debitos:
                    db.session.add(DebitoRelatorio(
                        relatorio_id=relatorio.id,
                        tipo=self._tipo_from_key(key),
                        receita=debito.get('receita', ''),
                        periodo_apuracao=debito.get('periodo_apuracao', ''),
                        data_vencimento=self._parse_date(debito.get('data_vencimento')),
                        valor_original=Decimal(str(debito.get('valor_original', 0))),
                        saldo_devedor=Decimal(str(debito.get('saldo_devedor', 0))),
                        multa=Decimal(str(debito.get('multa', 0))),
                        juros=Decimal(str(debito.get('juros', 0))),
                        saldo_devedor_total=Decimal(
                            str(debito.get('saldo_devedor_total', 0))),
                        situacao=debito.get('situacao', ''),
                    ))

            company.current_protocol = None
            company.protocol_requested_at = None
            company.protocol_wait_until = None
            company.last_processed_at = datetime.now()

            db.session.commit()

            update_company_progress(
                company_id,
                status='success',
                step='Concluído',
                progress=100,
                message='Relatório processado com sucesso',
            )

            return ProcessResult(True, 'Relatório processado com sucesso', company.id)

        except Exception as e:
            db.session.rollback()

            update_company_progress(
                company_id,
                status='error',
                step='Erro',
                progress=0,
                message=str(e),
            )

            return ProcessResult(False, str(e), company_id)

    def process_all_background(self):
        companies = Company.query.filter_by(ativo=True).order_by(
            Company.razao_social.asc()).all()

        for company in companies:
            try:
                company = db.session.get(Company, company.id)

                if not company or not company.ativo:
                    continue

                if company.processing_status == 'processing':
                    continue

                self.process_company(company.id)
            except Exception as e:
                db.session.rollback()
                update_company_progress(
                    company.id,
                    status='error',
                    step='Erro',
                    progress=0,
                    message=str(e),
                )

    def process_all(self):
        results = []
        companies = Company.query.filter_by(ativo=True).order_by(
            Company.razao_social.asc()).all()
        for company in companies:
            results.append(self.process_company(company.id).__dict__)
        return results

    @staticmethod
    def _gravar_pendencias_parcelamento_pgfn(relatorio, parsed):
        """DESVIO INTENCIONAL do exe (pedido do Jean, 31/07/2026).

        O exe só registra pendência de *omissão de declaração*, então parcelamento ativo
        e inscrição na PGFN ficavam invisíveis no painel. A API da SERPRO não cobre a
        PGFN — esse dado só existe no texto do PDF.

        Grava no máximo 2 pendências: uma da RFB (parcelamento) e uma da PGFN.
        """
        itens = parsed.get('parcelamentos_pgfn') or []
        if not itens:
            return

        ano = str(relatorio.data_hora.year) if relatorio.data_hora else ''

        rfb, pgfn = [], []

        for item in itens:
            if item.get('tipo') == 'PARCELAMENTO_ATIVO':
                rfb.append(item['descricao'])
                if item.get('parcelas_em_atraso'):
                    n = item['parcelas_em_atraso']
                    rfb.append(f'{n} parcela(s) em atraso')
            elif item.get('tipo') == 'PARCELAMENTO_VALOR':
                rfb.append(item['descricao'])
                if item.get('valor') is not None:
                    rfb.append(f"Valor suspenso: R$ {item['valor']:,.2f}"
                               .replace(',', 'X').replace('.', ',').replace('X', '.'))
            elif item.get('tipo') == 'INSCRICAO_PGFN':
                pgfn.append(item['descricao'])
                if item.get('situacao'):
                    pgfn.append(item['situacao'])

        if rfb:
            db.session.add(PendenciaRelatorio(
                relatorio_id=relatorio.id,
                tipo='PARCELAMENTO',
                ano=ano,
                meses_json=rfb,
            ))
        if pgfn:
            db.session.add(PendenciaRelatorio(
                relatorio_id=relatorio.id,
                tipo='PGFN',
                ano=ano,
                meses_json=pgfn,
            ))

    @staticmethod
    def _parse_date(value):
        if not value:
            return None
        for fmt in ('%d/%m/%Y', '%m/%Y'):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def latest_report_subquery():
        return (
            db.session.query(
                RelatorioSitFiscal.company_id,
                func.max(RelatorioSitFiscal.id).label('max_id'),
            )
            .group_by(RelatorioSitFiscal.company_id)
            .subquery()
        )
