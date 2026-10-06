# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
scripts/database_pins.py: the one way an instance's image digest is read and compared.

A digest in the catalog is never typed from memory, so what is pinned here is what
the tool reports when the registry and the catalog disagree: a tag that moved, an
image that is not published for both architectures a server runs on, a registry
that does not answer. No network: the registry functions it shares with
``scripts/recipe_pins.py`` are replaced.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from noust.managers.database.instance_catalog import SPECS

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "database_pins.py"


@pytest.fixture
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("database_pins", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["database_pins"] = module
    spec.loader.exec_module(module)
    return module


def registry(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, *, moved: str | None = None, **kwargs
) -> None:
    """A registry that serves exactly what the catalog pins, but for what a test changes."""
    pinned = {
        f"{spec.repository}:{pin.version}": pin.digest
        for spec in SPECS.values()
        for pin in spec.pins
    }
    broken = kwargs.get("broken")
    only_amd64 = kwargs.get("only_amd64")

    def resolve_digest(reference: str) -> str:
        if reference == broken:
            raise RuntimeError(f"{reference}: the registry answered HTTP 500")
        if reference == moved:
            return "sha256:" + "f" * 64
        return pinned[reference]

    def resolve_platforms(reference: str, digest: str) -> set[str]:
        return {"amd64"} if reference == only_amd64 else {"amd64", "arm64", "arm"}

    fake = SimpleNamespace(resolve_digest=resolve_digest, resolve_platforms=resolve_platforms)
    monkeypatch.setattr(tool, "_recipe_pins", lambda: fake)


def test_when_the_registry_serves_what_the_catalog_pins_nothing_is_reported(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry(tool, monkeypatch)

    report = tool.check([])

    assert report["problems"] == []
    assert len(report["images"]) == sum(len(spec.pins) for spec in SPECS.values())
    assert all(entry["served"] == entry["pinned"] for entry in report["images"])


def test_a_tag_that_moved_says_which_and_what_to_do(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry(tool, monkeypatch, moved="postgres:17")

    (problem,) = tool.check([])["problems"]

    assert problem.startswith("postgres:17: the catalog pins sha256:")
    assert "sha256:" + "f" * 64 in problem and "release notes" in problem


def test_an_image_that_is_not_published_for_arm_is_reported(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry(tool, monkeypatch, only_amd64="mongo:8.0")

    (problem,) = tool.check([])["problems"]

    assert "mongo:8.0" in problem and "arm64" in problem


def test_a_registry_that_does_not_answer_is_one_problem_and_the_rest_are_still_read(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry(tool, monkeypatch, broken="redis:8.0")

    report = tool.check([])

    assert any("HTTP 500" in problem for problem in report["problems"])
    assert len(report["images"]) == sum(len(spec.pins) for spec in SPECS.values())


def test_only_the_flavours_named_are_read(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry(tool, monkeypatch)

    report = tool.check(["valkey"])

    assert {entry["flavour"] for entry in report["images"]} == {"valkey"}


def test_the_exit_status_and_the_evidence(
    tool: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    registry(tool, monkeypatch)
    evidence = tmp_path / "evidence.json"

    assert tool.main(["check", "--evidence", str(evidence)]) == 0
    written = json.loads(evidence.read_text())
    assert written["problems"] == [] and written["images"]

    registry(tool, monkeypatch, moved="mariadb:11.8")
    assert tool.main(["check", "mariadb"]) == 1
    assert "differs" in capsys.readouterr().out.lower()
