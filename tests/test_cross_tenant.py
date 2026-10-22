# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A member of one team cannot list, read, change or delete another team's resources.

Two teams, ``acme`` and ``globex``, share a server (I-01, I-04). What these
tests pin is the promise of the 4.0 design: whatever a route is asked about,
what is outside the asker's teams reads as not there - the same 404 as a
resource that does not exist, never a 403 that would say it does - for
applications, their sites, databases and backup schedules, their history, and
the streams that carry it. They go through the console as it is served, with
the credentials people hold, so a route that forgot to ask whose a resource is
fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.store import App, NoustStore
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.service import DatabaseListing, DatabaseService, DatabaseView
from noust.web.auth import FLEET_ACTOR_HEADER, FLEET_ACTOR_SCOPE_HEADER
from noust.web.server import get_token_manager
from tests.teams_support import World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"
ACME_DB = ("postgresql", "shop_db")
GLOBEX_DB = ("postgresql", "blog_db")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    """Two teams, their resources and their people, served by the console."""
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def domains(body: dict[str, Any], key: str = "apps") -> set[str]:
    """
    Args:
        body: A list response.
        key: The list's key.

    Returns:
        The domains it names.
    """
    return {item.get("domain") or item.get("name") for item in body[key]}


class TestTheListsHoldOnlyWhatIsTheirs:
    def test_a_member_lists_its_own_team_apps_and_nobody_elses(self, world: World) -> None:
        mine = world.client.get("/api/apps", headers=world.as_("alice"))
        theirs = world.client.get("/api/apps", headers=world.as_("bob"))

        assert mine.status_code == theirs.status_code == 200
        assert domains(mine.json()) == {ACME}
        assert domains(theirs.json()) == {GLOBEX}

    def test_an_administrator_the_master_token_and_the_auditors_see_every_team(
        self, world: World
    ) -> None:
        for headers in (
            world.as_("admin"),
            world.master,
            world.as_("sec"),
            world.as_("aud"),
        ):
            body = world.client.get("/api/apps", headers=headers).json()
            assert domains(body) == {ACME, GLOBEX}

    def test_a_viewer_and_an_operator_see_the_teams_they_belong_to(self, world: World) -> None:
        assert domains(world.client.get("/api/apps", headers=world.as_("dana")).json()) == {ACME}
        assert domains(world.client.get("/api/apps", headers=world.as_("omar")).json()) == {ACME}

    def test_the_list_says_where_each_application_lives(self, world: World) -> None:
        body = world.client.get("/api/apps", headers=world.as_("alice")).json()

        assert [(a["team"], a["project"], a["environment"]) for a in body["apps"]] == [
            ("acme", "default", "production")
        ]

    def test_the_project_and_environment_filters_narrow_the_list(self, world: World) -> None:
        world.teams.create_project("acme", "Mobile", slug="mobile", created_by="test")
        target = world.teams.resolve_target(
            world.teams.scope_for(1, "admin"), team="acme", project="mobile"
        )
        world.store.create_app(
            App(
                domain="m.acme.example",
                app_path="/srv/m",
                project_id=target.project_id,
                environment_id=target.environment_id,
            )
        )

        every = world.client.get("/api/apps", headers=world.as_("alice")).json()
        mobile = world.client.get("/api/apps?project=mobile", headers=world.as_("alice")).json()
        staging = world.client.get(
            "/api/apps?environment=staging", headers=world.as_("alice")
        ).json()

        assert domains(every) == {ACME, "m.acme.example"}
        assert domains(mobile) == {"m.acme.example"}
        assert staging["apps"] == []
        # Another team's project of the same name is not one the member can name.
        assert (
            world.client.get("/api/apps?team=globex", headers=world.as_("alice")).json()["apps"]
            == []
        )

    def test_sites_databases_and_schedules_are_listed_by_team_too(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            BackupScheduler,
            "list_schedules",
            lambda self: [
                {"domain": ACME, "app_name": "shop-acme-example", "timer": "t1"},
                {"domain": GLOBEX, "app_name": "blog-globex-example", "timer": "t2"},
            ],
        )

        # The engines are not running here: they report these three, one of which
        # (orphan) the store never recorded and so belongs to nobody.
        monkeypatch.setattr(
            DatabaseService,
            "listing",
            lambda self, engine=None: DatabaseListing(
                [
                    DatabaseView(name="shop_db", engine="postgresql", tracked=True),
                    DatabaseView(name="blog_db", engine="postgresql", tracked=True),
                    DatabaseView(name="orphan", engine="postgresql"),
                ],
                [],
            ),
        )

        sites = world.client.get("/api/sites", headers=world.as_("alice")).json()
        databases = world.client.get("/api/databases/databases", headers=world.as_("alice")).json()
        schedules = world.client.get("/api/backup-schedules", headers=world.as_("alice")).json()
        everything = world.client.get("/api/databases/databases", headers=world.as_("admin")).json()

        assert {s["name"] for s in sites["sites"]} == {ACME}
        assert {d["name"] for d in databases["databases"]} == {"shop_db"}
        assert {d["name"] for d in everything["databases"]} == {"shop_db", "blog_db", "orphan"}
        assert {s["domain"] for s in schedules["schedules"]} == {ACME}

    def test_history_is_listed_by_team_too(self, world: World) -> None:
        deployments = world.client.get("/api/deployments", headers=world.as_("alice")).json()
        jobs = world.client.get("/api/jobs", headers=world.as_("alice")).json()

        assert {d["domain"] for d in deployments["items"]} == {ACME}
        assert {j["metadata"].get("domain") for j in jobs["jobs"]} == {ACME}
        assert {
            d["domain"]
            for d in world.client.get("/api/deployments", headers=world.as_("bob")).json()["items"]
        } == {GLOBEX}


