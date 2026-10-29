# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A recipe that is root on the server by design is offered with a warning and a confirmation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.logger import Logger
from noust.core.server_text import CATALOG, HINT_SUFFIX
from noust.core.store import NoustStore
from noust.deployers.helpers import sandbox
from noust.recipes import RecipeError, parse_recipe
from noust.recipes import deploy as deploy_module
from noust.recipes.deploy import plan_recipe
from noust.recipes.model import ROOT_EQUIVALENT_KINDS, ROOT_EQUIVALENT_SENTENCES
from tests.recipes.factory import DIGEST, compose_recipe, use_demo_assets

SOCKET = "/var/run/docker.sock:/var/run/docker.sock"
LOG = Logger(verbose=False)


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the assets of a shipped recipe."""
    use_demo_assets(monkeypatch)


def manager(*mounts: str, **extra: str) -> dict[str, Any]:
    """A Compose recipe whose web service mounts what is given, as Portainer's does."""
    lines = "".join(f"      - {mount}\n" for mount in mounts)
    extras = "".join(f"      {key}: {value}\n" for key, value in extra.items())
    document = (
        "services:\n"
        "  web:\n"
        f"    image: ghcr.io/o/app:1.2.3@{DIGEST}\n"
        "    restart: unless-stopped\n"
        f"{extras}"
        "    volumes:\n"
        f"{lines}"
        "      - data:/data\n"
        "    ports:\n"
        '      - "127.0.0.1:${PORT}:9000"\n'
        "volumes:\n"
        "  data:\n"
    )
    return compose_recipe(compose=document, variables={})


def test_the_docker_socket_is_refused_in_a_recipe_that_does_not_say_what_it_is() -> None:
    """The rule of the closed schema: a recipe is not root on the host."""
    with pytest.raises(RecipeError, match=r"docker\.sock|must be a declared named volume"):
        parse_recipe("demo", manager(SOCKET))


def test_a_recipe_that_says_it_is_root_on_the_server_may_mount_the_docker_socket() -> None:
    """It names what it is, and the catalogue says so in the server's words."""
    recipe = parse_recipe("demo", {**manager(SOCKET), "root_equivalent": "docker_socket"})

    summary = recipe.summary()
    assert recipe.root_equivalent == "docker_socket"
    assert summary["root_equivalent"] == "docker_socket"
    assert summary["root_equivalent_warning"] == {
        "code": "recipes.root_equivalent.docker_socket",
        "params": {"title": "Demo"},
    }
    assert "Demo" in summary["root_equivalent_text"] and "root" in summary["root_equivalent_text"]
    assert summary["root_equivalent_hint"]


def test_a_recipe_that_installs_without_being_root_says_nothing_of_it() -> None:
    """The fields are there and empty."""
    summary = parse_recipe("demo", compose_recipe()).summary()

    assert summary["root_equivalent"] is None and summary["root_equivalent_warning"] is None
    assert summary["root_equivalent_text"] is None


@pytest.mark.parametrize(
    "mount",
    [
        "/var/run/docker.sock:/docker.sock",
        "/run/docker.sock:/var/run/docker.sock",
        "/var/run:/var/run",
        "/:/host",
        "/var/lib/docker:/var/lib/docker",
        "/var/run/docker.sock:/var/run/docker.sock:rw,z",
    ],
)
def test_the_allowance_is_the_socket_and_nothing_else_of_the_host(mount: str) -> None:
    """A recipe that says it controls Docker is not given the rest of the machine."""
    with pytest.raises(RecipeError):
        parse_recipe("demo", {**manager(mount), "root_equivalent": "docker_socket"})


def test_the_socket_may_be_mounted_read_only_and_a_privileged_container_never() -> None:
    """``:ro`` is the same socket; privileged is another thing."""
    parse_recipe("demo", {**manager(SOCKET + ":ro"), "root_equivalent": "docker_socket"})
    document = manager(SOCKET)["compose"].replace(
        "    restart: unless-stopped\n", "    restart: unless-stopped\n    privileged: true\n"
    )
    with pytest.raises(RecipeError, match="privileged"):
        parse_recipe(
            "demo", {**manager(SOCKET), "compose": document, "root_equivalent": "docker_socket"}
        )


