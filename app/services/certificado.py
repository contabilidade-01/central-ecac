"""Resolução do caminho do certificado A1 — um lugar só.

⚠️ DESVIO INTENCIONAL (3o) — ampliado em 03/08/2026.

`settings.certificado_path` guarda um caminho ABSOLUTO, gravado na máquina onde o
certificado foi cadastrado (ex.: `C:\\Users\\parce\\...\\contador_certificado.pfx`).
Esse caminho não existe em outra máquina — e muito menos no container Linux da VPS.

O que aconteceu por causa disso
-------------------------------
O fallback existia **só em `report_service`**. Resultado no servidor: a situação fiscal
funcionava e **todo o resto falhava** — caixa postal, parcelamentos, DAS e pagamentos —
com `FileNotFoundError`, que não tem cara de erro da SERPRO e por isso aparecia como
"erro sem detalhe" na tela de procurações. Foi o que derrubou as 72 empresas na primeira
varredura, em 03/08/2026.

Agora todos os módulos usam esta função. Se o caminho gravado não existir, procura o
mesmo arquivo — e depois o nome padrão — dentro do `CERTS_DIR` desta máquina.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

NOME_PADRAO = 'contador_certificado.pfx'


def _certs_dir() -> Path:
    try:
        from flask import current_app
        return Path(current_app.config['CERTS_DIR'])
    except Exception:
        from app.config import CERTS_DIR
        return Path(CERTS_DIR)


def resolver(certificado_path: Optional[str]) -> str:
    """Devolve um caminho que EXISTE nesta máquina, ou levanta ValueError."""
    if certificado_path and Path(certificado_path).exists():
        return str(certificado_path)

    certs_dir = _certs_dir()
    candidatos = []
    if certificado_path:
        candidatos.append(certs_dir / Path(certificado_path.replace('\\', '/')).name)
    candidatos.append(certs_dir / NOME_PADRAO)

    for candidato in candidatos:
        if candidato.exists():
            logger.info('Certificado não encontrado em %s; usando %s',
                        certificado_path, candidato)
            return str(candidato)

    raise ValueError(
        f'Certificado não encontrado: {certificado_path or "(vazio)"}. '
        f'Envie o .pfx pela tela "Restaurar dados" ou corrija o caminho em '
        f'Configurações. Procurado também em {certs_dir}.'
    )


def carregar(certificado_path: Optional[str]) -> bytes:
    """Conteúdo do .pfx, resolvendo o caminho antes."""
    return Path(resolver(certificado_path)).read_bytes()
