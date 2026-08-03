import json
import logging
import time
from typing import Any

import requests


logger = logging.getLogger('serpro.http')

MAX_LOG_CHARS = 8000
MAX_STRING_CHARS = 1200

SENSITIVE_KEYS = {
    'authorization',
    'jwt_token',
    'access_token',
    'token',
    'autenticar_procurador_token',
    'password',
    'senha',
    'certificate_password',
    'certificado_password',
    'procurador_certificado_password',
    'consumer_key',
    'consumer_secret',
    'xml',
}

LARGE_CONTENT_KEYS = {
    'pdf',
    'pdf_base64',
    'base64',
    'arquivo',
    'pdfbytearraybase64',
    'docarrecadacaopdfb64',
}


def _mask(value: Any) -> str:
    text = str(value or '')
    if not text:
        return '***'
    return f'***{text[-6:]}' if len(text) > 6 else '***'


def _sanitize(value: Any, key: str = '') -> Any:
    key_lower = str(key or '').lower()
    if key_lower in SENSITIVE_KEYS or any(
            part in key_lower for part in ('password', 'senha', 'secret', 'token')):
        return _mask(value)

    if isinstance(value, dict):
        return {item_key: _sanitize(item_value, item_key)
                for item_key, item_value in value.items()}

    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]

    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')

    if isinstance(value, str):
        if key_lower in LARGE_CONTENT_KEYS or any(
                part in key_lower for part in ('pdf', 'base64')):
            return f'<conteudo grande omitido: {len(value)} chars>'
        if len(value) > MAX_STRING_CHARS:
            return f'{value[:MAX_STRING_CHARS]}... <truncado: {len(value)} chars>'

    return value


def _headers_to_dict(headers: Any) -> dict[str, Any]:
    if not headers:
        return {}
    if isinstance(headers, dict):
        return dict(headers)
    result = {}
    for item in headers:
        text = str(item)
        if ':' in text:
            key, value = text.split(':', 1)
            result[key.strip()] = value.strip()
        else:
            result[text] = ''
    return result


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text


def _format(value: Any) -> str:
    safe = _sanitize(value)
    try:
        text = json.dumps(safe, ensure_ascii=False, default=str)
    except Exception:
        text = str(safe)

    if len(text) > MAX_LOG_CHARS:
        return f'{text[:MAX_LOG_CHARS]}... <log truncado: {len(text)} chars>'
    return text


def log_serpro_request(method: str, url: str, headers: Any = None,
                       payload: Any = None, context: str = '') -> float:
    logger.info(
        '[SERPRO][REQUEST] %s %s context=%s headers=%s payload=%s',
        method,
        url,
        context or '-',
        _format(_headers_to_dict(headers)),
        _format(payload),
    )
    return time.perf_counter()


def log_serpro_response(url: str, status_code: Any = None, body: Any = None,
                        headers: Any = None, started_at: float | None = None,
                        context: str = ''):
    elapsed_ms = int((time.perf_counter() - started_at) * 1000) if started_at else None
    body_value = _try_json(body) if isinstance(body, str) else body
    logger.info(
        '[SERPRO][RESPONSE] %s status=%s elapsed_ms=%s context=%s headers=%s body=%s',
        url,
        status_code,
        elapsed_ms if elapsed_ms is not None else '-',
        context or '-',
        _format(_headers_to_dict(headers)),
        _format(body_value),
    )


def log_serpro_exception(url: str, exc: Exception, started_at: float | None = None,
                         context: str = ''):
    elapsed_ms = int((time.perf_counter() - started_at) * 1000) if started_at else None
    logger.exception(
        '[SERPRO][ERROR] %s elapsed_ms=%s context=%s error=%s',
        url,
        elapsed_ms if elapsed_ms is not None else '-',
        context or '-',
        exc,
    )


def serpro_post(url: str, headers: dict[str, Any] | None = None, json_payload: Any = None,
                context: str = '', **kwargs) -> requests.Response:
    started_at = log_serpro_request('POST', url, headers=headers,
                                    payload=json_payload, context=context)
    try:
        response = requests.post(url, headers=headers, json=json_payload, **kwargs)
    except Exception as exc:
        log_serpro_exception(url, exc, started_at=started_at, context=context)
        raise

    log_serpro_response(url, response.status_code, response.text or '',
                        headers=response.headers, started_at=started_at, context=context)
    return response
