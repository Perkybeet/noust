# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The wizard's inspection reads other platforms' configuration too.

A repository that carries ``vercel.json``, ``render.yaml`` and the like gets
their proposal attached to the inspection, so the review can prefill the
build, the variables and the health check; the sparse checkout fetches those
files for it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from wasm.core.store import WASMStore
from wasm.deployers.importers import PLATFORM_FILES
from wasm.deployers.inspect import inspect_source, sparse_patterns

PACKAGE_JSON = json.dumps({"name": "shop", "scripts": {"start": "node server.js"}})


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[WASMStore]:
    """Detection builds deployers, which read the store."""
    WASMStore.reset_instance()
    instance = WASMStore(tmp_path / "wasm.db")
    yield instance
    WASMStore.reset_instance()


def project(root: Path, files: dict[str, str]) -> Path:
    """Write a fake repository."""
    root.mkdir()
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_the_sparse_checkout_fetches_every_platform_file() -> None:
    patterns = sparse_patterns()
    for name in PLATFORM_FILES:
        assert f"/{name}" in patterns


def test_an_inspection_carries_the_render_proposal(tmp_path: Path) -> None:
    repo = project(
        tmp_path / "repo",
        {
            "package.json": PACKAGE_JSON,
            "render.yaml": "services:\n  - type: web\n    runtime: node\n"
            "    healthCheckPath: /healthz\n    envVars:\n      - key: SESSION_SECRET\n"
            "        generateValue: true\n",
        },
    )
    result = inspect_source(str(repo))

    assert result.app_type == "nodejs"
    proposal = result.platform_proposal
    assert proposal is not None
    assert proposal.platform == "render"
    assert proposal.health_path == "/healthz"
    assert [v.name for v in proposal.env] == ["SESSION_SECRET"]
    assert proposal.env[0].generated


def test_an_inspection_without_platform_files_has_no_proposal(tmp_path: Path) -> None:
    repo = project(tmp_path / "repo", {"package.json": PACKAGE_JSON})
    assert inspect_source(str(repo)).platform_proposal is None


def test_a_broken_platform_file_does_not_fail_the_inspection(tmp_path: Path) -> None:
    repo = project(tmp_path / "repo", {"package.json": PACKAGE_JSON, "vercel.json": "{"})
    result = inspect_source(str(repo))
    assert result.app_type == "nodejs"
    assert result.platform_proposal is not None
    assert any("not valid JSON" in w for w in result.platform_proposal.warnings)
