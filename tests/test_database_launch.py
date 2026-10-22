# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an instance's container is started with, changed after it was made (4.0).

The arguments the operator adds to the engine's, or the whole command they write instead, are a
column of the instance's row and a new Compose file, applied by restarting the engine behind the
readiness gate every change of an instance passes. What is defended:

- **nothing is applied without the price accepted**: words of the operator's are not tested by
  Noust, and saying so is not optional;
- **the engine that does not come up on them is put back as it was**, with the container's own
  words, and the row too;
- **taking them back is free**: no confirmation to return to what Noust passes;
- **a command is the whole command**, so it replaces arguments and arguments replace it, and a
  command is refused for an instance that serves TLS, whose arguments Noust cannot add to it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.exceptions import ConfirmationRequired, DatabaseEngineError, ValidationError
from noust.managers.database import instance_layout as layout
from noust.managers.database import managed as managed_module
from noust.managers.database.instance_launch import InstanceLaunch
from tests.database_instance_support import World, build_world, close_world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    yield built
    close_world()


@pytest.fixture
def launch(world: World) -> InstanceLaunch:
    world.managed.create("pg", "postgresql", version="17", memory="1g")
    return InstanceLaunch(world.managed)


def command_of(name: str) -> list[str]:
    document = yaml.safe_load(layout.compose_path(name).read_text())
    return list(document["services"]["db"]["command"])


class TestLooking:
    def test_an_instance_starts_with_the_engines_own_command(self, launch: InstanceLaunch) -> None:
        status = launch.status("pg")

        assert status["command"] == [] and status["args"] == [] and status["customised"] is False
        assert status["key"] == "postgresql@pg"
        assert status["effective"][:2] == ["postgres", "-c"]

    def test_an_unknown_instance_is_not_found(self, launch: InstanceLaunch) -> None:
        with pytest.raises(Exception, match="no database instance"):
            launch.status("ghost")


class TestPlanning:
    def test_it_says_what_changes_and_that_the_engine_restarts(
        self, launch: InstanceLaunch
    ) -> None:
        plan = launch.plan("pg", args=["-c", "max_connections=77"])

        assert plan.restarts is True and plan.blockers == []
        assert plan.changes == [{"setting": "args", "from": [], "to": ["-c", "max_connections=77"]}]
        assert plan.requires_confirmation is True
        assert any("not tested" in warning for warning in plan.warnings)
        assert plan.effective[-2:] == ["-c", "max_connections=77"]

    def test_nothing_is_changed_by_planning(self, launch: InstanceLaunch, world: World) -> None:
        before = command_of("pg")

        launch.plan("pg", args=["-c", "max_connections=77"])

        assert command_of("pg") == before and world.host.started == ["noust-db-pg"]

    def test_saying_nothing_is_not_a_request(self, launch: InstanceLaunch) -> None:
        with pytest.raises(ValidationError) as caught:
            launch.plan("pg")

        assert "args" in caught.value.details and "reset" in caught.value.details

    def test_a_command_and_arguments_together_are_refused(self, launch: InstanceLaunch) -> None:
        with pytest.raises(ValidationError):
            launch.plan("pg", command=["postgres"], args=["-c", "x=1"])

    def test_the_same_words_are_nothing_to_do(self, launch: InstanceLaunch) -> None:
        launch.apply("pg", args=["-c", "work_mem=8MB"], accept_untested=True)

        plan = launch.plan("pg", args=["-c", "work_mem=8MB"])

        assert plan.changes == [] and plan.restarts is False
        assert any("already" in warning for warning in plan.warnings)

    def test_a_command_is_refused_for_an_instance_that_serves_tls(
        self, world: World, launch: InstanceLaunch
    ) -> None:
        world.managed.records.set_tls_mode("pg", "optional")

        plan = launch.plan("pg", command=["postgres", "-c", "max_connections=33"])

        assert plan.blockers and "TLS" in plan.blockers[0]

    def test_arguments_are_fine_for_an_instance_that_serves_tls(
        self, world: World, launch: InstanceLaunch
    ) -> None:
        world.managed.records.set_tls_mode("pg", "optional")

        assert launch.plan("pg", args=["-c", "work_mem=8MB"]).blockers == []

    def test_an_instance_that_is_being_changed_is_not_changed_again(
        self, world: World, launch: InstanceLaunch
    ) -> None:
        world.managed.records.set_state("pg", "upgrading")

        plan = launch.plan("pg", args=["-c", "work_mem=8MB"])

        assert plan.blockers and "not ready" in plan.blockers[0]


