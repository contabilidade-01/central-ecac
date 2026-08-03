"""Catálogo de rotinas e verificação de permissão por rotina e por empresa.

⚠️ DESVIO INTENCIONAL (13o) — NÃO existe no exe: lá havia um único operador, dono da
máquina. Publicado numa VPS com mais de um usuário, é preciso limitar **o que** cada um
abre e **de quais empresas** enxerga dado fiscal.

Onde é aplicado
---------------
Tudo passa por um único `before_request` em `app/security.py`. Nenhuma rota do exe foi
alterada — a regra fica fora delas, então a fidelidade ao bytecode continua intacta.

Como a rotina é descoberta
--------------------------
Pelo prefixo da URL, do mais específico para o mais genérico: `/api/das/dctfweb` casa com
`dctfweb` antes de `/api/das` casar com `das`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

TODAS = 'todas'

# chave -> (nome na tela, prefixos de URL, só admin?)
ROTINAS: Dict[str, Dict[str, Any]] = {
    'dashboard': {
        'nome': 'Dashboard',
        'prefixos': ['/api/dashboard', '/api/reports'],
        'admin': False,
    },
    'configuracoes': {
        'nome': 'Configurações',
        'prefixos': ['/api/settings'],
        'admin': True,   # contém credenciais da SERPRO e senha do certificado
    },
    'pagamentos': {
        'nome': 'Pagamentos de Tributos',
        'prefixos': ['/api/pagamentos'],
        'admin': False,
    },
    'dctfweb': {
        'nome': 'DCTFWeb Lote',
        'prefixos': ['/api/das/dctfweb'],
        'admin': False,
    },
    'das': {
        'nome': 'DAS Lote',
        'prefixos': ['/api/das'],
        'admin': False,
    },
    'caixa_postal': {
        'nome': 'Caixa Postal',
        'prefixos': ['/api/caixa-postal'],
        'admin': False,
    },
    'parcelamentos': {
        'nome': 'Parcelamentos',
        'prefixos': ['/api/parcelamentos'],
        'admin': False,
    },
    'custos_api': {
        'nome': 'Custos API',
        'prefixos': ['/api/api-costs'],
        'admin': False,
    },
    'procuracoes': {
        'nome': 'Procurações',
        'prefixos': ['/procuracoes', '/api/procuracoes'],
        'admin': False,
    },
    'agendamento': {
        'nome': 'Agendamento',
        'prefixos': ['/agendamento', '/api/agendamento'],
        'admin': True,   # liga rotina automática que gasta dinheiro
    },
    'restaurar': {
        'nome': 'Restaurar dados',
        'prefixos': ['/restaurar', '/api/restaurar'],
        'admin': True,   # troca o banco inteiro
    },
}

# Sempre liberado para quem está logado: são a base de qualquer tela.
# `/api/companies` é liberado, mas o CONTEÚDO é filtrado pelas empresas do usuário.
LIVRES_LOGADO = (
    '/api/companies',
    '/api/me',
    '/api/license',
    '/config.json',
)

# Exclusivo de administrador.
PREFIXOS_ADMIN = ('/usuarios', '/api/usuarios')

# Pares (prefixo, chave) ordenados do mais específico para o mais genérico.
_PREFIXOS = sorted(
    ((p, chave) for chave, meta in ROTINAS.items() for p in meta['prefixos']),
    key=lambda par: len(par[0]),
    reverse=True,
)


def catalogo(inclui_admin: bool = True) -> List[Dict[str, Any]]:
    """Lista para montar a tela de permissões."""
    return [
        {'chave': chave, 'nome': meta['nome'], 'admin': meta['admin']}
        for chave, meta in ROTINAS.items()
        if inclui_admin or not meta['admin']
    ]


def rotina_da_rota(path: str) -> Optional[str]:
    for prefixo, chave in _PREFIXOS:
        if path == prefixo or path.startswith(prefixo + '/'):
            return chave
    return None


def e_admin(usuario: Dict[str, Any]) -> bool:
    return (usuario or {}).get('papel') == 'admin'


def pode_rotina(usuario: Dict[str, Any], chave: str) -> bool:
    if not chave:
        return True
    if e_admin(usuario):
        return True
    if ROTINAS.get(chave, {}).get('admin'):
        return False
    permitidas = (usuario or {}).get('rotinas', TODAS)
    if permitidas == TODAS:
        return True
    return chave in (permitidas or [])


def rotinas_do_usuario(usuario: Dict[str, Any]) -> List[str]:
    """Chaves que este usuário realmente pode abrir."""
    return [chave for chave in ROTINAS if pode_rotina(usuario, chave)]


def empresas_do_usuario(usuario: Dict[str, Any]):
    if e_admin(usuario):
        return TODAS
    return (usuario or {}).get('empresas', TODAS)


def pode_empresa(usuario: Dict[str, Any], company_id) -> bool:
    permitidas = empresas_do_usuario(usuario)
    if permitidas == TODAS:
        return True
    try:
        return int(company_id) in {int(e) for e in permitidas}
    except (TypeError, ValueError):
        return False


def _id_da_empresa(item: Dict[str, Any]):
    """Descobre a que empresa a linha pertence — ou None se não for linha de empresa.

    Duas formas aparecem na API: linhas de outras tabelas trazem `company_id`, e a
    própria lista de empresas traz `id` (com `cnpj`/`razao_social` ao lado).
    """
    if item.get('company_id') is not None:
        return item['company_id']
    if 'cnpj' in item or 'razao_social' in item:
        return item.get('id')
    return None


def filtrar_empresas(usuario: Dict[str, Any], itens: List[Any]):
    """Remove da lista as empresas que o usuário não pode ver.

    O que **não** é linha de empresa passa intacto: filtrar por engano é tão ruim
    quanto vazar — foi o que aconteceu com a lista de IDs do `/api/me`.
    """
    if empresas_do_usuario(usuario) == TODAS:
        return itens

    saida = []
    for item in itens:
        if not isinstance(item, dict):
            saida.append(item)
            continue
        company_id = _id_da_empresa(item)
        if company_id is None or pode_empresa(usuario, company_id):
            saida.append(item)
    return saida
