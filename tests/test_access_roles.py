# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Roles held on a project or an environment, and roles an administrator defines (I-03, I-07).

The same person is admin of one project and only reads another; the staging
environment of a project is run by someone who cannot touch production. A
custom role is a set of the permissions that exist, bound like a built-in one,
and never one that governs access and runs the server at once.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from noust.core.access import (
    AccessConflictError,
    AccessError,
    AccessNotFoundError,
    AccessStore,
)
from noust.core.access.model import validate_role_name
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.core.teams import Scope, TeamNotFoundError, visible_clause
from noust.web.permissions import ALL_PERMISSIONS, Permission
from noust.web.permissions.access import (
    GOVERNANCE,
    RUNNING,
    AccessManager,
    resolve_grants,
    separation_conflict,
)
from noust.web.permissions.principal import scope_of
from noust.web.permissions.team_roles import permissions_at, team_fields
from noust.web.server import get_token_manager
from tests.teams_support import Person, World, build_world, make_app, make_person

P = Permission

ACME_SHOP = "shop.acme.example"
MOBILE_APP = "m.acme.example"
STAGING_APP = "s.acme.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    """The two-team server, plus a second project and a staging environment in acme."""
    built = build_world(tmp_path, monkeypatch)
    built.teams.create_project("acme", "Mobile", slug="mobile")
    built.teams.create_environment("acme", "mobile", "Staging", slug="staging")
    built.targets["mobile"] = built.teams.resolve_target(
        Scope.everything(), team="acme", project="mobile", environment="production"
    )
    built.targets["staging"] = built.teams.resolve_target(
        Scope.everything(), team="acme", project="mobile", environment="staging"
    )
    make_app(built.store, MOBILE_APP, built.targets["mobile"], tmp_path)
    make_app(built.store, STAGING_APP, built.targets["staging"], tmp_path)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def add_person(world: World, username: str, role: str = "viewer", **kwargs: Any) -> Person:
    """
    Make an account with a token and keep it among the world's people.

    Args:
        world: The server.
        username: The account.
        role: Its instance role.
        **kwargs: What :func:`make_person` takes: ``team``, ``team_role``, ``permissions``.

    Returns:
        The person.
    """
    person = make_person(world.teams, username, role, **kwargs)
    world.people[username] = person
    return person


_TOKENS = count(1)


def headers_of(world: World, who: str) -> dict[str, str]:
    """
    A credential that holds what the person holds now.

    A token is capped by what its owner held when it was issued, and these tests
    give roles after the people exist, so each request is made with a new one.

    Args:
        world: The server.
        who: An account.

    Returns:
        The Authorization header of a token issued just now.
    """
    manager = get_token_manager()
    account = manager.accounts.find(who)
    assert account is not None
    issued = manager.create_api_token(
        f"{who}-{next(_TOKENS)}", "admin", None, owner=account, allow_elevated=True
    )
    return {"Authorization": f"Bearer {issued['token']}"}


def apps_seen(world: World, who: str) -> set[str]:
    """
    Args:
        world: The server.
        who: One of its people.

    Returns:
        The domains of the applications the person lists.
    """
    response = world.client.get("/api/apps", headers=headers_of(world, who))
    assert response.status_code == 200, response.text
    return {item["domain"] for item in response.json()["apps"]}


def can_manage(world: World, who: str, domain: str) -> int:
    """
    Probe ``apps.manage`` where an application lives, without changing anything.

    Args:
        world: The server.
        who: One of its people.
        domain: An application.

    Returns:
        The status: 400 means "allowed, say where" and 403 or 404 mean refused.
    """
    return world.client.post(
        f"/api/apps/{domain}/move", json={}, headers=headers_of(world, who)
    ).status_code


class TestTheNames:
    def test_a_role_is_named_by_a_slug(self) -> None:
        assert validate_role_name("  Release-Manager ") == "release-manager"

    @pytest.mark.parametrize("name", ["a", "-x", "x-", "Has Space", "a" * 41, "ünï", ""])
    def test_a_name_that_is_not_a_slug_is_refused(self, name: str) -> None:
        with pytest.raises(ValidationError):
            validate_role_name(name)

    @pytest.mark.parametrize(
        "name", ["viewer", "operator", "admin", "owner", "security", "auditor"]
    )
    def test_a_built_in_role_cannot_be_redefined(self, name: str) -> None:
        with pytest.raises(ValidationError, match="built-in"):
            validate_role_name(name)


class TestSeparationOfDuties:
    def test_governing_and_running_in_one_set_is_a_conflict(self) -> None:
        conflict = separation_conflict({P.SECURITY_MANAGE, P.APPS_DEPLOY})

        assert conflict == ([P.SECURITY_MANAGE], [P.APPS_DEPLOY])

    def test_either_half_alone_is_fine(self) -> None:
        assert separation_conflict({P.ACCOUNTS_MANAGE, P.AUDIT_READ, P.APPS_READ}) is None
        assert separation_conflict({P.APPS_DEPLOY, P.APPS_OPERATE, P.DATABASES_WRITE}) is None

    def test_the_built_in_roles_do_not_break_the_rule_they_come_from(self) -> None:
        from noust.web.permissions.roles import ROLE_PERMISSIONS

        for name, held in ROLE_PERMISSIONS.items():
            assert separation_conflict(held) is None, name

    def test_every_governing_permission_exists(self) -> None:
        assert GOVERNANCE <= ALL_PERMISSIONS
        assert RUNNING <= ALL_PERMISSIONS
        assert not GOVERNANCE & RUNNING


class TestDefiningRoles:
    def test_a_role_is_a_set_of_permissions_that_exist(self, world: World) -> None:
        manager = AccessManager(world.store)

        role = manager.create_role(
            "release-manager",
            [P.APPS_READ, P.APPS_DEPLOY],
            label="Release manager",
            description="Ships what the team approved",
            actor="sofia",
        )

        assert role.name == "release-manager"
        assert role.label == "Release manager"
        assert set(role.permissions) == {P.SELF, P.APPS_READ, P.APPS_DEPLOY}
        assert role.created_by == "sofia"
        assert manager.data.get_role("release-manager") == role

    def test_an_unknown_permission_is_refused_and_named(self, world: World) -> None:
        with pytest.raises(ValidationError, match="apps.teleport"):
            AccessManager(world.store).create_role("odd", ["apps.read", "apps.teleport"])

    def test_a_role_with_nothing_in_it_is_refused(self, world: World) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            AccessManager(world.store).create_role("empty", [])

    def test_a_role_that_governs_and_runs_is_refused_with_both_halves_named(
        self, world: World
    ) -> None:
        with pytest.raises(AccessError) as caught:
            AccessManager(world.store).create_role("god", [P.SECURITY_MANAGE, P.APPS_DEPLOY])

        assert "security.manage" in str(caught.value)
        assert "apps.deploy" in (caught.value.details or "")
        assert AccessManager(world.store).data.get_role("god") is None

    def test_a_name_is_taken_once(self, world: World) -> None:
        manager = AccessManager(world.store)
        manager.create_role("ops", [P.APPS_OPERATE])

        with pytest.raises(AccessConflictError):
            manager.create_role("ops", [P.APPS_READ])

    def test_a_server_keeps_a_catalogue_not_a_database(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.web.permissions.access.MAX_ROLES", 2)
        manager = AccessManager(world.store)
        manager.create_role("one", [P.APPS_READ])
        manager.create_role("two", [P.APPS_READ])

        with pytest.raises(AccessConflictError, match="at most 2"):
            manager.create_role("three", [P.APPS_READ])

    def test_changing_a_role_changes_what_everyone_who_holds_it_holds(self, world: World) -> None:
        manager = AccessManager(world.store)
        manager.create_role("ci", [P.APPS_READ])

        manager.update_role("ci", permissions=[P.APPS_READ, P.APPS_DEPLOY], label="CI")

        role = manager.data.require_role("ci")
        assert P.APPS_DEPLOY in role.permissions
        assert role.label == "CI"

    def test_a_role_cannot_be_changed_into_a_conflict(self, world: World) -> None:
        manager = AccessManager(world.store)
        manager.create_role("ci", [P.APPS_READ])

        with pytest.raises(AccessError):
            manager.update_role("ci", permissions=[P.ACCOUNTS_MANAGE, P.APPS_MANAGE])

    def test_a_role_somebody_holds_is_not_removed(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.create_role("ci", [P.APPS_READ])
        manager.bind("pia", "ci", team="acme", project="mobile")

        with pytest.raises(AccessConflictError, match="held by 1"):
            manager.delete_role("ci")

        manager.unbind(manager.list_bindings("pia")[0].id)
        assert manager.delete_role("ci").name == "ci"
        with pytest.raises(AccessNotFoundError):
            manager.data.require_role("ci")


class TestBinding:
    def test_a_project_is_given_a_built_in_role(self, world: World) -> None:
        add_person(world, "pia", "viewer")

        binding = AccessManager(world.store).bind(
            "pia", "operator", team="acme", project="mobile", actor="alice"
        )

        assert (binding.scope_kind, binding.scope, binding.role) == (
            "project",
            "acme/mobile",
            "operator",
        )
        assert binding.created_by == "alice"

    def test_an_environment_is_given_a_role(self, world: World) -> None:
        add_person(world, "pia", "viewer")

        binding = AccessManager(world.store).bind(
            "pia", "admin", team="acme", project="mobile", environment="staging"
        )

        assert (binding.scope_kind, binding.scope) == ("environment", "acme/mobile/staging")

    def test_giving_a_role_again_replaces_it(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.bind("pia", "viewer", team="acme", project="mobile")

        manager.bind("pia", "admin", team="acme", project="mobile")

        found = manager.list_bindings("pia")
        assert [(b.scope, b.role) for b in found] == [("acme/mobile", "admin")]

    def test_a_built_in_role_on_a_whole_team_is_a_membership(self, world: World) -> None:
        add_person(world, "pia", "viewer")

        binding = AccessManager(world.store).bind("pia", "operator", team="globex")

        assert binding.id == 0
        assert world.teams.member("globex", "pia").role == "operator"

    def test_owner_governs_a_team_and_so_is_not_given_below_it(self, world: World) -> None:
        add_person(world, "pia", "viewer")

        with pytest.raises(ValidationError, match="governs a team's members"):
            AccessManager(world.store).bind("pia", "owner", team="acme", project="mobile")

    def test_a_built_in_team_role_is_not_a_role_of_the_server(self, world: World) -> None:
        add_person(world, "pia", "viewer")

        with pytest.raises(ValidationError, match="not of the whole server"):
            AccessManager(world.store).bind("pia", "admin", scope="instance")

    def test_a_security_officer_and_an_auditor_never_join_a_team(self, world: World) -> None:
        for name in ("sofia", "aurora"):
            with pytest.raises(ValidationError, match="never join a team"):
                AccessManager(world.store).bind(name, "viewer", team="acme", project="mobile")

    def test_the_project_and_the_account_must_exist(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)

        with pytest.raises(TeamNotFoundError):
            manager.bind("pia", "viewer", team="acme", project="nowhere")
        with pytest.raises(AccessNotFoundError, match="ghost"):
            manager.bind("ghost", "viewer", team="acme", project="mobile")
        with pytest.raises(AccessNotFoundError, match="no-such-role"):
            manager.bind("pia", "no-such-role", team="acme", project="mobile")

    def test_an_environment_needs_its_project(self, world: World) -> None:
        add_person(world, "pia", "viewer")

        with pytest.raises(ValidationError, match="project"):
            AccessManager(world.store).bind("pia", "viewer", team="acme", environment="staging")

    def test_a_role_with_nothing_a_project_can_own_is_refused(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.create_role("auditor-lite", [P.AUDIT_READ, P.ACCOUNTS_READ])

        with pytest.raises(ValidationError, match="holds nothing a project can own"):
            manager.bind("pia", "auditor-lite", team="acme", project="mobile")

    def test_a_role_on_the_server_adds_to_the_accounts_own(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.create_role("db-reader", [P.DATABASES_READ, P.BACKUPS_READ])
        manager.bind("pia", "db-reader", scope="instance")

        fields = team_fields(
            manager.account("pia").id, "viewer", frozenset({P.SELF, P.APPS_READ}), world.store
        )

        assert P.DATABASES_READ in fields["instance_permissions"]
        assert P.APPS_READ in fields["instance_permissions"]

    def test_a_role_on_the_server_cannot_make_an_account_govern_and_run(self, world: World) -> None:
        manager = AccessManager(world.store)
        manager.create_role("deployer", [P.APPS_DEPLOY, P.APPS_OPERATE])

        with pytest.raises(AccessError, match="govern access and run"):
            manager.bind("sofia", "deployer", scope="instance")

    def test_an_auditor_holds_nothing_besides_auditing(self, world: World) -> None:
        manager = AccessManager(world.store)
        manager.create_role("deployer", [P.APPS_DEPLOY])

        with pytest.raises(AccessError, match="auditor"):
            manager.bind("aurora", "deployer", scope="instance")

    def test_redefining_a_role_cannot_break_the_duty_of_an_account_that_holds_it(
        self, world: World
    ) -> None:
        manager = AccessManager(world.store)
        manager.create_role("reviewer", [P.APPS_READ])
        manager.bind("sofia", "reviewer", scope="instance")

        with pytest.raises(AccessError, match="govern access and run"):
            manager.update_role("reviewer", permissions=[P.APPS_DEPLOY])

    def test_the_ens_profile_gives_an_account_one_role(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = AccessManager(world.store)
        manager.create_role("db-reader", [P.DATABASES_READ])
        monkeypatch.setattr("noust.web.permissions.access._is_ens", lambda: True)

        with pytest.raises(AccessError, match="one role"):
            manager.bind("root-admin", "db-reader", scope="instance")
        add_person(world, "pia", "viewer")
        # Below the server it is a membership, not a second role.
        assert manager.bind("pia", "db-reader", team="acme", project="mobile").role == "db-reader"

    def test_deleting_what_a_binding_points_at_deletes_the_binding(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.bind("pia", "viewer", team="acme", project="mobile", environment="staging")
        manager.bind("pia", "viewer", team="globex")
        world.teams.create_environment("acme", "mobile", "Preview", slug="preview")
        manager.bind("pia", "viewer", team="acme", project="mobile", environment="preview")

        world.teams.delete_environment("acme", "mobile", "preview")

        assert {b.scope for b in manager.list_bindings("pia")} == {"acme/mobile/staging"}

    def test_removing_an_account_forgets_its_bindings(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.bind("pia", "viewer", team="acme", project="mobile")
        pia = manager.account("pia")

        with world.store._transaction() as cursor:
            cursor.execute("DELETE FROM accounts WHERE id = ?", (pia.id,))

        assert manager.data.list_bindings(account_id=pia.id) == []


class TestWhatIsHeldWhere:
    def test_an_account_holds_what_each_scope_gives_and_a_resource_unions_its_places(
        self, world: World
    ) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.bind("pia", "viewer", team="acme", project="mobile")
        manager.bind("pia", "admin", team="acme", project="mobile", environment="staging")
        pia = manager.account("pia")
        fields = team_fields(pia.id, "viewer", frozenset({P.SELF, P.APPS_READ}), world.store)
        mobile = world.targets["mobile"]
        staging = world.targets["staging"]

        in_production = permissions_at(
            {**fields, "scope": "admin"}, mobile.team_id, mobile.project_id, mobile.environment_id
        )
        in_staging = permissions_at(
            {**fields, "scope": "admin"},
            staging.team_id,
            staging.project_id,
            staging.environment_id,
        )
        elsewhere = permissions_at({**fields, "scope": "admin"}, mobile.team_id, 9999, 9999)

        assert P.APPS_MANAGE not in in_production
        assert P.APPS_READ in in_production
        assert P.APPS_MANAGE in in_staging
        assert P.APPS_MANAGE not in elsewhere

    def test_a_role_the_server_forgot_holds_nothing(self) -> None:
        grants = resolve_grants({}, [("project", 7, "deleted-role")], {})

        assert grants.project[7] == frozenset()

    def test_a_scope_that_is_not_the_servers_never_carries_what_reaches_the_machine(
        self,
    ) -> None:
        custom = {"sneaky": frozenset({P.ROOT_EQUIVALENT, P.SERVER_MANAGE, P.APPS_READ})}

        grants = resolve_grants({}, [("project", 1, "sneaky"), ("instance", 0, "sneaky")], custom)

        assert grants.project[1] == frozenset({P.APPS_READ})
        assert P.ROOT_EQUIVALENT in grants.instance


class TestTheClause:
    def test_a_project_given_alone_is_a_row_filter_of_its_own(self) -> None:
        clause, params = visible_clause(Scope(project_roles={4: "viewer"}), "a.project_id")

        assert clause == "a.project_id IN (?)"
        assert params == [4]

    def test_an_environment_given_alone_filters_on_the_environment_column(self) -> None:
        clause, params = visible_clause(Scope(environment_roles={9: "admin"}), "project_id")

        assert clause == "environment_id IN (?)"
        assert params == [9]

    def test_the_three_ways_to_be_seen_are_alternatives(self) -> None:
        scope = Scope(
            team_roles={1: "viewer"}, project_roles={2: "viewer"}, environment_roles={3: "viewer"}
        )

        clause, params = visible_clause(scope, "s.project_id")

        assert clause.startswith("(") and clause.count(" OR ") == 2
        assert "s.environment_id IN (?)" in clause
        assert params == [1, 2, 3]

    def test_a_row_is_seen_through_the_place_it_was_given(self) -> None:
        scope = Scope(project_roles={2: "viewer"}, environment_roles={3: "viewer"})

        assert scope.sees_placement(1, 2, 8)
        assert scope.sees_placement(1, 5, 3)
        assert not scope.sees_placement(1, 5, 8)
        assert not scope.sees_placement(1, None, None)

    def test_an_environment_does_not_make_the_rest_of_its_project_visible(self) -> None:
        scope = Scope(environment_roles={3: "viewer"}, parent_projects=frozenset({5}))

        assert scope.sees_project(5, 1)
        assert not scope.sees_placement(1, 5, 8)
        assert not scope.sees_team(1)


class TestWhatAProjectGrantShows:
    def test_a_project_shows_only_its_own_applications(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")

        seen = apps_seen(world, "pia")

        # Her own team, globex, is hers whole; of acme she sees the mobile project, and not the
        # default project's shop.
        assert MOBILE_APP in seen and STAGING_APP in seen
        assert ACME_SHOP not in seen

    def test_the_rest_of_the_team_reads_as_absent(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")
        headers = headers_of(world, "pia")

        shop = world.client.get(f"/api/apps/{ACME_SHOP}", headers=headers)
        mobile = world.client.get(f"/api/apps/{MOBILE_APP}", headers=headers)
        missing = world.client.get("/api/apps/no-such.example", headers=headers)

        assert shop.status_code == 404
        assert shop.json()["error"] == missing.json()["error"] == "not_found"
        assert mobile.status_code == 200

    def test_a_project_given_to_a_viewer_does_not_let_it_manage(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")

        assert can_manage(world, "pia", MOBILE_APP) == 403
        assert can_manage(world, "pia", ACME_SHOP) == 404

    def test_admin_of_one_project_and_only_a_reader_of_another(self, world: World) -> None:
        world.teams.create_project("acme", "Web", slug="web")
        web = world.teams.resolve_target(Scope.everything(), team="acme", project="web")
        make_app(world.store, "w.acme.example", web, Path("/tmp"))
        add_person(world, "pia", "viewer", team="globex")
        manager = AccessManager(world.store)
        manager.bind("pia", "admin", team="acme", project="mobile")
        manager.bind("pia", "viewer", team="acme", project="web")

        assert can_manage(world, "pia", MOBILE_APP) == 400  # allowed: it only lacks a destination
        assert can_manage(world, "pia", "w.acme.example") == 403
        assert can_manage(world, "pia", ACME_SHOP) == 404

    def test_an_environment_shows_that_environment_and_nothing_else_of_the_project(
        self, world: World
    ) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind(
            "pia", "operator", team="acme", project="mobile", environment="staging"
        )

        seen = apps_seen(world, "pia")

        assert STAGING_APP in seen
        assert MOBILE_APP not in seen and ACME_SHOP not in seen
        assert (
            world.client.get(
                f"/api/apps/{MOBILE_APP}", headers=headers_of(world, "pia")
            ).status_code
            == 404
        )

    def test_a_lower_role_on_the_environment_does_not_lower_the_projects(
        self, world: World
    ) -> None:
        add_person(world, "pia", "viewer", team="globex")
        manager = AccessManager(world.store)
        manager.bind("pia", "admin", team="acme", project="mobile")
        manager.bind("pia", "viewer", team="acme", project="mobile", environment="staging")

        assert can_manage(world, "pia", STAGING_APP) == 400

    def test_the_sites_databases_and_history_follow_the_application(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")
        headers = headers_of(world, "pia")

        sites = {s["name"] for s in world.client.get("/api/sites", headers=headers).json()["sites"]}
        jobs = world.client.get("/api/jobs", headers=headers).json()
        names = {j["metadata"].get("domain") for j in jobs["jobs"]}

        assert MOBILE_APP in sites and ACME_SHOP not in sites
        assert "m.acme.example" in names and ACME_SHOP not in names

    def test_what_a_project_holds_is_listed_in_projects_and_not_the_rest_of_the_team(
        self, world: World
    ) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")

        projects = world.client.get("/api/projects", headers=headers_of(world, "pia")).json()[
            "projects"
        ]

        assert {(p["team"], p["slug"]) for p in projects} == {
            ("globex", "default"),
            ("acme", "mobile"),
        }

    def test_an_environment_shows_its_project_as_a_parent_and_only_that_environment(
        self, world: World
    ) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind(
            "pia", "viewer", team="acme", project="mobile", environment="staging"
        )

        projects = world.client.get("/api/projects", headers=headers_of(world, "pia")).json()[
            "projects"
        ]
        environments = world.client.get(
            "/api/environments", headers=headers_of(world, "pia")
        ).json()["environments"]

        assert ("acme", "mobile") in {(p["team"], p["slug"]) for p in projects}
        assert {(e["project"], e["slug"]) for e in environments if e["team"] == "acme"} == {
            ("mobile", "staging")
        }

    def test_a_project_grant_does_not_open_the_team_itself(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "admin", team="acme", project="mobile")
        headers = headers_of(world, "pia")

        team = world.client.get("/api/teams/acme", headers=headers)
        members = world.client.get("/api/teams/acme/members", headers=headers)
        listed = world.client.get("/api/teams", headers=headers).json()["teams"]

        assert team.status_code == 404
        assert members.status_code == 404
        assert {t["slug"] for t in listed} == {"globex"}

    def test_a_new_application_goes_where_the_role_reaches(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "admin", team="acme", project="mobile")

        target = world.teams.resolve_target(
            scope_of(_payload_of(world, "pia")), team="acme", project="mobile"
        )
        assert target.project_id == world.targets["mobile"].project_id
        with pytest.raises(TeamNotFoundError):
            world.teams.resolve_target(
                scope_of(_payload_of(world, "pia")), team="acme", project="default"
            )

    def test_taking_the_role_away_takes_the_view_with_it(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        manager = AccessManager(world.store)
        binding = manager.bind("pia", "viewer", team="acme", project="mobile")
        assert MOBILE_APP in apps_seen(world, "pia")

        manager.unbind(binding.id)

        assert MOBILE_APP not in apps_seen(world, "pia")


def _payload_of(world: World, username: str) -> dict[str, Any]:
    """
    Args:
        world: The server.
        username: One of its people.

    Returns:
        The payload their credential authenticates, as the console builds it.
    """
    from noust.web.server import get_token_manager

    account = get_token_manager().accounts.find(username)
    assert account is not None
    return {
        "account_id": account.id,
        **team_fields(
            account.id,
            account.role,
            frozenset({P.SELF, P.APPS_READ}),
            world.store,
        ),
    }


class TestCustomRolesOverHttp:
    def test_a_custom_role_gives_exactly_what_it_lists(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        manager = AccessManager(world.store)
        manager.create_role("tester", [P.APPS_READ, P.APPS_MANAGE])
        manager.bind("pia", "tester", team="acme", project="mobile")

        assert can_manage(world, "pia", MOBILE_APP) == 400
        assert can_manage(world, "pia", ACME_SHOP) == 404

    def test_a_custom_role_on_a_team_makes_the_account_one_of_its_people(
        self, world: World
    ) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        manager.create_role("ci", [P.APPS_READ, P.APPS_DEPLOY])
        manager.bind("pia", "ci", team="acme")

        assert ACME_SHOP in apps_seen(world, "pia")
        assert can_manage(world, "pia", ACME_SHOP) == 403

    def test_changing_the_role_changes_the_next_request(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        manager = AccessManager(world.store)
        manager.create_role("tester", [P.APPS_READ])
        manager.bind("pia", "tester", team="acme", project="mobile")
        assert can_manage(world, "pia", MOBILE_APP) == 403

        manager.update_role("tester", permissions=[P.APPS_READ, P.APPS_MANAGE])

        assert can_manage(world, "pia", MOBILE_APP) == 400

    def test_a_role_that_holds_the_machine_is_inert_on_a_project(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        manager = AccessManager(world.store)
        manager.create_role("rooty", [P.APPS_READ, P.ROOT_EQUIVALENT])
        manager.bind("pia", "rooty", team="acme", project="mobile")

        response = world.client.get(
            f"/api/apps/{MOBILE_APP}/cron", headers=headers_of(world, "pia")
        )

        assert response.status_code in (403, 404)
        fields = team_fields(manager.account("pia").id, "viewer", frozenset({P.SELF}), world.store)
        assert P.ROOT_EQUIVALENT not in fields["permissions"]


class TestTheAccessApi:
    def test_the_catalogue_lists_the_roles_and_what_a_scope_can_hold(self, world: World) -> None:
        AccessManager(world.store).create_role("ci", [P.APPS_READ, P.APPS_DEPLOY])

        body = world.client.get("/api/access/roles", headers=world.as_("alice")).json()

        names = {r["name"]: r for r in body["roles"]}
        assert {"viewer", "operator", "admin", "owner", "ci"} <= set(names)
        assert names["owner"]["built_in"] and names["owner"]["scopes"] == ["team"]
        assert names["ci"]["built_in"] is False
        assert P.ROOT_EQUIVALENT not in body["narrow_permissions"]
        assert body["permissions"][P.APPS_DEPLOY]

    def test_a_security_officer_defines_a_role_in_sudo_mode(self, world: World) -> None:
        response = world.client.post(
            "/api/access/roles",
            json={"name": "ci", "permissions": ["apps.read", "apps.deploy"], "label": "CI"},
            headers=world.as_("sec"),
        )

        assert response.status_code == 201, response.text
        assert response.json()["permissions"] == ["apps.deploy", "apps.read", "self"]

    def test_a_team_owner_does_not_define_roles(self, world: World) -> None:
        response = world.client.post(
            "/api/access/roles",
            json={"name": "ci", "permissions": ["apps.read"]},
            headers=world.as_("alice"),
        )

        assert response.status_code == 403

    def test_a_conflicting_role_is_a_validation_error(self, world: World) -> None:
        response = world.client.post(
            "/api/access/roles",
            json={"name": "god", "permissions": ["security.manage", "apps.deploy"]},
            headers=world.as_("sec"),
        )

        assert response.status_code == 400
        assert "govern access and run" in response.json()["detail"]

    def test_an_owner_gives_a_role_on_a_project_of_its_team(self, world: World) -> None:
        add_person(world, "carla", "viewer", team="acme", team_role="owner")
        add_person(world, "pia", "viewer", team="globex")

        response = world.client.post(
            "/api/teams/acme/access",
            json={"username": "pia", "role": "operator", "project": "mobile"},
            headers=headers_of(world, "carla"),
        )

        assert response.status_code == 201, response.text
        assert response.json()["scope"] == "acme/mobile"
        assert MOBILE_APP in apps_seen(world, "pia")

    def test_the_owner_of_a_team_does_not_give_roles_in_another(self, world: World) -> None:
        add_person(world, "carla", "viewer", team="acme", team_role="owner")
        add_person(world, "pia", "viewer")

        response = world.client.post(
            "/api/teams/globex/access",
            json={"username": "pia", "role": "viewer", "project": "default"},
            headers=headers_of(world, "carla"),
        )

        assert response.status_code == 404

    def test_a_team_admin_who_is_not_its_owner_cannot_give_roles(self, world: World) -> None:
        add_person(world, "pia", "viewer")

        response = world.client.post(
            "/api/teams/acme/access",
            json={"username": "pia", "role": "viewer", "project": "mobile"},
            headers=world.as_("alice"),
        )

        assert response.status_code == 403

    def test_the_bindings_of_a_team_are_listed_to_its_people_only(self, world: World) -> None:
        add_person(world, "pia", "viewer", team="globex")
        AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")

        mine = world.client.get("/api/teams/acme/access", headers=world.as_("alice"))
        theirs = world.client.get("/api/teams/acme/access", headers=world.as_("bob"))

        assert [b["username"] for b in mine.json()["bindings"]] == ["pia"]
        assert theirs.status_code == 404

    def test_a_binding_is_removed_only_through_its_own_team(self, world: World) -> None:
        add_person(world, "carla", "viewer", team="globex", team_role="owner")
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        binding = manager.bind("pia", "viewer", team="acme", project="mobile")

        wrong = world.client.delete(
            f"/api/teams/globex/access/{binding.id}", headers=headers_of(world, "carla")
        )

        assert wrong.status_code == 404
        assert manager.data.get_binding(binding.id) is not None

    def test_the_server_lists_and_removes_every_binding(self, world: World) -> None:
        add_person(world, "pia", "viewer")
        manager = AccessManager(world.store)
        binding = manager.bind("pia", "viewer", team="acme", project="mobile")

        listed = world.client.get("/api/access/bindings", headers=world.as_("sec")).json()
        removed = world.client.delete(
            f"/api/access/bindings/{binding.id}", headers=world.as_("sec")
        )

        assert [b["scope"] for b in listed["bindings"]] == ["acme/mobile"]
        assert removed.status_code == 200
        assert manager.list_bindings("pia") == []

    def test_every_change_is_an_event_the_audit_catalog_declares(self) -> None:
        from noust.core.audit.catalog import EVENTS

        for event in ("access.role.create", "access.role.update", "access.role.delete"):
            assert event in EVENTS
        assert {"access.bind", "access.unbind"} <= set(EVENTS)


def test_the_account_a_scope_belongs_to_is_in_its_scope(world: World) -> None:
    add_person(world, "pia", "viewer", team="globex")
    AccessStore(world.store)
    AccessManager(world.store).bind("pia", "viewer", team="acme", project="mobile")
    payload = _payload_of(world, "pia")

    scope = scope_of(payload)

    assert world.targets["mobile"].project_id in scope.project_roles
    assert not scope.sees_all
