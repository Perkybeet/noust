# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_clone.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, DomainKind, NoustStore
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.app_env import write_app_env

SOURCE = "shop.example.com"

GIT = "https://github.com/acme/shop.git"


@pytest.fixture
def app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore, runner: FakeRunner
) -> App:
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    store.create_app(
        App(
            domain=SOURCE,
            app_type="nodejs",
            source=GIT,
            branch="main",
            port=3001,
            app_path=str(root),
            layout="releases",
        )
    )
    store.add_domain(SOURCE, f"alias.{SOURCE}", DomainKind.ALIAS.value) if hasattr(
        store, "add_domain"
    ) else None
    found = store.get_app(SOURCE)
    assert found is not None
    write_app_env(found, {"API_URL": "https://shop.example.com", "FLAG": "1"})
    return found
