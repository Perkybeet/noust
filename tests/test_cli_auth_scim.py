# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust auth scim``: the identity providers that provision accounts, from the command line."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.scim import SCHEMA_USER, ScimManager
from noust.core.audit import get_log
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from noust.web.auth import STATE_DIR_ENV
from noust.web.permissions.access import AccessManager

PASSWORD = "correct horse battery staple"


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
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def run(runner: CliRunner, *args: str, ok: bool = True, stdin: str | None = None) -> str:
    result = runner.invoke(root_cli, list(args), input=stdin)
    if ok:
        assert result.exit_code == 0, result.output
        return result.output
    assert result.exit_code != 0, result.output
    return result.output + str(result.exception)


def token_of(output: str) -> str:
    return next(
        line.split("Token:", 1)[1].strip()
        for line in output.splitlines()
        if line.startswith("Token:")
    )


def test_a_new_source_prints_its_token_once(runner: CliRunner) -> None:
    out = run(runner, "auth", "scim", "set", "entra", "--label", "Entra ID")

    token = token_of(out)
    assert token.startswith("noust_scim_")
    assert "only time the token is shown" in out and "/api/scim/v2" in out
    shown = run(runner, "auth", "scim", "show", "entra")
    assert token not in shown and token[-4:] in shown
    assert ScimManager().authenticate(token) is not None


def test_changing_a_setting_keeps_the_token_and_rotating_prints_a_new_one(
    runner: CliRunner,
) -> None:
    first = token_of(run(runner, "auth", "scim", "set", "entra"))

    again = run(runner, "auth", "scim", "set", "entra", "--label", "Acme")
    rotated = token_of(run(runner, "auth", "scim", "set", "entra", "--rotate-token"))

    assert "Token:" not in again
    assert rotated != first
    manager = ScimManager()
    assert manager.authenticate(first) is None and manager.authenticate(rotated) is not None


def test_the_json_form_has_the_token_only_when_one_was_issued(runner: CliRunner) -> None:
    added = json.loads(run(runner, "--json", "auth", "scim", "set", "entra"))
    shown = json.loads(run(runner, "--json", "auth", "scim", "show"))

    assert added["token"].startswith("noust_scim_") and added["endpoint"].endswith("/api/scim/v2")
    assert "token" not in shown["sources"][0] and "token_hash" not in json.dumps(shown)
    assert shown["sources"][0]["users"] == 0


def test_a_dry_run_says_the_token_was_not_saved(runner: CliRunner) -> None:
    out = run(runner, "--dry-run", "auth", "scim", "set", "entra")

    assert "not saved" in out.lower()
    assert ScimManager().sources() == []


def test_the_settings_are_validated(runner: CliRunner) -> None:
    TeamStore().create_team("Acme", slug="acme")
    AccountManager().create("root-admin", "admin", password=PASSWORD)

    ok = run(
        runner,
        "auth",
        "scim",
        "set",
        "entra",
        "--default-role",
        "operator",
        "--default-team",
        "acme",
        "--default-team-role",
        "operator",
        "--allowed-domain",
        "acme.test",
        "--link-by-email",
    )
    refused = run(runner, "auth", "scim", "set", "okta", "--default-team", "ghost", ok=False)

    assert "operator" in ok and "acme.test" in ok
    assert "ghost" in refused
    assert [source.name for source in ScimManager().sources()] == ["entra"]


def test_groups_are_mapped_with_the_same_options_as_every_source(runner: CliRunner) -> None:
    teams = TeamStore()
    teams.create_team("Acme", slug="acme")
    teams.create_project("acme", "Web", slug="web")
    AccessManager().create_role("release-manager", ["apps.read", "apps.deploy"])
    AccountManager().create("root-admin", "admin", password=PASSWORD)

    out = run(
        runner,
        "auth",
        "scim",
        "set",
        "entra",
        "--group-sync",
        "--map-role",
        "noust-ops=operator",
        "--map-team",
        "acme-ops=acme:operator",
        "--map-binding",
        "acme-release=release-manager@acme/web",
    )

    source = ScimManager().require_source("entra")
    assert source.group_sync is True
    assert [m.to_line() for m in source.group_mappings] == [
        "noust-ops=operator",
        "acme-ops=acme:operator",
        "acme-release=release-manager@acme/web",
    ]
    assert "role release-manager on acme/web" in out
    cleared = run(runner, "auth", "scim", "set", "entra", "--clear-mappings", "--no-group-sync")
    assert "groups decide roles" in cleared
    assert ScimManager().require_source("entra").group_mappings == ()


def test_a_flag_given_both_ways_is_refused(runner: CliRunner) -> None:
    out = run(runner, "auth", "scim", "set", "entra", "--enable", "--disable", ok=False)

    assert "--enable/--disable" in out


def test_the_roster_lists_who_was_provisioned_and_how_their_account_stands(
    runner: CliRunner,
) -> None:
    AccountManager().create("root-admin", "admin", password=PASSWORD)
    run(runner, "auth", "scim", "set", "entra")
    manager = ScimManager()
    service = manager.service(
        manager.require_source("entra"), base_url="/api/scim/v2", client_ip="127.0.0.1"
    )
    service.create_user({"schemas": [SCHEMA_USER], "userName": "maria@acme.test"})
    service.patch_user(
        service.list_users(None, None, None)["Resources"][0]["id"],
        {"Operations": [{"op": "replace", "path": "active", "value": False}]},
    )

    out = run(runner, "auth", "scim", "users", "entra")
    body = json.loads(run(runner, "--json", "auth", "scim", "users", "entra"))

    assert "maria" in out and "disabled (by SCIM)" in out
    assert body["total"] == 1 and body["users"][0]["disabled_by_scim"] is True


def test_removing_a_source_asks_and_keeps_the_accounts(runner: CliRunner) -> None:
    AccountManager().create("root-admin", "admin", password=PASSWORD)
    token = token_of(run(runner, "auth", "scim", "set", "entra"))
    manager = ScimManager()
    manager.service(
        manager.require_source("entra"), base_url="/api/scim/v2", client_ip="127.0.0.1"
    ).create_user({"schemas": [SCHEMA_USER], "userName": "maria@acme.test"})

    declined = run(runner, "auth", "scim", "remove", "entra", stdin="n\n")
    assert "Cancelled" in declined and ScimManager().authenticate(token) is not None

    run(runner, "auth", "scim", "remove", "entra", "--force")

    assert ScimManager().authenticate(token) is None
    assert AccountManager().find("maria") is not None


def test_showing_nothing_says_how_to_add_a_source(runner: CliRunner) -> None:
    assert "noust auth scim set" in run(runner, "auth", "scim", "show")


def test_every_change_is_on_the_record_without_the_token(runner: CliRunner) -> None:
    token = token_of(run(runner, "auth", "scim", "set", "entra"))
    run(runner, "auth", "scim", "remove", "entra", "--force")

    events = [e for e in get_log().read(limit=200) if e["action"] == "auth.scim.config"]

    assert len(events) >= 2 and token not in json.dumps(events)
