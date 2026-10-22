# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Compose stack's ``ports:`` are checked against everything else that holds a port (checklist E-44).

Before this the only guard was Docker's own "address already in use", found when the containers
were recreated and naming nobody. The stack is read before anything is built, its host ports go to
the one registry of who holds which port, and the sentence names the holder. A stack updating
itself is not in its own way: the ports its containers hold now are its own.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import PortConflictError
from noust.core.runner import FakeRunner
from noust.core.store import App, AppType, NoustStore
from noust.deployers import docker_compose
from noust.deployers.docker_compose import DockerComposeDeployer, stack_published_ports

STACK = """\
services:
  web:
    image: example/web:1
    ports:
      - "127.0.0.1:8081:80"
  dns:
    image: example/dns:1
    ports:
      - "5353:53/udp"
"""

#: The form a recipe and examples/docker-compose write: the port the site proxies to arrives as PORT.
PORT_STACK = """\
services:
  web:
    image: example/web:1
    ports:
      - "127.0.0.1:${PORT:-3000}:80"
"""

SOURCE = "https://example.com/stack.git"

pytestmark = pytest.mark.real_listeners


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    monkeypatch.setattr(docker_compose, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def runner() -> FakeRunner:
    fake = FakeRunner()
    fake.script(("ss", "-ltnpH"), stdout="")
    fake.script(("ss", "-lunpH"), stdout="")
    return fake


def deployer_for(
    tmp_path: Path, runner: FakeRunner, *, document: str = STACK, new: bool = True
) -> DockerComposeDeployer:
    root = tmp_path / "app"
    root.mkdir(exist_ok=True)
    (root / "docker-compose.yml").write_text(document)
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure("stack.example.com", SOURCE, app_path=root)
    deployer._is_new_deployment = new
    deployer._discover_compose_file()
    deployer._parse_compose_services()
    return deployer


def test_a_free_stack_passes(tmp_path: Path, store: NoustStore, runner: FakeRunner) -> None:
    deployer_for(tmp_path, runner)._require_ports_free()


def test_a_port_an_application_holds_is_refused_naming_the_registry_not_docker(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    store.create_app(App(domain="shop.example.com", app_path="/srv/shop", port=8081))

    with pytest.raises(PortConflictError, match="8081"):
        deployer_for(tmp_path, runner)._require_ports_free()


def test_a_port_the_stack_takes_from_port_is_judged_at_the_port_noust_gives(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    # The file's own default (3000) is what nothing sets; Noust sets PORT to the application's
    # port before the stack starts, so another application on 3000 is not in its way.
    store.create_app(App(domain="other.example.com", app_path="/srv/other", port=3000))
    deployer = deployer_for(tmp_path, runner, document=PORT_STACK)
    deployer.port = 3031

    deployer._require_ports_free()


def test_the_port_noust_gives_a_stack_is_still_checked(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    store.create_app(App(domain="other.example.com", app_path="/srv/other", port=3031))
    deployer = deployer_for(tmp_path, runner, document=PORT_STACK)
    deployer.port = 3031

    with pytest.raises(PortConflictError, match="3031"):
        deployer._require_ports_free()


def test_a_port_the_operator_set_in_the_stacks_env_is_the_one_judged(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    store.create_app(App(domain="other.example.com", app_path="/srv/other", port=4040))
    deployer = deployer_for(tmp_path, runner, document=PORT_STACK)
    deployer.port = 3031
    (tmp_path / "app" / ".env").write_text("PORT=4040\n")

    with pytest.raises(PortConflictError, match="4040"):
        deployer._require_ports_free()


def test_a_udp_port_something_listens_on_is_refused(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-lunpH"),
        stdout='UNCONN 0 0 0.0.0.0:5353 0.0.0.0:* users:(("avahi-daemon",pid=7,fd=9))\n',
    )

    with pytest.raises(PortConflictError, match=r"UDP 5353 is already in use \(avahi-daemon\)"):
        deployer_for(tmp_path, runner)._require_ports_free()


def test_the_same_number_over_the_other_protocol_is_not_in_the_way(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 511 0.0.0.0:5353 0.0.0.0:* users:(("node",pid=7,fd=9))\n',
    )

    deployer_for(tmp_path, runner)._require_ports_free()


def test_the_ports_the_stacks_own_containers_hold_are_its_own_on_an_update(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 4096 127.0.0.1:8081 0.0.0.0:* users:(("docker-proxy",pid=7,fd=9))\n',
    )
    running = json.dumps(
        {
            "Name": "stack-web-1",
            "Publishers": [
                {"URL": "127.0.0.1", "TargetPort": 80, "PublishedPort": 8081, "Protocol": "tcp"}
            ],
        }
    )
    runner.script(("docker", "compose"), stdout=running)

    deployer_for(tmp_path, runner, new=False)._require_ports_free()


def test_the_update_flow_never_sets_the_new_deployment_flag_and_its_ports_are_still_its_own(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    """
    ``_is_new_deployment`` is set by ``deploy`` and defaults to True, so an update reaches the
    check with it True. Found against a real Docker: the update of a running stack was refused
    its own port (``TCP 18081 is already in use (docker-proxy)``). What decides is what the
    stack's containers publish, not a flag.
    """
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 4096 127.0.0.1:8081 0.0.0.0:* users:(("docker-proxy",pid=7,fd=9))\n',
    )
    runner.script(
        ("docker", "compose"),
        stdout=json.dumps(
            {
                "Name": "stack-web-1",
                "Publishers": [
                    {"URL": "127.0.0.1", "TargetPort": 80, "PublishedPort": 8081, "Protocol": "tcp"}
                ],
            }
        ),
    )

    deployer_for(tmp_path, runner, new=True)._require_ports_free()


def test_a_port_the_stack_does_not_hold_is_still_refused_whoever_listens(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 4096 127.0.0.1:8081 0.0.0.0:* users:(("docker-proxy",pid=7,fd=9))\n',
    )
    runner.script(("docker", "compose"), stdout="")

    with pytest.raises(PortConflictError, match="8081"):
        deployer_for(tmp_path, runner, new=True)._require_ports_free()


def test_a_stack_that_publishes_nothing_asks_nothing(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    deployer = deployer_for(
        tmp_path, runner, document="services:\n  worker:\n    image: example/worker:1\n"
    )

    deployer._require_ports_free()

    assert not any(call[:1] == ("ss",) for call in runner.calls)


def test_the_reader_of_a_deployed_stack_lists_the_host_ports_its_file_publishes(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    root = tmp_path / "blog"
    root.mkdir()
    (root / "docker-compose.yml").write_text(STACK + '  db:\n    image: x\n    ports: ["5432"]\n')
    app = App(
        domain="blog.example.com",
        app_path=str(root),
        port=8081,
        app_type=AppType.DOCKER_COMPOSE.value,
    )
    store.create_app(app)

    mappings = stack_published_ports(app, runner=runner)

    assert [(m.host_port, m.protocol, m.host_ip) for m in mappings] == [
        (8081, "tcp", "127.0.0.1"),
        (5353, "udp", None),
    ]


def test_the_reader_gives_nothing_for_what_is_not_a_stack_or_has_no_directory(
    tmp_path: Path, store: NoustStore, runner: FakeRunner
) -> None:
    native = App(domain="a.example.com", app_path=str(tmp_path), port=3000)
    gone = App(
        domain="b.example.com",
        app_path=str(tmp_path / "gone"),
        app_type=AppType.DOCKER_COMPOSE.value,
    )

    assert stack_published_ports(native, runner=runner) == []
    assert stack_published_ports(gone, runner=runner) == []
