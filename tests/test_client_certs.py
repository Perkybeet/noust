# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Client certificates: an application's site that only answers who holds one of its certificates.

The authority and the certificates are made by the real ``openssl`` (the tests that need it are
marked, and skipped on a machine without it), so what is pinned is what a certificate really is: it
can authenticate a client and nothing else, it is signed by its application's authority and by no
other, its serial is written the same by every tool, the bundle opens with the password it is given
and with no other, and no secret was on a command line. The rest (the record, the revocation, what
the site's file says) needs no process.
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noust.core.client_cert_store import ClientCertificateStore
from noust.core.exceptions import ValidationError
from noust.core.runner import SubprocessRunner
from noust.core.store import NoustStore
from noust.managers.client_certs import ClientCertificates

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not installed"
)
real = pytest.mark.allow_subprocess


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def certificates(store: NoustStore, tmp_path: Path) -> ClientCertificates:
    return ClientCertificates(
        store=ClientCertificateStore(store), runner=SubprocessRunner(), root=tmp_path / "clients"
    )


def openssl(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["openssl", *args], input=input_text, capture_output=True, text=True, check=False
    )


def text_of(path: Path) -> str:
    return openssl("x509", "-in", str(path), "-noout", "-text").stdout


# -- what a certificate really is -----------------------------------------------------------


@needs_openssl
@real
def test_a_certificate_can_authenticate_a_client_and_nothing_else(
    certificates: ClientCertificates,
) -> None:
    issued = certificates.issue("shop.example.com", "ana-laptop", days=90, actor="ana")

    cert = certificates.directory("shop.example.com") / "issued" / f"{issued.record.serial}.crt"
    described = text_of(cert)
    assert "TLS Web Client Authentication" in described
    assert "TLS Web Server Authentication" not in described
    assert "CA:FALSE" in described
    assert "Digital Signature" in described
    assert "Subject: CN = ana-laptop" in described
    assert (
        issued.key.startswith("-----BEGIN PRIVATE KEY-----") and "EC" not in issued.certificate[:5]
    )


@needs_openssl
@real
def test_it_is_signed_by_its_applications_authority_and_by_no_other(
    certificates: ClientCertificates,
) -> None:
    shop = certificates.issue("shop.example.com", "ana", days=30)
    certificates.issue("blog.example.com", "bob", days=30)
    shop_ca = certificates.ca_path("shop.example.com")
    blog_ca = certificates.ca_path("blog.example.com")
    cert = certificates.directory("shop.example.com") / "issued" / f"{shop.record.serial}.crt"

    own = openssl("verify", "-purpose", "sslclient", "-CAfile", str(shop_ca), str(cert))
    other = openssl("verify", "-purpose", "sslclient", "-CAfile", str(blog_ca), str(cert))

    assert own.returncode == 0 and "OK" in own.stdout
    assert other.returncode != 0
    assert "Noust client CA for shop.example.com" in text_of(shop_ca)
    assert shop_ca != blog_ca


@needs_openssl
@real
def test_the_serial_is_written_the_same_by_every_tool(certificates: ClientCertificates) -> None:
    for number in range(12):
        issued = certificates.issue("shop.example.com", f"client-{number}", days=30)
        serial = issued.record.serial
        assert re.fullmatch(r"[1-7][0-9A-F]{31}", serial), serial
        cert = certificates.directory("shop.example.com") / "issued" / f"{serial}.crt"
        printed = openssl("x509", "-in", str(cert), "-noout", "-serial").stdout.strip()
        assert printed == f"serial={serial}"


