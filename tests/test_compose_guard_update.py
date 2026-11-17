# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An update does not make what it changed look like something the stack already had.

The compose file that served is judged on the tree as it is after the pull, and a line that reached a
directory before reaches wherever a link leads once the update puts a link there. Git says which links
the commits changed: a finding that goes through one of them is the update's and is refused; a link
somebody made by hand is in no commit and was there before, so it is warned about and nothing stops.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import compose_verdict
from noust.deployers.helpers.compose_guard import ComposeFindings, inspect_compose
from noust.managers.source_links import links_changed
from noust.managers.source_manager import SourceManager

SERVED = "services:\n  web:\n    image: nginx\n    volumes: ['./data:/data']\n"
DOCUMENT = {"services": {"web": {"image": "nginx", "volumes": ["./data:/data"]}}}
COMMIT = "a" * 40


@pytest.fixture
def store(tmp_path: Path) -> Any:
    """A store of the test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def stack(tmp_path: Path) -> Path:
    """An application directory whose ``data`` is a link that leaves it."""
    app = tmp_path / "stack"
    app.mkdir()
    os.symlink("/srv/elsewhere", app / "data")
    return app


def deployer(app: Path, store: NoustStore, said: list[str] | None = None) -> Any:
    """A compose deployer at ``app`` whose Compose cannot resolve the stack."""
    runner = FakeRunner()
    runner.script(["docker", "compose"], stdout="", stderr="invalid compose project", exit_code=1)
    compose = DockerComposeDeployer(runner=runner)
    compose.configure("stack.example.com", "src", app_path=app)
    compose.store = store
    compose.compose_path = app / "docker-compose.yml"
    compose.compose_path.write_text(SERVED)
    compose.previous_commit = COMMIT
    if said is not None:
        compose.logger.attach_sink(said.append)
    return compose


class TestWhichLinksGitSaysChanged:
    """``git diff-tree`` between the commit that served and the one checked out."""

    def manager(self, output: str, exit_code: int = 0) -> SourceManager:
        runner = FakeRunner()
        runner.script(["git"], stdout=output, exit_code=exit_code)
        return SourceManager(runner=runner)

    def test_a_link_that_was_added_or_changed_is_listed(self, tmp_path: Path) -> None:
        output = (
            ":000000 120000 " + "0" * 40 + " " + "1" * 40 + " A\0data\0"
            ":100644 100644 " + "2" * 40 + " " + "3" * 40 + " M\0README.md\0"
            ":120000 120000 " + "4" * 40 + " " + "5" * 40 + " M\0config\0"
        )

        changed = links_changed(self.manager(output), tmp_path, COMMIT)

        assert changed == frozenset({tmp_path / "data", tmp_path / "config"})

    def test_a_directory_that_became_a_link_is_one(self, tmp_path: Path) -> None:
        output = (
            ":100644 000000 " + "6" * 40 + " " + "0" * 40 + " D\0data/keep\0"
            ":000000 120000 " + "0" * 40 + " " + "7" * 40 + " A\0data\0"
        )

        assert links_changed(self.manager(output), tmp_path, COMMIT) == frozenset(
            {tmp_path / "data"}
        )

    def test_no_change_at_all_is_an_empty_set(self, tmp_path: Path) -> None:
        assert links_changed(self.manager(""), tmp_path, COMMIT) == frozenset()

    def test_when_git_cannot_say_nothing_is_known(self, tmp_path: Path) -> None:
        assert links_changed(self.manager("", exit_code=128), tmp_path, COMMIT) is None


class TestWhatCountsAsAlreadyHad:
    def findings(self, app: Path) -> ComposeFindings:
        return inspect_compose(DOCUMENT, app)

    def test_a_finding_through_a_link_the_update_changed_is_not_carried_over(
        self, stack: Path
    ) -> None:
        found = self.findings(stack)

        assert found.refused
        assert compose_verdict.unchanged(found, frozenset({stack / "data"})) == []

    def test_a_finding_through_a_link_nobody_committed_is_carried_over(self, stack: Path) -> None:
        found = self.findings(stack)

        assert compose_verdict.unchanged(found, frozenset()) == found.refused

    def test_when_git_could_not_say_every_link_counts_as_changed(self, stack: Path) -> None:
        assert compose_verdict.unchanged(self.findings(stack), None) == []

    def test_a_finding_that_goes_through_no_link_is_always_carried_over(
        self, tmp_path: Path
    ) -> None:
        document = {"services": {"web": {"image": "x", "devices": ["/dev/kvm"]}}}
        found = inspect_compose(document, tmp_path)

        assert compose_verdict.unchanged(found, None) == found.refused


class TestTheUpdate:
    """The deployer's way: the commit that served, the links the update changed, one verdict."""

    def update(self, compose: Any, monkeypatch: pytest.MonkeyPatch, changed: Any) -> None:
        class Source:
            def file_at_commit(self, *_args: Any) -> str:
                return SERVED

        monkeypatch.setattr(compose, "_source_manager", lambda: Source())
        monkeypatch.setattr(
            "noust.deployers.helpers.compose_privileges.changed_links", lambda *_a: changed
        )
        compose._check_update_privileges(DOCUMENT)

    def test_a_stack_that_mounted_a_link_of_its_own_before_goes_on_with_a_warning(
        self, stack: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        said: list[str] = []

        self.update(deployer(stack, store, said), monkeypatch, frozenset())

        assert any("through the link ./data that leads to /srv/elsewhere" in line for line in said)

    def test_the_same_line_is_refused_when_the_update_is_what_put_the_link_there(
        self, stack: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(DeploymentError, match="asks for root") as raised:
            self.update(deployer(stack, store), monkeypatch, frozenset({stack / "data"}))

        assert "through the link ./data" in (raised.value.details or "")

    def test_when_git_cannot_say_it_is_refused_rather_than_guessed(
        self, stack: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(DeploymentError, match="asks for root"):
            self.update(deployer(stack, store), monkeypatch, None)


class TestJustBeforeUp:
    """What a build or a hook wrote into the tree after the guard looked is judged before Compose mounts it."""

    def test_the_stack_is_judged_again_before_the_containers_are_recreated(
        self, stack: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose = deployer(stack, store)
        judged: list[str] = []

        def refuse() -> None:
            judged.append("judged")
            raise DeploymentError("asks for root")

        monkeypatch.setattr(compose, "_check_final_privileges", refuse)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._recreate()

        assert judged == ["judged"]
        assert not [call for call in compose.runner.calls if "up" in call]  # type: ignore[attr-defined]
