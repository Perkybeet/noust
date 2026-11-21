# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The start of a Compose stack is held to what was judged.

The unit of a stack runs ``docker compose up`` at every start (and ``down`` and ``up`` again at every
restart) with the files and the variables that are there at that moment. Between a deployment and a
start, whoever can write the stack's ``.env`` or a file of its tree, and restart it, changes what Compose
resolves without anybody judging it. The gate resolves the model as it is now, with the same function that
judges a deployment, and compares it with what the stack was accepted with: what adds something that gives
the server is held back, with the line that is new and the two ways out; what a stack that was never
recorded does is what it always did.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import compose_acceptance
from noust.core.exceptions import DeploymentError, StartHeldBackError
from noust.core.runner import FakeRunner
from noust.core.store import App, AppStatus, AppType, NoustStore
from noust.deployers import compose_gate
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import sandbox
from noust.deployers.helpers.compose_guard import inspect_compose

DOMAIN = "stack.example.com"
FILE = "services:\n  web:\n    image: nginx\n    volumes:\n      - ${DATA:-./data}:/data\n"


def model(source: str) -> dict[str, Any]:
    """What ``docker compose config`` prints for the file when ``DATA`` is ``source``."""
    return {
        "name": "stack-example-com",
        "services": {
            "web": {
                "image": "nginx",
                "volumes": [{"type": "bind", "source": source, "target": "/data"}],
            }
        },
    }


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path) -> Path:
    path = tmp_path / "stack-example-com"
    path.mkdir()
    (path / "docker-compose.yml").write_text(FILE)
    return path


def stack(app: Path, store: NoustStore, printed: dict[str, Any] | None) -> DockerComposeDeployer:
    """A deployer of the stack whose Compose prints ``printed`` (or fails)."""
    runner = FakeRunner()
    if printed is None:
        runner.script(["docker", "compose"], stderr="invalid compose project", exit_code=1)
    else:
        runner.script(["docker", "compose"], stdout=json.dumps(printed))
    deployer = DockerComposeDeployer(runner=runner)
    deployer.configure(DOMAIN, "src", app_path=app)
    deployer.store = store
    deployer._stored_project = "stack-example-com"
    return deployer


def accepted_with(app: Path, store: NoustStore, printed: dict[str, Any]) -> None:
    """Write down what the stack is given when the model is ``printed``."""
    findings = inspect_compose(printed, app)
    compose_acceptance.record(
        DOMAIN, [findings.keys[s] for s in findings.refused], source="deploy", store=store
    )


class TestWhatTheGateAllows:
    def test_a_stack_nobody_recorded_starts_as_it_always_did(
        self, app: Path, store: NoustStore
    ) -> None:
        outcome = compose_gate.judge(stack(app, store, model("/")))

        assert outcome.allowed and outcome.unrecorded

    def test_a_stack_that_resolves_to_what_it_was_accepted_with_starts(
        self, app: Path, store: NoustStore
    ) -> None:
        accepted_with(app, store, model(str(app / "data")))

        outcome = compose_gate.judge(stack(app, store, model(str(app / "data"))))

        assert outcome.allowed and not outcome.unrecorded

    def test_a_stack_that_was_accepted_with_a_mount_of_the_server_keeps_starting(
        self, app: Path, store: NoustStore
    ) -> None:
        # What 4.2 started and 4.3 took over is what it was given: it starts as it did.
        accepted_with(app, store, model("/srv/shared"))

        outcome = compose_gate.judge(stack(app, store, model("/srv/shared")))

        assert outcome.allowed

    def test_what_an_exception_covers_starts(self, app: Path, store: NoustStore) -> None:
        accepted_with(app, store, model(str(app / "data")))
        sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="admin", reason="it needs it", store=store
        )

        outcome = compose_gate.judge(stack(app, store, model("/srv/shared")))

        assert outcome.allowed


