import base64
import json
import pycurl
from io import BytesIO
import tempfile
import os
import uuid
from contextlib import contextmanager
from requests_pkcs12 import post
import logging
import re
import time
import certifi

from app.services.serpro_logging import (
    log_serpro_request,
    log_serpro_response,
    log_serpro_exception,
)

logger = logging.getLogger(__name__)


class SerproService:

    def __init__(self, certificate_content, certificate_password, contratante_cnpj,
                 contador_cnpj, consumer_key, consumer_secret):
        self.certificate_content = certificate_content
        self.certificate_password = certificate_password
        self.contratante_cnpj = contratante_cnpj
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.request_id = str(uuid.uuid4())
        self.contador_cnpj = contador_cnpj

    @contextmanager
    def _temp_certificate(self):
        temp_dir = tempfile.mkdtemp(prefix=f'serpro_{self.request_id}_')
        temp_cert_path = os.path.join(temp_dir, 'cert.pfx')
        try:
            with open(temp_cert_path, 'wb') as temp_cert:
                temp_cert.write(self.certificate_content)
            yield temp_cert_path
        finally:
            try:
                if os.path.exists(temp_cert_path):
                    os.unlink(temp_cert_path)
                os.rmdir(temp_dir)
            except Exception as e:
                logger.error('Error cleaning up temporary certificate: %s', e)

    def _get_auth_token(self):
        try:
            with self._temp_certificate() as temp_cert_path:
                url = 'https://autenticacao.sapi.serpro.gov.br/authenticate'

                def converter_base64(credenciais):
                    return base64.b64encode(credenciais.encode('utf8')).decode('utf8')

                headers = {
                    'Authorization': 'Basic ' + converter_base64(
                        self.consumer_key + ':' + self.consumer_secret),
                    'role-type': 'TERCEIROS',
                    'content-type': 'application/x-www-form-urlencoded',
                }

                body = {'grant_type': 'client_credentials'}

                started_at = log_serpro_request('POST', url, headers=headers,
                                                payload=body, context='auth_token')
                try:
                    response = post(
                        url,
                        data=body,
                        headers=headers,
                        verify=True,
                        pkcs12_filename=temp_cert_path,
                        pkcs12_password=self.certificate_password,
                    )
                except Exception as exc:
                    log_serpro_exception(url, exc, started_at=started_at,
                                         context='auth_token')
                    raise

                log_serpro_response(url, response.status_code, response.text or '',
                                    headers=response.headers, started_at=started_at,
                                    context='auth_token')

                if response.status_code != 200:
                    raise Exception(
                        f'Authentication failed with status code: {response.status_code}')

                result = json.loads(response.content.decode('utf-8'))
                return {
                    'access_token': result['access_token'],
                    'jwt_token': result['jwt_token'],
                }
        except Exception as e:
            logger.error('Error getting auth token: %s', e)
            return None

    def get_auth_token(self):
        return self._get_auth_token()

    def _procurador_setting(self):
        try:
            from app.models import AppSetting
            from app.services.serpro_procurador_service import SerproProcuradorService

            setting = AppSetting.query.first()
            if SerproProcuradorService.enabled(setting):
                return setting
        except Exception:
            logger.exception('Erro ao verificar configuração de procurador PF')
        return None

    def request_protocol(self, cnpj, retried_auth=False):
        try:
            setting = self._procurador_setting()
            procurador_service = None
            if setting:
                from app.services.serpro_procurador_service import SerproProcuradorService
                procurador_service = SerproProcuradorService(setting)
                payload = procurador_service.build_payload(
                    contribuinte_numero=cnpj,
                    id_sistema='SITFIS',
                    id_servico='SOLICITARPROTOCOLO91',
                    versao_sistema='2.0',
                    dados='',
                )
                headers = procurador_service.pycurl_headers(
                    accept='text/plain', contribuinte_numero=cnpj)
            else:
                auth_tokens = self._get_auth_token()
                if not auth_tokens:
                    return {'success': False, 'error': 'Failed to get auth token'}

                tipo_contador = 1 if len(self.contador_cnpj) == 11 else 2

                payload = {
                    'contratante': {'numero': self.contratante_cnpj, 'tipo': 2},
                    'autorPedidoDados': {'numero': self.contador_cnpj,
                                         'tipo': tipo_contador},
                    'contribuinte': {'numero': cnpj, 'tipo': 2},
                    'pedidoDados': {
                        'idSistema': 'SITFIS',
                        'idServico': 'SOLICITARPROTOCOLO91',
                        'versaoSistema': '2.0',
                        'dados': '',
                    },
                }

                headers = [
                    f"jwt_token:{auth_tokens['jwt_token']}",
                    f"Authorization: Bearer {auth_tokens['access_token']}",
                    'Content-Type: application/json',
                    'accept: text/plain',
                ]

            buffer = BytesIO()
            headers_buffer = BytesIO()
            c = pycurl.Curl()
            url = 'https://gateway.apiserpro.serpro.gov.br/integra-contador/v1/Apoiar'
            c.setopt(c.URL, url)
            c.setopt(c.POSTFIELDS, json.dumps(payload))
            c.setopt(c.HTTPHEADER, headers)
            c.setopt(c.WRITEDATA, buffer)
            c.setopt(c.HEADERFUNCTION, headers_buffer.write)
            c.setopt(c.TIMEOUT, 30)
            c.setopt(c.SSLVERSION, pycurl.SSLVERSION_TLSv1_2)
            try:
                c.setopt(pycurl.CAINFO, certifi.where())
            except Exception:
                pass

            try:
                started_at = log_serpro_request('POST', url, headers=headers,
                                                payload=payload,
                                                context='request_protocol')
                c.perform()
                status_code = c.getinfo(pycurl.HTTP_CODE)
                response_text = buffer.getvalue().decode('utf-8')
                log_serpro_response(
                    url, status_code, response_text,
                    headers=headers_buffer.getvalue().decode('utf-8', errors='replace'),
                    started_at=started_at, context='request_protocol')

                if status_code in (401, 403) and procurador_service and not retried_auth:
                    procurador_service.invalidate_authorization_token()
                    return self.request_protocol(cnpj, retried_auth=True)

                if status_code == 304:
                    headers = headers_buffer.getvalue().decode('utf-8')
                    etag_match = re.search(r'etag:\s*"protocoloRelatorio:([^"]+)"',
                                           headers, re.IGNORECASE)
                    if etag_match:
                        return {'success': True, 'protocol': etag_match.group(1),
                                'wait_time': 30}
                    raise Exception('Protocol not found in ETag header')

                if status_code == 200:
                    response = json.loads(response_text)
                    if 'dados' in response:
                        dados = json.loads(response['dados'])
                        if 'protocoloRelatorio' in dados:
                            return {
                                'success': True,
                                'protocol': dados['protocoloRelatorio'],
                                'wait_time': dados.get('tempoEspera', 30),
                            }
                    raise Exception('Protocol not found in response body')

                raise Exception(
                    f'Protocol request failed with status code: {status_code}')
            finally:
                c.close()
        except Exception as e:
            logger.error('Error requesting protocol: %s', e)
            return {'success': False, 'error': str(e)}

    def get_report(self, cnpj, protocol, max_retries=2, retry_delay=60):
        for attempt in range(max_retries):
            try:
                setting = self._procurador_setting()
                procurador_service = None
                if setting:
                    from app.services.serpro_procurador_service import (
                        SerproProcuradorService)
                    procurador_service = SerproProcuradorService(setting)
                    dadospedido = procurador_service.build_payload(
                        contribuinte_numero=cnpj,
                        id_sistema='SITFIS',
                        id_servico='RELATORIOSITFIS92',
                        versao_sistema='2.0',
                        dados={'protocoloRelatorio': protocol},
                    )
                    headers = procurador_service.pycurl_headers(
                        accept='text/plain', contribuinte_numero=cnpj)
                else:
                    auth_tokens = self._get_auth_token()
                    if not auth_tokens:
                        return {'success': False, 'error': 'Failed to get auth token'}

                    tipo_contador = 1 if len(self.contador_cnpj) == 11 else 2

                    dadospedido = {
                        'contratante': {'numero': self.contratante_cnpj, 'tipo': 2},
                        'autorPedidoDados': {'numero': self.contador_cnpj,
                                             'tipo': tipo_contador},
                        'contribuinte': {'numero': cnpj, 'tipo': 2},
                        'pedidoDados': {
                            'idSistema': 'SITFIS',
                            'idServico': 'RELATORIOSITFIS92',
                            'versaoSistema': '2.0',
                            'dados': json.dumps({'protocoloRelatorio': protocol}),
                        },
                    }

                    headers = [
                        f"jwt_token:{auth_tokens['jwt_token']}",
                        f"Authorization: Bearer {auth_tokens['access_token']}",
                        'Content-Type: application/json',
                        'accept: text/plain',
                    ]

                buffer = BytesIO()
                c = pycurl.Curl()
                url = 'https://gateway.apiserpro.serpro.gov.br/integra-contador/v1/Emitir'
                c.setopt(c.URL, url)
                c.setopt(c.POSTFIELDS, json.dumps(dadospedido))
                c.setopt(c.HTTPHEADER, headers)
                c.setopt(c.WRITEDATA, buffer)
                c.setopt(c.SSLVERSION, pycurl.SSLVERSION_TLSv1_2)
                try:
                    c.setopt(pycurl.CAINFO, certifi.where())
                except Exception:
                    pass
                c.setopt(c.TIMEOUT, 300)

                try:
                    started_at = log_serpro_request(
                        'POST', url, headers=headers, payload=dadospedido,
                        context=f'get_report_attempt_{attempt + 1}')
                    c.perform()
                    status_code = c.getinfo(pycurl.HTTP_CODE)
                    response_text = buffer.getvalue().decode('utf-8', errors='replace')
                    log_serpro_response(
                        url, status_code, response_text, started_at=started_at,
                        context=f'get_report_attempt_{attempt + 1}')

                    if status_code in (401, 403) and procurador_service:
                        procurador_service.invalidate_authorization_token()
                        continue

                    if status_code == 204:
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                        return {'success': False,
                                'error': 'Report not ready after maximum retries'}

                    if status_code != 200:
                        return {'success': False,
                                'error': f'Request failed with status code: {status_code}'}

                    resultadopdf = json.loads(response_text)
                    dados_array = json.loads(resultadopdf['dados'])
                    dados = (dados_array[0]
                             if isinstance(dados_array, list) and dados_array
                             else dados_array)

                    if 'pdf' not in dados:
                        raise ValueError('PDF data not found in response')

                    return {'success': True, 'pdf_base64': dados['pdf']}
                finally:
                    c.close()
            except Exception as e:
                logger.error('Error getting report (attempt %s/%s): %s',
                             attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                return {'success': False, 'error': str(e)}

        return {'success': False, 'error': 'Maximum retries exceeded'}
