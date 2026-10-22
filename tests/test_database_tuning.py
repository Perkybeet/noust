# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an instance may use, and how it is probed (3.4, C-30 and C-31).

The machine is :class:`~tests.database_instance_support.DockerHost`, so a change is really
written to the Compose file *on disk*, the unit restarts and the container is made from that
file. What is defended:

- **nothing the server could not honour is accepted**: memory above what the other instances
  leave, CPUs it does not have, a probe that takes longer than the time between probes;
- **a change is a restart behind the readiness gate**: when the engine does not come up on
  the new numbers the previous row and file are back, and the error carries the engine's log;
- **what is shown is what the container was made with**, and the probe's thresholds are the
  ones Docker was given.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.db_instance_store import READY
from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.managers.database import instance_layout as layout
from noust.managers.database.instance_tuning import InstanceTuning
from tests.database_instance_support import World, build_world, close_world

UNIT = "noust-db-pg16"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    built.managed.create("pg16", "postgresql", version="16", memory="1g")
    yield built
    close_world()


@pytest.fixture
def tuning(world: World) -> InstanceTuning:
    return InstanceTuning(world.managed)


def service_of() -> dict[str, Any]:
    return yaml.safe_load(layout.compose_path("pg16").read_text())["services"]["db"]


class TestWhatItIs:
    def test_a_new_instance_is_held_to_its_memory_and_probed_by_the_defaults(
        self, tuning: InstanceTuning
    ) -> None:
        status = tuning.status("pg16")

        assert status["limits"] == {"memory_mb": 1024, "cpus": None, "swap_mb": None}
        assert status["probe"] == {"interval_s": 10, "timeout_s": 5, "retries": 12}
        assert status["defaults"] == {"interval_s": 10, "timeout_s": 5, "retries": 12}
        assert status["server"]["cpus"] == 4
        assert status["server"]["available_mb"] > 1024, "its own memory counts as available to it"

    def test_health_is_what_docker_says_of_the_container(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        world.host.health["noust-db-pg16"] = ["unhealthy"]

        health = tuning.status("pg16")["health"]

        assert health["status"] == "unhealthy" and health["failing_streak"] == 3
        assert health["last"] == {"exit_code": 1, "output": "no response"}

    def test_a_container_that_is_not_there_has_no_health(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        world.managed.stop("pg16")

        assert tuning.status("pg16")["health"]["status"] == "absent"

    def test_an_unknown_instance_is_not_found(self, tuning: InstanceTuning) -> None:
        from noust.core.exceptions import DatabaseNotFoundError

        with pytest.raises(DatabaseNotFoundError):
            tuning.status("nope")


class TestWhatCannotBeHad:
    def test_more_memory_than_the_server_can_give_is_refused_with_what_it_can(
        self, tuning: InstanceTuning
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            tuning.plan("pg16", memory="64g")

        assert caught.value.field == "memory_mb" and "At most" in caught.value.details

    def test_less_memory_than_the_engine_starts_in_is_refused(self, tuning: InstanceTuning) -> None:
        with pytest.raises(ValidationError) as caught:
            tuning.plan("pg16", memory="64m")

        assert caught.value.field == "memory_mb" and "256" in caught.value.details

    def test_the_memory_it_already_holds_is_available_to_it(self, tuning: InstanceTuning) -> None:
        # 8 GiB server, 90 % may be held: 7372 MB, of which this instance has 1024.
        plan = tuning.plan("pg16", memory="7372m")

        assert plan.blockers == []

    def test_more_cpus_than_the_server_has_is_refused(self, tuning: InstanceTuning) -> None:
        with pytest.raises(ValidationError) as caught:
            tuning.plan("pg16", cpus=8)

        assert caught.value.field == "cpus" and "4" in caught.value.details

    @pytest.mark.parametrize("value", ["lots", 0, -1, "0.05"])
    def test_cpus_that_are_not_a_number_in_range_are_refused(
        self, tuning: InstanceTuning, value: object
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            tuning.plan("pg16", cpus=value)

        assert caught.value.field == "cpus"

    def test_a_probe_cannot_take_longer_than_the_time_between_probes(
        self, tuning: InstanceTuning
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            tuning.plan("pg16", interval=3, timeout=4)

        assert caught.value.field == "timeout"

    def test_a_timeout_longer_than_the_interval_it_keeps_is_refused_too(
        self, tuning: InstanceTuning
    ) -> None:
        with pytest.raises(ValidationError):
            tuning.plan("pg16", timeout=30)  # the interval stays at its default, 10 s

    @pytest.mark.parametrize(
        ("setting", "value"),
        [("interval", 1), ("interval", 100000), ("retries", 0), ("timeout", "x")],
    )
    def test_a_threshold_out_of_range_is_refused_naming_the_field(
        self, tuning: InstanceTuning, setting: str, value: object
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            tuning.plan("pg16", **{setting: value})

        assert caught.value.field == setting

    def test_a_default_cannot_be_asked_for_and_given_at_once(self, tuning: InstanceTuning) -> None:
        with pytest.raises(ValidationError):
            tuning.plan("pg16", cpus=2, clear=["cpus"])

    def test_only_what_has_a_default_can_be_cleared(self, tuning: InstanceTuning) -> None:
        with pytest.raises(ValidationError):
            tuning.plan("pg16", clear=["memory"])

    def test_an_instance_that_is_being_changed_cannot_be_changed(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        world.managed.records.set_state("pg16", "upgrading")

        plan = tuning.plan("pg16", cpus=1)

        assert any("upgrading" in blocker for blocker in plan.blockers)
        with pytest.raises(ValidationError):
            tuning.apply("pg16", cpus=1)


class TestThePlan:
    def test_it_lists_what_changes_and_the_applications_that_lose_their_connection(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        plan = tuning.plan("pg16", memory="2g", cpus=1.5, swap=512, interval=15, retries=5)

        assert plan.restarts and plan.blockers == []
        assert {change["setting"]: (change["from"], change["to"]) for change in plan.changes} == {
            "memory": (1024, 2048),
            "cpus": (None, 1.5),
            "swap": (None, 512),
            "interval": (10, 15),
            "retries": (12, 5),
        }
        assert any("restarts" in warning for warning in plan.warnings)

    def test_planning_changes_nothing(self, world: World, tuning: InstanceTuning) -> None:
        before = layout.compose_path("pg16").read_text()

        tuning.plan("pg16", memory="2g", cpus=2)

        assert layout.compose_path("pg16").read_text() == before
        assert world.managed.get("pg16").memory_limit_mb == 1024

    def test_less_memory_says_the_caches_shrink(self, tuning: InstanceTuning) -> None:
        plan = tuning.plan("pg16", memory="512m")

        assert any("caches" in warning for warning in plan.warnings)

    def test_what_is_already_so_changes_nothing_and_does_not_restart(
        self, tuning: InstanceTuning
    ) -> None:
        plan = tuning.plan("pg16", memory="1g")

        assert plan.changes == [] and plan.restarts is False
        assert any("nothing changes" in warning for warning in plan.warnings)


class TestChanging:
    def test_the_limits_are_in_the_file_the_container_is_made_from(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        tuning.apply("pg16", memory="2g", cpus=1.5, swap=512)

        service = service_of()
        assert service["deploy"]["resources"]["limits"] == {"memory": "2048M", "cpus": "1.5"}
        assert service["memswap_limit"] == "2560M", "Compose counts memory and swap together"
        container = world.host.containers["noust-db-pg16"]
        assert container["_limits"] == {"memory": "2048M", "cpus": "1.5"}
        assert container["_memswap"] == "2560M"
        record = world.managed.get("pg16")
        assert (record.memory_limit_mb, record.cpu_limit, record.swap_limit_mb) == (2048, 1.5, 512)
        assert record.state == READY

    def test_no_swap_is_a_total_equal_to_the_memory(self, tuning: InstanceTuning) -> None:
        tuning.apply("pg16", swap=0)

        assert service_of()["memswap_limit"] == "1024M"

    def test_the_engine_is_sized_to_the_new_memory(self, tuning: InstanceTuning) -> None:
        before = service_of()["command"]

        tuning.apply("pg16", memory="4g")

        assert service_of()["command"] != before

    def test_the_probe_is_what_docker_is_given(self, world: World, tuning: InstanceTuning) -> None:
        tuning.apply("pg16", interval=15, timeout=3, retries=5)

        probe = world.host.containers["noust-db-pg16"]["_healthcheck"]
        assert (probe["interval"], probe["timeout"], probe["retries"]) == ("15s", "3s", 5)
        assert tuning.status("pg16")["probe"] == {"interval_s": 15, "timeout_s": 3, "retries": 5}

    def test_a_default_is_put_back_by_clearing_it(self, tuning: InstanceTuning) -> None:
        tuning.apply("pg16", cpus=2, swap=256, interval=20, retries=3)

        status = tuning.apply("pg16", clear=["cpus", "swap", "interval", "retries"])

        assert status["limits"] == {"memory_mb": 1024, "cpus": None, "swap_mb": None}
        assert status["probe"] == {"interval_s": 10, "timeout_s": 5, "retries": 12}
        service = service_of()
        assert (
            "memswap_limit" not in service
            and "cpus" not in service["deploy"]["resources"]["limits"]
        )

    def test_the_container_is_restarted_once_through_its_unit(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        world.host.started.clear()

        tuning.apply("pg16", cpus=1)

        assert world.host.started == [UNIT]

    def test_nothing_is_restarted_when_nothing_changes(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        world.host.started.clear()

        tuning.apply("pg16", memory="1g")

        assert world.host.started == []

    def test_the_change_is_written_in_the_audit_trail(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []
        original = world.managed.service.audit

        def spy(event: str, target: str, *args: Any, **kwargs: Any) -> None:
            recorded.append((event, kwargs))
            original(event, target, *args, **kwargs)

        world.managed.service.audit = spy  # type: ignore[method-assign]

        tuning.apply("pg16", cpus=1, retries=4)

        [(event, fields)] = [item for item in recorded if item[0] == "db.instance.tune"]
        assert event == "db.instance.tune" and fields["changed"] == "cpus,retries"


class TestAnEngineThatDoesNotComeUp:
    def test_the_previous_settings_are_back_and_the_error_carries_the_log(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        before = layout.compose_path("pg16").read_text()
        world.host.health["noust-db-pg16"] = ["unhealthy", "healthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            tuning.apply("pg16", memory="256m", cpus=0.5)

        assert "runs as it did before" in caught.value.message
        assert "ready to accept connections" in (caught.value.output or "")
        record = world.managed.get("pg16")
        assert (record.memory_limit_mb, record.cpu_limit, record.state) == (1024, None, READY)
        assert layout.compose_path("pg16").read_text() == before
        assert world.host.containers["noust-db-pg16"]["_limits"] == {"memory": "1024M"}

    def test_an_engine_that_cannot_be_put_back_says_where_to_look(
        self, world: World, tuning: InstanceTuning
    ) -> None:
        world.host.health["noust-db-pg16"] = ["unhealthy"]

        with pytest.raises(DatabaseEngineError) as caught:
            tuning.apply("pg16", cpus=1)

        assert "could not be put back" in caught.value.message
        assert str(layout.compose_path("pg16")) in caught.value.details
        assert world.managed.get("pg16").state == "upgrading"
