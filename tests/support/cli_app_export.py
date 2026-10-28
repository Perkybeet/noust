# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_cli_app_export.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.commands import app as app_module
from noust.core.appdir.layout import env_file_for
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore
from noust.deployers import app_export

DOMAIN = "shop.example.com"
STRIPE = "sk_live_" + "51Habcdefghijklmn" + "opqrstuvwxyz"


class NoCron:
    """A cron manager with no jobs."""

    def list_jobs(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture(autouse=True)
def _real_runner() -> Iterator[None]:
    """--dry-run installs a rehearsing runner process-wide; put the default back."""
    yield
    set_runner(None)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(app_export, "CronManager", NoCron)
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


@pytest.fixture
def shop(store: NoustStore, tmp_path: Path) -> App:
    app = store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/acme/shop.git",
            port=3000,
            app_path=str(tmp_path / "apps" / "shop-example-com"),
            memory_max_mb=512,
        )
    )
    env = env_file_for(app)
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(f"APP_NAME=Shop\nSTRIPE_KEY={STRIPE}\n")
    return app


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for wasm create's deploy, recording what reached it."""
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(app_module, "_create_app", create)
    return calls


def invoke(args: list[str]) -> Result:
    return CliRunner().invoke(root_cli, args)
