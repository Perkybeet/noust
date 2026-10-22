# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Teams, members, projects and environments over HTTP (I-01, I-02, I-10).

The rules are the team store's and are tested there; what is pinned here is
what the API adds: who may call each route (an administrator governs every
team, a team's owner governs that team, nobody else), what a caller sees of
teams it does not belong to (nothing), choosing the team one works in, moving an
application, and bringing a new person into a team.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.audit import record  # noqa: F401 - the catalog must know the events used here
from noust.core.audit.catalog import EVENTS
from noust.core.store import NoustStore
from tests.teams_support import World, build_world, make_person

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    """Two teams, their resources and their people, served by the console."""
    built = build_world(tmp_path, monkeypatch)
    # alice administers acme's applications; carla owns the team itself.
    built.people["carla"] = make_person(
        built.teams, "carla", "viewer", team="acme", team_role="owner"
    )
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def slugs(body: dict[str, Any], key: str = "teams") -> set[str]:
    """
    Args:
        body: A list response.
        key: The list's key.

    Returns:
        The slugs it holds.
    """
    return {item["slug"] for item in body[key]}


class TestSeeingTeams:
    def test_an_administrator_sees_every_team(self, world: World) -> None:
        body = world.client.get("/api/teams", headers=world.as_("admin")).json()

        assert slugs(body) == {"default", "acme", "globex"}
        assert body["total"] == 3

    def test_a_member_sees_its_own_teams_with_its_role(self, world: World) -> None:
        body = world.client.get("/api/teams", headers=world.as_("alice")).json()

        assert slugs(body) == {"acme"}
        assert body["teams"][0]["role"] == "admin"

    def test_a_team_one_does_not_belong_to_is_not_found(self, world: World) -> None:
        mine = world.client.get("/api/teams/acme", headers=world.as_("alice"))
        theirs = world.client.get("/api/teams/globex", headers=world.as_("alice"))
        missing = world.client.get("/api/teams/nowhere", headers=world.as_("alice"))

        assert mine.status_code == 200
        assert theirs.status_code == missing.status_code == 404
        assert theirs.json()["error"] == missing.json()["error"] == "not_found"

    def test_a_team_says_what_it_holds(self, world: World) -> None:
        body = world.client.get("/api/teams/acme", headers=world.as_("alice")).json()

        assert body["resources"] == {"apps": 1, "sites": 1, "databases": 1, "backup_schedules": 1}
        assert body["members"] == 3 + 1  # alice, dana, omar and carla
        assert body["projects"] == 1

    def test_the_projects_and_environments_are_seen_by_team_too(self, world: World) -> None:
        projects = world.client.get("/api/projects", headers=world.as_("alice")).json()
        environments = world.client.get("/api/environments", headers=world.as_("alice")).json()

        assert {(p["team"], p["slug"]) for p in projects["projects"]} == {("acme", "default")}
        assert projects["projects"][0]["environments"] == ["production"]
        assert {(e["team"], e["project"], e["slug"]) for e in environments["environments"]} == {
            ("acme", "default", "production")
        }
        everything = world.client.get("/api/projects", headers=world.as_("admin")).json()
        assert {p["team"] for p in everything["projects"]} == {"default", "acme", "globex"}
        narrowed = world.client.get("/api/projects?team=globex", headers=world.as_("admin")).json()
        assert {p["team"] for p in narrowed["projects"]} == {"globex"}

    def test_the_teams_of_another_one_are_not_listed_among_its_members_either(
        self, world: World
    ) -> None:
        assert (
            world.client.get("/api/teams/globex/members", headers=world.as_("alice")).status_code
            == 404
        )
        assert (
            world.client.get("/api/teams/globex/projects", headers=world.as_("alice")).status_code
            == 404
        )


class TestWhatTheRolesOfATeamHold:
    def test_they_are_listed_weakest_first_with_what_they_mean(self, world: World) -> None:
        body = world.client.get("/api/teams/roles", headers=world.as_("alice")).json()

        assert [r["role"] for r in body["roles"]] == ["viewer", "operator", "admin", "owner"]
        by_role = {r["role"]: set(r["permissions"]) for r in body["roles"]}
        assert by_role["viewer"] < by_role["operator"] < by_role["admin"] < by_role["owner"]
        assert "teams.manage" in by_role["owner"] and "teams.manage" not in by_role["admin"]
        assert not any(
            "root_equivalent" in held or "server.manage" in held for held in by_role.values()
        )
        assert set(body["descriptions"]) == set().union(*by_role.values())


class TestCreatingAndDeletingTeams:
    def test_an_administrator_creates_a_team_with_its_default_project(self, world: World) -> None:
        response = world.client.post(
            "/api/teams", json={"name": "Initech"}, headers=world.as_("admin")
        )

        assert response.status_code == 201, response.text
        assert response.json()["slug"] == "initech"
        projects = world.client.get(
            "/api/teams/initech/projects", headers=world.as_("admin")
        ).json()
        assert [p["slug"] for p in projects["projects"]] == ["default"]
        assert projects["projects"][0]["environments"] == ["production"]

    def test_a_team_member_does_not_create_teams(self, world: World) -> None:
        for who in ("alice", "carla", "dana"):
            response = world.client.post(
                "/api/teams", json={"name": "Mine"}, headers=world.as_(who)
            )
            assert response.status_code == 403, (who, response.text)
            assert response.json()["error"] in ("instance_scope_required", "permission_denied")

    def test_a_slug_taken_is_a_conflict(self, world: World) -> None:
        response = world.client.post(
            "/api/teams", json={"name": "Acme again", "slug": "acme"}, headers=world.as_("admin")
        )

        assert response.status_code == 409

    def test_an_empty_team_is_deleted_and_one_that_holds_something_is_not(
        self, world: World
    ) -> None:
        world.client.post("/api/teams", json={"name": "Empty"}, headers=world.as_("admin"))

        gone = world.client.delete("/api/teams/empty", headers=world.as_("admin"))
        refused = world.client.delete("/api/teams/acme", headers=world.as_("admin"))
        default = world.client.delete("/api/teams/default", headers=world.as_("admin"))

        assert gone.status_code == 200
        assert refused.status_code == default.status_code == 409
        assert "still holds" in refused.json()["detail"]

    def test_an_owner_renames_its_team_and_a_plain_admin_does_not(self, world: World) -> None:
        refused = world.client.patch(
            "/api/teams/acme", json={"name": "Acme Inc"}, headers=world.as_("alice")
        )
        renamed = world.client.patch(
            "/api/teams/acme", json={"name": "Acme Inc"}, headers=world.as_("carla")
        )

        assert refused.status_code == 403
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Acme Inc"

    def test_an_owner_cannot_touch_another_team(self, world: World) -> None:
        response = world.client.patch(
            "/api/teams/globex", json={"name": "Mine now"}, headers=world.as_("carla")
        )

        assert response.status_code == 404


class TestMembers:
    def test_an_owner_adds_an_existing_account_and_changes_its_role(self, world: World) -> None:
        make_person(world.teams, "erin", "viewer")
        added = world.client.post(
            "/api/teams/acme/members",
            json={"username": "erin", "role": "viewer"},
            headers=world.as_("carla"),
        )
        promoted = world.client.patch(
            "/api/teams/acme/members/erin", json={"role": "operator"}, headers=world.as_("carla")
        )

        assert added.status_code == 201, added.text
        assert promoted.status_code == 200
        members = world.client.get("/api/teams/acme/members", headers=world.as_("alice")).json()
        assert {m["username"]: m["role"] for m in members["members"]}["erin"] == "operator"

    def test_a_team_admin_who_is_not_its_owner_does_not_manage_its_members(
        self, world: World
    ) -> None:
        make_person(world.teams, "erin", "viewer")

        response = world.client.post(
            "/api/teams/acme/members",
            json={"username": "erin", "role": "admin"},
            headers=world.as_("alice"),
        )

        assert response.status_code == 403
        assert response.json()["error"] == "permission_denied"

    def test_nobody_gives_itself_a_place_in_another_team(self, world: World) -> None:
        response = world.client.post(
            "/api/teams/globex/members",
            json={"username": "carla", "role": "owner"},
            headers=world.as_("carla"),
        )

        assert response.status_code == 404
        assert "carla" not in {m.username for m in world.teams.list_members("globex")}

    def test_an_owner_cannot_make_an_auditor_a_member(self, world: World) -> None:
        response = world.client.post(
            "/api/teams/acme/members",
            json={"username": "aurora", "role": "viewer"},
            headers=world.as_("carla"),
        )

        assert response.status_code == 400
        assert "auditor" in response.json()["detail"]

    def test_an_unknown_account_is_not_found(self, world: World) -> None:
        response = world.client.post(
            "/api/teams/acme/members",
            json={"username": "nobody", "role": "viewer"},
            headers=world.as_("carla"),
        )

        assert response.status_code == 404

    def test_the_last_owner_stays(self, world: World) -> None:
        removal = world.client.delete("/api/teams/acme/members/carla", headers=world.as_("carla"))
        demotion = world.client.patch(
            "/api/teams/acme/members/carla", json={"role": "viewer"}, headers=world.as_("carla")
        )

        assert removal.status_code == demotion.status_code == 409

    def test_a_removed_member_stops_seeing_the_team(self, world: World) -> None:
        assert world.client.get("/api/teams/acme", headers=world.as_("dana")).status_code == 200

        world.client.delete("/api/teams/acme/members/dana", headers=world.as_("carla"))

        assert world.client.get("/api/teams/acme", headers=world.as_("dana")).status_code == 404

    def test_a_person_invited_for_a_team_belongs_to_it_only(self, world: World) -> None:
        response = world.client.post(
            "/api/auth/invitations",
            json={"username": "frank", "role": "viewer", "team": "acme", "team_role": "operator"},
            headers=world.as_("sec"),
        )

        assert response.status_code == 201, response.text
        assert response.json()["code"]
        frank = (
            world.store._get_connection()
            .execute("SELECT id FROM accounts WHERE username = 'frank'")
            .fetchone()[0]
        )
        assert list(world.teams.memberships_of(frank).values()) == ["operator"]
        assert world.teams.require_team("acme").id in world.teams.memberships_of(frank)

    def test_an_invitation_to_a_team_that_is_not_there_creates_nothing(self, world: World) -> None:
        response = world.client.post(
            "/api/auth/invitations",
            json={"username": "frank", "role": "viewer", "team": "nowhere"},
            headers=world.as_("sec"),
        )

        assert response.status_code == 404
        count = (
            world.store._get_connection()
            .execute("SELECT COUNT(*) FROM accounts WHERE username = 'frank'")
            .fetchone()[0]
        )
        assert count == 0

    def test_an_invitation_without_a_team_joins_the_default_team(self, world: World) -> None:
        response = world.client.post(
            "/api/auth/invitations",
            json={"username": "gina", "role": "operator"},
            headers=world.as_("sec"),
        )

        assert response.status_code == 201, response.text
        gina = (
            world.store._get_connection()
            .execute("SELECT id FROM accounts WHERE username = 'gina'")
            .fetchone()[0]
        )
        assert world.teams.memberships_of(gina) == {
            world.teams.require_team("default").id: "operator"
        }


class TestProjectsAndEnvironments:
    def test_an_owner_makes_and_removes_a_project_and_its_environments(self, world: World) -> None:
        made = world.client.post(
            "/api/teams/acme/projects", json={"name": "Mobile"}, headers=world.as_("carla")
        )
        staged = world.client.post(
            "/api/teams/acme/projects/mobile/environments",
            json={"name": "Staging"},
            headers=world.as_("carla"),
        )
        listed = world.client.get(
            "/api/teams/acme/projects/mobile/environments", headers=world.as_("alice")
        ).json()
        removed_env = world.client.delete(
            "/api/teams/acme/projects/mobile/environments/staging", headers=world.as_("carla")
        )
        removed = world.client.delete("/api/teams/acme/projects/mobile", headers=world.as_("carla"))

        assert made.status_code == 201 and staged.status_code == 201
        assert {e["slug"] for e in listed["environments"]} == {"production", "staging"}
        assert removed_env.status_code == removed.status_code == 200

    def test_a_project_with_an_application_in_it_is_not_deleted(self, world: World) -> None:
        response = world.client.delete(
            "/api/teams/acme/projects/default", headers=world.as_("carla")
        )

        assert response.status_code == 409
        assert "still holds 1 application" in response.json()["detail"]

    def test_an_owner_renames_a_project_and_an_environment(self, world: World) -> None:
        world.client.post(
            "/api/teams/acme/projects", json={"name": "Mobile"}, headers=world.as_("carla")
        )
        world.client.post(
            "/api/teams/acme/projects/mobile/environments",
            json={"name": "Staging"},
            headers=world.as_("carla"),
        )

        project = world.client.patch(
            "/api/teams/acme/projects/mobile",
            json={"name": "Mobile app"},
            headers=world.as_("carla"),
        )
        environment = world.client.patch(
            "/api/teams/acme/projects/mobile/environments/staging",
            json={"name": "Pre-production"},
            headers=world.as_("carla"),
        )

        assert project.status_code == environment.status_code == 200
        assert (project.json()["slug"], project.json()["name"]) == ("mobile", "Mobile app")
        assert (environment.json()["slug"], environment.json()["name"]) == (
            "staging",
            "Pre-production",
        )
        listed = world.client.get("/api/teams/acme/projects", headers=world.as_("alice")).json()[
            "projects"
        ]
        assert {p["slug"]: p["name"] for p in listed}["mobile"] == "Mobile app"

    def test_renaming_is_the_owners_and_not_a_plain_admins_or_another_teams(
        self, world: World
    ) -> None:
        plain = world.client.patch(
            "/api/teams/acme/projects/default", json={"name": "Mine"}, headers=world.as_("alice")
        )
        other = world.client.patch(
            "/api/teams/globex/projects/default",
            json={"name": "Mine"},
            headers=world.as_("carla"),
        )
        missing = world.client.patch(
            "/api/teams/nowhere/projects/default",
            json={"name": "Mine"},
            headers=world.as_("carla"),
        )

        assert plain.status_code == 403
        assert other.status_code == missing.status_code
        assert other.json()["error"] == missing.json()["error"]
        assert world.teams.require_project("globex", "default").name != "Mine"

    def test_renaming_something_that_is_not_there_is_not_found(self, world: World) -> None:
        project = world.client.patch(
            "/api/teams/acme/projects/nope", json={"name": "Mine"}, headers=world.as_("carla")
        )
        environment = world.client.patch(
            "/api/teams/acme/projects/default/environments/nope",
            json={"name": "Mine"},
            headers=world.as_("carla"),
        )

        assert project.status_code == environment.status_code == 404

    def test_nobody_creates_in_the_projects_of_a_team_it_is_not_in(self, world: World) -> None:
        # Whoever is asked, a team of someone else answers as a team that is not there.
        for who in ("alice", "carla"):
            hidden = world.client.post(
                "/api/teams/globex/projects", json={"name": "Mine"}, headers=world.as_(who)
            )
            missing = world.client.post(
                "/api/teams/nowhere/projects", json={"name": "Mine"}, headers=world.as_(who)
            )

            assert hidden.status_code == missing.status_code, who
            assert hidden.status_code in (403, 404)
            assert hidden.json()["error"] == missing.json()["error"], who
        assert world.teams.list_projects(team="globex")[0].slug == "default"
        assert len(world.teams.list_projects(team="globex")) == 1

    def test_a_plain_admin_of_the_team_does_not_make_projects(self, world: World) -> None:
        response = world.client.post(
            "/api/teams/acme/projects", json={"name": "Mine"}, headers=world.as_("alice")
        )

        assert response.status_code == 403


class TestChoosingTheTeamOneWorksIn:
    def test_mine_says_what_one_belongs_to(self, world: World) -> None:
        body = world.client.get("/api/teams/mine", headers=world.as_("alice")).json()

        assert body["sees_all"] is False
        assert [t["slug"] for t in body["teams"]] == ["acme"]
        assert body["active"] is None

    def test_a_member_of_two_teams_chooses_where_new_things_go(self, world: World) -> None:
        world.teams.add_member("globex", "alice", "admin")
        body = {"domain": "new.example.com", "source": "https://x.test/r.git"}

        unsaid = world.client.post("/api/apps", json=body, headers=world.as_("alice"))
        chose = world.client.put(
            "/api/teams/active", json={"team": "globex"}, headers=world.as_("alice")
        )
        again = world.client.post("/api/apps", json=body, headers=world.as_("alice"))

        assert unsaid.status_code == 400
        assert "several teams" in unsaid.json()["detail"]
        assert chose.status_code == 200 and chose.json()["active"] == "globex"
        assert again.status_code == 202, again.text
        from noust.web.jobs import get_job_manager

        job = get_job_manager().get_job(again.json()["job_id"])
        assert job is not None and job.ownership[0] == world.targets["globex"].project_id  # type: ignore[index]

    def test_the_choice_is_forgotten_with_null(self, world: World) -> None:
        world.client.put("/api/teams/active", json={"team": "acme"}, headers=world.as_("alice"))

        cleared = world.client.put(
            "/api/teams/active", json={"team": None}, headers=world.as_("alice")
        )

        assert cleared.json()["active"] is None

    def test_a_team_one_does_not_belong_to_cannot_be_chosen(self, world: World) -> None:
        response = world.client.put(
            "/api/teams/active", json={"team": "globex"}, headers=world.as_("alice")
        )

        assert response.status_code == 404
        assert (
            world.client.get("/api/teams/mine", headers=world.as_("alice")).json()["active"] is None
        )

    def test_the_master_token_has_no_team_to_work_in(self, world: World) -> None:
        response = world.client.put(
            "/api/teams/active", json={"team": "acme"}, headers=world.master
        )

        assert response.status_code == 400

    def test_an_administrator_works_in_a_team_it_chose(self, world: World) -> None:
        world.client.put("/api/teams/active", json={"team": "globex"}, headers=world.as_("admin"))
        body = {"domain": "new.example.com", "source": "https://x.test/r.git"}

        response = world.client.post("/api/apps", json=body, headers=world.as_("admin"))

        assert response.status_code == 202, response.text
        from noust.web.jobs import get_job_manager

        job = get_job_manager().get_job(response.json()["job_id"])
        assert job is not None and job.ownership[0] == world.targets["globex"].project_id  # type: ignore[index]


class TestMovingAnApplication:
    def test_an_administrator_moves_an_application_to_another_team(self, world: World) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/move", json={"team": "globex"}, headers=world.as_("admin")
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "domain": ACME,
            "team": "globex",
            "project": "default",
            "environment": "production",
            "moved": {"apps": 1, "sites": 1, "databases": 1, "backup_schedules": 1},
        }
        # alice no longer sees it; bob now does, with its site and database.
        assert world.client.get(f"/api/apps/{ACME}", headers=world.as_("alice")).status_code == 404
        assert world.client.get(f"/api/apps/{ACME}", headers=world.as_("bob")).status_code == 200
        assert world.client.get(f"/api/sites/{ACME}", headers=world.as_("bob")).status_code != 404
        assert (
            world.client.get(
                "/api/databases/databases/postgresql/shop_db", headers=world.as_("bob")
            ).status_code
            != 404
        )

    def test_a_team_admin_moves_within_its_team(self, world: World) -> None:
        world.teams.create_environment("acme", "default", "Staging")

        response = world.client.post(
            f"/api/apps/{ACME}/move", json={"environment": "staging"}, headers=world.as_("alice")
        )

        assert response.status_code == 200, response.text
        app = world.client.get(f"/api/apps/{ACME}", headers=world.as_("alice")).json()
        assert (app["team"], app["project"], app["environment"]) == ("acme", "default", "staging")

    def test_nobody_hands_an_application_to_a_team_it_is_not_in(self, world: World) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/move", json={"team": "globex"}, headers=world.as_("alice")
        )

        assert response.status_code == 404
        app = world.store.get_app(ACME)
        assert app is not None and app.project_id == world.targets["acme"].project_id

    def test_nobody_moves_an_application_to_a_team_it_may_only_read(self, world: World) -> None:
        world.teams.add_member("globex", "alice", "viewer")

        response = world.client.post(
            f"/api/apps/{ACME}/move", json={"team": "globex"}, headers=world.as_("alice")
        )

        assert response.status_code == 403
        assert response.json()["error"] == "permission_denied"
        app = world.store.get_app(ACME)
        assert app is not None and app.project_id == world.targets["acme"].project_id

    def test_a_viewer_does_not_move_anything(self, world: World) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/move", json={"environment": "production"}, headers=world.as_("dana")
        )

        assert response.status_code == 403

    def test_it_says_where_or_it_is_refused(self, world: World) -> None:
        response = world.client.post(f"/api/apps/{ACME}/move", json={}, headers=world.as_("admin"))

        assert response.status_code == 400

    def test_the_move_is_on_the_audit_trail(self, world: World) -> None:
        assert "apps.move" in EVENTS
        for event in (
            "team.create",
            "team.update",
            "team.delete",
            "team.member.add",
            "team.member.remove",
            "project.create",
            "project.update",
            "project.delete",
            "environment.create",
            "environment.update",
            "environment.delete",
        ):
            assert event in EVENTS, event


