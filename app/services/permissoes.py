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


# --------------------------------------------------------------------- menu
#
# Fonte única do menu lateral (14o desvio). As telas do Flask montam a barra a partir
# daqui (`app/ui.py`), e a SPA compilada recebe a mesma estrutura já filtrada em
# `/api/me` — assim as duas nunca divergem.
#
# `spa`  = rótulo do botão original do bundle; o item da barra CLICA nesse botão, que
#          é o único jeito de trocar de aba sem ter o fonte do React.
# `url`  = tela servida pelo Flask (navegação normal).
# `chave` = rotina para a permissão; `__admin__` aparece só para administrador.

ICONES = {
    'dashboard':     'M3 3h7v7H3zM14 3h7v4h-7zM14 10h7v11h-7zM3 13h7v8H3z',
    'caixa_postal':  'M3 5h18v14H3zM3 5l9 7 9-7',
    'parcelamentos': 'M12 8v4l3 2M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    'pagamentos':    'M3 6h18v12H3zM3 10h18M7 15h4',
    'dctfweb':       'M8 3h9l4 4v14H8zM17 3v4h4M4 7v14h9',
    'das':           'M6 2h9l5 5v15H6zM15 2v5h5M9 13h8M9 17h5',
    'custos_api':    'M12 3a9 9 0 109 9h-9z M12 3v9h9',
    'configuracoes': 'M12 15a3 3 0 100-6 3 3 0 000 6zM19 12a7 7 0 00-.1-1l2-1.6-2-3.4-2.4 1a7 7 0 00-1.7-1L14.5 3h-5l-.3 2.6a7 7 0 00-1.7 1l-2.4-1-2 3.4 2 1.6a7 7 0 000 2l-2 1.6 2 3.4 2.4-1a7 7 0 001.7 1l.3 2.4h5l.3-2.4a7 7 0 001.7-1l2.4 1 2-3.4-2-1.6c.1-.3.1-.7.1-1z',
    'procuracoes':   'M9 12l2 2 4-4M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z',
    'agendamento':   'M8 2v4M16 2v4M3 10h18M5 6h14v15H5zM12 14v3',
    'restaurar':     'M21 12a9 9 0 11-3-6.7M21 3v6h-6',
    '__admin__':     'M17 20v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9.5 6.5a3.5 3.5 0 11-7 0 3.5 3.5 0 017 0zM22 20v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8',
    'sair':          'M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9',
}

MENU: List = [
    ('Painel', [
        {'chave': 'dashboard',     'rotulo': 'Dashboard',     'spa': 'Dashboard'},
        {'chave': 'caixa_postal',  'rotulo': 'Caixa Postal',  'spa': 'Caixa Postal'},
        {'chave': 'parcelamentos', 'rotulo': 'Parcelamentos', 'spa': 'Parcelamentos'},
    ]),
    ('Rotinas', [
        {'chave': 'pagamentos', 'rotulo': 'Pagamentos de Tributos',
         'spa': 'Pagamentos de Tributos'},
        {'chave': 'dctfweb', 'rotulo': 'DCTFWeb Lote', 'spa': 'DCTFWeb Lote'},
        {'chave': 'das',     'rotulo': 'DAS Lote',     'spa': 'DAS Lote'},
    ]),
    ('Administração', [
        {'chave': 'procuracoes', 'rotulo': 'Procurações',       'url': '/procuracoes'},
        {'chave': 'agendamento', 'rotulo': 'Agendamento',       'url': '/agendamento'},
        {'chave': '__admin__',   'rotulo': 'Usuários e acessos', 'url': '/usuarios'},
        {'chave': 'restaurar',   'rotulo': 'Restaurar dados',   'url': '/restaurar'},
    ]),
    ('Sistema', [
        {'chave': 'configuracoes', 'rotulo': 'Configurações', 'spa': 'Configurações'},
        {'chave': 'custos_api',    'rotulo': 'Custos API',    'spa': 'Custos API'},
    ]),
]


def menu_do_usuario(usuario: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Menu já filtrado pelo que este usuário pode abrir."""
    saida = []
    for titulo, itens in MENU:
        liberados = []
        for item in itens:
            chave = item['chave']
            if chave == '__admin__':
                if not e_admin(usuario):
                    continue
            elif not pode_rotina(usuario, chave):
                continue
            liberados.append({**item, 'icone': ICONES.get(chave, '')})
        if liberados:
            saida.append({'titulo': titulo, 'itens': liberados})
    return saida


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
