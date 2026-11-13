# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cloudflare Tunnel on the central of a NAS: the connector is a container, not a unit.

A NAS has ports 80 and 443 for its own interface and often no address anybody can point a name at, so a
tunnel that dials out is the way an application there is reached by a name without opening a port. The
manager is the one a server has (the Cloudflare API, the routes, the CNAME, the records); what differs is the
engine that keeps the connector running, which here is Docker with a restart policy, so that it comes back by
itself when the NAS restarts and nothing has to type a passphrase for it.

What is pinned: the image is pinned by digest; the token reaches the container as a private file that is
mounted read-only and named by an environment variable, and is **never** an environment variable itself
(``docker inspect`` prints those) nor an argument; the connector runs as the central's own user with nothing
but a read-only file system; an application's route goes to the port Docker published it on; what was made is
undone when the connector does not start; and the connector is Noust's in the Docker zone, so that it is not
offered for removal as something nobody owns.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.central.machine import MachineFacts
from noust.core import sealing
from noust.core.config import Config
from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.docker import connector
from noust.core.exceptions import PrerequisiteError, ServiceError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.server_text import say
from noust.core.store import App, NoustStore
from noust.core.tunnel_store import READY
from noust.managers.dns.accounts import DnsAccounts
from noust.managers.dns.records import ManagedDns
from noust.managers.tunnels import TunnelManager, secret_name, token_secret_name
from tests.support.dns_support import FakeTunnelProvider
from tests.support.docker_machine import DockerMachine

APP = "shop.example.com"
HOME = "noust-tunnel-home"


@pytest.fixture(autouse=True)
def a_container_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    # The central runs as an unprivileged user, and the connector runs as that user too.
    monkeypatch.setattr(os, "getuid", lambda: 10001)
    monkeypatch.setattr(os, "getgid", lambda: 10001)
    yield
    Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    instance.create_app(App(domain=APP, app_type="container", app_path="/srv/shop", port=8081))
    instance.create_app(App(domain="jobs.example.com", app_type="docker-compose", app_path="/j"))
    instance.add_domain(APP, "www.shop.example.com", "alias")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def provider() -> FakeTunnelProvider:
    return FakeTunnelProvider("example.com")


@pytest.fixture
def box() -> DockerMachine:
    machine = DockerMachine()
    machine.script(
        ("docker", "inspect", "--type", "container", "--format", "{{.State.Status}}"),
        stdout="running\n",
    )
    return machine


@pytest.fixture
def tunnels(
    store: NoustStore,
    secrets: SecretStore,
    provider: FakeTunnelProvider,
    box: DockerMachine,
    monkeypatch: pytest.MonkeyPatch,
) -> TunnelManager:
    DnsStore(store).save_account(DnsAccount(name="cf", kind="cloudflare", automatic=True))
    monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: provider)
    accounts = DnsAccounts(store=store, secrets=secrets)
    return TunnelManager(
        accounts=accounts,
        store=store,
        runner=box,
        secrets=secrets,
        # A container host has no web server whose site a route could reach: asking it is a bug.
        serves_tls=lambda app: pytest.fail("a container host has no site to ask"),
        dns=lambda: ManagedDns(accounts=accounts, store=store, label="srv"),
    )


def make(tunnels: TunnelManager, name: str = "home") -> Any:
    return tunnels.create(name, account="cf")


def docker_calls(box: DockerMachine, verb: str) -> list[tuple[str, ...]]:
    return [call for call in box.calls if call[:2] == ("docker", verb)]


# -- The image ---------------------------------------------------------------------------------


def test_the_connector_s_image_is_pinned_by_tag_and_digest() -> None:
    assert re.fullmatch(
        r"cloudflare/cloudflared:\d{4}\.\d+\.\d+@sha256:[0-9a-f]{64}", connector.CONNECTOR_IMAGE
    )


# -- What the machine can do -------------------------------------------------------------------


