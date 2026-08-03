"""Credenciais de acesso ao sistema.

⚠️ DESVIO INTENCIONAL (10o) — NÃO existe no exe, que era desktop em localhost.

Guarda usuário e **hash** de senha em `<DATA_DIR>/instance/usuarios.json`, seguindo o
mesmo padrão de `procuracao_service` e `agendamento_service`: arquivo JSON ao lado do
banco, dentro do volume persistente. Não toca em `app/models.py` (regra inviolável 3).

Precedência
-----------
1. `usuarios.json` — o que a tela de primeiro acesso e o `scripts/definir_senha.py` gravam;
2. `AUTH_USER` + `AUTH_PASSWORD` do ambiente — compatibilidade com o 7o desvio (HTTP Basic);
3. nada configurado → o sistema exige o **primeiro acesso** antes de liberar qualquer tela.

A senha NUNCA é gravada em texto: `werkzeug.security` (scrypt por padrão no Werkzeug 3).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

_LOCK = threading.Lock()

ARQUIVO = 'usuarios.json'


def _caminho() -> Path:
    from app.config import DATA_DIR
    destino = Path(DATA_DIR) / 'instance'
    destino.mkdir(parents=True, exist_ok=True)
    return destino / ARQUIVO


def carregar() -> Optional[dict]:
    caminho = _caminho()
    if not caminho.exists():
        return None
    try:
        with caminho.open('r', encoding='utf-8') as f:
            dados = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not dados.get('usuario') or not dados.get('senha_hash'):
        return None
    return dados


def _credenciais_ambiente() -> Optional[tuple]:
    usuario = os.getenv('AUTH_USER')
    senha = os.getenv('AUTH_PASSWORD')
    if usuario and senha:
        return usuario, senha
    return None


def configurado() -> bool:
    """True quando existe alguma credencial válida (arquivo ou ambiente)."""
    return carregar() is not None or _credenciais_ambiente() is not None


def origem() -> Optional[str]:
    if carregar() is not None:
        return 'arquivo'
    if _credenciais_ambiente() is not None:
        return 'ambiente'
    return None


def definir(usuario: str, senha: str) -> dict:
    """Grava (ou troca) a credencial. Devolve o registro salvo, sem o hash."""
    usuario = (usuario or '').strip()
    if not usuario:
        raise ValueError('Informe o usuário.')
    if len(senha or '') < 8:
        raise ValueError('A senha precisa ter pelo menos 8 caracteres.')

    with _LOCK:
        anterior = carregar() or {}
        registro = {
            'usuario': usuario,
            'senha_hash': generate_password_hash(senha),
            'criado_em': anterior.get('criado_em') or datetime.now().isoformat(),
            'atualizado_em': datetime.now().isoformat(),
        }
        caminho = _caminho()
        temporario = caminho.with_suffix('.tmp')
        with temporario.open('w', encoding='utf-8') as f:
            json.dump(registro, f, ensure_ascii=False, indent=2)
        temporario.replace(caminho)

    return {'usuario': usuario, 'atualizado_em': registro['atualizado_em']}


def verificar(usuario: str, senha: str) -> bool:
    """Confere a credencial informada. Nunca levanta exceção."""
    usuario = (usuario or '').strip()
    if not usuario or not senha:
        return False

    dados = carregar()
    if dados is not None:
        if usuario != dados['usuario']:
            return False
        try:
            return check_password_hash(dados['senha_hash'], senha)
        except Exception:
            return False

    ambiente = _credenciais_ambiente()
    if ambiente is not None:
        import secrets as _secrets
        env_usuario, env_senha = ambiente
        # tempo constante nos dois campos
        ok_usuario = _secrets.compare_digest(usuario, env_usuario)
        ok_senha = _secrets.compare_digest(senha, env_senha)
        return ok_usuario and ok_senha

    return False
