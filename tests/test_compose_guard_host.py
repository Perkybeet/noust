# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What gives the machine to a container is refused for a new stack, and warned about for one that had it.

The guard used to refuse three things and warn about the rest, so a stack that mounted ``/run``
(where the Docker socket lives), a device or ``SYS_ADMIN`` reached root all the same. Every entry
here is a thing a container can be given that one without it cannot use, and each is held in the
two directions that decide whether an application in production keeps updating: a stack that
already had it goes on, with a warning; a stack that adds it is refused until an operator records
why.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.compose_guard import inspect_compose

APP = Path("/var/www/apps/stack-example-com")

#: What a service can be given of the machine, and a word of the sentence that says it.
GIVES_THE_MACHINE: dict[str, tuple[dict[str, Any], str]] = {
    "a device": ({"devices": ["/dev/sda:/dev/sda"]}, "the host device"),
    "a device as a mapping": ({"devices": [{"source": "/dev/kvm"}]}, "the host device"),
    "a cgroup rule for devices": ({"device_cgroup_rules": ["c 1:3 mr"]}, "the host device"),
    "ALL capabilities": ({"cap_add": ["ALL"]}, "which gives it this server"),
    "SYS_ADMIN": ({"cap_add": ["SYS_ADMIN"]}, "which gives it this server"),
    "SYS_PTRACE": ({"cap_add": ["SYS_PTRACE"]}, "which gives it this server"),
    "SYS_MODULE": ({"cap_add": ["SYS_MODULE"]}, "which gives it this server"),
    "SYS_RAWIO": ({"cap_add": ["SYS_RAWIO"]}, "which gives it this server"),
    "SYS_BOOT": ({"cap_add": ["SYS_BOOT"]}, "which gives it this server"),
    "DAC_READ_SEARCH": ({"cap_add": ["DAC_READ_SEARCH"]}, "which gives it this server"),
    "NET_ADMIN": ({"cap_add": ["NET_ADMIN"]}, "which gives it this server"),
    "BPF": ({"cap_add": ["BPF"]}, "which gives it this server"),
    "PERFMON": ({"cap_add": ["PERFMON"]}, "which gives it this server"),
    "a capability spelled with its CAP_ prefix": ({"cap_add": ["cap_sys_admin"]}, "which gives it"),
    "the host's pid namespace": ({"pid": "host"}, "shares the host's pid"),
    "the host's ipc namespace": ({"ipc": "host"}, "shares the host's ipc"),
    "the host's uts namespace": ({"uts": "host"}, "shares the host's uts"),
    "no user namespace": ({"userns_mode": "host"}, "shares the host's userns_mode"),
    "the host's cgroup namespace": ({"cgroup": "host"}, "shares the host's cgroup"),
    "the host's network": ({"network_mode": "host"}, "shares the host's network_mode"),
    "no seccomp": ({"security_opt": ["seccomp=unconfined"]}, "confinement off"),
    "no apparmor": ({"security_opt": ["apparmor:unconfined"]}, "confinement off"),
    "no selinux label": ({"security_opt": ["label=disable"]}, "confinement off"),
    "a cgroup of the host": ({"cgroup_parent": "/system.slice"}, "cgroup /system.slice"),
    "another runtime": ({"runtime": "nvidia"}, "runtime nvidia"),
    "a program the machine runs": ({"provider": {"type": "sh"}}, "program sh"),
    "the volumes of a container that is not the stack's": (
        {"volumes_from": ["container:payments-db"]},
        "volumes of the container payments-db",
    ),
    "the network of a container that is not the stack's": (
        {"network_mode": "container:vpn"},
        "joins the network_mode of the container vpn",
    ),
    "a build with elevated privileges": (
        {"build": {"context": ".", "privileged": True}},
        "builds with elevated privileges",
    ),
    "a build on the host's network": (
        {"build": {"context": ".", "network": "host"}},
        "builds on this server's own network",
    ),
    "the run directory (the Docker socket is in it)": (
        {"volumes": ["/run:/hostrun"]},
        "outside the application",
    ),
    "/var/run": ({"volumes": ["/var/run:/hostrun"]}, "outside the application"),
    "Docker's own directory": ({"volumes": ["/var/lib/docker:/d"]}, "outside the application"),
    "root's home": ({"volumes": ["/root:/r"]}, "outside the application"),
    "the cron directory": ({"volumes": ["/etc/cron.d:/c"]}, "outside the application"),
    "the home directories": ({"volumes": ["/home:/h"]}, "outside the application"),
    "the socket of another container runtime": (
        {"volumes": ["/run/containerd/containerd.sock:/c.sock"]},
        "socket of a container runtime",
    ),
}

