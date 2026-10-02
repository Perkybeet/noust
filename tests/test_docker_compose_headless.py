# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose worker is not a web that is down (owner item 57, spec 3.2 section 8.4).

``licitaciones.picconia.com`` is a stack that publishes no port: a worker
that looks for tenders and sends mail. WASM 1.x stored the Compose default
port (3000) for it and wrote it a site, and since then ``noust diagnose``, the
console and "needs attention" called it down. Here: a headless stack is
registered without a port, every health reader judges it by its containers,
and ``noust app headless`` is how one registered wrongly is put right - only
when asked.

The fixture is the production worker's compose file, verbatim.
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

from noust.core import app_state
from noust.core.exceptions import ValidationError
from noust.core.runner import CommandResult, FakeRunner, set_runner
from noust.core.store import App, NoustStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import (
    DockerComposeDeployer,
    headless_stack_state,
    make_headless,
)
from noust.managers import diagnose
from noust.managers.nginx_manager import NginxManager
from noust.managers.webserver import NGINX_BACKEND

DOMAIN = "licitaciones.example.com"
FIXTURES = Path(__file__).parent / "fixtures" / "compose"
RUNNING = {"Service": "licitaciones-avisos", "Name": "licitaciones-avisos", "State": "running"}


class Docker(FakeRunner):
    """Docker answering ``ps`` with the containers a test sets."""

    def __init__(self) -> None:
        super().__init__()
        self.containers: list[dict[str, Any]] = [RUNNING]

    def _lookup(self, argv: Any, user: str | None = None, env: Any = None) -> CommandResult:
        result = super()._lookup(argv, user, env)
        args = result.argv
        if args[:2] == ("docker", "compose") and "ps" in args and "-a" in args:
            return replace(result, stdout="\n".join(json.dumps(c) for c in self.containers))
        if args[:2] == ("docker", "compose") and "logs" in args:
            return replace(result, stdout="licitaciones-avisos  | 12 avisos enviados\n")
        if args[:2] == ("systemctl", "show") and any("ActiveState" in a for a in args):
            return replace(result, stdout="ActiveState=active\nSubState=exited\nNRestarts=0\n")
        return result

    def probed_ports(self) -> list[tuple[str, ...]]:
        """Every command that asked which ports listen."""
        return [call for call in self.calls if call[:1] == ("ss",)]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test's directory, installed as the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker() -> Iterator[Docker]:
    """The fake docker, as the process-wide runner."""
    fake = Docker()
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def web(tmp_path: Path, docker: Docker, store: NoustStore) -> NginxManager:
    """An nginx manager writing into a temporary configuration tree."""
    return NginxManager(
        backend=replace(
            NGINX_BACKEND,
            sites_available=tmp_path / "nginx/sites-available",
            sites_enabled=tmp_path / "nginx/sites-enabled",
        )
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The worker's tree, with its production compose file."""
    path = tmp_path / "apps" / "licitaciones"
    path.mkdir(parents=True)
    shutil.copy(FIXTURES / "licitaciones.docker-compose.yml", path / "docker-compose.yml")
    return path


def registered(store: NoustStore, root: Path, *, port: int | None) -> App:
    """The worker's row, as 1.x (port 3000) or 3.2 (no port) left it."""
    return store.create_app(
        App(
            domain=DOMAIN,
            app_type="docker-compose",
            app_path=str(root),
            port=port,
            webserver="nginx",
            status="running",
        )
    )


def noust_site(web: NginxManager) -> None:
    """The proxy site 1.x wrote for the worker."""
    web.create_site(DOMAIN, template="proxy", context={"port": 3000})
    web.enable_site(DOMAIN)


# -- Deploying it -----------------------------------------------------------------


def test_the_worker_deploys_without_a_port_a_site_or_a_certificate(
    root: Path,
    store: NoustStore,
    docker: Docker,
    web: NginxManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployer = DockerComposeDeployer(runner=docker)
    deployer.configure(DOMAIN, "https://example.com/l.git", app_path=root)
    deployer.webserver_manager = lambda: web  # type: ignore[method-assign]
    deployer.deploy_target = SimpleNamespace(undo_fetch=lambda fs, log: None)  # type: ignore[assignment]
    for step in ("_fetch_source", "_create_systemd_service", "_start_and_verify"):
        monkeypatch.setattr(deployer, step, lambda: None)

    deployer.deploy()

    app = store.get_app(DOMAIN)
    assert app is not None and app.port is None
    assert not web.site_exists(DOMAIN)
    assert not [call for call in docker.calls if call[0] == "certbot"]


def test_an_update_of_a_worker_registered_with_a_port_does_not_probe_it(
    root: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to probe 3000, fail, and put the previous containers back."""
    registered(store, root, port=3000)
    probed: list[str] = []
    monkeypatch.setattr(
        docker_compose, "wait_until_healthy", lambda url, **kw: probed.append(url) or False
    )
    monkeypatch.setattr(docker_compose.time, "sleep", lambda seconds: None)
    deployer = DockerComposeDeployer(runner=docker)
    deployer.app_path, deployer.app_name, deployer.domain = root, root.name, DOMAIN

    deployer.update()

    assert probed == []
    assert store.list_deployments(DOMAIN)[0].status == "success"


# -- Judged by its containers ------------------------------------------------------


def test_a_worker_whose_containers_run_is_healthy(
    root: Path, store: NoustStore, docker: Docker
) -> None:
    app = registered(store, root, port=None)

    state = headless_stack_state(app)

    assert state is not None and state.healthy and state.recorded_port is None


@pytest.mark.parametrize(
    ("container", "said"),
    [
        ({**RUNNING, "State": "exited", "ExitCode": 1}, "exited with code 1"),
        ({**RUNNING, "State": "restarting", "ExitCode": 137}, "restarting"),
    ],
)
def test_a_worker_whose_container_fails_or_loops_is_not(
    root: Path, store: NoustStore, docker: Docker, container: dict[str, Any], said: str
) -> None:
    app = registered(store, root, port=None)
    docker.containers = [container]

    state = headless_stack_state(app)

    assert state is not None and not state.healthy and said in state.summary


def test_a_one_shot_container_that_exited_cleanly_is_fine(
    root: Path, store: NoustStore, docker: Docker
) -> None:
    app = registered(store, root, port=None)
    docker.containers = [RUNNING, {**RUNNING, "Name": "seed", "State": "exited", "ExitCode": 0}]

    state = headless_stack_state(app)

    assert state is not None and state.healthy


def test_a_web_stack_is_not_judged_as_a_worker(
    tmp_path: Path, store: NoustStore, docker: Docker
) -> None:
    root = tmp_path / "apps" / "web"
    root.mkdir(parents=True)
    (root / "docker-compose.yml").write_text("services:\n  web:\n    ports: ['8080:80']\n")

    assert headless_stack_state(registered(store, root, port=8080)) is None


def test_the_application_state_asks_the_containers_not_the_port(
    root: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `noust list`, `noust health` and the console show."""
    app = registered(store, root, port=None)
    monkeypatch.setattr(app_state, "port_answers", lambda port, **kw: pytest.fail("probed"))
    status = {"exists": True, "active": True, "active_state": "active", "sub_state": "exited"}

    state = app_state._state_from_status(app, status, probe=True)

    assert (state.label, state.healthy) == (app_state.RUNNING, True)


def test_the_application_state_flags_a_recorded_port_with_its_fix(
    root: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = registered(store, root, port=3000)
    monkeypatch.setattr(app_state, "port_answers", lambda port, **kw: pytest.fail("probed"))
    status = {"exists": True, "active": True, "active_state": "active", "sub_state": "exited"}

    state = app_state._state_from_status(app, status, probe=True)

    assert state.label == app_state.RUNNING and not state.healthy
    assert f"noust app headless {DOMAIN}" in state.detail


def test_the_application_state_names_a_failed_container(
    root: Path, store: NoustStore, docker: Docker
) -> None:
    app = registered(store, root, port=None)
    docker.containers = [{**RUNNING, "State": "exited", "ExitCode": 2}]
    status = {"exists": True, "active": True, "active_state": "active", "sub_state": "exited"}

    state = app_state._state_from_status(app, status, probe=True)

    assert state.label == app_state.FAILED and "exited with code 2" in state.detail


def test_diagnose_judges_the_worker_by_its_containers(
    root: Path, store: NoustStore, docker: Docker, unit: None
) -> None:
    registered(store, root, port=None)

    result = diagnose.diagnose(DOMAIN, runner=docker, store=store, http_get=_no_http)

    names = [check.name for check in result.checks]
    assert names[0] == "containers"
    assert not {"port", "http_direct", "http_nginx", "certificate"} & set(names)
    assert result.verdict == "healthy", [(c.name, c.status, c.summary) for c in result.checks]
    assert "12 avisos enviados" in result.checks[0].evidence
    assert not docker.probed_ports()


def test_diagnose_names_the_container_that_fails(
    root: Path, store: NoustStore, docker: Docker, unit: None
) -> None:
    registered(store, root, port=None)
    docker.containers = [{**RUNNING, "State": "restarting", "ExitCode": 1}]

    result = diagnose.diagnose(DOMAIN, runner=docker, store=store, http_get=_no_http)

    assert result.verdict == "down"
    assert result.probable_cause and "restarting" in result.probable_cause


def test_diagnose_gives_the_fix_for_a_recorded_port(
    root: Path, store: NoustStore, docker: Docker, unit: None
) -> None:
    registered(store, root, port=3000)

    result = diagnose.diagnose(DOMAIN, runner=docker, store=store, http_get=_no_http)

    assert result.verdict == "degraded"
    assert result.probable_cause and f"noust app headless {DOMAIN}" in result.probable_cause


@pytest.fixture
def unit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker's unit is on disk and Noust's."""
    from noust.managers.service_manager import ServiceManager, UnitOwnership

    def owned(self: ServiceManager, name: str, *, serving: bool = True) -> UnitOwnership:
        path = Path(f"/etc/systemd/system/{name}.service")
        return UnitOwnership(
            unit=name, path=path, fragment_path=path, exists=True, managed=True, reason=""
        )

    monkeypatch.setattr(ServiceManager, "inspect_unit", owned)


def _no_http(url: str, headers: Any) -> tuple[int | None, str | None]:
    raise AssertionError(f"a worker has nothing to ask over HTTP: {url}")


# -- noust app headless -------------------------------------------------------------


def test_headless_clears_the_port_and_keeps_the_site_unless_asked(
    root: Path, store: NoustStore, docker: Docker, web: NginxManager
) -> None:
    registered(store, root, port=3000)
    noust_site(web)

    change = make_headless(DOMAIN, remove_site=False, logger=_Log(), manager=web)

    app = store.get_app(DOMAIN)
    assert app is not None and app.port is None
    assert (change.previous_port, change.site) == (3000, "kept")
    assert web.site_exists(DOMAIN)


def test_headless_removes_the_site_noust_wrote_when_asked(
    root: Path, store: NoustStore, docker: Docker, web: NginxManager
) -> None:
    registered(store, root, port=3000)
    noust_site(web)

    change = make_headless(DOMAIN, remove_site=True, logger=_Log(), manager=web)

    assert change.site == "removed" and not web.site_exists(DOMAIN)


def test_headless_keeps_a_site_that_answers_on_other_names(
    root: Path, store: NoustStore, docker: Docker, web: NginxManager
) -> None:
    registered(store, root, port=3000)
    store.add_domain(DOMAIN, "avisos.example.com", "alias")
    noust_site(web)

    change = make_headless(DOMAIN, remove_site=True, logger=_Log(), manager=web)

    assert change.site == "kept_aliases" and web.site_exists(DOMAIN)


def test_headless_keeps_the_operator_site(
    root: Path, store: NoustStore, docker: Docker, web: NginxManager
) -> None:
    registered(store, root, port=3000)
    path = web.config_path(DOMAIN)
    path.parent.mkdir(parents=True)
    path.write_text("server { server_name licitaciones.example.com; }\n")

    change = make_headless(DOMAIN, remove_site=True, logger=_Log(), manager=web)

    assert change.site == "kept_operator" and path.exists()


def test_headless_refuses_a_stack_that_publishes_a_port(
    tmp_path: Path, store: NoustStore, docker: Docker, web: NginxManager
) -> None:
    root = tmp_path / "apps" / "web"
    root.mkdir(parents=True)
    (root / "docker-compose.yml").write_text("services:\n  web:\n    ports: ['8080:80']\n")
    registered(store, root, port=8080)

    with pytest.raises(ValidationError, match="publishes ports"):
        make_headless(DOMAIN, remove_site=True, logger=_Log(), manager=web)

    app = store.get_app(DOMAIN)
    assert app is not None and app.port == 8080


def test_the_command_passes_the_choice_and_prints_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.cli.app import cli as root_cli
    from noust.cli.commands import app_headless

    seen: dict[str, Any] = {}

    def fake(domain: str, *, remove_site: bool, logger: Any) -> Any:
        seen.update(domain=domain, remove_site=remove_site)
        return docker_compose.HeadlessChange(domain=domain, previous_port=3000, site="removed")

    monkeypatch.setattr(app_headless, "make_headless", fake)

    result = CliRunner().invoke(root_cli, ["app", "headless", DOMAIN, "--remove-site", "--json"])

    assert result.exit_code == 0, result.output
    assert seen == {"domain": DOMAIN, "remove_site": True}
    assert json.loads(result.output.strip().splitlines()[-1]) == {
        "domain": DOMAIN,
        "previous_port": 3000,
        "site": "removed",
    }


class _Log:
    """Swallows what make_headless reports."""

    def substep(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass
