"""
``noust user``: accounts from the root CLI.

The CLI is the channel the first account is created through and access is
recovered by, so it is a client of the same :class:`AccountManager` the
console uses: an account made here signs in there. Passwords never travel in
argv - a hidden prompt or ``--stdin`` - because every local user can read
another's command line in ``ps``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.store import NoustStore
from noust.web.auth import STATE_DIR_ENV, SecurityConfig, TokenManager

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The console's state (and the audit trail) inside the test's directory."""
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> CliRunner:
    """A Click runner."""
    return CliRunner()


def run(runner: CliRunner, *args: str, stdin: str | None = None) -> str:
    """
    Run a command, expecting it to succeed.

    Args:
        runner: The Click runner.
        *args: The command line after ``noust``.
        stdin: What to pipe in.

    Returns:
        Its output.
    """
    result = runner.invoke(root_cli, list(args), input=stdin)
    assert result.exit_code == 0, result.output
    return result.output


def test_create_reads_the_password_from_stdin(runner: CliRunner) -> None:
    output = run(
        runner, "user", "create", "maria", "--role", "admin", "--stdin", stdin=PASSWORD + "\n"
    )

    assert "Account created: maria (admin)" in output
    account = AccountManager().find("maria")
    assert account is not None and account.role == "admin"
    assert AccountManager().authenticate("maria", PASSWORD, None, client_ip="local")


def test_create_prompts_twice_without_echo(runner: CliRunner) -> None:
    output = run(
        runner, "user", "create", "maria", "--role", "viewer", stdin=f"{PASSWORD}\n{PASSWORD}\n"
    )

    assert PASSWORD not in output
    assert AccountManager().find("maria") is not None


def test_there_is_no_password_option(runner: CliRunner) -> None:
    result = runner.invoke(
        root_cli, ["user", "create", "maria", "--role", "admin", "--password", "x"]
    )

    assert result.exit_code != 0
    assert AccountManager().find("maria") is None


def test_a_weak_password_is_refused_with_the_reason(runner: CliRunner) -> None:
    result = runner.invoke(
        root_cli, ["user", "create", "maria", "--role", "admin", "--stdin"], input="short\n"
    )

    # The error reaches the CLI's own boundary (noust.cli.app.main), which
    # prints the message and the details; CliRunner stops short of it.
    assert result.exit_code != 0
    assert "shorter than" in str(result.exception)


def test_list_shows_accounts_and_json(runner: CliRunner) -> None:
    assert "No accounts yet" in run(runner, "user", "list")
    run(runner, "user", "create", "maria", "--role", "operator", "--stdin", stdin=PASSWORD + "\n")

    table = run(runner, "user", "list")
    data = json.loads(run(runner, "--json", "user", "list"))

    assert "maria" in table and "operator" in table
    assert data["accounts"][0]["username"] == "maria"
    assert "password_hash" not in data["accounts"][0]


def test_invite_prints_a_single_use_code(runner: CliRunner) -> None:
    output = run(runner, "user", "invite", "lucia", "--role", "auditor")

    code = next(line.split("Code: ", 1)[1] for line in output.splitlines() if "Code: " in line)
    assert code.startswith("noust_inv_")
    account, _secret = AccountManager().open_invitation(code.strip())
    assert account.username == "lucia" and account.status == "invited"


def test_the_lifecycle_commands(runner: CliRunner) -> None:
    run(runner, "user", "create", "maria", "--role", "viewer", "--stdin", stdin=PASSWORD + "\n")

    assert "now operator" in run(runner, "user", "set-role", "maria", "operator")
    assert "disabled" in run(runner, "user", "disable", "maria", "--reason", "left")
    assert AccountManager().find("maria").status == "disabled"
    run(runner, "user", "enable", "maria")
    run(runner, "user", "unlock", "maria")
    run(runner, "user", "reset-mfa", "maria")
    run(runner, "user", "remove", "maria", "--force")
    assert AccountManager().find("maria") is None


def test_disabling_ends_the_console_sessions(runner: CliRunner, state_dir: Path) -> None:
    run(runner, "user", "create", "maria", "--role", "viewer", "--stdin", stdin=PASSWORD + "\n")
    maria = AccountManager().find("maria")
    manager = TokenManager(SecurityConfig())
    manager.create_session("10.0.0.1", account_id=maria.id, auth_method="password")
    manager.sessions.close()

    output = run(runner, "user", "disable", "maria")

    assert "Ended 1 session(s)" in output


def test_the_first_admin_adopts_the_tokens_issued_before(runner: CliRunner) -> None:
    manager = TokenManager(SecurityConfig())
    issued = manager.create_api_token("old-ci", "deploy")
    manager.sessions.close()

    output = run(
        runner, "user", "create", "maria", "--role", "admin", "--stdin", stdin=PASSWORD + "\n"
    )

    assert "adopted 1 API token" in output
    manager = TokenManager(SecurityConfig())
    assert (
        manager.get_api_token(issued["id"])["owner_account_id"] == AccountManager().find("maria").id
    )
    manager.sessions.close()


def test_separation_of_duties_and_its_exception(runner: CliRunner) -> None:
    run(
        runner,
        "user",
        "create",
        "maria",
        "--role",
        "admin",
        "--person-ref",
        "maria@example.com",
        "--stdin",
        stdin=PASSWORD + "\n",
    )
    refused = runner.invoke(
        root_cli,
        [
            "user",
            "create",
            "maria.sec",
            "--role",
            "security",
            "--person-ref",
            "maria@example.com",
            "--stdin",
        ],
        input=PASSWORD + "\n",
    )
    assert refused.exit_code != 0
    assert "noust user exception add" in getattr(refused.exception, "details", "")

    run(
        runner,
        "user",
        "exception",
        "add",
        "maria@example.com",
        "--reason",
        "One responsible person at a small office",
        "--days",
        "30",
    )
    run(
        runner,
        "user",
        "create",
        "maria.sec",
        "--role",
        "security",
        "--person-ref",
        "maria@example.com",
        "--stdin",
        stdin=PASSWORD + "\n",
    )
    listed = run(runner, "user", "list")
    assert "documented exception" in listed
    assert "One responsible person" in run(runner, "user", "exception", "list")


def test_account_changes_are_on_the_audit_trail(runner: CliRunner, state_dir: Path) -> None:
    run(runner, "user", "create", "maria", "--role", "viewer", "--stdin", stdin=PASSWORD + "\n")

    log = (state_dir / "web-audit.log").read_text()

    assert "user.create" in log
    assert PASSWORD not in log