class TestApplying:
    def test_without_the_price_accepted_nothing_changes(
        self, launch: InstanceLaunch, world: World
    ) -> None:
        with pytest.raises(ConfirmationRequired) as caught:
            launch.apply("pg", args=["-c", "max_connections=77"])

        assert caught.value.field == "accept_untested"
        assert any("not tested" in warning for warning in caught.value.warnings)
        assert world.managed.get("pg").args == () and "max_connections" not in str(command_of("pg"))

    def test_the_arguments_are_in_the_file_and_the_engine_restarted_on_them(
        self, launch: InstanceLaunch, world: World
    ) -> None:
        launch.apply("pg", args=["-c", "max_connections=77"], accept_untested=True)

        assert command_of("pg")[-2:] == ["-c", "max_connections=77"]
        assert world.managed.get("pg").args == ("-c", "max_connections=77")
        assert world.host.started.count("noust-db-pg") == 2
        assert world.managed.get("pg").state == "ready"

    def test_a_command_replaces_the_arguments_and_arguments_replace_a_command(
        self, launch: InstanceLaunch, world: World
    ) -> None:
        launch.apply("pg", args=["-c", "work_mem=8MB"], accept_untested=True)
        launch.apply("pg", command=["postgres", "-c", "max_connections=33"], accept_untested=True)
        row = world.managed.get("pg")
        assert (row.command, row.args) == (("postgres", "-c", "max_connections=33"), ())
        assert command_of("pg") == ["postgres", "-c", "max_connections=33"]

        launch.apply("pg", args=["-c", "work_mem=4MB"], accept_untested=True)

        row = world.managed.get("pg")
        assert (row.command, row.args) == ((), ("-c", "work_mem=4MB"))
        assert command_of("pg")[0] == "postgres" and command_of("pg")[-2:] == ["-c", "work_mem=4MB"]

    def test_taking_them_back_needs_no_confirmation(
        self, launch: InstanceLaunch, world: World
    ) -> None:
        launch.apply("pg", args=["-c", "work_mem=8MB"], accept_untested=True)

        status = launch.apply("pg", reset=True)

        assert status["args"] == [] and status["customised"] is False
        assert "work_mem" not in str(command_of("pg"))

    def test_a_word_that_is_not_one_changes_nothing(
        self, launch: InstanceLaunch, world: World
    ) -> None:
        with pytest.raises(ValidationError):
            launch.apply("pg", args=["--requirepass", "x"], accept_untested=True)

        assert world.managed.get("pg").args == ()
        assert world.host.started == ["noust-db-pg"]

    def test_it_is_recorded_in_the_audit_trail(
        self, launch: InstanceLaunch, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[tuple[str, str, dict[str, Any]]] = []
        monkeypatch.setattr(
            world.service,
            "audit",
            lambda event, target, outcome="ok", **kw: events.append((event, target, kw)),
        )

        launch.apply("pg", args=["-c", "work_mem=8MB"], accept_untested=True)

        [(event, target, details)] = [e for e in events if e[0] == "db.instance.launch"]
        assert target == "pg" and details["changed"] == "args"
        assert "work_mem" not in str(details)


class TestWhenTheEngineDoesNotComeUp:
    def test_the_previous_row_and_file_are_put_back_with_the_engines_words(
        self, launch: InstanceLaunch, world: World
    ) -> None:
        before = command_of("pg")
        world.host.log = 'FATAL: unrecognized configuration parameter "bogus"\n'
        world.host.health["noust-db-pg"] = ["unhealthy", "healthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            launch.apply("pg", args=["-c", "bogus=1"], accept_untested=True)

        assert "runs as it did before" in caught.value.message
        assert "unrecognized configuration parameter" in (caught.value.output or "")
        assert command_of("pg") == before
        row = world.managed.get("pg")
        assert row.args == () and row.state == "ready"

    def test_when_it_cannot_be_put_back_the_error_says_where_it_stands(
        self, launch: InstanceLaunch, world: World
    ) -> None:
        world.host.health["noust-db-pg"] = ["unhealthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            launch.apply("pg", args=["-c", "bogus=1"], accept_untested=True)

        assert "could not be put back" in caught.value.message
        assert world.managed.get("pg").state == "upgrading"
