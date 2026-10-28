# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_choice.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.managers.database import flavours
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.server.host import HostPaths
from tests.support.web_databases_api import make_engine

DOMAIN = "shop.example.com"


@pytest.fixture(autouse=True)
def noble(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server on Ubuntu 24.04, whose catalog the versions are checked against."""
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text(
        'ID=ubuntu\nVERSION_CODENAME=noble\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(root))


def engine_classes(
    tmp_path: Path, *, postgres: bool = True, mysql: str | None = None
) -> dict[str, type[BaseDatabaseManager]]:
    """
    Build the fake engines of a server.

    Args:
        tmp_path: Where dumps would go.
        postgres: PostgreSQL 16.3 is installed.
        mysql: ``mysql`` or ``mariadb`` when that flavour is installed.

    Returns:
        The classes by engine name.
    """
    pg = make_engine(tmp_path / "pg", installed=postgres)
    my = make_engine(
        tmp_path / "my",
        engine="mysql",
        display="MySQL/MariaDB",
        installed=mysql is not None,
        running=mysql is not None,
        seeded=False,
    )
    my.get_version = lambda self: "10.11.6" if mysql == "mariadb" else "8.0.36"  # type: ignore[method-assign]
    my.installed_flavour = lambda self: mysql  # type: ignore[method-assign]
    return {"postgresql": pg, "mysql": my}
