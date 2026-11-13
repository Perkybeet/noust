# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The table of functions, walked key by key for the central of a NAS.

A function a container host offers has to work there from end to end, and one it does not offer must not be
offered, in words. The table (:mod:`noust.central.features`) says which is which; this module holds it to
that for the machine the owner has: a hub in a container that was told to deploy containers on its machine's
Docker (``central.deploy_here: containers``), whose image has the Docker client, git, openssl and ssh and has no
systemd, no nginx, no certbot, no journal and no root.

Two things are held. **The map is closed and said**: every key of the table has an entry here, available or
with the code of the sentence that says why not, and what the guard refuses is refused in those words. **What
is available works**: each available key has a walk against the simulated machine, and a test fails for an
available key nobody walked. The walks are what found the defects of the first version of this machine: a
readiness check that refused every deployment because the image has no web server, and recipes that
published their port on loopback, where nothing on a NAS would ever reach it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust import central
from noust.central import features
from noust.central.machine import MachineFacts
from noust.core.config import Config
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from tests.support.docker_machine import DockerMachine
from tests.support.volume_backups import (  # noqa: F401
    _reset_store,
    app,
    app_dir,
    audited,
    manager,
    runner,
    services,
)

# The fixtures of the volume backups are used by name; a test that asks for one looks like a redefinition.
# ruff: noqa: F811

#: What the machine of the owner's NAS has: Docker, and none of what the others of the table ask for.
NAS_FACTS = MachineFacts(systemd=False, web_server=False, docker=True, root=False, journal=False)

#: Every key, and what a container host says of it: ``None`` is available, anything else is the code of the
#: sentence that says why not. A new key fails ``test_the_map_is_closed`` until somebody rules on it here.
EXPECTED: dict[str, str | None] = {
    # -- Applications: what only Docker is needed for is offered.
    "applications": None,
    "native_deploy": "features.containers_only",
    "container_deploy": None,
    "releases": None,
    "rollbacks": None,
    "preview_environments": "features.containers_only",
    "importing_applications": "features.containers_only",
    "recipes": None,
    "application_environments": None,
    "application_diagnosis": "features.containers_only",
    # -- What watches and sets up the server.
    "process_monitor": "features.containers_only",
    "server_health": "features.containers_only",
    "server_setup": "features.containers_only",
    # -- Names, sites and certificates: there is no web server of Noust's.
    "domains": "features.containers_only",
    "sites": "features.containers_only",
    "certificates": "features.containers_only",
    "console_exposure": "features.containers_only",
    "status_page": "features.containers_only",
    "published_ports": "features.containers_only",
    "tunnels": None,
    "noust_dns": "features.containers_only",
    "dns_records": None,
    "dns_accounts": None,
    # -- Databases.
    "databases": "features.containers_only",
    "database_instances": None,
    "public_database_access": "features.containers_only",
    "database_backups": "features.containers_only",
    "point_in_time_recovery": "features.containers_only",
    "application_backups": None,
    "volume_backups": None,
    "restoring_a_server": "features.containers_only",
    "moving_a_server": "features.containers_only",
    "checking_a_moved_server": "features.containers_only",
    # -- What keeps running on the machine.
    "scheduled_jobs": "features.containers_only",
    "services": "features.containers_only",
    "software": "features.containers_only",
    "log_destinations": "features.containers_only",
    # -- The machine itself: a container has none of it.
    "system_clock": "features.needs_systemd",
    "power": "features.needs_systemd",
    "system_logs": "features.needs_journal",
    "root_shell": "features.needs_root",
    "server_security": "features.needs_systemd",
    "firewall": "features.needs_systemd",
    # -- The Docker zone: all it asks is Docker.
    "docker_zone": None,
}

#: Which walk proves each available key, by the name of the test: registered by :func:`walks`.
WALKED: dict[str, list[str]] = {}


