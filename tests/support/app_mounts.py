# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_app_mounts.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.fs import RealFileSystem
from noust.core.store import App, NoustStore

DOMAIN = "shop.example.com"
RELEASE = "20260925-120000-aaaaaaa"


class OwnerFs(RealFileSystem):
    """The real filesystem, except that handing a file to an account is recorded."""

    def __init__(self) -> None:
        self.owned: list[tuple[Path, str, str, int]] = []

    def set_owner(self, path: Path, *, user: str, group: str, mode: int) -> None:
        self.owned.append((path, user, group, mode))
        path.chmod(mode)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    base = tmp_path / "apps" / "shop-example-com"
    (base / "releases" / RELEASE).mkdir(parents=True)
    (base / "current").symlink_to(f"releases/{RELEASE}")
    (base / "shared").mkdir()
    return base


def add_app(store: NoustStore, root: Path, **fields: Any) -> App:
    values: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "nodejs",
        "app_path": str(root),
        "layout": "releases",
        "identity": "noust-app-shop",
    }
    values.update(fields)
    store.create_app(App(**values))
    row = store.get_app(DOMAIN)
    assert row is not None
    return row


@pytest.fixture
def app(store: NoustStore, root: Path) -> App:
    return add_app(store, root)
