# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Uploading a certificate: what is checked, where it is kept, and what it can never be.

openssl is faked with the text a real one printed for a certificate with subject
alternative names (RSA 2048) and one for an EC certificate, so the parsing is held to the
format as it is. Pinned: a key that is not the certificate's, a key with a passphrase,
a certificate that has expired and one that names nothing are refused before anything is
written; the key reaches openssl on its standard input and is stored 0600 in a 0700
directory; and a certificate an application serves cannot be removed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noust.core import paths
from noust.core.certificate_store import CertificateSettings, CertificateStore
from noust.core.exceptions import CertificateError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers import custom_certificates as custom_module
from noust.managers.custom_certificates import (
    CustomCertificates,
    covers,
    parse_inspection,
    pem_blocks,
)

RSA_TEXT = """\
Certificate:
    Data:
        Version: 3 (0x2)
        Signature Algorithm: sha256WithRSAEncryption
        Issuer: CN = Cloudflare Origin SSL Certificate Authority, O = CloudFlare, Inc.
        Validity
            Not Before: Oct 13 13:18:54 2026 GMT
            Not After : Nov 17 13:18:54 2027 GMT
        Subject: CN = origin.example.com, O = Test Org
        Subject Public Key Info:
            Public Key Algorithm: rsaEncryption
                Public-Key: (2048 bit)
        X509v3 extensions:
            X509v3 Subject Alternative Name:
                DNS:origin.example.com, DNS:*.example.com
sha256 Fingerprint=05:0C:ED:6E:CB:83:49:FF:BD:0A:F6:5D:EC:6B:54:7A:E2:8B:4A:06:77:A9:E0:8E:8A:14:62:18:B2:24:C1:20
"""

EC_TEXT = """\
Certificate:
    Data:
        Issuer: CN = ec.example.com
        Validity
            Not Before: Oct 13 13:19:03 2026 GMT
            Not After : Jan 11 2027 13:19:03 GMT
        Subject: CN = ec.example.com
        Subject Public Key Info:
            Public Key Algorithm: id-ecPublicKey
                Public-Key: (256 bit)
                ASN1 OID: prime256v1
sha256 Fingerprint=B4:47:5D:5C:5B:D7:36:1B:D8:0A:03:CC:42:A4:6A:F8
"""

LEAF = "-----BEGIN CERTIFICATE-----\nLEAFLEAFLEAF\n-----END CERTIFICATE-----\n"
CHAIN = "-----BEGIN CERTIFICATE-----\nINTERMEDIATE\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nKEYKEYKEY\n-----END PRIVATE KEY-----\n"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "etc-noust"
    monkeypatch.setattr(paths, "config_dir", lambda: directory)
    return directory / "certs"


@pytest.fixture
def uploads(store: NoustStore, runner: FakeRunner, root: Path) -> CustomCertificates:
    runner.script(["openssl", "x509", "-noout", "-text"], stdout=RSA_TEXT)
    runner.script(["openssl", "x509", "-noout", "-pubkey"], stdout="PUB\n")
    runner.script(["openssl", "pkey", "-pubout"], stdout="PUB\n")
    runner.script(["openssl", "verify"], stdout="leaf.pem: OK\n")
    return CustomCertificates(CertificateStore(store), now=NOW)


# -- reading what openssl says ---------------------------------------------------------


def test_the_text_of_a_real_certificate_is_read_whole() -> None:
    seen = parse_inspection(RSA_TEXT)

    assert seen.names == ("origin.example.com", "*.example.com")
    assert seen.key_type == "RSA 2048"
    assert seen.fingerprint.startswith("05:0C:ED")
    assert seen.not_after == datetime(2027, 11, 17, 13, 18, 54, tzinfo=timezone.utc)
    assert seen.issuer.startswith("CN = Cloudflare Origin")


