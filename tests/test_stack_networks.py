# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker networks of a Compose stack, read and never changed.

Docker is a fake runner that answers ``docker ps`` and ``docker network`` with the documents the
real one prints, so what is pinned is the reading: which networks are the stack's (the ones its
containers are on and the ones its project made), who else is on each, what is worth saying about
them, and that nothing but a look is ever asked of Docker.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner, is_read_only
from noust.core.store import App
from noust.managers.stack_networks import stack_networks

APP_PATH = "/var/www/apps/shop-example-com"
OWN_WEB = "a" * 64
OWN_DB = "b" * 64
OTHER = "c" * 64


def container(identifier: str, name: str, project: str = "shop-example-com") -> str:
    return "\t".join(
        (
            identifier,
            name,
            "running",
            project,
            f"{APP_PATH}/docker-compose.yml",
            APP_PATH,
        )
    )


def network(
    name: str,
    *members: tuple[str, str, str],
    project: str = "",
    driver: str = "bridge",
    internal: bool = False,
    subnet: str = "172.18.0.0/16",
) -> dict[str, Any]:
    labels = {"com.docker.compose.project": project, "com.docker.compose.network": "default"}
    return {
        "Name": name,
        "Id": ("f" * 64),
        "Driver": driver,
        "Scope": "local",
        "EnableIPv6": False,
        "Internal": internal,
        "IPAM": {"Config": [{"Subnet": subnet}]},
        "Labels": labels if project else {},
        "Containers": {
            identifier: {"Name": cname, "IPv4Address": address}
            for identifier, cname, address in members
        },
    }


def docker(runner: FakeRunner, containers: list[str], networks: list[dict[str, Any]]) -> FakeRunner:
    runner.script(["docker", "ps"], stdout="\n".join(containers) + "\n")
    runner.script(["docker", "network", "ls"], stdout="\n".join("f" * 64 for _ in networks) + "\n")
    runner.script(["docker", "network", "inspect"], stdout=json.dumps(networks))
    return runner


@pytest.fixture
def app() -> App:
    return App(domain="shop.example.com", app_type="docker-compose", app_path=APP_PATH)


STACK = [container(OWN_WEB, "shop-web-1"), container(OWN_DB, "shop-db-1")]
OWN = network(
    "shop-example-com_default",
    (OWN_WEB, "shop-web-1", "172.18.0.2/16"),
    (OWN_DB, "shop-db-1", "172.18.0.3/16"),
    project="shop-example-com",
)


def test_a_stack_on_its_own_network_alone_is_isolated(runner: FakeRunner, app: App) -> None:
    docker(runner, STACK, [OWN, network("somebody_else_default", project="elsewhere")])

    answer = stack_networks(app, runner=runner)

    assert answer.applicable and answer.isolated
    assert answer.containers == ("shop-db-1", "shop-web-1")
    (own,) = answer.networks
    assert own.name == "shop-example-com_default" and own.logical == "default"
    assert own.subnets == ("172.18.0.0/16",) and not own.shared and own.findings == ()
    assert [(m.name, m.address, m.own) for m in own.members] == [
        ("shop-db-1", "172.18.0.3", True),
        ("shop-web-1", "172.18.0.2", True),
    ]


def test_a_network_somebody_elses_container_is_on_is_shared_and_says_who(
    runner: FakeRunner, app: App
) -> None:
    proxy = network(
        "proxy",
        (OWN_WEB, "shop-web-1", "172.19.0.5/16"),
        (OTHER, "traefik", "172.19.0.2/16"),
        subnet="172.19.0.0/16",
    )
    docker(runner, STACK, [OWN, proxy])

    answer = stack_networks(app, runner=runner)

    assert not answer.isolated
    shared = next(n for n in answer.networks if n.name == "proxy")
    assert shared.shared and [m.name for m in shared.members if not m.own] == ["traefik"]
    assert any("traefik is on it too" in finding for finding in shared.findings)
    assert any("Compose did not make it" in finding for finding in shared.findings)


