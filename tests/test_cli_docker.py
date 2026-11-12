# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust docker``: the images in use and the stacks, from the command line.

Every command is a client of what the console uses (the gate, the update planner, the bringing of a stack, the
Compose editor), so what is held here is what it presents and the exit status that goes with it: a command a
refusal suggests runs as written, a refusal says why and what to do and exits 1, an update goes through the
application's update, and a document that is refused is not applied.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore
from noust.recipes.deploy import COMPOSE_FILE, recipe_source_dir
from noust.web.auth import STATE_DIR_ENV
from tests.support.compose_document import APP_NAME, DOCUMENT, DOMAIN, NEWER, make_stack
from tests.support.docker_bring import Adoption
from tests.support.docker_machine import DockerMachine, RemoteTag

A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
PROJECT = "stack-example-com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))


@pytest.fixture
def machine(store: NoustStore, tmp_path: Path) -> Iterator[DockerMachine]:
    """A machine with an application's stack, a stack nobody made and a container on its own."""
    stack = make_stack(store, tmp_path)
    box = DockerMachine()
    image = box.image("1", "nginx", "1.27")
    box.box(
        "stack-web-1",
        image="nginx:1.27",
        project=PROJECT,
        service="web",
        working_dir=stack.app_path,
        policy="unless-stopped",
    )
    box.facts(image, repo_digests=[f"nginx@{A}"])
    other = tmp_path / "stacks" / "nextcloud"
    other.mkdir(parents=True)
    (other / "compose.yaml").write_text("services: {}\n")
    box.box(
        "nextcloud-app-1",
        image="nextcloud:29",
        project="nextcloud",
        service="app",
        working_dir=str(other),
        config_files=str(other / "compose.yaml"),
        policy="unless-stopped",
    )
    adminer = box.image("2", "adminer", "latest")
    box.box("adminer", image="adminer:latest", policy="unless-stopped")
    box.facts(adminer, repo_digests=[f"adminer@{A}"])
    box.remote["docker.io/library/nginx:1.27"] = RemoteTag(B)
    box.remote["docker.io/library/nginx:1.28"] = RemoteTag(B)
    box.remote["docker.io/library/adminer:latest"] = RemoteTag(B)
    box.dry_run_output = " ✔ DRY-RUN MODE -  Container stack-web-1  Recreated   0.0s\n"
    set_runner(box)
    yield box
    set_runner(None)


def run(*args: str, code: int = 0, input: str | None = None) -> str:
    result = CliRunner().invoke(root_cli, ["docker", *args], input=input)
    assert result.exit_code == code, f"{result.output} {result.exception!r}"
    return f"{result.output}{result.exception or ''}"


def record(reference: str, change: str = "patch") -> None:
    ImageVersionStore().save(
        VersionRecord(
            reference,
            "newer",
            A,
            B,
            None,
            None,
            None,
            change,
            False,
            "imagetools",
            "",
            "2026-11-12T10:00:00+00:00",
        )
    )


class FakeUpdate:
    def __init__(self) -> None:
        self.called: list[dict[str, Any]] = []

    def update_app(self, domain: str, **kwargs: Any) -> Any:
        self.called.append({"domain": domain, **kwargs})
        return type("Outcome", (), {"deployment_id": 41, "warnings": None})()

    def put_stack_back(self, *args: Any, **kwargs: Any) -> None:
        pass


@pytest.fixture
def update(monkeypatch: pytest.MonkeyPatch) -> FakeUpdate:
    fake = FakeUpdate()
    monkeypatch.setattr("noust.deployers.lifecycle.update_app", fake.update_app)
    monkeypatch.setattr("noust.recipes.updates.put_stack_back", fake.put_stack_back)
    return fake


class TestImages:
    def test_the_list_says_what_the_last_look_found_and_what_updating_does(
        self, machine: DockerMachine
    ) -> None:
        record("adminer:latest")

        body = json.loads(run("images", "list", "--json"))

        found = {item["tags"][0]: item["update"] for item in body}
        assert found["adminer:latest"]["mode"] == "loose"
        assert found["nginx:1.27"]["state"] == "not_looked"

    def test_looking_asks_the_registries_and_says_what_it_found(
        self, machine: DockerMachine
    ) -> None:
        out = run("images", "check")

        assert "adminer:latest" in out and "new version" in out
        assert (
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            "docker.io/library/adminer:latest",
        ) in machine.calls

    def test_looking_is_what_a_refusal_suggests_and_runs_as_written(
        self, machine: DockerMachine
    ) -> None:
        refused = run("images", "update", "nginx:1.27", code=1)

        assert "noust docker images check" in refused
        run("images", "check")
        assert "new version" in run("images", "plan", "nginx:1.27")

    def test_what_nobody_made_is_not_updated_and_says_why(
        self, machine: DockerMachine, update: FakeUpdate
    ) -> None:
        record("adminer:latest")

        out = run("images", "update", "adminer:latest", "--yes", code=1)

        assert "does not know how it was created" in out
        assert update.called == []

    def test_an_image_of_an_application_goes_through_the_applications_update(
        self, machine: DockerMachine, update: FakeUpdate
    ) -> None:
        record("nginx:1.27")

        out = run("images", "update", "nginx:1.27", "--yes")

        assert [(c["domain"], c["trigger"]) for c in update.called] == [(DOMAIN, "cli")]
        assert DOMAIN in out

    def test_it_asks_first_and_does_nothing_when_told_no(
        self, machine: DockerMachine, update: FakeUpdate
    ) -> None:
        record("nginx:1.27")

        result = CliRunner().invoke(
            root_cli, ["docker", "images", "update", "nginx:1.27"], input="n\n"
        )

        assert result.exit_code != 0
        assert update.called == []

    def test_a_major_version_is_taken_on_purpose(
        self, machine: DockerMachine, update: FakeUpdate
    ) -> None:
        record("nginx:1.27", change="major")

        refused = run("images", "update", "nginx:1.27", "--yes", code=1)
        run("images", "update", "nginx:1.27", "--yes", "--accept-major")

        assert "major" in refused
        assert len(update.called) == 1


