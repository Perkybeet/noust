# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adopting a Docker Compose stack that already runs (spec 3.2 section 1.4).

The case these tests model is Proggest: a clone in ``/opt/proggest`` brought
up by its own ``deploy.sh`` with ``docker-compose.prod.yml`` under the project
``proggest`` (fixed container names, volumes ``proggest_pgdata`` and
``proggest_redisdata``), served by a site file the operator wrote and called
``proggest``. Adopting it must not touch any of that: nothing cloned, nothing
cleaned, no container recreated, the site left byte for byte; what Noust adds
is a store row, a unit that is enabled but not started, and a first history
row.

The directory here is called ``proggest-src`` on purpose: the project Compose
would derive from it is ``proggest-src``, so a test that finds ``proggest``
proves it was read from the containers' labels.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.core.exceptions import DeploymentError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore, get_store
from noust.deployers.helpers.target import claim_deploy_target
from noust.managers.nginx_manager import NginxManager
from noust.managers.service_manager import ServiceManager
from noust.managers.webserver import NGINX_BACKEND

DOMAIN = "proggest.es"
UNIT = "proggest-es"
PROJECT = "proggest"
COMMIT = "4f2463e"
REMOTE = "git@github.com:Perkybeet/proggest.git"
FIXTURES = Path(__file__).parent / "fixtures" / "compose"

OPERATOR_SITE = """# Proggest, written by hand
upstream nestjs_upstream { server 127.0.0.1:3000; }
server {
    listen 80;
    server_name proggest.es www.proggest.es;
    location /api/ { proxy_pass http://nestjs_upstream; }
    location / { proxy_pass http://127.0.0.1:3001; }
}
"""

UNCHANGED = """[+] Running 4/0
 ✔ DRY-RUN MODE -  Container proggest-postgres  Running   0.0s
 ✔ DRY-RUN MODE -  Container proggest-redis     Running   0.0s
 ✔ DRY-RUN MODE -  Container proggest-backend   Running   0.0s
 ✔ DRY-RUN MODE -  Container proggest-frontend  Running   0.0s
end of 'compose up' output, interactive run is not supported in dry-run mode
"""

RECREATES = """[+] Running 4/0
 ✔ DRY-RUN MODE -  Container proggest-postgres  Running     0.0s
 ✔ DRY-RUN MODE -  Container proggest-redis     Running     0.0s
 ✔ DRY-RUN MODE -  Container proggest-backend   Recreated   0.0s
 ✔ DRY-RUN MODE -  Container proggest-frontend  Running     0.0s
"""


class Docker(FakeRunner):
    """Docker and git answering as Proggest's server does."""

    def __init__(self, compose_file: Path) -> None:
        super().__init__()
        self.compose_file = compose_file
        self.dry_run = UNCHANGED
        self.dry_run_exit = 0
        self.containers = [
            (f"{n:064x}", name, "running", PROJECT, str(compose_file), str(compose_file.parent))
            for n, name in enumerate(
                ("proggest-postgres", "proggest-redis", "proggest-backend", "proggest-frontend"),
                start=1,
            )
        ]

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv
        if args[:2] == ("docker", "ps"):
            return replace(result, stdout="".join("\t".join(c) + "\n" for c in self.containers))
        if args[:2] == ("docker", "compose") and "--dry-run" in args:
            return replace(result, stderr=self.dry_run, exit_code=self.dry_run_exit)
        if args[:1] == ("git",):
            if args[-3:] == ("rev-parse", "--abbrev-ref", "HEAD"):
                return replace(result, stdout="main\n")
            if args[-3:] == ("rev-parse", "--short", "HEAD"):
                return replace(result, stdout=f"{COMMIT}\n")
            if args[-3:] == ("config", "--get", "remote.origin.url"):
                return replace(result, stdout=f"{REMOTE}\n")
        return result

    def mutating(self) -> list[tuple[str, ...]]:
        """Every command that could change the stack, the tree or the unit's state."""
        changing = {"clone", "fetch", "reset", "clean", "checkout", "pull"}
        found = []
        for call in self.calls:
            if call[:1] == ("git",) and changing & set(call):
                found.append(call)
            if call[:2] == ("docker", "compose") and "--dry-run" not in call:
                found.append(call)
            if call[:2] == ("systemctl", "start") or call[:2] == ("systemctl", "restart"):
                found.append(call)
        return found


