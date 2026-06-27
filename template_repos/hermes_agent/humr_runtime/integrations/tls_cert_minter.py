"""Boot-generated CA that mints per-SNI leaf certs for TLS-intercept."""

import contextlib
import datetime as dt
import ipaddress
import logging
import os
import random
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


logger = logging.getLogger("tls_intercept")


class _CertMinter:
    """Boot-generated CA that mints leaf certs on demand, one per SNI hostname."""

    def __init__(self, ca_dir: Path, private_dir: Path) -> None:
        self._ca_dir = ca_dir
        self._private_dir = private_dir
        self._ca_key: rsa.RSAPrivateKey | None = None
        self._ca_cert: x509.Certificate | None = None
        self._leaf_cache: dict[str, ssl.SSLContext] = {}

    def bootstrap(self) -> None:
        """Generate the CA and write bundle.pem. Called once at broker startup."""
        self._ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        subject = issuer = x509.Name([
            x509.NameAttribute(x509.NameOID.COMMON_NAME, "HUMR Integrations Broker CA"),
        ])
        now = dt.datetime.now(dt.timezone.utc)
        ca_ski = x509.SubjectKeyIdentifier.from_public_key(self._ca_key.public_key())
        self._ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self._ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365 * 5))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True,
                    key_encipherment=False, content_commitment=False, data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(ca_ski, critical=False)
            .sign(private_key=self._ca_key, algorithm=hashes.SHA256())
        )
        self._write_bundle()

    def _write_bundle(self) -> None:
        """Write the CA cert + system roots into bundle.pem for SSL_CERT_FILE."""
        self._ca_dir.mkdir(parents=True, exist_ok=True)
        self._private_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._private_dir, 0o700)
        our_pem = self._ca_cert.public_bytes(serialization.Encoding.PEM)
        system_roots = b""
        for candidate in (Path("/etc/ssl/certs/ca-certificates.crt"), Path("/etc/pki/tls/certs/ca-bundle.crt")):
            if candidate.exists():
                system_roots = candidate.read_bytes()
                break
        if not system_roots:
            logger.error("no system root bundle found; broker-trusted bundle will be HUMR-only")
        bundle_path = self._ca_dir / "bundle.pem"
        bundle_path.write_bytes(our_pem + b"\n" + system_roots)
        os.chmod(bundle_path, 0o644)
        logger.info("wrote CA bundle to %s (system roots included: %s)", bundle_path, bool(system_roots))

    def context_for(self, hostname: str) -> ssl.SSLContext:
        """Return an SSLContext presenting a leaf cert valid for hostname."""
        if hostname in self._leaf_cache:
            return self._leaf_cache[hostname]
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(dt.timezone.utc)
        san_entries: list[x509.GeneralName] = []
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(hostname)))
        except ValueError:
            san_entries.append(x509.DNSName(hostname))
        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)]))
            .issuer_name(self._ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365 * 2))
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_encipherment=True,
                    content_commitment=False, data_encipherment=False,
                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self._ca_key.public_key()), critical=False)
            .sign(private_key=self._ca_key, algorithm=hashes.SHA256())
        )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert_path = self._pem_bytes_to_private_tmp(pem=leaf_cert.public_bytes(serialization.Encoding.PEM))
        key_path = self._pem_bytes_to_private_tmp(
            pem=leaf_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        try:
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        finally:
            for path in (cert_path, key_path):
                with contextlib.suppress(FileNotFoundError):
                    Path(path).unlink()
        ctx.set_alpn_protocols(["http/1.1"])
        self._leaf_cache[hostname] = ctx
        logger.info("minted leaf cert for %s", hostname)
        return ctx

    def _pem_bytes_to_private_tmp(self, pem: bytes) -> str:
        """Write PEM to a broker-private tmpfile and return its path."""
        target = self._private_dir / f".leaf-{random.randbytes(8).hex()}.pem"
        target.write_bytes(pem)
        os.chmod(target, 0o600)
        return str(target)
