# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The guard writes down what it accepted, and an update is compared with that and not with the tree.

What a stack "already had" used to be read, in part, from the compose file at the commit that served,
and that commit is read from the ``.git`` folder of the tree: a container that mounts the tree can write
it. Now an activation that passed the guard records what the stack was given of the server, in the store,
and an update inherits that and what the containers have, and nothing a repository can write.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import compose_acceptance
from noust.core.exceptions import DeploymentError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers.docker_compose import DockerComposeDeployer
from noust.deployers.helpers import compose_privileges, compose_verdict, sandbox
from noust.deployers.helpers.compose_guard import inspect_compose
from noust.deployers.helpers.compose_verdict import Heritage
from tests.support.docker_grants_samples import container_id, grants_line, ps_row

DOMAIN = "stack.example.com"
BENIGN = {"services": {"web": {"image": "nginx", "volumes": ["./:/app"]}}}
PRIVILEGED = {"services": {"web": {"image": "nginx", "volumes": ["./:/app"], "privileged": True}}}
FORGED = "services:\n  web:\n    image: nginx\n    privileged: true\n"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def deployer(app: Path, store: NoustStore) -> DockerComposeDeployer:
    """A compose deployer whose Compose cannot resolve the stack (the file is judged as written)."""
    runner = FakeRunner()
    runner.script(["docker", "compose"], stdout="", stderr="invalid compose project", exit_code=1)
    compose = DockerComposeDeployer(runner=runner)
    compose.configure(DOMAIN, "src", app_path=app)
    compose.store = store
    compose.compose_path = app / "docker-compose.yml"
    compose.compose_path.write_text("services:\n  web:\n    image: nginx\n")
    compose.previous_commit = "a" * 40
    return compose


@pytest.fixture
def app(tmp_path: Path) -> Path:
    path = tmp_path / "stack"
    path.mkdir()
    return path


class TestAKeyIsWhatTheRecordHolds:
    def keys(self, document: Any, app: Path) -> tuple[Any, frozenset[Any]]:
        findings = inspect_compose(document, app)
        return findings, frozenset(findings.keys[s] for s in findings.refused)

    def test_what_the_record_has_is_what_the_stack_already_had(self, app: Path) -> None:
        findings, keys = self.keys(PRIVILEGED, app)

        verdict = compose_verdict.decide(findings, already=Heritage(keys=keys))

        assert not verdict.stop
        assert verdict.had == tuple(findings.refused)

    def test_what_it_does_not_have_stops(self, app: Path) -> None:
        findings, _ = self.keys(PRIVILEGED, app)

        verdict = compose_verdict.decide(findings, already=Heritage(keys=frozenset()))

        assert verdict.stop == tuple(findings.refused)

    def test_a_record_covers_what_no_exception_could_allow_when_the_stack_had_it(
        self, app: Path
    ) -> None:
        # What 4.2 started and 4.3 took over: another application's files by a door that has no
        # reason to read them. It is warned about, never stopped, like everything the stack had.
        document = {"services": {"web": {"image": "x", "env_file": ["/var/www/apps/other/.env"]}}}
        findings, keys = self.keys(document, app)
        assert findings.hard

        verdict = compose_verdict.decide(findings, already=Heritage(keys=keys))

        assert not verdict.stop and verdict.had

    def test_a_record_of_another_service_does_not_cover_this_one(self, app: Path) -> None:
        findings, _ = self.keys(PRIVILEGED, app)
        other = frozenset({("service worker", "privileged", "")})

        verdict = compose_verdict.decide(findings, already=Heritage(keys=other))

        assert verdict.stop

    def test_the_file_that_served_still_counts_for_a_stack_with_no_record(self, app: Path) -> None:
        findings, _ = self.keys(PRIVILEGED, app)

        verdict = compose_verdict.decide(findings, already=Heritage(frozenset(findings.refused)))

        assert not verdict.stop


