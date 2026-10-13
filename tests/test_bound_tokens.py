# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A token bound to a team, a project or an environment sees nothing else (I-34).

A client's pipeline gets a token for its project. Whoever owns the token, even
an administrator of the whole server, the token reaches that project and holds
at most what its owner holds there, never what reaches the machine; and it
never reaches more than its owner does, so a token outlives neither the
owner's place in the team nor the scope itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import SecurityError
from noust.core.store import NoustStore
from noust.core.teams import Scope
from noust.web.permissions import Permission
from noust.web.permissions.access import AccessManager
from noust.web.permissions.roles import ADMIN
from noust.web.permissions.team_roles import bound_fields, bound_reach, team_fields
from noust.web.server import get_token_manager
from tests.teams_support import World, build_world, make_app, make_person

P = Permission

SHOP = "shop.acme.example"
MOBILE = "m.acme.example"
STAGING = "s.acme.example"
BLOG = "blog.globex.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    """Two teams; acme has a second project with a staging environment."""
    built = build_world(tmp_path, monkeypatch)
    built.teams.create_project("acme", "Mobile", slug="mobile")
    built.teams.create_environment("acme", "mobile", "Staging", slug="staging")
    mobile = built.teams.resolve_target(Scope.everything(), team="acme", project="mobile")
    staging = built.teams.resolve_target(
        Scope.everything(), team="acme", project="mobile", environment="staging"
    )
    make_app(built.store, MOBILE, mobile, tmp_path)
    make_app(built.store, STAGING, staging, tmp_path)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def issue(
    username: str,
    *,
    team: str | None = None,
    project: str | None = None,
    environment: str | None = None,
    scope: str = "admin",
    permissions: list[str] | None = None,
) -> dict[str, str]:
    """
    Issue a token for an account, bound to a scope, and return its header.

    Args:
        username: The owner.
        team: The team to bind it to.
        project: The project.
        environment: The environment.
        scope: The token's scope.
        permissions: What to narrow it to.

    Returns:
        The Authorization header.
    """
    manager = get_token_manager()
    account = manager.accounts.find(username)
    assert account is not None
    bound = AccessManager().bound_scope(team=team, project=project, environment=environment)
    issued = manager.create_api_token(
        f"{username}-{team}-{project}-{environment}-{scope}",
        scope,
        None,
        owner=account,
        permissions=permissions,
        allow_elevated=True,
        bound=bound,
    )
    return {"Authorization": f"Bearer {issued['token']}"}


def listed(world: World, headers: dict[str, str]) -> set[str]:
    """
    Args:
        world: The server.
        headers: A credential.

    Returns:
        The domains of the applications it lists.
    """
    response = world.client.get("/api/apps", headers=headers)
    assert response.status_code == 200, response.text
    return {item["domain"] for item in response.json()["apps"]}


class TestWhatABoundTokenSees:
    def test_a_token_bound_to_a_project_sees_that_project_even_for_an_administrator(
        self, world: World
    ) -> None:
        headers = issue("root-admin", team="acme", project="mobile")

        assert listed(world, headers) == {MOBILE, STAGING}

    def test_the_same_owner_without_the_binding_sees_everything(self, world: World) -> None:
        assert listed(world, issue("root-admin")) == {SHOP, MOBILE, STAGING, BLOG}

    def test_a_token_bound_to_a_team_sees_the_team_and_no_other(self, world: World) -> None:
        headers = issue("root-admin", team="acme")

        assert listed(world, headers) == {SHOP, MOBILE, STAGING}
        assert world.client.get(f"/api/apps/{BLOG}", headers=headers).status_code == 404

    def test_a_token_bound_to_an_environment_sees_that_environment_only(self, world: World) -> None:
        headers = issue("root-admin", team="acme", project="mobile", environment="staging")

        assert listed(world, headers) == {STAGING}
        assert world.client.get(f"/api/apps/{MOBILE}", headers=headers).status_code == 404

    def test_what_is_outside_reads_as_absent_not_forbidden(self, world: World) -> None:
        headers = issue("root-admin", team="acme", project="mobile")

        outside = world.client.get(f"/api/apps/{SHOP}", headers=headers)
        missing = world.client.get("/api/apps/nope.example", headers=headers)

        assert outside.status_code == missing.status_code == 404
        assert outside.json()["error"] == missing.json()["error"]

    def test_a_bound_token_does_not_reach_the_server_itself(self, world: World) -> None:
        headers = issue("root-admin", team="acme", project="mobile")

        server = world.client.get("/api/services", headers=headers)
        overview = world.client.get("/api/overview", headers=headers)

        assert server.status_code == 403
        assert server.json()["error"] == "instance_scope_required"
        assert overview.status_code == 403

    def test_the_streams_and_histories_are_bound_too(self, world: World) -> None:
        headers = issue("root-admin", team="acme", project="mobile")

        jobs = world.client.get("/api/jobs", headers=headers).json()["jobs"]
        deployments = world.client.get("/api/deployments", headers=headers).json()["items"]

        assert {j["metadata"].get("domain") for j in jobs} == {MOBILE, STAGING}
        assert {d["domain"] for d in deployments} == {MOBILE, STAGING}


class TestWhatABoundTokenHolds:
    def test_an_administrators_bound_token_manages_inside_and_nowhere_else(
        self, world: World
    ) -> None:
        headers = issue("root-admin", team="acme", project="mobile")

        inside = world.client.post(f"/api/apps/{MOBILE}/move", json={}, headers=headers)
        outside = world.client.post(f"/api/apps/{SHOP}/move", json={}, headers=headers)

        assert inside.status_code == 400  # allowed: it only lacks a destination
        assert outside.status_code == 404

    def test_it_never_holds_what_reaches_the_machine(self, world: World) -> None:
        account = get_token_manager().accounts.find("root-admin")
        assert account is not None
        owned = team_fields(account.id, "admin", ADMIN, world.store)
        mobile = world.teams.get_project("acme", "mobile")
        assert mobile is not None

        bound = bound_fields(owned, "project", mobile.id)

        assert P.ROOT_EQUIVALENT not in bound["permissions"]
        assert P.SERVER_MANAGE not in bound["permissions"]
        assert P.SETTINGS_MANAGE not in bound["permissions"]
        assert P.ACCOUNTS_MANAGE not in bound["permissions"]
        assert P.APPS_MANAGE in bound["permissions"]

    def test_the_floor_outside_the_scope_is_a_viewers(self, world: World) -> None:
        headers = issue("root-admin", team="acme", project="mobile")

        response = world.client.patch(
            "/api/config", json={"path": "server.name", "value": "x"}, headers=headers
        )

        assert response.status_code == 403

    def test_a_token_narrowed_to_reading_stays_narrow_inside_its_scope(self, world: World) -> None:
        headers = issue("root-admin", team="acme", project="mobile", permissions=[P.APPS_READ])

        read = world.client.get(f"/api/apps/{MOBILE}", headers=headers)
        manage = world.client.post(f"/api/apps/{MOBILE}/move", json={}, headers=headers)

        assert read.status_code == 200
        assert manage.status_code == 403

    def test_a_members_bound_token_holds_what_the_member_holds_there(self, world: World) -> None:
        make_person(world.teams, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "admin", team="acme", project="mobile")

        headers = issue("pia", team="acme", project="mobile")

        assert (
            world.client.post(f"/api/apps/{MOBILE}/move", json={}, headers=headers).status_code
            == 400
        )
        assert listed(world, headers) == {MOBILE, STAGING}


class TestWhoMayIssueOne:
    def test_the_owner_must_reach_the_scope(self, world: World) -> None:
        make_person(world.teams, "pia", "viewer", team="globex")

        with pytest.raises(SecurityError, match="does not reach"):
            issue("pia", team="acme", project="mobile")

    def test_an_ownerless_token_cannot_be_bound(self, world: World) -> None:
        with pytest.raises(SecurityError, match="belongs to an account"):
            get_token_manager().create_api_token("orphan", "read", None, bound=("project", 1))

    def test_a_token_bound_to_a_scope_that_does_not_exist_is_refused(self, world: World) -> None:
        account = get_token_manager().accounts.find("root-admin")
        assert account is not None

        with pytest.raises(SecurityError):
            get_token_manager().create_api_token(
                "ghost", "read", None, owner=account, bound=("galaxy", 1)
            )
        with pytest.raises(SecurityError, match="does not reach"):
            get_token_manager().create_api_token(
                "ghost2",
                "read",
                None,
                owner=get_token_manager().accounts.find("alice"),
                bound=("project", 99999),
            )

    def test_the_names_must_exist(self, world: World) -> None:
        from noust.core.teams import TeamNotFoundError

        with pytest.raises(TeamNotFoundError):
            AccessManager().bound_scope(team="acme", project="nowhere")

    def test_a_project_cannot_be_named_without_its_team(self, world: World) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="team"):
            AccessManager().bound_scope(project="mobile")

    def test_naming_nothing_binds_nothing(self, world: World) -> None:
        assert AccessManager().bound_scope() is None


class TestABoundTokenNeverOutlivesWhatItIsBoundTo:
    def test_leaving_the_team_ends_what_the_token_reaches(self, world: World) -> None:
        make_person(world.teams, "pia", "viewer", team="acme", team_role="admin")
        headers = issue("pia", team="acme")
        assert listed(world, headers) == {SHOP, MOBILE, STAGING}

        world.teams.remove_member("acme", "pia")

        assert listed(world, headers) == set()
        assert world.client.get(f"/api/apps/{SHOP}", headers=headers).status_code == 404

    def test_losing_the_role_on_the_project_ends_it_too(self, world: World) -> None:
        make_person(world.teams, "pia", "viewer", team="globex")
        manager = AccessManager(world.store)
        binding = manager.bind("pia", "viewer", team="acme", project="mobile")
        headers = issue("pia", team="acme", project="mobile")
        assert listed(world, headers) == {MOBILE, STAGING}

        manager.unbind(binding.id)

        assert listed(world, headers) == set()

    def test_the_scope_being_deleted_leaves_a_token_that_reaches_nothing(
        self, world: World
    ) -> None:
        world.teams.create_project("acme", "Scratch", slug="scratch")
        headers = issue("root-admin", team="acme", project="scratch")
        assert listed(world, headers) == set()

        world.teams.delete_project("acme", "scratch")

        assert listed(world, headers) == set()
        assert world.client.get(f"/api/apps/{SHOP}", headers=headers).status_code == 404

    def test_a_token_that_is_not_bound_still_follows_its_owner(self, world: World) -> None:
        make_person(world.teams, "pia", "viewer", team="acme", team_role="admin")
        headers = issue("pia")
        assert SHOP in listed(world, headers)

        world.teams.remove_member("acme", "pia")

        assert SHOP not in listed(world, headers)


class TestTheTokenApi:
    def test_the_list_says_what_each_token_is_bound_to(self, world: World) -> None:
        issue("root-admin", team="acme", project="mobile")
        issue("root-admin", team="acme")
        issue("root-admin")

        tokens = world.client.get("/api/auth/tokens", headers=world.as_("sec")).json()["tokens"]

        bound = {t["name"]: t["bound"] for t in tokens}
        assert bound["root-admin-acme-mobile-None-admin"] == "acme/mobile"
        assert bound["root-admin-acme-None-None-admin"] == "acme"
        assert bound["root-admin-None-None-None-admin"] is None

    def test_a_signed_in_person_binds_a_token_by_naming_the_scope(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.api import auth as auth_api

        admin = get_token_manager().accounts.find("root-admin")
        monkeypatch.setattr(auth_api, "_account_of", lambda session: admin)

        response = world.client.post(
            "/api/auth/tokens",
            json={"name": "client-ci", "scope": "read", "team": "acme", "project": "mobile"},
            headers=world.as_("sec"),
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["bound"] == "acme/mobile"
        assert listed(world, {"Authorization": f"Bearer {body['token']}"}) == {MOBILE, STAGING}

    def test_the_master_token_has_no_account_and_so_binds_nothing(self, world: World) -> None:
        response = world.client.post(
            "/api/auth/tokens",
            json={"name": "x", "scope": "read", "team": "acme"},
            headers=world.master,
        )

        assert response.status_code == 400
        assert "belongs to an account" in response.json()["detail"]

    def test_a_project_without_its_team_is_a_validation_error(self, world: World) -> None:
        response = world.client.post(
            "/api/auth/tokens",
            json={"name": "x", "scope": "read", "project": "mobile"},
            headers=world.as_("sec"),
        )

        assert response.status_code == 400


class TestTheReach:
    def test_an_instance_wide_account_reaches_every_scope(self, world: World) -> None:
        account = get_token_manager().accounts.find("root-admin")
        assert account is not None
        fields = team_fields(account.id, "admin", frozenset({P.SELF}), world.store)

        assert bound_reach(fields, "team", world.targets["acme"].team_id)
        assert bound_reach(fields, "project", world.targets["acme"].project_id)

    def test_a_member_reaches_its_teams_and_not_the_others(self, world: World) -> None:
        account = get_token_manager().accounts.find("alice")
        assert account is not None
        fields = team_fields(account.id, "viewer", frozenset({P.SELF}), world.store)

        assert bound_reach(fields, "team", world.targets["acme"].team_id)
        assert bound_reach(fields, "project", world.targets["acme"].project_id)
        assert not bound_reach(fields, "team", world.targets["globex"].team_id)
        assert not bound_reach(fields, "project", world.targets["globex"].project_id)
        assert not bound_reach(fields, "galaxy", 1)

    def test_a_project_role_does_not_reach_the_team_above(self, world: World) -> None:
        make_person(world.teams, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")
        account = get_token_manager().accounts.find("pia")
        assert account is not None
        fields = team_fields(account.id, "viewer", frozenset({P.SELF}), world.store)

        assert bound_reach(fields, "project", world.teams.get_project("acme", "mobile").id)  # type: ignore[union-attr]
        assert not bound_reach(fields, "team", world.targets["acme"].team_id)
