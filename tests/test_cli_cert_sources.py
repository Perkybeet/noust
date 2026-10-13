# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``noust cert acme|upload|uploads|remove-upload|use|source``.

The managers decide (``tests/test_cert_network.py``, ``tests/test_custom_certificates.py``,
``tests/test_certificate_choice.py``). Pinned here is the translation: a private key and
the HMAC key of an external account are read from a file or standard input and never
an argument, ``acme`` only changes what it is told to change and is a read otherwise, a
choice needs exactly one source, and a refusal is exit code 1 with the operation's own
words.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import cert_sources
from noust.core.certificate_store import CustomCertificate
from noust.core.exceptions import ValidationError
from noust.core.logger import Logger
from noust.core.secrets import SecretStore
from noust.deployers.certificates import CertificateChange, CertificateView
from noust.managers.acme import EAB_KEY_SECRET, AcmeSettings

APP = "example.com"
HMAC = "hmac-" + "k" * 40


class MemoryConfig:
    """A configuration that lives in memory: ``get``, ``set`` and ``write``."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.written = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def write(self) -> None:
        self.written += 1


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def invoke(args: list[str], input_text: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=input_text)


# -- the authority ----------------------------------------------------------------


@pytest.fixture
def acme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    config = MemoryConfig()
    secrets = SecretStore(root=tmp_path / "secrets")
    registered: list[bool] = []

    class Manager:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def register_account(self) -> bool:
            registered.append(True)
            return True

    monkeypatch.setattr(
        cert_sources,
        "AcmeSettings",
        lambda: AcmeSettings(config=config, secrets=secrets),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(cert_sources, "CertManager", Manager)
    return {"config": config, "secrets": secrets, "registered": registered}


def test_showing_the_authority_changes_and_writes_nothing(acme: dict[str, Any]) -> None:
    result = invoke(["cert", "acme", "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["ca"] == "letsencrypt" and body["trusted"] is True
    assert acme["config"].written == 0


def test_only_what_is_named_changes(acme: dict[str, Any]) -> None:
    result = invoke(["cert", "acme", "--ca", "letsencrypt-staging", "--email", "ops@example.com"])

    assert result.exit_code == 0, result.output
    assert acme["config"].values == {
        "ssl.ca": "letsencrypt-staging",
        "ssl.email": "ops@example.com",
    }


def test_staging_is_said_to_be_untrusted_by_browsers(acme: dict[str, Any], log: list[str]) -> None:
    invoke(["cert", "acme", "--ca", "letsencrypt-staging"])

    assert any("browsers do not trust" in line for line in log)


def test_the_hmac_key_comes_from_standard_input_and_lives_only_in_the_secret_store(
    acme: dict[str, Any], log: list[str]
) -> None:
    result = invoke(
        [
            "cert",
            "acme",
            "--ca",
            "zerossl",
            "--eab-kid",
            "kid-1",
            "--eab-hmac-key-stdin",
            "--email",
            "ops@example.com",
        ],
        input_text=HMAC + "\n",
    )

    assert result.exit_code == 0, result.output
    assert acme["secrets"].read(EAB_KEY_SECRET) == HMAC
    assert HMAC not in result.output
    assert not any(HMAC in line for line in log)
    assert HMAC not in json.dumps(acme["config"].values)


def test_there_is_no_option_to_give_the_hmac_key_as_an_argument(acme: dict[str, Any]) -> None:
    result = invoke(["cert", "acme", "--eab-hmac-key", HMAC])

    assert result.exit_code == 2
    assert HMAC not in acme["config"].values.values()


def test_a_custom_authority_without_its_directory_is_refused(
    acme: dict[str, Any], log: list[str]
) -> None:
    assert main(["cert", "acme", "--ca", "custom"]) == 1
    assert any("directory URL" in line for line in log)


def test_registering_proves_the_account_now(acme: dict[str, Any]) -> None:
    result = invoke(["cert", "acme", "--register"])

    assert result.exit_code == 0, result.output
    assert acme["registered"] == [True]


def test_a_rehearsal_changes_no_setting(acme: dict[str, Any]) -> None:
    result = invoke(["--dry-run", "cert", "acme", "--ca", "zerossl"])

    assert result.exit_code == 0, result.output
    assert acme["config"].values == {}


# -- uploads ----------------------------------------------------------------------


def kept(name: str = "origin") -> CustomCertificate:
    return CustomCertificate(
        name=name,
        names=("example.com", "*.example.com"),
        subject="CN=example.com",
        issuer="CN=Origin CA",
        key_type="EC prime256v1",
        fingerprint="AA:BB",
        not_before="2026-01-01T00:00:00+00:00",
        not_after="2030-01-01T00:00:00+00:00",
        uploaded_by="cli",
        uploaded_at="2026-10-13T00:00:00+00:00",
    )


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class Fake:
        records = SimpleNamespace(domains_using=lambda source, name: ["example.com"])

        def __init__(self) -> None:
            pass

        def upload(self, *args: Any, **kwargs: Any) -> CustomCertificate:
            calls.append(("upload", args, kwargs))
            return kept(args[0])

        def uploaded(self) -> list[CustomCertificate]:
            return [kept()]

        def remove(self, name: str) -> None:
            calls.append(("remove", (name,), {}))

    monkeypatch.setattr(cert_sources, "CustomCertificates", Fake)
    return calls


def test_the_certificate_and_the_key_are_read_from_files(
    uploads: list[Any], tmp_path: Path
) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("CERT PEM")
    key.write_text("KEY PEM")

    result = invoke(["cert", "upload", "origin", "--cert", str(cert), "--key", str(key), "--json"])

    assert result.exit_code == 0, result.output
    name, args, kwargs = uploads[-1]
    assert (name, args) == ("upload", ("origin", "CERT PEM", "KEY PEM", ""))
    assert kwargs["uploaded_by"]
    body = json.loads(result.output)
    assert body["name"] == "origin" and body["names"] == ["example.com", "*.example.com"]
    assert "KEY PEM" not in result.output


def test_the_key_can_come_from_standard_input(uploads: list[Any], tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_text("CERT PEM")

    result = invoke(
        ["cert", "upload", "origin", "--cert", str(cert), "--key", "-"], input_text="KEY FROM STDIN"
    )

    assert result.exit_code == 0, result.output
    assert uploads[-1][1][2] == "KEY FROM STDIN"


def test_the_certificate_and_the_key_cannot_both_come_from_standard_input(
    uploads: list[Any],
) -> None:
    result = invoke(["cert", "upload", "origin", "--cert", "-", "--key", "-"], input_text="x")

    assert result.exit_code == 2
    assert uploads == []


def test_a_file_that_cannot_be_read_is_refused_with_its_name(
    uploads: list[Any], tmp_path: Path, log: list[str]
) -> None:
    key = tmp_path / "key.pem"
    key.write_text("KEY PEM")
    missing = tmp_path / "nope.pem"

    assert main(["cert", "upload", "origin", "--cert", str(missing), "--key", str(key)]) == 1
    assert any(str(missing) in line for line in log)
    assert uploads == []


def test_a_file_far_too_large_to_be_a_pem_is_refused(
    uploads: list[Any], tmp_path: Path, log: list[str]
) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("A" * (cert_sources._MAX_FILE + 1))
    key.write_text("KEY PEM")

    assert main(["cert", "upload", "origin", "--cert", str(cert), "--key", str(key)]) == 1
    assert uploads == []


def test_the_managers_refusal_reaches_the_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log: list[str]
) -> None:
    class Refusing:
        def __init__(self) -> None:
            pass

        def upload(self, *args: Any, **kwargs: Any) -> None:
            raise ValidationError("The key is not the certificate's")

    monkeypatch.setattr(cert_sources, "CustomCertificates", Refusing)
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("CERT PEM")
    key.write_text("KEY PEM")

    assert main(["cert", "upload", "origin", "--cert", str(cert), "--key", str(key)]) == 1
    assert any("not the certificate's" in line for line in log)


def test_a_rehearsal_keeps_nothing(uploads: list[Any], tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("CERT PEM")
    key.write_text("KEY PEM")

    result = invoke(
        ["--dry-run", "cert", "upload", "origin", "--cert", str(cert), "--key", str(key)]
    )

    assert result.exit_code == 0, result.output
    assert uploads == []


def test_the_uploads_are_listed_with_who_serves_them_and_never_their_keys(
    uploads: list[Any],
) -> None:
    result = invoke(["cert", "uploads", "--json"])

    assert result.exit_code == 0, result.output
    (row,) = json.loads(result.output)["certificates"]
    assert row["name"] == "origin" and row["used_by"] == ["example.com"]
    assert "key" not in " ".join(row)


def test_removing_an_upload_asks_unless_told_not_to(uploads: list[Any]) -> None:
    declined = invoke(["cert", "remove-upload", "origin"], input_text="n\n")
    assert declined.exit_code == 0
    assert uploads == []

    confirmed = invoke(["cert", "remove-upload", "origin", "--yes"])
    assert confirmed.exit_code == 0, confirmed.output
    assert uploads == [("remove", ("origin",), {})]


# -- the choice -------------------------------------------------------------------


def view(**changes: Any) -> CertificateView:
    fields: dict[str, Any] = {
        "app": APP,
        "source": "acme",
        "name": None,
        "challenge": "http-01",
        "dns_provider": None,
        "authority": "Let's Encrypt",
        "tls": True,
        "names": (APP,),
        "certificate": None,
    }
    fields.update(changes)
    return CertificateView(**fields)


@pytest.fixture
def choice(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def use_acme(*args: Any, **kwargs: Any) -> CertificateChange:
        calls.append(("use_acme", args, kwargs))
        return CertificateChange(
            view=view(challenge=kwargs["challenge"], dns_provider=kwargs["dns_provider"]),
            ordered=kwargs["order"],
        )

    def use_uploaded(*args: Any, **kwargs: Any) -> CertificateChange:
        calls.append(("use_uploaded", args, kwargs))
        return CertificateChange(view=view(source="custom", name=args[1]))

    def describe(*args: Any, **kwargs: Any) -> CertificateView:
        calls.append(("describe", args, kwargs))
        return view(
            certificate={
                "name": APP,
                "expires_on": "2026-12-01",
                "days_remaining": 49,
                "issuer": "R11",
            }
        )

    monkeypatch.setattr(cert_sources.certificates, "use_acme", use_acme)
    monkeypatch.setattr(cert_sources.certificates, "use_uploaded", use_uploaded)
    monkeypatch.setattr(cert_sources.certificates, "describe", describe)
    return calls


def test_ordering_over_dns_names_the_account(choice: list[Any]) -> None:
    result = invoke(["cert", "use", APP, "--acme", "--dns", "cf", "--json"])

    assert result.exit_code == 0, result.output
    _, args, kwargs = choice[-1]
    assert args == (APP,)
    assert (kwargs["challenge"], kwargs["dns_provider"], kwargs["order"]) == ("dns-01", "cf", True)
    assert json.loads(result.output)["ordered"] is True


def test_ordering_over_http_names_no_account(choice: list[Any]) -> None:
    invoke(["cert", "use", APP, "--acme"])

    _, _, kwargs = choice[-1]
    assert (kwargs["challenge"], kwargs["dns_provider"]) == ("http-01", None)


def test_no_order_only_keeps_the_choice(choice: list[Any]) -> None:
    invoke(["cert", "use", APP, "--acme", "--dns", "cf", "--no-order"])

    assert choice[-1][2]["order"] is False


def test_serving_an_upload(choice: list[Any]) -> None:
    result = invoke(["cert", "use", APP, "--upload", "origin"])

    assert result.exit_code == 0, result.output
    assert choice[-1][:2] == ("use_uploaded", (APP, "origin"))


def test_a_source_is_required_and_there_is_only_one(choice: list[Any]) -> None:
    neither = invoke(["cert", "use", APP])
    both = invoke(["cert", "use", APP, "--acme", "--upload", "origin"])

    assert neither.exit_code == 2 and both.exit_code == 2
    assert choice == []


def test_dns_is_for_an_ordered_certificate_not_an_uploaded_one(choice: list[Any]) -> None:
    result = invoke(["cert", "use", APP, "--upload", "origin", "--dns", "cf"])

    assert result.exit_code == 2
    assert choice == []


def test_a_rehearsal_chooses_nothing(choice: list[Any]) -> None:
    result = invoke(["--dry-run", "cert", "use", APP, "--acme", "--dns", "cf"])

    assert result.exit_code == 0, result.output
    assert choice == []


def test_the_source_of_a_certificate_is_a_read(choice: list[Any], log: list[str]) -> None:
    result = invoke(["cert", "source", APP])

    assert result.exit_code == 0, result.output
    assert choice[-1][0] == "describe"
    said = " ".join(log)
    assert "Let's Encrypt" in said and "49 days" in said
