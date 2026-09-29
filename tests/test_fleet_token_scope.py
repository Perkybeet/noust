"""
The ``fleet`` token scope: where it may be created, and the table that stores it.

A fleet token is the one credential allowed to speak for somebody else, so it
has exactly one door: ``noust fleet authorize`` on the node, as root. The API
and ``noust token create`` both refuse it, at the manager they share.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import SecurityError
from noust.web.auth import FLEET_SCOPE, STATE_DIR_ENV, SecurityConfig, SessionStore, TokenManager


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TokenManager:
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return TokenManager(SecurityConfig())


def test_the_scope_is_spelled_fleet() -> None:
    assert FLEET_SCOPE == "fleet"


def test_create_api_token_refuses_fleet_with_a_pointer(manager: TokenManager) -> None:
    with pytest.raises(SecurityError) as caught:
        manager.create_api_token("fleet-nas", "fleet")

    assert "noust fleet authorize" in caught.value.details
    assert manager.list_api_tokens() == []


def test_create_fleet_token_issues_a_fleet_scoped_token(manager: TokenManager) -> None:
    issued = manager.create_fleet_token("fleet-nas")

    assert issued["scope"] == "fleet"
    assert issued["expires_at"] is None
    assert issued["token"].startswith("noust_tok_")
    payload = manager.verify_api_token(issued["token"], "127.0.0.1")
    assert payload is not None
    assert payload["token_name"] == "fleet-nas"
    assert [record["scope"] for record in manager.list_api_tokens()] == ["fleet"]


def test_a_fleet_token_name_is_unique_like_any_other(manager: TokenManager) -> None:
    manager.create_fleet_token("fleet-nas")

    with pytest.raises(SecurityError):
        manager.create_fleet_token("fleet-nas")


def test_an_old_table_is_widened_keeping_every_row_and_id(tmp_path: Path) -> None:
    """A 2.x session database refuses 'fleet' in its CHECK until it is rebuilt."""
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            token_hash TEXT NOT NULL UNIQUE,
            scope TEXT NOT NULL CHECK (scope IN ('read', 'deploy', 'admin')),
            created_at REAL NOT NULL,
            expires_at REAL,
            last_used_at REAL,
            revoked_at REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO api_tokens (id, name, token_hash, scope, created_at, revoked_at) "
        "VALUES (7, 'ci', 'h1', 'deploy', 1.0, 2.0)"
    )
    conn.commit()
    conn.close()

    store = SessionStore(path)
    try:
        sql = store._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'api_tokens'"
        ).fetchone()[0]
        assert "'fleet'" in sql
        rows = store.list_api_tokens()
        assert [(row["id"], row["name"], row["revoked_at"]) for row in rows] == [(7, "ci", 2.0)]
        new_id = store.create_api_token("fleet-nas", "h2", "fleet", 3.0, None)
        assert new_id > 7
    finally:
        store.close()

    # Opening it again finds the widened table and leaves it alone.
    again = SessionStore(path)
    try:
        assert len(again.list_api_tokens()) == 2
    finally:
        again.close()


def test_the_token_cli_refuses_fleet_with_a_pointer(manager: TokenManager) -> None:
    result = CliRunner().invoke(root_cli, ["token", "create", "fleet-nas", "--scope", "fleet"])

    assert result.exit_code == 2
    assert "noust fleet authorize" in result.output
    assert manager.list_api_tokens() == []
