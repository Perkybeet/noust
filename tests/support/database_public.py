# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_public.py`` that other tests build on."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.database import public_access
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.public_access import PublicAccess
from noust.managers.database.service import DatabaseService
from tests.support.database_public_support import PublicHostRunner, fresh_state, public_engine

ENGINE = "postgresql"

PASSWORD = "Once-0nly-Generated-9!"


@dataclass
class World:
    """Everything a test touches."""

    access: PublicAccess
    service: DatabaseService
    runner: PublicHostRunner
    state: dict[str, Any]
    engine: type[BaseDatabaseManager]
    units: Path
    store: NoustStore


def build_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ufw: str = "active",
    tls_blocker: str | None = None,
) -> World:
    """
    Build a server with a PostgreSQL, a firewall and systemd, in a temporary root.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper.
        ufw: ``active``, ``inactive`` or ``absent``.
        tls_blocker: Why the engine cannot require TLS; None when it can.

    Returns:
        The world.
    """
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "state" / "noust.db")
    units = tmp_path / "systemd"
    units.mkdir()
    proxy = tmp_path / "systemd-socket-proxyd"
    proxy.write_text("#!/bin/sh\n")
    monkeypatch.setattr(public_access, "SYSTEMD_DIR", units)
    monkeypatch.setattr(public_access, "PROXY_CANDIDATES", (proxy,))
    state = fresh_state()
    engine = public_engine(tmp_path, state, tls_blocker=tls_blocker)
    runner = PublicHostRunner(ufw=ufw)
    set_runner(runner)
    service = DatabaseService(
        resolve=lambda name: engine() if name.lower() in ("postgresql", "pg") else None,
        engines=lambda: [ENGINE],
        store=store,
        secrets=SecretStore(root=tmp_path / "state" / "secrets"),
    )
    access = PublicAccess(service, runner=runner)
    return World(access, service, runner, state, engine, units, store)
