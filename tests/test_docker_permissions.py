# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who may look at Docker and who may change it, and what is recorded when they do.

Docker is the machine's, not a team's: a container that nobody of Noust made is the instance's, and the
notices of 2026 against the tools that manage Docker are about a member of a team reaching what is not
the team's. The two permissions are held to that here: reading is what reading the server is (every role),
changing is what changing the server is (the administrator), a team role never holds either, a central's
ceiling narrows them like every other permission, and what changes something is in the audit catalog.
"""

from __future__ import annotations

from noust.core.audit.catalog import EVENTS
from noust.fleet.policy import FleetAccess, permits
from noust.web.permissions import ALL_PERMISSIONS, DESCRIPTIONS, Permission
from noust.web.permissions.roles import (
    ADMIN,
    ADMIN_STANDARD,
    AUDITOR,
    OPERATOR,
    SECURITY,
    VIEWER,
)
from noust.web.permissions.team_roles import TEAM_ROLE_PERMISSIONS, TENANT_PERMISSIONS

P = Permission


class TestThePermissions:
    def test_there_are_two_and_each_is_described(self) -> None:
        assert P.DOCKER_READ == "docker.read"
        assert P.DOCKER_MANAGE == "docker.manage"
        assert {P.DOCKER_READ, P.DOCKER_MANAGE} <= ALL_PERMISSIONS
        assert DESCRIPTIONS[P.DOCKER_READ] and DESCRIPTIONS[P.DOCKER_MANAGE]

    def test_reading_docker_is_what_reading_the_server_is(self) -> None:
        for role in (VIEWER, OPERATOR, ADMIN, SECURITY, AUDITOR, ADMIN_STANDARD):
            assert (P.DOCKER_READ in role) == (P.SERVER_READ in role)

    def test_changing_docker_is_what_changing_the_server_is(self) -> None:
        for role in (VIEWER, OPERATOR, ADMIN, SECURITY, AUDITOR, ADMIN_STANDARD):
            assert (P.DOCKER_MANAGE in role) == (P.SERVER_MANAGE in role)

    def test_an_operator_looks_and_does_not_change_it(self) -> None:
        assert P.DOCKER_READ in OPERATOR and P.DOCKER_MANAGE not in OPERATOR

    def test_the_administrator_changes_it(self) -> None:
        assert P.DOCKER_MANAGE in ADMIN and P.DOCKER_MANAGE in ADMIN_STANDARD


class TestATeamNeverHoldsThem:
    def test_neither_is_something_a_team_can_own(self) -> None:
        assert not {P.DOCKER_READ, P.DOCKER_MANAGE} & TENANT_PERMISSIONS

    def test_no_team_role_holds_either(self) -> None:
        for role, held in TEAM_ROLE_PERMISSIONS.items():
            assert not {P.DOCKER_READ, P.DOCKER_MANAGE} & held, role


class TestACentralIsHeldToItsCeiling:
    def test_a_central_that_may_only_read_may_read_docker_and_not_change_it(self) -> None:
        read = FleetAccess("read")
        assert permits(read, P.DOCKER_READ) and not permits(read, P.DOCKER_MANAGE)

    def test_one_that_may_deploy_does_not_change_docker_either(self) -> None:
        deploy = FleetAccess("deploy")
        assert permits(deploy, P.DOCKER_READ) and not permits(deploy, P.DOCKER_MANAGE)

    def test_one_with_the_admin_ceiling_may(self) -> None:
        assert permits(FleetAccess("admin"), P.DOCKER_MANAGE)


class TestWhatIsRecorded:
    def test_an_action_a_removal_a_cleanup_and_a_refusal_are_events_of_the_catalog(self) -> None:
        for name in ("docker.action", "docker.remove", "docker.cleanup", "docker.denied"):
            assert name in EVENTS, name

    def test_a_refusal_is_a_denial_and_the_changes_are_changes(self) -> None:
        assert EVENTS["docker.denied"].category == "denial"
        for name in ("docker.action", "docker.remove", "docker.cleanup"):
            assert EVENTS[name].category == "change"
