# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What updating an image does, and what it refuses to do, decided by whose the image is.

Held here: an image of an application of Noust is updated through the application (the health gate and
the way back are its own), and the plan says what moves with it; a major version of a database engine is
not applied until there is a copy that was proven to restore, and any major version is a decision taken on
purpose; what Noust did not create is brought to it first when it is a stack, and is not updated blind when
it is a container on its own, and the refusal says why; what a database engine or Noust itself owns is
changed through its own door; and nobody who may not see an image can ask about it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noust.core.docker.errors import DockerBlockedError, DockerNotFoundError, DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.owners import AppRecord, Registry
from noust.core.docker_image_checks import ImageVersionStore, VersionRecord
from noust.core.restore_drills import DrillRecords, DrillReport, DrillStep
from noust.core.store import App, NoustStore
from noust.core.teams import Scope
from noust.managers.docker_image_versions import ImageVersions
from noust.managers.docker_updates import ImageUpdates
from tests.support.docker_machine import DockerMachine

SHOP = "/var/www/apps/shop-example-com"
NEXTCLOUD = "/opt/stacks/nextcloud"
NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
REGISTRY = Registry(apps=(AppRecord("shop.example.com", SHOP),), engines=("pg15",))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_type="docker-compose", app_path=SHOP))
    yield instance
    NoustStore.reset_instance()


def record(
    reference: str, change: str = "patch", state: str = "newer", **more: object
) -> VersionRecord:
    base: dict[str, object] = {
        "reference": reference,
        "state": state,
        "local_digest": A,
        "remote_digest": B if state == "newer" else A,
        "current_version": None,
        "latest_version": "17.2" if change == "major" else None,
        "latest_created": None,
        "change": change if state == "newer" else "",
        "floating": reference.endswith(":latest"),
        "method": "imagetools",
        "detail": "",
        "checked_at": "2026-11-12T10:00:00+00:00",
    }
    base.update(more)
    return VersionRecord(**base)  # type: ignore[arg-type]


def machine() -> DockerMachine:
    """A server: an application with a web service and a database, a stack nobody made, a loose one."""
    box = DockerMachine()
    for digit, name, image, project, where in (
        ("1", "shop-web-1", "ghcr.io/acme/shop:2", "shop-example-com", SHOP),
        ("2", "shop-db-1", "postgres:16", "shop-example-com", SHOP),
        ("3", "nextcloud-db-1", "postgres:latest", "nextcloud", NEXTCLOUD),
        ("4", "adminer", "adminer:latest", "", ""),
        ("5", "noust-db-pg15", "postgres:15", "noust-db-pg15", ""),
    ):
        repository, _, tag = image.rpartition(":")
        box.image(digit, repository, tag)
        box.box(
            name,
            image=image,
            project=project,
            working_dir=where,
            policy="unless-stopped",
            instance="pg15" if name == "noust-db-pg15" else "",
        )
    return box


def updates(
    box: DockerMachine,
    store: NoustStore,
    *,
    scope: Scope | None = None,
    sees: set[str] | None = None,
) -> ImageUpdates:
    gate = DockerGate(
        box,
        store=store,
        registry=lambda: REGISTRY,
        scope=scope,
        sees_app=(lambda domain: domain in sees) if sees is not None else None,
        disk=lambda path: None,
    )
    return ImageUpdates(
        gate,
        versions=ImageVersions(gate, store=store, runner=box),
        store=store,
        now=lambda: NOW,
    )


def save(store: NoustStore, *records: VersionRecord) -> None:
    for item in records:
        ImageVersionStore(store).save(item)


def a_good_copy(store: NoustStore, finished: str) -> None:
    DrillRecords(store).save(
        DrillReport(
            "shop.example.com",
            "backup-1",
            "manual",
            finished,
            finished,
            [DrillStep("archive", True, "ok"), DrillStep("postgresql/shop", True, "loaded")],
        )
    )


