# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
People and tokens for the tests of the approval policy and of what a credential did (items 184, 185).

A :class:`Person` is an account signed in to the console in a browser of its own, with codes to confirm
sudo mode with; :func:`issue` makes a token of an account as the header that calls the API with it, and
:func:`mark_as_assistant` makes one of them the built-in assistant's the way the token record of the base of
the assistant does, with a ``caller`` mark in the payload its credential authenticates and nowhere else.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.teams import TeamStore
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.server import get_token_manager

PASSWORD = "correct horse battery staple"

_ASSISTANTS: set[str] = set()


class Person:
    """A signed-in account in its own browser, with codes to confirm with."""

    def __init__(self, app: Any, username: str, role: str, *, team: str | None = None) -> None:
        manager = get_token_manager().accounts
        self.account = manager.create(
            username, role, password=PASSWORD, join_default_team=team is None
        )
        if team is not None:
            TeamStore(manager.store).add_member(team, username, "operator")
        secret = manager.begin_totp(self.account.id)
        codes = manager.confirm_totp(self.account.id, totp.totp_now(secret))
        assert codes is not None
        self.codes = list(codes)
        self.client = TestClient(app, client=("testclient", 50000))
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": PASSWORD, "totp_code": self.codes.pop()},
        )
        assert response.status_code == 200, response.text
        self.csrf = response.json()["csrf_token"]

    def headers(self, **extra: str) -> dict[str, str]:
        return {CSRF_HEADER_NAME: self.csrf, **extra}

    def elevate(self) -> None:
        response = self.client.post(
            "/api/auth/elevate", json={"code": self.codes.pop()}, headers=self.headers()
        )
        assert response.status_code == 200, response.text

    def get(self, path: str) -> Any:
        return self.client.get(path, headers=self.headers())

    def post(self, path: str, body: Any = None, **headers: str) -> Any:
        return self.client.post(path, json=body, headers=self.headers(**headers))

    def put(self, path: str, body: Any, **headers: str) -> Any:
        return self.client.put(path, json=body, headers=self.headers(**headers))

    def decide(self, approval_id: str | int, verb: str = "approve") -> Any:
        self.elevate()
        return self.post(f"/api/approvals/{approval_id}/{verb}", {"comment": "ok"})


def issue(
    owner: Any,
    name: str,
    *permissions: str,
    elevated: bool = False,
    assistant: bool = False,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> dict[str, str]:
    """
    Make a token of an account, narrowed to some permissions.

    Args:
        owner: The account it acts for.
        name: The token's name.
        *permissions: What it holds; everything its owner holds when none.
        elevated: Issue it to act where sudo mode is asked.
        assistant: Make it the built-in assistant's (needs ``monkeypatch``).
        monkeypatch: Patching helper, for the assistant's mark.

    Returns:
        The headers that call the API with it, with a ``User-Agent`` named after it.
    """
    issued = get_token_manager().create_api_token(
        name,
        "admin",
        None,
        owner=owner,
        permissions=list(permissions) or None,
        allow_elevated=elevated,
    )
    if assistant:
        assert monkeypatch is not None
        mark_as_assistant(monkeypatch, name)
    return {"Authorization": f"Bearer {issued['token']}", "User-Agent": f"pipeline-{name}/1.4"}


def mark_as_assistant(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """
    Make a token the built-in assistant's: a ``caller`` mark in the payload its credential authenticates.

    Args:
        monkeypatch: Patching helper.
        name: The token's name.
    """
    manager = get_token_manager()
    _ASSISTANTS.add(name)
    original = type(manager)._api_token_payload

    def marked(self: Any, record: Any, client_ip: Any, **kwargs: Any) -> Any:
        payload = original(self, record, client_ip, **kwargs)
        if payload is not None and str(record["name"]) in _ASSISTANTS:
            payload["caller"] = "assistant"
        return payload

    monkeypatch.setattr(type(manager), "_api_token_payload", marked)


def mark_records_as_assistant(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """
    Make the records of some tokens say they are the built-in assistant's, as the base of the assistant writes it.

    Args:
        monkeypatch: Patching helper.
        *names: The tokens' names.
    """
    cls = type(get_token_manager())
    listed, found = cls.list_api_tokens, cls.get_api_token

    def tag(record: Any) -> Any:
        return {**record, "caller": "assistant"} if record and record["name"] in names else record

    monkeypatch.setattr(
        cls, "list_api_tokens", lambda self, owner=None: [tag(r) for r in listed(self, owner)]
    )
    monkeypatch.setattr(cls, "get_api_token", lambda self, token_id: tag(found(self, token_id)))


def forget_assistants() -> None:
    """Forget which tokens were marked; called between tests."""
    _ASSISTANTS.clear()
