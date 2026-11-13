# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for "how it runs": the forms an application can take on this server, and which one is proposed.

An application runs directly on the server (a service of the system) or in a container. Which of the
two a source *can* be, and why not when it cannot, is one decision (``noust.deployers.how_it_runs``)
that the API, ``noust create --run-as`` and the console all read. What is *proposed* is what Noust has
always done (the first type the registry recognises), so nothing that deployed one way starts to
deploy another.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.central.features import Availability
from noust.central.machine import MachineFacts
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.server_text import say
from noust.core.store import NoustStore
from noust.deployers.how_it_runs import (
    FORM_CONTAINER,
    FORM_SERVER,
    STATUS_HIDDEN,
    STATUS_NO,
    STATUS_YES,
    VIA_COMPOSE,
    VIA_DOCKERFILE,
    VIA_RAILPACK,
    AppRuns,
    Machine,
    TreeReading,
    decide,
    form_of_type,
    machine_here,
    read_tree,
    run_as_conflict,
    runs_of,
)
from noust.deployers.registry import available_types

NODE_PACKAGE_JSON = json.dumps({"name": "shop", "scripts": {"start": "node server.js"}})
GO_MAIN = "package main\n\nfunc main() {}\n"

YES = Availability(True)


def machine(**changes: object) -> Machine:
    """
    An ordinary server: it can run both forms, and Railpack is not installed.

    Args:
        **changes: What differs from that.

    Returns:
        The machine.
    """
    base: dict[str, object] = {
        "server": YES,
        "container": YES,
        "docker_only": False,
        "railpack": False,
    }
    return Machine(**{**base, **changes})  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path):
    """An isolated store: every deployer constructed during detection reads the process-wide one."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "wasm.db")
    yield instance
    NoustStore.reset_instance()


def tree(root: Path, files: dict[str, str]) -> Path:
    """
    Write a repository.

    Args:
        root: Where it goes.
        files: Its files by relative path.

    Returns:
        ``root``.
    """
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


class TestReadingATree:
    """What a repository is, and what it would be if its Dockerfile were not how it runs."""

    def test_node_with_a_dockerfile_is_both(self, tmp_path: Path, store: NoustStore) -> None:
        tree(tmp_path, {"package.json": NODE_PACKAGE_JSON, "Dockerfile": "FROM node:22\n"})

        reading = read_tree(tmp_path)

        assert reading.detected == ("nodejs", "container")
        assert reading.native == ("nodejs",)
        assert reading.dockerfile is True

    def test_go_with_a_dockerfile_is_a_container_that_could_have_run_directly(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        """Go and the other compiled stacks decline a Dockerfile: that is why the page never said."""
        tree(
            tmp_path,
            {"go.mod": "module shop\n\ngo 1.22\n", "main.go": GO_MAIN, "Dockerfile": "FROM go\n"},
        )

        reading = read_tree(tmp_path)

        assert reading.detected == ("container",)
        assert reading.native == ("go",)
        assert reading.dockerfile is True

    def test_the_port_a_dockerfile_exposes_is_read_with_it(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        """A container listens where its Dockerfile says, whatever the language's own default is."""
        tree(
            tmp_path,
            {"package.json": NODE_PACKAGE_JSON, "Dockerfile": "FROM node:22\nEXPOSE 8123\n"},
        )

        assert read_tree(tmp_path).port == 8123

    def test_a_dockerfile_that_exposes_nothing_says_no_port(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        tree(tmp_path, {"package.json": NODE_PACKAGE_JSON, "Dockerfile": "FROM node:22\n"})

        assert read_tree(tmp_path).port is None

    def test_python_without_a_dockerfile_has_one_way(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        tree(tmp_path, {"requirements.txt": "flask\n"})

        reading = read_tree(tmp_path)

        assert reading.detected == ("python",)
        assert reading.native == ("python",)
        assert reading.dockerfile is False

    def test_a_containerfile_is_a_dockerfile(self, tmp_path: Path, store: NoustStore) -> None:
        tree(tmp_path, {"package.json": NODE_PACKAGE_JSON, "Containerfile": "FROM node\n"})

        assert read_tree(tmp_path).dockerfile is True

    def test_a_compose_file_is_not_a_way_to_run_directly(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        tree(tmp_path, {"Dockerfile": "FROM scratch\n", "docker-compose.yml": "services: {}\n"})

        reading = read_tree(tmp_path)

        assert reading.detected[0] == "docker-compose"
        assert reading.native == ()

    def test_reading_never_leaves_the_dockerfile_ignored(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        """The second look is scoped: a deployer asked afterwards still defers to the Dockerfile."""
        tree(
            tmp_path,
            {"go.mod": "module shop\n\ngo 1.22\n", "main.go": GO_MAIN, "Dockerfile": "FROM go\n"},
        )

        read_tree(tmp_path)

        assert read_tree(tmp_path).detected == ("container",)


class TestWhatIsProposed:
    """Nothing that deployed one way is proposed the other: the first type of the registry decides."""

    def test_node_with_a_dockerfile_is_proposed_directly_and_the_container_is_offered(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True)

        decision = decide(reading, machine())

        assert decision.choice is True
        assert decision.proposed == FORM_SERVER
        assert decision.why.code == "how_it_runs.why.server_dockerfile"
        assert decision.server.status == STATUS_YES
        assert decision.server.app_type == "nodejs"
        assert decision.container.status == STATUS_YES
        assert decision.container.app_type == "container"
        assert decision.container.via == "dockerfile"

    def test_go_with_a_dockerfile_is_proposed_as_a_container_and_directly_is_offered(self) -> None:
        reading = TreeReading(("container",), ("go",), dockerfile=True)

        decision = decide(reading, machine())

        assert decision.choice is True
        assert decision.proposed == FORM_CONTAINER
        assert decision.why.code == "how_it_runs.why.container_dockerfile"
        assert decision.why.params["type"] == "Go"
        assert decision.server.app_type == "go"

    def test_python_without_a_dockerfile_or_railpack_can_only_run_directly(self) -> None:
        reading = TreeReading(("python",), ("python",), dockerfile=False)

        decision = decide(reading, machine())

        assert decision.choice is False
        assert decision.proposed == FORM_SERVER
        assert decision.why.code == "how_it_runs.why.server_only"
        assert decision.server.status == STATUS_YES
        assert decision.container.status == STATUS_NO
        assert decision.container.app_type is None
        assert decision.container.reason is not None
        assert decision.container.reason.code == "how_it_runs.container.no_build"

    def test_railpack_makes_the_container_possible_without_a_dockerfile(self) -> None:
        reading = TreeReading(("python",), ("python",), dockerfile=False)

        decision = decide(reading, machine(railpack=True))

        assert decision.choice is True
        assert decision.proposed == FORM_SERVER
        assert decision.why.code == "how_it_runs.why.server_railpack"
        assert decision.container.status == STATUS_YES
        assert decision.container.via == "railpack"

    def test_the_container_way_carries_the_port_the_dockerfile_exposes(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True, port=8123)

        decision = decide(reading, machine())

        assert decision.container.port == 8123
        # What a service listens on is the type's, which the inspection already says.
        assert decision.server.port is None

    def test_the_port_is_said_where_the_machine_cannot_run_a_container_yet_too(self) -> None:
        """What the Dockerfile says is a fact of the repository: it is true on a server without Docker."""
        reading = TreeReading(("container",), (), dockerfile=True, port=8123)
        no_docker = Availability(False, say("docker.engine.missing", command="noust setup init"))

        decision = decide(reading, machine(container=no_docker))

        assert decision.container.status == STATUS_NO
        assert decision.container.port == 8123

    def test_a_compose_file_has_no_port_of_its_own_to_say(self) -> None:
        reading = TreeReading(("docker-compose", "container"), (), dockerfile=True, port=8123)

        assert decide(reading, machine()).container.port is None

    def test_a_container_built_without_a_dockerfile_has_no_port_to_read(self) -> None:
        reading = TreeReading(("python",), ("python",), dockerfile=False, port=8123)

        assert decide(reading, machine(railpack=True)).container.port is None

    def test_a_dockerfile_wins_over_railpack_as_the_way_to_build(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True)

        assert decide(reading, machine(railpack=True)).container.via == "dockerfile"

    def test_a_lone_dockerfile_is_a_container_with_nothing_to_run_directly(self) -> None:
        reading = TreeReading(("container",), (), dockerfile=True)

        decision = decide(reading, machine())

        assert decision.choice is False
        assert decision.proposed == FORM_CONTAINER
        assert decision.why.code == "how_it_runs.why.container_only"
        # Nothing to fix: no program in it runs that way, and the proposal says it.
        assert decision.server.status == STATUS_HIDDEN
        assert decision.server.reason is not None
        assert decision.server.reason.code == "how_it_runs.server.nothing_recognised"

    def test_what_only_railpack_recognises_is_a_container(self) -> None:
        """No deployer of Noust's knows it and Railpack builds it: it is already how it is deployed."""
        reading = TreeReading(("container",), (), dockerfile=False)

        decision = decide(reading, machine(railpack=True))

        assert decision.proposed == FORM_CONTAINER
        assert decision.container.via == "railpack"
        assert decision.why.code == "how_it_runs.why.container_railpack"

    def test_a_compose_file_runs_in_containers_and_says_so(self) -> None:
        reading = TreeReading(("docker-compose", "container"), (), dockerfile=True)

        decision = decide(reading, machine())

        assert decision.choice is False
        assert decision.proposed == FORM_CONTAINER
        assert decision.why.code == "how_it_runs.why.compose"
        assert decision.container.app_type == "docker-compose"
        assert decision.container.via == "compose"

    def test_a_compose_file_next_to_a_framework_leaves_the_framework_as_the_other_way(self) -> None:
        reading = TreeReading(("docker-compose", "nextjs"), ("nextjs",), dockerfile=False)

        decision = decide(reading, machine())

        assert decision.proposed == FORM_CONTAINER
        assert decision.server.app_type == "nextjs"
        assert decision.choice is True


class TestWhatTheMachineCannot:
    """The way that cannot be is said with its reason; one that never can is not offered."""

    def test_a_server_without_docker_says_why_the_container_cannot_be(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True)
        no_docker = Availability(False, say("docker.engine.missing", command="noust setup init"))

        decision = decide(reading, machine(container=no_docker))

        assert decision.choice is False
        assert decision.proposed == FORM_SERVER
        assert decision.container.status == STATUS_NO
        assert decision.container.reason is not None
        assert decision.container.reason.code == "docker.engine.missing"

    def test_a_server_without_docker_proposes_the_way_that_can_run(self) -> None:
        """Go with a Dockerfile is a container today; without Docker it can only run directly."""
        reading = TreeReading(("container",), ("go",), dockerfile=True)
        no_docker = Availability(False, say("docker.engine.missing", command="noust setup init"))

        decision = decide(reading, machine(container=no_docker))

        assert decision.proposed == FORM_SERVER
        assert decision.choice is False
        assert decision.why.code == "how_it_runs.why.server_only"

    def test_a_server_without_a_web_server_says_why_directly_cannot_be(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True)
        no_web = Availability(False, say("features.needs_web_server"))

        decision = decide(reading, machine(server=no_web))

        assert decision.choice is False
        assert decision.proposed == FORM_CONTAINER
        assert decision.server.status == STATUS_NO
        assert decision.server.reason is not None
        assert decision.server.reason.code == "features.needs_web_server"

    def test_a_machine_that_only_has_docker_does_not_offer_running_directly(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True)
        nothing = Availability(False, say("features.needs_systemd"))

        decision = decide(reading, machine(server=nothing, docker_only=True))

        assert decision.choice is False
        assert decision.proposed == FORM_CONTAINER
        assert decision.why.code == "how_it_runs.why.docker_only"
        assert decision.server.status == STATUS_HIDDEN
        assert decision.container.status == STATUS_YES

    def test_a_machine_that_only_has_docker_cannot_build_without_a_dockerfile(self) -> None:
        reading = TreeReading(("python",), ("python",), dockerfile=False)
        nothing = Availability(False, say("features.needs_systemd"))

        decision = decide(reading, machine(server=nothing, docker_only=True))

        assert decision.server.status == STATUS_HIDDEN
        assert decision.container.status == STATUS_NO
        assert decision.container.reason is not None
        assert decision.container.reason.code == "how_it_runs.container.no_build"

    def test_a_machine_that_can_run_neither_proposes_what_it_always_did(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True)
        nothing = Availability(False, say("features.needs_systemd"))

        decision = decide(reading, machine(server=nothing, container=nothing))

        assert decision.choice is False
        assert decision.proposed == FORM_SERVER


class TestWhyEachWayIsSaid:
    """Every sentence a decision can carry exists in the catalog and takes the values it is given."""

    def test_every_reason_and_proposal_is_a_sentence_of_the_catalog(self) -> None:
        readings = [
            TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True),
            TreeReading(("container",), ("go",), dockerfile=True),
            TreeReading(("python",), ("python",), dockerfile=False),
            TreeReading(("container",), (), dockerfile=True),
            TreeReading(("container",), (), dockerfile=False),
            TreeReading(("docker-compose",), (), dockerfile=False),
        ]
        machines = [
            machine(),
            machine(railpack=True),
            machine(container=Availability(False, say("features.needs_root"))),
            machine(server=Availability(False, say("features.needs_systemd")), docker_only=True),
        ]
        for reading in readings:
            for here in machines:
                decision = decide(reading, here)
                assert decision.why.text
                for way in (decision.server, decision.container):
                    if way.reason is not None:
                        assert way.reason.text

    def test_the_decision_can_say_each_way_by_form(self) -> None:
        decision = decide(TreeReading(("nodejs",), ("nodejs",), dockerfile=False), machine())

        assert decision.way(FORM_SERVER) is decision.server
        assert decision.way(FORM_CONTAINER) is decision.container
        with pytest.raises(KeyError):
            decision.way("vm")


class TestWhatThisMachineCan:
    """The machine's abilities come from the table of functions, and Railpack from the machine."""

    def test_an_ordinary_server_runs_both_forms(self, monkeypatch: pytest.MonkeyPatch) -> None:
        here = machine_here(FakeRunner().only_knows("railpack"))

        assert here.server.available and here.container.available
        assert here.docker_only is False
        assert here.railpack is True

    def test_railpack_is_only_there_when_it_is_installed(self) -> None:
        assert machine_here(FakeRunner().only_knows("git")).railpack is False

    def test_a_machine_with_docker_and_no_systemd_only_has_docker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facts = MachineFacts(
            systemd=False, web_server=False, docker=True, root=False, journal=False
        )
        monkeypatch.setattr("noust.central.machine.read_machine_facts", lambda *a, **k: facts)

        here = machine_here(FakeRunner())

        assert here.docker_only is True
        assert here.server.available is False

    def test_a_server_without_docker_says_what_the_doctor_says(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        problem = say("docker.engine.missing", command="noust setup init")
        facts = MachineFacts(
            systemd=True,
            web_server=True,
            docker=False,
            root=True,
            journal=True,
            docker_problem=problem,
        )
        monkeypatch.setattr("noust.central.machine.read_machine_facts", lambda *a, **k: facts)

        here = machine_here(FakeRunner())

        assert here.container.available is False
        assert here.container.reason == problem
        assert here.docker_only is False

    def test_a_hub_runs_neither(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        Config.reset_instance()

        try:
            here = machine_here(FakeRunner())
        finally:
            Config.reset_instance()

        assert not here.server.available and not here.container.available
        assert here.container.reason is not None
        assert here.container.reason.code == "features.hub_role"


class TestHowARunningApplicationRuns:
    """An application's form, from its type and, for a container, how it is built."""

    def test_a_service_runs_directly_on_the_server(self) -> None:
        assert runs_of("nodejs") == AppRuns(FORM_SERVER)
        assert runs_of("static") == AppRuns(FORM_SERVER)

    def test_a_container_says_how_it_is_built(self) -> None:
        assert runs_of("container", "railpack") == AppRuns(FORM_CONTAINER, VIA_RAILPACK)
        assert runs_of("container", "image") == AppRuns(FORM_CONTAINER, "image")
        assert runs_of("container") == AppRuns(FORM_CONTAINER, VIA_DOCKERFILE)

    def test_a_stack_is_containers_from_a_compose_file(self) -> None:
        assert runs_of("docker-compose") == AppRuns(FORM_CONTAINER, VIA_COMPOSE)

    def test_what_noust_adopted_and_does_not_run_says_nothing(self) -> None:
        assert runs_of("adopted") is None
        assert runs_of("unknown") is None

    def test_every_registered_type_is_ruled_on(self) -> None:
        """A type added to the registry is a decision here: a service, or containers."""
        services = {
            "angular", "astro", "deno", "dotnet", "elixir", "go", "java", "monorepo", "nextjs",
            "nodejs", "nuxt", "php-fpm", "python", "remix", "ruby", "rust", "static", "sveltekit",
            "vite",
        }  # fmt: skip
        registered = {entry["type"] for entry in available_types()} - {"auto"}

        assert registered == services | {"container", "docker-compose"}
        assert {form_of_type(app_type) for app_type in services} == {FORM_SERVER}
        assert form_of_type("container") == form_of_type("docker-compose") == FORM_CONTAINER


class TestAFormAskedForAndATypeAskedFor:
    """``--run-as`` and ``--type`` must not say different things."""

    def test_auto_agrees_with_anything(self) -> None:
        assert run_as_conflict("server", "auto") is None
        assert run_as_conflict("container", "auto") is None

    def test_a_type_of_the_form_agrees(self) -> None:
        assert run_as_conflict("server", "go") is None
        assert run_as_conflict("container", "container") is None
        assert run_as_conflict("container", "docker-compose") is None

    def test_a_native_type_asked_to_run_in_a_container_says_how(self) -> None:
        said = run_as_conflict("container", "nodejs")

        assert said is not None
        assert "--type container" in said

    def test_a_container_type_asked_to_run_directly_is_refused(self) -> None:
        said = run_as_conflict("server", "container")

        assert said is not None
        assert "runs in containers" in said


class TestOnTheCentralOfANas:
    """
    A machine that has only Docker and was told to deploy containers on it: what runs there is said true.

    Railpack is not offered here: installing it writes /usr/local/bin as root and its BuildKit frontend
    needs Docker's containerd image store, which a NAS's engine (Synology's 24.0) does not have by default.
    A repository with no Dockerfile and no Compose file therefore runs nowhere on this machine, and it is
    said once, with the one thing that fixes it.
    """

    @staticmethod
    def host(**changes: object) -> Machine:
        nothing = Availability(False, say("features.containers_only", feature="Applications"))
        return machine(server=nothing, docker_only=True, container_host=True, **changes)

    def test_the_machine_says_it_is_one_and_runs_only_the_container_form(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facts = MachineFacts(
            systemd=False, web_server=False, docker=True, root=False, journal=False
        )
        monkeypatch.setattr("noust.central.machine.read_machine_facts", lambda *a, **k: facts)
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
        Config.reset_instance()

        try:
            here = machine_here(FakeRunner().only_knows("railpack"))
        finally:
            Config.reset_instance()

        assert here.container_host is True
        assert here.container.available is True
        assert here.server.available is False
        assert here.server.reason is not None
        assert here.server.reason.code == "features.containers_only"
        # Even a Railpack somebody put on the machine is not offered: it cannot build here.
        assert here.railpack is False

    def test_a_hub_that_was_not_told_to_deploy_here_is_not_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facts = MachineFacts(
            systemd=False, web_server=False, docker=True, root=False, journal=False
        )
        monkeypatch.setattr("noust.central.machine.read_machine_facts", lambda *a, **k: facts)
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
        Config.reset_instance()

        try:
            here = machine_here(FakeRunner())
        finally:
            Config.reset_instance()

        assert here.container_host is False and here.container.available is False

    def test_a_dockerfile_runs_as_a_container_and_nothing_else_is_offered(self) -> None:
        reading = TreeReading(("nodejs", "container"), ("nodejs",), dockerfile=True)

        decision = decide(reading, self.host())

        assert decision.choice is False
        assert (decision.proposed, decision.why.code) == (
            FORM_CONTAINER,
            "how_it_runs.why.docker_only",
        )
        assert decision.container.status == STATUS_YES and decision.server.status == STATUS_HIDDEN

    def test_a_compose_file_runs(self) -> None:
        decision = decide(TreeReading(("docker-compose",), (), dockerfile=False), self.host())

        assert decision.container.status == STATUS_YES
        assert decision.container.via == VIA_COMPOSE

    def test_a_repository_with_no_dockerfile_does_not_propose_railpack_because_it_cannot_build(
        self,
    ) -> None:
        decision = decide(TreeReading(("python",), ("python",), dockerfile=False), self.host())

        reason = decision.container.reason
        assert decision.container.status == STATUS_NO and reason is not None
        assert reason.code == "how_it_runs.container.no_build_here"
        said = f"{reason.text} {reason.hint}"
        assert "Dockerfile" in said and "Railpack" not in said and "railpack" not in said
        assert decision.container.via is None and decision.container.app_type is None

    def test_when_no_way_is_possible_it_is_said_with_a_code_of_its_own_and_one_fix(self) -> None:
        decision = decide(TreeReading(("python",), ("python",), dockerfile=False), self.host())

        assert decision.choice is False
        assert STATUS_YES not in (decision.server.status, decision.container.status)
        assert decision.why.code == "how_it_runs.why.nothing_runs_here"
        assert decision.why.hint is not None and "Dockerfile" in decision.why.hint
        assert "Railpack" not in decision.why.text + decision.why.hint

    def test_what_a_source_with_nothing_recognised_runs_as_is_said_the_same(self) -> None:
        decision = decide(TreeReading((), (), dockerfile=False), self.host())

        assert decision.why.code == "how_it_runs.why.nothing_runs_here"

    def test_a_server_keeps_proposing_railpack_where_it_is_installed_and_its_old_sentence(
        self,
    ) -> None:
        reading = TreeReading(("python",), ("python",), dockerfile=False)
        nothing = Availability(False, say("features.needs_systemd"))

        decision = decide(reading, machine(server=nothing, docker_only=True))

        assert decision.container.reason is not None
        assert decision.container.reason.code == "how_it_runs.container.no_build"
        assert decision.why.code == "how_it_runs.why.none"
