# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_lifecycle.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import App, NoustStore
from noust.deployers import lifecycle
from noust.deployers.interface import UpdateResult

DOMAIN = "example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NoustStore:
    """A store in the test's directory, installed where the module looks for it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


class Recorder:
    """Everything the update asked of its collaborators, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.configured: dict[str, Any] = {}
        self.service_exists = True
        self.service_active = True
        # The branch the checkout is on, as git would answer it.
        self.checkout_branch: str | None = None


class FakeDeployer:
    """A deployer that records instead of building."""

    def __init__(self, recorder: Recorder, *, is_static: bool = False) -> None:
        self.recorder = recorder
        self.is_static = is_static

    def configure(self, **kwargs: Any) -> None:
        self.recorder.configured = kwargs

    def update(self, on_step: Any = None) -> UpdateResult:
        self.recorder.calls.append(("update",))
        if on_step:
            on_step("Building")
        return UpdateResult(
            package_manager="npm",
            prisma_updated=False,
            is_static=self.is_static,
            start_command="" if self.is_static else "/usr/bin/npm run start",
        )

    def deploy(self) -> bool:
        self.recorder.calls.append(("deploy",))
        return True


def make_app(store: NoustStore, app_path: Path, app_type: str = "nextjs") -> App:
    """Register an application whose tree lives at ``app_path``."""
    (app_path / ".git").mkdir(parents=True, exist_ok=True)
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type=app_type,
            source="https://github.com/example/app.git",
            branch="main",
            port=3000,
            app_path=str(app_path),
        )
    )
