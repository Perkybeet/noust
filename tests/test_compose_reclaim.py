# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose stack that runs outside its unit, and handing it back (item 73).

The case: on a production server convertidordepdf.com's unit was stopped and
disabled while its three containers had been up for 37 hours, because someone
ran ``docker compose up -d`` by hand. ``noust list``, the console and the
overview called it stopped and the monitor said its unit was not running,
while the site served. It is its own state, ``Running outside Noust``, the
monitor says so once, and ``noust app reclaim`` hands it back to its unit
without recreating a container unless the operator accepts it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.core.app_state import (
    FAILED,
    RUNNING,
    RUNNING_UNMANAGED,
    STOPPED,
    resolve_state,
    resolve_states,
)
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers.compose_adopt import StackContainer
from noust.deployers.compose_reclaim import running_outside_units, stack_identity
from noust.managers.nginx_manager import NginxManager
from tests.test_app_reachability_events import (
    DOMAIN as SHOP,
)
from tests.test_app_reachability_events import (
    STOPPED_BY_COMMAND,
    Scans,
)
from tests.test_compose_adopt import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    PROJECT,
    RECREATES,
    UNIT,
    Docker,
    adopt,
    docker,
    root,
    store,
    units,
    web,
)

# The adoption tests' fixtures are imported rather than replicated, so there
# stays one fake of Proggest's server.
# ruff: noqa: F811

CONVERTER = "convertidordepdf.com"
CONVERTER_PATH = "/var/www/apps/convertidordepdf-com"
CONVERTER_CONTAINERS = (
    "convertidordepdf-com-backend-1",
    "convertidordepdf-com-db-1",
    "convertidordepdf-com-frontend-1",
)


def ps_line(name: str, project: str, working_dir: str, state: str = "running") -> str:
    """One line of ``docker ps`` in the format the container lister asks for."""
    return "\t".join(
        (f"{abs(hash(name)):064x}", name, state, project, f"{working_dir}/compose.yml", working_dir)
    )


def converter_ps(state: str = "running") -> str:
    """convertidordepdf.com's three containers, as ``docker ps -a`` lists them."""
    return "".join(
        ps_line(name, "convertidordepdf-com", CONVERTER_PATH, state) + "\n"
        for name in CONVERTER_CONTAINERS
    )


def stack(domain: str = CONVERTER, app_path: str = CONVERTER_PATH, **fields: Any) -> App:
    """A Compose stack's row."""
    return App(
        id=1, domain=domain, port=3000, app_type="docker-compose", app_path=app_path, **fields
    )


class Units:
    """A systemd that answers what the test scripted, as ``ServiceManager`` would."""

    def __init__(self, statuses: dict[str, dict[str, Any]]) -> None:
        self.statuses = statuses

    def serving_units(self, app: App) -> list[str]:
        return [app.domain.replace(".", "-")]

    def get_status(self, name: str) -> dict[str, Any]:
        return self.statuses[name]


INACTIVE = {"exists": True, "active": False, "active_state": "inactive", "sub_state": "dead"}
FAILED_UNIT = {"exists": True, "active": False, "active_state": "failed", "sub_state": "failed"}
ACTIVE = {"exists": True, "active": True, "active_state": "active", "sub_state": "exited"}


def docker_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:1] == ("docker",)]


# -- Which containers are an application's -----------------------------------------


def container(project: str, working_dir: str) -> StackContainer:
    return StackContainer("id", "c", "running", project, (), working_dir)


def test_a_stack_is_known_by_its_derived_project_and_its_directory() -> None:
    identity = stack_identity(stack())

    assert identity.derived == "convertidordepdf-com"
    assert identity.matches(container("convertidordepdf-com", "/elsewhere"))
    assert identity.matches(container("renamed", CONVERTER_PATH + "/"))
    assert not identity.matches(container("other", "/srv/other"))


