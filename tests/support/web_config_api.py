# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_config_api.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.web.api import config as config_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from tests.support.config_files import stored_config


@pytest.fixture
def config_path(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point the global config file at the sandbox and reset the singleton.

    Args:
        sandbox: Isolated filesystem root.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        Path the API reads from and writes to.
    """
    path = sandbox / "etc" / "wasm" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def client(config_path: Path) -> TestClient:
    """
    Build a test client for the config router with authentication stubbed out.

    Args:
        config_path: Fixture redirecting configuration writes into the sandbox.

    Returns:
        A client whose requests are already authenticated.
    """
    app = FastAPI()
    app.include_router(config_api.router, prefix="/api/config")
    # The same error contract the real application answers in, so a 422 is
    # asserted field by field rather than in FastAPI's own shape.
    install_error_handlers(app)
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test", "type": "master"}
    return TestClient(app, raise_server_exceptions=False)


def stored_value(config_path: Path, dotted_key: str) -> Any:
    """
    Read a value straight from the configuration file on disk.

    Args:
        config_path: Path of the configuration file.
        dotted_key: Dotted path of the setting.

    Returns:
        The stored value, or None when absent.
    """
    node: Any = stored_config(config_path)
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node
