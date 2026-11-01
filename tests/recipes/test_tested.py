# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The catalogue says of each recipe whether it was started for real, and for which version."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from noust.recipes import list_recipes, search_recipes
from noust.recipes import tested as tested_module

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_tested.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    """The script that writes recipes/tested.json."""
    spec = importlib.util.spec_from_file_location("recipe_tested", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_tested"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def entries(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    """Replace what the package carries with these runs."""
    runs: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(tested_module, "tested_recipes", lambda: runs)
    return runs


def test_a_recipe_that_was_started_says_which_version_when_and_how_long(
    entries: dict[str, dict[str, Any]],
) -> None:
    """The version it was started at, the day, the seconds, and that it is the one pinned now."""
    entries["gotify"] = {"version": "3.1.1", "date": "2026-10-28", "seconds": 37}

    view = tested_module.tested_view("gotify", "3.1.1")

    assert view == {"version": "3.1.1", "date": "2026-10-28", "seconds": 37, "current": True}


def test_a_recipe_revised_since_its_run_still_says_it_was_tested_and_for_which_version(
    entries: dict[str, dict[str, Any]],
) -> None:
    """``current`` is whether the run was of the version the recipe pins now."""
    entries["gotify"] = {"version": "3.1.0", "date": "2026-10-28", "seconds": 37}

    view = tested_module.tested_view("gotify", "3.1.1")

    assert view is not None and view["current"] is False and view["version"] == "3.1.0"


def test_a_recipe_nobody_started_and_an_operator_s_own_say_nothing(
    entries: dict[str, dict[str, Any]],
) -> None:
    """A recipe of the operator's with a shipped one's name is not the one that was started."""
    entries["gotify"] = {"version": "3.1.1", "date": "2026-10-28", "seconds": 37}

    assert tested_module.tested_view("memos", "1.0.0") is None
    assert tested_module.tested_view("gotify", "3.1.1", origin="operator") is None


def test_the_summary_and_the_filters_carry_it(entries: dict[str, dict[str, Any]]) -> None:
    """``tested`` is in every summary; the listing filters by it."""
    first = next(recipe for recipe in list_recipes() if recipe.available)
    entries[first.name] = {"version": first.version, "date": "2026-10-28", "seconds": 5}

    assert first.summary()["tested"]["current"] is True
    assert [recipe.name for recipe in search_recipes(tested=True)] == [first.name]
    assert first.name not in {recipe.name for recipe in search_recipes(tested=False)}
    assert all(recipe.summary()["tested"] is None for recipe in search_recipes(tested=False))


def test_only_the_runs_that_passed_of_recipes_in_the_catalogue_are_written(
    tool: ModuleType,
) -> None:
    """A failure is not a test passed, and a draft that never entered is not the catalogue's."""
    results = {
        "results": [
            {
                "recipe": "b",
                "version": "1",
                "result": "pass",
                "date": "2026-10-28T10:00:00Z",
                "seconds": 40,
            },
            {
                "recipe": "a",
                "version": "2",
                "result": "pass",
                "date": "2026-10-27T10:00:00Z",
                "seconds": 5,
            },
            {
                "recipe": "c",
                "version": "3",
                "result": "fail",
                "date": "2026-10-28T10:00:00Z",
                "seconds": 9,
            },
            {
                "recipe": "draft",
                "version": "1",
                "result": "pass",
                "date": "2026-10-28T10:00:00Z",
                "seconds": 9,
            },
        ]
    }

    built = tool.build(results, {"a", "b", "c"})

    assert list(built["recipes"]) == ["a", "b"]
    assert built["recipes"]["b"] == {"version": "1", "date": "2026-10-28", "seconds": 40}


def test_the_file_the_package_carries_is_what_the_results_say() -> None:
    """Regenerate it with scripts/recipe_tested.py after a run: a stale file fails here."""
    spec = importlib.util.spec_from_file_location("recipe_tested_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--check"]) == 0


def test_every_run_the_package_carries_is_of_a_recipe_it_has() -> None:
    """No name outside the catalogue, no version that is not a string."""
    path = Path(tested_module.__file__).with_name("tested.json")
    carried = json.loads(path.read_text(encoding="utf-8"))["recipes"] if path.is_file() else {}
    names = {recipe.name for recipe in list_recipes()}

    assert set(carried) <= names
    assert all(isinstance(entry["version"], str) for entry in carried.values())


def test_a_recipe_that_is_only_listed_is_not_carried_as_started(tool: ModuleType) -> None:
    """Whoogle was started on 2026-10-30 and withdrawn after: a tested badge on a listing misleads."""
    path = Path(tested_module.__file__).with_name("tested.json")
    carried = json.loads(path.read_text(encoding="utf-8"))["recipes"]
    listed = {recipe.name for recipe in list_recipes() if not recipe.available}

    assert listed and not listed & set(carried)
    assert "whoogle" not in tool.installable() and "searxng" in tool.installable()
    assert tool.installable() == {recipe.name for recipe in list_recipes() if recipe.available}
