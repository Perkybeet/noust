# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a role holds in a team, and who sees which teams (I-01, I-04, I-10).

The five roles of the server stay what they were (and are now instance roles);
the four roles of a team map onto the same permission sets, cut to what a team
can own, so that a team's admin can run its applications and cannot become root
through them. A credential carries its teams in its payload, written where it is
verified, and a credential that cannot be placed in a team sees nothing rather
than everything.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.accounts import AccountManager, passwords
from noust.core.store import NoustStore
from noust.core.teams import TEAM_ROLES, Scope, TeamStore
from noust.fleet.policy import FleetAccess, permits
from noust.web.permissions import ALL_PERMISSIONS, DESCRIPTIONS, Permission
from noust.web.permissions.principal import scope_of
from noust.web.permissions.roles import ADMIN, AUDITOR, OPERATOR, SECURITY, VIEWER
from noust.web.permissions.team_roles import (
    TEAM_ADMIN,
    TEAM_OPERATOR,
    TEAM_OWNER,
    TEAM_ROLE_PERMISSIONS,
    TEAM_VIEWER,
    TENANT_PERMISSIONS,
    instance_permissions_of,
    permissions_for_team_role,
    permissions_in_team,
    team_fields,
    team_permissions_union,
)

P = Permission

#: What reaches the machine, or governs people and the audit trail: no team role holds it.
NEVER_A_TEAMS = {
    P.ROOT_EQUIVALENT,
    P.SERVER_READ,
    P.SERVER_MANAGE,
    P.SERVER_HOST_ACCESS,
    P.SETTINGS_READ,
    P.SETTINGS_MANAGE,
    P.SECURITY_MANAGE,
    P.FLEET_READ,
    P.FLEET_MANAGE,
    P.ACCOUNTS_READ,
    P.ACCOUNTS_MANAGE,
    P.AUDIT_READ,
    P.AUDIT_MANAGE,
    P.COMPLIANCE_READ,
}


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A fresh store."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


class TestTheTeamRoles:
    def test_every_team_role_has_a_set_and_each_holds_the_one_below(self) -> None:
        assert set(TEAM_ROLE_PERMISSIONS) == set(TEAM_ROLES)
        assert TEAM_VIEWER < TEAM_OPERATOR < TEAM_ADMIN < TEAM_OWNER

    def test_they_map_onto_the_sets_of_the_instance_roles(self) -> None:
        assert TEAM_VIEWER == VIEWER & TENANT_PERMISSIONS
        assert TEAM_OPERATOR == OPERATOR & TENANT_PERMISSIONS
        assert TEAM_ADMIN == ADMIN & TENANT_PERMISSIONS
        assert TEAM_OWNER == TEAM_ADMIN | {P.TEAMS_MANAGE}

    @pytest.mark.parametrize("role", TEAM_ROLES)
    def test_none_reaches_the_machine(self, role: str) -> None:
        assert not permissions_for_team_role(role) & NEVER_A_TEAMS

    def test_what_a_team_admin_adds_to_an_operator_is_creating_and_secrets(self) -> None:
        added = TEAM_ADMIN - TEAM_OPERATOR

        assert P.APPS_MANAGE in added
        assert P.SECRETS_REVEAL in added
        assert {P.DATABASES_WRITE, P.DATABASES_MANAGE, P.BACKUPS_MANAGE} <= added
        assert P.ROOT_EQUIVALENT not in added

    def test_only_an_owner_manages_the_team_itself(self) -> None:
        assert [
            role for role in TEAM_ROLES if P.TEAMS_MANAGE in permissions_for_team_role(role)
        ] == ["owner"]

    def test_a_role_nobody_has_heard_of_holds_nothing(self) -> None:
        assert permissions_for_team_role("emperor") == frozenset()
        assert permissions_for_team_role(None) == frozenset()

    def test_the_union_of_a_principals_roles(self) -> None:
        assert team_permissions_union({1: "viewer", 2: "admin"}) == TEAM_ADMIN
        assert team_permissions_union({}) == frozenset()

    def test_the_new_permissions_are_declared_and_held_by_the_right_roles(self) -> None:
        assert {P.TEAMS_READ, P.TEAMS_MANAGE} <= ALL_PERMISSIONS
        assert P.TEAMS_READ in DESCRIPTIONS and P.TEAMS_MANAGE in DESCRIPTIONS
        for role in (VIEWER, OPERATOR, ADMIN, SECURITY, AUDITOR):
            assert P.TEAMS_READ in role
        assert P.TEAMS_MANAGE in ADMIN
        assert not {P.TEAMS_MANAGE} & (VIEWER | OPERATOR | SECURITY | AUDITOR)


