from pathlib import Path

from flask import Blueprint, jsonify, request, current_app

from app.extensions import db
from app.models import AppSetting
from werkzeug.utils import secure_filename
from flask import send_file

settings_bp = Blueprint('settings', __name__)


@settings_bp.get('/logo')
def get_logo():
    settings = AppSetting.query.first()
    if not settings or not settings.office_logo_path:
        return jsonify({'success': False, 'message': 'Logo não encontrada'}), 404

    path = Path(settings.office_logo_path)
    if not path.exists():
        return jsonify({'success': False,
                        'message': 'Arquivo de logo não encontrado'}), 404

    return send_file(path)


@settings_bp.get('')
def get_settings():
    setting = AppSetting.query.first()
    if not setting:
        return jsonify({
            'contatorConfigured': False,
            'contador_cnpj': '',
            'certificado_path': '',
            'certificado_password': '',
            'serpro_consumer_key': '',
            'serpro_consumer_secret': '',
            'procurador_pf_habilitado': False,
            'procurador_cpf': '',
            'procurador_nome': '',
            'procurador_certificado_path': '',
            'procurador_certificado_password': '',
            'office_name': '',
            'office_logo_url': None,
        })

    office_logo_url = None
    if setting.office_logo_path:
        office_logo_url = '/api/settings/logo'

    return jsonify({
        'contatorConfigured': True,
        'contador_cnpj': setting.contador_cnpj or '',
        'certificado_path': setting.certificado_path or '',
        'certificado_password': setting.certificado_password or '',
        'serpro_consumer_key': setting.serpro_consumer_key or '',
        'serpro_consumer_secret': setting.serpro_consumer_secret or '',
        'procurador_pf_habilitado': bool(setting.procurador_pf_habilitado),
        'procurador_cpf': setting.procurador_cpf or '',
        'procurador_nome': setting.procurador_nome or '',
        'procurador_certificado_path': setting.procurador_certificado_path or '',
        'procurador_certificado_password': setting.procurador_certificado_password or '',
        'office_name': setting.office_name or '',
        'office_logo_url': office_logo_url,
    })


@settings_bp.post('')
def save_settings():
    data = request.form or request.json or {}
    file = request.files.get('certificado')
    procurador_file = request.files.get('procurador_certificado')

    setting = AppSetting.query.first() or AppSetting()

    old_procurador_config = (
        bool(setting.procurador_pf_habilitado),
        setting.procurador_cpf or '',
        setting.procurador_certificado_path or '',
        setting.procurador_certificado_password or '',
    )

    setting.contador_cnpj = only_digits(data.get('contador_cnpj', ''))
    setting.certificado_password = data.get('certificado_password', '')
    setting.serpro_consumer_key = data.get('serpro_consumer_key', '')
    setting.serpro_consumer_secret = data.get('serpro_consumer_secret', '')
    setting.office_name = data.get('office_name', '').strip()
    setting.procurador_pf_habilitado = str(
        data.get('procurador_pf_habilitado', '')).lower() in ('1', 'true', 'on', 'yes')
    setting.procurador_cpf = only_digits(data.get('procurador_cpf', ''))
    setting.procurador_nome = data.get('procurador_nome', '').strip()
    setting.procurador_certificado_password = data.get(
        'procurador_certificado_password', '')

    logo_file = request.files.get('office_logo')
    if logo_file and logo_file.filename:
        branding_dir = Path(current_app.config['DATA_DIR']) / 'branding'
        branding_dir.mkdir(parents=True, exist_ok=True)

        ext = Path(secure_filename(logo_file.filename)).suffix.lower() or '.png'
        logo_path = branding_dir / f'office_logo{ext}'
        logo_file.save(logo_path)
        setting.office_logo_path = str(logo_path)

    if file:
        cert_dir = Path(current_app.config['CERTS_DIR'])
        cert_path = cert_dir / 'contador_certificado.pfx'
        file.save(cert_path)
        setting.certificado_path = str(cert_path)

    if procurador_file:
        cert_dir = Path(current_app.config['CERTS_DIR'])
        cert_path = cert_dir / 'procurador_pf_certificado.pfx'
        procurador_file.save(cert_path)
        setting.procurador_certificado_path = str(cert_path)

    new_procurador_config = (
        bool(setting.procurador_pf_habilitado),
        setting.procurador_cpf or '',
        setting.procurador_certificado_path or '',
        setting.procurador_certificado_password or '',
    )

    if old_procurador_config != new_procurador_config:
        setting.procurador_token = None
        setting.procurador_token_expires_at = None
        setting.procurador_token_raw_expires = None
        setting.procurador_token_response_json = None

    db.session.add(setting)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Configurações salvas com sucesso'})


def only_digits(value: str) -> str:
    return ''.join(ch for ch in str(value) if ch.isdigit())