def test_a_central_that_deploys_containers_can_run_a_tunnel_with_nothing_installed(
    tunnels: TunnelManager,
) -> None:
    support = tunnels.support()

    assert support.available is True and support.engine == "docker"
    assert support.cloudflared == connector.CONNECTOR_IMAGE
    assert support.accounts == ("cf",)
    assert support.to_dict()["engine"] == "docker"


def test_a_server_still_says_it_is_systemd_and_where_the_program_is(
    tunnels: TunnelManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "server")
    monkeypatch.delenv("NOUST_DEPLOY_HERE")
    Config.reset_instance()
    tunnels._which = lambda program: "/usr/bin/cloudflared"

    support = tunnels.support()

    assert (support.engine, support.cloudflared) == ("systemd", "/usr/bin/cloudflared")


def test_without_docker_it_says_what_the_doctor_says(
    tunnels: TunnelManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The suite gives every test a machine that has everything; this one has no Docker.
    nothing = MachineFacts(
        systemd=False,
        web_server=False,
        docker=False,
        root=False,
        journal=False,
        docker_problem=say("docker.engine.missing", command="noust setup init"),
    )
    monkeypatch.setattr("noust.central.machine.read_machine_facts", lambda *a, **k: nothing)

    support = tunnels.support()

    assert support.available is False
    assert support.coded is not None and support.coded.code.startswith("docker.")
    with pytest.raises(PrerequisiteError):
        make(tunnels)


def test_a_sealed_central_cannot_feed_a_connector_that_starts_by_itself(
    tunnels: TunnelManager, secrets: SecretStore
) -> None:
    sealing.header_path(secrets.root).parent.mkdir(parents=True, exist_ok=True)
    sealing.header_path(secrets.root).write_text("sealed")

    support = tunnels.support()

    assert support.available is False and support.sealed is True
    assert support.coded is not None and support.coded.code == "tunnels.store_sealed"
    with pytest.raises(PrerequisiteError, match="sealed"):
        make(tunnels)


# -- Creating ----------------------------------------------------------------------------------


def test_creating_runs_a_container_that_docker_keeps_alive_and_writes_no_unit(
    tunnels: TunnelManager, box: DockerMachine, secrets: SecretStore, tmp_path: Path
) -> None:
    result = make(tunnels)

    assert (result.tunnel.state, result.tunnel.unit) == (READY, HOME)
    run = docker_calls(box, "run")
    assert len(run) == 1
    argv = run[0]
    token_file = secrets.path(token_secret_name("home"))
    assert argv[argv.index("--name") + 1] == HOME
    assert argv[argv.index("--restart") + 1] == "unless-stopped"
    assert argv[argv.index("--user") + 1] == "10001:10001"
    assert "--read-only" in argv and "--detach" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges:true" in argv
    assert argv[argv.index("--add-host") + 1] == "host.docker.internal:host-gateway"
    assert argv[argv.index("--label") + 1] == "io.noust.tunnel=home"
    assert argv[argv.index("--mount") + 1] == (
        f"type=bind,source={token_file},target=/run/secrets/tunnel_token,readonly"
    )
    assert argv[argv.index("--env") + 1] == "TUNNEL_TOKEN_FILE=/run/secrets/tunnel_token"
    image = argv.index(connector.CONNECTOR_IMAGE)
    assert argv[image:] == (connector.CONNECTOR_IMAGE, "tunnel", "--no-autoupdate", "run")
    assert not any(call[0] in ("systemctl", "journalctl") for call in box.calls)
    assert not list(tmp_path.rglob("*.service"))


def test_the_token_is_a_private_file_and_never_an_argument_an_environment_or_an_input(
    tunnels: TunnelManager,
    provider: FakeTunnelProvider,
    box: DockerMachine,
    secrets: SecretStore,
) -> None:
    make(tunnels)

    token_file = secrets.path(token_secret_name("home"))
    assert token_file.read_text().strip() == provider.token
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert not any(provider.token in word for call in box.calls for word in call)
    assert not any(provider.token in str(value) for value in box.envs if value)
    assert not any(provider.token in str(value) for value in box.inputs if value)
    # An environment variable lands in the container's configuration, which `docker inspect` prints.
    run = docker_calls(box, "run")[0]
    assert not any(word.startswith("TUNNEL_TOKEN=") for word in run)
    assert all(word != "-e" for word in run) and all("TUNNEL_TOKEN=" not in word for word in run)


def test_the_unit_s_secret_is_not_written_where_there_is_no_unit(
    tunnels: TunnelManager, secrets: SecretStore
) -> None:
    make(tunnels)

    assert secrets.read(secret_name("home")) is None


def test_a_connector_that_does_not_start_undoes_everything_and_dockers_words_are_the_error(
    tunnels: TunnelManager,
    provider: FakeTunnelProvider,
    box: DockerMachine,
    secrets: SecretStore,
) -> None:
    box.script(
        ("docker", "run"),
        stderr="docker: Error response from daemon: pull access denied for cloudflare/cloudflared",
        exit_code=125,
    )

    with pytest.raises(ServiceError, match="did not start") as caught:
        make(tunnels)

    assert "pull access denied" in caught.value.details
    assert provider.tunnels == {} and tunnels.records.all() == []
    assert secrets.read(token_secret_name("home")) is None
    assert any(call[:3] == ("docker", "rm", "--force") for call in box.calls)


def test_a_connector_left_by_an_interrupted_creation_is_replaced(
    tunnels: TunnelManager, box: DockerMachine
) -> None:
    box.box(HOME, image="cloudflare/cloudflared:old", state="exited")

    make(tunnels)

    assert f"container:{HOME}" in box.removed
    assert box.boxes[HOME].image == connector.CONNECTOR_IMAGE
    assert box.boxes[HOME].policy == "unless-stopped"


def test_a_token_that_is_not_one_is_never_written(
    tunnels: TunnelManager, provider: FakeTunnelProvider, secrets: SecretStore, box: DockerMachine
) -> None:
    provider.token = "abc\n--privileged"

    from noust.managers.dns.provider import DnsProviderError

    with pytest.raises(DnsProviderError, match="not one"):
        make(tunnels)

    assert secrets.read(token_secret_name("home")) is None
    assert docker_calls(box, "run") == []


# -- What state it is in -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("docker_says", "said"),
    [
        ("running", "active"),
        ("restarting", "activating"),
        ("created", "activating"),
        ("exited", "inactive"),
        ("paused", "inactive"),
        ("dead", "inactive"),
    ],
)
def test_it_is_active_when_the_container_runs(
    tunnels: TunnelManager, box: DockerMachine, docker_says: str, said: str
) -> None:
    tunnel = make(tunnels).tunnel
    box.script(
        ("docker", "inspect", "--type", "container", "--format", "{{.State.Status}}"),
        stdout=f"{docker_says}\n",
    )

    assert tunnels.active(tunnel) == said


