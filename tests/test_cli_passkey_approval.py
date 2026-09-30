"""
``noust passkey`` and ``noust approval``: root's view of passkeys and four-eyes requests.

Both are front ends over the managers the console uses, on the same store:
a passkey registered in the console is listed and removed here, and a request
the console's guard created is decided here - on record with the operating
system identity of whoever ran the command.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import audit, totp
from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.approvals import (
    ApprovalActor,
    ApprovalManager,
    ApprovalPolicy,
    rule_for,
    snapshot,
)
from noust.core.store import NoustStore
from noust.web.auth import STATE_DIR_ENV

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    yield instance
    NoustStore.reset_instance()


def run(*args: str) -> Any:
    result = CliRunner().invoke(root_cli, list(args))
    return result


def audit_actions() -> list[dict[str, Any]]:
    path = audit.get_log().path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def enrol(account_id: int | None, username: str) -> int:
    pytest.importorskip("cryptography")
    from noust.core.accounts.passkeys import PasskeyManager, PasskeyOwner, RelyingParty
    from tests.webauthn_authenticator import SoftwareAuthenticator

    rp = RelyingParty(id="localhost", origins=("http://localhost:8080",))
    manager = PasskeyManager(hostname="web1")
    owner = PasskeyOwner(account_id=account_id, username=username)
    options = manager.registration_options(owner, rp, binding="s")
    credential = SoftwareAuthenticator().create(options, "http://localhost:8080")
    return manager.register(
        owner, rp, credential, name=f"{username} key", binding="s", created_by="test"
    ).passkey.id


class TestPasskey:
    def test_list_names_every_owner(self) -> None:
        maria = AccountManager().create("maria", "admin", password=PASSWORD)
        enrol(maria.id, "maria")
        enrol(None, "master")

        result = run("passkey", "list", "--json")

        assert result.exit_code == 0, result.output
        owners = sorted(item["owner"] for item in json.loads(result.output)["passkeys"])
        assert owners == ["maria", "master"]
        only_master = json.loads(run("passkey", "list", "--master", "--json").output)
        assert [item["owner"] for item in only_master["passkeys"]] == ["master"]

    def test_remove_takes_any_passkey_and_is_on_record(self) -> None:
        maria = AccountManager().create("maria", "admin", password=PASSWORD)
        passkey_id = enrol(maria.id, "maria")

        result = run("passkey", "remove", str(passkey_id), "--force")

        assert result.exit_code == 0, result.output
        assert "no second factor left" in result.output
        assert json.loads(run("passkey", "list", "--json").output)["passkeys"] == []
        assert any(entry["action"] == "auth.passkey.remove" for entry in audit_actions())

    def test_reset_is_root_s_recovery_lever(self) -> None:
        maria = AccountManager().create("maria", "admin", password=PASSWORD)
        enrol(maria.id, "maria")
        enrol(None, "master")

        result = run("passkey", "reset", "maria", "--force")
        assert result.exit_code == 0, result.output
        assert "Removed 1 passkey(s) of maria" in result.output
        assert not AccountManager().require("maria").has_mfa

        master = run("passkey", "reset", "--master", "--force")
        assert master.exit_code == 0, master.output
        assert "noust web token --new" in master.output
        assert any(entry["action"] == "auth.passkey.reset" for entry in audit_actions())

    def test_reset_needs_one_owner(self) -> None:
        result = run("passkey", "reset", "--force")
        assert result.exit_code != 0
        # The error boundary of noust's entry point prints it; here it is raised.
        assert "--master" in str(result.exception)


class TestApproval:
    def ask(self) -> int:
        manager = ApprovalManager(policy=ApprovalPolicy(enabled=True))
        account = AccountManager().create("alice", "admin", password=PASSWORD)
        requester = ApprovalActor(kind="account", id=str(account.id), name="alice", role="admin")
        rule = rule_for("POST", "/api/cron", ["root_equivalent"], {}, {})
        assert rule is not None
        digest, parameters = snapshot("POST", "/api/cron", [], b'{"name": "x", "password": "p"}')
        request, _created = manager.request(
            rule,
            method="POST",
            path="/api/cron",
            parameters=parameters,
            fingerprint=digest,
            reason="ticket 7",
            requester=requester,
        )
        return request.id

    def test_list_and_show(self) -> None:
        approval_id = self.ask()

        listed = run("approval", "list", "--json")
        assert listed.exit_code == 0, listed.output
        assert [item["id"] for item in json.loads(listed.output)["approvals"]] == [approval_id]

        shown = run("approval", "show", str(approval_id))
        assert shown.exit_code == 0, shown.output
        assert "POST /api/cron" in shown.output
        assert '"p"' not in shown.output  # the password is redacted

    def test_root_approves_on_record(self) -> None:
        approval_id = self.ask()

        result = run("approval", "approve", str(approval_id), "--comment", "ok")

        assert result.exit_code == 0, result.output
        assert ApprovalManager().get(approval_id).state == "approved"
        decision = [entry for entry in audit_actions() if entry["action"] == "approval.approve"]
        assert decision and decision[-1]["who"]["kind"] in ("cli", "system")

    def test_root_rejects(self) -> None:
        approval_id = self.ask()
        result = run("approval", "reject", str(approval_id), "--comment", "no")
        assert result.exit_code == 0, result.output
        assert ApprovalManager().get(approval_id).state == "rejected"

    def test_an_unknown_request_says_so(self) -> None:
        result = run("approval", "approve", "999")
        assert result.exit_code != 0
        assert "No approval request with id 999" in str(result.exception)


def test_both_groups_are_registered() -> None:
    from noust.cli.app import COMMAND_MODULES

    assert COMMAND_MODULES["passkey"] == "noust.cli.commands.passkey"
    assert COMMAND_MODULES["approval"] == "noust.cli.commands.approval"


def test_a_backup_code_still_signs_in_after_a_passkey_reset_via_totp() -> None:
    manager = AccountManager()
    account = manager.create("maria", "admin", password=PASSWORD)
    secret = manager.begin_totp(account.id)
    codes = manager.confirm_totp(account.id, totp.totp_now(secret))
    assert codes is not None
    enrol(account.id, "maria")
    assert run("passkey", "reset", "maria", "--force").exit_code == 0
    assert manager.authenticate("maria", PASSWORD, codes[0], client_ip="x").username == "maria"