#: What a service can be given that does not give the machine, and is only said.
ONLY_SAID: dict[str, dict[str, Any]] = {
    "a capability that changes what the container does inside itself": {
        "cap_add": ["NET_BIND_SERVICE"]
    },
    "docker's own runtime": {"runtime": "runc"},
    "a namespace of a service of the stack": {"network_mode": "service:db", "pid": "service:db"},
    "a private namespace": {"ipc": "private", "cgroup": "private", "network_mode": "bridge"},
    "the application's own directory": {"volumes": ["./data:/data", f"{APP}/uploads:/u"]},
}


def stack(**service: Any) -> dict[str, Any]:
    """A compose document with one service."""
    return {"services": {"web": {"image": "nginx", **service}}}


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def deployer(
    tmp_path: Path, store: NoustStore, said: list[str] | None = None
) -> DockerComposeDeployer:
    """A compose deployer of an application, its log going to ``said``; Compose cannot resolve."""
    runner = FakeRunner()
    runner.script(["docker", "compose"], stdout="", stderr="invalid compose project", exit_code=1)
    compose = DockerComposeDeployer(runner=runner)
    compose.configure("stack.example.com", "src", app_path=tmp_path / "stack")
    compose.store = store
    if said is not None:
        compose.logger.attach_sink(said.append)
    return compose


class TestWhatTheFileAsks:
    """The guard sorts each into refused."""

    @pytest.mark.parametrize("name", sorted(GIVES_THE_MACHINE))
    def test_what_gives_the_machine_is_refused(self, name: str) -> None:
        service, word = GIVES_THE_MACHINE[name]

        findings = inspect_compose(stack(**service), APP)

        assert findings.refused, name
        assert any(word in sentence for sentence in findings.refused), findings.refused

    @pytest.mark.parametrize("name", sorted(ONLY_SAID))
    def test_what_does_not_is_not_refused(self, name: str) -> None:
        findings = inspect_compose(stack(**ONLY_SAID[name]), APP)

        assert findings.refused == []

    def test_the_capabilities_that_do_not_give_the_machine_are_only_said(self) -> None:
        findings = inspect_compose(stack(cap_add=["SYS_ADMIN", "CHOWN", "NET_RAW"]), APP)

        assert findings.refused == [
            "service web adds the capability SYS_ADMIN, which gives it this server"
        ]
        assert findings.warned == ["service web adds capabilities: CHOWN, NET_RAW"]

    def test_a_volume_that_is_a_bind_of_the_host_by_another_name_is_a_mount(self) -> None:
        document = {
            "services": {"web": {"image": "nginx", "volumes": ["etc:/host-etc"]}},
            "volumes": {
                "etc": {
                    "driver": "local",
                    "driver_opts": {"type": "none", "o": "bind", "device": "/srv/data"},
                }
            },
        }

        findings = inspect_compose(document, APP)

        assert findings.refused == [
            "volume etc mounts /srv/data from this server, outside the application"
        ]

    def test_a_volume_whose_device_is_in_the_application_is_the_applications(self) -> None:
        document = {
            "volumes": {
                "data": {"driver_opts": {"type": "none", "o": "bind", "device": f"{APP}/data"}}
            },
            "services": {"web": {"image": "nginx"}},
        }

        assert not inspect_compose(document, APP)

    def test_a_job_is_given_no_more_than_a_service(self) -> None:
        document = {"services": {}, "jobs": {"migrate": {"image": "x", "devices": ["/dev/sda"]}}}

        findings = inspect_compose(document, APP)

        assert any(sentence.startswith("job migrate") for sentence in findings.refused)


