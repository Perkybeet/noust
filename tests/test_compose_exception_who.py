# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who can record the compose exception, the one thing that lets a stack have what gives the server away.

It is equivalent to root, so it is the permission of root-equivalent things and sudo mode. A team role
never holds that permission (a team administrator runs applications and cannot become root through
them), and a central whose node did not allow host access does not either, whatever its ceiling:
``admin`` is the most a central may do and still not reach the host. The same holds for adopting a
stack that runs with what gives the server away, which records the same exception.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.deployers.helpers import sandbox as build_sandbox
from noust.fleet.policy import ACCESS_LEVELS, FleetAccess, permits
from noust.web.permissions import Permission
from noust.web.permissions.routes_sandbox import ROUTES
from noust.web.permissions.team_roles import TEAM_ROLE_PERMISSIONS, TENANT_PERMISSIONS
from tests.support.teams_support import World, build_world

EXCEPTION = ("PUT", "/api/apps/{domain}/sandbox/compose-exception")


class TestTheRoute:
    def test_recording_it_is_root_equivalent(self) -> None:
        assert ROUTES[EXCEPTION] == Permission.ROOT_EQUIVALENT

    def test_only_taking_it_back_is_the_permission_to_manage_applications(self) -> None:
        # Revoking only ever takes something away.
        assert ROUTES[("DELETE", "/api/apps/{domain}/sandbox/compose-exception")] == (
            Permission.APPS_MANAGE
        )


class TestNoTeamRole:
    def test_the_permission_is_not_one_a_team_can_own(self) -> None:
        assert Permission.ROOT_EQUIVALENT not in TENANT_PERMISSIONS

    @pytest.mark.parametrize("role", sorted(TEAM_ROLE_PERMISSIONS))
    def test_no_team_role_holds_it(self, role: str) -> None:
        assert Permission.ROOT_EQUIVALENT not in TEAM_ROLE_PERMISSIONS[role]


class TestNoCentralWithoutHostAccess:
    @pytest.mark.parametrize("level", ACCESS_LEVELS)
    def test_a_ceiling_that_does_not_allow_host_access_never_records_it(self, level: str) -> None:
        assert not permits(FleetAccess(level), Permission.ROOT_EQUIVALENT)  # type: ignore[arg-type]

    @pytest.mark.parametrize("level", ["read", "deploy"])
    def test_host_access_alone_does_not_make_a_ceiling_higher(self, level: str) -> None:
        assert not permits(FleetAccess(level, True), Permission.ROOT_EQUIVALENT)  # type: ignore[arg-type]

    def test_an_admin_ceiling_with_the_nodes_explicit_yes_does(self) -> None:
        assert permits(FleetAccess("admin", True), Permission.ROOT_EQUIVALENT)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    built = build_world(tmp_path, monkeypatch)
    yield built
    built.store.close()


def exception_of(world: World) -> Any:
    return build_sandbox.get_compose_exception("shop.acme.example", store=world.store)


class TestOnTheServer:
    """The same, through the API as it is served."""

    URL = "/api/apps/shop.acme.example/sandbox/compose-exception"

    @pytest.mark.parametrize("who", ["alice", "omar", "dana"])
    def test_a_member_of_the_applications_team_cannot_record_it(
        self, world: World, who: str
    ) -> None:
        response = world.client.put(
            self.URL, json={"reason": "I need the socket"}, headers=world.as_(who)
        )

        assert response.status_code in (403, 404), response.text
        assert exception_of(world) is None

    def test_a_member_of_another_team_does_not_even_see_the_application(self, world: World) -> None:
        response = world.client.put(self.URL, json={"reason": "x"}, headers=world.as_("bob"))

        assert response.status_code == 404
        assert exception_of(world) is None

    def test_an_administrator_of_the_server_can(self, world: World) -> None:
        response = world.client.put(
            self.URL, json={"reason": "traefik needs it"}, headers=world.as_("admin")
        )

        assert response.status_code == 200, response.text
        assert exception_of(world).allowed_by  # who recorded it is kept

    def test_adopting_what_gives_the_server_away_is_refused_to_a_team_administrator(
        self, world: World
    ) -> None:
        response = world.client.post(
            "/api/apps/adopt",
            json={"domain": "new.acme.example", "path": "/opt/new", "accept_host_access": True},
            headers=world.as_("alice"),
        )

        assert response.status_code in (403, 404), response.text
