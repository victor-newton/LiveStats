"""Par TLS de TESTE (D33): gerado em diretorio temporario pela propria suite e apagado ao final.

Nada daqui entra em producao nem le `./secrets/` do repositorio. O certificado e autoassinado (CA:TRUE) e tem
os IPs pedidos no SAN; os clientes de teste o usam como ancora de confianca (`cadata`), entao a cadeia e VERIFICADA
de verdade (so o nome do host, quando o cliente fala com `web` pela rede do Docker, deixa de ser conferido).
"""

from __future__ import annotations

import datetime
import ipaddress
import ssl
from pathlib import Path


def make_tls_pair(directory: Path, ips=("127.0.0.1",), days=2, mode=0o644) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "livestats-admin-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(i)) for i in ips]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = directory / "admin_tls_cert.pem", directory / "admin_tls_key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    )
    cert_path.chmod(mode)
    key_path.chmod(mode)
    return cert_path, key_path


def client_context(cert_path: Path, check_hostname: bool = True) -> ssl.SSLContext:
    """Contexto que confia SO no certificado de teste (TLS >= 1.2)."""
    ctx = ssl.create_default_context(cadata=Path(cert_path).read_text(encoding="ascii"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = check_hostname
    return ctx
