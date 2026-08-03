from flask import Blueprint, jsonify

license_bp = Blueprint('license', __name__)


@license_bp.get('/status')
def license_status():
    return jsonify({
        'allowed': True,
        'message': 'Licenca dispensada.',
    }), 200
