# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The compose guard judges what Compose resolves, not only the ``services`` of the file.

``deployers/helpers/compose_guard.py`` read ``document["services"]`` of the file the repository
wrote. A service brought in by ``include`` or ``extends``, a ``jobs`` entry, a ``volumes_from``
that borrows the mounts of another container, the file of a ``secrets`` or ``configs`` entry and a
service under a profile are all things that run and that line never showed: the class of the
notices of 2026 (Arcane's ``include`` directive, Portainer's ``volumes_from`` and its configs and
secrets), and ``jobs``, which the Compose specification took in on 2026-08-16.

What the guard now does is judge the file as written, then the model ``docker compose config``
prints for it (every profile on, every include and extension merged), and say what only the second
showed. The commands are the runner's: nothing here executes a process.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner, is_read_only
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.compose_guard import (
    inspect_compose,
    merge_findings,
    uses_composition,
)

APP = Path("/var/www/apps/stack-example-com")

#: A bind mount as ``docker compose config --format json`` prints it.
SOCKET = {"type": "bind", "source": "/var/run/docker.sock", "target": "/sock"}


def stack(**service: Any) -> dict[str, Any]:
    """A compose document with one service."""
    return {"services": {"web": {"image": "nginx", **service}}}


def model(**services: Any) -> dict[str, Any]:
    """The resolved model of a stack: its name and one entry per service."""
    return {"name": "stack", "services": services}


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class TestWhatTheFileBringsInFromElsewhere:
    """The ways a stack reaches the host or another application without saying ``privileged``."""

    def test_a_job_is_judged_like_a_service(self) -> None:
        findings = inspect_compose(
            {
                "services": {"web": {"image": "nginx"}},
                "jobs": {
                    "migrate": {
                        "image": "app",
                        "privileged": True,
                        "volumes": ["/var/run/docker.sock:/sock"],
                    }
                },
            },
            APP,
        )

        assert findings.refused == [
            "job migrate is privileged: it has every capability and every device of this server",
            "job migrate mounts the Docker socket (/var/run/docker.sock): it can start a "
            "container with this server's root filesystem",
        ]

    def test_the_volumes_of_a_container_outside_the_stack_are_refused(self) -> None:
        findings = inspect_compose(stack(volumes_from=["container:payments-db:ro"]), APP)

        assert findings.refused == [
            "service web takes the volumes of the container payments-db, which is not part of "
            "this stack: whatever that container has mounted is then in this one"
        ]

    def test_the_volumes_of_a_service_of_the_stack_are_its_own_business(self) -> None:
        document = {
            "services": {
                "web": {"image": "nginx", "volumes_from": ["db", "db:ro", "service:db"]},
                "db": {"image": "postgres", "volumes": ["data:/var/lib/postgresql"]},
            }
        }

        assert not inspect_compose(document, APP)

    @pytest.mark.parametrize("namespace", ["pid", "ipc", "network_mode", "userns_mode"])
    def test_joining_the_namespace_of_another_container_is_refused(self, namespace: str) -> None:
        findings = inspect_compose(stack(**{namespace: "container:other-app"}), APP)

        assert findings.refused == [
            f"service web joins the {namespace} of the container other-app, which is not part "
            "of this stack"
        ]

    def test_joining_the_namespace_of_a_service_of_the_stack_is_not_a_finding(self) -> None:
        assert not inspect_compose(stack(network_mode="service:db", pid="service:db"), APP)

    @pytest.mark.parametrize(
        "entry",
        [
            "../other-example-com/compose.yaml",
            "/etc/noust/compose.yaml",
            {"path": "../../../tmp/x.yaml"},
            {"path": ["ok.yaml", "../escape.yaml"], "project_directory": "."},
        ],
    )
    def test_an_include_that_leaves_the_application_is_refused(self, entry: object) -> None:
        findings = inspect_compose({**stack(), "include": [entry]}, APP)

        assert len(findings.refused) == 1, findings
        assert "include" in findings.refused[0]
        assert "outside the application" in findings.refused[0]

    def test_an_include_inside_the_application_is_left_to_the_resolved_model(self) -> None:
        document = {**stack(), "include": ["deploy/db.yaml", {"path": "a.yml"}]}

        assert not inspect_compose(document, APP)

    def test_a_link_inside_the_application_that_leads_out_is_an_include_that_leaves(
        self, tmp_path: Path
    ) -> None:
        app = tmp_path / "app"
        app.mkdir()
        (tmp_path / "secret.yaml").write_text("services: {}\n")
        (app / "inc.yaml").symlink_to(tmp_path / "secret.yaml")

        findings = inspect_compose({**stack(), "include": ["inc.yaml"]}, app)

        assert len(findings.refused) == 1
        assert "outside the application" in findings.refused[0]

    @pytest.mark.parametrize(
        "extends",
        [
            {"file": "../../other-example-com/compose.yaml", "service": "web"},
            {"file": "/var/www/apps/other-example-com/docker-compose.yml", "service": "web"},
        ],
    )
    def test_extending_a_service_of_a_file_outside_the_application_is_refused(
        self, extends: object
    ) -> None:
        findings = inspect_compose(stack(extends=extends), APP)

        assert len(findings.refused) == 1, findings
        assert "service web extends a file outside the application" in findings.refused[0]

    def test_extending_inside_the_file_or_the_application_is_left_to_the_resolved_model(
        self,
    ) -> None:
        assert not inspect_compose(stack(extends="base"), APP)
        assert not inspect_compose(stack(extends={"file": "common.yml", "service": "web"}), APP)

    @pytest.mark.parametrize("kind", ["secrets", "configs"])
    def test_a_secret_or_a_config_that_is_a_file_of_noust_is_refused(self, kind: str) -> None:
        document = {
            "services": {"web": {"image": "nginx", kind: ["token"]}},
            kind: {"token": {"file": "/etc/noust/web-secret"}},
        }

        findings = inspect_compose(document, APP)

        assert len(findings.refused) == 1, findings
        assert "service web" in findings.refused[0]
        assert "configuration and secrets" in findings.refused[0]
        assert f"as a {kind[:-1]}" in findings.refused[0]

    def test_a_secret_from_another_applications_file_is_refused_in_the_long_syntax(self) -> None:
        document = {
            "services": {"web": {"image": "nginx", "secrets": [{"source": "env", "target": "e"}]}},
            "secrets": {"env": {"file": "/var/www/apps/other-example-com/.env"}},
        }

        findings = inspect_compose(document, APP)

        assert len(findings.refused) == 1
        assert "other application" in findings.refused[0]

    def test_a_secret_from_a_file_outside_the_application_is_warned_about(self) -> None:
        document = {
            "services": {"web": {"image": "nginx", "secrets": ["cert"]}},
            "secrets": {"cert": {"file": "/etc/ssl/private/cert.pem"}},
        }

        findings = inspect_compose(document, APP)

        assert findings.refused == []
        assert findings.warned == [
            "service web mounts /etc/ssl/private/cert.pem from this server as a secret, "
            "outside the application"
        ]

    def test_a_secret_nobody_uses_and_one_from_the_application_are_nothing(self) -> None:
        document = {
            "services": {"web": {"image": "nginx", "secrets": ["mine"]}},
            "secrets": {
                "mine": {"file": "./secrets/mine.txt"},
                "unused": {"file": "/etc/noust/web-secret"},
                "inline": {"content": "x"},
            },
        }

        assert not inspect_compose(document, APP)


