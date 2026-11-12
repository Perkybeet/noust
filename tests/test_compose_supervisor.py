# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What keeps a deployment alive when there is no systemd: Docker, through the Compose project.

On a server a unit keeps a stack alive (``docker-compose.service`` starts it, stops it and gives its
state and its journal), and every deployer, the health gate and the lifecycle reach it through
``ServiceManager``. The container of a NAS has no systemd, so the same seam has a second engine:
:class:`~noust.deployers.compose_supervisor.ComposeSupervisor`, which starts, stops, restarts, deletes,
reads the state of and reads the log of the stack with ``docker compose``, where Docker's restart policy
is what brings it back after a reboot. A server does not change: the factory hands back the
``ServiceManager`` it was given, and what is held here is that a container host never builds one and never
runs ``systemctl``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_compose_store, app_state
from noust.core.config import Config
from noust.core.exceptions import ServiceError
from noust.core.store import App, NoustStore
from noust.deployers import compose_supervisor as supervisor_module
from noust.deployers.compose_supervisor import ComposeSupervisor
from noust.managers import supervision
from noust.managers.service_manager import ServiceManager
from noust.managers.supervision import service_control
from tests.support.container_support import APP, DOMAIN, Docker, install_fakes, root_of


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "store" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def docker(store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[Docker]:
    yield from install_fakes(monkeypatch, store)


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
    """The central of a NAS that was told to deploy containers here."""
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()


@pytest.fixture
def stack(
    tmp_path: Path, store: NoustStore, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> App:
    """A Compose stack that is deployed: its row, and a compose file in its directory."""
    root = root_of(tmp_path)
    root.mkdir(parents=True)
    (root / "compose.yaml").write_text(
        'services:\n  app:\n    image: nginx\n    ports:\n      - "3001:8080"\n', encoding="utf-8"
    )
    monkeypatch.setattr(supervisor_module, "get_store", lambda: store)
    return store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(root), port=3001)
    )


def supervise(docker: Docker) -> ComposeSupervisor:
    return ComposeSupervisor(runner=docker)


def compose(docker: Docker, verb: str) -> list[tuple[str, ...]]:
    """The ``docker compose`` calls of a verb, with the project and file flags still on."""
    return [call for call in docker.calls if call[:2] == ("docker", "compose") and verb in call]


def nothing_ran_through_systemd(docker: Docker) -> bool:
    return not any(call[0] in ("systemctl", "journalctl") for call in docker.calls)


class TestTheFactory:
    class Systemd:
        """Stands in for ServiceManager: it records how it was built."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args, self.kwargs = args, kwargs

    def test_a_server_gets_the_service_manager_it_was_given_built_as_it_asked(self) -> None:
        control = service_control(self.Systemd, verbose=True, runner=None)

        assert isinstance(control, self.Systemd)
        assert control.kwargs == {"verbose": True, "runner": None}

    def test_a_hub_that_was_not_told_is_the_same(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        Config.reset_instance()

        assert isinstance(service_control(self.Systemd, verbose=False), self.Systemd)

    def test_a_container_host_gets_docker_and_never_builds_the_service_manager(
        self, container_host: None
    ) -> None:
        def refuse(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("a container host has no systemd to build a manager for")

        control = service_control(refuse, verbose=True)

        assert isinstance(control, ComposeSupervisor)

    def test_the_manager_a_module_holds_is_looked_up_when_it_is_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tests that replace a module's ServiceManager keep working: the class is passed in."""

        class Fake:
            def __init__(self, verbose: bool = False, runner: Any = None) -> None:
                self.built = True

        assert service_control(Fake, verbose=False).built is True  # type: ignore[attr-defined]

    def test_the_answer_is_one_function_both_ask(self, container_host: None) -> None:
        assert supervision.keeps_alive_with_docker() is True


