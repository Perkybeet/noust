# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_env_merge.py`` that other tests build on."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def merge(client: TestClient, body: dict[str, Any]) -> Any:
    """
    Args:
        client: A signed-in client.
        body: The merge request.

    Returns:
        The response.
    """
    return client.patch("/api/apps/example.com/env", json=body)


def env_file(directory: Path) -> dict[str, str]:
    """
    Args:
        directory: The application's directory.

    Returns:
        The variables its ``.env`` holds.
    """
    pairs = (
        line.split("=", 1) for line in (directory / ".env").read_text().splitlines() if "=" in line
    )
    return {name: value.strip("'\"") for name, value in pairs}
