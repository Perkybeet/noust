# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Starting, stopping and restarting a whole stack, judged by whose it is.

A stack is its containers, and what is done to it is done by Compose in its own order (what a service waits
for starts after it and stops before it). The gate judges the action as (owner, action, stack) before a
process starts, like every other action on Docker: a stack nobody of Noust made is the instance's to start,
stop and restart; one an application owns is changed through the application, with the command that does it;
and a database engine's through the engine. The stack is found among the projects Docker's own containers
name, so what a person types never reaches the command.
"""

from __future__ import annotations

import pytest

from noust.core.docker import names
from noust.core.docker.errors import DockerNotFoundError, DockerOwnedError
from noust.core.docker.gate import DockerGate
from noust.core.docker.models import NOBODY, ObjectKind, Owner, OwnerKind
from noust.core.docker.owners import AppRecord, Registry
from noust.core.teams import Scope
from tests.support.docker_machine import SHOP, DockerMachine

NEXTCLOUD = "/opt/stacks/nextcloud"
REGISTRY = Registry(apps=(AppRecord("shop.example.com", SHOP),), engines=("pg16",))


def machine() -> DockerMachine:
    box = DockerMachine()
    box.box("shop-web-1", project="shop-example-com", service="web", working_dir=SHOP)
    box.box("nextcloud-app-1", project="nextcloud", service="app", working_dir=NEXTCLOUD)
    box.box(
        "nextcloud-db-1",
        project="nextcloud",
        service="db",
        working_dir=NEXTCLOUD,
        state="exited",
        status="Exited (0) 2 days ago",
        policy="unless-stopped",
    )
    box.box(
        "noust-db-pg16",
        project=names.instance_project("pg16"),
        image="postgres:16",
        instance="pg16",
    )
    return box


def gate(box: DockerMachine, **options: object) -> DockerGate:
    return DockerGate(box, registry=lambda: REGISTRY, disk=lambda path: None, **options)  # type: ignore[arg-type]


class TestWhatNobodyMade:
    @pytest.mark.parametrize("verb", ["start", "stop", "restart"])
    def test_the_instance_does_it_with_compose_in_the_stacks_own_name(self, verb: str) -> None:
        box = machine()
        done = getattr(gate(box), f"{verb}_stack")("nextcloud")

        assert done.name == "nextcloud"
        wanted = ["docker", "compose", "-p", "nextcloud", verb]
        if verb != "start":
            wanted += ["--timeout", "30"]
        assert tuple(wanted) in box.calls

    def test_the_states_follow(self) -> None:
        box = machine()

        gate(box).stop_stack("nextcloud")

        assert {b.state for b in box.boxes.values() if b.project == "nextcloud"} == {"exited"}
        assert box.boxes["shop-web-1"].state == "running"

    def test_what_is_typed_is_never_part_of_a_command_unless_docker_listed_it(self) -> None:
        box = machine()

        for typed in ("nextcloud; rm -rf /", "--help", "../nextcloud", "other"):
            with pytest.raises(DockerNotFoundError):
                gate(box).stop_stack(typed)

        assert box.changes() == []


class TestWhatAnApplicationOwns:
    @pytest.mark.parametrize(
        ("verb", "door"),
        [
            ("start", "noust start shop.example.com"),
            ("stop", "noust stop shop.example.com"),
            ("restart", "noust restart shop.example.com"),
        ],
    )
    def test_it_is_changed_through_the_application_and_nothing_runs(
        self, verb: str, door: str
    ) -> None:
        box = machine()

        with pytest.raises(DockerOwnedError) as refused:
            getattr(gate(box), f"{verb}_stack")("shop-example-com")

        assert door in refused.value.details
        assert refused.value.owner_kind == "app"
        assert box.changes() == []

    def test_a_database_engines_stack_is_changed_through_the_engine(self) -> None:
        box = machine()

        with pytest.raises(DockerOwnedError) as refused:
            gate(box).stop_stack(names.instance_project("pg16"))

        assert "noust db instance stop pg16" in refused.value.details
        assert box.changes() == []


class TestWhatThePrincipalMaySee:
    def test_a_stack_a_team_does_not_own_is_not_there_to_stop(self) -> None:
        box = machine()
        mine = gate(box, scope=Scope.nothing(), sees_app=lambda domain: False)

        with pytest.raises(DockerNotFoundError):
            mine.stop_stack("nextcloud")
        assert box.changes() == []


class TestWhatARowMayOffer:
    def project(self, name: str):  # type: ignore[no-untyped-def]
        return gate(machine()).project(name)[0]

    def test_a_stack_nobody_made_offers_what_the_instance_may_do_and_not_to_remove_it(self) -> None:
        verdict = gate(machine()).can(self.project("nextcloud"))

        assert (verdict.start, verdict.stop, verdict.restart) == (True, True, True)
        assert verdict.remove is False
        assert verdict.blocked == ""

    def test_a_stack_of_an_application_offers_nothing_and_says_where_it_is_done(self) -> None:
        verdict = gate(machine()).can(self.project("shop-example-com"))

        assert (verdict.start, verdict.stop, verdict.restart) == (False, False, False)
        assert verdict.blocked == "owner"
        assert "noust restart shop.example.com" in verdict.via

    def test_a_credential_that_may_not_change_docker_is_offered_nothing_and_told_nothing(
        self,
    ) -> None:
        verdict = gate(machine()).can(self.project("nextcloud"), manage=False)

        assert (verdict.start, verdict.stop, verdict.restart) == (False, False, False)
        assert verdict.blocked == "permission"
        assert verdict.via == ""

    def test_a_stack_noust_runs_itself_offers_nothing(self) -> None:
        box = DockerMachine()
        box.box("noust", project="noust", image="ghcr.io/perkybeet/noust:4.3")
        seen = DockerGate(
            box,
            registry=lambda: Registry(own_ids=frozenset({box.boxes["noust"].id})),
            disk=lambda path: None,
        )

        verdict = seen.can(seen.project("noust")[0])

        assert (verdict.start, verdict.stop, verdict.restart) == (False, False, False)
        assert verdict.blocked == "owner"


def test_the_judgement_of_a_stack_is_the_one_of_its_owner() -> None:
    from noust.core.docker.judge import can

    verdict = can(
        Owner(OwnerKind.APP, "shop.example.com"),
        ObjectKind.PROJECT,
        "shop",
        restricted=False,
        sees_app=lambda domain: True,
    )
    free = can(NOBODY, ObjectKind.PROJECT, "x", restricted=False, sees_app=lambda domain: True)

    assert not (verdict.start or verdict.stop or verdict.restart)
    assert free.start and free.stop and free.restart and not free.remove
