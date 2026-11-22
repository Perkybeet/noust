# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An update does not make what it changed look like something the stack already had.

A line that reached a directory before reaches wherever a link leads once the update puts a link there.
What the stack already had is therefore never read from the tree it runs from (the history of the checkout
is written by whatever mounts it): it is the record of what the stack was accepted with, and what its
containers were given. A link is carried over only by the record, which names the finding whatever the
link leads to today.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core import compose_acceptance
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers.compose_guard import inspect_compose

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


class TestTheUpdate:
    """The deployer's way: the record of what the stack was accepted with, one verdict."""

    def update(self, compose: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        class Source:
            def file_at_commit(self, *_args: Any) -> str:
                raise AssertionError("the tree's history is not a witness of what the stack had")

        monkeypatch.setattr(compose, "_source_manager", lambda: Source())
        compose._check_update_privileges(DOCUMENT)

    def record_the_link(self, stack: Path, store: NoustStore) -> None:
        findings = inspect_compose(DOCUMENT, stack)
        compose_acceptance.record(
            "stack.example.com",
            [findings.keys[sentence] for sentence in findings.refused],
            source="upgrade",
            store=store,
        )

    def test_a_stack_whose_record_has_a_link_of_its_own_goes_on_with_a_warning(
        self, stack: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.record_the_link(stack, store)
        said: list[str] = []

        self.update(deployer(stack, store, said), monkeypatch)

        assert any("through the link ./data that leads to /srv/elsewhere" in line for line in said)

    def test_the_same_line_is_refused_when_nothing_records_it(
        self, stack: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(DeploymentError, match="asks for root") as raised:
            self.update(deployer(stack, store), monkeypatch)

        assert "through the link ./data" in (raised.value.details or "")

    def test_a_link_the_record_does_not_name_is_refused_though_another_is_recorded(
        self, stack: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose_acceptance.record(
            "stack.example.com",
            [("service web", "outside", "/srv/other")],
            source="upgrade",
            store=store,
        )

        with pytest.raises(DeploymentError, match="asks for root"):
            self.update(deployer(stack, store), monkeypatch)


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
