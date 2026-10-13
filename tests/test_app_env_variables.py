# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for an application's variables beyond its ``.env``: references to shared
variables and secret managers, and the audience of a variable (build-only,
runtime-only), checklist B-43, B-44, B-45 and B-48.

The file holds what the process runs with; the store holds what it is made of.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.core.teams import TeamStore
from noust.core.variables.model import ENVIRONMENT, PROJECT, SERVER
from noust.core.variables.providers import ProviderRegistry, SecretProviderError
from noust.core.variables.resolver import Resolver, UnresolvedReferenceError, context_for
from noust.core.variables.store import VariableStore
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.app_env import (
    read_app_env,
    read_environment_of,
    read_runtime_env,
    refresh_app_env,
    write_app_env,
)
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.env_variables import deployment_for
from noust.validators.environment import EnvironmentValidationError

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore, runner: FakeRunner
) -> App:
    """An application deployed in place, in the staging environment of a project."""
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    teams = TeamStore(store)
    teams.create_team("Acme", slug="acme")
    project = teams.create_project("acme", "Shop", slug="shop")
    staging = teams.create_environment("acme", "shop", "Staging", slug="staging")
    store.create_app(App(domain=DOMAIN, app_path=str(root), app_type="nodejs", port=3001))
    teams.move_app(DOMAIN, project.id, staging.id)
    found = store.get_app(DOMAIN)
    assert found is not None
    return found


def file_of(app: App) -> dict[str, str]:
    return EnvManager().read_env_file(Path(app.app_path) / ".env")


