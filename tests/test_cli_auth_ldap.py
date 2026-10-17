# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust auth ldap``: the directories people sign in against, from the command line."""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.directory import DirectoryManager
from noust.core.audit import get_log
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from noust.web.auth import STATE_DIR_ENV
from tests.ldap_support import FakeDirectory, entry

PASSWORD = "correct horse battery staple"
SERVICE = "cn=svc,ou=system,dc=acme,dc=test"
SECRET = "the service account password"


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


@pytest.fixture(autouse=True)
def allowed(real_config: Any) -> None:
    """The directory of these tests is on this machine, which is allowed by naming it."""
    real_config.set("security.allow_private_hosts", ["localhost"])


@pytest.fixture
def server() -> Iterator[FakeDirectory]:
    entries = [
        entry(
            "uid=maria,ou=people,dc=acme,dc=test",
            uid="maria",
            userPassword="correct horse",
            entryUUID="uuid-maria",
            objectClass="person",
        ),
        entry(SERVICE, cn="svc", userPassword=SECRET, objectClass="applicationProcess"),
    ]
    with FakeDirectory(entries) as directory:
        yield directory


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


def add(
    runner: CliRunner, server: FakeDirectory, *extra: str, stdin: str | None = SECRET + "\n"
) -> str:
    return run(
        runner,
        "auth",
        "ldap",
        "set",
        "corp",
        "--host",
        "localhost",
        "--port",
        str(server.port),
        "--tls",
        "none",
        "--user-base",
        "ou=people,dc=acme,dc=test",
        "--bind-dn",
        SERVICE,
        "--password-stdin",
        *extra,
        stdin=stdin,
    )


def test_a_directory_is_added_with_its_password_from_stdin(
    runner: CliRunner, server: FakeDirectory, store: NoustStore
) -> None:
    out = add(runner, server, "--label", "Acme directory")

    assert "Directory added: corp" in out and SECRET not in out
    manager = DirectoryManager()
    assert manager.data.read_secret("corp") == SECRET
    path = manager.data.secrets.path("ldap/corp")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "noust auth ldap test corp" in out


def test_the_password_can_be_typed_at_a_hidden_prompt(
    runner: CliRunner, server: FakeDirectory
) -> None:
    run(
        runner,
        "auth",
        "ldap",
        "set",
        "corp",
        "--host",
        "localhost",
        "--port",
        str(server.port),
        "--tls",
        "none",
        "--user-base",
        "ou=people,dc=acme,dc=test",
        "--bind-dn",
        SERVICE,
        "--prompt-password",
        stdin=SECRET + "\n",
    )

    assert DirectoryManager().data.read_secret("corp") == SECRET


def test_an_empty_standard_input_is_said_not_stored(
    runner: CliRunner, server: FakeDirectory
) -> None:
    out = run(
        runner,
        "auth",
        "ldap",
        "set",
        "corp",
        "--host",
        "localhost",
        "--port",
        str(server.port),
        "--tls",
        "none",
        "--user-base",
        "ou=people,dc=acme,dc=test",
        "--bind-dn",
        SERVICE,
        "--password-stdin",
        ok=False,
        stdin="\n",
    )

    assert "No bind password" in out
    assert DirectoryManager().directories() == []


def test_show_never_shows_the_password(runner: CliRunner, server: FakeDirectory) -> None:
    add(runner, server)

    full = run(runner, "auth", "ldap", "show", "corp")
    listed = run(runner, "auth", "ldap", "show")
    as_json = json.loads(run(runner, "--json", "auth", "ldap", "show"))

    assert SECRET not in full + listed + json.dumps(as_json)
    assert "stored" in full and as_json["directories"][0]["bind_password_set"] is True


def test_a_directory_that_answers_passes_its_test(runner: CliRunner, server: FakeDirectory) -> None:
    add(runner, server)

    out = run(runner, "auth", "ldap", "test", "corp")
    body = json.loads(run(runner, "--json", "auth", "ldap", "test", "corp"))

    assert "connection:" in out and "bind:" in out and "users:" in out
    assert body["ok"] is True


def test_a_wrong_service_password_fails_the_test_with_exit_1(
    runner: CliRunner, server: FakeDirectory
) -> None:
    add(runner, server, stdin="wrong password\n")

    result = runner.invoke(root_cli, ["auth", "ldap", "test", "corp"])

    assert result.exit_code == 1 and "refused the bind" in result.output


def test_a_filter_without_the_placeholder_is_refused(
    runner: CliRunner, server: FakeDirectory
) -> None:
    out = run(
        runner,
        "auth",
        "ldap",
        "set",
        "corp",
        "--host",
        "localhost",
        "--user-base",
        "dc=acme",
        "--tls",
        "none",
        "--user-filter",
        "(uid=maria)",
        ok=False,
    )

    assert "{username}" in out
    assert DirectoryManager().directories() == []


def test_groups_are_mapped_with_the_options_every_source_has(
    runner: CliRunner, server: FakeDirectory
) -> None:
    teams = TeamStore()
    teams.create_team("Acme", slug="acme")
    AccountManager().create("root-admin", "admin", password=PASSWORD)

    out = add(
        runner,
        server,
        "--group-sync",
        "--map-role",
        "cn=ops,ou=groups,dc=acme,dc=test=operator",
        "--map-team",
        "devs=acme:operator",
    )

    directory = DirectoryManager().require_directory("corp")
    assert [m.to_line() for m in directory.group_mappings] == [
        "cn=ops,ou=groups,dc=acme,dc=test=operator",
        "devs=acme:operator",
    ]
    assert "groups decide roles" in out


def test_a_person_is_linked_by_hand(runner: CliRunner, server: FakeDirectory) -> None:
    AccountManager().create("maria", "viewer", password=PASSWORD)
    add(runner, server)

    out = run(runner, "auth", "ldap", "link", "corp", "maria", "--subject", "uuid-maria")

    assert "Linked maria to corp" in out
    identities = DirectoryManager().admission.data.list_identities()
    assert [(i.protocol, i.provider, i.subject) for i in identities] == [
        ("ldap", "corp", "uuid-maria")
    ]


def test_removing_a_directory_asks_and_forgets_the_password(
    runner: CliRunner, server: FakeDirectory
) -> None:
    add(runner, server)

    declined = run(runner, "auth", "ldap", "remove", "corp", stdin="n\n")
    assert "Cancelled" in declined and DirectoryManager().directory("corp") is not None

    run(runner, "auth", "ldap", "remove", "corp", "--force")

    manager = DirectoryManager()
    assert manager.directory("corp") is None and not manager.data.has_secret("corp")


def test_showing_nothing_says_how_to_add_one(runner: CliRunner) -> None:
    assert "noust auth ldap set" in run(runner, "auth", "ldap", "show")


def test_every_change_is_on_the_record_without_the_password(
    runner: CliRunner, server: FakeDirectory
) -> None:
    add(runner, server)
    run(runner, "auth", "ldap", "remove", "corp", "--force")

    events = [e for e in get_log().read(limit=200) if e["action"] == "auth.ldap.config"]

    assert len(events) >= 2 and SECRET not in json.dumps(events)