def test_what_it_says_it_is_comes_from_a_closed_list_and_only_a_stack_can_say_it() -> None:
    """A free-text kind would drift; a native recipe has no socket to mount."""
    with pytest.raises(RecipeError, match="root_equivalent must be one of"):
        parse_recipe("demo", {**manager(SOCKET), "root_equivalent": "because"})
    with pytest.raises(RecipeError, match=r"root_equivalent|unknown keys"):
        parse_recipe(
            "demo",
            {
                "schema": 2,
                "name": "demo",
                "kind": "native",
                "root_equivalent": "docker_socket",
            },
        )


def test_every_kind_has_a_sentence_and_a_fix_and_the_confirmation_is_asked_in_words() -> None:
    """The console translates them."""
    for kind in ROOT_EQUIVALENT_KINDS:
        assert ROOT_EQUIVALENT_SENTENCES[kind] in CATALOG
        assert ROOT_EQUIVALENT_SENTENCES[kind] + HINT_SUFFIX in CATALOG
    assert "recipes.root_equivalent.confirm_required" in CATALOG
    assert "recipes.root_equivalent.confirm_required" + HINT_SUFFIX in CATALOG


# ---------------------------------------------------------------------------
# Installing one
# ---------------------------------------------------------------------------


def use(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any]) -> None:
    """Make ``demo`` a recipe of the catalogue for the plan."""
    recipe = parse_recipe("demo", data)
    monkeypatch.setattr(deploy_module, "get_recipe", lambda name: recipe)


def test_installing_one_without_the_confirmation_is_refused_before_anything_exists(
    monkeypatch: pytest.MonkeyPatch, recipe_store: NoustStore
) -> None:
    """The guard is the plan's, so the command line, the API and a job all meet it."""
    use(monkeypatch, {**manager(SOCKET), "root_equivalent": "docker_socket"})

    with pytest.raises(RecipeError) as refused:
        plan_recipe("demo", "tools.example.com", port=8123, ssl=True, logger=LOG)

    assert refused.value.coded is not None
    assert refused.value.coded.code == "recipes.root_equivalent.confirm_required"
    assert "--accept-root-equivalent" in refused.value.details
    assert sandbox.get_compose_exception("tools.example.com", store=recipe_store) is None


def test_the_confirmation_records_why_this_stack_may_mount_the_socket_and_who_said_so(
    monkeypatch: pytest.MonkeyPatch, recipe_store: NoustStore
) -> None:
    """The stack's exception is the one the compose guard reads: the same record, audited."""
    use(monkeypatch, {**manager(SOCKET), "root_equivalent": "docker_socket"})

    plan = plan_recipe(
        "demo",
        "tools.example.com",
        port=8123,
        ssl=True,
        logger=LOG,
        accept_root_equivalent="operator@example.com",
    )

    exception = sandbox.get_compose_exception("tools.example.com", store=recipe_store)
    assert exception is not None and exception.allowed_by == "operator@example.com"
    assert "demo" in exception.reason and "Docker" in exception.reason
    assert plan.recipe.root_equivalent == "docker_socket"


def test_a_confirmation_given_for_a_recipe_that_is_not_root_records_nothing(
    monkeypatch: pytest.MonkeyPatch, recipe_store: NoustStore
) -> None:
    """Accepting what is not there is not an exception to keep."""
    use(monkeypatch, compose_recipe())

    plan_recipe(
        "demo",
        "tools.example.com",
        port=8123,
        ssl=True,
        logger=LOG,
        accept_root_equivalent="operator@example.com",
    )

    assert sandbox.get_compose_exception("tools.example.com", store=recipe_store) is None


# ---------------------------------------------------------------------------
# Through the API and the command line
# ---------------------------------------------------------------------------


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the jobs instead of running them."""
    captured: list[dict[str, Any]] = []

    class Queued:
        id = "job-1"
        status = type("Status", (), {"value": "pending"})()

        def to_dict(self) -> dict[str, Any]:
            return {"id": self.id}

    def create_job(**kwargs: Any) -> Queued:
        captured.append(kwargs)
        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.apps.get_job_manager", lambda: manager)
    return captured


def api_as(session: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The recipes router with ``demo`` (root on the server) in the catalogue, signed in as this."""
    from fastapi.testclient import TestClient

    from noust.web.api import apps as apps_api
    from noust.web.api import recipes as recipes_api
    from noust.web.api.auth import get_current_session
    from tests.support.recipes_api import small_app

    recipe = parse_recipe("demo", {**manager(SOCKET), "root_equivalent": "docker_socket"})
    monkeypatch.setattr(apps_api, "get_recipe", lambda name: recipe)
    monkeypatch.setattr(recipes_api, "get_recipe", lambda name: recipe)
    api = small_app()
    api.dependency_overrides[get_current_session] = lambda: session
    return TestClient(api)


