# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cleaning Docker is a plan that is shown, and what is shown is what is done.

``docker system prune`` takes volumes with it, and volumes are the data; ``docker image prune -a``
takes the image an application keeps to go back to. So a cleanup here is a plan: what would go, whose
it is, how much it holds and why it is not needed, and what stays and why. Four properties are held.

- **A volume is never in what is ticked and is named before it goes**, with what it holds.
- **What an application or an engine owns is never in the plan**, and the images that are an
  application's way back are said to stay and are never offered as unused.
- **A plan that changed since it was shown is not applied**: it is shown again.
- **What is removed is what Docker listed**, each through the gate that judges its owner again.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import pytest

from noust.core.docker import names
from noust.core.docker.errors import DockerPlanChangedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import OwnerKind
from noust.core.docker.owners import AppRecord, Registry
from noust.core.exceptions import ConfirmationRequired, ValidationError
from noust.core.teams import Scope
from tests.support.docker_machine import RELEASE, SHOP, DockerMachine, full_id

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
REGISTRY = Registry(apps=(AppRecord("shop.example.com", SHOP),), engines=("pg16",))


def dirty() -> DockerMachine:
    """A machine with what a cleanup is about: names nobody gave, stopped things, unused volumes."""
    machine = DockerMachine()
    machine.box(
        "shop-web-1",
        project="shop-example-com",
        working_dir=SHOP,
        image=f"shop-example-com-web:noust-{RELEASE}",
        mounts="shop-example-com_data",
        networks="shop-example-com_default",
    )
    machine.box("adminer", image="adminer:latest", state="exited", status="Exited (143) 3 days ago")
    machine.box(
        "old-test", image="hello-world:latest", state="exited", status="Exited (0) 41 days ago"
    )
    machine.box(
        "nextcloud-setup-1",
        project="nextcloud",
        working_dir="/opt/stacks/nextcloud",
        state="exited",
        status="Exited (0) 30 days ago",
    )
    machine.box("redis-cache", image="redis:7-alpine", mounts="cache_data")
    machine.box(
        names.instance_container("pg16"),
        project=names.instance_project("pg16"),
        image="postgres:16",
        instance="pg16",
    )
    machine.image("1", "<none>", "<none>", "380MB")
    machine.image("2", "<none>", "<none>", "260MB")
    machine.image("3", "debian", "11", "120MB")
    machine.image("4", "shop-example-com-web", f"noust-{RELEASE}", "300MB")
    machine.image("5", "shop-example-com-web", "noust-20261001-100000-aaaaaaa", "298MB")
    machine.image("6", "alpine", "3.20", "8MB")
    machine.image("7", "adminer", "latest", "148MB")
    machine.image("8", "hello-world", "latest", "10kB")
    machine.image("9", "redis", "7-alpine", "41MB")
    machine.image("a", "postgres", "16", "438MB")
    machine.volume("shop-example-com_data", project="shop-example-com", volume="data", size="6.8GB")
    machine.volume("wiki_data", size="3.4GB")
    machine.volume("cache_data", size="12MB")
    machine.volume("f" * 64, size="14MB")
    machine.network("shop-example-com_default", project="shop-example-com")
    machine.network("bridge")
    machine.network("scratch")
    machine.network("tools_default")
    machine.networks[-1]["project"] = "tools"
    machine.df = [
        {"Type": "Images", "TotalCount": "10", "Size": "2GB", "Reclaimable": "640MB (31%)"},
        {"Type": "Build Cache", "TotalCount": "4", "Size": "2GB", "Reclaimable": "1.2GB"},
    ]
    return machine


def gate(
    machine: DockerMachine,
    *,
    scope: Scope | None = None,
    sees_app: Callable[[str], bool] | None = None,
) -> DockerGate:
    return DockerGate(
        machine,
        registry=lambda: REGISTRY,
        scope=scope or Scope.everything(),
        sees_app=sees_app,
        clock=lambda: NOW.timestamp(),
    )