class TestWhatAPrincipalHoldsInATeam:
    def payload(self, **fields: object) -> dict[str, object]:
        base: dict[str, object] = {
            "permissions": VIEWER | TEAM_ADMIN,
            "instance_permissions": VIEWER,
            "team_roles": {1: "admin", 2: "viewer"},
        }
        return {**base, **fields}

    def test_the_instance_role_is_the_floor_and_the_team_role_raises_it(self) -> None:
        payload = self.payload()

        assert permissions_in_team(payload, 1) == VIEWER | TEAM_ADMIN
        assert permissions_in_team(payload, 2) == VIEWER

    def test_a_team_the_principal_is_not_in_gives_the_floor_only(self) -> None:
        assert permissions_in_team(self.payload(), 3) == VIEWER
        assert permissions_in_team(self.payload(), None) == VIEWER

    def test_it_never_exceeds_what_the_credential_holds(self) -> None:
        # A token narrowed to reading holds no more in a team where its owner is an admin.
        narrowed = self.payload(permissions=frozenset({P.SELF, P.APPS_READ}))

        assert permissions_in_team(narrowed, 1) == frozenset({P.SELF, P.APPS_READ})

    def test_a_credential_that_is_no_teams_holds_what_it_holds_everywhere(self) -> None:
        master = {"permissions": ALL_PERMISSIONS, "grant": "compat"}

        assert permissions_in_team(master, 1) == ALL_PERMISSIONS
        assert instance_permissions_of(master) == ALL_PERMISSIONS

    def test_a_higher_instance_operator_keeps_what_it_has_in_every_team(self) -> None:
        operator = self.payload(
            permissions=OPERATOR | TEAM_VIEWER,
            instance_permissions=OPERATOR,
            team_roles={1: "viewer"},
        )

        assert P.APPS_DEPLOY in permissions_in_team(operator, 1)
        assert P.APPS_MANAGE not in permissions_in_team(operator, 1)


class TestWhoSeesWhichTeams:
    def test_the_master_token_a_central_and_a_token_nobody_owns_see_everything(self) -> None:
        assert not scope_of({"type": "master", "grant": "compat"}).restricted
        assert not scope_of({"fleet": True, "role": "viewer", "on_behalf_of": "ana"}).restricted
        assert not scope_of(
            {"type": "api_token", "scope": "read", "owner_account_id": None}
        ).restricted
        assert not scope_of(None).restricted
        assert not scope_of({}).restricted

    def test_an_account_that_carries_no_teams_sees_none(self) -> None:
        # Not "everything": a principal that cannot be placed in a team is not given the server.
        for payload in (
            {"account_id": 7, "role": "viewer"},
            {"type": "api_token", "owner_account_id": 7, "role": "viewer"},
        ):
            scope = scope_of(payload)
            assert scope.restricted and scope.team_ids == frozenset(), payload

    def test_an_account_sees_what_its_payload_says(self) -> None:
        scope = scope_of(
            {
                "account_id": 7,
                "team_roles": {"1": "admin", "4": "viewer"},
                "sees_all_teams": False,
            }
        )

        assert scope.team_ids == {1, 4}
        assert scope.role_in(1) == "admin"
        assert scope.account_id == 7

    def test_a_token_carries_the_teams_of_its_owner(self) -> None:
        scope = scope_of(
            {
                "type": "api_token",
                "owner_account_id": 9,
                "team_roles": {2: "operator"},
                "sees_all_teams": False,
            }
        )

        assert scope.team_ids == {2} and scope.account_id == 9

    def test_an_account_that_sees_all_says_so(self) -> None:
        assert not scope_of({"account_id": 7, "team_roles": {}, "sees_all_teams": True}).restricted

    def test_a_central_is_never_narrowed_by_what_it_forwards(self) -> None:
        # On a node, the fleet token is an instance principal whatever role the central names.
        for role in ("viewer", "operator", "admin", None):
            assert scope_of({"fleet": True, "role": role, "team_roles": {1: "viewer"}}).sees_all


class TestAnAccountsPayload:
    def test_it_holds_the_roles_floor_and_what_its_teams_add(self, store: NoustStore) -> None:
        teams = TeamStore(store)
        accounts = AccountManager(store)
        acme = teams.create_team("Acme", slug="acme")
        account = accounts.create(
            "maria", "viewer", password="correct horse battery staple", join_default_team=False
        )
        teams.add_member("acme", "maria", "admin")

        fields = team_fields(account.id, "viewer", VIEWER, store)

        assert fields["team_roles"] == {acme.id: "admin"}
        assert fields["sees_all_teams"] is False
        assert fields["instance_permissions"] == VIEWER
        assert fields["permissions"] == VIEWER | TEAM_ADMIN

    def test_an_administrator_sees_all_and_holds_what_it_always_held(
        self, store: NoustStore
    ) -> None:
        teams = TeamStore(store)
        accounts = AccountManager(store)
        teams.create_team("Acme", slug="acme")
        account = accounts.create("adam", "admin", password="correct horse battery staple")

        fields = team_fields(account.id, "admin", ADMIN, store)

        assert fields["sees_all_teams"] is True
        assert fields["permissions"] == ADMIN

    def test_when_the_teams_cannot_be_read_the_account_sees_none(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        def broken(self: TeamStore, account_id: int, instance_role: str) -> Scope:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(TeamStore, "scope_for", broken)

        fields = team_fields(1, "viewer", VIEWER, store)
        administrator = team_fields(1, "admin", ADMIN, store)

        assert fields["sees_all_teams"] is False and fields["team_roles"] == {}
        assert fields["permissions"] == VIEWER
        # An administrator's reach is its role's, which needs no query.
        assert administrator["sees_all_teams"] is True


class TestTheFleetCeilingForTheNewPermissions:
    def test_a_central_may_read_teams_but_managing_them_is_admin(self) -> None:
        assert permits(FleetAccess("read"), P.TEAMS_READ)
        assert not permits(FleetAccess("read"), P.TEAMS_MANAGE)
        assert not permits(FleetAccess("deploy"), P.TEAMS_MANAGE)
        assert permits(FleetAccess("admin"), P.TEAMS_MANAGE)
