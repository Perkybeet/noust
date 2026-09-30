"""
Tests for the Docker Compose check: what in a compose file is root on the host.

The file comes from the repository and runs through the root Docker daemon, so
a privileged service or the Docker socket is the whole server. A new stack is
refused them without an exception an operator recorded; a running one is
warned about and never stopped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.compose_guard import inspect_compose

APP = Path("/var/www/apps/stack-example-com")


def stack(**service: Any) -> dict[str, Any]:
    """A compose document with one service."""
    return {"services": {"web": {"image": "nginx", **service}}}


class TestInspect:
    """What the file asks for, sorted into refused and warned."""

    def test_a_plain_stack_asks_for_nothing(self) -> None:
        findings = inspect_compose(
            stack(ports=["8080:80"], volumes=["./data:/data", "db:/var/lib/db"]), APP
        )

        assert not findings

    @pytest.mark.parametrize("value", [True, "true", "yes"])
    def test_privileged_is_refused(self, value: object) -> None:
        findings = inspect_compose(stack(privileged=value), APP)

        assert findings.refused == [
            "service web is privileged: it has every capability and every device of this server"
        ]

    @pytest.mark.parametrize(
        "volume",
        [
            "/var/run/docker.sock:/var/run/docker.sock",
            "/run/docker.sock:/var/run/docker.sock:ro",
            {"type": "bind", "source": "/var/run/docker.sock", "target": "/sock"},
        ],
    )
    def test_the_docker_socket_is_refused_in_either_syntax(self, volume: object) -> None:
        findings = inspect_compose(stack(volumes=[volume]), APP)

        assert len(findings.refused) == 1
        assert "Docker socket" in findings.refused[0]

    def test_the_hosts_namespaces_and_capabilities_are_warned_about(self) -> None:
        findings = inspect_compose(
            stack(
                pid="host",
                network_mode="host",
                cap_add=["SYS_ADMIN"],
                devices=["/dev/kvm"],
                security_opt=["apparmor:unconfined"],
                volumes=["/etc:/host-etc:ro", f"{APP}/uploads:/uploads", "./ok:/ok"],
            ),
            APP,
        )

        assert findings.refused == []
        assert findings.warned == [
            "service web mounts /etc from this server, outside the application",
            "service web shares the host's pid",
            "service web shares the host's network_mode",
            "service web adds capabilities: SYS_ADMIN",
            "service web is given host devices",
            "service web turns its confinement off (apparmor:unconfined)",
        ]

    def test_something_that_is_not_a_compose_file_asks_for_nothing(self) -> None:
        assert not inspect_compose(None, APP)
        assert not inspect_compose({"services": "nope"}, APP)


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def deployer(
    tmp_path: Path, store: NoustStore, *, new: bool, said: list[str] | None = None
) -> DockerComposeDeployer:
    """A compose deployer at a new or an existing stack, its log going to ``said``."""
    compose = DockerComposeDeployer()
    compose.configure("stack.example.com", "src", app_path=tmp_path / "stack")
    compose.store = store
    compose._is_new_deployment = new
    if said is not None:
        compose.logger.attach_sink(said.append)
    return compose


class TestDeployer:
    """Refused for a new stack, warned about for one that runs, allowed by an exception."""

    def test_a_new_privileged_stack_is_refused_with_the_way_out(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = deployer(tmp_path, store, new=True)

        with pytest.raises(DeploymentError) as raised:
            compose._check_host_privileges(stack(privileged=True))

        assert "asks for root" in raised.value.message
        details = raised.value.details or ""
        assert "service web is privileged" in details
        assert "noust app sandbox compose-exception stack.example.com --reason" in details

    def test_a_running_stack_is_warned_about_and_goes_on(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        said: list[str] = []
        compose = deployer(tmp_path, store, new=False, said=said)

        compose._check_host_privileges(stack(volumes=["/var/run/docker.sock:/sock"]))

        assert any("A new stack is refused this" in line for line in said)

    def test_an_update_is_never_refused(self, tmp_path: Path, store: NoustStore) -> None:
        compose = deployer(tmp_path, store, new=True)

        compose._check_host_privileges(stack(privileged=True), existing=True)

    def test_an_exception_lets_a_new_stack_through_and_says_whose(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="runs portainer", store=store
        )
        said: list[str] = []
        compose = deployer(tmp_path, store, new=True, said=said)

        compose._check_host_privileges(stack(privileged=True))

        assert any(
            "allowed for stack.example.com by alice (runs portainer)" in line for line in said
        )


def test_a_running_privileged_stack_is_in_the_warnings_until_it_has_an_exception(
    tmp_path: Path, store: NoustStore
) -> None:
    from noust.core.store import App

    app_path = tmp_path / "stack"
    app_path.mkdir()
    (app_path / "docker-compose.yml").write_text(
        "services:\n  agent:\n    image: portainer/agent\n"
        "    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n"
    )
    app = App(domain="stack.example.com", app_type="docker-compose", app_path=str(app_path))
    store.create_app(app)

    warning = build_sandbox.sandbox_warning(app)

    assert warning is not None
    assert "mounts the Docker socket" in warning
    assert "noust app sandbox compose-exception stack.example.com" in warning
    build_sandbox.set_compose_exception(
        "stack.example.com", allowed=True, actor="alice", reason="portainer", store=store
    )
    assert build_sandbox.sandbox_warning(app) is None