class TestTheGuardWritesItDown:
    def test_a_stack_that_passed_is_recorded_with_what_it_was_given(
        self, app: Path, store: NoustStore
    ) -> None:
        sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="admin", reason="needs the host", store=store
        )
        compose = deployer(app, store)

        compose_privileges.check_host_privileges(compose, PRIVILEGED, final=True, record="deploy")

        accepted = compose_acceptance.get(DOMAIN, store=store)
        assert accepted is not None and accepted.source == "deploy"
        assert {key[1] for key in accepted.keys} == {"privileged"}

    def test_a_stack_that_was_stopped_is_recorded_with_nothing(
        self, app: Path, store: NoustStore
    ) -> None:
        compose = deployer(app, store)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose_privileges.check_host_privileges(
                compose, PRIVILEGED, final=True, record="deploy"
            )

        assert compose_acceptance.get(DOMAIN, store=store) is None

    def test_a_check_that_does_not_ask_for_it_writes_nothing(
        self, app: Path, store: NoustStore
    ) -> None:
        sandbox.set_compose_exception(
            DOMAIN, allowed=True, actor="admin", reason="needs the host", store=store
        )

        compose_privileges.check_host_privileges(deployer(app, store), PRIVILEGED, final=True)

        assert compose_acceptance.get(DOMAIN, store=store) is None

    def test_a_clean_stack_is_recorded_too(self, app: Path, store: NoustStore) -> None:
        # So that a later start is compared with a record, and what it adds is what is new.
        compose_privileges.check_host_privileges(
            deployer(app, store), BENIGN, final=True, record="deploy"
        )

        accepted = compose_acceptance.get(DOMAIN, store=store)
        assert accepted is not None and accepted.keys == frozenset()

    def test_what_serves_settles_the_record_with_what_the_stack_has(
        self, app: Path, store: NoustStore
    ) -> None:
        compose_acceptance.record(
            DOMAIN, [("service web", "privileged", "")], source="deploy", store=store
        )
        compose = deployer(app, store)
        compose_privileges.check_host_privileges(compose, BENIGN, final=True, record="update")

        compose_privileges.settle(compose)

        accepted = compose_acceptance.get(DOMAIN, store=store)
        assert accepted is not None and accepted.keys == frozenset()