def test_an_adopted_stack_is_known_by_the_project_the_store_pins() -> None:
    identity = stack_identity(stack(compose_project="proggest"))

    assert identity.matches(container("proggest", "/opt/proggest"))
    assert identity.filters()[0] == "label=com.docker.compose.project=proggest"


def test_one_docker_call_answers_for_every_stack(runner: FakeRunner) -> None:
    runner.script(
        ["docker", "ps"],
        stdout=converter_ps()
        + ps_line("shop-web-1", "shop-example-com", "/var/www/apps/shop-example-com", "exited")
        + "\n",
    )
    apps = [stack(), stack("shop.example.com", "/var/www/apps/shop-example-com")]

    found = running_outside_units(apps, runner=runner)

    assert found == {CONVERTER: CONVERTER_CONTAINERS}
    assert len(docker_calls(runner)) == 1


def test_no_stack_asks_nothing(runner: FakeRunner) -> None:
    assert running_outside_units([App(id=1, domain="a.com", app_type="nextjs")]) == {}
    assert docker_calls(runner) == []


# -- The state -----------------------------------------------------------------------


def test_a_stopped_unit_with_its_containers_up_is_running_outside_noust(
    runner: FakeRunner,
) -> None:
    runner.script(["docker", "ps"], stdout=converter_ps())

    state = resolve_state(stack(), Units({"convertidordepdf-com": INACTIVE}), probe=False)  # type: ignore[arg-type]

    assert state.label == RUNNING_UNMANAGED
    assert not state.healthy
    assert "convertidordepdf-com-db-1" in state.detail
    assert "reboot" in state.detail
    assert f"noust app reclaim {CONVERTER}" in state.detail


def test_a_stopped_unit_with_nothing_running_is_stopped(runner: FakeRunner) -> None:
    runner.script(["docker", "ps"], stdout=converter_ps("exited"))

    state = resolve_state(stack(), Units({"convertidordepdf-com": INACTIVE}), probe=False)  # type: ignore[arg-type]

    assert state.label == STOPPED
    assert state.detail == "the unit is not running"


def test_a_running_unit_never_asks_docker(runner: FakeRunner) -> None:
    state = resolve_state(stack(), Units({"convertidordepdf-com": ACTIVE}), probe=False)  # type: ignore[arg-type]

    assert state.label == RUNNING
    assert docker_calls(runner) == []


def test_a_failed_unit_stays_failed_whatever_runs(runner: FakeRunner) -> None:
    runner.script(["docker", "ps"], stdout=converter_ps())

    state = resolve_state(stack(), Units({"convertidordepdf-com": FAILED_UNIT}), probe=False)  # type: ignore[arg-type]

    assert state.label == FAILED
    assert docker_calls(runner) == []


def test_a_docker_that_cannot_be_asked_leaves_it_stopped(runner: FakeRunner) -> None:
    runner.script(["docker", "ps"], stderr="Cannot connect to the Docker daemon", exit_code=1)

    state = resolve_state(stack(), Units({"convertidordepdf-com": INACTIVE}), probe=False)  # type: ignore[arg-type]

    assert state.label == STOPPED


def test_a_list_asks_docker_once_for_every_stack_whose_unit_is_down(runner: FakeRunner) -> None:
    runner.script(["docker", "ps"], stdout=converter_ps())
    apps = [
        stack(),
        stack("other.example.com", "/var/www/apps/other-example-com"),
        App(id=3, domain="node.example.com", port=3001, app_type="nextjs"),
    ]
    units = Units(
        {
            "convertidordepdf-com": INACTIVE,
            "other-example-com": INACTIVE,
            "node-example-com": INACTIVE,
        }
    )

    states = resolve_states(apps, units, probe=False)  # type: ignore[arg-type]

    assert states[CONVERTER].label == RUNNING_UNMANAGED
    assert states["other.example.com"].label == STOPPED
    assert states["node.example.com"].label == STOPPED
    assert len(docker_calls(runner)) == 1