class TestKeepingAStackAlive:
    def test_starting_brings_the_project_up_with_its_own_file_and_project(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        supervise(docker).start(APP)

        (call,) = compose(docker, "up")
        assert call[2] == "-p"
        assert call[call.index("-f") + 1].endswith("compose.yaml")
        assert call[-2:] == ("-d", "--remove-orphans")
        assert nothing_ran_through_systemd(docker)

    def test_the_arguments_and_services_the_operator_chose_are_honoured(
        self, stack: App, docker: Docker, container_host: None, store: NoustStore
    ) -> None:
        chosen = app_compose_store.check_options(up_args="--wait --timeout 30", services=["app"])
        app_compose_store.set_options(DOMAIN, chosen, actor="test", store=store)

        supervise(docker).start(APP)

        (call,) = compose(docker, "up")
        assert "--wait" in call and call[-1] == "app"

    def test_a_start_that_fails_says_what_docker_said(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        docker.fail.add("compose -p")

        with pytest.raises(ServiceError) as caught:
            supervise(docker).start(APP)

        assert "boom" in caught.value.details

    def test_stopping_keeps_the_containers_so_that_starting_is_quick_and_nothing_is_lost(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        assert supervise(docker).stop(APP) is True

        assert compose(docker, "stop") and not compose(docker, "down")

    def test_stopping_what_does_not_exist_is_nothing_to_do_as_it_is_for_a_unit(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        assert supervise(docker).stop("other-example-com") is True

        assert compose(docker, "stop") == []

    def test_restarting_recreates_so_that_the_current_environment_is_picked_up(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        supervise(docker).restart(APP)

        (call,) = compose(docker, "up")
        assert call[-3:] == ("-d", "--force-recreate", "--remove-orphans")

    def test_starting_what_is_not_there_is_an_error_that_says_so(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        with pytest.raises(ServiceError, match="other-example-com"):
            supervise(docker).start("other-example-com")

    def test_deleting_takes_the_project_down_and_tolerates_one_that_is_gone(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        supervise(docker).delete_service(APP)
        supervise(docker).delete_service("other-example-com")

        assert len(compose(docker, "down")) == 1

    def test_there_is_no_unit_to_write_and_no_unit_text_to_read(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        control = supervise(docker)

        assert (
            control.create_service(name=APP, working_directory="/x", template="docker-compose")
            is None
        )
        assert control.get_service_config(APP) is None
        assert control.serving_units(stack) == [APP]
        assert docker.calls == []

    def test_nothing_of_this_ever_runs_systemctl(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        control = supervise(docker)

        control.start(APP)
        control.restart(APP)
        control.stop(APP)
        control.get_status(APP)
        control.logs(APP)
        control.delete_service(APP)

        assert nothing_ran_through_systemd(docker)


class TestItsState:
    def ps(self, docker: Docker, *containers: dict[str, Any]) -> None:
        docker.script(["docker", "compose"], stdout="")
        docker.containers = ""
        docker.ps_override = "\n".join(json.dumps(item) for item in containers)  # type: ignore[attr-defined]

    @pytest.fixture(autouse=True)
    def a_docker_that_says_what_the_test_gives(self, docker: Docker) -> None:
        original = docker._lookup

        def lookup(argv: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(argv, *args, **kwargs)
            override = getattr(docker, "ps_override", None)
            if override is not None and tuple(argv[:2]) == ("docker", "compose") and "ps" in argv:
                from dataclasses import replace

                return replace(result, stdout=override)
            return result

        docker._lookup = lookup  # type: ignore[method-assign]

    def status(self, docker: Docker, *containers: dict[str, Any]) -> dict[str, Any]:
        docker.ps_override = "\n".join(json.dumps(item) for item in containers)  # type: ignore[attr-defined]
        return supervise(docker).get_status(APP)

    def test_what_runs_is_active_the_way_a_running_unit_is(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        status = self.status(docker, {"Service": "app", "Name": "app-1", "State": "running"})

        assert status["exists"] is True and status["active"] is True
        assert (status["active_state"], status["sub_state"]) == ("active", "running")
        assert status["managed"] is True

    def test_a_stack_with_no_containers_is_stopped_not_failed_and_not_missing(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        status = self.status(docker)

        assert status["exists"] is True and status["active"] is False
        assert (status["active_state"], status["sub_state"]) == ("inactive", "dead")

    def test_a_container_docker_keeps_restarting_is_the_crash_loop_a_unit_reports(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        status = self.status(
            docker, {"Service": "app", "Name": "app-1", "State": "restarting", "ExitCode": 1}
        )

        assert (status["active_state"], status["sub_state"]) == ("activating", "auto-restart")
        assert status["active"] is False

    @pytest.mark.parametrize(
        "container",
        [
            {"Service": "app", "Name": "app-1", "State": "exited", "ExitCode": 137},
            {"Service": "app", "Name": "app-1", "State": "dead"},
            {"Service": "app", "Name": "app-1", "State": "running", "Health": "unhealthy"},
        ],
    )
    def test_one_that_died_or_is_unhealthy_is_failed(
        self, stack: App, docker: Docker, container_host: None, container: dict[str, Any]
    ) -> None:
        status = self.status(docker, container)

        assert (status["active_state"], status["sub_state"]) == ("failed", "failed")

    def test_a_job_that_ran_and_exited_cleanly_is_not_a_failure_of_the_stack(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        status = self.status(
            docker,
            {"Service": "app", "Name": "app-1", "State": "running"},
            {"Service": "migrate", "Name": "migrate-1", "State": "exited", "ExitCode": 0},
        )

        assert status["active"] is True

    def test_an_application_that_is_not_there_does_not_exist(
        self, docker: Docker, container_host: None, store: NoustStore
    ) -> None:
        status = supervise(docker).get_status("nothing-example-com")

        assert status["exists"] is False and status["active"] is False

    def test_the_keys_are_the_ones_a_unit_s_status_has(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        wanted = set(ServiceManager.status_from_properties("x", {}, managed=True))

        got = self.status(docker, {"Service": "app", "State": "running"})

        assert wanted <= set(got)

    def test_what_decides_the_state_of_an_application_reads_it_without_knowing_the_engine(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        docker.ps_override = json.dumps(  # type: ignore[attr-defined]
            {"Service": "app", "Name": "app-1", "State": "running"}
        )

        state = app_state.resolve_state(stack, supervise(docker), probe=False)  # type: ignore[arg-type]

        assert state.label == app_state.RUNNING

    def test_the_port_on_loopback_is_not_asked_because_it_is_not_where_a_container_answers_from(
        self,
        stack: App,
        docker: Docker,
        container_host: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def nobody_asks(*args: Any, **kwargs: Any) -> bool:
            raise AssertionError("127.0.0.1 is the central's own, not the machine's")

        monkeypatch.setattr(app_state, "port_answers", nobody_asks)
        docker.ps_override = json.dumps(  # type: ignore[attr-defined]
            {"Service": "app", "Name": "app-1", "State": "running"}
        )

        state = app_state.resolve_state(stack, supervise(docker), probe=True)  # type: ignore[arg-type]

        assert state.label == app_state.RUNNING
        assert state.healthy


class TestItsLog:
    def test_the_log_is_what_the_containers_printed_masked_like_everywhere_else(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        text = supervise(docker).logs(APP, lines=40)

        assert "listening on 8080" in text
        (call,) = compose(docker, "logs")
        assert call[call.index("--tail") + 1] == "40"

    def test_the_log_of_what_is_not_there_says_so_instead_of_failing(
        self, docker: Docker, container_host: None, store: NoustStore
    ) -> None:
        assert "nothing-example-com" in supervise(docker).logs("nothing-example-com")


class TestWhatItWillNotPretend:
    #: What the stack's lifecycle asks of whatever keeps it alive, and so what this one does.
    DOES = {
        "create_service",
        "delete_service",
        "disable",
        "enable",
        "get_service_config",
        "get_status",
        "is_managed",
        "logs",
        "restart",
        "serving_units",
        "service_exists",
        "start",
        "stop",
        "unit_exists",
        "app_units",
    }

    def public(self) -> set[str]:
        """The methods of a service manager an instance has to be asked for: not its static helpers."""
        import inspect

        return {
            name
            for name in dir(ServiceManager)
            if not name.startswith("_")
            and callable(getattr(ServiceManager, name))
            and not isinstance(
                inspect.getattr_static(ServiceManager, name), staticmethod | classmethod
            )
        }

    def test_what_it_does_is_part_of_what_a_service_manager_does(self) -> None:
        assert self.DOES <= self.public()

    def test_everything_else_a_service_manager_does_is_refused_in_a_sentence_not_run_or_missed(
        self, docker: Docker, container_host: None
    ) -> None:
        control = supervise(docker)
        refused = sorted(self.public() - self.DOES)
        assert refused, "the test would pass for a class that refuses nothing"

        for name in refused:
            with pytest.raises(ServiceError, match="systemd"):
                getattr(control, name)()

        assert docker.calls == []

    def test_a_name_that_is_not_a_service_manager_s_is_just_missing(
        self, docker: Docker, container_host: None
    ) -> None:
        with pytest.raises(AttributeError):
            supervise(docker).no_such_thing  # noqa: B018


class TestWhereItAnswers:
    """Seen from Noust, which is in a container of its own, the stack answers on a network they share."""

    @pytest.fixture(autouse=True)
    def noust_is_a_container(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
        asked: list[tuple[str, ...]] = []

        def service_address(runner: Any, container: str, *, me: str | None) -> str | None:
            asked.append(("address", container, str(me)))
            return "172.18.0.3"

        def container_port(runner: Any, container: str, published: int | None) -> int:
            asked.append(("port", container, str(published)))
            return 8080

        monkeypatch.setattr(supervisor_module.reach, "own_container_id", lambda: "noustid")
        monkeypatch.setattr(supervisor_module.reach, "service_address", service_address)
        monkeypatch.setattr(supervisor_module.reach, "container_port", container_port)
        return asked

    def test_it_is_the_address_of_the_service_the_site_would_proxy_to_and_the_port_it_listens_on(
        self,
        stack: App,
        docker: Docker,
        container_host: None,
        noust_is_a_container: list[tuple[str, ...]],
    ) -> None:
        docker.serving = "nginx"

        assert supervise(docker).address_of(APP) == "172.18.0.3:8080"

        (listed,) = compose(docker, "ps")
        assert listed[-2:] == ("-q", "app")
        assert noust_is_a_container == [
            ("address", "c0ffee01c0ffee01", "noustid"),
            ("port", "c0ffee01c0ffee01", "3001"),
        ]

    def test_a_central_on_the_machines_own_network_has_nothing_to_say(
        self,
        stack: App,
        docker: Docker,
        container_host: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        docker.serving = "nginx"
        monkeypatch.setattr(supervisor_module.reach, "service_address", lambda *a, **k: None)

        assert supervise(docker).address_of(APP) is None

    def test_a_service_with_no_container_says_so(
        self, stack: App, docker: Docker, container_host: None
    ) -> None:
        docker.serving = None

        with pytest.raises(ServiceError, match="no container to ask"):
            supervise(docker).address_of(APP)

    def test_what_publishes_no_port_has_nothing_to_ask_over_http(
        self, stack: App, docker: Docker, container_host: None, tmp_path: Path
    ) -> None:
        (root_of(tmp_path) / "compose.yaml").write_text(
            "services:\n  worker:\n    image: busybox\n", encoding="utf-8"
        )

        with pytest.raises(ServiceError, match="publishes no port"):
            supervise(docker).address_of(APP)
