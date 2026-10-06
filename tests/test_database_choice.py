# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Choosing the engine and the version a new application's database is made on (checklist C-02).

"PostgreSQL 17 for this application" is resolved against what the server has:
the engine that runs and its version, what the catalog says this release can be
given, and what the caller allows. What matters is that nothing changes when no
version is asked for, that a version that is not the one that runs is refused
with what runs, that a missing engine is installed only when asked, and that the
refusal happens before anything is created.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.store import App, NoustStore
from noust.managers.database import flavours
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.choice import (
    choices,
    parse_version,
    resolve_choice,
    version_matches,
)
from noust.managers.database.service import DatabaseService
from noust.managers.server.host import HostPaths
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.test_web_databases_api import elevate, make_engine, wire

DOMAIN = "shop.example.com"


@pytest.fixture(autouse=True)
def noble(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server on Ubuntu 24.04, whose catalog the versions are checked against."""
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text(
        'ID=ubuntu\nVERSION_CODENAME=noble\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(root))


def engine_classes(
    tmp_path: Path, *, postgres: bool = True, mysql: str | None = None
) -> dict[str, type[BaseDatabaseManager]]:
    """
    Build the fake engines of a server.

    Args:
        tmp_path: Where dumps would go.
        postgres: PostgreSQL 16.3 is installed.
        mysql: ``mysql`` or ``mariadb`` when that flavour is installed.

    Returns:
        The classes by engine name.
    """
    pg = make_engine(tmp_path / "pg", installed=postgres)
    my = make_engine(
        tmp_path / "my",
        engine="mysql",
        display="MySQL/MariaDB",
        installed=mysql is not None,
        running=mysql is not None,
        seeded=False,
    )
    my.get_version = lambda self: "10.11.6" if mysql == "mariadb" else "8.0.36"  # type: ignore[method-assign]
    my.installed_flavour = lambda self: mysql  # type: ignore[method-assign]
    return {"postgresql": pg, "mysql": my}


def service_for(classes: dict[str, type[BaseDatabaseManager]]) -> DatabaseService:
    """
    A database service over fake engines, with MariaDB as an alias of MySQL.

    Args:
        classes: The fake engines.

    Returns:
        The service.
    """
    aliases = {"mariadb": "mysql", "pg": "postgresql"}

    def resolve(name: str) -> BaseDatabaseManager | None:
        cls = classes.get(aliases.get(name.lower(), name.lower()))
        return cls() if cls else None

    return DatabaseService(resolve=resolve, engines=lambda: list(classes))


class TestVersions:
    """A version is numbers and dots, and matches by components."""

    @pytest.mark.parametrize("text", ["17", "11.4", "8.0.36", " 16 "])
    def test_a_version_is_accepted(self, text: str) -> None:
        assert parse_version(text) == text.strip()

    @pytest.mark.parametrize("text", ["abc", "17.x", "17,2", "17;", "1.2.3.4", "v17", "-1"])
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_version(text)
        assert caught.value.field == "version"

    @pytest.mark.parametrize("text", [None, "", "  "])
    def test_none_asked_is_none(self, text: str | None) -> None:
        assert parse_version(text) is None

    @pytest.mark.parametrize(
        ("asked", "running", "matches"),
        [
            ("17", "17.2", True),
            ("17.2", "17.2", True),
            ("17.2", "17.3", False),
            ("1", "16.4", False),
            ("16", "16.4", True),
            ("11", "10.11.6", False),
            ("10.11", "10.11.6", True),
            ("11.4", "11.4.5", True),
            ("17", None, False),
            ("8.0", "8.0.36", True),
            ("8", "8.4.1", True),
        ],
    )
    def test_a_version_is_a_prefix_by_components(
        self, asked: str, running: str | None, matches: bool
    ) -> None:
        assert version_matches(asked, running) is matches


class TestAnEngineThatIsInstalled:
    """What runs is what a database is made on."""

    def test_nothing_asked_changes_nothing(self, tmp_path: Path) -> None:
        choice = resolve_choice(service_for(engine_classes(tmp_path)), "postgresql")
        assert choice.action == "use"
        assert choice.running_version == "16.3"
        assert choice.version is None

    @pytest.mark.parametrize("version", ["16", "16.3"])
    def test_the_version_that_runs_is_used(self, tmp_path: Path, version: str) -> None:
        choice = resolve_choice(
            service_for(engine_classes(tmp_path)), "postgresql", version=version
        )
        assert choice.action == "use" and choice.version == version

    def test_another_version_is_refused_with_what_runs(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError) as caught:
            resolve_choice(service_for(engine_classes(tmp_path)), "postgresql", version="17")
        assert caught.value.field == "version"
        assert "PostgreSQL 17 was asked for" in caught.value.message
        assert "runs PostgreSQL 16.3" in caught.value.message
        assert "one PostgreSQL per server" in caught.value.details

    def test_an_alias_resolves_to_the_engine(self, tmp_path: Path) -> None:
        choice = resolve_choice(service_for(engine_classes(tmp_path)), "pg", version="16")
        assert choice.engine == "postgresql"

    def test_an_installing_is_not_asked_for_what_is_there(self, tmp_path: Path) -> None:
        choice = resolve_choice(
            service_for(engine_classes(tmp_path)), "postgresql", version="16", install=True
        )
        assert choice.action == "use" and choice.plan is None


class TestAFlavourThatIsNotTheOneInstalled:
    """MariaDB is not MySQL."""

    def test_mariadb_is_refused_while_mysql_runs(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, mysql="mysql"))
        with pytest.raises(ValidationError) as caught:
            resolve_choice(service, "mariadb")
        assert caught.value.field == "engine"
        assert "MariaDB was asked for" in caught.value.message
        assert "MySQL 8.0.36" in caught.value.message

    def test_mariadb_is_used_while_mariadb_runs(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, mysql="mariadb"))
        choice = resolve_choice(service, "mariadb", version="10.11")
        assert (choice.action, choice.flavour) == ("use", "mariadb")

    def test_mysql_typed_with_no_version_uses_whatever_is_installed(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, mysql="mariadb"))
        assert resolve_choice(service, "mysql").action == "use"

    def test_mysql_with_a_version_asks_for_mysql(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, mysql="mariadb"))
        with pytest.raises(ValidationError, match="MySQL was asked for"):
            resolve_choice(service, "mysql", version="8.0")


class TestAnEngineThatIsMissing:
    """It is installed as part of creating the database only when asked."""

    def test_it_is_refused_with_the_command_that_installs_it(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, postgres=False))
        with pytest.raises(DatabaseEngineError) as caught:
            resolve_choice(service, "postgresql", version="17", install_hint="add --install")
        assert caught.value.message == "PostgreSQL 17 is not installed"
        assert "noust db install postgresql --version 17" in caught.value.details
        assert "add --install" in caught.value.details

    def test_it_is_installed_when_asked_and_the_plan_is_the_catalog_s(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, postgres=False))
        choice = resolve_choice(service, "postgresql", version="17", install=True)
        assert choice.action == "install"
        assert choice.flavour == "postgresql"
        assert choice.display_name == "PostgreSQL 17"
        assert choice.plan is not None
        assert choice.plan.package_sets == (("postgresql-17",),)
        assert choice.plan.source == "upstream"

    def test_the_distribution_s_own_version_comes_from_its_repository(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, postgres=False))
        choice = resolve_choice(service, "postgresql", version="16", install=True)
        assert choice.plan is not None and choice.plan.source == "distribution"

    def test_a_version_the_release_cannot_have_lists_the_ones_it_can(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, postgres=False))
        with pytest.raises(ValidationError) as caught:
            resolve_choice(service, "postgresql", version="12", install=True)
        assert caught.value.field == "version"
        assert "14, 15, 16, 17, 18" in caught.value.details

    def test_a_missing_engine_with_no_version_is_the_old_error(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path, postgres=False))
        with pytest.raises(DatabaseEngineError, match="is not installed"):
            resolve_choice(service, "postgresql")

    def test_an_unknown_engine_is_unknown(self, tmp_path: Path) -> None:
        with pytest.raises(DatabaseEngineError, match="Unknown database engine"):
            resolve_choice(service_for(engine_classes(tmp_path)), "oracle")

    def test_a_container_s_version_is_its_image_s(self, tmp_path: Path) -> None:
        classes = engine_classes(tmp_path)
        classes["postgresql"].instance = SimpleNamespace(container="db")  # type: ignore[assignment]
        with pytest.raises(ValidationError, match="its image's"):
            resolve_choice(service_for(classes), "postgresql", version="16")
        assert resolve_choice(service_for(classes), "postgresql").action == "use"


class TestTheChoices:
    """What a person can pick."""

    def test_each_flavour_says_what_runs_and_what_can_be_had(self, tmp_path: Path) -> None:
        listing = {
            entry["flavour"]: entry for entry in choices(service_for(engine_classes(tmp_path)))
        }

        postgresql = listing["postgresql"]
        assert postgresql["installed"] is True and postgresql["running_version"] == "16.3"
        states = {item["version"]: item["state"] for item in postgresql["versions"]}
        assert states["16"] == "installed"
        assert states["17"] == "unavailable"
        assert [entry["flavour"] for entry in listing.values()][:3] == [
            "postgresql",
            "mysql",
            "mariadb",
        ]

    def test_an_engine_that_is_missing_offers_its_versions_as_installable(
        self, tmp_path: Path
    ) -> None:
        listing = {
            entry["flavour"]: entry
            for entry in choices(service_for(engine_classes(tmp_path, postgres=False)))
        }
        states = {item["version"]: item["state"] for item in listing["postgresql"]["versions"]}
        assert states == dict.fromkeys(("14", "15", "16", "17", "18"), "installable")
        assert listing["postgresql"]["installable"] is True
        assert [item["default"] for item in listing["postgresql"]["versions"]].count(True) == 1

    def test_the_other_flavour_of_an_installed_engine_is_unavailable_with_why(
        self, tmp_path: Path
    ) -> None:
        listing = {
            entry["flavour"]: entry
            for entry in choices(service_for(engine_classes(tmp_path, mysql="mysql")))
        }
        assert listing["mariadb"]["installable"] is False
        assert "MySQL is installed" in (listing["mariadb"]["reason"] or "")
        assert all(item["state"] == "unavailable" for item in listing["mariadb"]["versions"])


class TestProvisioning:
    """The refusal comes before anything is created."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> NoustStore:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "state" / "noust.db")
        instance.create_app(
            App(domain=DOMAIN, app_type="nodejs", source="https://github.com/a/b", port=3000)
        )
        yield instance  # type: ignore[misc]
        instance.close()
        NoustStore.reset_instance()

    def _service(self, tmp_path: Path, *, postgres: bool = True) -> tuple[DatabaseService, list]:
        service = service_for(engine_classes(tmp_path, postgres=postgres))
        made: list[Any] = []

        def provision(*args: Any, **kwargs: Any) -> tuple[str, str | None, bool]:
            made.append(("provision", args, kwargs))
            return "shop_db", "shop_user", True

        service._provision = provision  # type: ignore[method-assign]
        service.link = lambda *a, **k: SimpleNamespace(  # type: ignore[method-assign]
            created_database=False, to_dict=lambda: {}
        )
        return service, made

    def test_the_default_is_unchanged(self, tmp_path: Path, store: NoustStore) -> None:
        service, made = self._service(tmp_path)
        service.provision_for_app(DOMAIN, "postgresql")
        assert len(made) == 1

    def test_a_version_that_does_not_run_creates_nothing(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        service, made = self._service(tmp_path)
        with pytest.raises(ValidationError, match="PostgreSQL 17 was asked for"):
            service.provision_for_app(DOMAIN, "postgresql", version="17")
        assert made == []

    def test_a_missing_engine_is_refused_and_nothing_is_installed(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        service, made = self._service(tmp_path, postgres=False)
        installed: list[Any] = []
        service.install_engine = lambda *a, **k: installed.append((a, k))  # type: ignore[method-assign]
        with pytest.raises(DatabaseEngineError, match="PostgreSQL 17 is not installed"):
            service.provision_for_app(DOMAIN, "postgresql", version="17")
        assert installed == [] and made == []

    def test_install_installs_the_version_and_then_creates_the_database(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        classes = engine_classes(tmp_path, postgres=False)
        service = service_for(classes)
        order: list[str] = []

        def install(engine: str, *, flavour: str | None = None, version: str | None = None) -> None:
            order.append(f"install {flavour} {version}")
            classes["postgresql"].state["installed"] = True
            classes["postgresql"].state["running"] = True

        service.install_engine = install  # type: ignore[method-assign]
        service._provision = lambda *a, **k: (order.append("provision"), ("shop_db", "u", True))[1]  # type: ignore[method-assign]
        service.link = lambda *a, **k: SimpleNamespace(created_database=False, to_dict=lambda: {})  # type: ignore[method-assign]

        service.provision_for_app(DOMAIN, "postgresql", version="17", install=True)

        assert order == ["install postgresql 17", "provision"]

    def test_the_wizard_plan_says_when_it_would_install(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        service = service_for(engine_classes(tmp_path, postgres=False))
        plan = service.plan_for_app("new.example.com", "postgresql", "17")
        assert plan["will_install"] is True
        assert plan["version"] == "17" and plan["installed"] is False

    def test_the_wizard_plan_refuses_a_version_that_does_not_run(self, tmp_path: Path) -> None:
        service = service_for(engine_classes(tmp_path))
        with pytest.raises(ValidationError, match=r"runs PostgreSQL 16\.3"):
            service.plan_for_app("new.example.com", "postgresql", "17")

    def test_the_wizard_plan_without_a_version_is_as_it_was(self, tmp_path: Path) -> None:
        plan = service_for(engine_classes(tmp_path)).plan_for_app("new.example.com", "postgresql")
        assert plan["will_install"] is False and plan["version"] is None
        assert plan["running_version"] == "16.3"

    def test_a_new_application_s_database_checks_the_version_first(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        service, made = self._service(tmp_path)
        with pytest.raises(ValidationError, match="was asked for"):
            service.prepare_for_new_app("fresh.example.com", "postgresql", version="17")
        assert made == []


class TestCommandLine:
    """``noust db provision --version --install`` and ``noust db choices``."""

    def test_provision_passes_the_version_and_the_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.app import cli as root_cli
        from noust.cli.commands import db as db_cli

        seen: dict[str, Any] = {}

        class FakeService:
            def __init__(self, **_: Any) -> None:
                pass

            def provision_for_app(self, domain: str, engine: str, **kwargs: Any) -> Any:
                seen.update(domain=domain, engine=engine, **kwargs)
                return SimpleNamespace(
                    created_database=True, database="shop_db", env_vars=["DATABASE_URL"]
                )

        monkeypatch.setattr(db_cli, "DatabaseService", FakeService)
        result = CliRunner().invoke(
            root_cli,
            ["db", "provision", DOMAIN, "-e", "postgresql", "--version", "17", "--install"],
        )
        assert result.exit_code == 0, result.output
        assert seen["version"] == "17" and seen["install"] is True
        assert seen["install_hint"] == "add --install"

    def test_provision_without_them_asks_for_neither(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.cli.app import cli as root_cli
        from noust.cli.commands import db as db_cli

        seen: dict[str, Any] = {}

        class FakeService:
            def __init__(self, **_: Any) -> None:
                pass

            def provision_for_app(self, domain: str, engine: str, **kwargs: Any) -> Any:
                seen.update(kwargs)
                return SimpleNamespace(
                    created_database=False, database="shop_db", env_vars=["DATABASE_URL"]
                )

        monkeypatch.setattr(db_cli, "DatabaseService", FakeService)
        CliRunner().invoke(root_cli, ["db", "provision", DOMAIN, "-e", "postgresql"])
        assert seen["version"] is None and seen["install"] is False

    def test_a_refusal_is_printed_with_what_runs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        NoustStore.reset_instance()
        store = NoustStore(tmp_path / "state" / "noust.db")
        store.create_app(
            App(domain=DOMAIN, app_type="nodejs", source="https://github.com/a/b", port=3000)
        )
        from noust.cli.app import cli as root_cli
        from noust.cli.commands import db as db_cli

        service = service_for(engine_classes(tmp_path))
        monkeypatch.setattr(db_cli, "DatabaseService", lambda **_: service)
        result = CliRunner().invoke(
            root_cli, ["db", "provision", DOMAIN, "-e", "postgresql", "--version", "17"]
        )
        assert result.exit_code != 0
        assert "runs PostgreSQL 16.3" in result.output

    def test_choices_prints_the_versions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from noust.cli.app import cli as root_cli
        from noust.cli.commands import db as db_cli

        service = service_for(engine_classes(tmp_path))
        monkeypatch.setattr(db_cli, "DatabaseService", lambda **_: service)
        plain = CliRunner().invoke(root_cli, ["db", "choices"])
        assert plain.exit_code == 0, plain.output
        assert "postgresql" in plain.output
        as_json = CliRunner().invoke(root_cli, ["db", "choices", "--json"])
        assert as_json.exit_code == 0, as_json.output
        assert '"flavour": "postgresql"' in as_json.output

    def test_create_wants_a_database_for_the_version(self) -> None:
        from noust.cli.app import cli as root_cli

        result = CliRunner().invoke(
            root_cli,
            ["create", "-d", DOMAIN, "-s", "https://github.com/a/b", "--database-version", "17"],
        )
        assert result.exit_code != 0
        assert "need --database" in result.output


class TestApi:
    """The choices, the plan and the provisioning request."""

    @pytest.fixture
    def classes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> dict[str, type[BaseDatabaseManager]]:
        built = engine_classes(tmp_path)
        wire(monkeypatch, list(built.values()))
        return built

    @pytest.fixture
    def client(self, tmp_path: Path, runner: object, classes: object) -> TestClient:
        app: FastAPI = create_app(
            SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000)
        )
        signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
        token = get_token_manager().generate_master_token()
        login = signed_in.post("/api/auth/login", json={"token": token})
        signed_in.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
        return signed_in

    @pytest.fixture
    def deployed(self, tmp_path: Path) -> Callable[[], None]:
        NoustStore.reset_instance()
        store = NoustStore(tmp_path / "state" / "noust.db")
        store.create_app(
            App(domain=DOMAIN, app_type="nodejs", source="https://github.com/a/b", port=3000)
        )
        return lambda: None

    def test_the_choices(self, client: TestClient) -> None:
        body = client.get("/api/databases/provisioning/choices").json()
        by_flavour = {entry["flavour"]: entry for entry in body["choices"]}
        assert by_flavour["postgresql"]["running_version"] == "16.3"
        assert {"version": "16", "state": "installed", "default": True} in by_flavour["postgresql"][
            "versions"
        ] or any(
            item["version"] == "16" and item["state"] == "installed"
            for item in by_flavour["postgresql"]["versions"]
        )

    def test_the_choices_need_a_session(
        self, tmp_path: Path, runner: object, classes: object
    ) -> None:
        app = create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))
        anonymous = TestClient(app, client=("testclient", 50000))
        assert anonymous.get("/api/databases/provisioning/choices").status_code in (401, 403)

    def test_the_plan_takes_a_version(self, client: TestClient) -> None:
        ok = client.get(
            "/api/databases/provisioning/plan",
            params={"domain": "new.example.com", "engine": "postgresql", "version": "16"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["version"] == "16" and ok.json()["will_install"] is False

        refused = client.get(
            "/api/databases/provisioning/plan",
            params={"domain": "new.example.com", "engine": "postgresql", "version": "17"},
        )
        assert refused.status_code == 400
        assert "runs PostgreSQL" in refused.text

    def test_a_version_that_does_not_run_is_refused_before_a_job_is_queued(
        self, client: TestClient, deployed: Callable[[], None]
    ) -> None:
        response = client.post(
            f"/api/apps/{DOMAIN}/databases", json={"engine": "postgresql", "version": "17"}
        )
        assert response.status_code == 400, response.text
        assert "PostgreSQL 17 was asked for" in response.text

    def test_the_version_that_runs_queues_the_job_with_it(
        self,
        client: TestClient,
        deployed: Callable[[], None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        queued: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                queued.append(kwargs)
                return SimpleNamespace(
                    id="job-1", status=SimpleNamespace(value="pending"), to_dict=lambda: {}
                )

        monkeypatch.setattr("noust.web.api.databases.common.get_job_manager", lambda: Manager())
        response = client.post(
            f"/api/apps/{DOMAIN}/databases", json={"engine": "postgresql", "version": "16"}
        )
        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"]["version"] == "16"
        assert queued[0]["kwargs"]["install"] is False

    def test_installing_as_part_of_it_needs_sudo_mode(
        self, client: TestClient, deployed: Callable[[], None], classes: dict[str, Any]
    ) -> None:
        classes["postgresql"].state["installed"] = False
        classes["postgresql"].state["running"] = False
        body = {"engine": "postgresql", "version": "17", "install": True}

        refused = client.post(f"/api/apps/{DOMAIN}/databases", json=body)
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"

    def test_installing_as_part_of_it_is_queued_when_confirmed(
        self,
        client: TestClient,
        deployed: Callable[[], None],
        classes: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        classes["postgresql"].state["installed"] = False
        classes["postgresql"].state["running"] = False
        queued: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                queued.append(kwargs)
                return SimpleNamespace(
                    id="job-1", status=SimpleNamespace(value="pending"), to_dict=lambda: {}
                )

        monkeypatch.setattr("noust.web.api.databases.common.get_job_manager", lambda: Manager())
        elevate(client)
        response = client.post(
            f"/api/apps/{DOMAIN}/databases",
            json={"engine": "postgresql", "version": "17", "install": True},
        )
        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"]["install"] is True and queued[0]["kwargs"]["version"] == "17"

    def test_a_missing_engine_without_install_says_how(
        self, client: TestClient, deployed: Callable[[], None], classes: dict[str, Any]
    ) -> None:
        classes["postgresql"].state["installed"] = False
        classes["postgresql"].state["running"] = False
        response = client.post(
            f"/api/apps/{DOMAIN}/databases", json={"engine": "postgresql", "version": "17"}
        )
        assert response.status_code >= 400
        assert "noust db install postgresql --version 17" in response.text
        assert "send install: true" in response.text

    def test_a_new_application_s_version_is_checked_when_it_arrives(
        self, client: TestClient
    ) -> None:
        elevate(client)
        response = client.post(
            "/api/apps",
            json={
                "domain": "fresh.example.com",
                "source": "https://github.com/a/b",
                "app_type": "nodejs",
                "database": {"engine": "postgresql", "version": "17"},
            },
        )
        assert response.status_code == 400, response.text
        assert "PostgreSQL 17 was asked for" in response.text