@needs_openssl
@real
def test_the_record_holds_what_the_certificate_says_and_no_key(
    certificates: ClientCertificates, store: NoustStore
) -> None:
    issued = certificates.issue("shop.example.com", "ana-laptop", days=365, actor="ana")

    record = certificates.list("shop.example.com")[0]
    assert record == issued.record and record.name == "ana-laptop" and record.issued_by == "ana"
    assert re.fullmatch(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", record.fingerprint)
    begins, ends = (
        datetime.fromisoformat(record.not_before),
        datetime.fromisoformat(record.not_after),
    )
    assert 364 <= (ends - begins).days <= 366 and ends > datetime.now(timezone.utc)
    with store._transaction() as cursor:
        cursor.execute("SELECT * FROM client_certificates")
        everything = " ".join(str(value) for row in cursor.fetchall() for value in tuple(row))
    assert "PRIVATE KEY" not in everything and issued.key.splitlines()[1] not in everything


@needs_openssl
@real
def test_the_bundle_opens_with_its_password_and_with_no_other(
    certificates: ClientCertificates, tmp_path: Path
) -> None:
    issued = certificates.issue("shop.example.com", "ana-laptop", days=30)
    bundle = tmp_path / "ana.p12"
    bundle.write_bytes(issued.bundle)

    good = openssl("pkcs12", "-in", str(bundle), "-nodes", "-passin", f"pass:{issued.password}")
    bad = openssl("pkcs12", "-in", str(bundle), "-nodes", "-passin", "pass:not-the-password")

    assert good.returncode == 0
    assert "BEGIN PRIVATE KEY" in good.stdout and "friendlyName: ana-laptop" in good.stdout
    assert good.stdout.count("BEGIN CERTIFICATE") == 2  # the client's and the authority's
    assert bad.returncode != 0
    assert base64.b64decode(issued.bundle_base64()) == issued.bundle


@needs_openssl
@real
def test_no_secret_is_on_a_command_line(certificates: ClientCertificates) -> None:
    seen: list[list[str]] = []
    runner = SubprocessRunner()
    original = runner.run

    def record(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(list(argv))
        return original(argv, **kwargs)

    runner.run = record  # type: ignore[method-assign]
    mine = ClientCertificates(
        store=certificates.records, runner=runner, root=certificates.directory("x").parent
    )

    issued = mine.issue("shop.example.com", "ana", days=30)

    everything = " ".join(" ".join(argv) for argv in seen)
    assert issued.password not in everything
    assert issued.key.splitlines()[1] not in everything
    assert any(argv[:3] == ["openssl", "pkcs12", "-export"] for argv in seen)


@needs_openssl
@real
def test_the_files_made_for_the_bundle_are_gone(
    certificates: ClientCertificates, tmp_path: Path
) -> None:
    certificates.issue("shop.example.com", "ana", days=30)

    from noust.core import sealing
    from noust.core.secrets import secrets_dir

    work = sealing.plaintext_copy_dir(secrets_dir()) / "clientcert"
    assert not work.exists() or list(work.iterdir()) == []
    private = list(certificates.directory("shop.example.com").glob("*.key"))
    assert [p.name for p in private] == ["ca.key"]  # only the authority's own


# -- the rules that need no process ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", "-leading", "has space", "a/b", "a=b", "x" * 65, "ñandú", "a\nb", ".hidden", "a;b"],
)
def test_a_name_that_cannot_be_a_subject_is_refused(
    certificates: ClientCertificates, name: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        certificates.issue("shop.example.com", name, days=30)

    assert raised.value.field == "name"
    assert certificates.list("shop.example.com") == []


@pytest.mark.parametrize("days", [0, -1, 826, 100000])
def test_a_validity_out_of_range_is_refused(certificates: ClientCertificates, days: int) -> None:
    with pytest.raises(ValidationError) as raised:
        certificates.issue("shop.example.com", "ana", days=days)

    assert raised.value.field == "days"


def test_revoking_is_recorded_once_and_unknown_serials_are_refused(
    certificates: ClientCertificates, store: NoustStore
) -> None:
    records = certificates.records
    records.add(
        "shop.example.com",
        name="ana",
        serial="1A" + "0" * 30,
        fingerprint="AA",
        not_before="2026-01-01T00:00:00+00:00",
        not_after="2027-01-01T00:00:00+00:00",
        issued_by=None,
    )

    assert certificates.revoke("shop.example.com", "1a" + "0" * 30, actor="admin") is True
    assert certificates.revoke("shop.example.com", "1A" + "0" * 30, actor="admin") is False
    assert certificates.context("shop.example.com")["revoked"] == ["1A" + "0" * 30]
    revoked = certificates.list("shop.example.com")[0]
    assert revoked.revoked and revoked.revoked_by == "admin"
    with pytest.raises(ValidationError) as raised:
        certificates.revoke("shop.example.com", "FF" * 16)
    assert raised.value.field == "serial"
    with pytest.raises(ValidationError):
        certificates.revoke("blog.example.com", "1A" + "0" * 30)  # another application's


def test_a_second_valid_certificate_of_the_same_name_is_refused_until_the_first_is_revoked(
    certificates: ClientCertificates, monkeypatch: pytest.MonkeyPatch
) -> None:
    certificates.records.add(
        "shop.example.com",
        name="ana",
        serial="2B" + "0" * 30,
        fingerprint="AA",
        not_before="2026-01-01T00:00:00+00:00",
        not_after="2027-01-01T00:00:00+00:00",
        issued_by=None,
    )

    with pytest.raises(ValidationError) as raised:
        certificates.issue("shop.example.com", "ana", days=30)

    assert "already has a certificate called ana" in raised.value.message
    certificates.revoke("shop.example.com", "2B" + "0" * 30)
    # Revoked, the name is free again: the failure now is only that there is no openssl here.
    monkeypatch.setattr(
        "noust.managers.client_certs.TlsAuthority.ensure_ca",
        lambda self: (_ for _ in ()).throw(RuntimeError("reached the authority")),
    )
    with pytest.raises(RuntimeError, match="reached the authority"):
        certificates.issue("shop.example.com", "ana", days=30)


def test_the_authority_of_one_application_is_in_a_directory_of_its_own(
    certificates: ClientCertificates,
) -> None:
    assert certificates.directory("shop.example.com") != certificates.directory("blog.example.com")
    assert certificates.directory("shop.example.com").name == "shop-example-com"
    assert (
        certificates.ca_certificate("shop.example.com") is None
    )  # nothing is made until it is needed


def test_forgetting_an_application_takes_its_authority_and_its_records(
    certificates: ClientCertificates,
) -> None:
    directory = certificates.directory("shop.example.com")
    directory.mkdir(parents=True)
    (directory / "ca.crt").write_text("x")
    certificates.records.add(
        "shop.example.com",
        name="ana",
        serial="3C" + "0" * 30,
        fingerprint="AA",
        not_before="2026-01-01T00:00:00+00:00",
        not_after="2027-01-01T00:00:00+00:00",
        issued_by=None,
    )

    certificates.forget("shop.example.com")

    assert not directory.exists() and certificates.list("shop.example.com") == []