def test_a_network_another_stack_made_is_named_as_its(runner: FakeRunner, app: App) -> None:
    other = network(
        "blog_default",
        (OWN_WEB, "shop-web-1", "172.20.0.5/16"),
        (OTHER, "blog-web-1", "172.20.0.2/16"),
        project="blog",
    )
    docker(runner, STACK, [OWN, other])

    answer = stack_networks(app, runner=runner)

    found = next(n for n in answer.networks if n.name == "blog_default")
    assert found.owner == "blog"
    assert any("belongs to the stack blog" in finding for finding in found.findings)


def test_dockers_default_bridge_and_the_hosts_network_are_called_what_they_are(
    runner: FakeRunner, app: App
) -> None:
    bridge = network("bridge", (OWN_WEB, "shop-web-1", "172.17.0.2/16"), subnet="172.17.0.0/16")
    host = network("host", (OWN_DB, "shop-db-1", ""), driver="host")
    docker(runner, STACK, [bridge, host])

    answer = stack_networks(app, runner=runner)

    by_name = {n.name: n for n in answer.networks}
    assert any("default bridge" in f for f in by_name["bridge"].findings)
    assert any("host's own network" in f for f in by_name["host"].findings)
    assert not answer.isolated


def test_an_internal_network_says_it_cannot_reach_the_outside(runner: FakeRunner, app: App) -> None:
    private = network(
        "shop-example-com_backend",
        (OWN_DB, "shop-db-1", "172.21.0.2/16"),
        project="shop-example-com",
        internal=True,
    )
    docker(runner, STACK, [private])

    (found,) = stack_networks(app, runner=runner).networks

    assert found.internal and any("cannot reach the outside" in f for f in found.findings)


def test_a_network_the_stack_made_is_shown_while_its_containers_are_down(
    runner: FakeRunner, app: App
) -> None:
    docker(runner, [], [network("shop-example-com_default", project="shop-example-com")])

    answer = stack_networks(app, runner=runner)

    assert answer.containers == () and [n.name for n in answer.networks] == [
        "shop-example-com_default"
    ]
    assert answer.isolated


def test_an_application_that_does_not_run_in_docker_has_no_network_and_docker_is_not_asked(
    runner: FakeRunner,
) -> None:
    answer = stack_networks(App(domain="x.example.com", app_type="nodejs"), runner=runner)

    assert not answer.applicable and "does not run in Docker" in answer.reason
    assert runner.calls == []


def test_docker_that_cannot_be_asked_is_an_error_with_its_words(
    runner: FakeRunner, app: App
) -> None:
    runner.script(["docker", "ps"], stdout="")
    runner.script(
        ["docker", "network", "ls"], exit_code=1, stderr="Cannot connect to the Docker daemon"
    )

    with pytest.raises(DeploymentError) as raised:
        stack_networks(app, runner=runner)

    assert "Cannot connect" in (raised.value.output or "")


def test_only_a_look_is_ever_asked_of_docker(runner: FakeRunner, app: App) -> None:
    docker(runner, STACK, [OWN])

    stack_networks(app, runner=runner)

    for call in runner.calls:
        assert call[:2] in (("docker", "ps"), ("docker", "network"))
        assert call[2] in ("ls", "inspect") or call[:2] == ("docker", "ps")
        assert not set(call) & {"rm", "create", "connect", "disconnect", "prune"}


def test_the_reads_are_declared_so_a_rehearsal_may_run_them() -> None:
    assert is_read_only(["docker", "network", "ls", "-q", "--no-trunc"]) is True
    assert is_read_only(["docker", "network", "inspect", "abc"]) is True
    assert is_read_only(["docker", "network", "rm", "abc"]) is False
    assert is_read_only(["docker", "network", "create", "x"]) is False
    assert is_read_only(["docker", "network", "prune", "-f"]) is False


def test_the_command_prints_the_networks_as_json(
    runner: FakeRunner, app: App, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from noust.core import store as store_module

    monkeypatch.setattr(
        store_module, "get_store", lambda: type("S", (), {"get_app": lambda self, d: app})()
    )
    import noust.cli.commands.app_networks as command

    monkeypatch.setattr(command, "get_store", store_module.get_store)
    docker(runner, STACK, [OWN])

    result = CliRunner().invoke(root_cli, ["app", "networks", "shop.example.com", "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["isolated"] is True and body["networks"][0]["name"] == "shop-example-com_default"
