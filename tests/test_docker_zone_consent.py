# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A hub that has Docker's socket and was not told to deploy here leaves the machine's Docker alone.

The guide to deploying where the central runs promises that without ``central.deploy_here`` a central
is exactly what it was, and the socket alone is not the second consent: the zone (reading what runs on
the machine and changing it) is the machine's Docker, and what is in it is changed as root. So the
gate to Docker, the one door every read and change of the zone goes through, refuses on a hub that
was not told, before a process is started, and the table of functions says the same thing in the
session. A server is not a hub and the zone is what it was; so is a hub that was told.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from noust import central
from noust.central import features, machine
from noust.central.features import FEATURES, Need
from noust.central.machine import MachineFacts, read_machine_facts
from noust.core.central_settings import machine_docker_is_open
from noust.core.config import Config
from noust.core.docker.errors import DockerUnavailableError, DockerZoneClosedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import Registry
from noust.core.exceptions import RoleError
from noust.core.server_text import CATALOG
from noust.core.store import NoustStore
from noust.core.teams import Scope
from noust.web.api import docker as docker_api
from noust.web.jobs import JobManager
from tests.support.docker_machine import DockerMachine
from tests.support.docker_web import DockerWorld, build

DOCKER = MachineFacts(systemd=False, web_server=False, docker=True, root=False, journal=False)


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    NoustStore.reset_instance()
    machine.reset_machine_facts()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()
    machine.reset_machine_facts()


def configure(monkeypatch: pytest.MonkeyPatch, *, role: str, deploy_here: str = "") -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", role)
    if deploy_here:
        monkeypatch.setenv("NOUST_DEPLOY_HERE", deploy_here)
    Config.reset_instance()


def a_gate(docker: DockerMachine) -> DockerGate:
    return DockerGate(docker, registry=lambda: Registry(), scope=Scope.everything())


def a_machine() -> DockerMachine:
    docker = DockerMachine()
    docker.box("portainer", image="portainer/portainer-ce:latest", mounts="portainer_data")
    docker.volume("portainer_data")
    return docker


#: Every way the zone looks at the machine's Docker or changes it, as the console, the command
#: line and the jobs call them.
ASKS: dict[str, Callable[[DockerGate], Any]] = {
    "containers": lambda g: g.containers(),
    "images": lambda g: g.images(),
    "volumes": lambda g: g.volumes(),
    "networks": lambda g: g.networks(),
    "projects": lambda g: g.projects(),
    "engine": lambda g: g.engine(),
    "doctor": lambda g: g.doctor(),
    "summary": lambda g: g.summary(),
    "restart_loss": lambda g: g.restart_loss(),
    "cleanup_plan": lambda g: g.cleanup_plan(),
    "container": lambda g: g.container("portainer"),
    "start_container": lambda g: g.start_container("portainer"),
    "stop_container": lambda g: g.stop_container("portainer"),
    "remove_container": lambda g: g.remove_container("portainer"),
    "remove_volume": lambda g: g.remove_volume("portainer_data"),
}