class TestReferences:
    def test_a_reference_is_resolved_into_the_file_and_kept_as_text_in_the_store(
        self, app: App, store: NoustStore
    ) -> None:
        env = store.get_app(DOMAIN)
        assert env is not None
        project = TeamStore(store).get_project("acme", "shop")
        assert project is not None
        VariableStore(store).set(PROJECT, project.id, "DB_URL", "postgres://shared/shop")

        write_app_env(app, {"DATABASE_URL": "{{project.DB_URL}}", "PLAIN": "x"})

        assert file_of(app) == {"DATABASE_URL": "postgres://shared/shop", "PLAIN": "x"}
        environment = read_environment_of(app)
        assert environment.expressions == {"DATABASE_URL": "{{project.DB_URL}}"}
        assert read_app_env(app)["DATABASE_URL"] == "postgres://shared/shop"

    def test_the_same_definition_gives_each_environment_its_own_value(
        self, app: App, store: NoustStore
    ) -> None:
        teams = TeamStore(store)
        staging = teams.get_environment("acme", "shop", "staging")
        production = teams.get_environment("acme", "shop", "production")
        assert staging is not None and production is not None
        variables = VariableStore(store)
        variables.set(ENVIRONMENT, staging.id, "API", "https://staging.api")
        variables.set(ENVIRONMENT, production.id, "API", "https://api")

        write_app_env(app, {"API_URL": "{{environment.API}}"})
        assert file_of(app)["API_URL"] == "https://staging.api"

        teams.move_app(DOMAIN, staging.project_id, production.id)
        moved = store.get_app(DOMAIN)
        assert moved is not None
        refresh_app_env(moved)

        assert file_of(app)["API_URL"] == "https://api"

    def test_writing_back_what_was_read_keeps_the_references(
        self, app: App, store: NoustStore
    ) -> None:
        VariableStore(store).set(SERVER, 0, "REGION", "eu")
        write_app_env(app, {"R": "{{server.REGION}}", "A": "1"})

        # The console reads the resolved values and writes them back with one change.
        write_app_env(app, {**read_app_env(app), "A": "2"})

        assert read_environment_of(app).expressions == {"R": "{{server.REGION}}"}
        assert file_of(app) == {"R": "eu", "A": "2"}

    def test_typing_a_literal_over_a_reference_removes_it(
        self, app: App, store: NoustStore
    ) -> None:
        VariableStore(store).set(SERVER, 0, "REGION", "eu")
        write_app_env(app, {"R": "{{server.REGION}}"})

        write_app_env(app, {"R": "us"})

        assert read_environment_of(app).expressions == {}
        assert file_of(app) == {"R": "us"}

    def test_a_reference_that_does_not_resolve_changes_nothing(
        self, app: App, store: NoustStore
    ) -> None:
        write_app_env(app, {"KEEP": "1"})

        with pytest.raises(UnresolvedReferenceError, match="no variable NOPE"):
            write_app_env(app, {"KEEP": "2", "X": "{{server.NOPE}}"})

        assert file_of(app) == {"KEEP": "1"}

    def test_a_deploy_picks_up_a_shared_variable_that_changed(
        self, app: App, store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "REGION", "eu")
        write_app_env(app, {"R": "{{server.REGION}}"})
        variables.set(SERVER, 0, "REGION", "us")
        assert file_of(app)["R"] == "eu"  # nothing moves until a deploy (or resolve) asks

        plan = refresh_app_env(app)

        assert plan.changed == ("R",)
        assert file_of(app)["R"] == "us"

    def test_a_refresh_writes_nothing_when_nothing_changed(
        self, app: App, store: NoustStore
    ) -> None:
        VariableStore(store).set(SERVER, 0, "REGION", "eu")
        write_app_env(app, {"R": "{{server.REGION}}"})
        before = (Path(app.app_path) / ".env").stat().st_mtime_ns

        plan = refresh_app_env(app)

        assert plan.changed == ()
        assert (Path(app.app_path) / ".env").stat().st_mtime_ns == before

    def test_a_refresh_that_cannot_resolve_stops_before_anything_is_written(
        self, app: App, store: NoustStore
    ) -> None:
        variables = VariableStore(store)
        variables.set(SERVER, 0, "REGION", "eu")
        write_app_env(app, {"R": "{{server.REGION}}", "OTHER": "1"})
        variables.delete(SERVER, 0, "REGION")

        with pytest.raises(UnresolvedReferenceError):
            refresh_app_env(app)

        assert file_of(app) == {"R": "eu", "OTHER": "1"}

    def test_the_deployments_own_facts_are_references_too(self, app: App) -> None:
        write_app_env(app, {"SITE_URL": "{{app.URL}}/x", "BRANCH": "{{app.PORT}}"})

        assert file_of(app) == {"SITE_URL": "https://shop.example.com/x", "BRANCH": "3001"}

    def test_a_reference_to_the_commit_is_resolved_for_the_commit_being_built(
        self, app: App
    ) -> None:
        # Not resolvable while editing: nothing is being built.
        with pytest.raises(UnresolvedReferenceError, match="COMMIT"):
            write_app_env(app, {"V": "{{app.COMMIT}}"})

    def test_a_secret_manager_value_is_fetched_and_not_stored_in_the_database(
        self, app: App, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Fake(ProviderRegistry):
            calls = 0

            def fetch(self, name: str, path: str, *, team_id: int | None = None) -> str:
                type(self).calls += 1
                if path == "BROKEN":
                    raise SecretProviderError("the manager is down")
                return "sk_live_value"

        monkeypatch.setattr(
            app_env_module,
            "resolver_for",
            lambda a, d=None: Resolver(
                context_for(a.domain, deployment_for(a)), providers=Fake(store)
            ),
        )

        write_app_env(app, {"STRIPE": "{{secret:doppler/STRIPE_KEY}}"})

        assert file_of(app)["STRIPE"] == "sk_live_value"
        with store._transaction() as cursor:
            cursor.execute("SELECT * FROM app_env_variables")
            rows = [tuple(row) for row in cursor.fetchall()]
        assert rows and all("sk_live_value" not in str(row) for row in rows)
        assert read_environment_of(app).expressions == {"STRIPE": "{{secret:doppler/STRIPE_KEY}}"}

        with pytest.raises(SecretProviderError):
            write_app_env(app, {"STRIPE": "{{secret:doppler/BROKEN}}"})
        assert file_of(app)["STRIPE"] == "sk_live_value"  # failed closed: nothing changed

    def test_the_names_noust_sets_itself_are_refused(self, app: App) -> None:
        with pytest.raises(EnvironmentValidationError, match="NOUST_URL"):
            write_app_env(app, {"NOUST_URL": "https://elsewhere"})


class TestAudience:
    def test_a_build_only_variable_is_in_no_file(self, app: App) -> None:
        write_app_env(
            app, {"NPM_TOKEN": "npm_secret", "PLAIN": "1"}, audience={"NPM_TOKEN": "build"}
        )

        assert file_of(app) == {"PLAIN": "1"}
        assert "npm_secret" not in (Path(app.app_path) / ".env").read_text()
        assert read_runtime_env(app) == {"PLAIN": "1"}
        environment = read_environment_of(app)
        assert environment.build_only == {"NPM_TOKEN": "npm_secret"}
        assert read_app_env(app) == {"NPM_TOKEN": "npm_secret", "PLAIN": "1"}

    def test_a_round_trip_through_read_and_write_keeps_the_build_only_variable(
        self, app: App
    ) -> None:
        write_app_env(app, {"NPM_TOKEN": "t"}, audience={"NPM_TOKEN": "build"})

        # What the database linker, a recipe update and the console's editor all do.
        write_app_env(app, {**read_app_env(app), "NEW": "1"})

        assert read_environment_of(app).build_only == {"NPM_TOKEN": "t"}
        assert "NPM_TOKEN" not in file_of(app)

    def test_removing_it_from_the_set_removes_it_everywhere(
        self, app: App, store: NoustStore
    ) -> None:
        write_app_env(app, {"NPM_TOKEN": "t"}, audience={"NPM_TOKEN": "build"})

        write_app_env(app, {"OTHER": "1"})

        assert read_app_env(app) == {"OTHER": "1"}
        assert app.id is not None
        assert VariableStore(store).app_variables(app.id) == {}

    def test_a_runtime_only_variable_is_in_the_file_and_marked(self, app: App) -> None:
        write_app_env(app, {"SESSION_SECRET": "s"}, audience={"SESSION_SECRET": "runtime"})

        assert file_of(app) == {"SESSION_SECRET": "s"}
        assert read_environment_of(app).runtime_only_names == ["SESSION_SECRET"]

    def test_changing_a_build_only_variable_to_both_puts_it_in_the_file(self, app: App) -> None:
        write_app_env(app, {"T": "v"}, audience={"T": "build"})

        write_app_env(app, {"T": "v"}, audience={"T": "both"})

        assert file_of(app) == {"T": "v"}
        assert read_environment_of(app).build_only == {}

    def test_an_audience_for_a_variable_that_is_not_there_is_refused(self, app: App) -> None:
        with pytest.raises(Exception, match="not in the environment"):
            write_app_env(app, {"A": "1"}, audience={"B": "build"})

    def test_a_build_only_variable_can_be_made_of_a_reference(
        self, app: App, store: NoustStore
    ) -> None:
        VariableStore(store).set(SERVER, 0, "NPM", "npm_xyz")

        write_app_env(app, {"NPM_TOKEN": "{{server.NPM}}"}, audience={"NPM_TOKEN": "build"})
        VariableStore(store).set(SERVER, 0, "NPM", "npm_new")
        refresh_app_env(app)

        assert read_environment_of(app).build_only == {"NPM_TOKEN": "npm_new"}
        assert file_of(app) == {}

    def test_the_audience_survives_a_full_row_write_of_the_application(
        self, app: App, store: NoustStore
    ) -> None:
        write_app_env(app, {"T": "v"}, audience={"T": "build"})

        found = store.get_app(DOMAIN)
        assert found is not None
        found.status = "running"
        store.update_app(found)

        assert read_environment_of(found).build_only == {"T": "v"}


def test_an_application_found_on_disk_without_a_row_has_only_its_file(
    tmp_path: Path, store: NoustStore
) -> None:
    root = tmp_path / "apps" / "x-example-com"
    root.mkdir(parents=True)
    (root / ".env").write_text("A=1\n")
    stand_in = App(domain="x.example.com", app_path=str(root))

    environment: Any = read_environment_of(stand_in)

    assert environment.values == {"A": "1"} and environment.audience == {}