class TestAnUpdateInheritsTheRecord:
    def update(self, compose: Any, document: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        class Source:
            """What a container that writes the checkout's ``.git`` hands the update."""

            def file_at_commit(self, *_args: Any) -> str:
                raise AssertionError("the tree's history is not a record of what was accepted")

        monkeypatch.setattr(compose, "_source_manager", lambda: Source())
        compose._check_update_privileges(document)

    def test_a_forged_commit_is_not_read_when_there_is_a_record(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose_acceptance.record(DOMAIN, [], source="deploy", store=store)

        with pytest.raises(DeploymentError, match="asks for root"):
            self.update(deployer(app, store), PRIVILEGED, monkeypatch)

    def test_what_the_record_has_goes_on_with_a_warning(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        findings = inspect_compose(PRIVILEGED, app)
        compose_acceptance.record(
            DOMAIN, [findings.keys[s] for s in findings.refused], source="deploy", store=store
        )
        said: list[str] = []
        compose = deployer(app, store)
        compose.logger.attach_sink(said.append)

        self.update(compose, PRIVILEGED, monkeypatch)

        assert any("privileged" in line for line in said)

    def test_a_forged_commit_is_not_read_for_a_stack_with_no_record_either(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # What the commit that served says is written by whatever mounts the tree: with no record
        # nothing of the tree is what the stack had, and a stack with no container is a new stack.
        with pytest.raises(DeploymentError, match="asks for root"):
            self.update(deployer(app, store), PRIVILEGED, monkeypatch)


def running_with(compose: DockerComposeDeployer, *, devices: tuple[str, ...]) -> None:
    """Make the stack's one container have these devices, as Docker would say of it."""
    compose.runner.script(  # type: ignore[attr-defined]
        ["docker", "ps"],
        stdout=ps_row(
            container_id("web"),
            "stack-web-1",
            project="stack",
            service="web",
            working_dir=str(compose.app_path),
        ),
    )
    compose.runner.script(  # type: ignore[attr-defined]
        ["docker", "inspect"], stdout=grants_line("web", devices=devices)
    )


class TestAStackWithNoRecordHasItsContainersAndNothingElse:
    """
    Before a stack is taken over there is no record of what it was accepted with. What it had is then
    what its containers were given: neither the file at the commit that served (written by whatever
    mounts the checkout) nor the file on disk is a witness.
    """

    def test_an_update_has_no_sentence_of_the_commit_that_served(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Source:
            def file_at_commit(self, *_args: Any) -> str:
                raise AssertionError("the tree's history is not a witness of what the stack had")

        compose = deployer(app, store)
        monkeypatch.setattr(compose, "_source_manager", lambda: Source())

        heritage = compose_privileges.heritage_of_update(compose)

        assert heritage.sentences == frozenset() and heritage.keys == frozenset()

    def test_a_redeploy_has_no_sentence_of_the_file_on_disk(
        self, app: Path, store: NoustStore
    ) -> None:
        compose = deployer(app, store)
        compose.compose_path.write_text(FORGED)  # type: ignore[union-attr]

        heritage = compose_privileges.heritage_of_redeploy(compose)

        assert heritage.sentences == frozenset() and heritage.keys == frozenset()

    def test_a_redeploy_that_adds_to_the_file_on_disk_is_refused_with_no_record(
        self, app: Path, store: NoustStore
    ) -> None:
        compose = deployer(app, store)
        compose.compose_path.write_text(  # type: ignore[union-attr]
            "services:\n  web:\n    image: nginx\n    devices: ['/dev/kvm']\n"
        )
        compose._accepted_refusals = compose_privileges.heritage_of_redeploy(compose)

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_host_privileges(
                {"services": {"web": {"image": "nginx", "devices": ["/dev/kvm"]}}}
            )

    def test_what_its_containers_have_goes_on_with_a_warning(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Source:
            def file_at_commit(self, *_args: Any) -> str:
                raise AssertionError("the tree's history is not a witness of what the stack had")

        said: list[str] = []
        compose = deployer(app, store)
        compose.logger.attach_sink(said.append)
        running_with(compose, devices=("/dev/kvm",))
        monkeypatch.setattr(compose, "_source_manager", lambda: Source())
        document = {"services": {"web": {"image": "nginx", "devices": ["/dev/kvm"]}}}

        compose._check_update_privileges(document)

        assert any("/dev/kvm" in line for line in said)

    def test_what_no_container_has_is_still_refused(
        self, app: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Source:
            def file_at_commit(self, *_args: Any) -> str:
                raise AssertionError("the tree's history is not a witness of what the stack had")

        compose = deployer(app, store)
        running_with(compose, devices=("/dev/kvm",))
        monkeypatch.setattr(compose, "_source_manager", lambda: Source())
        more = {"services": {"web": {"image": "nginx", "devices": ["/dev/kvm", "/dev/sda"]}}}

        with pytest.raises(DeploymentError, match="asks for root"):
            compose._check_update_privileges(more)

    def test_a_stack_that_has_a_record_still_inherits_the_record(
        self, app: Path, store: NoustStore
    ) -> None:
        findings = inspect_compose(PRIVILEGED, app)
        compose_acceptance.record(
            DOMAIN, [findings.keys[s] for s in findings.refused], source="upgrade", store=store
        )
        compose = deployer(app, store)

        heritage = compose_privileges.heritage_of_update(compose)

        assert heritage.keys == frozenset(findings.keys[s] for s in findings.refused)