class TestAnApplicationInProductionKeepsUpdating:
    """A new stack is refused; one that had it goes on, with a warning; one that adds it is refused."""

    @pytest.mark.parametrize("name", sorted(GIVES_THE_MACHINE))
    def test_a_new_stack_is_refused_with_the_way_out(
        self, name: str, tmp_path: Path, store: NoustStore
    ) -> None:
        service, _word = GIVES_THE_MACHINE[name]

        with pytest.raises(DeploymentError) as raised:
            deployer(tmp_path, store)._check_host_privileges(stack(**service), accepted=[])

        details = raised.value.details or ""
        assert "noust app sandbox compose-exception stack.example.com --reason" in details

    @pytest.mark.parametrize("name", sorted(GIVES_THE_MACHINE))
    def test_a_stack_that_had_it_is_updated_with_a_warning(
        self, name: str, tmp_path: Path, store: NoustStore
    ) -> None:
        service, _word = GIVES_THE_MACHINE[name]
        said: list[str] = []
        compose = deployer(tmp_path, store, said)
        document = stack(**service)

        compose._check_host_privileges(document, accepted=compose._refusals_in(document))

        assert any("A new stack is refused this" in line for line in said), said

    @pytest.mark.parametrize("name", sorted(GIVES_THE_MACHINE))
    def test_a_stack_that_adds_it_is_refused(
        self, name: str, tmp_path: Path, store: NoustStore
    ) -> None:
        service, _word = GIVES_THE_MACHINE[name]
        compose = deployer(tmp_path, store)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(stack(**service), accepted=compose._refusals_in(stack()))

    def test_what_an_update_takes_away_is_never_taken_for_something_added(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = deployer(tmp_path, store)
        before = stack(devices=["/dev/kvm", "/dev/sda"], cap_add=["SYS_ADMIN", "NET_ADMIN"])

        compose._check_host_privileges(
            stack(devices=["/dev/sda"], cap_add=["NET_ADMIN"]),
            accepted=compose._refusals_in(before),
        )

    def test_a_stack_that_had_one_thing_is_refused_the_second_it_adds(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = deployer(tmp_path, store)
        before = stack(devices=["/dev/kvm"])

        with pytest.raises(DeploymentError) as raised:
            compose._check_host_privileges(
                stack(devices=["/dev/kvm"], pid="host"), accepted=compose._refusals_in(before)
            )

        assert "shares the host's pid" in (raised.value.details or "")
        assert "the host device" not in (raised.value.details or "")

    @pytest.mark.parametrize("name", sorted(GIVES_THE_MACHINE))
    def test_an_exception_lets_a_new_stack_through_and_says_whose(
        self, name: str, tmp_path: Path, store: NoustStore
    ) -> None:
        service, _word = GIVES_THE_MACHINE[name]
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="runs a vpn", store=store
        )
        said: list[str] = []

        deployer(tmp_path, store, said)._check_host_privileges(stack(**service), accepted=[])

        assert any("allowed for stack.example.com by alice (runs a vpn)" in line for line in said)


class TestTheWayThroughTheRealFlows:
    """The flows that decide what a stack "already had" use the one verdict."""

    def test_a_redeploy_remembers_what_the_file_on_disk_asked_for(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        compose = deployer(tmp_path, store)
        compose.app_path.mkdir()
        (compose.app_path / "docker-compose.yml").write_text(
            "services:\n  web:\n    image: nginx\n    devices: ['/dev/kvm']\n"
        )

        compose._remember_serving_privileges()

        remembered = compose._accepted_refusals
        assert remembered is not None
        assert any("the host device" in sentence for sentence in remembered.sentences)
        compose._check_host_privileges(stack(devices=["/dev/kvm"]))

    def test_an_update_compares_with_the_compose_file_of_the_commit_that_served(
        self, tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose = deployer(tmp_path, store)
        compose.app_path.mkdir()
        compose.compose_path = compose.app_path / "docker-compose.yml"
        compose.compose_path.write_text("services:\n  web:\n    image: nginx\n")
        compose.previous_commit = "a" * 40
        served = "services:\n  web:\n    image: nginx\n    devices: ['/dev/kvm']\n"

        class Source:
            def file_at_commit(self, *_args: Any) -> str:
                return served

        monkeypatch.setattr(compose, "_source_manager", lambda: Source())
        monkeypatch.setattr(
            "noust.deployers.helpers.compose_privileges.changed_links", lambda *_a: frozenset()
        )

        compose._check_update_privileges(stack(devices=["/dev/kvm"]))
        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_update_privileges(stack(devices=["/dev/kvm", "/dev/sda"]))