def test_an_ec_certificate_without_alternative_names_falls_back_to_its_common_name() -> None:
    seen = parse_inspection(EC_TEXT.replace("Jan 11 2027 13:19:03", "Jan 11 13:19:03 2027"))

    assert seen.names == ("ec.example.com",)
    assert seen.key_type == "EC prime256v1"


def test_output_that_is_not_a_certificate_is_refused() -> None:
    with pytest.raises(CertificateError):
        parse_inspection("unable to load certificate\n")


def test_pem_is_split_into_its_blocks_in_order() -> None:
    blocks = pem_blocks(LEAF + CHAIN + KEY)

    assert [label for label, _ in blocks] == ["CERTIFICATE", "CERTIFICATE", "PRIVATE KEY"]


@pytest.mark.parametrize(
    ("names", "host", "covered"),
    [
        (["example.com"], "example.com", True),
        (["*.example.com"], "shop.example.com", True),
        (["*.example.com"], "example.com", False),
        (["*.example.com"], "a.b.example.com", False),
        (["*.example.com"], "*.example.com", True),
        (["example.com"], "*.example.com", False),
        (["Shop.Example.com"], "shop.example.com", True),
    ],
)
def test_which_hosts_a_certificates_names_cover(names: list[str], host: str, covered: bool) -> None:
    assert covers(names, host) is covered


# -- uploading -----------------------------------------------------------------------


def test_a_good_pair_is_checked_and_kept_with_the_key_private(
    uploads: CustomCertificates, runner: FakeRunner, root: Path
) -> None:
    kept = uploads.upload("origin", LEAF, KEY, CHAIN, uploaded_by="ana")

    assert kept.names == ("origin.example.com", "*.example.com")
    assert kept.uploaded_by == "ana"
    assert (root / "origin" / "fullchain.pem").read_text() == LEAF + CHAIN
    assert (root / "origin" / "privkey.pem").read_text() == KEY
    assert (root / "origin" / "privkey.pem").stat().st_mode & 0o777 == 0o600
    assert (root / "origin").stat().st_mode & 0o777 == 0o700
    # The key was given to openssl on its input, and never named in an argument.
    assert "KEYKEYKEY" not in " ".join(part for call in runner.calls for part in call)
    assert KEY in runner.inputs
    assert uploads.get("origin").fingerprint.startswith("05:0C")


def test_a_key_that_is_not_the_certificates_is_refused_before_anything_is_written(
    uploads: CustomCertificates, runner: FakeRunner, root: Path
) -> None:
    runner.script(["openssl", "pkey", "-pubout"], stdout="SOMETHING ELSE\n")

    with pytest.raises(ValidationError) as raised:
        uploads.upload("origin", LEAF, KEY)

    assert raised.value.field == "private_key"
    assert not root.exists()
    assert uploads.uploaded() == []


def test_a_key_with_a_passphrase_is_refused_with_the_command_that_removes_it(
    uploads: CustomCertificates,
) -> None:
    encrypted = "-----BEGIN ENCRYPTED PRIVATE KEY-----\nxxxx\n-----END ENCRYPTED PRIVATE KEY-----\n"

    with pytest.raises(ValidationError) as raised:
        uploads.upload("origin", LEAF, encrypted)

    assert "openssl pkey" in raised.value.details
    assert raised.value.field == "private_key"