class TestTheListsSayWhereEachRowLives:
    def test_a_site_says_its_team_project_and_environment(self, world: World) -> None:
        sites = world.client.get("/api/sites", headers=world.as_("admin")).json()["sites"]

        by_name = {s["name"]: s for s in sites}
        assert (
            by_name[ACME]["team"],
            by_name[ACME]["project"],
            by_name[ACME]["environment"],
        ) == ("acme", "default", "production")
        assert by_name[GLOBEX]["team"] == "globex"

    def test_a_database_says_where_it_lives(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.database.service import DatabaseListing, DatabaseService, DatabaseView

        monkeypatch.setattr(
            DatabaseService,
            "listing",
            lambda self, engine=None: DatabaseListing(
                [
                    DatabaseView(name="shop_db", engine="postgresql", tracked=True),
                    DatabaseView(name="orphan", engine="postgresql"),
                ],
                [],
            ),
        )

        rows = world.client.get("/api/databases/databases", headers=world.as_("admin")).json()[
            "databases"
        ]

        by_name = {d["name"]: d for d in rows}
        assert (by_name["shop_db"]["team"], by_name["shop_db"]["project"]) == ("acme", "default")
        assert by_name["shop_db"]["environment"] == "production"
        # What an engine holds and the store never recorded belongs to nobody.
        assert by_name["orphan"]["team"] is None


class TestTheListFiltersOfTheApi:
    def test_the_applications_name_where_they_are(self, world: World) -> None:
        body = world.client.get("/api/apps?team=globex", headers=world.as_("admin")).json()

        assert [(a["domain"], a["team"], a["project"], a["environment"]) for a in body["apps"]] == [
            (GLOBEX, "globex", "default", "production")
        ]

    def test_the_one_application_names_where_it_is(self, world: World) -> None:
        app = world.client.get(f"/api/apps/{ACME}", headers=world.as_("alice")).json()

        assert (app["team"], app["project"], app["environment"]) == (
            "acme",
            "default",
            "production",
        )
