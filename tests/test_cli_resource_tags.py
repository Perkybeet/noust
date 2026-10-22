# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db tag``, ``noust service tag`` and ``noust app tag restart`` (checklist M-07).

The commands are shells around :class:`~noust.core.resource_tags.ResourceTags`; what is pinned is
that a resource is found before it is tagged, that the listings filter by a tag, and that
restarting a tag reaches applications and services, leaves databases and says so.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import db as db_cli
from noust.cli.commands import webapp
from noust.core.app_tags import AppTags
from noust.core.exceptions import ServiceError
from noust.core.resource_tags import ResourceTags
from noust.core.store import App, NoustStore
from noust.managers.service_manager import ServiceManager
from noust.web.auth import STATE_DIR_ENV
from tests.test_web_databases_api import make_engine, wire


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="a.example.com", app_type="nodejs", app_path="/tmp/a"))
    yield instance
    instance.close()
    NoustStore.reset_instance()


def run(*args: str, code: int = 0) -> str:
    """
    Returns:
        What the command printed; for one that fails, its error as well.
    """
    result = CliRunner().invoke(root_cli, list(args))
    assert result.exit_code == code, f"{result.output} {result.exception}"
    return result.output if code == 0 else f"{result.output}{result.exception or ''}"


@pytest.fixture
def engine(store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> type:
    cls = make_engine(tmp_path / "pg", seeded=False)
    cls.dbs = {"shop": {"owner": None, "size": None}, "blog": {"owner": None, "size": None}}
    describe = cls.get_database_info

    def get_database_info(self: Any, name: str) -> Any:
        from noust.core.exceptions import DatabaseNotFoundError

        if name not in type(self).dbs:
            raise DatabaseNotFoundError(f"No database named {name}")
        return describe(self, name)

    cls.get_database_info = get_database_info  # type: ignore[method-assign]
    wire(monkeypatch, [cls])
    monkeypatch.setattr(
        db_cli,
        "get_db_manager",
        lambda name, verbose=False: cls(verbose=verbose) if name == "postgresql" else None,
    )
    return cls


@pytest.fixture
def units(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Two units that exist, as the service manager would find them."""
    names = ["worker", "mailer"]
    monkeypatch.setattr(
        ServiceManager,
        "inspect_unit",
        lambda self, name: SimpleNamespace(exists=name in names, unit=name),
    )
    monkeypatch.setattr(
        ServiceManager,
        "list_services",
        lambda self, all_services=False, state=None: [
            {"name": name, "active": "active", "sub": "running", "managed": True} for name in names
        ],
    )
    monkeypatch.setattr(ServiceManager, "state_is_running", lambda self, active: True)
    return names


class TestDatabases:
    def test_a_database_is_tagged_listed_and_filtered(self, engine: type) -> None:
        run("db", "tag", "add", "shop", "--engine", "postgresql", "Prod", "eu")
        run("db", "tag", "add", "blog", "--engine", "postgresql", "staging")

        listed = json.loads(run("--json", "db", "tag", "list", "--tag", "prod"))
        one = json.loads(run("--json", "db", "tag", "list", "shop", "--engine", "postgresql"))
        databases = json.loads(run("--json", "db", "list", "--tag", "prod"))

        assert listed["databases"] == {"postgresql/shop": ["eu", "prod"]}
        assert one["databases"] == {"postgresql/shop": ["eu", "prod"]}
        assert [row["name"] for row in databases] == ["shop"]
        assert databases[0]["tags"] == ["eu", "prod"]

    def test_the_json_listing_names_the_owning_application_as_app_only(self, engine: type) -> None:
        databases = json.loads(run("--json", "db", "list"))

        assert databases
        # 2.x called it linked_app; 4.0 breaks with 2.x and the field is app.
        assert all("app" in row and "linked_app" not in row for row in databases)

    def test_the_human_listing_shows_the_tags(self, engine: type) -> None:
        run("db", "tag", "set", "shop", "--engine", "postgresql", "prod")

        assert "#prod" in run("db", "list")

    def test_remove_and_set(self, engine: type, store: NoustStore) -> None:
        run("db", "tag", "set", "shop", "--engine", "postgresql", "x", "y")
        run("db", "tag", "remove", "shop", "--engine", "postgresql", "x")
        assert ResourceTags(store).of("database", "postgresql/shop") == ["y"]

        run("db", "tag", "set", "shop", "--engine", "postgresql")
        assert ResourceTags(store).of("database", "postgresql/shop") == []

    def test_a_database_that_is_not_there_is_not_tagged(
        self, engine: type, store: NoustStore
    ) -> None:
        run("db", "tag", "add", "ghost", "--engine", "postgresql", "prod", code=1)

        assert ResourceTags(store).by_resource("database") == {}

    def test_the_engine_has_to_be_said(self, engine: type) -> None:
        out = run("db", "tag", "add", "shop", "prod", code=1)

        assert "--engine" in out

    def test_a_bad_tag_is_refused(self, engine: type) -> None:
        run("db", "tag", "add", "shop", "--engine", "postgresql", "not a tag", code=1)


class TestServices:
    def test_a_service_is_tagged_listed_and_filtered(
        self, units: list[str], store: NoustStore
    ) -> None:
        run("service", "tag", "add", "worker", "Prod")

        listed = json.loads(run("--json", "service", "tag", "list"))
        services = json.loads(run("--json", "service", "list", "--tag", "prod"))
        everything = json.loads(run("--json", "service", "list"))

        assert listed["services"] == {"worker": ["prod"]}
        assert [row["name"] for row in services] == ["worker"]
        assert {row["name"]: row["tags"] for row in everything} == {
            "worker": ["prod"],
            "mailer": [],
        }

    def test_a_unit_that_does_not_exist_is_not_tagged(
        self, units: list[str], store: NoustStore
    ) -> None:
        run("service", "tag", "add", "ghost", "prod", code=1)

        assert ResourceTags(store).by_resource("service") == {}

    def test_remove_and_set(self, units: list[str], store: NoustStore) -> None:
        run("service", "tag", "set", "worker", "x", "y")
        run("service", "tag", "remove", "worker", "x")
        assert ResourceTags(store).of("service", "worker") == ["y"]

        run("service", "tag", "set", "worker")
        assert ResourceTags(store).of("service", "worker") == []


class TestRestartingATag:
    @pytest.fixture
    def restarted(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        done: list[str] = []

        def control(domain: str, action: str, logger: Any) -> int:
            assert action == "restart"
            done.append(domain)
            return 0

        def restart(self: ServiceManager, name: str) -> bool:
            if name == "mailer":
                raise ServiceError("mailer would not restart")
            done.append(name)
            return True

        monkeypatch.setattr(webapp, "_control_service", control)
        monkeypatch.setattr(ServiceManager, "restart", restart)
        return done

    def test_it_restarts_applications_and_services_and_leaves_databases(
        self, store: NoustStore, restarted: list[str]
    ) -> None:
        AppTags(store).set("a.example.com", ["prod"])
        tags = ResourceTags(store)
        tags.set("service", "worker", ["prod"])
        tags.set("database", "postgresql/shop", ["prod"])

        out = json.loads(run("--json", "app", "tag", "restart", "prod"))

        rows = {(row["kind"], row["name"]): row for row in out["results"]}
        assert rows[("application", "a.example.com")]["outcome"] == "restarted"
        assert rows[("service", "worker")]["outcome"] == "restarted"
        assert rows[("database", "postgresql/shop")]["outcome"] == "skipped"
        assert "noust db restart" in rows[("database", "postgresql/shop")]["detail"]
        assert restarted == ["a.example.com", "worker"]

    def test_one_that_fails_does_not_stop_the_others_and_the_exit_says_so(
        self, store: NoustStore, restarted: list[str]
    ) -> None:
        tags = ResourceTags(store)
        tags.set("service", "mailer", ["prod"])
        tags.set("service", "worker", ["prod"])

        result = CliRunner().invoke(root_cli, ["--json", "app", "tag", "restart", "prod"])

        assert result.exit_code == 1
        rows = {row["name"]: row for row in json.loads(result.output)["results"]}
        assert rows["mailer"]["outcome"] == "failed"
        assert "would not restart" in rows["mailer"]["detail"]
        assert rows["worker"]["outcome"] == "restarted"

    def test_a_tag_nobody_carries_says_so(self, store: NoustStore, restarted: list[str]) -> None:
        out = run("app", "tag", "restart", "ghost", code=1)

        assert "Nothing carries the tag ghost" in out
        assert restarted == []