@pytest.fixture
def store() -> Iterator[NoustStore]:
    """The process-wide store, where conftest puts it."""
    NoustStore.reset_instance()
    yield get_store()
    NoustStore.reset_instance()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Proggest's checkout, with what git does not track beside it."""
    path = tmp_path / "opt" / "proggest-src"
    (path / ".git").mkdir(parents=True)
    shutil.copy(FIXTURES / "proggest.docker-compose.prod.yml", path / "docker-compose.prod.yml")
    (path / ".env").write_text("DB_PASSWORD=secret\nREDIS_PASSWORD=secret\n")
    (path / "uploads").mkdir()
    (path / "uploads" / "invoice.pdf").write_bytes(b"%PDF data")
    return path


@pytest.fixture
def docker(root: Path) -> Iterator[Docker]:
    """The fake docker and git, as the process-wide runner."""
    fake = Docker(root / "docker-compose.prod.yml")
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private unit directory, and nothing in the system ones."""
    managed = tmp_path / "etc/systemd/system"
    managed.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", managed)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (managed,))
    return managed


@pytest.fixture
def web(tmp_path: Path, docker: Docker, store: NoustStore) -> NginxManager:
    """nginx over a temporary tree holding the operator's own site."""
    manager = NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )
    manager.sites_available.mkdir(parents=True)
    manager.sites_enabled.mkdir(parents=True)
    (manager.sites_available / "proggest").write_text(OPERATOR_SITE)
    (manager.sites_enabled / "proggest").symlink_to(manager.sites_available / "proggest")
    (manager.sites_available / "default").write_text("server { listen 80 default_server; }\n")
    return manager


def tree(path: Path) -> dict[str, bytes]:
    """Every file under a directory, with its content."""
    return {
        str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()
    }


def adopt(root: Path, web: NginxManager, **options: Any) -> Any:
    from noust.deployers.compose_adopt import adopt_stack

    return adopt_stack(DOMAIN, root, web=web, **options)


# -- The directory ----------------------------------------------------------------


def test_an_adoption_claims_a_directory_with_files_and_never_owns_it(tmp_path: Path) -> None:
    (tmp_path / "data").write_text("kept")

    target = claim_deploy_target(tmp_path, domain=DOMAIN, existing=None, replace=False, adopt=True)

    assert target.existed and target.had_files and not target.created_here


def test_without_adopting_the_same_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "data").write_text("kept")

    with pytest.raises(DeploymentError, match="already exists and is not empty"):
        claim_deploy_target(tmp_path, domain=DOMAIN, existing=None, replace=False)


# -- The plan: project, proof, site -----------------------------------------------


