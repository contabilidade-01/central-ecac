"""Pix copia e cola da GFD, lido do QR Code do PDF e validado pelo CRC16.

Na fase 0 (09/11/2026) o extrator de texto do programa recuperado falhou no PDF real; o
QR Code foi lido e o CRC bateu. Por isso a leitura é pela imagem.
"""
from __future__ import annotations

import re
from typing import Optional


def crc16_ccitt(texto: str) -> str:
    crc = 0xFFFF
    for byte in texto.encode('utf-8'):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f'{crc:04X}'


def valido(codigo: Optional[str]) -> bool:
    codigo = (codigo or '').strip()
    if not codigo.startswith('000201') or len(codigo) < 30 or codigo[-8:-4] != '6304':
        return False
    return crc16_ccitt(codigo[:-4]) == codigo[-4:].upper()


def do_pdf(pdf: bytes) -> Optional[str]:
    """Primeiro QR válido das duas primeiras páginas; None se não houver."""
    import cv2
    import fitz
    import numpy as np
    doc = fitz.open(stream=pdf, filetype='pdf')
    detector = cv2.QRCodeDetector()
    for pagina in list(doc)[:2]:
        for dpi in (250, 400):
            img = pagina.get_pixmap(dpi=dpi)
            matriz = np.frombuffer(img.samples, dtype=np.uint8).reshape(img.h, img.w, img.n)[:, :, :3]
            texto, _, _ = detector.detectAndDecode(matriz)
            if texto and valido(texto):
                return texto
    return None


RE_NUMERO = re.compile(r'\b(\d{16}-\d)\b')


def numero_do_pdf(pdf: bytes) -> Optional[str]:
    import fitz
    doc = fitz.open(stream=pdf, filetype='pdf')
    texto = ''.join(p.get_text() for p in list(doc)[:2])
    achado = RE_NUMERO.search(texto)
    return achado.group(1) if achado else None