def test_a_connector_that_is_not_there_is_inactive(
    tunnels: TunnelManager, box: DockerMachine
) -> None:
    tunnel = make(tunnels).tunnel
    box.script(
        ("docker", "inspect", "--type", "container", "--format", "{{.State.Status}}"),
        stderr="Error: No such container: noust-tunnel-home",
        exit_code=1,
    )

    assert tunnels.active(tunnel) == "inactive"


# -- Routes ------------------------------------------------------------------------------------


def test_an_application_s_route_goes_to_the_port_docker_published_it_on(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)

    routed = tunnels.add_route("home", APP, app_domain=APP, dns=False)

    assert routed.route.service == "http://host.docker.internal:8081"
    rule = provider.ingress["tun-1"][0]
    assert rule["hostname"] == APP
    assert rule["service"] == "http://host.docker.internal:8081"
    assert rule["originRequest"] == {"httpHostHeader": APP}


def test_an_application_that_serves_no_web_has_no_route_to_make(tunnels: TunnelManager) -> None:
    make(tunnels)

    with pytest.raises(ValidationError, match="serves no web"):
        tunnels.add_route("home", "jobs.example.com", app_domain="jobs.example.com", dns=False)


def test_a_service_given_by_hand_is_taken_as_it_is(tunnels: TunnelManager) -> None:
    make(tunnels)

    routed = tunnels.add_route(
        "home", "grafana.example.com", service="http://host.docker.internal:3000", dns=False
    )

    assert routed.route.service == "http://host.docker.internal:3000"


