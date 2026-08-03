"""Usuários, permissões e convites de acesso.

⚠️ DESVIO INTENCIONAL (10o, ampliado em 03/08/2026) — NÃO existe no exe, que era desktop.

Guarda tudo em `<DATA_DIR>/instance/usuarios.json`, no mesmo padrão de
`procuracao_service` e `agendamento_service`: JSON ao lado do banco, dentro do volume.
**Nada é acrescentado a `app/models.py`** — a regra inviolável 3 continua de pé.

Modelo
------
* `admin` — enxerga tudo e administra os demais. Sempre `empresas="todas"` e
  `rotinas="todas"`; não há como se trancar para fora.
* `operador` — vê apenas as **rotinas** e as **empresas** que o admin liberar.

Senha e convites (padrão emprestado do portal `queijeiros`)
-----------------------------------------------------------
* A senha vive só como hash (`werkzeug`, scrypt).
* Usuário novo nasce **sem senha**: o admin gera um link de primeiro acesso e o entrega.
* Do convite guardamos o **sha256 do token**, nunca o token — quem lê o arquivo não
  consegue usar o link. Uso único, com validade, e um convite novo invalida o anterior.
* A mesma mecânica serve para recuperação de senha.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash

_LOCK = threading.RLock()

ARQUIVO = 'usuarios.json'
VERSAO = 2

PAPEL_ADMIN = 'admin'
PAPEL_OPERADOR = 'operador'
PAPEIS = (PAPEL_ADMIN, PAPEL_OPERADOR)

TODAS = 'todas'

HORAS_PRIMEIRO_ACESSO = 168   # 7 dias — o admin entrega o link com calma
HORAS_RECUPERACAO = 4

SENHA_MINIMA = 8


# --------------------------------------------------------------------- arquivo

def _caminho() -> Path:
    from app.config import DATA_DIR
    destino = Path(DATA_DIR) / 'instance'
    destino.mkdir(parents=True, exist_ok=True)
    return destino / ARQUIVO


def _vazio() -> Dict[str, Any]:
    return {'versao': VERSAO, 'usuarios': [], 'convites': []}


def _migrar_v1(dados: Dict[str, Any]) -> Dict[str, Any]:
    """Formato antigo (credencial única) vira o primeiro admin."""
    novo = _vazio()
    if dados.get('usuario') and dados.get('senha_hash'):
        novo['usuarios'].append({
            'id': str(uuid.uuid4()),
            'usuario': dados['usuario'],
            'nome': dados.get('nome') or dados['usuario'],
            'papel': PAPEL_ADMIN,
            'senha_hash': dados['senha_hash'],
            'ativo': True,
            'empresas': TODAS,
            'rotinas': TODAS,
            'criado_em': dados.get('criado_em') or datetime.now().isoformat(),
            'atualizado_em': dados.get('atualizado_em') or datetime.now().isoformat(),
            'ultimo_login': None,
        })
    return novo


def carregar() -> Dict[str, Any]:
    caminho = _caminho()
    if not caminho.exists():
        return _vazio()
    try:
        with caminho.open('r', encoding='utf-8') as f:
            dados = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _vazio()

    if not isinstance(dados, dict):
        return _vazio()
    if dados.get('versao') != VERSAO:
        dados = _migrar_v1(dados)
        _salvar(dados)
    dados.setdefault('usuarios', [])
    dados.setdefault('convites', [])
    return dados


def _salvar(dados: Dict[str, Any]) -> None:
    caminho = _caminho()
    temporario = caminho.with_suffix('.tmp')
    with temporario.open('w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    temporario.replace(caminho)


# --------------------------------------------------------------------- consulta

def _publico(u: Dict[str, Any]) -> Dict[str, Any]:
    """Cópia sem o hash, para devolver na API/tela."""
    return {k: v for k, v in u.items() if k != 'senha_hash'} | {
        'senha_definida': bool(u.get('senha_hash')),
    }


def listar(publico: bool = True) -> List[Dict[str, Any]]:
    usuarios = carregar()['usuarios']
    return [_publico(u) for u in usuarios] if publico else usuarios


def buscar(usuario_id: str = '', login: str = '') -> Optional[Dict[str, Any]]:
    for u in carregar()['usuarios']:
        if usuario_id and u['id'] == usuario_id:
            return u
        if login and u['usuario'] == (login or '').strip():
            return u
    return None


def configurado() -> bool:
    """True quando existe pelo menos um usuário ATIVO com senha definida.

    É o que impede o sistema de ficar aberto: sem isso, o guard manda todo mundo
    para o primeiro acesso.
    """
    if any(u.get('ativo') and u.get('senha_hash') for u in carregar()['usuarios']):
        return True
    return bool(os.getenv('AUTH_USER') and os.getenv('AUTH_PASSWORD'))


def origem() -> Optional[str]:
    if any(u.get('ativo') and u.get('senha_hash') for u in carregar()['usuarios']):
        return 'arquivo'
    if os.getenv('AUTH_USER') and os.getenv('AUTH_PASSWORD'):
        return 'ambiente'
    return None


def total_admins_ativos() -> int:
    return sum(1 for u in carregar()['usuarios']
               if u.get('ativo') and u.get('papel') == PAPEL_ADMIN and u.get('senha_hash'))


# --------------------------------------------------------------------- escrita

def _normalizar_permissoes(papel: str, empresas, rotinas):
    """Admin nunca fica restrito — evita o cenário de trancar a si mesmo."""
    if papel == PAPEL_ADMIN:
        return TODAS, TODAS
    if empresas != TODAS:
        empresas = sorted({int(e) for e in (empresas or []) if str(e).strip()})
    if rotinas != TODAS:
        rotinas = sorted({str(r).strip() for r in (rotinas or []) if str(r).strip()})
    return empresas, rotinas


def criar(usuario: str, nome: str = '', papel: str = PAPEL_OPERADOR,
          empresas=None, rotinas=None, senha: str = '') -> Dict[str, Any]:
    usuario = (usuario or '').strip()
    if not usuario:
        raise ValueError('Informe o usuário (CPF).')
    if papel not in PAPEIS:
        raise ValueError('Papel inválido.')
    if buscar(login=usuario):
        raise ValueError(f'Já existe um usuário "{usuario}".')
    if senha and len(senha) < SENHA_MINIMA:
        raise ValueError(f'A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.')

    empresas, rotinas = _normalizar_permissoes(
        papel,
        TODAS if empresas is None else empresas,
        TODAS if rotinas is None else rotinas)

    agora = datetime.now().isoformat()
    novo = {
        'id': str(uuid.uuid4()),
        'usuario': usuario,
        'nome': (nome or '').strip() or usuario,
        'papel': papel,
        'senha_hash': generate_password_hash(senha) if senha else None,
        'ativo': True,
        'empresas': empresas,
        'rotinas': rotinas,
        'criado_em': agora,
        'atualizado_em': agora,
        'ultimo_login': None,
    }

    with _LOCK:
        dados = carregar()
        dados['usuarios'].append(novo)
        _salvar(dados)
    return _publico(novo)


def atualizar(usuario_id: str, **campos) -> Dict[str, Any]:
    with _LOCK:
        dados = carregar()
        alvo = next((u for u in dados['usuarios'] if u['id'] == usuario_id), None)
        if not alvo:
            raise ValueError('Usuário não encontrado.')

        papel = campos.get('papel', alvo['papel'])
        if papel not in PAPEIS:
            raise ValueError('Papel inválido.')

        # Não deixar o sistema sem nenhum administrador.
        virando_nao_admin = (alvo['papel'] == PAPEL_ADMIN and papel != PAPEL_ADMIN)
        desativando = (alvo.get('ativo') and campos.get('ativo') is False
                       and alvo['papel'] == PAPEL_ADMIN)
        if (virando_nao_admin or desativando) and total_admins_ativos() <= 1:
            raise ValueError('Este é o único administrador ativo. '
                             'Promova outro antes de alterar este.')

        if 'nome' in campos:
            alvo['nome'] = (campos['nome'] or '').strip() or alvo['usuario']
        if 'ativo' in campos:
            alvo['ativo'] = bool(campos['ativo'])
        alvo['papel'] = papel

        empresas = campos.get('empresas', alvo.get('empresas', TODAS))
        rotinas = campos.get('rotinas', alvo.get('rotinas', TODAS))
        alvo['empresas'], alvo['rotinas'] = _normalizar_permissoes(papel, empresas, rotinas)

        alvo['atualizado_em'] = datetime.now().isoformat()
        _salvar(dados)
        return _publico(alvo)


def remover(usuario_id: str) -> None:
    with _LOCK:
        dados = carregar()
        alvo = next((u for u in dados['usuarios'] if u['id'] == usuario_id), None)
        if not alvo:
            raise ValueError('Usuário não encontrado.')
        if alvo['papel'] == PAPEL_ADMIN and total_admins_ativos() <= 1:
            raise ValueError('Não dá para remover o único administrador.')
        dados['usuarios'] = [u for u in dados['usuarios'] if u['id'] != usuario_id]
        dados['convites'] = [c for c in dados['convites'] if c['usuario_id'] != usuario_id]
        _salvar(dados)


def definir_senha(usuario_id: str, senha: str) -> None:
    if len(senha or '') < SENHA_MINIMA:
        raise ValueError(f'A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.')
    with _LOCK:
        dados = carregar()
        alvo = next((u for u in dados['usuarios'] if u['id'] == usuario_id), None)
        if not alvo:
            raise ValueError('Usuário não encontrado.')
        alvo['senha_hash'] = generate_password_hash(senha)
        alvo['atualizado_em'] = datetime.now().isoformat()
        _salvar(dados)


def registrar_login(usuario_id: str) -> None:
    with _LOCK:
        dados = carregar()
        alvo = next((u for u in dados['usuarios'] if u['id'] == usuario_id), None)
        if alvo:
            alvo['ultimo_login'] = datetime.now().isoformat()
            _salvar(dados)


# --------------------------------------------------------------------- login

def verificar(login: str, senha: str) -> Optional[Dict[str, Any]]:
    """Devolve o usuário quando a credencial confere; None caso contrário."""
    login = (login or '').strip()
    if not login or not senha:
        return None

    alvo = buscar(login=login)
    if alvo:
        if not alvo.get('ativo') or not alvo.get('senha_hash'):
            return None
        try:
            if check_password_hash(alvo['senha_hash'], senha):
                return alvo
        except Exception:
            return None
        return None

    # Compatibilidade com o 7o desvio: AUTH_USER/AUTH_PASSWORD do ambiente,
    # válido só enquanto não houver nenhum usuário cadastrado com senha.
    env_usuario, env_senha = os.getenv('AUTH_USER'), os.getenv('AUTH_PASSWORD')
    if env_usuario and env_senha and not any(
            u.get('senha_hash') for u in carregar()['usuarios']):
        if secrets.compare_digest(login, env_usuario) and \
                secrets.compare_digest(senha, env_senha):
            return {'id': 'ambiente', 'usuario': env_usuario, 'nome': env_usuario,
                    'papel': PAPEL_ADMIN, 'ativo': True,
                    'empresas': TODAS, 'rotinas': TODAS}
    return None


# --------------------------------------------------------------------- convites

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def gerar_convite(usuario_id: str, tipo: str = 'primeiro_acesso') -> Dict[str, Any]:
    """Cria o token de primeiro acesso/recuperação e devolve o token EM CLARO.

    O token em claro só existe aqui e na tela do admin — no arquivo fica o sha256.
    Gerar um convite novo invalida o anterior do mesmo usuário.
    """
    horas = HORAS_PRIMEIRO_ACESSO if tipo == 'primeiro_acesso' else HORAS_RECUPERACAO

    with _LOCK:
        dados = carregar()
        alvo = next((u for u in dados['usuarios'] if u['id'] == usuario_id), None)
        if not alvo:
            raise ValueError('Usuário não encontrado.')
        if not alvo.get('ativo'):
            raise ValueError('Usuário inativo. Reative antes de gerar o link.')

        token = secrets.token_urlsafe(32)
        expira = datetime.now() + timedelta(hours=horas)

        dados['convites'] = [c for c in dados['convites']
                             if c['usuario_id'] != usuario_id or c.get('usado_em')]
        dados['convites'].append({
            'token_hash': _hash_token(token),
            'usuario_id': usuario_id,
            'tipo': tipo,
            'criado_em': datetime.now().isoformat(),
            'expira_em': expira.isoformat(),
            'usado_em': None,
        })
        _salvar(dados)

    return {'token': token, 'expira_em': expira.isoformat(), 'tipo': tipo,
            'usuario': alvo['usuario'], 'horas': horas}


def validar_convite(token: str) -> Optional[Dict[str, Any]]:
    """Devolve {'convite', 'usuario'} se o token servir; None se inválido/expirado/usado."""
    if not token:
        return None
    alvo_hash = _hash_token(token)
    dados = carregar()
    for c in dados['convites']:
        if not secrets.compare_digest(c['token_hash'], alvo_hash):
            continue
        if c.get('usado_em'):
            return None
        try:
            if datetime.fromisoformat(c['expira_em']) <= datetime.now():
                return None
        except ValueError:
            return None
        usuario = next((u for u in dados['usuarios']
                        if u['id'] == c['usuario_id']), None)
        if not usuario or not usuario.get('ativo'):
            return None
        return {'convite': c, 'usuario': usuario}
    return None


def consumir_convite(token: str, senha: str) -> Dict[str, Any]:
    """Define a senha e queima o token. Devolve o usuário."""
    valido = validar_convite(token)
    if not valido:
        raise ValueError('Link inválido, já usado ou expirado. Peça outro ao administrador.')
    if len(senha or '') < SENHA_MINIMA:
        raise ValueError(f'A senha precisa ter pelo menos {SENHA_MINIMA} caracteres.')

    alvo_hash = _hash_token(token)
    with _LOCK:
        dados = carregar()
        convite = next((c for c in dados['convites']
                        if secrets.compare_digest(c['token_hash'], alvo_hash)), None)
        if not convite or convite.get('usado_em'):
            raise ValueError('Link já utilizado.')
        usuario = next((u for u in dados['usuarios']
                        if u['id'] == convite['usuario_id']), None)
        if not usuario:
            raise ValueError('Usuário não encontrado.')

        usuario['senha_hash'] = generate_password_hash(senha)
        usuario['atualizado_em'] = datetime.now().isoformat()
        convite['usado_em'] = datetime.now().isoformat()
        _salvar(dados)
        return dict(usuario)


def convite_pendente(usuario_id: str) -> Optional[Dict[str, Any]]:
    agora = datetime.now()
    for c in carregar()['convites']:
        if c['usuario_id'] != usuario_id or c.get('usado_em'):
            continue
        try:
            if datetime.fromisoformat(c['expira_em']) > agora:
                return {'tipo': c['tipo'], 'expira_em': c['expira_em']}
        except ValueError:
            continue
    return None
