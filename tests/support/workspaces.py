# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_workspaces.py`` that other tests build on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

NEXT = {"name": "web", "dependencies": {"next": "15", "react": "19", "@acme/ui": "workspace:*"}}
API = {
    "name": "@acme/api",
    "scripts": {"start": "node dist/main.js"},
    "dependencies": {"express": "4", "@acme/ui": "workspace:*", "@acme/db": "workspace:*"},
}
UI = {"name": "@acme/ui", "main": "index.js", "dependencies": {"react": "19"}}
DB = {"name": "@acme/db", "main": "index.js", "dependencies": {"@acme/config": "workspace:*"}}
CONFIG = {"name": "@acme/config", "main": "index.js"}


def write_json(root: Path, name: str, data: object) -> None:
    """
    Args:
        root: The repository.
        name: A path inside it.
        data: What the JSON file holds.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def write(root: Path, name: str, text: str = "") -> None:
    """
    Args:
        root: The repository.
        name: A path inside it.
        text: Its content.
    """
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def turbo(tmp_path: Path) -> Path:
    """
    Returns:
        A pnpm workspace with Turborepo: two applications and three libraries.
    """
    write_json(
        tmp_path,
        "package.json",
        {"name": "acme", "private": True, "devDependencies": {"turbo": "2"}},
    )
    write(tmp_path, "pnpm-workspace.yaml", "packages:\n  - apps/*\n  - packages/*\n")
    write(tmp_path, "pnpm-lock.yaml", "lockfileVersion: 9\n")
    write_json(tmp_path, "turbo.json", {"tasks": {"build": {}}})
    write_json(tmp_path, "apps/web/package.json", NEXT)
    write_json(tmp_path, "apps/api/package.json", API)
    write_json(tmp_path, "packages/ui/package.json", UI)
    write_json(tmp_path, "packages/db/package.json", DB)
    write_json(tmp_path, "packages/config/package.json", CONFIG)
    return tmp_path
