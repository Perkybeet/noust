# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How what Noust deploys is reached when the machine has no web server of Noust's to put a site on.

On a server an application gets a site in nginx and, when asked, a certificate, and the container it runs
in is published on 127.0.0.1 so that the web server is the only way in. The central of a NAS has no nginx,
and the NAS keeps ports 80 and 443 for its own interface, so an application there is published on a port
of the machine and reached at ``http://<the machine>:<port>``. That is one policy, the *edge*
(:mod:`noust.deployers.helpers.edge`), read by the two deployers: neither builds a web server manager nor a
certificate manager on a container host, and neither has a condition about it beyond asking the edge. What
is held here is that policy, that a server is exactly what it was, the port the search starts from and the
address a person is given, and that a port the NAS already uses is said, with another to try.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.exceptions import ServiceError
from noust.core.runner import FakeRunner
from noust.core.store import App, NoustStore
from noust.deployers import compose_supervisor
from noust.deployers.compose_supervisor import ComposeSupervisor
from noust.deployers.container import ContainerDeployer
from noust.deployers.helpers import edge as edges
from noust.deployers.helpers.container_compose import ContainerPlan, build_document
from noust.managers.service_manager import ResourceLimits
from tests.support.container_deployer import (  # noqa: F401
    IMAGE,
    certs,
    compose_text,
    docker,
    make,
    store,
    web,
)
from tests.support.container_support import APP, DOMAIN, Docker, FakeServices, root_of

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def container_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()


class TestThePolicy:
    def test_a_server_has_a_site_on_loopback_and_looks_for_a_port_where_it_always_did(self) -> None:
        edge = edges.edge()

        assert (edge.has_site, edge.bind, edge.first_port) == (True, "127.0.0.1", None)

    def test_a_hub_that_was_not_told_is_a_server_in_this(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        Config.reset_instance()

        assert edges.edge().has_site

    def test_a_container_host_publishes_on_the_machine_from_8081_and_has_no_site(
        self, container_host: None
    ) -> None:
        edge = edges.edge()

        assert (edge.has_site, edge.bind, edge.first_port) == (False, "", 8081)

    def test_what_is_skipped_on_a_server_is_what_a_stack_that_serves_no_web_skips(self) -> None:
        assert edges.skipped(headless=False, port=3001) is None
        worker = edges.skipped(headless=True, port=3001)
        assert worker is not None and worker.reason == "headless worker"

    def test_a_container_host_skips_the_site_and_says_where_the_application_is_instead(
        self, container_host: None
    ) -> None:
        skipped = edges.skipped(headless=False, port=8081, runner=FakeRunner())

        assert skipped is not None
        assert skipped.reason == "no web server on this machine"
        assert "port 8081 of the machine" in skipped.site_note
        assert "certificate" in skipped.ssl_note

    def test_a_worker_is_a_worker_on_a_container_host_too(self, container_host: None) -> None:
        skipped = edges.skipped(headless=True, port=8081)

        assert skipped is not None and skipped.reason == "headless worker"


class TestTheAddress:
    @pytest.fixture
    def named(self) -> FakeRunner:
        runner = FakeRunner()
        runner.script(["docker", "info", "--format", "{{.Name}}"], stdout="nas-oficina\n")
        return runner

    def test_it_is_the_name_the_machines_docker_has_and_the_port(
        self, container_host: None, named: FakeRunner
    ) -> None:
        assert edges.address(8081, runner=named) == "http://nas-oficina:8081"

    def test_a_server_has_no_such_address_because_it_has_a_domain(self, named: FakeRunner) -> None:
        assert edges.address(8081, runner=named) is None
        assert named.calls == []

    def test_a_machine_that_does_not_say_its_name_is_not_given_a_made_up_one(
        self, container_host: None
    ) -> None:
        runner = FakeRunner()
        runner.script(["docker", "info", "--format", "{{.Name}}"], exit_code=1)

        assert edges.address(8081, runner=runner) == "http://<this machine>:8081"


class TestAPortTheMachineUses:
    @pytest.mark.parametrize(
        ("said", "port"),
        [
            ("Bind for 0.0.0.0:8081 failed: port is already allocated", 8081),
            ("Bind for :::5000 failed: port is already allocated", 5000),
            (
                "driver failed programming external connectivity on endpoint shop-app-1: Error "
                "starting userland proxy: listen tcp4 0.0.0.0:443: bind: address already in use",
                443,
            ),
            (
                "Error response from daemon: listen tcp6 [::]:9000: bind: address already in use",
                9000,
            ),
        ],
    )
    def test_docker_s_two_wordings_are_read(self, said: str, port: int) -> None:
        assert edges.port_taken(said) == port

    def test_anything_else_is_not_a_port_problem(self) -> None:
        assert edges.port_taken("no such image: nginx:latest") is None
        assert edges.port_taken("") is None

    def test_the_stack_that_cannot_start_for_it_says_which_port_and_proposes_another(
        self, store: NoustStore, container_host: None, tmp_path: Path, monkeypatch
    ) -> None:
        root = root_of(tmp_path)
        root.mkdir(parents=True)
        (root / "compose.yaml").write_text(
            'services:\n  app:\n    image: nginx\n    ports:\n      - "8081:80"\n', encoding="utf-8"
        )
        monkeypatch.setattr(compose_supervisor, "get_store", lambda: store)
        store.create_app(
            App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=8081)
        )
        runner = FakeRunner()
        runner.script(
            ["docker", "compose"],
            stderr="Bind for 0.0.0.0:8081 failed: port is already allocated",
            exit_code=1,
        )

        with pytest.raises(ServiceError) as caught:
            ComposeSupervisor(runner=runner).start(APP)

        assert "port 8081" in caught.value.message
        assert "--port" in caught.value.details
        assert "Try 8082" in caught.value.details
        assert "port is already allocated" in caught.value.details

    def test_an_error_that_is_not_about_a_port_is_dockers_own_words_and_nothing_else(
        self, store: NoustStore, container_host: None, tmp_path: Path, monkeypatch
    ) -> None:
        root = root_of(tmp_path)
        root.mkdir(parents=True)
        (root / "compose.yaml").write_text(
            "services:\n  app:\n    image: nginx\n", encoding="utf-8"
        )
        monkeypatch.setattr(compose_supervisor, "get_store", lambda: store)
        store.create_app(App(domain=DOMAIN, app_type="docker-compose", app_path=str(root)))
        runner = FakeRunner()
        runner.script(["docker", "compose"], stderr="pull access denied for nginx", exit_code=1)

        with pytest.raises(ServiceError) as caught:
            ComposeSupervisor(runner=runner).restart(APP)

        assert caught.value.message == f"Failed to restart {APP}"
        assert caught.value.details == "pull access denied for nginx"


