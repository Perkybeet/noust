# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The backups an application starts life with when it is installed from a recipe (checklist D-18)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core.exceptions import BackupError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, Database, NoustStore
from noust.recipes import get_recipe
from noust.recipes.backups import (
    DEFAULT_DAYS,
    DEFAULT_KEEP,
    DEFAULT_SCHEDULE,
    DefaultBackups,
    apply_default_backups,
)
from noust.recipes.deploy import RecipePlan, finish_recipe

DOMAIN = "wiki.example.com"


@pytest.fixture
def scheduled(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    The two managers a policy is made through, replaced by recorders.

    ``database_error`` and ``data_error`` make the corresponding call raise;
    ``targets`` is what the application declares as data.
    """
    state = SimpleNamespace(
        databases=[], data=[], database_error=None, data_error=None, targets=["data"]
    )

    class FakeBackups:
        def __init__(self, service: Any) -> None:
            pass

        def set_policy(self, engine: str, database: str, **kwargs: Any) -> None:
            if state.database_error is not None:
                raise state.database_error
            state.databases.append((engine, database, kwargs))

    class FakeVolumes:
        def __init__(self, logger: Any = None) -> None:
            pass

        def targets(self, domain: str) -> list[str]:
            return list(state.targets)

        def set_policy(self, domain: str, **kwargs: Any) -> None:
            if state.data_error is not None:
                raise state.data_error
            state.data.append((domain, kwargs))

    monkeypatch.setattr("noust.managers.database.backups.DatabaseBackups", FakeBackups)
    monkeypatch.setattr("noust.managers.volume_backups.VolumeBackupManager", FakeVolumes)
    monkeypatch.setattr(
        "noust.managers.database.service.DatabaseService", lambda logger=None: object()
    )
    return state


@pytest.fixture
def app(recipe_store: NoustStore) -> App:
    """An application with a PostgreSQL database and a Redis cache."""
    created = recipe_store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/srv/wiki"))
    recipe_store.create_database(Database(name="wiki", engine="postgresql", app_id=created.id))
    recipe_store.create_database(Database(name="0", engine="redis", app_id=created.id))
    return created


def test_a_database_is_dumped_daily_and_each_dump_is_test_restored(
    app: App, recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    result = apply_default_backups(DOMAIN, logger=Logger(), store=recipe_store)

    [(engine, database, kwargs)] = scheduled.databases
    assert (engine, database) == ("postgresql", "wiki")
    assert kwargs == {
        "schedule": DEFAULT_SCHEDULE,
        "retention_count": DEFAULT_KEEP,
        "retention_days": DEFAULT_DAYS,
        "verify_restore": True,
    }
    assert result.databases == ("postgresql/wiki",)


def test_a_cache_is_not_scheduled(
    app: App, recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    apply_default_backups(DOMAIN, logger=Logger(), store=recipe_store)

    assert all(engine != "redis" for engine, _database, _kwargs in scheduled.databases)


def test_the_data_gets_a_daily_archive_too(
    app: App, recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    result = apply_default_backups(DOMAIN, logger=Logger(), store=recipe_store)

    assert scheduled.data == [
        (
            DOMAIN,
            {
                "schedule": DEFAULT_SCHEDULE,
                "retention_count": DEFAULT_KEEP,
                "retention_days": DEFAULT_DAYS,
            },
        )
    ]
    assert result.data is True
    assert any("test-restored" in note for note in result.notes)
    assert any("noust backup destination add" in note for note in result.notes)


def test_an_application_with_no_data_gets_no_data_policy_and_no_complaint(
    app: App, recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    scheduled.targets = []

    result = apply_default_backups(DOMAIN, logger=Logger(), store=recipe_store)

    assert scheduled.data == [] and result.data is False
    assert not any("data could not" in note for note in result.notes)


def test_a_policy_that_cannot_be_made_is_a_note_not_a_failed_install(
    app: App, recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    scheduled.database_error = BackupError("systemd refused the timer")
    scheduled.data_error = ValidationError("declares no data")

    result = apply_default_backups(DOMAIN, logger=Logger(), store=recipe_store)

    assert result.databases == () and result.data is False
    assert "No daily backup could be scheduled for postgresql/wiki" in result.notes[0]
    assert "systemd refused the timer" in result.notes[0]
    assert "data could be scheduled" in result.notes[1] or "data could" in result.notes[1]


def test_an_application_that_does_not_exist_has_nothing_scheduled(
    recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    result = apply_default_backups("ghost.example.com", logger=Logger(), store=recipe_store)

    assert result == DefaultBackups()
    assert scheduled.databases == [] and scheduled.data == []


def plan_for(domain: str) -> RecipePlan:
    """A plan that provisioned nothing, for a recipe that does not."""
    return RecipePlan(
        recipe=get_recipe("vaultwarden"),
        domain=domain,
        app_type="docker-compose",
        source="/srv/source",
        branch=None,
        port=8000,
        env_vars={},
        layout="inplace",
        persistent_paths=[],
        notes=("Open it.",),
    )


def test_finishing_a_recipe_schedules_the_backups_and_says_so(
    app: App, recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    notes = finish_recipe(plan_for(DOMAIN), logger=Logger(), store=recipe_store)

    assert notes[0] == "Open it."
    assert any("dumped every day" in note for note in notes)
    assert scheduled.databases and scheduled.data


def test_they_can_be_left_out(
    app: App, recipe_store: NoustStore, scheduled: SimpleNamespace
) -> None:
    notes = finish_recipe(
        plan_for(DOMAIN), logger=Logger(), store=recipe_store, default_backups=False
    )

    assert notes == ["Open it."]
    assert scheduled.databases == [] and scheduled.data == []


def test_the_install_command_offers_to_leave_them_out() -> None:
    result = CliRunner().invoke(cli, ["recipe", "install", "--help"])

    assert "--no-backups" in result.output