class TestStacks:
    def test_a_stack_nobody_made_is_stopped_started_and_restarted_as_a_whole(
        self, machine: DockerMachine
    ) -> None:
        run("stack", "stop", "nextcloud")
        assert machine.boxes["nextcloud-app-1"].state == "exited"
        run("stack", "start", "nextcloud")
        run("stack", "restart", "nextcloud")

        assert machine.boxes["nextcloud-app-1"].state == "running"

    def test_what_an_application_owns_is_changed_through_it(self, machine: DockerMachine) -> None:
        out = run("stack", "stop", PROJECT, code=1)

        assert f"noust stop {DOMAIN}" in out
        assert machine.boxes["stack-web-1"].state == "running"

    def test_the_list_says_whose_each_is_and_what_may_be_done(self, machine: DockerMachine) -> None:
        body = {item["name"]: item for item in json.loads(run("stack", "list", "--json"))}

        assert body["nextcloud"]["can"]["stop"] is True
        assert body[PROJECT]["can"]["stop"] is False

    def test_bringing_says_what_it_registers_and_registers_nothing_on_preview(
        self, machine: DockerMachine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        out = run("stack", "bring", "nextcloud", "--preview")

        assert "changes nothing" in out
        assert adoption.adopted == []

    def test_bringing_registers_it_as_the_name_given(
        self, machine: DockerMachine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adoption = Adoption(monkeypatch)

        out = run("stack", "bring", "nextcloud", "--name", "cloud", "--yes")

        assert [plan.domain for plan in adoption.adopted] == ["cloud"]
        assert "cloud" in out

    def test_a_stack_that_is_already_nousts_is_not_brought(
        self, machine: DockerMachine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Adoption(monkeypatch)

        out = run("stack", "bring", PROJECT, "--yes", code=1)

        assert DOMAIN in out


class TestCompose:
    def write(self, tmp_path: Path, text: str) -> str:
        path = tmp_path / "new.yml"
        path.write_text(text)
        return str(path)

    def kept(self, store: NoustStore) -> str:
        return (recipe_source_dir(APP_NAME, store) / COMPOSE_FILE).read_text()

    def test_the_document_is_printed_as_it_is(self, machine: DockerMachine) -> None:
        assert run("stack", "compose", "show", PROJECT) == DOCUMENT

    def test_a_stack_whose_document_is_not_here_says_whose_it_is(
        self, machine: DockerMachine
    ) -> None:
        out = run("stack", "compose", "show", "nextcloud", code=1)

        assert "noust docker stack bring nextcloud" in out

    def test_checking_says_what_changes_and_writes_nothing(
        self, machine: DockerMachine, store: NoustStore, tmp_path: Path
    ) -> None:
        out = run("stack", "compose", "check", PROJECT, self.write(tmp_path, NEWER))

        assert "+    image: nginx:1.28" in out
        assert "recreate container web" in out
        assert self.kept(store) == DOCUMENT

    def test_a_document_that_does_not_pass_exits_1_with_composes_words(
        self, machine: DockerMachine, tmp_path: Path
    ) -> None:
        broken = DOCUMENT.replace('- "127.0.0.1:8080:80"', '- ["127.0.0.1:8080:80"')

        out = run("stack", "compose", "check", PROJECT, self.write(tmp_path, broken), code=1)

        assert "did not find expected" in out

    def test_applying_keeps_a_version_and_runs_the_applications_update(
        self, machine: DockerMachine, store: NoustStore, tmp_path: Path, update: FakeUpdate
    ) -> None:
        run(
            "stack",
            "compose",
            "apply",
            PROJECT,
            self.write(tmp_path, NEWER),
            "--reason",
            "Use 1.28",
            "--yes",
        )

        assert self.kept(store) == NEWER
        assert [c["domain"] for c in update.called] == [DOMAIN]
        history = run("stack", "compose", "history", PROJECT)
        assert "Use 1.28" in history and "found" in history

    def test_a_reason_is_needed(self, machine: DockerMachine, tmp_path: Path) -> None:
        run("stack", "compose", "apply", PROJECT, self.write(tmp_path, NEWER), "--yes", code=2)

    def test_what_putting_a_version_back_would_change_is_shown_and_it_is_put_back(
        self, machine: DockerMachine, store: NoustStore, tmp_path: Path, update: FakeUpdate
    ) -> None:
        run(
            "stack",
            "compose",
            "apply",
            PROJECT,
            self.write(tmp_path, NEWER),
            "--reason",
            "x",
            "--yes",
        )

        shown = run("stack", "compose", "diff", PROJECT, "1")
        run("stack", "compose", "restore", PROJECT, "1", "--yes")

        assert "-    image: nginx:1.28" in shown
        assert self.kept(store) == DOCUMENT


def test_the_help_lists_the_group() -> None:
    result = CliRunner().invoke(root_cli, ["docker", "--help"])

    assert result.exit_code == 0
    assert "images" in result.output and "stack" in result.output


def test_every_command_a_refusal_suggests_exists() -> None:
    for words in (
        ["docker", "images", "check", "--help"],
        ["docker", "stack", "bring", "--help"],
    ):
        assert CliRunner().invoke(root_cli, words).exit_code == 0


_ = App
