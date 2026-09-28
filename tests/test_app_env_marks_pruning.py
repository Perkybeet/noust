# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for pruning stale ``env_secret_marks`` in the one writer they all share.

Verified finding 3 of the 2.2 pre-release review: a mark an operator set on a
variable must not outlive that variable. Before this fix only
``PUT /api/apps/{domain}/env`` dropped a mark for a name a write removed;
``wasm env configure`` (and any other caller of
:func:`~wasm.deployers.helpers.app_env.write_app_env`) left it sitting in the
store, so a later write that happened to reuse the same name for a real
secret silently inherited a stale "not secret" - or "secret" - verdict from
whatever used to live under that name. The prune now lives inside
``write_app_env`` itself, the one function every writer - the CLI included -
goes through, so there is exactly one place this can be forgotten again.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from wasm.core.runner import FakeRunner
from wasm.core.secret_detection import classify
from wasm.core.store import App, WASMStore
from wasm.deployers.helpers import app_env as app_env_module
from wasm.deployers.helpers.app_env import write_app_env

DOMAIN = "example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WASMStore]:
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


@pytest.fixture
def deployed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: WASMStore, runner: FakeRunner
) -> App:
    """
    Register an application deployed in place, with a store row of its own.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
        store: The store fixture.
        runner: Fake runner for the ownership hand-over of a written .env.

    Returns:
        The application row.
    """
    app_path = tmp_path / "apps" / "example-com"
    app_path.mkdir(parents=True)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    return store.create_app(App(domain=DOMAIN, app_path=str(app_path)))


def test_a_dropped_variables_mark_is_pruned_by_the_writer(deployed: App, store: WASMStore) -> None:
    """The one writer every caller shares prunes a mark for a name it removes."""
    write_app_env(deployed, {"API_KEY": "not-really-secret"})
    store.set_env_secret_marks(DOMAIN, {"API_KEY": False})
    app = store.get_app(DOMAIN)
    assert app is not None
    assert app.env_secret_marks == {"API_KEY": False}

    # A later write - wasm env configure regenerating from a changed
    # .env.example, say - drops API_KEY entirely.
    write_app_env(app, {"OTHER": "1"})

    after = store.get_app(DOMAIN)
    assert after is not None
    assert "API_KEY" not in after.env_secret_marks


def test_a_stale_not_secret_mark_does_not_leak_onto_a_later_real_secret(
    deployed: App, store: WASMStore
) -> None:
    """The exact scenario the finding names: a reused name must not inherit the old verdict."""
    write_app_env(deployed, {"API_KEY": "not-really-secret"})
    store.set_env_secret_marks(DOMAIN, {"API_KEY": False})

    app = store.get_app(DOMAIN)
    assert app is not None
    write_app_env(app, {"OTHER": "1"})  # API_KEY dropped, its mark pruned

    app = store.get_app(DOMAIN)
    assert app is not None
    real_secret = "sk_l" + "ive_9f3c8b2a1e0d7c6f5a4b3d2e1f0"
    write_app_env(app, {"API_KEY": real_secret})

    app = store.get_app(DOMAIN)
    assert app is not None
    verdict = classify("API_KEY", real_secret, app.env_secret_marks)
    assert verdict.secret is True
    assert verdict.reason == "value: stripe"


def test_a_mark_for_a_variable_still_present_is_kept(deployed: App, store: WASMStore) -> None:
    """Only a name the write actually drops is pruned - not everything else."""
    write_app_env(deployed, {"API_KEY": "unchanged", "OTHER": "1"})
    store.set_env_secret_marks(DOMAIN, {"API_KEY": False, "OTHER": True})

    app = store.get_app(DOMAIN)
    assert app is not None
    write_app_env(app, {"API_KEY": "still-here", "OTHER": "2"})

    after = store.get_app(DOMAIN)
    assert after is not None
    assert after.env_secret_marks == {"API_KEY": False, "OTHER": True}


def test_an_undeployed_stand_in_application_is_not_an_error(
    tmp_path: Path, store: WASMStore, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory-only application (no store row) has no marks to prune."""
    from wasm.deployers.helpers.layout import layout_on_disk

    app_path = tmp_path / "apps" / "unregistered-com"
    app_path.mkdir(parents=True)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    app = App(domain="unregistered.com", app_path=str(app_path), layout=layout_on_disk(app_path))

    write_app_env(app, {"FEATURE": "on"})  # must not raise
