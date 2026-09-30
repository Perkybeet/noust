# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console serves TLS 1.2 at the least and only AEAD suites (ENS G07).

There is no socket in these tests (tests/conftest.py makes real network access
fail), so what is pinned is the context uvicorn is given: the version floor,
the list of TLS 1.2 suites OpenSSL will offer, and that the subclass of
``uvicorn.Config`` the server runs applies both to the context it builds.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from fastapi import FastAPI

from noust.core import tls_policy
from noust.web import server as server_module


def server_context() -> ssl.SSLContext:
    """
    Build a server context the way uvicorn does, without a certificate.

    Returns:
        A bare TLS server context.
    """
    return ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)


def test_the_floor_is_tls_1_2() -> None:
    context = tls_policy.harden_server_context(server_context())

    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_every_tls_1_2_suite_is_ecdhe_with_an_aead_cipher() -> None:
    context = tls_policy.harden_server_context(server_context())

    suites = [cipher for cipher in context.get_ciphers() if cipher["protocol"] == "TLSv1.2"]

    assert suites, "no TLS 1.2 suite left: clients without TLS 1.3 could not connect"
    for cipher in suites:
        name = cipher["name"]
        assert name.startswith("ECDHE-"), name
        assert "GCM" in name or "CHACHA20" in name, name
        assert "CBC" not in name and "3DES" not in name and "RC4" not in name, name


def test_no_suite_below_tls_1_2_is_offered() -> None:
    context = tls_policy.harden_server_context(server_context())

    protocols = {cipher["protocol"] for cipher in context.get_ciphers()}

    assert protocols <= {"TLSv1.2", "TLSv1.3"}


def test_compression_and_client_ordering_are_off() -> None:
    context = tls_policy.harden_server_context(server_context())

    assert context.options & ssl.OP_NO_COMPRESSION
    assert context.options & ssl.OP_CIPHER_SERVER_PREFERENCE


def test_hardening_keeps_what_the_context_already_had() -> None:
    context = server_context()
    context.verify_mode = ssl.CERT_OPTIONAL

    assert tls_policy.harden_server_context(context) is context
    assert context.verify_mode == ssl.CERT_OPTIONAL


def test_uvicorn_is_told_the_same_suites(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cipher list is also given to uvicorn, which sets it while it builds the context."""
    kwargs = server_module._uvicorn_kwargs(FastAPI(), "203.0.113.5", 8443, "/c.pem", "/k.pem")

    assert kwargs["ssl_ciphers"] == tls_policy.TLS12_CIPHERS


def test_the_server_config_hardens_the_context_uvicorn_builds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever context uvicorn made, the floor is applied to it before it serves."""
    import uvicorn.config

    built: list[ssl.SSLContext] = []

    def fake_context(*args: object, **kwargs: object) -> ssl.SSLContext:
        context = server_context()
        built.append(context)
        return context

    monkeypatch.setattr(uvicorn.config, "create_ssl_context", fake_context)

    config = server_module.console_config_class()(
        FastAPI(), ssl_certfile="/c.pem", ssl_keyfile="/k.pem"
    )
    config.load()

    assert config.ssl is built[0]
    assert config.ssl.minimum_version == ssl.TLSVersion.TLSv1_2
    assert config.ssl.options & ssl.OP_NO_COMPRESSION


def test_plain_http_is_left_alone() -> None:
    config = server_module.console_config_class()(FastAPI())
    config.load()

    assert config.ssl is None


# -- a real handshake, in memory --------------------------------------------------------


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    """
    Mint a throwaway ECDSA P-256 pair, the kind ``generate_self_signed`` makes.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        The certificate and key paths.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "nas")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("nas")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "c.pem", tmp_path / "k.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _handshake(server: ssl.SSLContext, client: ssl.SSLContext) -> tuple[str | None, str]:
    """
    Run a TLS handshake between two contexts through memory buffers.

    Args:
        server: The console's context.
        client: The client's.

    Returns:
        The negotiated protocol version and cipher.

    Raises:
        ssl.SSLError: When they cannot agree.
    """
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server_side = server.wrap_bio(server_in, server_out, server_side=True)
    client_side = client.wrap_bio(client_in, client_out, server_hostname="nas")
    done = {"client": False, "server": False}
    for _ in range(20):
        for label, side, out, other in (
            ("client", client_side, client_out, server_in),
            ("server", server_side, server_out, client_in),
        ):
            if not done[label]:
                try:
                    side.do_handshake()
                    done[label] = True
                except ssl.SSLWantReadError:
                    pass
            data = out.read()
            if data:
                other.write(data)
        if all(done.values()):
            cipher = client_side.cipher()
            assert cipher is not None
            return client_side.version(), cipher[0]
    raise AssertionError("the handshake did not finish")


def _client() -> ssl.SSLContext:
    """
    Returns:
        A client that trusts anything: what is under test is the server's floor.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def test_a_modern_client_gets_tls_1_3(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    cert, key = _pair(tmp_path)
    server = server_context()
    server.load_cert_chain(cert, key)
    tls_policy.harden_server_context(server)

    version, _cipher = _handshake(server, _client())

    assert version == "TLSv1.3"


def test_a_tls_1_2_client_gets_an_ecdhe_aead_suite(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    cert, key = _pair(tmp_path)
    server = server_context()
    server.load_cert_chain(cert, key)
    tls_policy.harden_server_context(server)
    client = _client()
    client.maximum_version = ssl.TLSVersion.TLSv1_2

    version, cipher = _handshake(server, client)

    assert version == "TLSv1.2"
    assert cipher.startswith("ECDHE-ECDSA-") and ("GCM" in cipher or "CHACHA20" in cipher)


def test_a_client_that_only_knows_cbc_suites_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    cert, key = _pair(tmp_path)
    server = server_context()
    server.load_cert_chain(cert, key)
    tls_policy.harden_server_context(server)
    client = _client()
    client.maximum_version = ssl.TLSVersion.TLSv1_2
    client.set_ciphers("ECDHE-ECDSA-AES128-SHA")

    with pytest.raises(ssl.SSLError):
        _handshake(server, client)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_client_that_only_knows_tls_1_1_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    cert, key = _pair(tmp_path)
    server = server_context()
    server.load_cert_chain(cert, key)
    tls_policy.harden_server_context(server)
    client = _client()
    try:
        client.minimum_version = ssl.TLSVersion.TLSv1
        client.maximum_version = ssl.TLSVersion.TLSv1_1
    except (ssl.SSLError, ValueError):
        pytest.skip("this OpenSSL cannot even offer TLS 1.1 as a client")

    with pytest.raises(ssl.SSLError):
        _handshake(server, client)