MASTER = {"type": "master", "scope": "admin", "name": "master"}
CONSOLE = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}


def test_the_api_refuses_a_root_recipe_nobody_confirmed_and_says_the_command(
    monkeypatch: pytest.MonkeyPatch, recipe_store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """A 400 with the sentence, its code and the way to confirm; no job."""
    client = api_as(MASTER, monkeypatch)

    response = client.post("/api/recipes/demo/install", json={"domain": "tools.example.com"})

    assert response.status_code == 400, response.text
    body = response.json()
    assert "recipes.root_equivalent.confirm_required" in response.text
    assert "accept_root_equivalent" in response.text or "--accept-root-equivalent" in response.text
    assert queued == [] and body


def test_the_console_asks_for_sudo_mode_before_a_root_recipe_is_queued(
    monkeypatch: pytest.MonkeyPatch, recipe_store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """Confirmed in the request, but the person has not confirmed recently: 403 elevation_required."""
    client = api_as(CONSOLE, monkeypatch)

    response = client.post(
        "/api/recipes/demo/install",
        json={"domain": "tools.example.com", "accept_root_equivalent": True},
    )

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"
    assert queued == []


def test_a_confirmed_root_recipe_is_queued_in_the_name_of_whoever_confirmed(
    monkeypatch: pytest.MonkeyPatch, recipe_store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """The job carries the account that said yes, and nothing is queued for a root recipe without it."""
    from noust.web.api.deps import actor_label

    client = api_as(MASTER, monkeypatch)

    response = client.post(
        "/api/recipes/demo/install",
        json={"domain": "tools.example.com", "accept_root_equivalent": True},
    )

    assert response.status_code == 202, response.text
    assert len(queued) == 1
    # The confirmation is the stack's exception for the compose guard, in that account's name:
    # the job that plans the recipe finds it there.
    exception = sandbox.get_compose_exception("tools.example.com", store=recipe_store)
    assert exception is not None and exception.allowed_by == actor_label(MASTER)
    assert "demo" in exception.reason


def test_a_recipe_that_is_not_root_is_queued_without_asking_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch, recipe_store: NoustStore, queued: list[dict[str, Any]]
) -> None:
    """Nothing changes for the others."""
    from fastapi.testclient import TestClient

    from tests.support.recipes_api import small_app

    client = TestClient(small_app())

    response = client.post("/api/recipes/vaultwarden/install", json={"domain": "pw.example.com"})

    assert response.status_code == 202, response.text
    assert sandbox.get_compose_exception("pw.example.com", store=recipe_store) is None


def test_the_compose_guard_lets_the_stack_of_a_confirmed_recipe_through_and_not_another(
    recipe_store: NoustStore, tmp_path: Any
) -> None:
    """The confirmation is the exception the guard reads: Portainer deploys, a stranger's stack does not."""
    import yaml

    from noust.core.exceptions import DeploymentError
    from noust.deployers.docker_compose import DockerComposeDeployer

    plan = plan_recipe(
        "portainer",
        "tools.example.com",
        port=8123,
        ssl=True,
        logger=LOG,
        accept_root_equivalent="operator@example.com",
    )
    document = yaml.safe_load((Path(plan.source) / "docker-compose.yml").read_text())

    allowed = DockerComposeDeployer()
    allowed.configure(
        domain="tools.example.com", port=8123, webserver="nginx", ssl=True, source=plan.source
    )
    allowed.app_path = tmp_path
    allowed._check_host_privileges(document)

    other = DockerComposeDeployer()
    other.configure(
        domain="other.example.com", port=8124, webserver="nginx", ssl=True, source=plan.source
    )
    other.app_path = tmp_path
    with pytest.raises(DeploymentError, match="root on this server"):
        other._check_host_privileges(document)