class TestAResourceOfAnotherTeamReadsAsAbsent:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", f"/api/apps/{GLOBEX}"),
            ("GET", f"/api/apps/{GLOBEX}/domains"),
            ("GET", f"/api/apps/{GLOBEX}/env"),
            ("GET", f"/api/apps/{GLOBEX}/logs"),
            ("GET", f"/api/apps/{GLOBEX}/releases"),
            ("GET", f"/api/apps/{GLOBEX}/diagnose"),
            ("GET", f"/api/apps/{GLOBEX}/export"),
            ("GET", f"/api/apps/{GLOBEX}/hooks"),
            ("GET", f"/api/apps/{GLOBEX}/webhook"),
            ("GET", f"/api/apps/{GLOBEX}/previews"),
            ("GET", f"/api/apps/{GLOBEX}/databases"),
            ("GET", f"/api/apps/{GLOBEX}/metrics"),
            ("GET", f"/api/sites/{GLOBEX}"),
            ("GET", f"/api/sites/{GLOBEX}/config"),
            ("GET", "/api/databases/databases/postgresql/blog_db"),
            ("GET", "/api/databases/databases/postgresql/blog_db/overview"),
            ("GET", "/api/databases/databases/postgresql/blog_db/rows"),
            ("GET", f"/api/certs/{GLOBEX}"),
        ],
    )
    def test_reading_it_is_a_404(self, world: World, method: str, path: str) -> None:
        response = world.client.request(method, path, headers=world.as_("alice"))

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("PATCH", f"/api/apps/{GLOBEX}/health", {"path": "/z"}),
            ("PATCH", f"/api/apps/{GLOBEX}/branch", {"branch": "main"}),
            ("PATCH", f"/api/apps/{GLOBEX}/limits", {"memory_max_mb": 512}),
            ("PUT", f"/api/apps/{GLOBEX}/env", {"variables": {"A": "1"}}),
            ("POST", f"/api/apps/{GLOBEX}/restart", None),
            ("POST", f"/api/apps/{GLOBEX}/stop", None),
            ("POST", f"/api/apps/{GLOBEX}/domains", {"domain": "x.globex.example"}),
            ("POST", f"/api/apps/{GLOBEX}/move", {"project": "default"}),
            ("DELETE", f"/api/apps/{GLOBEX}", None),
            ("DELETE", f"/api/sites/{GLOBEX}", None),
            ("POST", f"/api/sites/{GLOBEX}/disable", None),
            ("DELETE", "/api/databases/databases/postgresql/blog_db", None),
            ("DELETE", f"/api/backup-schedules/{GLOBEX}", None),
            ("POST", "/api/jobs/update", {"domain": GLOBEX}),
            ("POST", "/api/jobs/delete", {"domain": GLOBEX}),
            ("POST", "/api/jobs/backup", {"domain": GLOBEX}),
            ("POST", "/api/jobs/rollback", {"domain": GLOBEX}),
            (
                "POST",
                "/api/databases/query",
                {"engine": "postgresql", "database": "blog_db", "statement": "select 1"},
            ),
        ],
    )
    def test_changing_it_is_a_404_and_changes_nothing(
        self, world: World, method: str, path: str, body: Any
    ) -> None:
        before = world.store.get_app(GLOBEX)

        response = world.client.request(method, path, json=body, headers=world.as_("alice"))

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"
        assert world.store.get_app(GLOBEX) == before
        assert world.store.get_site(GLOBEX) is not None
        assert world.store.get_database("blog_db", "postgresql") is not None
        assert world.store.get_backup_schedule(GLOBEX) is not None

    @pytest.mark.parametrize("who", ["dana", "omar"])
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("PATCH", f"/api/apps/{GLOBEX}/health", {"path": "/z"}),
            ("DELETE", f"/api/apps/{GLOBEX}", None),
            ("DELETE", f"/api/sites/{GLOBEX}", None),
            ("DELETE", "/api/databases/databases/postgresql/blog_db", None),
            ("POST", "/api/jobs/delete", {"domain": GLOBEX}),
        ],
    )
    def test_a_member_who_could_not_do_it_anywhere_is_still_told_it_is_not_there(
        self, world: World, who: str, method: str, path: str, body: Any
    ) -> None:
        # dana holds no permission to change anything; the answer for another team's
        # resource is the same 404 as for a member who holds it, never a 403 about it.
        response = world.client.request(method, path, json=body, headers=world.as_(who))

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"

    def test_the_answer_is_the_one_for_a_resource_that_does_not_exist(self, world: World) -> None:
        hidden = world.client.get(f"/api/apps/{GLOBEX}", headers=world.as_("alice"))
        missing = world.client.get("/api/apps/nothing.example.com", headers=world.as_("alice"))

        assert hidden.status_code == missing.status_code == 404
        assert hidden.json().keys() == missing.json().keys()
        assert hidden.json()["error"] == missing.json()["error"]
        assert hidden.json()["detail"].replace(GLOBEX, "X") == missing.json()["detail"].replace(
            "nothing.example.com", "X"
        )

    def test_a_deployment_a_job_and_a_certificate_of_another_team_are_absent_too(
        self, world: World
    ) -> None:
        deployment = world.deployments[GLOBEX]

        for path in (
            f"/api/deployments/{deployment}",
            f"/api/deployments/{deployment}/log",
            "/api/jobs/job-blog",
            "/api/jobs/job-blog/log",
        ):
            response = world.client.get(path, headers=world.as_("alice"))
            assert response.status_code == 404, (path, response.text)
        assert (
            world.client.post("/api/jobs/job-blog/cancel", headers=world.as_("alice")).status_code
            == 404
        )

    def test_the_build_queue_holds_only_the_callers_builds_and_counts_only_them(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.api import jobs as jobs_api
        from noust.web.jobs import Job, JobType, QueueSnapshot

        def job(name: str, domain: str) -> Job:
            return Job(
                id=name,
                type=JobType.DEPLOY,
                name=name,
                description="",
                metadata={"domain": domain},
            )

        snapshot = QueueSnapshot(
            running=(job("run-acme", ACME), job("run-globex", GLOBEX)),
            queued=(job("wait-globex", GLOBEX), job("wait-acme", ACME)),
            builds_running=2,
            max_concurrent_builds=2,
            queue_limit=10,
            builds_queued=2,
        )

        class FakeManager:
            def queue(self) -> QueueSnapshot:
                return snapshot

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: FakeManager())

        mine = world.client.get("/api/jobs/queue", headers=world.as_("alice")).json()
        everything = world.client.get("/api/jobs/queue", headers=world.as_("admin")).json()

        assert [j["id"] for j in mine["running"]] == ["run-acme"]
        assert [j["id"] for j in mine["queued"]] == ["wait-acme"]
        assert (mine["builds_running"], mine["builds_queued"]) == (1, 1)
        assert mine["queue_limit"] == 10
        assert {j["id"] for j in everything["running"]} == {"run-acme", "run-globex"}
        assert (everything["builds_running"], everything["builds_queued"]) == (2, 2)

    def test_the_owner_team_member_reaches_the_same_resources(self, world: World) -> None:
        deployment = world.deployments[GLOBEX]

        for path in (
            f"/api/apps/{GLOBEX}",
            f"/api/apps/{GLOBEX}/domains",
            f"/api/sites/{GLOBEX}",
            "/api/databases/databases/postgresql/blog_db",
            f"/api/deployments/{deployment}",
            "/api/jobs/job-blog",
        ):
            response = world.client.get(path, headers=world.as_("bob"))
            assert response.status_code != 404, (path, response.text)

    def test_a_change_inside_the_team_goes_through(self, world: World) -> None:
        response = world.client.patch(
            f"/api/apps/{ACME}/health", json={"path": "/healthz"}, headers=world.as_("alice")
        )

        assert response.status_code == 200, response.text
        app = world.store.get_app(ACME)
        assert app is not None and app.health_path == "/healthz"