class TestTheResolvedModel:
    """What ``docker compose config`` says the file means, which is what runs."""

    def test_a_service_an_include_brought_in_is_judged(self) -> None:
        findings = inspect_compose(
            model(web={"image": "nginx"}, reporter={"image": "x", "volumes": [SOCKET]}), APP
        )

        assert len(findings.refused) == 1
        assert findings.refused[0].startswith("service reporter mounts the Docker socket")

    def test_what_the_file_already_showed_is_not_said_twice(self) -> None:
        # The file says it with a relative path and Compose resolves it to an absolute one.
        raw = inspect_compose(stack(volumes=["../../../srv/x:/x"], cap_add=["SYS_ADMIN"]), APP)
        resolved = inspect_compose(
            model(
                web={
                    "image": "nginx",
                    "cap_add": ["SYS_ADMIN"],
                    "volumes": [{"type": "bind", "source": "/var/srv/x", "target": "/x"}],
                }
            ),
            APP,
        )

        merged = merge_findings(raw, resolved)

        assert merged.warned == raw.warned
        assert merged.refused == raw.refused
        assert len(raw.warned) == 2

    def test_what_only_the_model_shows_is_added(self) -> None:
        raw = inspect_compose(stack(), APP)
        resolved = inspect_compose(model(web={"image": "nginx", "privileged": True}), APP)

        assert merge_findings(raw, resolved).refused == resolved.refused

    def test_composition_is_what_brings_in_the_text_of_another_file(self) -> None:
        assert uses_composition({"include": ["x.yaml"], "services": {}})
        assert uses_composition(stack(extends={"file": "base.yml", "service": "web"}))
        assert uses_composition({"services": {}, "jobs": {"j": {"extends": {"file": "x.yml"}}}})
        # What a service of the same file does is in the file, and the file is judged.
        assert not uses_composition(stack(extends="base"))
        assert not uses_composition(stack(volumes_from=["db"]))
        assert not uses_composition(stack(ports=["80:80"]))
        assert not uses_composition(None)


def stack_at(tmp_path: Path, store: NoustStore, runner: FakeRunner, *, new: bool = True) -> Any:
    """A deployer at ``tmp_path/stack`` whose compose commands go to ``runner``."""
    compose = DockerComposeDeployer(runner=runner)
    compose.configure("stack.example.com", "src", app_path=tmp_path / "stack")
    compose.store = store
    compose._is_new_deployment = new
    compose.compose_path = tmp_path / "stack" / "docker-compose.yml"
    return compose