class TestAContainerApplication:
    def test_it_is_published_on_every_address_of_the_machine_only_on_a_container_host(self) -> None:
        plan = ContainerPlan(
            app_name=APP,
            domain=DOMAIN,
            release_id="r1",
            host_port=8081,
            settings=IMAGE,
            limits=ResourceLimits(),
        )

        assert build_document(plan)["services"]["app"]["ports"] == ["127.0.0.1:8081:8080"]
        public = build_document(ContainerPlan(**{**plan.__dict__, "bind": ""}))
        assert public["services"]["app"]["ports"] == ["8081:8080"]

    def test_it_is_deployed_with_no_site_no_certificate_and_a_port_of_the_machine(
        self,
        make: Callable[..., ContainerDeployer],
        docker: Docker,
        store: NoustStore,
        container_host: None,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        deployer = make(IMAGE, port=None)
        del deployer._create_systemd_service  # type: ignore[attr-defined]
        monkeypatch.setattr(
            deployer,
            "webserver_manager",
            lambda: pytest.fail("a container host has no web server manager to build"),
        )
        monkeypatch.setattr(
            ContainerDeployer,
            "cert_manager",
            property(lambda self: pytest.fail("nor a certificate manager")),
        )
        docker.script(["docker", "info", "--format", "{{.Name}}"], stdout="nas-oficina\n")
        docker.serving = IMAGE.image
        from noust.deployers import compose_supervisor as supervisor

        monkeypatch.setattr(supervisor.reach, "own_container_id", lambda: "noustid")
        monkeypatch.setattr(supervisor.reach, "service_address", lambda *a, **k: "172.18.0.3")
        monkeypatch.setattr(supervisor.reach, "container_port", lambda *a, **k: 8080)

        deployer.deploy()

        row = store.get_app(DOMAIN)
        assert row is not None
        assert (row.port, row.ssl_enabled, row.webserver) == (8081, False, "")
        assert compose_text(tmp_path)["services"]["app"]["ports"] == ["8081:8080"]
        assert FakeServices.started == []

    def test_a_server_keeps_its_loopback_and_its_first_port(
        self,
        make: Callable[..., ContainerDeployer],
        docker: Docker,
        store: NoustStore,
        tmp_path: Path,
    ) -> None:
        make(IMAGE, port=None).deploy()

        row = store.get_app(DOMAIN)
        assert row is not None and row.port == 3000
        assert compose_text(tmp_path)["services"]["app"]["ports"] == ["127.0.0.1:3000:8080"]