# -- Taking it away ----------------------------------------------------------------------------


def test_deleting_removes_the_container_the_token_and_the_tunnel(
    tunnels: TunnelManager,
    provider: FakeTunnelProvider,
    box: DockerMachine,
    secrets: SecretStore,
) -> None:
    make(tunnels)
    box.box(HOME, image=connector.CONNECTOR_IMAGE)

    tunnels.delete("home")

    assert HOME not in box.boxes and provider.tunnels == {}
    assert secrets.read(token_secret_name("home")) is None
    assert tunnels.records.all() == []
    assert not any(call[0] == "systemctl" for call in box.calls)


def test_deleting_one_whose_container_is_already_gone_finishes(
    tunnels: TunnelManager, provider: FakeTunnelProvider
) -> None:
    make(tunnels)

    tunnels.delete("home")

    assert provider.tunnels == {}


def test_an_interrupted_creation_is_finished_by_repair(
    tunnels: TunnelManager, provider: FakeTunnelProvider, box: DockerMachine
) -> None:
    from noust.core.tunnel_store import CREATING, Tunnel

    tunnels.records.create(
        Tunnel(
            name="half",
            provider="cf",
            account_id="acc-1",
            tunnel_id="",
            unit="noust-tunnel-half",
            state=CREATING,
        )
    )

    assert tunnels.repair() == ["half"]
    assert tunnels.records.all() == []
    assert not any(call[0] == "systemctl" for call in box.calls)


# -- Whose it is in the Docker zone ----------------------------------------------------------------


def test_the_connector_is_noust_s_and_is_not_offered_for_removal_as_nobody_s(
    tunnels: TunnelManager, store: NoustStore, box: DockerMachine
) -> None:
    from noust.core.docker.gate import DockerGate
    from noust.core.docker.models import OwnerKind
    from noust.core.teams.scope import Scope

    make(tunnels)
    box.box(HOME, image=connector.CONNECTOR_IMAGE, policy="unless-stopped")
    box.box("somebody-elses-tunnel", image=connector.CONNECTOR_IMAGE, policy="always")
    gate = DockerGate(box, store=store, scope=Scope.everything(), disk=lambda path: None)

    mine = gate.container(HOME)
    theirs = gate.container("somebody-elses-tunnel")

    assert (mine.owner.kind, mine.owner.subject) == (OwnerKind.NOUST, "tunnel:home")
    assert theirs.owner.kind is OwnerKind.NOBODY
    from noust.core.docker.errors import DockerOwnedError

    with pytest.raises(DockerOwnedError) as refused:
        gate.remove_container(HOME)
    assert "tunnel" in refused.value.message and "home" in refused.value.message
    assert "noust tunnel" in (refused.value.details or "")
    assert HOME in box.boxes


def test_a_claim_on_a_container_that_is_not_a_registered_tunnel_gives_nothing(
    tunnels: TunnelManager, store: NoustStore, box: DockerMachine
) -> None:
    from noust.core.docker.gate import DockerGate
    from noust.core.docker.models import OwnerKind
    from noust.core.teams.scope import Scope

    box.box("noust-tunnel-ghost", image="alpine", policy="no")
    gate = DockerGate(box, store=store, scope=Scope.everything(), disk=lambda path: None)

    assert gate.container("noust-tunnel-ghost").owner.kind is OwnerKind.NOBODY


# -- The installer has nothing to install here ---------------------------------------------------


def test_there_is_nothing_to_install_where_docker_runs_the_connector(box: DockerMachine) -> None:
    from noust.managers.cloudflared_install import CloudflaredInstaller

    with pytest.raises(PrerequisiteError) as refused:
        CloudflaredInstaller(runner=box).install()

    assert refused.value.coded is not None
    assert refused.value.coded.code == "tunnels.nothing_to_install"
    assert not any(call[0] in ("apt-get", "dnf", "zypper", "curl") for call in box.calls)