def resolved_by(runner: FakeRunner, document: dict[str, Any], *, exit_code: int = 0) -> None:
    """Make the fake ``docker compose config`` print a model, or fail."""
    runner.script(
        ["docker", "compose"],
        stdout=json.dumps(document) if exit_code == 0 else "",
        stderr="" if exit_code == 0 else "invalid compose project",
        exit_code=exit_code,
    )


class TestTheDeployerJudgesWhatComposeResolves:
    """An include, an extends or a profile does not hide what the stack asks of the host."""

    def test_a_service_hidden_in_an_include_is_refused_with_the_way_out(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        resolved_by(
            runner, model(web={"image": "nginx"}, agent={"image": "x", "volumes": [SOCKET]})
        )
        compose = stack_at(tmp_path, store, runner)

        with pytest.raises(DeploymentError) as raised:
            compose._check_host_privileges({"include": ["agent.yaml"], "services": {}})

        assert "asks for root" in raised.value.message
        details = raised.value.details or ""
        assert "service agent mounts the Docker socket" in details
        assert "noust app sandbox compose-exception stack.example.com --reason" in details

    def test_every_profile_is_resolved_and_the_project_is_the_stacks_own(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        resolved_by(runner, model(web={"image": "nginx"}))
        compose = stack_at(tmp_path, store, runner)
        compose.compose_profiles = ["only-the-one-the-unit-names"]

        compose._check_host_privileges(stack())

        (call,) = runner.calls
        assert call == (
            "docker",
            "compose",
            "-p",
            "stack",
            "-f",
            str(tmp_path / "stack" / "docker-compose.yml"),
            "--profile",
            "*",
            "config",
            "--format",
            "json",
        )

    def test_a_profile_flag_the_compose_in_use_does_not_know_falls_back_to_the_active_ones(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        resolved_by(runner, model(web={"image": "nginx", "privileged": True}))
        path = str(tmp_path / "stack" / "docker-compose.yml")
        runner.script(
            ["docker", "compose", "-p", "stack", "-f", path, "--profile"],
            stderr="no such profile: *",
            exit_code=1,
        )
        compose = stack_at(tmp_path, store, runner)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(stack())

        assert len(runner.calls) == 2
        assert "--profile" not in runner.calls[1]
        # Looking changes nothing, so a dry run may look too (the runner's declared probes).
        assert all(is_read_only(call) for call in runner.calls)

    def test_a_file_that_includes_and_cannot_be_resolved_is_refused_not_guessed(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        resolved_by(runner, {}, exit_code=15)
        compose = stack_at(tmp_path, store, runner)

        with pytest.raises(DeploymentError) as raised:
            compose._check_host_privileges({"include": ["db.yaml"], "services": {"web": {}}})

        assert "cannot be judged" in (raised.value.details or "")
        assert "include" in (raised.value.details or "")

    def test_a_plain_file_that_cannot_be_resolved_is_judged_as_written(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        resolved_by(runner, {}, exit_code=15)
        said: list[str] = []
        compose = stack_at(tmp_path, store, runner)
        compose.logger.attach_sink(said.append)

        compose._check_host_privileges(stack(ports=["80:80"]))

        assert any("could not be resolved" in line for line in said)

    def test_what_the_file_alone_refuses_is_refused_before_compose_reads_anything_else(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        compose = stack_at(tmp_path, store, runner)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges({"include": ["/etc/noust/x.yaml"], "services": {}})

        # An include that leaves the application is not read, not even to say what is in it.
        assert runner.calls == []

    def test_an_exception_lets_the_hidden_service_through_and_says_so(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        build_sandbox.set_compose_exception(
            "stack.example.com", allowed=True, actor="alice", reason="agent", store=store
        )
        runner = FakeRunner()
        resolved_by(runner, model(agent={"image": "x", "volumes": [SOCKET]}))
        said: list[str] = []
        compose = stack_at(tmp_path, store, runner)
        compose.logger.attach_sink(said.append)

        compose._check_host_privileges({"include": ["agent.yaml"], "services": {}})

        assert any("allowed for stack.example.com by alice (agent)" in line for line in said)

    def test_an_existing_stack_is_refused_a_service_an_update_brings_in_through_an_include(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        resolved_by(runner, model(web={"image": "nginx"}, agent={"image": "x", "privileged": True}))
        compose = stack_at(tmp_path, store, runner, new=False)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges({"include": ["agent.yaml"], "services": {}}, accepted=[])

    def test_a_stack_that_ran_with_a_finding_is_warned_about_when_the_model_shows_it_too(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        runner = FakeRunner()
        resolved_by(runner, model(web={"image": "nginx", "volumes": [SOCKET]}))
        said: list[str] = []
        compose = stack_at(tmp_path, store, runner, new=False)
        compose.logger.attach_sink(said.append)
        document = stack(volumes=["/var/run/docker.sock:/sock"])

        compose._check_host_privileges(document, accepted=compose._refusals_in(document))

        assert any("A new stack is refused this" in line for line in said)