def test_the_api_names_the_state_running_unmanaged() -> None:
    from noust.web.api.apps import _STATUS_LABELS

    assert _STATUS_LABELS[RUNNING_UNMANAGED] == "running_unmanaged"


# -- The machine snapshot and the overview -------------------------------------------


def test_the_snapshot_counts_a_stack_running_outside_its_unit_on_its_own(
    runner: FakeRunner, store: NoustStore
) -> None:
    from noust.web import machine

    machine._outside_units_cache.clear()
    store.create_app(stack())
    runner.script(["docker", "ps"], stdout=converter_ps())
    services = [
        {"name": "convertidordepdf-com", "active": "inactive", "sub": "dead", "app": CONVERTER}
    ]

    states = machine.classify_apps(services)
    tally = machine._count_apps(services)

    assert states == {CONVERTER: machine.APP_UNMANAGED}
    assert (tally.unmanaged, tally.stopped, tally.running) == (1, 0, 0)


def test_the_overview_lists_it_as_a_warning(runner: FakeRunner, store: NoustStore) -> None:
    from noust.managers import overview
    from noust.web import machine

    machine._outside_units_cache.clear()
    store.create_app(stack())
    runner.script(["docker", "ps"], stdout=converter_ps())
    services = [
        {"name": "convertidordepdf-com", "active": "inactive", "sub": "dead", "app": CONVERTER}
    ]

    items, _total = overview._attention(store, services, lambda: [], lambda: [], [stack()])
    figure = overview._apps_figure(machine.classify_apps(services))

    (item,) = [item for item in items if item["id"] == f"app:{CONVERTER}"]
    assert item["severity"] == "warn"
    assert [reason["code"] for reason in item["reasons"]] == ["running_outside_unit"]
    assert figure["unmanaged"] == 1 and figure["stopped"] == 0


# -- The monitor ---------------------------------------------------------------------


@pytest.fixture
def stack_scans(tmp_path: Path, store: NoustStore) -> Scans:
    return Scans(tmp_path, store, app_type="docker-compose")


def shop_ps(state: str = "running") -> str:
    return (
        ps_line(
            "shop-example-com-web-1", "shop-example-com", "/var/www/apps/shop-example-com", state
        )
        + "\n"
    )


def test_the_monitor_says_once_that_a_stack_runs_outside_its_unit(stack_scans: Scans) -> None:
    stack_scans.runner.script(["docker", "ps"], stdout=shop_ps())

    for _ in range(3):
        stack_scans.scan(STOPPED_BY_COMMAND)

    assert stack_scans.codes == ["app.outside_unit"]
    (event,) = stack_scans.notifier.events
    assert event.subject == SHOP
    assert event.kind == "unit_failed"
    assert event.state.value == "warning"
    assert event.command is not None and event.command.value == f"noust app reclaim {SHOP}"
    assert "shop-example-com-web-1" in {fact.key: fact.value for fact in event.facts}["containers"]


def test_a_restarted_monitor_does_not_say_it_again(stack_scans: Scans) -> None:
    stack_scans.runner.script(["docker", "ps"], stdout=shop_ps())
    stack_scans.scan(STOPPED_BY_COMMAND)

    stack_scans.monitor = stack_scans.build()
    stack_scans.scan(STOPPED_BY_COMMAND)

    assert stack_scans.codes == ["app.outside_unit"]


def test_it_is_said_again_after_it_stopped_running_outside(stack_scans: Scans) -> None:
    stack_scans.runner.script(["docker", "ps"], stdout=shop_ps())
    stack_scans.scan(STOPPED_BY_COMMAND)
    stack_scans.runner.script(["docker", "ps"], stdout=shop_ps("exited"))
    stack_scans.scan(STOPPED_BY_COMMAND)
    stack_scans.runner.script(["docker", "ps"], stdout=shop_ps())
    stack_scans.scan(STOPPED_BY_COMMAND)

    assert stack_scans.codes == ["app.outside_unit", "app.outside_unit"]