class TestWhatThePlanSays:
    def items(self, machine: DockerMachine) -> dict[tuple[str, str], object]:
        plan = gate(machine).cleanup_plan()
        return {(item.kind, item.name): item for item in plan.items}

    def test_the_images_nobody_names_and_nobody_uses_are_in_it_and_ticked(self) -> None:
        found = [i for i in gate(dirty()).cleanup_plan().items if i.why == "dangling"]

        assert sorted((i.size_bytes, i.tick) for i in found) == [
            (260_000_000, True),
            (380_000_000, True),
        ]

    def test_a_named_image_nobody_uses_is_in_it_and_not_ticked(self) -> None:
        item = self.items(dirty())[("image", "debian:11")]

        assert (item.why, item.tick, item.size_bytes) == ("unused", False, 120_000_000)  # type: ignore[attr-defined]

    def test_an_image_a_container_uses_is_not_in_it_even_if_the_container_is_stopped(self) -> None:
        found = self.items(dirty())

        for name in ("adminer:latest", "hello-world:latest", "redis:7-alpine", "postgres:16"):
            assert ("image", name) not in found

    def test_the_image_an_application_keeps_to_go_back_is_said_to_stay_and_never_offered(
        self,
    ) -> None:
        plan = gate(dirty()).cleanup_plan()

        assert ("image", "shop-example-com-web:noust-20261001-100000-aaaaaaa") not in {
            (i.kind, i.name) for i in plan.items
        }
        kept = {k.name: k for k in plan.kept}
        way_back = kept["shop-example-com-web:noust-20261001-100000-aaaaaaa"]
        assert (way_back.why, way_back.owner.subject) == ("rollback", "shop.example.com")

    def test_stopped_containers_nobody_made_are_in_it_and_never_ticked(self) -> None:
        found = [i for i in gate(dirty()).cleanup_plan().items if i.kind == "container"]

        assert sorted(i.name for i in found) == ["adminer", "old-test"]
        assert not any(i.tick for i in found)
        assert all(i.owner.kind is OwnerKind.NOBODY and i.why == "stopped" for i in found)

    def test_a_stopped_container_of_a_stack_is_the_stacks_and_not_a_loose_one(self) -> None:
        names_ = {i.name for i in gate(dirty()).cleanup_plan().items if i.kind == "container"}

        assert "nextcloud-setup-1" not in names_

    def test_volumes_nobody_uses_are_in_it_with_their_size_and_must_be_named(self) -> None:
        volumes = [i for i in gate(dirty()).cleanup_plan().items if i.kind == "volume"]

        assert sorted((v.name, v.size_bytes) for v in volumes) == [
            ("f" * 64, 14_000_000),
            ("wiki_data", 3_400_000_000),
        ]
        assert all(v.type_name and not v.tick for v in volumes)

    def test_a_volume_without_a_name_is_said_to_be_anonymous(self) -> None:
        volumes = {i.name: i for i in gate(dirty()).cleanup_plan().items if i.kind == "volume"}

        assert volumes["f" * 64].anonymous and not volumes["wiki_data"].anonymous

    def test_a_volume_a_container_mounts_is_not_in_it_and_nor_is_an_applications(self) -> None:
        names_ = {i.name for i in gate(dirty()).cleanup_plan().items if i.kind == "volume"}

        assert "cache_data" not in names_ and "shop-example-com_data" not in names_

    def test_networks_with_nobody_on_them_are_in_it_and_dockers_own_never(self) -> None:
        networks = [i for i in gate(dirty()).cleanup_plan().items if i.kind == "network"]

        assert sorted(n.name for n in networks) == ["scratch", "tools_default"]
        assert all(n.tick for n in networks)

    def test_the_build_cache_is_in_it_with_what_can_be_recovered(self) -> None:
        cache = [i for i in gate(dirty()).cleanup_plan().items if i.kind == "cache"]

        assert len(cache) == 1
        assert (cache[0].size_bytes, cache[0].tick, cache[0].why) == (
            1_200_000_000,
            True,
            "old_cache",
        )

    def test_what_belongs_to_an_application_or_an_engine_is_never_in_it(self) -> None:
        plan = gate(dirty()).cleanup_plan()

        assert not [i for i in plan.items if i.owner.kind is not OwnerKind.NOBODY]

    def test_nothing_in_the_plan_is_removed_by_planning(self) -> None:
        machine = dirty()

        gate(machine).cleanup_plan()

        assert machine.changes() == []

    def test_what_a_team_may_not_see_is_not_in_it_and_the_plan_is_empty(self) -> None:
        mine = gate(dirty(), scope=Scope.nothing(), sees_app=lambda domain: True)

        assert mine.cleanup_plan().items == ()

    def test_the_plan_adds_up_what_the_default_selection_recovers(self) -> None:
        plan = gate(dirty()).cleanup_plan()

        assert plan.default_bytes == 380_000_000 + 260_000_000 + 1_200_000_000 + 0

    def test_a_machine_with_nothing_to_clean_has_an_empty_plan_and_still_a_token(self) -> None:
        machine = DockerMachine()
        machine.box("web")

        plan = gate(machine).cleanup_plan()

        assert plan.items == () and plan.token


class TestAPlanThatChanged:
    def test_the_token_is_the_same_while_the_plan_is_the_same(self) -> None:
        machine = dirty()

        assert gate(machine).cleanup_plan().token == gate(machine).cleanup_plan().token

    def test_the_token_changes_when_something_leaves_or_joins_the_plan(self) -> None:
        machine = dirty()
        before = gate(machine).cleanup_plan().token

        machine.image("c", "<none>", "<none>", "5MB")

        assert gate(machine).cleanup_plan().token != before

    def test_the_size_of_a_thing_is_not_part_of_the_token(self) -> None:
        machine = dirty()
        before = gate(machine).cleanup_plan().token

        machine.df[1]["Reclaimable"] = "1.3GB"

        assert gate(machine).cleanup_plan().token == before

    def test_a_plan_that_is_not_the_one_that_was_shown_is_not_applied_and_is_shown_again(
        self,
    ) -> None:
        machine = dirty()
        shown = gate(machine).cleanup_plan()
        machine.image("c", "<none>", "<none>", "5MB")

        with pytest.raises(DockerPlanChangedError) as changed:
            gate(machine).apply_cleanup(shown.token, [shown.items[0].id])

        assert changed.value.plan.token != shown.token
        assert len(changed.value.plan.items) == len(shown.items) + 1
        assert machine.changes() == []

    def test_a_container_that_started_since_leaves_the_plan_and_so_the_plan_changes(self) -> None:
        machine = dirty()
        shown = gate(machine).cleanup_plan()
        machine.boxes["adminer"].state = "running"

        with pytest.raises(DockerPlanChangedError):
            gate(machine).apply_cleanup(shown.token, [shown.items[0].id])

        assert machine.changes() == []


