# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust auth saml``: the SAML identity providers people sign in with, from the command line."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

pytest.importorskip("cryptography")

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.saml import SamlManager
from noust.core.audit import get_log
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from noust.web.auth import STATE_DIR_ENV
from tests.saml_support import make_identity
from tests.test_saml import ISSUER, SSO, metadata_xml

PASSWORD = "correct horse battery staple"
ORIGIN = "https://noust.acme.test"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    _key, pem = make_identity()
    metadata = tmp_path / "idp-metadata.xml"
    metadata.write_text(metadata_xml(), encoding="utf-8")
    certificate = tmp_path / "idp.pem"
    certificate.write_text(pem, encoding="utf-8")
    return {"metadata": metadata, "certificate": certificate}


def run(runner: CliRunner, *args: str, ok: bool = True, stdin: str | None = None) -> str:
    result = runner.invoke(root_cli, list(args), input=stdin)
    if ok:
        assert result.exit_code == 0, result.output
        return result.output
    assert result.exit_code != 0, result.output
    return result.output + str(result.exception)


def add(runner: CliRunner, files: dict[str, Path], *extra: str) -> str:
    return run(
        runner,
        "auth",
        "saml",
        "set",
        "corp",
        "--metadata-file",
        str(files["metadata"]),
        "--origin",
        ORIGIN,
        *extra,
    )


def test_a_provider_is_added_from_its_metadata(runner: CliRunner, files: dict[str, Path]) -> None:
    out = add(runner, files, "--label", "Acme SSO")

    assert "SAML provider added: corp" in out
    provider = SamlManager().require_provider("corp")
    assert (provider.idp_entity_id, provider.sso_url, provider.label) == (ISSUER, SSO, "Acme SSO")
    assert f"{ORIGIN}/api/auth/saml/acs" in out
    assert "noust auth saml metadata corp" in out and "noust auth saml test corp" in out


def test_a_provider_is_added_from_its_three_halves(
    runner: CliRunner, files: dict[str, Path]
) -> None:
    run(
        runner,
        "auth",
        "saml",
        "set",
        "corp",
        "--entity-id",
        ISSUER,
        "--sso-url",
        SSO,
        "--certificate-file",
        str(files["certificate"]),
    )

    provider = SamlManager().require_provider("corp")
    assert len(provider.certificates) == 1 and provider.enabled


def test_a_provider_without_its_certificate_is_refused(runner: CliRunner) -> None:
    out = run(
        runner,
        "auth",
        "saml",
        "set",
        "corp",
        "--entity-id",
        ISSUER,
        "--sso-url",
        SSO,
        ok=False,
    )

    assert "entity id, sign-in URL and certificate" in out
    assert SamlManager().providers() == []


def test_a_file_that_does_not_exist_is_said_not_a_traceback(runner: CliRunner) -> None:
    out = run(
        runner, "auth", "saml", "set", "corp", "--metadata-file", "/no/such/file.xml", ok=False
    )

    assert "Cannot read the metadata file" in out


def test_metadata_can_be_piped(runner: CliRunner) -> None:
    run(runner, "auth", "saml", "set", "corp", "--metadata-file", "-", stdin=metadata_xml())

    assert SamlManager().require_provider("corp").idp_entity_id == ISSUER


def test_show_lists_and_describes(runner: CliRunner, files: dict[str, Path]) -> None:
    add(runner, files, "--label", "Acme SSO")

    listed = run(runner, "auth", "saml", "show")
    full = run(runner, "auth", "saml", "show", "corp", "--origin", ORIGIN)
    as_json = json.loads(run(runner, "--json", "auth", "saml", "show"))

    assert "corp" in listed and ISSUER in listed
    assert "certificate 1" in full and "SHA-256" in full and ORIGIN in full
    assert as_json["providers"][0]["name"] == "corp"


def test_showing_nothing_says_how_to_add_one(runner: CliRunner) -> None:
    assert "noust auth saml set" in run(runner, "auth", "saml", "show")


def test_the_metadata_to_register_is_printed(runner: CliRunner, files: dict[str, Path]) -> None:
    add(runner, files)

    out = run(runner, "auth", "saml", "metadata", "corp", "--origin", ORIGIN)

    assert f'entityID="{ORIGIN}/api/auth/saml/metadata/corp"' in out
    assert f'Location="{ORIGIN}/api/auth/saml/acs"' in out


