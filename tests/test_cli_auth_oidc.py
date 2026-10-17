"""
``noust auth oidc``: single sign-in set up from the root CLI.

A client of the same :class:`~noust.core.accounts.oidc.OidcManager` the console
uses. The client secret never travels in argv (a hidden prompt, or ``--client-secret-stdin``),
is kept in a 0600 file, and is never printed.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

pytest.importorskip("cryptography")

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.oidc import OidcManager
from noust.core.accounts.oidc_client import OidcClient, OidcHttp, set_client
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from noust.web.auth import STATE_DIR_ENV
from noust.web.permissions.access import AccessManager
from tests.oidc_support import FakeIdP

PASSWORD = "correct horse battery staple"
SECRET = "the-client-secret-value"


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
def idp() -> Iterator[FakeIdP]:
    provider = FakeIdP(client_secret=SECRET)
    set_client(OidcClient(OidcHttp(opener=provider.opener), clock=provider.clock))
    yield provider
    set_client(None)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def run(runner: CliRunner, *args: str, stdin: str | None = None, ok: bool = True) -> str:
    result = runner.invoke(root_cli, list(args), input=stdin)
    if ok:
        assert result.exit_code == 0, result.output
        return result.output
    assert result.exit_code != 0, result.output
    # The error reaches the CLI's own boundary, which prints it; CliRunner stops short of it.
    return result.output + str(result.exception)


def add(runner: CliRunner, idp: FakeIdP, *extra: str) -> str:
    return run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        idp.issuer,
        "--client-id",
        idp.client_id,
        "--client-secret-stdin",
        *extra,
        stdin=SECRET + "\n",
    )


def test_a_provider_is_added_with_its_secret_from_stdin(
    runner: CliRunner, idp: FakeIdP, store: NoustStore
) -> None:
    output = add(runner, idp, "--label", "Acme SSO", "--allowed-domain", "acme.test")

    assert "Provider added: corp" in output
    assert SECRET not in output
    assert "client secret" in output and "set" in output
    provider = OidcManager().provider("corp")
    assert provider is not None and provider.label == "Acme SSO"
    assert provider.allowed_domains == ("acme.test",)
    path = store.db_path.parent / "secrets" / "oidc" / "corp"
    assert path.read_text(encoding="utf-8") == SECRET
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_there_is_no_secret_option_in_argv(runner: CliRunner, idp: FakeIdP) -> None:
    output = run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        idp.issuer,
        "--client-id",
        "x",
        "--client-secret",
        "hunter2",
        ok=False,
    )
    assert "hunter2" not in output.replace("--client-secret", "")
    assert OidcManager().provider("corp") is None


def test_the_secret_can_be_typed_at_a_hidden_prompt(runner: CliRunner, idp: FakeIdP) -> None:
    output = run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        idp.issuer,
        "--client-id",
        "x",
        "--prompt-secret",
        stdin="typed-secret\n",
    )
    assert "typed-secret" not in output
    assert OidcManager().data.read_secret("corp") == "typed-secret"


def test_an_empty_secret_on_stdin_is_refused(runner: CliRunner, idp: FakeIdP) -> None:
    output = run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        idp.issuer,
        "--client-id",
        "x",
        "--client-secret-stdin",
        stdin="\n",
        ok=False,
    )
    assert "No client secret on standard input" in output
    assert OidcManager().provider("corp") is None


def test_a_change_keeps_everything_it_does_not_name(runner: CliRunner, idp: FakeIdP) -> None:
    add(runner, idp, "--provisioning", "invite")

    output = run(runner, "auth", "oidc", "set", "corp", "--label", "Login")

    assert "Provider changed: corp" in output
    provider = OidcManager().provider("corp")
    assert provider is not None
    assert (provider.label, provider.provisioning) == ("Login", "invite")
    assert OidcManager().has_secret("corp")


def test_show_never_prints_the_secret(runner: CliRunner, idp: FakeIdP) -> None:
    add(runner, idp)

    table = run(runner, "auth", "oidc", "show")
    detail = run(runner, "auth", "oidc", "show", "corp")
    as_json = run(runner, "--json", "auth", "oidc", "show")

    for text in (table, detail, as_json):
        assert SECRET not in text
    assert "corp" in table and "enabled" in table
    assert "redirect URI to register" in detail
    body = json.loads(as_json)
    assert body["providers"][0]["client_secret_set"] is True
    assert "client_secret" not in body["providers"][0]


def test_show_says_where_to_register_the_callback_once_the_public_url_is_known(
    runner: CliRunner, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.config import Config

    real = Config.get
    monkeypatch.setattr(
        Config,
        "get",
        lambda self, key, default=None: (
            "https://noust.example.test" if key == "web.public_url" else real(self, key, default)
        ),
    )
    add(runner, idp)

    assert "https://noust.example.test/api/auth/oidc/callback" in run(
        runner, "auth", "oidc", "show", "corp"
    )


def test_show_with_nothing_configured_says_how_to_start(runner: CliRunner) -> None:
    assert "noust auth oidc set" in run(runner, "auth", "oidc", "show")


def test_the_test_command_checks_the_provider_and_exits_with_the_verdict(
    runner: CliRunner, idp: FakeIdP
) -> None:
    add(runner, idp)

    output = run(runner, "auth", "oidc", "test", "corp")
    assert "discovery" in output and "signing keys" in output

    idp.offline = True
    result = runner.invoke(root_cli, ["auth", "oidc", "test", "corp"])
    assert result.exit_code == 1
    assert "could not be reached" in result.output


def test_the_test_command_has_a_json_form(runner: CliRunner, idp: FakeIdP) -> None:
    add(runner, idp)
    body = json.loads(run(runner, "--json", "auth", "oidc", "test", "corp"))
    assert body["ok"] is True and {c["name"] for c in body["checks"]} >= {"discovery"}


def test_group_mappings_are_given_as_options(runner: CliRunner, idp: FakeIdP) -> None:
    TeamStore().create_team("Acme", slug="acme")
    AccountManager().create("root-admin", "admin", password=PASSWORD)
    add(
        runner,
        idp,
        "--group-sync",
        "--groups-claim",
        "roles",
        "--map-role",
        "noust-admins=admin",
        "--map-team",
        "acme-ops=acme:operator",
    )

    provider = OidcManager().provider("corp")

    assert provider is not None and provider.group_sync is True
    assert provider.groups_claim == "roles"
    assert [m.to_dict() for m in provider.group_mappings] == [
        {"group": "noust-admins", "role": "admin"},
        {"group": "acme-ops", "team": "acme", "team_role": "operator"},
    ]
    run(runner, "auth", "oidc", "set", "corp", "--clear-mappings", "--no-group-sync")
    cleared = OidcManager().provider("corp")
    assert cleared is not None and cleared.group_mappings == () and not cleared.group_sync


def test_a_group_can_give_a_role_at_a_project(runner: CliRunner, idp: FakeIdP) -> None:
    teams = TeamStore()
    teams.create_team("Acme", slug="acme")
    teams.create_project("acme", "Web", slug="web")
    AccessManager().create_role("release-manager", ["apps.read", "apps.deploy"])
    AccountManager().create("root-admin", "admin", password=PASSWORD)
    add(
        runner,
        idp,
        "--group-sync",
        "--map-binding",
        "acme-release=release-manager@acme/web",
        "--map-binding",
        "cn=qa,ou=groups=viewer@acme/web",
    )

    provider = OidcManager().provider("corp")

    assert provider is not None
    assert [m.to_line() for m in provider.group_mappings] == [
        "acme-release=release-manager@acme/web",
        "cn=qa,ou=groups=viewer@acme/web",
    ]
    shown = run(runner, "auth", "oidc", "show", "corp")
    assert "role release-manager on acme/web" in shown


def test_a_role_at_a_scope_that_does_not_exist_is_refused(
    runner: CliRunner, idp: FakeIdP
) -> None:
    TeamStore().create_team("Acme", slug="acme")
    AccountManager().create("root-admin", "admin", password=PASSWORD)

    output = run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        idp.issuer,
        "--client-id",
        "x",
        "--map-binding",
        "acme-release=release-manager@acme/web",
        ok=False,
    )

    assert "release-manager" in output and "does not exist" in output


def test_a_mapping_that_is_not_of_its_shape_is_refused(runner: CliRunner, idp: FakeIdP) -> None:
    output = run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        idp.issuer,
        "--client-id",
        "x",
        "--map-role",
        "no-equals-sign",
        ok=False,
    )
    assert "GROUP=ROLE" in output or "Not GROUP=ROLE" in output


def test_both_sides_of_a_switch_are_refused(runner: CliRunner, idp: FakeIdP) -> None:
    output = run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        idp.issuer,
        "--client-id",
        "x",
        "--enable",
        "--disable",
        ok=False,
    )
    assert "only one" in output


def test_an_email_the_provider_never_vouches_for_can_be_assumed(
    runner: CliRunner, idp: FakeIdP
) -> None:
    add(runner, idp, "--assume-email-verified")
    provider = OidcManager().provider("corp")
    assert provider is not None and provider.assume_email_verified is True
    assert "assumed verified" in run(runner, "auth", "oidc", "show", "corp")

    run(runner, "auth", "oidc", "set", "corp", "--no-assume-email-verified")
    again = OidcManager().provider("corp")
    assert again is not None and again.assume_email_verified is False


def test_a_bad_issuer_is_refused_and_nothing_is_written(runner: CliRunner) -> None:
    run(
        runner,
        "auth",
        "oidc",
        "set",
        "corp",
        "--issuer",
        "http://idp.example.test",
        "--client-id",
        "x",
        ok=False,
    )
    assert OidcManager().provider("corp") is None


def test_auto_provisioning_warns_that_everyone_gets_an_account(
    runner: CliRunner, idp: FakeIdP
) -> None:
    output = add(runner, idp, "--provisioning", "auto", "--default-role", "viewer")
    assert "gets an account as viewer" in output


def test_a_provider_is_removed_with_its_secret(
    runner: CliRunner, idp: FakeIdP, store: NoustStore
) -> None:
    add(runner, idp)

    assert "Cancelled" in run(runner, "auth", "oidc", "remove", "corp", stdin="n\n")
    assert OidcManager().provider("corp") is not None
    run(runner, "auth", "oidc", "remove", "corp", "--force")

    assert OidcManager().provider("corp") is None
    assert not (store.db_path.parent / "secrets" / "oidc" / "corp").exists()


def test_people_are_linked_listed_and_unlinked(runner: CliRunner, idp: FakeIdP) -> None:
    AccountManager().create("maria", "operator", password=PASSWORD)
    add(runner, idp)

    run(runner, "auth", "oidc", "link", "corp", "maria", "--subject", "sub-maria")
    listing = run(runner, "auth", "oidc", "identities")
    assert "maria" in listing and "sub-maria" in listing
    as_json: dict[str, Any] = json.loads(run(runner, "--json", "auth", "oidc", "identities"))
    identity = as_json["identities"][0]
    assert identity["account"] == "maria"
    assert "no other link" not in run(runner, "auth", "oidc", "unlink", str(identity["id"]))
    assert "No account is linked" in run(runner, "auth", "oidc", "identities")


def test_unlinking_the_only_way_in_of_a_passwordless_account_warns(
    runner: CliRunner, idp: FakeIdP
) -> None:
    manager = AccountManager()
    manager.create_external("maria", "operator", created_by="test")
    add(runner, idp)
    run(runner, "auth", "oidc", "link", "corp", "maria", "--subject", "sub-maria")
    identity = OidcManager().identities("maria")[0]

    output = run(runner, "auth", "oidc", "unlink", str(identity.id))

    assert "cannot sign in" in output and "noust user invite maria" in output


def test_linking_the_same_person_twice_is_refused(runner: CliRunner, idp: FakeIdP) -> None:
    AccountManager().create("maria", "operator", password=PASSWORD)
    AccountManager().create("juan", "operator", password=PASSWORD)
    add(runner, idp)
    run(runner, "auth", "oidc", "link", "corp", "maria", "--subject", "sub-maria")

    run(runner, "auth", "oidc", "link", "corp", "juan", "--subject", "sub-maria", ok=False)


def test_every_change_is_on_the_audit_trail_without_the_secret(
    runner: CliRunner, idp: FakeIdP, state_dir: Path
) -> None:
    add(runner, idp)
    run(runner, "auth", "oidc", "remove", "corp", "--force")

    text = "\n".join(path.read_text(encoding="utf-8") for path in state_dir.glob("*.log"))
    assert "auth.oidc.config" in text
    assert SECRET not in text


def test_read_only_commands_are_declared_so() -> None:
    import click

    from noust.cli.app import cli

    for path in (
        ("auth", "oidc", "show"),
        ("auth", "oidc", "test"),
        ("auth", "oidc", "identities"),
    ):
        command: click.Command | None = cli
        for name in path:
            assert isinstance(command, click.Group)
            command = command.get_command(click.Context(command), name)
        assert getattr(command, "read_only", False) is True, path
    for path in (("auth", "oidc", "set"), ("auth", "oidc", "remove"), ("auth", "oidc", "link")):
        command = cli
        for name in path:
            assert isinstance(command, click.Group)
            command = command.get_command(click.Context(command), name)
        assert getattr(command, "read_only", False) is False, path
