# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Looking at what a person sees without becoming them (checklist I-15).

Noust has no "sign in as" button on purpose (docs/security.md, Acting as another person). What a
support question needs is a read-only, short-lived credential that acts for the person and nothing
more, and is plainly somebody else's in the audit trail. That is a token issued with `--owner`, and
this holds the documentation's claims about it to the code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.web.auth import STATE_DIR_ENV, actor_label
from noust.web.server import get_token_manager
from tests.test_web_accounts import (  # noqa: F401 - the fixtures of the account tests
    accounts,
    build,
    cheap_hashes,
    make_account,
    read_audit,
    store,
)

NAME = "look-at-maria"


@pytest.fixture
def ready(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(STATE_DIR_ENV, str(sandbox / "state"))
    client = build(sandbox, trusted_proxies=[])
    make_account("maria", "operator")
    make_account("sofia", "security")
    return client


def issue(**more: Any) -> dict[str, Any]:
    manager = get_token_manager()
    return manager.create_api_token(
        NAME, "read", 1, owner=manager.accounts.require("maria"), created_by="sofia", **more
    )


def test_the_token_reads_as_the_person_and_cannot_write(ready: Any) -> None:
    bearer = {"Authorization": f"Bearer {issue()['token']}"}

    assert ready.get("/api/auth/verify", headers=bearer).status_code == 200
    assert ready.get("/api/apps", headers=bearer).status_code == 200
    # A read token never writes, whatever the person it acts for may do.
    refused = ready.post("/api/apps/shop.example.com/restart", headers=bearer)
    assert refused.status_code in (401, 403)
    changed = ready.put("/api/auth/oidc/local-login", json={"enabled": False}, headers=bearer)
    assert changed.status_code in (401, 403)


def test_it_never_confirms_sudo_mode(ready: Any) -> None:
    bearer = {"Authorization": f"Bearer {issue()['token']}"}

    response = ready.post("/api/auth/elevate", json={"code": "123456"}, headers=bearer)

    assert response.status_code in (401, 403)


def test_what_it_does_is_attributed_to_the_token_and_not_to_the_person(ready: Any) -> None:
    payload = get_token_manager().verify_api_token(issue()["token"], "127.0.0.1")

    assert payload is not None
    assert actor_label(payload) == f"token:{NAME}"
    assert payload["owner_account_id"] == get_token_manager().accounts.require("maria").id
    assert "account_id" not in payload and "username" not in payload


def test_it_is_listed_and_ends_when_revoked_or_when_the_hour_is_over(ready: Any) -> None:
    issued = issue()
    manager = get_token_manager()
    bearer = {"Authorization": f"Bearer {issued['token']}"}

    listing = manager.list_api_tokens()
    assert [row["name"] for row in listing] == [NAME]
    assert listing[0]["expires_at"] is not None
    assert ready.get("/api/auth/verify", headers=bearer).status_code == 200
    manager.revoke_api_token(issued["id"])
    assert ready.get("/api/auth/verify", headers=bearer).status_code == 401


def test_the_command_line_issues_it_for_the_person_and_records_who_did(
    ready: Any, sandbox: Path
) -> None:
    result = CliRunner().invoke(
        root_cli,
        [
            "token",
            "create",
            "cli-look",
            "--scope",
            "read",
            "--owner",
            "maria",
            "--expires-hours",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "cli-look" in result.output
    rows = [r for r in get_token_manager().list_api_tokens() if r["name"] == "cli-look"]
    assert rows and rows[0]["owner_account_id"] == get_token_manager().accounts.require("maria").id
    # A command that changes something is on the trail with the operating system's identity.
    ran = [e for e in read_audit(sandbox) if e["action"] == "cli.command" and "token create" in str(e)]
    assert ran, [e["action"] for e in read_audit(sandbox)]
    assert "cli-look" in str(ran[-1]) or "token create" in str(ran[-1])
