# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_update_backup_required.py`` that other tests build on."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.store import Database, NoustStore
from noust.deployers import lifecycle
from tests.support.lifecycle import DOMAIN, FakeDeployer, Recorder


@pytest.fixture
def backups(monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> SimpleNamespace:
    """
    Every collaborator of an update faked, and the pre-update backup scripted.

    ``failure`` is raised by the backup when set; ``taken`` records what it was asked.
    """
    rec = Recorder()
    state = SimpleNamespace(recorder=rec, failure=None, taken=[], release_calls=[])

    def take(**kwargs: Any) -> None:
        state.taken.append(kwargs)
        rec.calls.append(("backup", kwargs["domain"]))
        if state.failure is not None:
            raise state.failure
        return SimpleNamespace(id="backup-1")

    monkeypatch.setattr(
        lifecycle,
        "RollbackManager",
        lambda verbose=False: SimpleNamespace(create_pre_deploy_backup=take),
    )
    monkeypatch.setattr(
        lifecycle,
        "SourceManager",
        lambda verbose=False: SimpleNamespace(
            get_repo_info=lambda path: {"commit": "abc1234", "branch": None},
            pull=lambda path, branch=None: rec.calls.append(("pull",)),
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "ServiceManager",
        lambda verbose=False: SimpleNamespace(
            get_status=lambda name: {"exists": True, "active": True},
            restart=lambda name: rec.calls.append(("restart", name)) or True,
            get_service_config=lambda name: None,
        ),
    )
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        lifecycle, "get_deployer", lambda app_type, verbose=False: FakeDeployer(rec)
    )

    def update_release(*args: Any, **kwargs: Any) -> str:
        rec.calls.append(("update_release",))
        return "released"

    monkeypatch.setattr(lifecycle, "_update_release", update_release)
    return state


def with_database(store: NoustStore, domain: str = DOMAIN) -> None:
    """Link a database to the application."""
    app = store.get_app(domain)
    assert app is not None and app.id is not None
    store.create_database(Database(name="shop", engine="postgresql", app_id=app.id))