def test_a_stopped_stack_with_nothing_running_says_nothing(stack_scans: Scans) -> None:
    stack_scans.runner.script(["docker", "ps"], stdout=shop_ps("exited"))

    stack_scans.scan(STOPPED_BY_COMMAND)

    assert stack_scans.codes == []


def test_a_running_unit_asks_docker_nothing(stack_scans: Scans) -> None:
    stack_scans.scan()

    assert docker_calls(stack_scans.runner) == []


# -- Handing it back -------------------------------------------------------------------


def unit_starts(docker: Docker) -> list[tuple[str, ...]]:
    return [
        call
        for call in docker.calls
        if call[:2] in (("systemctl", "start"), ("systemctl", "enable"))
    ]


@pytest.fixture
def adopted(root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path) -> Path:
    """Proggest adopted: a unit enabled and not started, its containers up."""
    adopt(root, web)
    docker.calls.clear()
    return root


def test_handing_it_back_enables_and_starts_the_unit(adopted: Path, docker: Docker) -> None:
    from noust.deployers.compose_reclaim import plan_reclaim, reclaim

    plan = plan_reclaim(DOMAIN)
    result = reclaim(plan)

    assert result.reclaimed
    assert plan.unit == UNIT and plan.project == PROJECT and plan.changes == ()
    assert "proggest-backend" in plan.containers
    assert unit_starts(docker) == [
        ("systemctl", "enable", f"{UNIT}.service"),
        ("systemctl", "start", f"{UNIT}.service"),
    ]
    dry_run = next(call for call in docker.calls if "--dry-run" in call)
    assert dry_run[:4] == ("docker", "compose", "-p", PROJECT)


def test_a_start_that_would_recreate_is_refused_with_compose_output(
    adopted: Path, docker: Docker
) -> None:
    from noust.deployers.compose_adopt import AdoptionRefusedError
    from noust.deployers.compose_reclaim import reclaim_stack

    docker.dry_run = RECREATES

    with pytest.raises(AdoptionRefusedError) as raised:
        reclaim_stack(DOMAIN)

    assert "Recreated" in raised.value.output
    assert "hand it back with --accept-recreate" in raised.value.details
    assert unit_starts(docker) == []


def without_the_docker_socket(root: Path) -> None:
    """Take the Docker socket mount out of Proggest's compose file."""
    compose = root / "docker-compose.prod.yml"
    compose.write_text(
        compose.read_text().replace("      - /var/run/docker.sock:/var/run/docker.sock:ro\n", "")
    )


def test_accepting_the_recreate_hands_it_back(adopted: Path, docker: Docker) -> None:
    from noust.deployers.compose_reclaim import reclaim_stack

    # A recreate proves nothing about what the file asks of the host: that
    # is the guard's subject below, not this test's.
    without_the_docker_socket(adopted)
    docker.dry_run = RECREATES

    result = reclaim_stack(DOMAIN, accept_recreate=True)

    assert result.reclaimed
    assert result.plan.changes == ("Container proggest-backend   Recreated",)
    assert ("systemctl", "start", f"{UNIT}.service") in unit_starts(docker)


def test_a_recreate_does_not_carry_a_docker_socket_the_stack_is_not_proven_to_have(
    adopted: Path, docker: Docker
) -> None:
    """
    Handing back starts ``up -d`` on the file as it is now, with the guard adoption runs.

    It used to skip the guard: a file edited by hand to mount the Docker
    socket (root on the server) was started by the unit once the operator
    accepted the recreate the edit caused.
    """
    from noust.deployers.compose_reclaim import reclaim_stack

    docker.dry_run = RECREATES

    with pytest.raises(DeploymentError, match=r"docker\.sock|Docker socket"):
        reclaim_stack(DOMAIN, accept_recreate=True)
    assert unit_starts(docker) == []


