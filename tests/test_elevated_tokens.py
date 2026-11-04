# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An API token acts in sudo mode only when it was made to, by whoever governs accounts.

Sudo mode is a fresh proof that a person is there. A token cannot give one, so a token made
for an account (``--owner``) is refused wherever sudo mode is asked, and until 4.2 the command
line had no way to make one that is not: a script that has to change settings over the API had
to be a person's session. The way through is explicit and leaves a trail:

- it is asked for (``noust token create NAME --owner USER --elevated``, ``allow_elevated`` in
  the API) and never the default, and from the command line it names the account it acts for:
  sudo mode is a person's;
- the one who issues it governs accounts (and, over HTTP, is in sudo mode: issuing any token
  already is); an operator's own session cannot hand itself a standing exemption;
- it shows in the list of tokens, as a column and as a field;
- every action it takes where sudo mode is asked is in the audit trail with the token's name and
  what it did, not only the issue of it;
- a server where the compliance control ``restricted_tokens`` is on refuses it whoever asks.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.audit import get_log
from noust.core.config import Config
from noust.core.exceptions import SecurityError
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, STATE_DIR_ENV, SecurityConfig, TokenManager
from noust.web.permissions import Permission
from noust.web.server import create_app, get_token_manager

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A store, a state directory, a configuration and three accounts of the test's own."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    Config.reset_instance()
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    accounts = AccountManager(store)
    accounts.create("ops", "admin", password=PASSWORD)
    accounts.create("olga", "operator", password=PASSWORD)
    accounts.create("vera", "viewer", password=PASSWORD)
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


def run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(root_cli, list(args))
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, output
    return output


def records() -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in TokenManager(SecurityConfig()).list_api_tokens()}


def events(action: str) -> list[dict[str, Any]]:
    # The log reads newest first; a test reads what happened in order.
    return list(reversed([e for e in get_log().read(limit=1000) if e["action"] == action]))


class TestFromTheCommandLine:
    def test_a_token_for_an_account_is_not_elevated_unless_it_says_so(self) -> None:
        run("token", "create", "plain", "--owner", "ops", "--scope", "admin")

        assert records()["plain"]["allow_elevated"] is False

    def test_the_flag_makes_one(self) -> None:
        output = run(
            "token", "create", "pipeline", "--owner", "ops", "--scope", "admin", "--elevated"
        )

        assert records()["pipeline"]["allow_elevated"] is True
        assert "sudo mode" in output
        assert re.search(r"Token: noust_\S+", output)

    def test_it_belongs_to_a_person_or_it_is_not_made(self) -> None:
        output = run("token", "create", "orphan", "--elevated", code=2)

        assert "--owner" in output and "sudo mode" in output
        assert "orphan" not in records()

    def test_it_is_refused_where_tokens_are_restricted(self) -> None:
        Config().set("security.frameworks", ["ens-media"])

        output = run(
            "token", "create", "pipeline", "--owner", "ops", "--scope", "admin", "--elevated",
            "--expires-hours", "24", "--reason", "pipeline", code=1,
        )  # fmt: skip

        assert "cannot act in sudo mode" in output
        assert "pipeline" not in records()

    def test_the_list_says_which_tokens_may_act_in_sudo_mode(self) -> None:
        run("token", "create", "plain", "--owner", "ops")
        run("token", "create", "pipeline", "--owner", "ops", "--elevated")

        shown = run("token", "list")
        listed = {r["name"]: r for r in json.loads(run("token", "list", "--json"))["tokens"]}

        assert "Sudo" in shown and "yes" in shown and "no" in shown
        assert listed["plain"]["allow_elevated"] is False
        assert listed["pipeline"]["allow_elevated"] is True

    def test_issuing_it_is_in_the_audit_trail_with_what_it_may_do(self) -> None:
        run("token", "create", "pipeline", "--owner", "ops", "--scope", "admin", "--elevated")

        (issued,) = events("auth.token.create")

        assert issued["resource"] == f"token:{records()['pipeline']['id']}"
        assert issued["details"]["sudo_mode"] is True
        assert issued["details"]["owner"] == "ops"

    def test_a_plain_one_says_it_does_not(self) -> None:
        run("token", "create", "plain", "--owner", "ops")

        (issued,) = events("auth.token.create")

        assert issued["details"]["sudo_mode"] is False


class TestAtTheChokepoint:
    """The manager is the one place a token is made: the rule is held there, not in a caller."""

    def issue(self, owner: str, **extra: Any) -> dict[str, Any]:
        manager = TokenManager(SecurityConfig())
        return manager.create_api_token(
            f"t-{owner}", "admin", None, owner=manager.accounts.require(owner), **extra
        )

    def test_whoever_governs_accounts_may_issue_one(self) -> None:
        issued = self.issue(
            "ops", allow_elevated=True, issuer_permissions=frozenset({Permission.ACCOUNTS_MANAGE})
        )

        assert issued["allow_elevated"] is True

    def test_somebody_who_does_not_may_not_issue_it_even_for_themselves(self) -> None:
        with pytest.raises(SecurityError) as refused:
            self.issue("olga", allow_elevated=True, issuer_permissions=frozenset({Permission.SELF}))

        assert "accounts" in str(refused.value)
        assert refused.value.details and "administrator" in refused.value.details.lower()

    def test_without_an_issuer_it_is_root_and_may(self) -> None:
        assert self.issue("ops", allow_elevated=True)["allow_elevated"] is True

    def test_asking_for_nothing_asks_nobody_anything(self) -> None:
        issued = self.issue("olga", issuer_permissions=frozenset({Permission.SELF}))

        assert issued["allow_elevated"] is False


