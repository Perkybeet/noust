# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container application deployed where Docker, and not systemd, keeps it alive.

The deployer, the health gate and the lifecycle reach what keeps a deployment alive through the
service manager of their own module, and a server gets it back exactly as it was. On a container host
(a hub told to deploy containers on its machine's Docker) the same seam hands back Docker's engine, so a
deployment is created, started behind the health gate, stopped, restarted and deleted without a unit
being written and without ``systemctl`` being run. The machine here is the shared one of the container
tests (``tests/support/container_support.py``): a Docker that remembers its images, and the module's
``ServiceManager`` replaced by a fake that records what a server would have asked of it, which on a
container host must be nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.store import AppStatus
from noust.deployers.container import ContainerDeployer
from noust.managers.supervision import remember_stack
from tests.support.container_deployer import (  # noqa: F401
    IMAGE,
    certs,
    docker,
    make,
    store,
    web,
)
from tests.support.container_support import APP, DOMAIN, Docker, FakeServices

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


@pytest.fixture(autouse=True)
def a_container_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def unit_made(make: Callable[..., ContainerDeployer]) -> Callable[..., ContainerDeployer]:
    """
    The shared builder, with the creation of the unit put back (it is what is being tested) and the
    site and its certificate set aside (a container host has none, which is its own change).
    """

    def build(*args: object, **kwargs: object) -> ContainerDeployer:
        deployer = make(*args, **kwargs)
        del deployer._create_systemd_service  # type: ignore[attr-defined]
        deployer._create_site = lambda: None  # type: ignore[method-assign]
        deployer._obtain_certificate = lambda: None  # type: ignore[method-assign]
        return deployer

    return build


@pytest.fixture(autouse=True)
def noust_is_a_container(docker, monkeypatch) -> list[str]:
    """
    Noust is in a container of its own, and the stack's container is on a network it shares: the
    probe is what asks where it answers, and every URL it was given is kept.
    """
    from noust.deployers import compose_supervisor, docker_compose

    urls: list[str] = []

    def probe(url: str, **kwargs: object) -> bool:
        urls.append(url)
        return docker.healthy

    monkeypatch.setattr(docker_compose, "wait_until_healthy", probe)
    monkeypatch.setattr(compose_supervisor.reach, "own_container_id", lambda: "noustid")
    monkeypatch.setattr(
        compose_supervisor.reach, "service_address", lambda *args, **kwargs: "172.18.0.3"
    )
    monkeypatch.setattr(compose_supervisor.reach, "container_port", lambda *args, **kwargs: 8080)
    docker.serving = "ghcr.io/owner/app:1.4"
    return urls


def systemd_calls(docker: Docker) -> list[tuple[str, ...]]:
    return [call for call in docker.calls if call[0] in ("systemctl", "journalctl")]


class TestDeploying:
    def test_it_is_started_through_docker_behind_the_health_gate_and_no_unit_is_asked_for(
        self, unit_made, docker, store
    ):
        unit_made(IMAGE).deploy()

        assert FakeServices.started == []
        assert ("up", "-d", "--remove-orphans") in [call[-3:] for call in docker.compose_calls()]
        assert systemd_calls(docker) == []
        row = store.get_app(DOMAIN)
        assert row is not None and row.status == AppStatus.RUNNING.value

    def test_a_container_that_does_not_answer_fails_the_deployment_as_it_does_on_a_server(
        self, unit_made, docker, store
    ):
        docker.healthy = False

        with pytest.raises(Exception, match="answer"):
            unit_made(IMAGE).deploy()

        assert systemd_calls(docker) == []

    def test_what_the_gate_reads_when_it_fails_is_what_the_containers_printed(
        self, unit_made, docker, store
    ):
        docker.healthy = False

        with pytest.raises(Exception) as caught:
            unit_made(IMAGE).deploy()

        assert "listening on 8080" in str(caught.value)
        assert "journal line" not in str(caught.value)


class TestTheLifecycleOfADeployedApplication:
    def test_stop_start_and_restart_are_dockers(self, unit_made, docker, store):
        deployer = unit_made(IMAGE)
        deployer.deploy()
        docker.calls.clear()

        deployer.stop()
        deployer.start()
        deployer.restart()

        verbs = [call[-4:] for call in docker.compose_calls()]
        assert ("stop",) in [call[-1:] for call in docker.compose_calls()]
        assert any("--force-recreate" in call for call in verbs) is True
        assert FakeServices.started == [] and FakeServices.restarted == []
        assert systemd_calls(docker) == []
        row = store.get_app(DOMAIN)
        assert row is not None and row.status == AppStatus.RUNNING.value

    def test_deleting_takes_the_project_down_without_a_unit_to_delete(
        self, unit_made, docker, store
    ):
        deployer = unit_made(IMAGE)
        deployer.deploy()

        deployer.delete()

        assert FakeServices.deleted == []
        assert any(call[-2:] == ("down", "--remove-orphans") for call in docker.compose_calls())
        assert systemd_calls(docker) == []
        assert store.get_app(DOMAIN) is None


class TestTheProbe:
    """Noust is in a container of its own: the gate asks where the container answers from there."""

    def test_the_first_deployment_is_judged_where_the_container_answers_not_on_loopback(
        self, unit_made, docker, store, noust_is_a_container
    ):
        unit_made(IMAGE).deploy()

        assert noust_is_a_container == ["http://172.18.0.3:8080/"]

    def test_an_update_is_judged_there_too_and_a_failure_names_the_address(
        self, unit_made, docker, store, noust_is_a_container
    ):
        from noust.deployers.docker_compose import stack_deployer

        unit_made(IMAGE).deploy()
        noust_is_a_container.clear()

        app = store.get_app(DOMAIN)
        assert app is not None
        result = stack_deployer(app, runner=docker).update()

        assert noust_is_a_container and set(noust_is_a_container) == {"http://172.18.0.3:8080/"}
        assert result is not None


class TestAServerIsWhatItWas:
    def test_the_same_deployment_asks_the_service_manager_as_it_always_did(
        self, make, docker, store, monkeypatch
    ):
        monkeypatch.delenv("NOUST_CENTRAL_ROLE")
        monkeypatch.delenv("NOUST_DEPLOY_HERE")
        Config.reset_instance()

        make(IMAGE).deploy()

        assert FakeServices.started == [APP]

    def test_and_the_health_gate_asks_127_0_0_1_on_the_published_port(
        self, make, docker, store, monkeypatch
    ):
        from noust.deployers import docker_compose

        monkeypatch.delenv("NOUST_CENTRAL_ROLE")
        monkeypatch.delenv("NOUST_DEPLOY_HERE")
        Config.reset_instance()
        urls: list[str] = []
        monkeypatch.setattr(
            docker_compose, "wait_until_healthy", lambda url, **kw: urls.append(url) or True
        )

        make(IMAGE).deploy()

        assert urls == ["http://127.0.0.1:3001/"]


class TestWhatIsRememberedOfAStack:
    class Stack:
        """What a Compose deployer has once its stack is registered."""

        def __init__(self, store, root: Path, file: str, profiles: list[str]) -> None:
            self.store = store
            self.domain = DOMAIN
            self.app_path = root
            self.compose_path = root / file
            self.compose_profiles = profiles

    @pytest.fixture
    def registered(self, store, tmp_path: Path):
        from noust.core.store import App

        root = tmp_path / "apps" / APP
        root.mkdir(parents=True)
        store.create_app(App(domain=DOMAIN, app_type="docker-compose", app_path=str(root)))
        return root

    def test_a_stack_that_brings_its_own_document_has_it_and_its_profiles_remembered(
        self, store, registered
    ):
        from noust.core import app_compose_store

        remember_stack(self.Stack(store, registered, "docker-compose.prod.yml", ["web"]))

        kept = app_compose_store.get_options(DOMAIN, store=store)
        assert kept.compose_file == "docker-compose.prod.yml"
        assert tuple(kept.profiles) == ("web",)

    def test_a_deployment_that_says_nothing_new_writes_nothing(self, store, registered):
        from noust.core import app_compose_store

        stack = self.Stack(store, registered, "compose.yaml", [])
        remember_stack(stack)
        first = app_compose_store.get_options(DOMAIN, store=store)

        remember_stack(stack)

        assert app_compose_store.get_options(DOMAIN, store=store) == first

    def test_a_server_writes_nothing_because_its_unit_says_it(self, store, registered, monkeypatch):
        from noust.core import app_compose_store

        monkeypatch.delenv("NOUST_CENTRAL_ROLE")
        monkeypatch.delenv("NOUST_DEPLOY_HERE")
        Config.reset_instance()

        remember_stack(self.Stack(store, registered, "docker-compose.prod.yml", ["web"]))

        assert app_compose_store.get_options(DOMAIN, store=store).compose_file is None