class TestTheGateOfAHubThatWasNotTold:
    @pytest.mark.parametrize("ask", sorted(ASKS))
    def test_nothing_of_the_zone_reaches_the_machines_docker(
        self, ask: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub")
        docker = a_machine()

        with pytest.raises(DockerZoneClosedError):
            ASKS[ask](a_gate(docker))

        assert docker.calls == []

    def test_what_it_says_is_the_table_s_sentence_and_what_to_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub")

        with pytest.raises(DockerZoneClosedError) as caught:
            a_gate(a_machine()).containers()

        refusal = features.availability("docker_zone", role="hub", facts=DOCKER).reason
        assert refusal is not None
        assert caught.value.coded == refusal
        assert caught.value.message == refusal.text
        assert "NOUST_DEPLOY_HERE=containers" in caught.value.details
        assert refusal.code in CATALOG

    def test_it_is_a_refusal_of_the_role_and_a_docker_that_is_not_there_for_this_central(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whoever already handles a Docker that cannot be used handles this, and the API says hub."""
        configure(monkeypatch, role="hub")

        with pytest.raises(DockerZoneClosedError) as caught:
            a_gate(a_machine()).images()

        assert isinstance(caught.value, RoleError)
        assert isinstance(caught.value, DockerUnavailableError)
        assert isinstance(caught.value, central.RoleError)

    def test_a_hub_that_was_told_uses_the_machines_docker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub", deploy_here="containers")
        docker = a_machine()

        found = a_gate(docker).containers().items

        assert [c.name for c in found] == ["portainer"]
        assert docker.calls

    def test_a_server_uses_it_as_it_always_did(self) -> None:
        docker = a_machine()

        found = a_gate(docker).containers().items

        assert [c.name for c in found] == ["portainer"]

    def test_the_consent_alone_on_a_server_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="server", deploy_here="containers")

        assert [c.name for c in a_gate(a_machine()).containers().items] == ["portainer"]


class TestTheTableAndTheGateAreOneDecision:
    @pytest.mark.parametrize("role", ["server", "hub"])
    @pytest.mark.parametrize("deploy_here", ["", "containers"])
    def test_what_is_published_is_what_the_gate_lets_through(
        self, role: str, deploy_here: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role=role, deploy_here=deploy_here)
        published = features.availability(
            "docker_zone", role=role, facts=DOCKER, deploy_here=deploy_here
        )

        try:
            a_gate(a_machine()).containers()
            open_ = True
        except DockerZoneClosedError:
            open_ = False

        assert published.available is open_
        assert machine_docker_is_open(role, deploy_here) is open_

    def test_the_zone_needs_the_consent_of_a_hub_and_then_only_docker(self) -> None:
        zone = FEATURES["docker_zone"]

        assert zone.needs == (Need.DEPLOY_HERE, Need.DOCKER)
        for role, deploy_here, expected in (
            ("server", "", True),
            ("server", "containers", True),
            ("hub", "containers", True),
            ("hub", "", False),
        ):
            answer = features.availability(
                "docker_zone", role=role, facts=DOCKER, deploy_here=deploy_here
            )
            assert answer.available is expected, (role, deploy_here)

    def test_a_hub_that_was_not_told_hears_why_in_one_sentence_and_not_the_one_for_a_missing_docker(
        self,
    ) -> None:
        answer = features.availability("docker_zone", role="hub", facts=DOCKER)

        assert answer.reason is not None
        assert answer.reason.code == "features.needs_deploy_here"
        assert answer.reason.params == {"feature": FEATURES["docker_zone"].label}
        assert answer.reason.hint and "NOUST_DEPLOY_HERE" in answer.reason.hint

    def test_a_hub_that_was_told_and_has_no_docker_hears_the_doctor(self) -> None:
        none = MachineFacts(
            systemd=False, web_server=False, docker=False, root=False, journal=False
        )

        answer = features.availability(
            "docker_zone", role="hub", facts=none, deploy_here=features.CONTAINERS
        )

        assert answer.reason is not None
        assert answer.reason.code == "docker.engine.missing"

    def test_a_server_without_docker_hears_the_doctor_as_it_did(self) -> None:
        none = MachineFacts(systemd=True, web_server=True, docker=False, root=True, journal=True)

        answer = features.availability("docker_zone", role="server", facts=none)

        assert answer.reason is not None
        assert answer.reason.code == "docker.engine.missing"


class TestTheSessionDoesNotAskTheMachinesDocker:
    @pytest.fixture
    def asked(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """Records every look at the engine the session makes, and answers that there is none."""
        looks: list[str] = []

        def ask(runner: Any) -> Any:
            from noust.core.docker import engine

            looks.append("engine")
            return engine.diagnose(engine.EngineInfo(installed=False))

        # The suite gives every test a machine that has everything; the session is read for real here.
        monkeypatch.setattr(machine, "read_machine_facts", read_machine_facts)
        monkeypatch.setattr(machine, "_ask_docker", ask)
        monkeypatch.setattr(machine, "detect_platform", lambda *a, **k: _Platform())
        monkeypatch.setattr(machine, "_web_server", lambda runner: False)
        return looks

    def test_a_hub_that_was_not_told_is_not_looked_at_for_the_session(
        self, asked: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role="hub")

        states = central.feature_states()

        assert asked == []
        assert states["docker_zone"]["available"] is False
        assert states["docker_zone"]["reason"]["code"] == "features.needs_deploy_here"

    @pytest.mark.parametrize(("role", "here"), [("hub", "containers"), ("server", "")])
    def test_a_hub_that_was_told_and_a_server_are(
        self, role: str, here: str, asked: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configure(monkeypatch, role=role, deploy_here=here)

        central.feature_states()

        assert asked == ["engine"]


class _Platform:
    systemd = False
    updates_supported = True

    def updates_unsupported(self) -> None:
        return None


class TestTheApiOfAHubThatWasNotTold:
    @pytest.fixture
    def docker(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DockerWorld]:
        JobManager.reset_instance()
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        Config.reset_instance()
        built = build(tmp_path, monkeypatch)
        try:
            yield built
        finally:
            docker_api.reset_engine_cache()
            JobManager.reset_instance()
            NoustStore.reset_instance()

    @pytest.mark.parametrize(
        "path",
        [
            "/summary",
            "/engine",
            "/containers",
            "/images",
            "/volumes",
            "/networks",
            "/stacks",
            "/cleanup/plan",
            "/containers/adminer",
        ],
    )
    def test_every_read_is_a_conflict_that_says_why_and_asks_docker_nothing(
        self, docker: DockerWorld, path: str
    ) -> None:
        before = len(docker.machine.calls)

        response = docker.get("master", path)

        assert response.status_code == 409, response.text
        assert response.json()["error"] == "hub_role"
        assert "NOUST_DEPLOY_HERE" in response.json()["hint"]
        assert len(docker.machine.calls) == before

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/containers/adminer/start"),
            ("POST", "/containers/adminer/stop"),
            ("DELETE", "/containers/adminer"),
            ("DELETE", "/volumes/wiki_data"),
        ],
    )
    def test_every_change_is_refused_before_a_process_starts(
        self, docker: DockerWorld, method: str, path: str
    ) -> None:
        before = len(docker.machine.calls)

        response = docker.send("master", method, path)

        assert response.status_code == 409, response.text
        assert docker.machine.changes() == []
        assert len(docker.machine.calls) == before


class TestWhatStillAsksDockerByItselfDoesNotAskEither:
    """The disk page asks ``docker system df`` through its own runner, not through the gate."""

    def storage(self, docker: DockerMachine, tmp_path: Path) -> Any:
        from noust.core.fs import RecordingFileSystem
        from noust.managers.server.storage import StorageManager
        from tests.support.server_support import make_host, platform_for

        return StorageManager(
            runner=docker,
            host=make_host(tmp_path / "root"),
            platform=platform_for("apt"),
            fs=RecordingFileSystem(),
            release_apps=lambda: [],
            backups_usage=lambda: {},
            log_paths=lambda: [],
            registry=lambda: Registry(),
        )

    def test_a_hub_that_was_not_told_reads_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        configure(monkeypatch, role="hub")
        docker = a_machine()

        assert self.storage(docker, tmp_path).docker_df() is None
        assert self.storage(docker, tmp_path).docker_images() == ([], [])
        assert docker.calls == []

    def test_a_server_and_a_hub_that_was_told_read_as_they_did(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for role, here in (("server", ""), ("hub", "containers")):
            configure(monkeypatch, role=role, deploy_here=here)
            docker = a_machine()

            self.storage(docker, tmp_path).docker_df()

            assert any(call[:3] == ("docker", "system", "df") for call in docker.calls), role