class TestARoleInATeamIsHeldToItsPermissions:
    def test_a_viewer_of_the_team_reads_but_does_not_change(self, world: World) -> None:
        read = world.client.get(f"/api/apps/{ACME}", headers=world.as_("dana"))
        write = world.client.patch(
            f"/api/apps/{ACME}/health", json={"path": "/z"}, headers=world.as_("dana")
        )

        assert read.status_code == 200
        assert write.status_code == 403
        assert write.json()["error"] == "permission_denied"

    def test_a_role_in_one_team_gives_nothing_in_another(self, world: World) -> None:
        world.teams.add_member("globex", "alice", "viewer")

        # alice is an admin of acme and only a viewer of globex.
        in_acme = world.client.patch(
            f"/api/apps/{ACME}/health", json={"path": "/a"}, headers=world.as_("alice")
        )
        in_globex = world.client.patch(
            f"/api/apps/{GLOBEX}/health", json={"path": "/g"}, headers=world.as_("alice")
        )

        assert in_acme.status_code == 200
        assert in_globex.status_code == 403
        assert in_globex.json()["error"] == "permission_denied"
        assert (
            world.client.get(f"/api/apps/{GLOBEX}", headers=world.as_("alice")).status_code == 200
        )

    def test_a_member_of_two_teams_sees_both_and_asks_for_each(self, world: World) -> None:
        world.teams.add_member("globex", "alice", "viewer")

        body = world.client.get("/api/apps", headers=world.as_("alice")).json()

        assert domains(body) == {ACME, GLOBEX}

    def test_the_instance_role_is_the_floor_the_team_role_raises(self, world: World) -> None:
        # omar is an operator on the server and an operator of acme: he may restart and deploy
        # there, and may not manage; dana is a viewer of acme and of the server: only reads.
        deploy = world.client.post(
            "/api/jobs/update", json={"domain": ACME}, headers=world.as_("omar")
        )
        manage = world.client.patch(
            f"/api/apps/{ACME}/health", json={"path": "/z"}, headers=world.as_("omar")
        )

        assert deploy.status_code != 403, deploy.text
        assert manage.status_code == 403

    def test_a_removed_member_loses_sight_of_the_team_on_its_next_request(
        self, world: World
    ) -> None:
        assert world.client.get(f"/api/apps/{ACME}", headers=world.as_("dana")).status_code == 200

        world.teams.remove_member("acme", "dana")

        assert world.client.get(f"/api/apps/{ACME}", headers=world.as_("dana")).status_code == 404
        assert domains(world.client.get("/api/apps", headers=world.as_("dana")).json()) == set()


