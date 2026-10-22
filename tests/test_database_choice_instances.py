# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Choosing an instance Noust runs for a new application's database (checklist C-02, C-03).

The host's engine stays the primary one: it is what a database is made on when
no version, flavour or instance is asked for, and what it is made on when it is
the version asked for. When it is not (PostgreSQL 16 on the host, 17 wanted;
MariaDB wanted beside MySQL), an instance that runs that version is the answer,
by itself when exactly one does and by name when the operator says which. What
is never done is installing, creating or guessing: a version nothing runs is
refused with the command that makes an instance of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.db_instance_store import READY, DbInstanceStore, InstanceRecord
from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.store import App, NoustStore
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.choice import choices, resolve_choice
from noust.managers.database.instance_catalog import SPECS, resolve_pin
from noust.managers.database.instance_records import assign_managed
from noust.managers.database.service import DatabaseService
from tests.test_database_choice import (  # noqa: F401 - the autouse fixture
    DOMAIN,
    engine_classes,
    noble,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(
        App(domain=DOMAIN, app_type="nodejs", source="https://github.com/a/b", port=3000)
    )
    yield instance
    instance.close()
    NoustStore.reset_instance()


def make_instance(
    store: NoustStore,
    name: str,
    flavour: str,
    version: str,
    *,
    state: str = READY,
    port: int = 5440,
) -> InstanceRecord:
    spec = SPECS[flavour]
    pin = resolve_pin(spec, version)
    row = DbInstanceStore(store).create(
        name=name,
        flavour=flavour,
        version=pin.version,
        image=spec.repository,
        digest=pin.digest,
        host_port=port,
        memory_limit_mb=1024,
        created_by="t",
    )
    DbInstanceStore(store).set_state(name, state)
    return DbInstanceStore(store).get(row.name) or row


def service_with(
    tmp_path: Path, store: NoustStore, *, postgres: bool = True, mysql: str | None = None
) -> DatabaseService:
    """A service over fake host engines, which also finds the instances the store has."""
    classes = engine_classes(tmp_path, postgres=postgres, mysql=mysql)
    aliases = {"mariadb": "mysql", "pg": "postgresql", "valkey": "redis"}

    def resolve(name: str) -> BaseDatabaseManager | None:
        cls = classes.get(aliases.get(name.lower(), name.lower()))
        return cls() if cls else None

    return DatabaseService(
        store=store,
        resolve=resolve,
        engines=lambda: list(classes),
        instances=lambda: assign_managed([], DbInstanceStore(store).all()),
    )


class TestTheHostIsStillThePrimary:
    def test_a_version_the_host_runs_is_made_on_the_host_even_when_an_instance_has_it(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg16", "postgresql", "16")

        choice = resolve_choice(service_with(tmp_path, store), "postgresql", version="16")

        assert choice.engine == "postgresql"
        assert choice.instance is None

    def test_nothing_asked_is_the_host(self, tmp_path: Path, store: NoustStore) -> None:
        make_instance(store, "pg17", "postgresql", "17")

        choice = resolve_choice(service_with(tmp_path, store), "postgresql")

        assert choice.engine == "postgresql" and choice.instance is None


class TestAVersionTheHostDoesNotRun:
    def test_an_instance_that_runs_it_is_the_answer(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")

        choice = resolve_choice(service_with(tmp_path, store), "postgresql", version="17")

        assert choice.action == "use"
        assert choice.engine == "postgresql@pg17"
        assert choice.instance == "pg17"
        assert choice.display_name == "PostgreSQL 17 (instance pg17)"
        assert choice.to_dict()["instance"] == "pg17"

    def test_it_is_refused_with_the_command_that_makes_one_when_none_does(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            resolve_choice(service_with(tmp_path, store), "postgresql", version="17")

        assert "PostgreSQL 17 was asked for" in caught.value.message
        assert "noust db instance create" in caught.value.details
        assert "--version 17" in caught.value.details

    def test_an_instance_that_is_not_finished_is_not_an_answer(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17", state="creating")

        with pytest.raises(ValidationError):
            resolve_choice(service_with(tmp_path, store), "postgresql", version="17")

    def test_two_that_match_are_never_guessed_between(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17a", "postgresql", "17", port=5440)
        make_instance(store, "pg17b", "postgresql", "17", port=5441)

        with pytest.raises(ValidationError) as caught:
            resolve_choice(service_with(tmp_path, store), "postgresql", version="17")

        assert "pg17a" in caught.value.details and "pg17b" in caught.value.details
        assert "--instance" in caught.value.details

    def test_the_other_flavour_can_run_beside_the_one_installed_as_an_instance(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "maria", "mariadb", "11.8")

        choice = resolve_choice(service_with(tmp_path, store, mysql="mysql"), "mariadb")

        assert choice.engine == "mysql@maria" and choice.instance == "maria"

    def test_an_engine_not_installed_on_the_host_uses_the_instance_that_runs_the_version(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")

        choice = resolve_choice(
            service_with(tmp_path, store, postgres=False), "postgresql", version="17"
        )

        assert choice.engine == "postgresql@pg17"

    def test_an_engine_not_installed_and_no_version_asked_is_not_answered_by_a_guess(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")

        with pytest.raises(DatabaseEngineError) as caught:
            resolve_choice(service_with(tmp_path, store, postgres=False), "postgresql")

        assert "is not installed" in caught.value.message
        assert "--instance pg17" in caught.value.details


class TestNamingTheInstance:
    def test_the_name_picks_it(self, tmp_path: Path, store: NoustStore) -> None:
        make_instance(store, "pg17", "postgresql", "17")

        choice = resolve_choice(service_with(tmp_path, store), "postgresql", instance="pg17")

        assert (choice.engine, choice.instance) == ("postgresql@pg17", "pg17")

    def test_the_version_asked_alongside_must_be_the_instances(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")

        resolve_choice(service_with(tmp_path, store), "postgresql", instance="pg17", version="17")
        with pytest.raises(ValidationError) as caught:
            resolve_choice(
                service_with(tmp_path, store), "postgresql", instance="pg17", version="16"
            )
        assert caught.value.field == "version"
        assert "runs PostgreSQL 17" in caught.value.message

    def test_an_instance_of_another_engine_is_refused(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "cache", "redis", "8.0")

        with pytest.raises(ValidationError) as caught:
            resolve_choice(service_with(tmp_path, store), "postgresql", instance="cache")

        assert caught.value.field == "instance"
        assert "runs Redis" in caught.value.message

    def test_an_unknown_instance_names_the_ones_there_are(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")

        with pytest.raises(ValidationError) as caught:
            resolve_choice(service_with(tmp_path, store), "postgresql", instance="ghost")

        assert caught.value.field == "instance"
        assert "pg17" in caught.value.details

    def test_an_instance_that_is_not_ready_cannot_be_named(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17", state="removing")

        with pytest.raises(ValidationError):
            resolve_choice(service_with(tmp_path, store), "postgresql", instance="pg17")

    def test_a_key_typed_as_the_engine_is_checked_against_the_pinned_version(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        from noust.managers.database.instance_records import stub_instance

        row = make_instance(store, "pg17", "postgresql", "17")
        service = service_with(tmp_path, store)
        assert stub_instance(row).managed == "pg17"

        assert resolve_choice(service, "postgresql@pg17", version="17").instance == "pg17"
        with pytest.raises(ValidationError) as caught:
            resolve_choice(service, "postgresql@pg17", version="16")
        assert "runs PostgreSQL 17" in caught.value.message


class TestTheChoices:
    def test_an_instance_offers_its_version_and_says_which(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")
        make_instance(store, "pg18", "postgresql", "18", port=5441)

        listing = {entry["flavour"]: entry for entry in choices(service_with(tmp_path, store))}

        postgresql = listing["postgresql"]
        states = {item["version"]: item for item in postgresql["versions"]}
        assert states["16"]["state"] == "installed"
        assert states["17"]["state"] == "instance" and states["17"]["instance"] == "pg17"
        assert states["18"]["instance"] == "pg18"
        assert postgresql["instances"] == [
            {"name": "pg17", "key": "postgresql@pg17", "version": "17"},
            {"name": "pg18", "key": "postgresql@pg18", "version": "18"},
        ]

    def test_every_flavour_says_which_versions_could_be_run_as_an_instance(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        listing = {entry["flavour"]: entry for entry in choices(service_with(tmp_path, store))}

        assert listing["postgresql"]["instance_versions"] == ["15", "16", "17", "18"]
        assert listing["mariadb"]["instance_versions"] == ["10.11", "11.4", "11.8"]
        assert listing["valkey"]["instance_versions"] == ["7.2", "8", "9"]
        assert "instances" in listing["valkey"] and listing["valkey"]["instances"] == []

    def test_an_unfinished_instance_is_not_listed(self, tmp_path: Path, store: NoustStore) -> None:
        make_instance(store, "pg17", "postgresql", "17", state="creating")

        listing = {entry["flavour"]: entry for entry in choices(service_with(tmp_path, store))}

        assert listing["postgresql"]["instances"] == []


class TestProvisioning:
    def provisioning(self, tmp_path: Path, store: NoustStore) -> tuple[DatabaseService, list[Any]]:
        service = service_with(tmp_path, store)
        made: list[Any] = []
        original = service.manager

        def provision(domain: str, manager: BaseDatabaseManager, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            made.append(manager.ENGINE_NAME)
            return "shop_db", "shop_user", True

        service._provision = provision  # type: ignore[method-assign]
        service.link = lambda *a, **k: SimpleNamespace(  # type: ignore[method-assign]
            created_database=False, to_dict=lambda: {}
        )
        service.running = lambda engine: original(engine)  # type: ignore[method-assign]
        return service, made

    def test_the_database_is_made_on_the_instance_that_runs_the_version(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")
        service, made = self.provisioning(tmp_path, store)

        service.provision_for_app(DOMAIN, "postgresql", version="17")

        assert made == ["postgresql@pg17"]

    def test_the_database_is_made_on_the_named_instance(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")
        service, made = self.provisioning(tmp_path, store)

        service.provision_for_app(DOMAIN, "postgresql", instance="pg17")

        assert made == ["postgresql@pg17"]

    def test_the_wizards_plan_names_the_instance_and_its_port(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17", port=5441)
        service, _made = self.provisioning(tmp_path, store)

        plan = service.plan_for_app("new.example.com", "postgresql", "17")

        assert plan["engine"] == "postgresql@pg17"
        assert plan["instance"] == "pg17"
        assert ":5441/" in plan["url"]
        assert plan["will_install"] is False

    def test_a_new_applications_database_is_made_on_the_instance(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        make_instance(store, "pg17", "postgresql", "17")
        service, made = self.provisioning(tmp_path, store)
        service._link_account = lambda domain, manager, database, user: (user, "pw", True)  # type: ignore[method-assign]

        prepared = service.prepare_for_new_app("fresh.example.com", "postgresql", version="17")

        assert prepared.engine == "postgresql@pg17"
        assert made == ["postgresql@pg17"]
