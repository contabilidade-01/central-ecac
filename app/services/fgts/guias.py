"""Recebe a GFD que o executor emitiu e só aceita o que passa nas conferências.

Conferências, nesta ordem, antes de gravar:
1. o arquivo é PDF;
2. o texto do PDF traz a raiz do CNPJ do pedido (guia de outra empresa não entra);
3. o Pix copia e cola, quando enviado, tem CRC16 válido;
4. o número da guia é único: reenvio da mesma guia devolve a que já existe.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, Optional, Tuple

from app.extensions import db
from app.fgts_models import FgtsGuia

TAMANHO_MAXIMO = 5 * 1024 * 1024
_RE_NUMERO = re.compile(r'^[0-9A-Za-z.\-/]{6,40}$')


class GuiaRecusada(Exception):
    def __init__(self, mensagem: str, status: int = 422):
        super().__init__(mensagem)
        self.status = status


def crc16_ccitt(texto: str) -> str:
    crc = 0xFFFF
    for byte in texto.encode('utf-8'):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f'{crc:04X}'


def pix_valido(codigo: Optional[str]) -> bool:
    """BR Code: começa em 000201, termina no campo 63 (CRC) com 4 hexadecimais corretos."""
    codigo = (codigo or '').strip()
    if not codigo.startswith('000201') or len(codigo) < 30 or codigo[-8:-4] != '6304':
        return False
    return crc16_ccitt(codigo[:-4]) == codigo[-4:].upper()


def _centavos(valor: Any) -> Optional[int]:
    if valor is None or valor == '':
        return None
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    try:
        texto = str(valor).strip().replace('R$', '').strip()
        if ',' in texto:
            texto = texto.replace('.', '').replace(',', '.')
        return int((Decimal(texto) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise GuiaRecusada('Valor da guia ilegível.', 400)


def _data_iso(valor: Any) -> Optional[str]:
    from app.services.fgts_pedidos_service import PedidoRecusado, normalizar_data
    try:
        return normalizar_data(valor)
    except PedidoRecusado:
        raise GuiaRecusada('Data da guia ilegível.', 400)


def receber(pedido, pdf: bytes, meta: Dict[str, Any], origem: str = 'agente') -> Tuple[FgtsGuia, bool]:
    """Devolve (guia, nova). Não faz commit da trilha do pedido; quem chama fecha."""
    from app.services import fgts_pedidos_service as servico
    if pedido.acao != 'emitir':
        raise GuiaRecusada('Só pedidos de emissão recebem guia.', 409)
    if not pdf or len(pdf) > TAMANHO_MAXIMO or not pdf.startswith(b'%PDF'):
        raise GuiaRecusada('O arquivo enviado não é um PDF válido.')
    numero = str(meta.get('numero') or '').strip()
    if not _RE_NUMERO.match(numero):
        raise GuiaRecusada('Número da guia ausente ou inválido.', 400)
    competencia = servico.normalizar_competencia(meta.get('competencia') or pedido.competencia)
    escolha = servico.resultado(pedido).get('escolha') or ([pedido.competencia] if pedido.competencia else [])
    if competencia not in escolha:
        raise GuiaRecusada('Competência da guia diferente da pedida.', 409)
    pix = (meta.get('pix') or '').strip() or None
    if pix and not pix_valido(pix):
        raise GuiaRecusada('Pix copia e cola com CRC inválido.')
    if not servico.pdf_e_da_empresa(pdf, pedido.cnpj):
        raise GuiaRecusada('O PDF não traz o CNPJ da empresa do pedido. A guia foi descartada.')

    existente = FgtsGuia.query.filter_by(numero=numero).first()
    if existente:
        if existente.cnpj != pedido.cnpj:
            raise GuiaRecusada('Este número de guia pertence a outra empresa.', 409)
        return existente, False

    sha = hashlib.sha256(pdf).hexdigest()
    nome = f'{competencia}_{re.sub(r"[^0-9A-Za-z]", "", numero)}.pdf'
    destino = servico.pasta_pdfs(pedido.cnpj) / nome
    temporario = destino.with_suffix('.tmp')
    temporario.write_bytes(pdf)
    os.replace(temporario, destino)

    guia = FgtsGuia(company_id=pedido.company_id, cnpj=pedido.cnpj, competencia=competencia,
                    grupo=str(meta.get('grupo') or '')[:40] or None, numero=numero,
                    valor_centavos=_centavos(meta.get('valor')), vencimento=_data_iso(meta.get('vencimento')),
                    data_pagamento=_data_iso(meta.get('data_pagamento')), pdf_path=str(destino), sha256=sha,
                    pix_copia_e_cola=pix, pedido_id=pedido.id, origem=origem)
    db.session.add(guia)

    # Mantém o formato que a página do link e o atendechat já leem em `resultado.guias`.
    dados = servico.resultado(pedido)
    guias = dados.setdefault('guias', [])
    guias.append({'grupo': guia.grupo or 'FGTS', 'competencia': competencia, 'identificador': numero,
                  'valor': (guia.valor_centavos or 0) / 100 if guia.valor_centavos is not None else None,
                  'vencimento': meta.get('vencimento'), 'arquivo': nome, 'guia_id': None})
    dados['emitiu'] = True
    servico.gravar_resultado(pedido, dados)
    db.session.flush()
    guias[-1]['guia_id'] = guia.id
    servico.gravar_resultado(pedido, dados)
    return guia, True


def serializar(guia: FgtsGuia) -> Dict[str, Any]:
    return {'id': guia.id, 'cnpj': guia.cnpj, 'competencia': guia.competencia, 'grupo': guia.grupo,
            'numero': guia.numero, 'valor': (guia.valor_centavos / 100) if guia.valor_centavos is not None else None,
            'vencimento': guia.vencimento, 'data_pagamento': guia.data_pagamento,
            'tem_pix': bool(guia.pix_copia_e_cola), 'pedido_id': guia.pedido_id, 'origem': guia.origem,
            'criado_em': guia.criado_em.isoformat()}
