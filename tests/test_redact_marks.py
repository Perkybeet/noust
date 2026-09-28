# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for how an operator's env secret marks reach the log scrubber.

``tests/test_redact.py`` pins the scrubber and the unmarked classification;
this module is the companion for ``App.env_secret_marks`` specifically: a
mark always wins, in both directions, over what a name or a value would
otherwise say - see ``wasm.core.secret_detection.classify``, the one
classifier both now share.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wasm.core.redact import app_secret_values, secret_env_values
from wasm.core.store import App, WASMStore

DOMAIN = "app.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        An isolated store, installed as the process-wide singleton.
    """
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    try:
        yield instance
    finally:
        instance.close()
        WASMStore.reset_instance()


# ---------------------------------------------------------------------------
# secret_env_values(env, marks)
# ---------------------------------------------------------------------------


def test_a_secret_mark_scrubs_a_value_nothing_else_would_flag() -> None:
    values = secret_env_values({"CUSTOMER_NAME": "Jane Doe"}, marks={"CUSTOMER_NAME": True})

    assert "Jane Doe" in values


def test_a_not_secret_mark_stops_a_name_that_looks_secret_from_being_scrubbed() -> None:
    values = secret_env_values({"API_KEY": "not-actually-sensitive"}, marks={"API_KEY": False})

    assert values == []


def test_a_not_secret_mark_stops_even_an_embedded_url_credential_from_being_scrubbed() -> None:
    """
    The operator's mark is absolute: once they have said "trust this
    variable", nothing about it - including a password-shaped substring - is
    still pulled out and scrubbed against their word.
    """
    values = secret_env_values(
        {"DATABASE_URL": "postgres://app:hunter2@localhost/app"},
        marks={"DATABASE_URL": False},
    )

    assert values == []


def test_an_unmarked_url_credential_is_still_scrubbed_as_before() -> None:
    values = secret_env_values({"DATABASE_URL": "postgres://app:hunter2@localhost/app"})

    assert "hunter2" in values
    assert "postgres://app:hunter2@localhost/app" not in values


def test_marks_is_optional_and_defaults_to_no_override() -> None:
    without = secret_env_values({"API_KEY": "sk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc"})
    with_none = secret_env_values({"API_KEY": "sk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc"}, marks=None)

    assert without == with_none


# ---------------------------------------------------------------------------
# app_secret_values(domain): marks read off the application's own row
# ---------------------------------------------------------------------------


@pytest.fixture
def deployed_app(tmp_path: Path, store: WASMStore) -> App:
    """
    Register an application whose ``.env`` holds one ordinary-looking value.

    Args:
        tmp_path: Per-test temporary directory.
        store: The store fixture.

    Returns:
        The application row.
    """
    app_path = tmp_path / "apps" / "app"
    app_path.mkdir(parents=True)
    (app_path / ".env").write_text("CUSTOMER_NAME=Jane Doe\nAPI_KEY=not-a-real-secret\n")
    return store.create_app(App(domain=DOMAIN, app_path=str(app_path)))


def test_app_secret_values_honours_a_secret_mark(deployed_app: App, store: WASMStore) -> None:
    store.set_env_secret_marks(DOMAIN, {"CUSTOMER_NAME": True})

    values = app_secret_values(DOMAIN)

    assert "Jane Doe" in values


def test_app_secret_values_honours_a_not_secret_mark(deployed_app: App, store: WASMStore) -> None:
    store.set_env_secret_marks(DOMAIN, {"API_KEY": False})

    values = app_secret_values(DOMAIN)

    assert "not-a-real-secret" not in values