class TestWhatIsAboutTheWholeServerIsClosedToATeamMember:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/server/summary"),
            ("GET", "/api/services"),
            ("GET", "/api/cron"),
            ("GET", "/api/backups"),
            ("GET", "/api/overview"),
            ("GET", "/api/timeline"),
            ("GET", "/api/monitor/status"),
            ("GET", "/api/metrics"),
            ("GET", "/api/system/processes"),
            ("GET", "/api/fleet/servers"),
            ("GET", "/api/integrations/github"),
            ("POST", "/api/sites/reload"),
            ("POST", "/api/apps/adopt"),
            ("GET", "/api/databases/engines/postgresql/logs"),
            ("GET", "/api/databases/users/postgresql"),
        ],
    )
    def test_it_is_refused_with_the_reason(self, world: World, method: str, path: str) -> None:
        response = world.client.request(method, path, json={}, headers=world.as_("alice"))

        assert response.status_code == 403, response.text
        assert response.json()["error"] == "instance_scope_required"

    def test_the_same_routes_still_answer_an_administrator(self, world: World) -> None:
        for path in ("/api/server/summary", "/api/services", "/api/overview", "/api/nodes"):
            response = world.client.get(path, headers=world.as_("admin"))
            assert response.status_code != 403, (path, response.text)

    def test_a_node_of_no_team_of_its_own_is_neither_listed_nor_reached(self, world: World) -> None:
        # Servers of the fleet can be given to a team (docs/teams.md); one that is not given to
        # any of the member's teams is a resource that does not exist, never a 403.
        listed = world.client.get("/api/nodes", headers=world.as_("alice"))
        reached = world.client.get("/api/nodes/lisbon/api/apps", headers=world.as_("alice"))

        assert listed.status_code == 200, listed.text
        assert listed.json()["items"] == []
        assert reached.status_code == 404
        assert reached.json()["error"] == "not_found"

    def test_what_is_about_nobodys_resources_answers_as_before(self, world: World) -> None:
        for path in ("/api/config", "/api/recipes", "/api/apps/types", "/api/teams/mine"):
            assert world.client.get(path, headers=world.as_("alice")).status_code == 200, path