@pytest.mark.parametrize("text", ["not pem", "", "-----BEGIN CERTIFICATE-----\n"])
def test_something_that_is_not_a_pem_certificate_is_refused(
    uploads: CustomCertificates, text: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        uploads.upload("origin", text, KEY)

    assert raised.value.field == "certificate"


def test_no_private_key_in_the_key_file_is_said(uploads: CustomCertificates) -> None:
    with pytest.raises(ValidationError) as raised:
        uploads.upload("origin", LEAF, LEAF)

    assert raised.value.field == "private_key"


def test_an_expired_certificate_is_refused(uploads: CustomCertificates, runner: FakeRunner) -> None:
    runner.script(
        ["openssl", "x509", "-noout", "-text"],
        stdout=RSA_TEXT.replace("Nov 17 13:18:54 2027", "Nov 17 13:18:54 2025"),
    )

    with pytest.raises(ValidationError) as raised:
        uploads.upload("origin", LEAF, KEY)

    assert "expired on 2025-11-17" in raised.value.message


def test_a_certificate_that_names_nothing_is_refused(
    uploads: CustomCertificates, runner: FakeRunner
) -> None:
    runner.script(
        ["openssl", "x509", "-noout", "-text"],
        stdout=RSA_TEXT.replace("DNS:origin.example.com, DNS:*.example.com", "email:a@b.c").replace(
            "CN = origin.example.com", "O = nobody"
        ),
    )

    with pytest.raises(ValidationError) as raised:
        uploads.upload("origin", LEAF, KEY)

    assert "names nothing" in raised.value.message


@pytest.mark.parametrize("name", ["", "Upper", "a b", "../x", ".hidden", "x" * 70])
def test_a_name_that_could_leave_the_directory_is_refused(
    uploads: CustomCertificates, name: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        uploads.upload(name, LEAF, KEY)

    assert raised.value.field == "name"


def test_openssl_failing_is_the_error_with_its_own_words(
    uploads: CustomCertificates, runner: FakeRunner
) -> None:
    runner.script(
        ["openssl", "x509", "-noout", "-text"], stderr="unable to load certificate", exit_code=1
    )

    with pytest.raises(CertificateError) as raised:
        uploads.upload("origin", LEAF, KEY)

    assert raised.value.output == "unable to load certificate"


def test_a_chain_that_does_not_lead_to_the_issuer_is_a_warning_not_a_refusal(
    uploads: CustomCertificates, runner: FakeRunner, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.script(
        ["openssl", "verify"],
        stderr="error 20 at 0 depth lookup: unable to get local issuer certificate\n",
        exit_code=2,
    )
    seen: list[str | None] = []
    from noust.core import audit

    real = audit.record
    monkeypatch.setattr(
        custom_module.audit,
        "record",
        lambda event, **kw: (seen.append(kw["details"].get("chain_warning")), real(event, **kw))[1],
    )

    uploads.upload("origin", LEAF, KEY, CHAIN)

    assert (root / "origin" / "fullchain.pem").exists()
    assert seen and "does not lead to the certificate's issuer" in str(seen[0])
    assert not [p for p in root.iterdir() if p.name.startswith(".check-")]


def test_uploading_again_under_the_same_name_replaces_it(
    uploads: CustomCertificates, root: Path
) -> None:
    uploads.upload("origin", LEAF, KEY)
    uploads.upload("origin", LEAF + CHAIN, KEY)

    assert len(uploads.uploaded()) == 1
    assert (root / "origin" / "fullchain.pem").read_text() == LEAF + CHAIN


# -- removing -------------------------------------------------------------------------


def test_an_unused_upload_is_removed_with_its_files(
    uploads: CustomCertificates, root: Path
) -> None:
    uploads.upload("origin", LEAF, KEY)

    uploads.remove("origin")

    assert uploads.uploaded() == []
    assert not (root / "origin").exists()


def test_an_upload_an_application_serves_cannot_be_removed(
    uploads: CustomCertificates, store: NoustStore, root: Path
) -> None:
    uploads.upload("origin", LEAF, KEY)
    CertificateStore(store).set_settings(
        CertificateSettings(domain="shop.example.com", source="custom", name="origin")
    )

    with pytest.raises(ValidationError) as raised:
        uploads.remove("origin")

    assert "shop.example.com" in raised.value.message
    assert (root / "origin" / "privkey.pem").exists()


def test_removing_what_was_never_uploaded_says_so(uploads: CustomCertificates) -> None:
    with pytest.raises(CertificateError):
        uploads.remove("ghost")