class TestAnImageOfAnApplication:
    def test_it_is_updated_through_the_application_and_the_plan_says_what_that_does(
        self, store: NoustStore
    ) -> None:
        save(store, record("ghcr.io/acme/shop:2"))

        plan = updates(machine(), store).plan_for("ghcr.io/acme/shop:2")

        assert plan.mode == "with_net"
        assert plan.app == "shop.example.com"
        assert plan.apply
        assert plan.steps[0] == "pull"
        assert plan.steps[-3:] == [
            "recreate_behind_health",
            "keep_previous",
            "roll_back_on_failure",
        ]

    def test_what_moves_with_it_is_every_image_of_the_application_that_has_a_new_version(
        self, store: NoustStore
    ) -> None:
        save(store, record("ghcr.io/acme/shop:2"), record("postgres:16"))

        plan = updates(machine(), store).plan_for("ghcr.io/acme/shop:2")

        assert plan.images == ["ghcr.io/acme/shop:2", "postgres:16"]

    def test_a_stack_with_a_database_of_its_own_is_copied_first_and_stops_if_it_cannot(
        self, store: NoustStore
    ) -> None:
        save(store, record("ghcr.io/acme/shop:2"))

        plan = updates(machine(), store).plan_for("ghcr.io/acme/shop:2")

        assert "copy_databases" in plan.steps
        assert plan.steps.index("copy_databases") < plan.steps.index("recreate_behind_health")

    def test_an_application_with_no_database_in_it_is_not_copied(self, store: NoustStore) -> None:
        box = DockerMachine()
        box.image("1", "ghcr.io/acme/shop", "2")
        box.box(
            "shop-web-1", image="ghcr.io/acme/shop:2", project="shop-example-com", working_dir=SHOP
        )
        save(store, record("ghcr.io/acme/shop:2"))

        plan = updates(box, store).plan_for("ghcr.io/acme/shop:2")

        assert "copy_databases" not in plan.steps

    def test_prepared_it_is_the_application_to_update(self, store: NoustStore) -> None:
        save(store, record("ghcr.io/acme/shop:2"))

        prepared = updates(machine(), store).prepare("ghcr.io/acme/shop:2")

        assert prepared.domain == "shop.example.com"
        assert prepared.plan.mode == "with_net"


class TestAMajorVersion:
    def test_a_database_waits_for_a_copy_that_was_proven_to_restore(
        self, store: NoustStore
    ) -> None:
        save(store, record("postgres:16", change="major"))

        plan = updates(machine(), store).plan_for("postgres:16")

        assert plan.major
        assert plan.needs == "verified_copy"
        assert not plan.apply
        assert plan.backup is not None
        assert plan.backup.state == "missing"
        assert "noust backup drill shop.example.com" in plan.backup.via

    def test_the_refusal_says_which_door_is_shut_and_the_command_that_opens_it(
        self, store: NoustStore
    ) -> None:
        save(store, record("postgres:16", change="major"))

        with pytest.raises(DockerBlockedError) as caught:
            updates(machine(), store).prepare("postgres:16", accept_major=True)

        assert caught.value.reason == "verified_copy"
        assert "noust backup drill shop.example.com" in caught.value.via

    def test_a_copy_from_two_days_ago_is_not_the_copy_of_today(self, store: NoustStore) -> None:
        save(store, record("postgres:16", change="major"))
        a_good_copy(store, "2026-11-10T08:00:00+00:00")

        plan = updates(machine(), store).plan_for("postgres:16")

        assert plan.backup is not None
        assert (plan.backup.state, plan.backup.last_ok_at) == ("stale", "2026-11-10T08:00:00+00:00")
        assert not plan.apply

    def test_a_drill_that_loaded_no_database_does_not_prove_one(self, store: NoustStore) -> None:
        save(store, record("postgres:16", change="major"))
        DrillRecords(store).save(
            DrillReport(
                "shop.example.com",
                "backup-1",
                "manual",
                "2026-11-12T08:00:00+00:00",
                "2026-11-12T08:00:00+00:00",
                [DrillStep("archive", True, "ok"), DrillStep("unpack", True, "ok")],
            )
        )

        plan = updates(machine(), store).plan_for("postgres:16")

        assert plan.backup is not None
        assert plan.backup.state == "missing"

    def test_with_a_fresh_copy_it_is_a_decision_to_take_on_purpose(self, store: NoustStore) -> None:
        save(store, record("postgres:16", change="major"))
        a_good_copy(store, "2026-11-12T08:00:00+00:00")
        found = updates(machine(), store)

        assert found.plan_for("postgres:16").apply
        with pytest.raises(DockerBlockedError) as caught:
            found.prepare("postgres:16")
        assert caught.value.reason == "accept_major"
        assert found.prepare("postgres:16", accept_major=True).domain == "shop.example.com"

    def test_a_program_that_is_not_a_database_needs_the_decision_and_no_copy(
        self, store: NoustStore
    ) -> None:
        save(store, record("ghcr.io/acme/shop:2", change="major"))
        found = updates(machine(), store)

        plan = found.plan_for("ghcr.io/acme/shop:2")

        assert (plan.major, plan.needs, plan.apply) == (True, "accept_major", True)
        with pytest.raises(DockerBlockedError):
            found.prepare("ghcr.io/acme/shop:2")


