# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust health`` and ``GET /api/system/health`` say that an installed recipe has a newer version
(checklist D-15).

Coolify never looks at a template again once a service was made from it; Noust knows which
version each application was installed from. The notification says it when it changes; this is the
line a person reads whenever they run the report: informational, because an old recipe is a choice
and not a fault, and absent when no application was installed from one.
"""

from __future__ import annotations

import pytest

from noust.managers import health
from noust.recipes import updates as recipe_updates
from noust.recipes.updates import RecipeUpdateStatus


def status(domain: str, installed: str, latest: str, *, ahead: bool = True) -> RecipeUpdateStatus:
    return RecipeUpdateStatus(
        domain=domain,
        recipe=domain.split(".")[0],
        kind="compose",
        installed_version=installed,
        installed_revision=1,
        latest_version=latest,
        latest_revision=1,
        update_available=ahead,
    )


def test_a_recipe_behind_the_catalogue_is_named_with_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        recipe_updates,
        "installed_statuses",
        lambda: [status("vault.example.com", "1.37.4", "1.38.0")],
    )

    check = health._check_recipes()

    assert check is not None
    assert (check.name, check.status) == ("Recipes", "info")
    assert "vault.example.com" in check.value
    assert "1.37.4" in check.value and "1.38.0" in check.value
    assert "noust recipe outdated" in check.value


def test_many_are_counted_and_the_first_few_named(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [status(f"app{n}.example.com", "1", "2") for n in range(7)]
    monkeypatch.setattr(recipe_updates, "installed_statuses", lambda: rows)

    check = health._check_recipes()

    assert check is not None
    assert check.value.startswith("7 installed recipes have a newer version")
    assert "app0.example.com" in check.value and "app6.example.com" not in check.value


def test_recipes_all_current_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recipe_updates,
        "installed_statuses",
        lambda: [status("vault.example.com", "1.38.0", "1.38.0", ahead=False)],
    )

    check = health._check_recipes()

    assert check is not None and check.status == "ok"
    assert "1 installed recipe" in check.value


def test_without_a_recipe_installed_there_is_no_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recipe_updates, "installed_statuses", lambda: [])

    assert health._check_recipes() is None


def test_a_recipe_the_catalogue_dropped_is_not_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    gone = RecipeUpdateStatus(
        domain="old.example.com",
        recipe="old",
        kind="native",
        installed_version="1",
        installed_revision=1,
        latest_version=None,
        latest_revision=None,
        update_available=False,
        reason="The recipe is no longer in the catalogue",
    )
    monkeypatch.setattr(recipe_updates, "installed_statuses", lambda: [gone])

    check = health._check_recipes()

    assert check is not None and check.status == "ok"


def test_a_failure_to_read_is_not_a_failure_of_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> list[RecipeUpdateStatus]:
        raise OSError("catalogue unreadable")

    monkeypatch.setattr(recipe_updates, "installed_statuses", broken)

    check = health._check_recipes()

    assert check is not None and check.status == "info"
    assert "catalogue unreadable" in check.value


def test_the_report_carries_it_where_the_command_prints_it() -> None:
    check = health.HealthCheck("Recipes", "1 installed recipe has a newer version", "info")
    report = health.HealthReport(
        disk=None,
        nginx=health.HealthCheck("Nginx", "Running", "ok"),
        apache=health.HealthCheck("Apache", "Not installed", "info"),
        applications=health.HealthCheck("Applications", "none", "ok"),
        certificates=health.HealthCheck("Certificates", "none", "ok"),
        memory=health.HealthCheck("Memory", "fine", "ok"),
        recipes=check,
    )

    assert check in report.checks
