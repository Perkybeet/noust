# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What a recipe needs from the server, checked before anything is created (checklist D-12)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli
from noust.core.exceptions import DatabaseEngineError
from noust.core.logger import Logger
from noust.recipes import Recipe, RecipeError, parse_recipe
from noust.recipes.requirements import (
    RUNTIME,
    SERVICE,
    TOOL,
    check_requirements,
    refuse_unmet,
    unmet,
)
from tests.recipes.factory import compose_recipe, native_recipe, use_demo_assets

pytestmark = pytest.mark.real_requirements


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recipes here borrow a shipped recipe's logo."""
    use_demo_assets(monkeypatch)


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    A server that has what the test says it has.

    ``tools_missing`` are the names the readiness check reports; ``runtimes``
    the language versions installed; ``engines`` maps an engine to
    ``installed``, ``running`` or absent. ``installed`` records what the
    install put there.
    """
    state = SimpleNamespace(tools_missing=[], runtimes=set(), engines={}, installed=[])

    monkeypatch.setattr(
        "noust.recipes.requirements.check_deployment_ready",
        lambda app_type, *a, **k: (not state.tools_missing, list(state.tools_missing), []),
    )

    class Runtimes:
        def find(self, request: Any) -> Any:
            if request.text in state.runtimes or request.language in state.runtimes:
                return SimpleNamespace(version="22.11.0", managed=True)
            return None

        def ensure(self, request: Any) -> Any:
            state.installed.append(request.text)
            state.runtimes.add(request.text)
            return SimpleNamespace(version="22.11.0", managed=True)

    class Manager:
        def __init__(self, engine: str) -> None:
            self.engine = engine
            self.DISPLAY_NAME = engine.title()

        def is_installed(self) -> bool:
            return state.engines.get(self.engine) in ("installed", "running")

        def is_running(self) -> bool:
            return state.engines.get(self.engine) == "running"

    class Databases:
        def manager(self, engine: str) -> Manager:
            if engine == "broken":
                raise DatabaseEngineError("unknown engine")
            return Manager(engine)

        def install_engine(self, engine: str) -> None:
            state.installed.append(engine)
            state.engines[engine] = "running"

    monkeypatch.setattr("noust.managers.runtimes.RuntimeManager", Runtimes)
    monkeypatch.setattr("noust.managers.database.service.DatabaseService", Databases)
    return state


def native(**overrides: Any) -> Recipe:
    """A native recipe that pins Node 22 and needs PostgreSQL."""
    document = native_recipe(build={"runtime": "node@22"}, requires=["postgres"], **overrides)
    return parse_recipe("demo", document)


def stack(**overrides: Any) -> Recipe:
    """A Compose recipe."""
    return parse_recipe("demo", compose_recipe(**overrides))


class TestLookingAtWhatIsThere:
    def test_a_server_that_has_everything_is_ready(self, machine: SimpleNamespace) -> None:
        machine.runtimes = {"node@22"}
        machine.engines = {"postgresql": "running"}

        found = check_requirements(native())

        assert [(item.kind, item.name, item.met) for item in found] == [
            (TOOL, "build tools", True),
            (RUNTIME, "node@22", True),
            (SERVICE, "postgresql", True),
        ]
        assert unmet(found) == []

    def test_what_is_missing_says_what_to_run(self, machine: SimpleNamespace) -> None:
        found = unmet(check_requirements(native()))

        by_name = {item.name: item for item in found}
        assert by_name["node@22"].fix == "noust runtime install node@22"
        assert by_name["node@22"].installable is True
        assert by_name["postgresql"].fix == "noust db install postgresql"
        assert by_name["postgresql"].installable is True

    def test_an_engine_that_is_installed_but_stopped_is_started_not_installed(
        self, machine: SimpleNamespace
    ) -> None:
        machine.runtimes = {"node@22"}
        machine.engines = {"postgresql": "installed"}

        [item] = unmet(check_requirements(native()))

        assert item.fix == "noust db start postgresql" and item.installable is False
        assert "not running" in item.detail

    def test_docker_missing_is_a_tool_that_noust_never_installs(
        self, machine: SimpleNamespace
    ) -> None:
        machine.tools_missing = ["Docker"]

        [item] = check_requirements(stack())

        assert (item.kind, item.met, item.installable) == (TOOL, False, False)
        assert "docs.docker.com" in (item.fix or "")

    def test_a_stack_asks_for_docker_and_nothing_else(self, machine: SimpleNamespace) -> None:
        [item] = check_requirements(stack())

        assert item.name == "Docker with the Compose plugin" and item.met

    def test_an_unavailable_recipe_asks_nothing(self, machine: SimpleNamespace) -> None:
        listed = parse_recipe(
            "demo",
            {
                "schema": 2,
                "name": "demo",
                "kind": "compose",
                "title": "Demo",
                "slogan": "Not here.",
                "category": "utilities",
                "tags": ["demo"],
                "logo": "logo.svg",
                "version": "1.0.0",
                "updated": "2026-10-06",
                "description": "Not here.",
                "homepage": "https://example.com",
                "available": False,
                "unavailable_reason": "No image.",
            },
        )

        assert check_requirements(listed) == []

    def test_an_engine_the_service_cannot_name_is_reported_not_raised(
        self, machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.recipes.requirements.ENGINE_OF", {"postgres": "broken"})

        found = [item for item in check_requirements(native()) if item.kind == SERVICE]

        assert [(item.name, item.met) for item in found] == [("broken", False)]


class TestTheGate:
    def test_a_server_with_everything_passes(self, machine: SimpleNamespace) -> None:
        machine.runtimes = {"node@22"}
        machine.engines = {"postgresql": "running"}

        refuse_unmet(native(), install=False, logger=Logger())

        assert machine.installed == []

    def test_a_server_without_them_is_refused_with_every_missing_piece(
        self, machine: SimpleNamespace
    ) -> None:
        with pytest.raises(RecipeError) as refused:
            refuse_unmet(native(), install=False, logger=Logger())

        assert "node@22" in refused.value.message and "postgresql" in refused.value.message
        assert "noust runtime install node@22" in (refused.value.details or "")
        assert "noust db install postgresql" in (refused.value.details or "")
        assert "--install-requirements" in (refused.value.details or "")
        assert machine.installed == []

    def test_install_provides_what_noust_can_provide(self, machine: SimpleNamespace) -> None:
        refuse_unmet(native(), install=True, logger=Logger())

        assert sorted(machine.installed) == ["node@22", "postgresql"]

    def test_install_does_not_hide_what_it_cannot_provide(self, machine: SimpleNamespace) -> None:
        machine.engines = {"postgresql": "installed"}
        machine.runtimes = {"node@22"}

        with pytest.raises(RecipeError) as refused:
            refuse_unmet(native(), install=True, logger=Logger())

        assert "not running" in refused.value.message
        assert "--install-requirements" not in (refused.value.details or "")

    def test_the_tools_are_left_to_the_deployments_own_check_by_default(
        self, machine: SimpleNamespace
    ) -> None:
        machine.tools_missing = ["Docker"]

        refuse_unmet(stack(), install=False, logger=Logger())

        with pytest.raises(RecipeError, match="Docker"):
            refuse_unmet(stack(), install=False, logger=Logger(), kinds=(TOOL,))


class TestTheInstallPassesThroughIt:
    def test_plan_recipe_refuses_before_anything_is_provisioned(
        self, monkeypatch: pytest.MonkeyPatch, recipe_store: Any
    ) -> None:
        from noust.recipes import deploy

        calls: list[str] = []
        monkeypatch.setattr(
            deploy,
            "refuse_unmet",
            lambda recipe, **kwargs: (_ for _ in ()).throw(
                RecipeError("This server cannot run it")
            ),
        )
        monkeypatch.setattr(
            deploy, "_provision_requirements", lambda *a, **k: calls.append("provisioned")
        )

        with pytest.raises(RecipeError, match="cannot run"):
            deploy.plan_recipe(
                "uptime-kuma", "kuma.example.com", port=3001, ssl=True, logger=Logger()
            )

        assert calls == []

    def test_install_requirements_is_passed_on(
        self, monkeypatch: pytest.MonkeyPatch, recipe_store: Any
    ) -> None:
        from noust.recipes import deploy

        seen: dict[str, Any] = {}
        monkeypatch.setattr(deploy, "refuse_unmet", lambda recipe, **kwargs: seen.update(kwargs))

        deploy.plan_recipe(
            "uptime-kuma",
            "kuma.example.com",
            port=3001,
            ssl=True,
            logger=Logger(),
            install_requirements=True,
        )

        assert seen["install"] is True


class TestTheCommand:
    def test_it_lists_each_requirement_and_exits_1_when_one_is_missing(
        self, machine: SimpleNamespace
    ) -> None:
        machine.tools_missing = ["Docker"]

        result = CliRunner().invoke(cli, ["recipe", "requirements", "vaultwarden", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["recipe"] == "vaultwarden"
        assert payload["items"][0]["met"] is False

    def test_it_exits_0_for_a_server_that_can_run_it(self, machine: SimpleNamespace) -> None:
        result = CliRunner().invoke(cli, ["recipe", "requirements", "vaultwarden"])

        assert result.exit_code == 0, result.output
        assert "Docker with the Compose plugin" in result.output

    def test_install_offers_to_provide_them(self) -> None:
        result = CliRunner().invoke(cli, ["recipe", "install", "--help"])

        assert "--install-requirements" in result.output


class TestTheApi:
    @pytest.fixture
    def client(self, recipe_store: Any) -> TestClient:
        from tests.recipes.test_api import small_app

        return TestClient(small_app())

    def test_it_answers_what_is_met_and_what_is_not(
        self, client: TestClient, machine: SimpleNamespace
    ) -> None:
        machine.tools_missing = ["Docker"]

        body = client.get("/api/recipes/vaultwarden/requirements").json()

        assert body["ready"] is False
        assert body["items"][0]["name"] == "Docker"
        assert body["items"][0]["fix"]

    def test_a_server_that_can_run_it_is_ready(
        self, client: TestClient, machine: SimpleNamespace
    ) -> None:
        assert client.get("/api/recipes/vaultwarden/requirements").json()["ready"] is True

    def test_an_unknown_recipe_is_a_404(self, client: TestClient) -> None:
        assert client.get("/api/recipes/nothing/requirements").status_code == 404