class TestApplyingIt:
    def apply(
        self, machine: DockerMachine, kinds: set[str], *, confirm: dict[str, str] | None = None
    ) -> object:
        g = gate(machine)
        plan = g.cleanup_plan()
        chosen = [i.id for i in plan.items if i.kind in kinds]
        return g.apply_cleanup(plan.token, chosen, confirm_names=confirm or {})

    def test_what_was_chosen_goes_and_what_was_not_stays(self) -> None:
        machine = dirty()

        result = self.apply(machine, {"image", "network", "cache"})

        removed = set(machine.removed)
        assert {f"image:sha256:{digit * 64}" for digit in "1236"} <= removed
        assert sum(1 for item in removed if item.startswith("network:")) == 2
        assert "cache" in removed
        assert not any(item.startswith(("volume:", "container:")) for item in removed)
        assert result.removed  # type: ignore[attr-defined]

    def test_a_container_is_removed_by_the_id_docker_listed(self) -> None:
        machine = dirty()

        self.apply(machine, {"container"})

        assert [c for c in machine.changes() if c[:2] == ("docker", "rm")] == [
            ("docker", "rm", full_id("adminer")),
            ("docker", "rm", full_id("old-test")),
        ]

    def test_a_volume_is_not_removed_unless_its_name_is_typed(self) -> None:
        machine = dirty()
        g = gate(machine)
        plan = g.cleanup_plan()
        wiki = next(i for i in plan.items if i.name == "wiki_data")

        with pytest.raises(ConfirmationRequired) as asked:
            g.apply_cleanup(plan.token, [wiki.id], confirm_names={"wiki_data": "wiki"})

        assert "3.4 GB" in asked.value.warnings[0]
        assert machine.changes() == []

    def test_a_volume_whose_name_was_typed_is_removed(self) -> None:
        machine = dirty()

        self.apply(machine, {"volume"}, confirm={"wiki_data": "wiki_data", "f" * 64: "f" * 64})

        assert "volume:wiki_data" in machine.removed

    def test_nothing_is_removed_when_one_volume_was_not_named_even_if_the_rest_was_fine(
        self,
    ) -> None:
        machine = dirty()
        g = gate(machine)
        plan = g.cleanup_plan()
        chosen = [i.id for i in plan.items if i.kind in ("image", "volume")]

        with pytest.raises(ConfirmationRequired):
            g.apply_cleanup(plan.token, chosen, confirm_names={"wiki_data": "wiki_data"})

        assert machine.changes() == []

    def test_what_the_plan_did_not_offer_cannot_be_asked_for(self) -> None:
        machine = dirty()
        g = gate(machine)
        plan = g.cleanup_plan()

        with pytest.raises(ValidationError):
            g.apply_cleanup(plan.token, [f"container:{full_id('shop-web-1')}"])
        with pytest.raises(ValidationError):
            g.apply_cleanup(plan.token, ["../../etc"])

        assert machine.changes() == []

    def test_nothing_chosen_is_not_a_cleanup(self) -> None:
        g = gate(dirty())

        with pytest.raises(ValidationError):
            g.apply_cleanup(g.cleanup_plan().token, [])

    def test_the_cache_is_pruned_by_age_and_never_with_the_volumes(self) -> None:
        machine = dirty()

        self.apply(machine, {"cache"})

        assert machine.changes() == [("docker", "builder", "prune", "-f", "--filter", "until=168h")]

    def test_it_says_what_was_freed_and_what_failed_and_goes_on(self) -> None:
        machine = dirty()
        machine.script(["docker", "rm", full_id("adminer")], exit_code=1, stderr="device busy")

        result = self.apply(machine, {"container", "image"})

        assert [f["name"] for f in result.failed] == ["adminer"]  # type: ignore[attr-defined]
        assert "device busy" in result.failed[0]["error"]  # type: ignore[attr-defined]
        assert "container:old-test" in machine.removed
        assert result.freed_bytes >= 380_000_000 + 260_000_000  # type: ignore[attr-defined]

    def test_every_line_it_does_is_told_to_whoever_listens(self) -> None:
        machine = dirty()
        g = gate(machine)
        plan = g.cleanup_plan()
        told: list[str] = []

        g.apply_cleanup(
            plan.token,
            [i.id for i in plan.items if i.kind == "container"],
            on_line=told.append,
        )

        assert told == ["Removed container adminer", "Removed container old-test"]
