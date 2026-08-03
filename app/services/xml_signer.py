"""
Assinatura XML Digital (XMLDSig) sem dependencia externa alem de
cryptography + lxml.

Implementa o minimo necessario para o SERPRO Integra Contador:
- Canonicalizacao C14N 1.0 (http://www.w3.org/TR/2001/REC-xml-c14n-20010315)
- RSA-SHA256 sobre o octet-stream canonico
- Envelope XML (assinatura envelopada dentro do no raiz)

E usado por SerproProcuradorService quando signxml nao esta
disponivel no ambiente.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from typing import Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree


C14N_NS = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
DSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
XMLDSIG_SIGNED_INFO_TEMPLATE = (
    '<SignedInfo xmlns="{dsig}">'
    '<CanonicalizationMethod Algorithm="{c14n}"/>'
    '<SignatureMethod Algorithm="{dsig}#rsa-sha256"/>'
    '<Reference URI="">'
    '<Transforms>'
    '<Transform Algorithm="{dsig}#enveloped-signature"/>'
    '<Transform Algorithm="{c14n}"/>'
    '</Transforms>'
    '<DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
    '<DigestValue></DigestValue>'
    '</Reference>'
    '</SignedInfo>'
).format(dsig=DSIG_NS, c14n=C14N_NS)


def carregar_certificado(pfx_path: str, pfx_password: str) -> Tuple[Optional[object], Optional[x509.Certificate]]:
    """
    Carrega (private_key, certificate) de um arquivo .pfx A1.

    Retorna (None, None) em caso de erro.
    """
    try:
        with open(pfx_path, "rb") as f:
            pfx_data = f.read()
        pw = pfx_password.encode("utf-8") if isinstance(pfx_password, str) else pfx_password
        private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, pw)
        if private_key is None or certificate is None:
            return None, None
        return private_key, certificate
    except Exception:
        return None, None


def _cert_to_b64(certificate: x509.Certificate) -> str:
    der_bytes = certificate.public_bytes(serialization.Encoding.DER)
    return base64.b64encode(der_bytes).decode("ascii")


def _canonicalize(element) -> bytes:
    """Aplica Canonical XML 1.0 (C14N) em um elemento lxml."""
    return etree.tostring(
        element,
        method="c14n",
        with_comments=False,
        exclusive=False,
    )


def _digest_value(c14n_bytes: bytes) -> str:
    digest = hashlib.sha256(c14n_bytes).digest()
    return base64.b64encode(digest).decode("ascii")


def _signature_value(private_key, data: bytes) -> str:
    """Assina os dados com RSA-SHA256 e retorna base64."""
    signature = private_key.sign(
        data,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def assinar_xml_enveloped(
    xml_str: str,
    pfx_path: str,
    pfx_password: str,
) -> Optional[str]:
    """
    Assina o XML fornecido (string) com o certificado A1 do .pfx
    usando XMLDSig enveloped + C14N 1.0 + RSA-SHA256.

    Retorna o XML assinado (string) ou None em caso de erro.
    """
    private_key, certificate = carregar_certificado(pfx_path, pfx_password)
    if private_key is None or certificate is None:
        return None

    try:
        parser = etree.XMLParser(remove_blank_text=False)
        root = etree.fromstring(xml_str.encode("utf-8"), parser=parser)
    except Exception:
        return None

    # Calcula digest sobre o no raiz canonizado (com transform enveloped
    # que remove a propria assinatura apos inclusao).
    try:
        c14n_bytes = _canonicalize(root)
    except Exception:
        return None

    digest_value = _digest_value(c14n_bytes)
    signature_value = _signature_value(private_key, _build_signed_info_c14n(digest_value))

    # Monta o bloco <Signature> e insere como primeiro filho do root.
    signature_xml = (
        f'<Signature xmlns="{DSIG_NS}">'
        f"{_build_signed_info_xml(digest_value)}"
        f"<SignatureValue>{signature_value}</SignatureValue>"
        f'<KeyInfo><X509Data><X509Certificate>{_cert_to_b64(certificate)}</X509Certificate></X509Data></KeyInfo>'
        f"</Signature>"
    )

    try:
        signature_elem = etree.fromstring(signature_xml)
        root.insert(0, signature_elem)
    except Exception:
        return None

    # Serializa o documento final. A canonicalizacao para envio fica
    # a cargo do SERPRO (que aplica seu proprio C14N antes do digest
    # de verificacao).
    try:
        out = etree.tostring(
            root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )
        return out.decode("utf-8")
    except Exception:
        return None


def _build_signed_info_xml(digest_value: str) -> str:
    """Constroi o bloco SignedInfo ja com digest preenchido."""
    return (
        f'<SignedInfo xmlns="{DSIG_NS}">'
        f'<CanonicalizationMethod Algorithm="{C14N_NS}"/>'
        f'<SignatureMethod Algorithm="{DSIG_NS}#rsa-sha256"/>'
        f'<Reference URI="">'
        f'<Transforms>'
        f'<Transform Algorithm="{DSIG_NS}#enveloped-signature"/>'
        f'<Transform Algorithm="{C14N_NS}"/>'
        f'</Transforms>'
        f'<DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
        f'<DigestValue>{digest_value}</DigestValue>'
        f'</Reference>'
        f'</SignedInfo>'
    )


def _build_signed_info_c14n(digest_value: str) -> bytes:
    """Canonicaliza o SignedInfo para calcular SignatureValue."""
    signed_info_str = _build_signed_info_xml(digest_value)
    parser = etree.XMLParser(remove_blank_text=False)
    signed_info = etree.fromstring(signed_info_str, parser=parser)
    return _canonicalize(signed_info)
