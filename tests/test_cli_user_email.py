# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust user set-email`` and ``noust user invite --email``: the address an account is mailed at."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import user as user_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.accounts.mail import AccountMailer, MailUnavailable
from noust.core.store import NoustStore
from noust.web.auth import STATE_DIR_ENV

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


class FakeMailer(AccountMailer):
    sent: list[dict[str, Any]] = []
    available = True

    def __init__(self) -> None: ...

    def ready(self) -> bool:
        return FakeMailer.available

    def send_invitation(self, address, username, code, hours, *, invited_by=None):  # type: ignore[no-untyped-def]
        if not FakeMailer.available:
            raise MailUnavailable("There is no way to send mail from this server")
        FakeMailer.sent.append({"address": address, "username": username, "code": code})


@pytest.fixture(autouse=True)
def mail(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMailer.sent = []
    FakeMailer.available = True
    monkeypatch.setattr(user_cli, "AccountMailer", FakeMailer)


def _run(*args: str, stdin: str | None = None) -> Any:
    return CliRunner().invoke(root_cli, list(args), input=stdin)


def test_an_address_is_set_and_forgotten() -> None:
    _run("user", "create", "maria", "--role", "viewer", "--stdin", stdin=PASSWORD + "\n")

    set_ = _run("--json", "user", "set-email", "maria", "Maria@Example.com")
    cleared = _run("--json", "user", "set-email", "maria")

    assert json.loads(set_.output)["account"]["email"] == "maria@example.com"
    assert json.loads(cleared.output)["account"]["email"] is None


def test_what_is_not_an_address_is_refused() -> None:
    _run("user", "create", "maria", "--role", "viewer", "--stdin", stdin=PASSWORD + "\n")

    result = _run("user", "set-email", "maria", "not-an-address")

    assert result.exit_code != 0
    assert AccountManager().require("maria").email is None


def test_an_invitation_is_mailed_with_its_code_and_the_address_is_kept() -> None:
    result = _run(
        "--json", "user", "invite", "lucia", "--role", "auditor", "--email", "Lucia@Example.com"
    )

    body = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert body["emailed"] is True
    assert FakeMailer.sent == [
        {"address": "lucia@example.com", "username": "lucia", "code": body["code"]}
    ]
    assert body["account"]["email"] == "lucia@example.com"


def test_without_mail_the_invitation_is_not_made() -> None:
    FakeMailer.available = False

    result = _run("user", "invite", "lucia", "--role", "auditor", "--email", "lucia@example.com")

    assert result.exit_code != 0
    assert AccountManager().find("lucia") is None


def test_an_invitation_with_no_address_mails_nothing() -> None:
    result = _run("--json", "user", "invite", "lucia", "--role", "auditor")

    assert json.loads(result.output)["emailed"] is False
    assert FakeMailer.sent == []