class TestWhatTheGateHoldsBack:
    def test_a_variable_changed_to_lead_to_the_root_does_not_start(
        self, app: Path, store: NoustStore
    ) -> None:
        accepted_with(app, store, model(str(app / "data")))

        outcome = compose_gate.judge(stack(app, store, model("/")))

        assert not outcome.allowed
        assert any("service web mounts /" in line for line in outcome.lines)

    def test_it_says_what_is_new_and_the_two_ways_out(self, app: Path, store: NoustStore) -> None:
        accepted_with(app, store, model(str(app / "data")))

        outcome = compose_gate.judge(stack(app, store, model("/")))
        error = outcome.error()

        assert isinstance(error, DeploymentError)
        assert "was not started" in error.message
        assert "service web mounts /" in (error.details or "")
        assert f"noust update {DOMAIN}" in (error.details or "")
        assert f"noust app sandbox compose-exception {DOMAIN}" in (error.details or "")

    def test_what_was_already_accepted_is_not_listed_as_new(
        self, app: Path, store: NoustStore
    ) -> None:
        accepted_with(app, store, model("/srv/shared"))
        both = model("/srv/shared")
        both["services"]["web"]["privileged"] = True

        outcome = compose_gate.judge(stack(app, store, both))

        assert not outcome.allowed
        assert not any("/srv/shared" in line for line in outcome.lines)
        assert any("privileged" in line for line in outcome.lines)

    def test_a_stack_that_cannot_be_resolved_does_not_start_when_it_brings_files_in(
        self, app: Path, store: NoustStore
    ) -> None:
        (app / "docker-compose.yml").write_text(
            "include:\n  - deploy/part.yaml\nservices:\n  web:\n    image: nginx\n"
        )
        compose_acceptance.record(DOMAIN, [], source="deploy", store=store)

        outcome = compose_gate.judge(stack(app, store, None))

        assert not outcome.allowed

    def test_a_file_that_is_not_there_does_not_start(self, app: Path, store: NoustStore) -> None:
        (app / "docker-compose.yml").unlink()
        compose_acceptance.record(DOMAIN, [], source="deploy", store=store)

        outcome = compose_gate.judge(stack(app, store, model("/")))

        assert not outcome.allowed
        assert "could not be judged" in outcome.error().message

    def test_a_refusal_is_what_the_operator_finds_on_health(
        self, app: Path, store: NoustStore
    ) -> None:
        accepted_with(app, store, model(str(app / "data")))

        compose_gate.judge(stack(app, store, model("/")), note=True)

        note = compose_acceptance.note_of(DOMAIN, store=store)
        assert (
            note is not None and note.state == "refused" and "service web mounts /" in note.detail
        )

    def test_and_a_start_that_passes_takes_it_away(self, app: Path, store: NoustStore) -> None:
        accepted_with(app, store, model(str(app / "data")))
        compose_gate.judge(stack(app, store, model("/")), note=True)

        compose_gate.judge(stack(app, store, model(str(app / "data"))), note=True)

        assert compose_acceptance.note_of(DOMAIN, store=store) is None

    def test_a_note_of_a_stack_not_brought_up_to_date_is_not_taken_away_by_a_start(
        self, app: Path, store: NoustStore
    ) -> None:
        compose_acceptance.note(DOMAIN, "pending", "Compose said no", store=store)

        compose_gate.judge(stack(app, store, model("/")), note=True)

        note = compose_acceptance.note_of(DOMAIN, store=store)
        assert note is not None and note.state == "pending"


def make_app(store: NoustStore, path: Path) -> None:
    store.create_app(
        App(
            domain=DOMAIN,
            app_type=AppType.DOCKER_COMPOSE.value,
            source="src",
            app_path=str(path),
            status=AppStatus.RUNNING.value,
        )
    )


class TestBeforeSystemdIsAsked:
    """``ServiceManager`` and the supervisor ask first, so a refusal leaves the stack as it was."""

    def patched(self, monkeypatch: pytest.MonkeyPatch, outcome: compose_gate.Outcome) -> list[str]:
        asked: list[str] = []

        def judge_app(found: App, **_: Any) -> compose_gate.Outcome:
            asked.append(found.domain)
            return outcome

        monkeypatch.setattr(compose_gate, "judge_app", judge_app)
        return asked

    def test_a_refusal_is_raised_before_anything_is_asked_of_systemd(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_app(store, app)
        refused = compose_gate.Outcome(False, lines=("- service web mounts /",))
        self.patched(monkeypatch, refused)

        with pytest.raises(StartHeldBackError, match="was not started"):
            compose_gate.check_before("stack-example-com", store=store)

    def test_a_stack_that_passes_is_let_through(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_app(store, app)
        asked = self.patched(monkeypatch, compose_gate.Outcome(True))

        compose_gate.check_before("stack-example-com", store=store)

        assert asked == [DOMAIN]

    def test_a_unit_that_is_no_compose_stack_is_not_asked_about(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.create_app(
            App(
                domain="plain.example.com",
                app_type=AppType.NODEJS.value,
                source="src",
                app_path=str(app),
                status=AppStatus.RUNNING.value,
            )
        )
        asked = self.patched(monkeypatch, compose_gate.Outcome(False))

        compose_gate.check_before("plain-example-com", store=store)
        compose_gate.check_before("nginx", store=store)

        assert asked == []

    def test_a_stack_that_is_being_deployed_has_no_row_to_ask_about(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked = self.patched(monkeypatch, compose_gate.Outcome(False))

        compose_gate.check_before("stack-example-com", store=store)

        assert asked == []


class TestAServiceStartedAlone:
    """A restore brings the database's service up alone, and Compose resolves the whole file for it."""

    def test_a_restore_is_held_back_before_the_service_is_started(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import stack_databases
        from noust.managers.stack_databases import StackDatabase, restore_stack_database

        # Its service has no container: the restore is what would start it.
        monkeypatch.setattr(stack_databases, "_is_running", lambda *_a: False)

        accepted_with(app, store, model(str(app / "data")))
        deployer = stack(app, store, model("/"))
        database = StackDatabase(service="db", engine="postgres", database="app", user="app")

        with pytest.raises(StartHeldBackError, match="was not started"):
            restore_stack_database(deployer, database, app / "dump.sql")

        assert not [call for call in deployer.runner.calls if "up" in call]  # type: ignore[attr-defined]