def walks(*keys: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Say which functions of the table a test walks from end to end.

    Args:
        *keys: The keys of the table.

    Returns:
        The decorator, which records the test under each key.
    """

    def register(test: Callable[..., Any]) -> Callable[..., Any]:
        for key in keys:
            WALKED.setdefault(key, []).append(test.__name__)
        return test

    return register


@pytest.fixture(autouse=True)
def a_container_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    yield
    set_runner(None)
    Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def nas() -> DockerMachine:
    """The machine of the owner's NAS, as the simulated Docker it runs on."""
    machine = DockerMachine().only_has("git", "openssl", "ssh", "tar", "gzip")
    set_runner(machine)
    return machine


def published() -> dict[str, dict[str, Any]]:
    return features.published(
        role="hub", facts=NAS_FACTS, deploy_here=central.DEPLOY_HERE_MODES[-1]
    )


# -- The map ---------------------------------------------------------------------------------------


def test_the_map_is_closed() -> None:
    assert set(EXPECTED) == set(features.FEATURES)


def test_every_function_says_what_it_does_here_and_why_not_in_a_sentence() -> None:
    said = published()

    for key, code in EXPECTED.items():
        answer = said[key]
        if code is None:
            assert answer["available"] is True and answer["reason"] is None, key
        else:
            assert answer["available"] is False, key
            assert answer["reason"]["code"] == code, key
            assert answer["reason"]["text"], key


def test_what_is_not_offered_is_refused_by_the_commands_in_the_same_words() -> None:
    said = published()

    for key, code in EXPECTED.items():
        item = features.feature(key)
        if code is None or not item.guarded:
            continue
        with pytest.raises(central.RoleError) as refused:
            central.require_server_role(key)
        assert refused.value.message == said[key]["reason"]["text"], key


def test_what_is_offered_is_not_refused_by_the_guard() -> None:
    for key, code in EXPECTED.items():
        if code is None and features.feature(key).guarded:
            central.require_server_role(key)


# -- The walks ---------------------------------------------------------------------------------------

#: The programs the image of the central has (packaging/container/Dockerfile): Docker's client and Compose,
#: git, openssl and ssh, what archives need and the coreutils. Nothing else: a call to curl, nginx, systemctl,
#: certbot or journalctl is a call to what is not there.
IMAGE_PROGRAMS = {
    "docker",
    "git",
    "openssl",
    "ssh",
    "tar",
    "gzip",
    # Coreutils, which every image has.
    "chmod",
    "chown",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "ln",
    "stat",
}


def only_the_image_s_programs(runner: Any) -> list[str]:
    """
    Args:
        runner: A runner that recorded what it was asked to run.

    Returns:
        The programs it was asked for that the image of the central does not have.
    """
    return sorted({call[0] for call in runner.calls} - IMAGE_PROGRAMS)


@pytest.fixture
def app_world(tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A Docker that remembers its images, on a machine that has what the image has and no more."""
    from noust.deployers import compose_supervisor, docker_compose, lifecycle
    from tests.support.container_support import install_fakes

    monkeypatch.setattr(Config, "apps_directory", property(lambda _self: tmp_path / "apps"))
    monkeypatch.setattr(lifecycle, "get_store", lambda: store)
    monkeypatch.setattr(compose_supervisor, "get_store", lambda: store)
    docker = next(install_fakes(monkeypatch, store))
    docker.only_knows(*IMAGE_PROGRAMS)
    monkeypatch.setattr(docker_compose, "wait_until_healthy", lambda url, **kw: docker.healthy)
    monkeypatch.setattr(compose_supervisor.reach, "own_container_id", lambda: "noustid")
    monkeypatch.setattr(compose_supervisor.reach, "service_address", lambda *a, **k: "172.18.0.3")
    monkeypatch.setattr(compose_supervisor.reach, "container_port", lambda *a, **k: 8080)
    docker.serving = "ghcr.io/owner/app:1.4"
    yield docker
    set_runner(None)


@walks("applications", "container_deploy", "releases", "rollbacks")
def test_a_container_application_is_deployed_updated_gone_back_stopped_started_and_deleted(
    app_world: Any, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import yaml

    from noust.core.container_store import ContainerSettings
    from noust.deployers import lifecycle
    from noust.deployers.container import ContainerDeployer
    from noust.deployers.helpers.container_compose import COMPOSE_FILE
    from noust.managers.service_manager import ServiceManager
    from noust.managers.supervision import service_control
    from tests.support.container_support import DOMAIN, FakeServices, root_of

    monkeypatch.setattr(
        lifecycle,
        "RollbackManager",
        lambda verbose=False: SimpleNamespace(create_pre_deploy_backup=lambda **kw: None),
    )
    monkeypatch.setattr(lifecycle, "SourceManager", lambda verbose=False: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "ServiceManager", FakeServices)
    docker = app_world
    settings = ContainerSettings(method="image", image="ghcr.io/owner/app:1.4", container_port=8080)
    root = root_of(tmp_path)

    def running() -> str:
        return yaml.safe_load((root / COMPOSE_FILE).read_text())["services"]["app"]["image"]

    deployer = ContainerDeployer(runner=docker)
    deployer.configure(DOMAIN, settings.image or "", port=None, app_path=root, container=settings)
    deployer.deploy()
    docker.serving = running()
    row = store.get_app(DOMAIN)
    assert row is not None and (row.webserver, row.ssl_enabled) == ("", False)

    lifecycle.update_app(DOMAIN)
    releases = sorted(r.id for r in lifecycle.list_releases(DOMAIN))
    assert len(releases) == 2
    docker.serving = running()
    lifecycle.activate_release(DOMAIN, releases[0])

    # What stops and starts it is Docker's Compose, through the one seam every caller uses.
    manager = service_control(ServiceManager)
    name = "shop-example-com"
    assert manager.stop(name) is True
    manager.start(name)
    assert manager.get_status(name)["exists"] is True

    outcome = lifecycle.delete_app(DOMAIN)
    assert outcome.containers_stopped
    assert store.get_app(DOMAIN) is None
    assert only_the_image_s_programs(docker) == [], "asked for a program the image does not have"


@walks("application_environments")
def test_a_variable_is_set_and_the_application_restarted_with_it(
    app_world: Any, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from noust.cli.app import cli as root_cli
    from noust.core.appdir.layout import env_file_for
    from noust.core.container_store import ContainerSettings
    from noust.deployers import lifecycle
    from noust.deployers.container import ContainerDeployer
    from tests.support.container_support import DOMAIN, FakeServices, root_of

    monkeypatch.setattr(lifecycle, "ServiceManager", FakeServices)
    monkeypatch.setattr(lifecycle, "SourceManager", lambda verbose=False: SimpleNamespace())
    settings = ContainerSettings(method="image", image="ghcr.io/owner/app:1.4", container_port=8080)
    deployer = ContainerDeployer(runner=app_world)
    deployer.configure(
        DOMAIN, settings.image or "", port=None, app_path=root_of(tmp_path), container=settings
    )
    deployer.deploy()
    app = store.get_app(DOMAIN)
    assert app is not None

    changed = CliRunner().invoke(root_cli, ["env", "set", DOMAIN, "GREETING=hello"])
    restarted = CliRunner().invoke(root_cli, ["restart", DOMAIN])

    assert changed.exit_code == 0, changed.output
    assert "GREETING=hello" in env_file_for(app).read_text()
    assert restarted.exit_code == 0, restarted.output
    assert any("--force-recreate" in call for call in app_world.compose_calls())
    assert only_the_image_s_programs(app_world) == [], "asked for a program the image does not have"


@walks("docker_zone")
def test_the_docker_zone_lists_stops_starts_reads_and_opens_a_console_in_what_the_nas_runs(
    store: NoustStore, nas: DockerMachine
) -> None:
    from noust.core.docker.gate import DockerGate
    from noust.core.teams.scope import Scope

    nas.box(
        "media-web-1",
        project="media",
        working_dir="/volume1/docker/media",
        policy="unless-stopped",
        log="listening on 8096\n",
    )
    gate = DockerGate(nas, store=store, scope=Scope.everything(), disk=lambda path: None)

    assert [project.name for project in gate.projects().items] == ["media"]
    gate.stop_stack("media")
    assert nas.boxes["media-web-1"].state == "exited"
    gate.start_stack("media")
    assert nas.boxes["media-web-1"].state == "running"
    assert "listening on 8096" in gate.container_logs("media-web-1")
    box = gate.container_for_console("media-web-1")
    assert gate.exec_argv(box.id, "sh")[:2] == ["docker", "exec"]
    assert gate.summary() is not None
    assert only_the_image_s_programs(nas) == [], "asked for a program the image does not have"


@walks("dns_accounts", "dns_records")
def test_a_dns_account_is_listed_through_the_command_a_tunnel_names_are_made_with(
    store: NoustStore, nas: DockerMachine
) -> None:
    from noust.cli.app import cli as root_cli

    accounts = CliRunner().invoke(root_cli, ["dns", "provider", "list"])
    records = CliRunner().invoke(root_cli, ["dns", "records", "--help"])

    assert accounts.exit_code == 0, accounts.output
    assert records.exit_code == 0, records.output
    assert "not available here" not in accounts.output + records.output


@walks("tunnels")
def test_a_tunnel_can_be_made_here_with_nothing_installed(
    store: NoustStore, nas: DockerMachine
) -> None:
    from noust.managers.tunnels import TunnelManager

    support = TunnelManager(store=store, runner=nas).support()

    # The only thing missing is a Cloudflare account, which a person adds; Docker is there and nothing is
    # to be installed (the whole creation is in tests/test_tunnels_container.py).
    assert support.engine == "docker" and support.cloudflared is not None
    assert support.coded is not None and support.coded.code == "tunnels.no_account"


@walks("application_backups")
def test_an_application_is_backed_up_and_put_back_with_docker_stopping_and_starting_it(
    app_world: Any, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from noust.core.container_store import ContainerSettings
    from noust.deployers import lifecycle
    from noust.deployers.container import ContainerDeployer
    from noust.managers.backup_manager import BackupManager
    from tests.support.container_support import DOMAIN, FakeServices, root_of

    monkeypatch.setattr(lifecycle, "ServiceManager", FakeServices)
    monkeypatch.setattr(lifecycle, "SourceManager", lambda verbose=False: SimpleNamespace())
    settings = ContainerSettings(method="image", image="ghcr.io/owner/app:1.4", container_port=8080)
    deployer = ContainerDeployer(runner=app_world)
    deployer.configure(
        DOMAIN, settings.image or "", port=None, app_path=root_of(tmp_path), container=settings
    )
    deployer.deploy()
    manager = BackupManager(runner=app_world)
    monkeypatch.setattr(manager, "backup_dir", tmp_path / "backups")

    made = manager.create(DOMAIN, description="before the move")

    assert made.id and any(item.id == made.id for item in manager.list_backups(DOMAIN))
    app_world.calls.clear()
    assert manager.restore(made.id) is True
    # It was stopped and started again by Docker's Compose, the way a unit would have been.
    verbs = [call for call in app_world.compose_calls() if call and call[0] in ("stop", "up")]
    assert verbs
    assert not any(call[0] in ("systemctl", "journalctl") for call in app_world.calls)
    assert only_the_image_s_programs(app_world) == [], "asked for a program the image does not have"


@walks("volume_backups")
def test_the_data_of_an_application_is_copied_by_the_policy_the_central_runs(
    manager: Any, tmp_path: Path
) -> None:
    from tests.support.volume_backups import DOMAIN

    manager.set_policy(DOMAIN, targets=["path:uploads"], retention_count=3, retention_days=None)

    ran = manager.run_policy(DOMAIN)

    assert len(ran["backups"]) == 1
    (entry,) = manager.list_backups(DOMAIN)
    assert entry.scheduled
    assert manager.get_policy(DOMAIN).policy.last_status == "ok"


@walks("recipes")
def test_a_recipe_is_deployed_and_its_address_is_reached_from_the_network(
    app_world: Any, store: NoustStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from noust.cli.app import cli as root_cli
    from noust.deployers import lifecycle
    from noust.deployers.helpers import edge

    monkeypatch.setattr(
        lifecycle,
        "RollbackManager",
        lambda verbose=False: SimpleNamespace(create_pre_deploy_backup=lambda **kw: None),
    )
    docker = app_world

    result = CliRunner().invoke(
        root_cli, ["create", "-d", "adminer.example.com", "--recipe", "adminer", "--port", "8083"]
    )

    assert result.exit_code == 0, result.output
    row = store.get_app("adminer.example.com")
    assert row is not None and (row.webserver, row.ssl_enabled, row.port) == ("", False, 8083)
    deployed = (tmp_path / "apps" / "adminer-example-com" / "docker-compose.yml").read_text()
    # The recipe's web port is published on every address of the NAS, because nothing on it would ever
    # reach loopback; only that port.
    assert "127.0.0.1:${PORT}" not in deployed and "${PORT}:8080" in deployed
    assert edge.reached(row).kind == "port"
    assert only_the_image_s_programs(docker) == [], "asked for a program the image does not have"


@walks("database_instances")
def test_a_database_engine_in_a_container_is_made_stopped_started_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.database_instance_support import build_world, close_world

    world = build_world(tmp_path, monkeypatch)
    try:
        world.host.only_knows(*IMAGE_PROGRAMS)
        created = world.managed.create("pg16", "postgresql", version="16", memory="1g")
        assert created["name"] == "pg16"
        # Docker keeps it alive: no unit was written and none was asked for.
        assert list(world.units.iterdir()) == []
        assert "noust-db-pg16" in world.host.containers
        assert world.host.containers["noust-db-pg16"]["Config"]["Image"]

        world.managed.stop("pg16")
        assert "noust-db-pg16" not in world.host.containers
        world.managed.start("pg16")
        assert "noust-db-pg16" in world.host.containers
        world.managed.remove("pg16", confirm=True)
        assert "noust-db-pg16" not in world.host.containers
        assert not any(call[0] in ("systemctl", "journalctl") for call in world.host.calls)
        assert only_the_image_s_programs(world.host) == []
    finally:
        close_world()


# -- What the walks found, held on their own ---------------------------------------------------------


def test_the_readiness_check_does_not_ask_for_a_web_server_or_certbot_where_there_is_no_site() -> (
    None
):
    from noust.core.dependencies import check_deployment_ready
    from noust.core.runner import FakeRunner

    image = FakeRunner().only_knows(*IMAGE_PROGRAMS)

    ready, missing, warnings = check_deployment_ready(
        "docker-compose", runner=image, server_tools=False
    )
    assert (ready, missing, warnings) == (True, [], [])

    # A server asks, as it always did.
    ready, missing, warnings = check_deployment_ready("docker-compose", runner=image)
    assert ready is False and any("web server is required" in item for item in missing)
    assert any("certbot" in item for item in warnings)


def test_the_create_command_is_not_refused_for_a_web_server_the_image_does_not_have(
    app_world: Any, store: NoustStore
) -> None:
    from noust.cli.app import cli as root_cli

    result = CliRunner().invoke(
        root_cli,
        ["create", "-d", "jobs.example.com", "-s", "ghcr.io/owner/app:1.4", "--type", "container"],
    )

    assert "System is not ready for deployment" not in result.output
    assert "web server is required" not in result.output


@pytest.mark.parametrize(
    ("written", "said"),
    [
        ("- 127.0.0.1:${PORT}:8080", "- ${PORT}:8080"),
        ('- "127.0.0.1:${PORT}:80"', '- "${PORT}:80"'),
        ("- '127.0.0.1:${PORT:-8080}:8080'", "- '${PORT:-8080}:8080'"),
        # Only the web port: what else the document binds to loopback stays there.
        ("- 127.0.0.1:${DB_PORT}:5432", "- 127.0.0.1:${DB_PORT}:5432"),
        ("- 127.0.0.1:9000:9000", "- 127.0.0.1:9000:9000"),
        ("- ${PORT}:8080", "- ${PORT}:8080"),
    ],
)
def test_a_recipes_web_port_is_published_on_the_network_where_there_is_no_site(
    written: str, said: str
) -> None:
    from noust.deployers.helpers.edge import publish_web_port

    assert publish_web_port(f"ports:\n  {written}\n") == f"ports:\n  {said}\n"


def test_a_server_keeps_a_recipes_web_port_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.deployers.helpers.edge import publish_web_port

    monkeypatch.delenv("NOUST_DEPLOY_HERE")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "server")
    Config.reset_instance()
    document = "ports:\n  - 127.0.0.1:${PORT}:8080\n"

    assert publish_web_port(document) == document


def test_no_recipe_of_the_catalogue_asks_for_the_hosts_network_or_for_port_80_or_443() -> None:
    """What would be refused or taken from the NAS is said before; nothing in the catalogue asks."""
    from noust.deployers.helpers.compose_ports import parse_ports
    from noust.recipes import list_recipes

    asking = []
    for recipe in list_recipes():
        if recipe.kind != "compose" or not recipe.compose:
            continue
        if "network_mode: host" in recipe.compose or 'network_mode: "host"' in recipe.compose:
            asking.append(f"{recipe.name}: host network")
        for line in recipe.compose.splitlines():
            text = line.strip().lstrip("-").strip().strip("\"'")
            if text[:1].isdigit() or text.startswith("0.0.0.0"):  # noqa: S104 - read, never bound
                if any(m.host_port in (80, 443) for m in parse_ports(text)):
                    asking.append(f"{recipe.name}: {text}")
    assert asking == []


def test_a_stack_that_is_being_deployed_is_started_by_its_own_project_before_it_is_registered(
    app_world: Any, store: NoustStore, tmp_path: Path
) -> None:
    from noust.deployers.docker_compose import DockerComposeDeployer
    from noust.managers.service_manager import ServiceManager
    from noust.managers.supervision import service_control

    deployer = DockerComposeDeployer(runner=app_world)
    deployer.configure("fresh.example.com", "unused", app_path=tmp_path / "fresh")
    (tmp_path / "fresh").mkdir()
    (tmp_path / "fresh" / "docker-compose.yml").write_text("services:\n  web:\n    image: nginx\n")
    deployer._discover_compose_file()
    assert store.get_app("fresh.example.com") is None

    units = service_control(ServiceManager, verbose=False, runner=app_world, stack=deployer)
    units.start("fresh-example-com")

    assert any("up" in call for call in app_world.compose_calls())
    # Any other name is still looked up, and a stranger is not started.
    from noust.core.exceptions import ServiceError

    with pytest.raises(ServiceError, match="no application of that name"):
        units.start("another-example-com")


def test_every_function_a_container_host_offers_was_walked() -> None:
    offered = {key for key, code in EXPECTED.items() if code is None}

    assert offered - set(WALKED) == set(), "walk these from end to end in this module"
    assert set(WALKED) <= offered, "a walk for a key that is not offered here"
