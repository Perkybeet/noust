# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``wasm preview``.

The command decides nothing: the rules are :mod:`wasm.managers.previews`'s,
covered in ``tests/test_previews.py``. Pinned here is the translation: the
arguments that reach the manager, the confirmation before previews are
removed, ``--json``, and a refusal becoming exit code 1 with the manager's
own words.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from wasm.cli.app import cli as root_cli
from wasm.core.store import App, PreviewRecord, PreviewSettings, WASMStore
from wasm.managers import previews

PARENT = "shop.example.com"
BASE = "previews.example.com"
CHILD = f"pr-7-shop-example-com.{BASE}"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
    """The store the command reads, with the application previewed."""
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    instance.create_app(
        App(
            domain=PARENT,
            app_type="nodejs",
            source="git@github.com:acme/shop.git",
            port=3000,
            app_path="/var/www/apps/shop-example-com",
        )
    )
    yield instance
    instance.close()
    WASMStore.reset_instance()


@pytest.fixture(autouse=True)
def no_timer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    synced: list[str] = []
    monkeypatch.setattr(previews, "sync_sweep_timer", lambda **_: synced.append("sync") or True)
    return synced


@pytest.fixture
def deletions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    deleted: list[str] = []

    def delete(domain: str, on_phase: Any, logger: Any) -> tuple[str, ...]:
        deleted.append(domain)
        return ()

    monkeypatch.setattr(previews, "_delete", delete)
    return deleted


def invoke(args: list[str], stdin: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=stdin)


def preview(store: WASMStore) -> None:
    store.save_preview(
        PreviewRecord(
            parent_domain=PARENT,
            domain=CHILD,
            number=7,
            branch="feature/x",
            provider="github",
            expires_at="2026-10-05T12:00:00+00:00",
            status="ready",
        )
    )


def test_enable_turns_previews_on(store: WASMStore, no_timer: list[str]) -> None:
    result = invoke(["preview", "enable", PARENT, "--domain", BASE, "--max", "5", "--ttl", "2d"])

    assert result.exit_code == 0, result.output
    settings = store.get_preview_settings(PARENT)
    assert settings is not None
    assert (settings.base_domain, settings.max_previews, settings.ttl_hours) == (BASE, 5, 48)
    assert no_timer == ["sync"]
    assert "production secrets" in result.output


def test_enable_prints_json(store: WASMStore) -> None:
    result = invoke(["preview", "enable", PARENT, "--domain", BASE, "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert (body["app_domain"], body["ttl_hours"]) == (PARENT, 168)


@pytest.mark.parametrize(
    "extra", [["--max", "0"], ["--max", "21"], ["--ttl", "91d"], ["--ttl", "soon"]]
)
def test_enable_refuses_what_the_manager_refuses(store: WASMStore, extra: list[str]) -> None:
    result = invoke(["preview", "enable", PARENT, "--domain", BASE, *extra])

    assert result.exit_code == 1, result.output
    assert store.get_preview_settings(PARENT) is None


def test_enable_takes_bots_and_excluded_variables(store: WASMStore) -> None:
    result = invoke(
        [
            "preview",
            "enable",
            PARENT,
            "--domain",
            BASE,
            "--allow-bots",
            "--exclude-env",
            "STRIPE_KEY",
            "--exclude-env",
            "S3_SECRET",
        ]
    )

    assert result.exit_code == 0, result.output
    settings = store.get_preview_settings(PARENT)
    assert settings is not None
    assert (settings.allow_bots, settings.exclude_env) == (True, ["STRIPE_KEY", "S3_SECRET"])
    assert "as root" in result.output


def test_enable_changes_one_setting_and_keeps_the_rest(store: WASMStore) -> None:
    invoke(["preview", "enable", PARENT, "--domain", BASE, "--max", "5", "--exclude-env", "A"])

    result = invoke(["preview", "enable", PARENT, "--no-allow-bots", "--ttl", "1d"])

    assert result.exit_code == 0, result.output
    settings = store.get_preview_settings(PARENT)
    assert settings is not None
    assert (settings.base_domain, settings.max_previews, settings.ttl_hours) == (BASE, 5, 24)
    assert (settings.allow_bots, settings.exclude_env) == (False, ["A"])

    cleared = invoke(["preview", "enable", PARENT, "--no-exclude-env"])
    assert cleared.exit_code == 0, cleared.output
    settings = store.get_preview_settings(PARENT)
    assert settings is not None and settings.exclude_env == []


def test_enable_without_a_base_domain_needs_previews_on(store: WASMStore) -> None:
    result = invoke(["preview", "enable", PARENT])

    assert result.exit_code == 1, result.output
    assert store.get_preview_settings(PARENT) is None


def test_enable_refuses_an_invalid_variable_name(store: WASMStore) -> None:
    result = invoke(["preview", "enable", PARENT, "--domain", BASE, "--exclude-env", "NOT-A-NAME"])

    assert result.exit_code == 1, result.output
    assert store.get_preview_settings(PARENT) is None


def test_enable_help_says_builds_run_as_root() -> None:
    result = invoke(["preview", "enable", "--help"])

    assert result.exit_code == 0
    assert "as root" in result.output
    assert "--allow-bots" in result.output and "--exclude-env" in result.output


def test_list_prints_json(store: WASMStore) -> None:
    store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))
    preview(store)

    result = invoke(["preview", "list", PARENT, "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["domain"] == PARENT
    assert body["settings"]["base_domain"] == BASE
    (item,) = body["items"]
    assert (item["domain"], item["url"], item["status"]) == (CHILD, f"https://{CHILD}", "ready")


def test_list_without_previews(store: WASMStore) -> None:
    result = invoke(["preview", "list"])

    assert result.exit_code == 0, result.output


def test_disable_asks_before_removing_previews(store: WASMStore, deletions: list[str]) -> None:
    store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))
    preview(store)

    result = invoke(["preview", "disable", PARENT], stdin="n\n")

    assert result.exit_code == 0, result.output
    assert store.get_preview_settings(PARENT) is not None
    assert store.get_preview(PARENT, 7) is not None


def test_disable_yes_removes_them(store: WASMStore, deletions: list[str]) -> None:
    store.save_preview_settings(PreviewSettings(app_domain=PARENT, base_domain=BASE))
    preview(store)

    result = invoke(["preview", "disable", PARENT, "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"domain": PARENT, "enabled": False, "removed": [CHILD]}
    assert store.get_preview_settings(PARENT) is None
    assert store.get_preview(PARENT, 7) is None


def test_remove_one(store: WASMStore, deletions: list[str]) -> None:
    preview(store)
    store.create_app(App(domain=CHILD, app_type="nodejs", app_path="/x"))
    store.set_preview_parent(CHILD, PARENT)

    result = invoke(["preview", "remove", CHILD])

    assert result.exit_code == 0, result.output
    assert deletions == [CHILD]
    assert store.get_preview(PARENT, 7) is None


def test_remove_refuses_an_application_that_is_not_a_preview(
    store: WASMStore, deletions: list[str]
) -> None:
    result = invoke(["preview", "remove", PARENT])

    assert result.exit_code == 1, result.output
    assert "not a preview" in str(result.exception)
    assert deletions == []


def test_sweep_removes_the_expired(
    store: WASMStore, deletions: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    preview(store)
    monkeypatch.setattr(previews, "_now", lambda: datetime(2026, 10, 6, tzinfo=timezone.utc))

    result = invoke(["preview", "sweep", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"removed": [CHILD]}