def test_a_unit_started_after_the_plan_is_not_started_again(adopted: Path, docker: Docker) -> None:
    """The plan is made without the lock; the unit is asked again under it."""
    from noust.deployers.compose_reclaim import plan_reclaim, reclaim

    plan = plan_reclaim(DOMAIN)
    docker.script(["systemctl", "is-active"], stdout="active\n")

    with pytest.raises(DeploymentError, match="already runs under its unit"):
        reclaim(plan)
    assert unit_starts(docker) == []


def test_a_unit_that_runs_has_nothing_to_hand_back(adopted: Path, docker: Docker) -> None:
    from noust.deployers.compose_reclaim import plan_reclaim

    docker.script(["systemctl", "is-active"], stdout="active\n")

    with pytest.raises(DeploymentError, match="already runs under its unit"):
        plan_reclaim(DOMAIN)


def test_a_stack_with_nothing_running_is_stopped_not_outside(adopted: Path, docker: Docker) -> None:
    from noust.deployers.compose_reclaim import plan_reclaim

    docker.containers = [(*c[:2], "exited", *c[3:]) for c in docker.containers]

    with pytest.raises(DeploymentError, match=r"None of .* containers is running"):
        plan_reclaim(DOMAIN)
    assert unit_starts(docker) == []


def test_the_command_hands_it_back_and_prints_json(adopted: Path, docker: Docker) -> None:
    from noust.cli.app import cli as root_cli

    result = CliRunner().invoke(root_cli, ["app", "reclaim", DOMAIN, "--yes", "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output.strip().splitlines()[-1])
    assert body["reclaimed"] is True and body["unit"] == UNIT


def test_the_command_changes_nothing_when_not_confirmed(adopted: Path, docker: Docker) -> None:
    from noust.cli.app import cli as root_cli

    result = CliRunner().invoke(root_cli, ["app", "reclaim", DOMAIN], input="n\n")

    assert result.exit_code != 0
    assert unit_starts(docker) == []


def reclaim_client(*, elevated: bool) -> Any:
    """A client for the stack router alone, authenticated, elevated or not."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import app_stack
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_stack.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


def test_the_route_is_an_operate_permission() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_app_stack import ROUTES

    assert ROUTES[("POST", "/api/apps/{domain}/reclaim")] == Permission.APPS_OPERATE


def test_the_api_needs_sudo_mode(adopted: Path, docker: Docker) -> None:
    response = reclaim_client(elevated=False).post(f"/api/apps/{DOMAIN}/reclaim", json={})

    assert response.status_code == 403
    assert unit_starts(docker) == []


def test_the_api_previews_then_hands_it_back(adopted: Path, docker: Docker) -> None:
    client = reclaim_client(elevated=True)

    preview = client.post(f"/api/apps/{DOMAIN}/reclaim", json={"preview": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["reclaimed"] is False
    assert unit_starts(docker) == []

    done = client.post(f"/api/apps/{DOMAIN}/reclaim", json={})
    assert done.status_code == 200, done.text
    assert done.json()["reclaimed"] is True
    assert ("systemctl", "start", f"{UNIT}.service") in unit_starts(docker)


def test_the_api_refuses_a_recreate_with_409_and_the_output(adopted: Path, docker: Docker) -> None:
    docker.dry_run = RECREATES

    response = reclaim_client(elevated=True).post(f"/api/apps/{DOMAIN}/reclaim", json={})

    assert response.status_code == 409
    assert "Recreated" in response.json()["output"]
    assert "accept_recreate" in response.json()["hint"]
    assert unit_starts(docker) == []


def test_handing_it_back_is_audited(
    adopted: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core import audit
    from noust.deployers.compose_reclaim import reclaim_stack

    recorded: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(audit, "record", lambda event, **kw: recorded.append((event, kw)))

    reclaim_stack(DOMAIN)

    assert [event for event, _ in recorded] == ["apps.reclaim"]
    assert recorded[0][1]["target"] == f"app:{DOMAIN}"