class TestOverHttp:
    @pytest.fixture
    def console(self, tmp_path: Path) -> TestClient:
        app = create_app(
            SecurityConfig(
                state_dir=tmp_path / "state",
                rate_limit_requests=100_000,
                auth_policy=AuthPolicy(),
            )
        )
        return TestClient(app, client=("testclient", 50000))

    def bearer(self, name: str, owner: str, *, elevated: bool) -> dict[str, str]:
        manager = get_token_manager()
        issued = manager.create_api_token(
            name, "admin", None, owner=manager.accounts.require(owner), allow_elevated=elevated
        )
        return {"Authorization": f"Bearer {issued['token']}"}

    def smtp(self, console: TestClient, headers: dict[str, str]) -> Any:
        return console.put(
            "/api/config/smtp",
            json={"host": "", "port": 465, "use_ssl": True, "use_tls": False, "username": ""},
            headers=headers,
        )

    def test_a_token_that_was_not_made_to_is_asked_for_sudo_mode(self, console: TestClient) -> None:
        refused = self.smtp(console, self.bearer("plain", "ops", elevated=False))

        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"

    def test_one_that_was_acts_and_every_action_is_recorded_with_its_name(
        self, console: TestClient
    ) -> None:
        headers = self.bearer("pipeline", "ops", elevated=True)

        first = self.smtp(console, headers)
        second = self.smtp(console, headers)

        assert first.status_code == 200 and second.status_code == 200, second.text
        acted = events("auth.token.elevated")
        assert len(acted) == 2
        assert all(e["actor"] == "token:pipeline" for e in acted)
        assert all(e["resource"] == "/api/config/smtp" for e in acted)
        assert "PUT" in acted[0]["detail"]

    def test_a_refusal_is_recorded_too_and_is_not_a_use(self, console: TestClient) -> None:
        self.smtp(console, self.bearer("plain", "ops", elevated=False))

        assert events("auth.token.elevated") == []
        assert events("auth.elevation")

    def test_a_person_in_sudo_mode_is_not_recorded_as_a_token(self, console: TestClient) -> None:
        manager = get_token_manager()
        account = manager.accounts.require("ops")
        secret = manager.accounts.begin_totp(account.id)
        from noust.core import totp

        codes = manager.accounts.confirm_totp(account.id, totp.totp_now(secret))
        assert codes is not None
        login = console.post(
            "/api/auth/login",
            json={"username": "ops", "password": PASSWORD, "totp_code": codes.pop()},
        )
        headers = {CSRF_HEADER_NAME: login.json()["csrf_token"]}
        console.post(
            "/api/auth/elevate", json={"password": PASSWORD, "code": codes.pop()}, headers=headers
        )

        done = console.put(
            "/api/config/smtp",
            json={"host": "", "port": 465, "use_ssl": True, "use_tls": False, "username": ""},
            headers=headers,
        )

        assert done.status_code == 200, done.text
        assert events("auth.token.elevated") == []

    def create(self, console: TestClient, who: str, **body: Any) -> Any:
        manager = get_token_manager()
        account = manager.accounts.require(who)
        secret = manager.accounts.begin_totp(account.id)
        from noust.core import totp

        codes = manager.accounts.confirm_totp(account.id, totp.totp_now(secret))
        assert codes is not None
        login = console.post(
            "/api/auth/login",
            json={"username": who, "password": PASSWORD, "totp_code": codes.pop()},
        )
        assert login.status_code == 200, login.text
        headers = {CSRF_HEADER_NAME: login.json()["csrf_token"]}
        elevated = console.post(
            "/api/auth/elevate", json={"password": PASSWORD, "code": codes.pop()}, headers=headers
        )
        assert elevated.status_code == 200, elevated.text
        return console.post("/api/auth/tokens", json=body, headers=headers)

    def test_an_administrator_in_sudo_mode_issues_one_and_the_audit_says_so(
        self, console: TestClient
    ) -> None:
        response = self.create(console, "ops", name="pipeline", scope="admin", allow_elevated=True)

        assert response.status_code == 201, response.text
        assert response.json()["allow_elevated"] is True
        (issued,) = events("auth.token.create")
        assert "sudo mode" in issued["detail"]

    def test_an_operator_cannot_hand_herself_a_standing_exemption(
        self, console: TestClient
    ) -> None:
        response = self.create(console, "olga", name="mine", scope="deploy", allow_elevated=True)

        assert response.status_code in (400, 403), response.text
        assert "accounts" in response.text.lower()
        assert "mine" not in records()

    def test_an_operator_still_issues_an_ordinary_token(self, console: TestClient) -> None:
        response = self.create(console, "olga", name="mine", scope="deploy")

        assert response.status_code == 201, response.text
        assert response.json()["allow_elevated"] is False