class TestCreatingNamesWhereItGoes:
    def test_a_member_cannot_create_in_a_team_it_does_not_belong_to(self, world: World) -> None:
        body = {"domain": "new.acme.example", "source": "https://x.test/r.git", "team": "globex"}

        response = world.client.post("/api/apps", json=body, headers=world.as_("alice"))

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_a_viewer_cannot_create_in_its_own_team(self, world: World) -> None:
        body = {"domain": "new.acme.example", "source": "https://x.test/r.git"}

        response = world.client.post("/api/apps", json=body, headers=world.as_("dana"))

        assert response.status_code == 403

    def test_an_admin_of_the_team_creates_into_it(self, world: World) -> None:
        body = {"domain": "new.acme.example", "source": "https://x.test/r.git"}

        response = world.client.post("/api/apps", json=body, headers=world.as_("alice"))

        assert response.status_code == 202, response.text
        from noust.web.jobs import get_job_manager

        job = get_job_manager().get_job(response.json()["job_id"])
        assert job is not None
        assert job.ownership == (
            world.targets["acme"].project_id,
            world.targets["acme"].environment_id,
        )

    def test_a_port_another_team_holds_is_refused_without_naming_its_application(
        self, world: World
    ) -> None:
        theirs = world.store.get_app(GLOBEX)
        assert theirs is not None
        theirs.port = 3100
        world.store.update_app(theirs)
        body = {"domain": "new.acme.example", "source": "https://x.test/r.git", "port": 3100}

        response = world.client.post("/api/apps", json=body, headers=world.as_("alice"))

        assert response.status_code == 400, response.text
        assert "another application" in response.text
        assert GLOBEX not in response.text

    def test_a_domain_another_team_holds_is_a_conflict_for_everyone(self, world: World) -> None:
        body = {"domain": GLOBEX, "source": "https://x.test/r.git"}

        response = world.client.post("/api/apps", json=body, headers=world.as_("alice"))

        assert response.status_code == 409

    def test_a_site_is_created_in_the_project_asked_for(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.store import Site
        from noust.managers.webserver import SecuredSite
        from noust.web.api import sites as sites_api

        def write_the_site(domain: str, **_: Any) -> SecuredSite:
            # What the web server manager does: record the site it wrote, knowing no application.
            world.store.create_site(Site(domain=domain, config_path=f"/etc/nginx/{domain}"))
            return SecuredSite(domain=domain, webserver="nginx")

        monkeypatch.setattr(sites_api, "create_secured_site", write_the_site)

        mine = world.client.post(
            "/api/sites",
            json={"domain": "extra.acme.example", "template": "proxy", "port": 3010},
            headers=world.as_("alice"),
        )
        theirs = world.client.post(
            "/api/sites",
            json={
                "domain": "extra.globex.example",
                "template": "proxy",
                "port": 3011,
                "team": "globex",
            },
            headers=world.as_("alice"),
        )

        assert mine.status_code == 200, mine.text
        site = world.store.get_site("extra.acme.example")
        assert site is not None and site.project_id == world.targets["acme"].project_id
        assert theirs.status_code == 404
        assert world.store.get_site("extra.globex.example") is None
        # And it is hers to see, and not bob's.
        assert (
            world.client.get(
                "/api/sites/extra.acme.example", headers=world.as_("alice")
            ).status_code
            != 404
        )
        assert (
            world.client.get("/api/sites/extra.acme.example", headers=world.as_("bob")).status_code
            == 404
        )

    def test_a_database_is_created_in_the_project_asked_for(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.store import Database

        def create(
            self: DatabaseService,
            engine: str,
            name: str,
            owner: str | None = None,
            encoding: str | None = None,
            domain: str | None = None,
        ) -> DatabaseView:
            # What the service does: record the database it made, linked to its application when given.
            app = world.store.get_app(domain) if domain else None
            world.store.create_database(
                Database(name=name, engine=engine, app_id=app.id if app else None)
            )
            return DatabaseView(name=name, engine=engine, tracked=True)

        monkeypatch.setattr(DatabaseService, "create", create)

        loose = world.client.post(
            "/api/databases/databases",
            json={"name": "scratch", "engine": "postgresql"},
            headers=world.as_("alice"),
        )
        linked = world.client.post(
            "/api/databases/databases",
            json={"name": "orders", "engine": "postgresql", "app": ACME},
            headers=world.as_("alice"),
        )
        elsewhere = world.client.post(
            "/api/databases/databases",
            json={"name": "elsewhere", "engine": "postgresql", "team": "globex"},
            headers=world.as_("alice"),
        )

        assert loose.status_code == linked.status_code == 200, (loose.text, linked.text)
        for name in ("scratch", "orders"):
            record = world.store.get_database(name, "postgresql")
            assert record is not None and record.project_id == world.targets["acme"].project_id
        assert elsewhere.status_code == 404
        assert world.store.get_database("elsewhere", "postgresql") is None
        # Bob does not see what alice made.
        bob = world.client.get(
            "/api/databases/databases/postgresql/scratch", headers=world.as_("bob")
        )
        assert bob.status_code == 404

    def test_a_viewer_creates_no_database_and_a_foreign_app_is_not_linked(
        self, world: World
    ) -> None:
        viewer = world.client.post(
            "/api/databases/databases",
            json={"name": "scratch", "engine": "postgresql"},
            headers=world.as_("dana"),
        )
        foreign = world.client.post(
            "/api/databases/databases",
            json={"name": "stolen", "engine": "postgresql", "app": GLOBEX},
            headers=world.as_("alice"),
        )

        assert viewer.status_code == 403
        assert foreign.status_code == 400
        assert world.store.get_database("stolen", "postgresql") is None


class TestACentralStillDrivesTheWholeNode:
    """A node with teams is still one a central manages: the fleet token is the instance's."""

    @pytest.fixture
    def central(self, world: World) -> tuple[TestClient, dict[str, str]]:
        # What a central sends through its tunnel: the token, from loopback, naming the
        # person behind it and what that person may do.
        token = str(get_token_manager().create_fleet_token("fleet-hub")["token"])
        client = TestClient(world.client.app, client=("127.0.0.1", 50001))
        headers = {
            "Authorization": f"Bearer {token}",
            FLEET_ACTOR_HEADER: "ana",
            FLEET_ACTOR_SCOPE_HEADER: "admin",
        }
        return client, headers

    def test_it_lists_every_team_applications_and_every_team(
        self, world: World, central: tuple[TestClient, dict[str, str]]
    ) -> None:
        client, headers = central

        apps = client.get("/api/apps", headers=headers)
        teams = client.get("/api/teams", headers=headers)

        assert apps.status_code == 200, apps.text
        assert domains(apps.json()) == {ACME, GLOBEX}
        assert {t["slug"] for t in teams.json()["teams"]} == {"acme", "globex", "default"}

    def test_it_reaches_what_a_team_member_cannot(
        self, world: World, central: tuple[TestClient, dict[str, str]]
    ) -> None:
        client, headers = central

        for path in (f"/api/apps/{GLOBEX}", "/api/server/summary", "/api/services"):
            response = client.get(path, headers=headers)
            assert response.status_code == 200, (path, response.text)

    def test_what_it_forwards_does_not_narrow_it_to_the_teams_of_the_person_it_names(
        self, world: World, central: tuple[TestClient, dict[str, str]]
    ) -> None:
        # alice belongs to acme only; the central acting for her name still reads the node whole,
        # because the node's own teams are not the central's to apply.
        client, headers = central
        headers = {**headers, FLEET_ACTOR_HEADER: "alice"}

        assert domains(client.get("/api/apps", headers=headers).json()) == {ACME, GLOBEX}

    def test_the_same_token_from_anywhere_else_is_still_refused(
        self, world: World, central: tuple[TestClient, dict[str, str]]
    ) -> None:
        _client, headers = central
        elsewhere = TestClient(world.client.app, client=("203.0.113.9", 50000))

        assert elsewhere.get("/api/apps", headers=headers).status_code == 401
