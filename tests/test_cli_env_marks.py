# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``wasm env mark`` and the reason ``wasm env show`` now prints.

Companion to ``tests/test_cli_env.py``, which pins the redaction contract;
this module pins the operator's own override and the classifier's reason
reaching both the human report and ``--json``.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from noust.cli import app as app_module
from noust.cli.commands import env as env_module
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.helpers import app_env as app_env_module


@pytest.fixture
def cli_runner() -> CliRunner:
    """Return a Click test runner."""
    return CliRunner()


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """
    Capture what the commands print.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The buffer every Logger built by the env module writes to.
    """
    buffer = io.StringIO()
    monkeypatch.setattr(env_module, "Logger", lambda **kwargs: Logger(stream=buffer, **kwargs))
    return buffer


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """
    Provide an empty store in the test's directory, where the command looks.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The store.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(app_env_module, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def deployed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore, runner: FakeRunner
) -> Path:
    """
    Provide an application deployed at example.com, with no store row.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
        store: The empty store.
        runner: Fake runner for the ownership hand-over of a written .env.

    Returns:
        The application root.
    """
    apps = tmp_path / "apps"
    app_path = apps / "example-com"
    app_path.mkdir(parents=True)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=apps, service_user="www-data", service_group="www-data"
        ),
    )
    return app_path


@pytest.fixture
def registered(deployed: Path, store: NoustStore) -> Path:
    """
    A ``deployed`` application that also has a store row.

    ``wasm env mark`` writes to the store, so it needs a row of its own
    rather than the directory-only stand-in ``find_app`` otherwise returns.

    Args:
        deployed: The application root.
        store: The store to add the row to.

    Returns:
        The application root.
    """
    from noust.core.store import App

    store.create_app(App(domain="example.com", app_path=str(deployed)))
    return deployed


# ---------------------------------------------------------------------------
# wasm env mark
# ---------------------------------------------------------------------------


def test_mark_secret_masks_a_variable_show_would_otherwise_print(
    cli_runner: CliRunner, registered: Path, logged: io.StringIO
) -> None:
    (registered / ".env").write_text("APP_NAME=storefront\n", encoding="utf-8")

    result = cli_runner.invoke(
        app_module.cli, ["env", "mark", "example.com", "APP_NAME", "--secret"]
    )
    assert result.exit_code == 0, result.output

    env_module._env_show("example.com", unmask=False, verbose=False)
    assert "storefront" not in logged.getvalue()


def test_mark_not_secret_reveals_a_variable_show_would_otherwise_hide(
    cli_runner: CliRunner, registered: Path, logged: io.StringIO
) -> None:
    (registered / ".env").write_text("API_KEY=short\n", encoding="utf-8")

    result = cli_runner.invoke(
        app_module.cli, ["env", "mark", "example.com", "API_KEY", "--not-secret"]
    )
    assert result.exit_code == 0, result.output

    env_module._env_show("example.com", unmask=False, verbose=False)
    assert "short" in logged.getvalue()


def test_mark_auto_removes_a_previous_override(
    cli_runner: CliRunner, registered: Path, store: NoustStore
) -> None:
    (registered / ".env").write_text("API_KEY=short\n", encoding="utf-8")
    cli_runner.invoke(app_module.cli, ["env", "mark", "example.com", "API_KEY", "--not-secret"])

    result = cli_runner.invoke(app_module.cli, ["env", "mark", "example.com", "API_KEY", "--auto"])

    assert result.exit_code == 0, result.output
    app = store.get_app("example.com")
    assert app is not None
    assert "API_KEY" not in app.env_secret_marks


def test_mark_requires_exactly_one_flag(cli_runner: CliRunner, registered: Path) -> None:
    result = cli_runner.invoke(app_module.cli, ["env", "mark", "example.com", "API_KEY"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_mark_rejects_more_than_one_flag(cli_runner: CliRunner, registered: Path) -> None:
    result = cli_runner.invoke(
        app_module.cli,
        ["env", "mark", "example.com", "API_KEY", "--secret", "--not-secret"],
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_mark_on_an_undeployed_domain_is_an_error(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real CLI boundary, ``noust.cli.app.main``, like the rest of the group."""
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))

    exit_code = app_module.main(["env", "mark", "nowhere.example.com", "API_KEY", "--secret"])

    assert exit_code == 1
    assert any("nowhere.example.com" in line for line in lines)


def test_mark_persists_across_separate_invocations(
    cli_runner: CliRunner, registered: Path, store: NoustStore
) -> None:
    (registered / ".env").write_text("APP_NAME=storefront\nOTHER=1\n", encoding="utf-8")

    cli_runner.invoke(app_module.cli, ["env", "mark", "example.com", "APP_NAME", "--secret"])
    cli_runner.invoke(app_module.cli, ["env", "mark", "example.com", "OTHER", "--secret"])

    app = store.get_app("example.com")
    assert app is not None
    assert app.env_secret_marks == {"APP_NAME": True, "OTHER": True}


# ---------------------------------------------------------------------------
# wasm env show: the reason column and the --json secrets map
# ---------------------------------------------------------------------------


def test_show_prints_a_reason_next_to_each_variable(registered: Path, logged: io.StringIO) -> None:
    (registered / ".env").write_text("API_KEY=short\nPORT=3000\n", encoding="utf-8")

    assert env_module._env_show("example.com", unmask=False, verbose=False) == 0

    output = logged.getvalue()
    assert "API_KEY" in output and "name" in output
    assert "PORT" in output and "plain" in output


def test_show_json_includes_the_secrets_map(registered: Path) -> None:
    (registered / ".env").write_text("API_KEY=short\nPORT=3000\n", encoding="utf-8")

    result = CliRunner().invoke(app_module.cli, ["env", "show", "example.com", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["secrets"]["API_KEY"] == {"secret": True, "reason": "name", "marked": False}
    assert payload["secrets"]["PORT"] == {"secret": False, "reason": "plain", "marked": False}


def test_show_json_secrets_map_reflects_a_mark(cli_runner: CliRunner, registered: Path) -> None:
    (registered / ".env").write_text("APP_NAME=storefront\n", encoding="utf-8")
    cli_runner.invoke(app_module.cli, ["env", "mark", "example.com", "APP_NAME", "--secret"])

    result = CliRunner().invoke(app_module.cli, ["env", "show", "example.com", "--json"])

    payload = json.loads(result.output)
    assert payload["secrets"]["APP_NAME"] == {
        "secret": True,
        "reason": "marked secret",
        "marked": True,
    }
    assert payload["variables"]["APP_NAME"] == "***"
