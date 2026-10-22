# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
API tokens issued before 3.0 keep working, and the server says which are still in use.

A token is stored hashed, so its prefix cannot be read back; the only moment the server knows
that one is of the old kind is when it is presented. That moment is recorded, so ``noust health``
and the console can name the scripts that have not been given a new token yet.
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import legacy_names
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, get_token_manager


@pytest.fixture
def app(tmp_path: Path, runner: object) -> FastAPI:
    """
    Args:
        tmp_path: Per-test temporary directory.
        runner: The fake command runner.

    Returns:
        The application.
    """
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


def issue(name: str, prefix: str) -> tuple[int, str]:
    """
    Store a token the way an earlier release did, with the prefix it had.

    Args:
        name: The token's name.
        prefix: What the token starts with.

    Returns:
        Its id and the token.
    """
    manager = get_token_manager()
    token = prefix + secrets.token_urlsafe(32)
    token_id = manager.sessions.create_api_token(
        name, manager._hash_token(token), "admin", time.time(), None, created_by="test"
    )
    return token_id, token


def present(app: FastAPI, token: str) -> bool:
    """
    Args:
        app: The application.
        token: A token.

    Returns:
        Whether the session endpoint takes it for a signed-in caller.
    """
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    response = client.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
    return response.status_code == 200 and response.json()["authenticated"] is True


def test_a_token_of_before_still_signs_in(app: FastAPI) -> None:
    _, token = issue("old-script", legacy_names.API_TOKEN_PREFIX)

    assert present(app, token) is True


def test_using_one_records_it_and_a_new_one_is_not(app: FastAPI) -> None:
    _, old = issue("old-script", legacy_names.API_TOKEN_PREFIX)
    _, new = issue("new-script", "noust_tok_")
    manager = get_token_manager()
    assert manager.legacy_prefix_tokens_in_use() == []

    assert present(app, old) is True
    assert present(app, new) is True

    in_use = manager.legacy_prefix_tokens_in_use()
    assert [item["name"] for item in in_use] == ["old-script"]
    assert in_use[0]["last_used_at"] is not None


def test_a_revoked_one_is_not_in_use(app: FastAPI) -> None:
    token_id, old = issue("old-script", legacy_names.API_TOKEN_PREFIX)
    manager = get_token_manager()
    assert present(app, old) is True
    assert manager.legacy_prefix_tokens_in_use()

    manager.revoke_api_token(token_id)

    assert manager.legacy_prefix_tokens_in_use() == []


def test_one_nobody_has_used_for_a_while_is_not_in_use(app: FastAPI) -> None:
    _, old = issue("old-script", legacy_names.API_TOKEN_PREFIX)
    manager = get_token_manager()
    assert present(app, old) is True

    assert manager.legacy_prefix_tokens_in_use(days=0.0) == []


def test_a_wrong_token_with_the_old_prefix_records_nothing(app: FastAPI) -> None:
    issue("old-script", legacy_names.API_TOKEN_PREFIX)

    assert present(app, legacy_names.API_TOKEN_PREFIX + "x" * 40) is False

    assert get_token_manager().legacy_prefix_tokens_in_use() == []