def test_the_project_is_read_from_the_labels_of_the_running_containers(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    plan = plan_adoption(DOMAIN, root, web=web)

    assert plan.project == PROJECT
    assert plan.project_from == "containers"
    assert plan.compose_file == "docker-compose.prod.yml"
    assert set(plan.containers) == {
        "proggest-postgres",
        "proggest-redis",
        "proggest-backend",
        "proggest-frontend",
    }
    dry_run = next(call for call in docker.calls if "--dry-run" in call)
    assert dry_run == (
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        str(root / "docker-compose.prod.yml"),
        "up",
        "-d",
        "--remove-orphans",
        "--dry-run",
        "--no-build",
    )
    assert docker.mutating() == []


def test_containers_of_another_compose_file_do_not_name_the_project(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    docker.containers = [
        ("a" * 64, "other-web", "running", "other", "/srv/other/compose.yml", "/srv/other")
    ]
    # Nothing runs from the file, so nothing proves its Docker socket mount is
    # the stack's: that is the guard's to refuse, not this test's subject.
    compose = root / "docker-compose.prod.yml"
    compose.write_text(
        compose.read_text().replace("      - /var/run/docker.sock:/var/run/docker.sock:ro\n", "")
    )

    plan = plan_adoption(DOMAIN, root, web=web)

    # Nothing runs from this file: the name Compose itself would give it.
    assert plan.project == "proggest-src"
    assert plan.project_from == "compose"


def test_two_running_projects_from_one_file_are_refused(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    compose = str(root / "docker-compose.prod.yml")
    docker.containers.append(("b" * 64, "copy-web", "running", "copy", compose, str(root)))

    with pytest.raises(DeploymentError, match="more than one Compose project") as raised:
        plan_adoption(DOMAIN, root, web=web)
    assert "copy" in raised.value.details and PROJECT in raised.value.details


def test_an_up_that_would_recreate_something_is_refused_with_its_output(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.deployers.compose_adopt import AdoptionRefusedError

    docker.dry_run = RECREATES
    before = tree(root)

    with pytest.raises(AdoptionRefusedError) as raised:
        adopt(root, web)

    assert "proggest-backend" in raised.value.output
    assert "Recreated" in raised.value.output
    assert "--accept-recreate" in raised.value.details
    assert raised.value.changes == ("Container proggest-backend   Recreated",)
    # Refused before anything was written.
    assert store.get_app(DOMAIN) is None
    assert list(units.iterdir()) == []
    assert tree(root) == before
    assert docker.mutating() == []


def test_accepting_the_recreate_adopts_and_says_so(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.deployers.helpers.sandbox import set_compose_exception

    docker.dry_run = RECREATES
    # A recreated backend is not proven to run with the Docker socket it
    # mounts: accepting the recreate takes the operator's recorded reason.
    set_compose_exception(DOMAIN, allowed=True, actor="alice", reason="deploy.sh", store=store)

    result = adopt(root, web, accept_recreate=True)

    assert store.get_app(DOMAIN) is not None
    assert any("proggest-backend" in warning for warning in result.plan.warnings)


def test_a_dry_run_that_fails_is_refused_with_its_output(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.deployers.compose_adopt import AdoptionRefusedError

    docker.dry_run = "unknown flag: --dry-run\n"
    docker.dry_run_exit = 1

    with pytest.raises(AdoptionRefusedError) as raised:
        adopt(root, web)

    assert "unknown flag: --dry-run" in raised.value.output
    assert "2.20" in raised.value.details
    assert store.get_app(DOMAIN) is None


def test_the_site_serving_the_domain_is_found_by_the_names_it_serves(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    plan = plan_adoption(DOMAIN, root, web=web)

    assert plan.site == "proggest"
    assert plan.site_name == "proggest"


def test_a_site_named_after_the_domain_records_no_site_name(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    (web.sites_enabled / "proggest").unlink()
    (web.sites_available / "proggest").rename(web.sites_available / DOMAIN)

    plan = plan_adoption(DOMAIN, root, web=web)

    assert plan.site == DOMAIN
    assert plan.site_name is None


def test_a_named_site_that_does_not_serve_the_domain_is_refused(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    with pytest.raises(DeploymentError, match=r"does not answer on proggest\.es"):
        plan_adoption(DOMAIN, root, web=web, site="default")


def test_two_sites_serving_the_domain_need_the_operator_to_choose(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    (web.sites_available / "proggest-old").write_text(OPERATOR_SITE)
    (web.sites_enabled / "proggest-old").symlink_to(web.sites_available / "proggest-old")

    with pytest.raises(DeploymentError, match="--site") as raised:
        plan_adoption(DOMAIN, root, web=web)
    assert "proggest-old" in raised.value.details

    assert plan_adoption(DOMAIN, root, web=web, site="proggest").site_name == "proggest"


def test_no_site_is_a_warning_not_a_refusal(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    (web.sites_enabled / "proggest").unlink()
    (web.sites_available / "proggest").unlink()

    plan = plan_adoption(DOMAIN, root, web=web)

    assert plan.site is None
    assert any("No site" in warning for warning in plan.warnings)


def test_a_domain_already_deployed_is_refused(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    store.create_app(App(domain=DOMAIN, app_type="docker-compose", app_path=str(root)))

    with pytest.raises(DeploymentError, match="already deployed"):
        plan_adoption(DOMAIN, root, web=web)


def test_a_directory_that_is_not_a_checkout_is_refused(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    shutil.rmtree(root / ".git")

    with pytest.raises(DeploymentError, match="not a git checkout"):
        plan_adoption(DOMAIN, root, web=web)


def test_a_relative_or_missing_path_is_refused(
    tmp_path: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    with pytest.raises(DeploymentError, match="absolute"):
        plan_adoption(DOMAIN, Path("opt/proggest"), web=web)
    with pytest.raises(DeploymentError, match="does not exist"):
        plan_adoption(DOMAIN, tmp_path / "missing", web=web)


def test_a_compose_file_outside_the_directory_is_refused(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore
) -> None:
    from noust.deployers.compose_adopt import plan_adoption

    with pytest.raises(DeploymentError, match="not a path inside the application"):
        plan_adoption(DOMAIN, root, web=web, compose_file="../other/compose.yml")


# -- Adopting ---------------------------------------------------------------------


def test_adopting_registers_the_stack_in_place_with_its_project_and_site(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    site_before = (web.sites_available / "proggest").read_bytes()
    tree_before = tree(root)

    adopt(root, web)

    app = store.get_app(DOMAIN)
    assert app is not None
    assert app.app_type == "docker-compose"
    assert app.layout == "inplace"
    assert app.app_path == str(root)
    assert app.compose_project == PROJECT
    assert app.site_name == "proggest"
    assert app.source == REMOTE
    assert app.branch == "main"
    # The web root of Proggest's compose file (C1): the frontend, not the API.
    assert app.port == 3001
    # The domain resolves to the operator's file, which is left as it was.
    assert web.config_path(DOMAIN) == web.sites_available / "proggest"
    assert (web.sites_available / "proggest").read_bytes() == site_before
    assert not (web.sites_available / DOMAIN).exists()
    # Nothing cloned, cleaned, pulled, recreated or started.
    assert tree(root) == tree_before
    assert docker.mutating() == []


def test_the_unit_is_created_with_the_project_and_enabled_but_not_started(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    adopt(root, web)

    unit = (units / f"{UNIT}.service").read_text()
    assert "ExecStart=/usr/bin/docker compose -p proggest up -d --remove-orphans" in unit
    assert "ExecStop=/usr/bin/docker compose -p proggest down" in unit
    assert 'Environment="COMPOSE_FILE=docker-compose.prod.yml"' in unit
    assert f"WorkingDirectory={root}" in unit
    assert ("systemctl", "enable", f"{UNIT}.service") in docker.calls
    assert not any(call[:2] == ("systemctl", "start") for call in docker.calls)
    service = store.get_service(UNIT)
    app = store.get_app(DOMAIN)
    assert service is not None and app is not None and service.app_id == app.id


def test_the_history_starts_with_an_adopted_deployment_at_the_current_commit(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    result = adopt(root, web)

    rows = store.list_deployments(DOMAIN)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == result.deployment_id
    assert row.status == "success"
    assert row.git_commit == COMMIT
    assert row.git_branch == "main"
    assert "Adopted" in Path(row.log_path).read_text()


def test_adopting_is_audited(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.core import audit

    adopt(root, web)

    events = audit.get_log().read(action="apps.adopt", limit=10)
    assert len(events) == 1
    assert events[0]["resource"] == f"app:{DOMAIN}"
    assert events[0]["details"]["project"] == PROJECT
    assert events[0]["details"]["site"] == "proggest"


def test_a_unit_that_cannot_be_created_leaves_nothing_registered(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    (units / f"{UNIT}.service").write_text("[Service]\nExecStart=/bin/true\n")

    with pytest.raises(DeploymentError, match="already"):
        adopt(root, web)

    assert store.get_app(DOMAIN) is None


def test_after_adopting_every_compose_command_addresses_the_project(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.deployers.docker_compose import stack_deployer

    adopt(root, web)
    app = store.get_app(DOMAIN)
    assert app is not None

    deployer = stack_deployer(app)
    deployer._discover_compose_file()

    assert deployer._compose("ps")[:6] == (
        ["docker", "compose", "-p", PROJECT, "-f", str(root / "docker-compose.prod.yml")]
    )


def test_the_monitor_finds_the_containers_by_the_recorded_project(
    root: Path, docker: Docker, store: NoustStore
) -> None:
    from noust.monitor.plan import PlanBuilder

    app = SimpleNamespace(domain=DOMAIN, app_path=str(root), compose_project=PROJECT)
    builder = PlanBuilder.__new__(PlanBuilder)
    builder.runner = docker

    found = builder._compose_containers(app)

    assert isinstance(found, list) and len(found) == 4
    first = next(call for call in docker.calls if call[:2] == ("docker", "ps"))
    assert f"label=com.docker.compose.project={PROJECT}" in first


def test_the_logs_command_addresses_the_recorded_project(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    from noust.cli.app import cli as root_cli

    adopt(root, web)

    result = CliRunner().invoke(root_cli, ["logs", DOMAIN, "--lines", "5"])

    assert result.exit_code == 0, result.output
    logs = next(call for call in docker.calls if "logs" in call and call[:1] == ("docker",))
    assert logs[:4] == ("docker", "compose", "-p", PROJECT)
    assert "--tail" in logs


# -- The unit template -------------------------------------------------------------


def test_the_unit_template_passes_no_project_unless_given() -> None:
    from jinja2 import Environment, PackageLoader

    jinja = Environment(  # noqa: S701 - unit files are not markup
        loader=PackageLoader("noust", "templates/systemd"), trim_blocks=True, lstrip_blocks=True
    )
    template = jinja.get_template("docker-compose.service.j2")
    base = {"name": UNIT, "description": "d", "working_directory": "/opt/p", "environment": {}}

    plain = template.render(**base)
    pinned = template.render(**base, compose_project=PROJECT)

    assert "ExecStart=/usr/bin/docker compose up -d --remove-orphans" in plain
    assert " -p " not in plain
    for line in (
        "ExecStartPre=/usr/bin/docker compose -p proggest pull --ignore-pull-failures",
        "ExecStart=/usr/bin/docker compose -p proggest up -d --remove-orphans",
        "ExecStop=/usr/bin/docker compose -p proggest down",
        "ExecReload=/usr/bin/docker compose -p proggest up -d --remove-orphans --build",
    ):
        assert line in pinned


# -- The CLI and the API -------------------------------------------------------------


def test_the_command_shows_the_plan_and_adopts_when_confirmed(
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.cli.app import cli as root_cli
    from noust.deployers import compose_adopt

    monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)

    result = CliRunner().invoke(
        root_cli, ["app", "adopt", DOMAIN, "--path", str(root)], input="y\n"
    )

    assert result.exit_code == 0, result.output
    assert PROJECT in result.output and "proggest" in result.output
    assert store.get_app(DOMAIN) is not None


def test_the_command_adopts_nothing_when_not_confirmed(
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.cli.app import cli as root_cli
    from noust.deployers import compose_adopt

    monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)

    result = CliRunner().invoke(
        root_cli, ["app", "adopt", DOMAIN, "--path", str(root)], input="n\n"
    )

    assert result.exit_code != 0
    assert store.get_app(DOMAIN) is None


def test_the_command_prints_json(
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.cli.app import cli as root_cli
    from noust.deployers import compose_adopt

    monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)

    result = CliRunner().invoke(
        root_cli, ["app", "adopt", DOMAIN, "--path", str(root), "--yes", "--json"]
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output.strip().splitlines()[-1])
    assert body["adopted"] is True
    assert body["project"] == PROJECT
    assert body["site_name"] == "proggest"


def adopt_client(*, elevated: bool) -> Any:
    """A client for the adoption router alone, authenticated, elevated or not."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from noust.web.api import app_adopt
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers, require_elevated

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_adopt.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)


def test_the_route_declares_apps_manage() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_app_adopt import ROUTES

    assert ROUTES == {("POST", "/api/apps/adopt"): Permission.APPS_MANAGE}


def test_the_api_needs_sudo_mode(
    root: Path, docker: Docker, web: NginxManager, store: NoustStore, units: Path
) -> None:
    response = adopt_client(elevated=False).post(
        "/api/apps/adopt", json={"domain": DOMAIN, "path": str(root)}
    )

    assert response.status_code == 403
    assert store.get_app(DOMAIN) is None


def test_the_api_previews_then_adopts(
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.deployers import compose_adopt

    monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)
    client = adopt_client(elevated=True)

    preview = client.post(
        "/api/apps/adopt", json={"domain": DOMAIN, "path": str(root), "preview": True}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["adopted"] is False
    assert preview.json()["project"] == PROJECT
    assert store.get_app(DOMAIN) is None

    done = client.post("/api/apps/adopt", json={"domain": DOMAIN, "path": str(root)})
    assert done.status_code == 200, done.text
    assert done.json()["adopted"] is True
    assert done.json()["deployment_id"] is not None
    assert store.get_app(DOMAIN) is not None


def test_the_api_refuses_a_recreate_with_409_and_the_output(
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.deployers import compose_adopt

    monkeypatch.setattr(compose_adopt, "_default_web", lambda: web)
    docker.dry_run = RECREATES

    response = adopt_client(elevated=True).post(
        "/api/apps/adopt", json={"domain": DOMAIN, "path": str(root)}
    )

    assert response.status_code == 409
    assert "Recreated" in response.json()["output"]
    assert "accept_recreate" in response.json()["hint"]


def test_an_update_of_the_adopted_stack_works_in_its_directory_unit_and_file(
    root: Path,
    docker: Docker,
    web: NginxManager,
    store: NoustStore,
    units: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.deployers import lifecycle
    from noust.deployers.docker_compose import DockerComposeDeployer

    adopt(root, web)
    seen: dict[str, Any] = {}

    class Stop(Exception):
        """Where the test stops the update: it has seen what the deployer was given."""

    def update(self: DockerComposeDeployer, on_step: Any = None) -> Any:
        seen.update(
            app_name=self.app_name,
            app_path=self.app_path,
            compose_file=self.compose_file,
            project=self._pinned_project(),
        )
        raise Stop

    monkeypatch.setattr(DockerComposeDeployer, "update", update)

    with pytest.raises(Stop):
        lifecycle.update_app(DOMAIN)

    assert seen == {
        "app_name": UNIT,
        "app_path": root,
        "compose_file": "docker-compose.prod.yml",
        "project": PROJECT,
    }
    # Brought up to date in place: reset, never cleaned nor cloned.
    git = [call for call in docker.calls if call[:1] == ("git",)]
    assert not any("clean" in call or "clone" in call for call in git)
