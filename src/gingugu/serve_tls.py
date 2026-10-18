"""TLS for `gingugu serve`: secure by default off the box, zero-setup on it.

Local mode (stdio) never reaches this module. A serve bound to loopback stays
plain http, so trying it out on one machine needs nothing. A serve bound to
anything else gets TLS with no step from anyone: a self-signed cert generated
once next to the DB and reused after, which ``gingugu remote login`` pins over
the SSH channel it already trusts (the forced command's ``cert`` verb, which
calls ``cert_pem``). An owner can bring their own cert instead
(``MEMORY_SERVE_TLS_CERT`` + ``MEMORY_SERVE_TLS_KEY``), or opt out with
``MEMORY_SERVE_TLS=off``.

The generated cert is a leaf (CA:FALSE): a client pins it as its own trust
anchor, which Python's default verification accepts, strict mode included, with
hostname checking still on - and a pinned cert that cannot sign another one
cannot vouch for anything but this brain.
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .secret_file import write_private

_MODES = ("auto", "on", "off")
_PEM_BLOCK = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----")
_MAX_PEM = 16 * 1024
_VALID_DAYS = 3650  # private trust: no browser lifetime cap applies, and a renewal breaks pins


@dataclass(frozen=True)
class TlsFiles:
    cert: Path
    key: Path
    generated: bool  # serve's own self-signed cert, as opposed to the owner's


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _mode(config: Config) -> str:
    mode = "auto" if config.serve_tls is None else config.serve_tls.strip().lower()
    if mode not in _MODES:
        raise ValueError(f"MEMORY_SERVE_TLS must be one of {', '.join(_MODES)}, got {mode!r}")
    return mode


def _auto_paths(config: Config) -> tuple[Path, Path]:
    tls_dir = config.db_path.parent / "tls"
    return tls_dir / "serve_cert.pem", tls_dir / "serve_key.pem"


def _own_files(config: Config) -> TlsFiles | None:
    cert, key = config.serve_tls_cert, config.serve_tls_key
    if cert is None and key is None:
        return None
    if cert is None or key is None:
        raise ValueError("set both MEMORY_SERVE_TLS_CERT and MEMORY_SERVE_TLS_KEY, or neither")
    files = TlsFiles(Path(cert).expanduser(), Path(key).expanduser(), generated=False)
    for path in (files.cert, files.key):
        if not path.is_file():
            raise ValueError(f"TLS file not found: {path}")
    return files


def resolve(config: Config) -> TlsFiles | None:
    """The cert and key `gingugu serve` should present, or None for plain http.

    Generates the self-signed pair on first use. A bad setting raises, so the
    server refuses to start rather than quietly serving plain http.
    """
    mode = _mode(config)
    if mode == "off":
        return None
    own = _own_files(config)
    if own is not None:
        return own
    if mode == "auto" and is_loopback(config.serve_host):
        return None
    cert, key = _auto_paths(config)
    if not (cert.is_file() and key.is_file()):
        generate(cert, key, default_names(config))
    else:
        try:  # a key restored from a backup may be looser than owner-only
            key.chmod(0o600)
        except OSError:  # pragma: no cover - platform-dependent
            pass
    return TlsFiles(cert, key, generated=True)


def cert_pem(config: Config) -> str | None:
    """The generated cert a client should pin, or None. Never generates.

    Ignores the bind host on purpose: the SSH forced command does not run with
    the service's environment. A brought cert is never offered - a real one
    renews every few months, and a pin on it would break every client.
    """
    if _mode(config) == "off" or config.serve_tls_cert or config.serve_tls_key:
        return None
    cert, key = _auto_paths(config)
    if not (cert.is_file() and key.is_file()):
        return None
    # Re-encoded from the parsed cert: whatever else sits in the file stays put.
    return parse_cert(cert.read_text(encoding="ascii"))


def parse_cert(text: str) -> str:
    """Exactly one PEM certificate and nothing around it, re-encoded. Raises ValueError.

    Re-encoding from the parsed certificate means only certificate bytes are
    ever printed by the forced command or written to a client's pin.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    text = (text or "").strip()
    if not text or len(text) > _MAX_PEM:
        raise ValueError("not a certificate")
    if (
        _PEM_BLOCK.findall(text) != ["CERTIFICATE"]
        or not text.startswith("-----BEGIN CERTIFICATE-----")
        or not text.endswith("-----END CERTIFICATE-----")
    ):
        raise ValueError("expected exactly one PEM certificate and nothing else")
    try:
        cert = x509.load_pem_x509_certificate(text.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("not a valid certificate") from exc
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def default_names(config: Config) -> list[str]:
    """Names the generated cert answers to: this machine, loopback, and extras."""
    short = socket.gethostname().split(".")[0].lower()
    names = [short, f"{short}.local", "localhost", "127.0.0.1", "::1"]
    host = config.serve_host
    try:
        if not ipaddress.ip_address(host).is_unspecified:
            names.append(host)
    except ValueError:
        names.append(host)
    if config.serve_tls_san:
        names.extend(part.strip() for part in config.serve_tls_san.split(","))
    return list(dict.fromkeys(name for name in names if name))


def generate(cert_path: Path, key_path: Path, names: list[str]) -> None:
    """Write a self-signed EC P-256 leaf cert for ``names``; the key owner-only."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    sans: list[x509.GeneralName] = []
    for name in names:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            sans.append(x509.DNSName(name))
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"gingugu brain {names[0]}")])
    now = datetime.datetime.now(datetime.UTC)
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    usage = dict.fromkeys(
        (
            "content_commitment key_encipherment data_encipherment key_agreement"
            " key_cert_sign crl_sign encipher_only decipher_only"
        ).split(),
        False,
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, **usage), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(key_path.parent, 0o700)
    except OSError:  # pragma: no cover - platform-dependent
        pass
    # Key first: a cert on disk without its key is regenerated, never served.
    write_private(key_path, key_pem)
    # Atomic: a crash mid-write must not leave a half cert that passes is_file().
    fd, tmp = tempfile.mkstemp(dir=cert_path.parent, prefix=".cert.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
        os.replace(tmp, cert_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fingerprint(pem: str) -> str:
    """SHA-256 of the cert's DER, as colon-separated hex - what a person compares."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    der = x509.load_pem_x509_certificate(pem.encode("ascii")).public_bytes(
        serialization.Encoding.DER
    )
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))
