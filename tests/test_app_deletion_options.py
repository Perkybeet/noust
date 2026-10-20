# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What deleting an application takes with it is the operator's explicit choice (checklist B-83).

Docker volumes and databases hold data, so neither goes unless asked for: ``--remove-volumes``
and ``--remove-databases`` on the command line, ``remove_volumes`` and ``remove_databases`` over
the API. A database is dropped only when it belongs to the application and no other application
uses it, after a last dump; every other one stays, and the deletion says which and how to remove
it. The certificate has an option of its own, and the command asks for the application's name
to be typed rather than a ``y``.
"""

# The deletion harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import webapp
from noust.core.exceptions import DatabaseError
from noust.core.store import App, Database, NoustStore
from noust.deployers import lifecycle
from noust.managers.database.records import DatabaseLink, DatabaseRecords
from noust.web.jobs import delete_app_job
from tests.test_app_deletion import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    apps_directory,
    compose_app,
    downs,
    job_context,
    machine,
    store,
)

OTHER = "other.example.com"


@dataclass
class Dropped:
    engine: str
    name: str
    options: dict[str, Any]


class FakeDatabases:
    """The database service, recording what it was asked to drop."""

    dropped: list[Dropped] = []
    refuse: set[str] = set()

    def __init__(self, **_: Any) -> None:
        pass

    def drop(self, engine: str, name: str, **options: Any) -> Any:
        if name in FakeDatabases.refuse:
            raise DatabaseError(f"cannot drop {name}")
        FakeDatabases.dropped.append(Dropped(engine, name, options))
        return type("Outcome", (), {"safety_copy": f"/var/backups/{name}.sql.gz"})()


@pytest.fixture(autouse=True)
def databases(monkeypatch: pytest.MonkeyPatch) -> type[FakeDatabases]:
    FakeDatabases.dropped = []
    FakeDatabases.refuse = set()
    monkeypatch.setattr("noust.managers.database.service.DatabaseService", FakeDatabases)
    return FakeDatabases


def own(store: NoustStore, name: str, *, engine: str = "postgresql") -> Database:
    app = store.get_app(DOMAIN)
    assert app is not None
    return store.create_database(Database(app_id=app.id, name=name, engine=engine))


def link(store: NoustStore, domain: str, name: str, *, engine: str = "postgresql") -> None:
    app = store.get_app(domain)
    assert app is not None and app.id is not None
    DatabaseRecords(store).save_link(DatabaseLink(app_id=app.id, engine=engine, db_name=name))


def other_app(store: NoustStore) -> None:
    store.create_app(App(domain=OTHER, app_type="nodejs", app_path="/var/www/apps/other"))


class TestWhichDatabases:
    def test_the_ones_it_owns_come_first_and_the_ones_it_uses_after(
        self, tmp_path: Path, store: NoustStore, machine: Any
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")
        other_app(store)
        store.create_database(Database(app_id=None, name="shared", engine="postgresql"))
        link(store, DOMAIN, "shared")

        found = lifecycle.deletion_databases(DOMAIN, store=store)

        assert [(d.name, d.owned) for d in found] == [("shop", True), ("shared", False)]

    def test_one_another_application_uses_is_never_dropped(
        self, tmp_path: Path, store: NoustStore, machine: Any
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")
        other_app(store)
        link(store, OTHER, "shop")

        (found,) = lifecycle.deletion_databases(DOMAIN, store=store)

        assert found.owned and found.shared_with == (OTHER,) and not found.will_drop

    def test_an_application_that_is_not_deployed_has_none(self, store: NoustStore) -> None:
        assert lifecycle.deletion_databases("nothing.example.com", store=store) == []


class TestDeletingTheApplication:
    def test_by_default_every_database_stays_and_each_is_said_with_the_way_to_drop_it(
        self, tmp_path: Path, store: NoustStore, machine: Any, databases: type[FakeDatabases]
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")

        outcome = lifecycle.delete_app(DOMAIN)

        assert databases.dropped == [] and outcome.databases_removed == ()
        assert any(
            "noust db drop shop" in note and "--remove-databases" in note for note in outcome.kept
        )
        assert store.get_app(DOMAIN) is None

    def test_asked_for_it_drops_its_own_after_a_last_dump_and_unlinks_it(
        self, tmp_path: Path, store: NoustStore, machine: Any, databases: type[FakeDatabases]
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")

        outcome = lifecycle.delete_app(DOMAIN, remove_databases=True)

        (dropped,) = databases.dropped
        assert (dropped.engine, dropped.name) == ("postgresql", "shop")
        assert dropped.options == {"force": True, "keep_backup": True, "unlink": True}
        assert outcome.databases_removed == ("postgresql/shop",)
        assert outcome.database_dumps == ("/var/backups/shop.sql.gz",)

    def test_a_database_it_only_uses_stays_even_when_asked(
        self, tmp_path: Path, store: NoustStore, machine: Any, databases: type[FakeDatabases]
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        store.create_database(Database(app_id=None, name="shared", engine="postgresql"))
        link(store, DOMAIN, "shared")

        outcome = lifecycle.delete_app(DOMAIN, remove_databases=True)

        assert databases.dropped == []
        assert any("does not belong to it" in note for note in outcome.kept)

    def test_a_database_another_application_uses_stays_even_when_asked(
        self, tmp_path: Path, store: NoustStore, machine: Any, databases: type[FakeDatabases]
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")
        other_app(store)
        link(store, OTHER, "shop")

        outcome = lifecycle.delete_app(DOMAIN, remove_databases=True)

        assert databases.dropped == []
        assert any(OTHER in note and "also used" in note for note in outcome.kept)

    def test_one_that_cannot_be_dropped_is_a_warning_and_the_rest_goes_on(
        self, tmp_path: Path, store: NoustStore, machine: Any, databases: type[FakeDatabases]
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "bad")
        own(store, "good")
        databases.refuse = {"bad"}

        outcome = lifecycle.delete_app(DOMAIN, remove_databases=True)

        assert [d.name for d in databases.dropped] == ["good"]
        assert any("bad" in warning for warning in outcome.warnings)
        assert store.get_app(DOMAIN) is None

    def test_volumes_and_databases_are_two_choices(
        self, tmp_path: Path, store: NoustStore, machine: Any, databases: type[FakeDatabases]
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")

        outcome = lifecycle.delete_app(DOMAIN, remove_volumes=True)

        (down,) = downs(machine.runner)
        assert "--volumes" in down
        assert outcome.volumes_removed and databases.dropped == []

    def test_the_certificate_is_its_own_choice(
        self, tmp_path: Path, store: NoustStore, machine: Any
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")

        lifecycle.delete_app(DOMAIN, remove_certificate=False)

        assert machine.sites == [(DOMAIN, False)]

    def test_the_console_job_carries_both_choices(
        self, tmp_path: Path, store: NoustStore, machine: Any, databases: type[FakeDatabases]
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")

        result = delete_app_job(
            DOMAIN, remove_volumes=True, remove_databases=True, job_context=job_context()
        )

        assert result["databases_removed"] == ["postgresql/shop"]
        assert result["volumes_removed"] is True
        assert "--volumes" in downs(machine.runner)[0]


class TestTheCommand:
    def run(self, *args: str, answer: str | None = None) -> Any:
        return CliRunner().invoke(webapp.cli.commands["delete"], list(args), input=answer)

    def test_the_name_is_typed_and_a_y_does_not_delete(
        self, tmp_path: Path, store: NoustStore, machine: Any
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")

        refused = self.run(DOMAIN, answer="y\n")

        assert "Type its name to confirm" in refused.output
        assert store.get_app(DOMAIN) is not None and "name did not match" in refused.output

    def test_typing_the_name_deletes(self, tmp_path: Path, store: NoustStore, machine: Any) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")

        done = self.run(DOMAIN, answer=f"{DOMAIN}\n")

        assert done.exit_code == 0, done.output
        assert store.get_app(DOMAIN) is None

    def test_force_asks_for_nothing(self, tmp_path: Path, store: NoustStore, machine: Any) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")

        done = self.run(DOMAIN, "-y")

        assert done.exit_code == 0 and "Type its name" not in done.output

    def test_the_flags_reach_the_deletion(
        self,
        tmp_path: Path,
        store: NoustStore,
        machine: Any,
        databases: type[FakeDatabases],
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")

        done = self.run(
            DOMAIN, "-y", "--remove-volumes", "--remove-databases", "--keep-certificate"
        )

        assert done.exit_code == 0, done.output
        assert machine.sites == [(DOMAIN, False)]
        assert "--volumes" in downs(machine.runner)[0]
        assert [d.name for d in databases.dropped] == ["shop"]

    def test_the_question_says_what_goes_with_it(
        self, tmp_path: Path, store: NoustStore, machine: Any
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")

        asked = self.run(DOMAIN, "--remove-volumes", "--remove-databases", answer="no\n")

        assert "Docker volumes" in asked.output
        assert (
            "Database shop on postgresql belongs to" in asked.output and "dropped" in asked.output
        )

    def test_a_dry_run_says_all_of_it_and_touches_nothing(
        self,
        tmp_path: Path,
        store: NoustStore,
        machine: Any,
        databases: type[FakeDatabases],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        compose_app(store, tmp_path / "apps" / "shop-example-com")
        own(store, "shop")
        monkeypatch.setattr(
            webapp,
            "ServiceManager",
            lambda **kw: type("Units", (), {"get_status": lambda self, name: {"exists": False}})(),
        )

        done = CliRunner().invoke(
            root_cli, ["--dry-run", "delete", DOMAIN, "--remove-volumes", "--remove-databases"]
        )

        assert done.exit_code == 0, done.output
        assert store.get_app(DOMAIN) is not None and databases.dropped == []
        assert "Database shop on postgresql belongs to" in done.output
        assert "Remove Docker volumes" in done.output and "Would delete" in done.output
