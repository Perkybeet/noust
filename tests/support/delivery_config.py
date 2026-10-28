# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_delivery_config.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import delivery
from noust.core.config import Config


@pytest.fixture
def config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """
    A configuration file of the test's own, read fresh.

    Yields:
        The configuration.
    """
    path = sandbox / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    delivery.forget()
    monkeypatch.undo()  # the autouse stand-in of the settings is for the other tests
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    # The undo also dropped the isolation of the store's paths (tests/conftest.py), which would
    # leave every test that asks this fixture for a store reading the developer's own.
    from noust.core import store as store_module

    monkeypatch.setattr(store_module, "USER_DB_PATH", sandbox / "user-store" / "noust.db")
    monkeypatch.setattr(store_module, "DEFAULT_DB_PATH", sandbox / "system-store" / "noust.db")
    try:
        yield Config()
    finally:
        Config.reset_instance()
        delivery.forget()