class TestWhatNoustDidNotCreate:
    def test_a_stack_is_brought_to_noust_before_it_is_updated(self, store: NoustStore) -> None:
        save(store, record("postgres:latest", change="major"))
        found = updates(machine(), store)

        plan = found.plan_for("postgres:latest")

        assert plan.mode == "bring_first"
        assert plan.stack == "nextcloud"
        assert not plan.apply
        with pytest.raises(DockerBlockedError) as caught:
            found.prepare("postgres:latest", accept_major=True)
        assert caught.value.reason == "bring_first"
        assert "nextcloud" in caught.value.via

    def test_a_container_on_its_own_is_not_updated_blind_and_says_why(
        self, store: NoustStore
    ) -> None:
        save(store, record("adminer:latest", change="unknown"))
        found = updates(machine(), store)

        plan = found.plan_for("adminer:latest")

        assert (plan.mode, plan.apply) == ("loose", False)
        with pytest.raises(DockerBlockedError) as caught:
            found.prepare("adminer:latest")
        assert caught.value.reason == "loose"
        assert "does not know how it was created" in str(caught.value)


class TestWhatAnEngineOrNoustOwns:
    def test_a_database_engine_is_upgraded_through_the_engine(self, store: NoustStore) -> None:
        save(store, record("postgres:15"))
        found = updates(machine(), store)

        assert found.plan_for("postgres:15").mode == "via_owner"
        with pytest.raises(DockerOwnedError) as caught:
            found.prepare("postgres:15")
        assert "noust db instance upgrade pg15" in caught.value.details

    def test_an_engine_nobody_looked_at_does_not_say_it_has_a_new_version(
        self, store: NoustStore
    ) -> None:
        plan = updates(machine(), store).plan_for("postgres:15")

        assert (plan.mode, plan.state) == ("via_owner", "not_looked")
        assert plan.checked_at is None


class TestWhenThereIsNothingToUpdateTo:
    def test_an_image_nobody_looked_at_has_nothing_to_update_to(self, store: NoustStore) -> None:
        found = updates(machine(), store)

        plan = found.plan_for("ghcr.io/acme/shop:2")

        assert (plan.mode, plan.state) == ("unknown", "not_looked")
        with pytest.raises(DockerBlockedError) as caught:
            found.prepare("ghcr.io/acme/shop:2")
        assert caught.value.reason == "not_looked"

    def test_a_look_that_could_not_tell_says_what_the_registry_said(
        self, store: NoustStore
    ) -> None:
        save(store, record("ghcr.io/acme/shop:2", state="unknown", detail="unauthorized"))
        found = updates(machine(), store)

        with pytest.raises(DockerBlockedError) as caught:
            found.prepare("ghcr.io/acme/shop:2")

        assert caught.value.reason == "unknown"
        assert "unauthorized" in caught.value.details

    def test_a_current_image_is_current(self, store: NoustStore) -> None:
        save(store, record("ghcr.io/acme/shop:2", state="current"))
        found = updates(machine(), store)

        assert found.plan_for("ghcr.io/acme/shop:2").mode == "none"
        with pytest.raises(DockerBlockedError) as caught:
            found.prepare("ghcr.io/acme/shop:2")
        assert caught.value.reason == "nothing_new"

    def test_a_version_an_application_keeps_to_go_back_is_not_something_to_update(
        self, store: NoustStore
    ) -> None:
        box = machine()
        box.image("9", "shop-example-com-web", "noust-20261101-100000-abc1234")

        plan = updates(box, store).plan_for("shop-example-com-web:noust-20261101-100000-abc1234")

        assert plan.mode == "rollback"


def test_what_a_person_may_not_see_is_not_there_to_ask_about(store: NoustStore) -> None:
    save(store, record("ghcr.io/acme/shop:2"))
    a_team_that_sees_none = updates(machine(), store, scope=Scope.nothing(), sees=set())

    with pytest.raises(DockerNotFoundError):
        a_team_that_sees_none.plan_for("ghcr.io/acme/shop:2")
    with pytest.raises(DockerNotFoundError):
        a_team_that_sees_none.prepare("ghcr.io/acme/shop:2", accept_major=True)