def test_the_metadata_needs_to_know_the_consoles_address(
    runner: CliRunner, files: dict[str, Path]
) -> None:
    add(runner, files)

    out = run(runner, "auth", "saml", "metadata", "corp", ok=False)

    assert "web.public_url" in out


def test_a_provider_that_is_right_passes_its_test(
    runner: CliRunner, files: dict[str, Path]
) -> None:
    add(runner, files)

    out = run(runner, "auth", "saml", "test", "corp", "--origin", ORIGIN)
    body = json.loads(run(runner, "--json", "auth", "saml", "test", "corp", "--origin", ORIGIN))

    assert "certificate:" in out and "assertion consumer address:" in out
    assert body["ok"] is True


def test_a_console_over_plain_http_fails_the_test_with_exit_1(
    runner: CliRunner, files: dict[str, Path]
) -> None:
    add(runner, files)

    result = runner.invoke(
        root_cli, ["auth", "saml", "test", "corp", "--origin", "http://noust.acme.test"]
    )

    assert result.exit_code == 1 and "TLS" in result.output


def test_groups_are_mapped_with_the_options_every_source_has(
    runner: CliRunner, files: dict[str, Path]
) -> None:
    TeamStore().create_team("Acme", slug="acme")
    AccountManager().create("root-admin", "admin", password=PASSWORD)

    out = add(
        runner,
        files,
        "--group-sync",
        "--map-role",
        "ops=operator",
        "--map-team",
        "devs=acme:operator",
        "--groups-attribute",
        "http://schemas.xmlsoap.org/claims/Group",
    )

    provider = SamlManager().require_provider("corp")
    assert [m.to_line() for m in provider.group_mappings] == ["ops=operator", "devs=acme:operator"]
    assert provider.groups_attribute == "http://schemas.xmlsoap.org/claims/Group"
    assert "groups decide roles" in out


def test_an_open_provisioning_is_warned_about(runner: CliRunner, files: dict[str, Path]) -> None:
    out = add(runner, files, "--provisioning", "auto", "--default-role", "operator")

    assert "gets an account as operator" in out


def test_a_person_is_linked_by_hand(runner: CliRunner, files: dict[str, Path]) -> None:
    AccountManager().create("maria", "viewer", password=PASSWORD)
    add(runner, files)

    out = run(runner, "auth", "saml", "link", "corp", "maria", "--subject", "uuid-maria")

    assert "Linked maria to corp" in out
    identities = SamlManager().admission.data.list_identities()
    assert [(i.protocol, i.provider, i.subject) for i in identities] == [
        ("saml", "corp", "uuid-maria")
    ]


def test_removing_a_provider_asks(runner: CliRunner, files: dict[str, Path]) -> None:
    add(runner, files)

    declined = run(runner, "auth", "saml", "remove", "corp", stdin="n\n")
    assert "Cancelled" in declined and SamlManager().provider("corp") is not None

    run(runner, "auth", "saml", "remove", "corp", "--force")

    assert SamlManager().provider("corp") is None


def test_every_change_is_on_the_record(runner: CliRunner, files: dict[str, Path]) -> None:
    add(runner, files)
    AccountManager().create("maria", "viewer", password=PASSWORD)
    run(runner, "auth", "saml", "link", "corp", "maria", "--subject", "uuid-maria")
    run(runner, "auth", "saml", "remove", "corp", "--force")

    actions = [e["action"] for e in get_log().read(limit=200)]

    assert actions.count("auth.saml.config") >= 2 and "auth.saml.link" in actions


def test_the_name_must_not_be_another_providers(runner: CliRunner, files: dict[str, Path]) -> None:
    from noust.core.accounts.oidc import OidcManager

    OidcManager().configure("corp", {"issuer": "https://sso.acme.test", "client_id": "x"})

    out = run(
        runner,
        "auth",
        "saml",
        "set",
        "corp",
        "--metadata-file",
        str(files["metadata"]),
        ok=False,
    )

    assert "already a sign-in provider" in out
