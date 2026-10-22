# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ownership filter of the data layer, asked directly (I-04).

:class:`~noust.core.teams.scoped.ScopedStore` is what the API reads applications,
databases, sites and backup schedules through. What these tests pin is the
filter itself, in SQL and before any route: what a scope does not see is not
returned, not counted and not found, and never reads as anything but absent.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.store import (
    App,
    BackupScheduleRecord,
    Database,
    JobRecord,
    NoustStore,
    Site,
)
from noust.core.teams import (
    ResourceNotFoundError,
    Scope,
    ScopedStore,
    Target,
    TeamStore,
    bind,
    current_scope,
    scoped_store,
    visible_clause,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store with two teams, each with an application and what belongs to it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def world(store: NoustStore) -> dict[str, Target]:
    """Where each team's default project is, with resources in both."""
    teams = TeamStore(store)
    targets = {}
    for slug in ("acme", "globex"):
        teams.create_team(slug.title(), slug=slug)
        targets[slug] = teams.resolve_target(Scope.everything(), team=slug)
        domain = f"{slug}.example.com"
        app = store.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                app_path=f"/srv/{slug}",
                status="running" if slug == "acme" else "stopped",
                project_id=targets[slug].project_id,
                environment_id=targets[slug].environment_id,
            )
        )
        store.create_site(Site(app_id=app.id, domain=domain, config_path=f"/etc/{slug}"))
        store.create_database(Database(app_id=app.id, name=f"{slug}_db", engine="mysql"))
        store.save_backup_schedule(BackupScheduleRecord(app_domain=domain, schedule="daily"))
        store.add_domain(domain, f"www.{domain}", "alias")
        store.record_deployment_start(domain, "panel")
        store.create_job(JobRecord(id=f"job-{slug}", type="deploy", name=slug, domain=domain))
    store.create_job(JobRecord(id="job-server", type="update", name="Update the server"))
    return targets


def member_of(store: NoustStore, world: dict[str, Target], *slugs: str) -> ScopedStore:
    """
    Args:
        store: The store.
        world: Where each team lives.
        *slugs: The teams the principal belongs to.

    Returns:
        A scoped store for a principal that sees only those.
    """
    roles = {world[slug].team_id: "admin" for slug in slugs}
    return ScopedStore(Scope(team_roles=roles, sees_all=False), store)


class TestTheClause:
    def test_a_scope_that_sees_everything_filters_nothing(self) -> None:
        assert visible_clause(Scope.everything()) == ("1 = 1", [])

    def test_a_scope_that_sees_nothing_matches_nothing(self) -> None:
        assert visible_clause(Scope.nothing()) == ("0 = 1", [])

    def test_a_scope_of_teams_asks_the_projects_of_those_teams(self) -> None:
        clause, params = visible_clause(Scope(team_roles={3: "viewer", 1: "admin"}), "a.project_id")

        assert clause == "a.project_id IN (SELECT id FROM projects WHERE team_id IN (?, ?))"
        assert params == [1, 3]

    def test_the_team_ids_of_a_principal_that_sees_all_are_none(self) -> None:
        assert Scope.everything().team_ids is None
        assert Scope(team_roles={1: "admin"}).team_ids == frozenset({1})

    def test_a_row_with_no_team_is_seen_only_by_a_principal_that_sees_everything(self) -> None:
        assert Scope.everything().sees_team(None)
        assert not Scope(team_roles={1: "admin"}).sees_team(None)


class TestApplications:
    def test_a_member_lists_and_reads_its_teams_applications_only(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert [a.domain for a in acme.list_apps()] == ["acme.example.com"]
        assert acme.get_app("acme.example.com") is not None
        assert acme.get_app("globex.example.com") is None
        assert acme.get_app_by_id(2) is None
        assert not acme.app_exists("globex.example.com")

    def test_a_member_of_both_sees_both(self, store: NoustStore, world: dict[str, Target]) -> None:
        both = member_of(store, world, "acme", "globex")

        assert {a.domain for a in both.list_apps()} == {"acme.example.com", "globex.example.com"}

    def test_the_other_filters_apply_inside_the_scope(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        both = member_of(store, world, "acme", "globex")

        assert [a.domain for a in both.list_apps(status="stopped")] == ["globex.example.com"]
        assert [a.domain for a in both.list_apps(team="acme")] == ["acme.example.com"]
        assert both.list_apps(project="nope") == []
        assert {a.domain for a in both.list_apps(project="default", environment="production")} == {
            "acme.example.com",
            "globex.example.com",
        }
        # A member of one team cannot reach the other's by naming it.
        assert member_of(store, world, "acme").list_apps(team="globex") == []

    def test_a_row_nobody_owns_is_not_seen_by_a_member(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE apps SET project_id = NULL, environment_id = NULL WHERE domain = ?",
                ("acme.example.com",),
            )

        assert member_of(store, world, "acme").get_app("acme.example.com") is None
        assert ScopedStore(Scope.everything(), store).get_app("acme.example.com") is not None

    def test_a_principal_in_no_team_sees_nothing(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        nobody = ScopedStore(Scope.nothing(), store)

        assert nobody.list_apps() == []
        assert nobody.list_sites() == []
        assert nobody.list_databases() == []
        assert nobody.list_backup_schedules() == []
        assert nobody.list_deployments() == []
        assert nobody.list_jobs() == []

    def test_a_hidden_application_is_as_absent_as_one_that_never_existed(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        with pytest.raises(ResourceNotFoundError) as hidden:
            acme.require_app("globex.example.com")
        with pytest.raises(ResourceNotFoundError) as missing:
            acme.require_app("ghost.example.com")

        assert hidden.value.message.replace("globex", "X") == missing.value.message.replace(
            "ghost", "X"
        )
        assert type(hidden.value) is type(missing.value)
        assert hidden.value.field == missing.value.field is None

    def test_a_name_taken_by_another_team_is_still_taken(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        assert member_of(store, world, "acme").domain_is_taken("globex.example.com")
        assert not member_of(store, world, "acme").domain_is_taken("free.example.com")

    def test_the_domains_of_a_hidden_application_are_not_listed(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert {d.domain for d in acme.list_domains("acme.example.com")} == {
            "acme.example.com",
            "www.acme.example.com",
        }
        with pytest.raises(ResourceNotFoundError):
            acme.list_domains("globex.example.com")


class TestWhatBelongsToAnApplication:
    def test_sites_databases_and_schedules_are_filtered_the_same_way(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert [s.domain for s in acme.list_sites()] == ["acme.example.com"]
        assert acme.get_site("globex.example.com") is None
        assert acme.get_site_by_app_id(2) is None
        assert [d.name for d in acme.list_databases()] == ["acme_db"]
        assert acme.get_database("globex_db", "mysql") is None
        assert [s.app_domain for s in acme.list_backup_schedules()] == ["acme.example.com"]
        assert acme.get_backup_schedule("globex.example.com") is None

    def test_the_databases_the_engines_hold_but_nobody_recorded_belong_to_nobody(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert acme.visible_database_keys() == {("acme_db", "mysql")}
        assert acme.sees_database("acme_db", "mysql")
        assert not acme.sees_database("orphan", "mysql")
        assert ScopedStore(Scope.everything(), store).visible_database_keys() is None
        assert ScopedStore(Scope.everything(), store).sees_database("orphan", "mysql")

    def test_a_database_that_is_not_visible_is_refused_as_absent(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert acme.require_database("acme_db", "mysql") is not None
        with pytest.raises(ResourceNotFoundError):
            acme.require_database("globex_db", "mysql")

    def test_the_filters_of_a_list_name_a_team_a_project_and_an_environment(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        everything = ScopedStore(Scope.everything(), store)

        assert [d.name for d in everything.list_databases(team="globex")] == ["globex_db"]
        assert [s.domain for s in everything.list_sites(project="default", team="acme")] == [
            "acme.example.com"
        ]
        assert everything.list_databases(environment="staging") == []
        assert [s.app_domain for s in everything.list_backup_schedules(team="globex")] == [
            "globex.example.com"
        ]


class TestHistory:
    def test_deployments_follow_their_applications(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert {d.domain for d in acme.list_deployments()} == {"acme.example.com"}
        assert acme.list_deployments(domain="globex.example.com") == []
        assert acme.get_deployment(1) is not None
        assert acme.get_deployment(2) is None
        assert set(acme.get_latest_deployments(["acme.example.com", "globex.example.com"])) == {
            "acme.example.com"
        }

    def test_the_history_of_a_deleted_application_is_the_instances(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        store.delete_app("acme.example.com")

        assert member_of(store, world, "acme").list_deployments() == []
        assert len(ScopedStore(Scope.everything(), store).list_deployments()) == 2

    def test_jobs_follow_their_applications_and_a_job_of_the_server_is_the_instances(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert [j.id for j in acme.list_jobs()] == ["job-acme"]
        assert acme.get_job("job-globex") is None
        assert acme.get_job("job-server") is None
        assert {j.id for j in ScopedStore(Scope.everything(), store).list_jobs()} == {
            "job-acme",
            "job-globex",
            "job-server",
        }

    def test_a_job_filter_applies_inside_the_scope(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")

        assert acme.list_jobs(status="pending")[0].id == "job-acme"
        assert acme.list_jobs(status="failed") == []
        assert acme.list_jobs(domain="globex.example.com") == []
        assert len(acme.list_jobs(limit=1)) == 1


class TestDomains:
    def test_a_domain_is_an_applications_an_alias_or_a_site(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")
        store.create_site(
            Site(
                app_id=None,
                domain="hand.example.com",
                config_path="/x",
                project_id=world["acme"].project_id,
                environment_id=world["acme"].environment_id,
            )
        )

        for domain in ("acme.example.com", "www.acme.example.com", "hand.example.com"):
            assert acme.sees_domain(domain), domain
        for domain in (
            "globex.example.com",
            "www.globex.example.com",
            "ghost.example.com",
            "",
            None,
        ):
            assert not acme.sees_domain(domain), domain

    def test_a_site_nobody_recorded_is_not_a_teams(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        assert not member_of(store, world, "acme").sees_domain("hand-written.example.com")
        assert ScopedStore(Scope.everything(), store).sees_domain("hand-written.example.com")

    def test_the_team_of_a_domain(self, store: NoustStore, world: dict[str, Target]) -> None:
        acme = member_of(store, world, "acme")

        assert acme.team_of_domain("www.acme.example.com") == world["acme"].team_id
        assert acme.team_of_domain("globex.example.com") is None
        assert acme.team_of_domain(None) is None

    def test_a_list_from_elsewhere_is_cut_to_the_visible_domains(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        acme = member_of(store, world, "acme")
        items = [
            {"domain": "acme.example.com"},
            {"domain": "globex.example.com"},
            {"domain": None},
            {"domain": "www.acme.example.com"},
        ]

        kept = acme.filter_domains(items, lambda item: item["domain"])

        assert [item["domain"] for item in kept] == ["acme.example.com", "www.acme.example.com"]
        assert (
            ScopedStore(Scope.everything(), store).filter_domains(
                items, lambda item: item["domain"]
            )
            == items
        )

    def test_the_visible_domains(self, store: NoustStore, world: dict[str, Target]) -> None:
        acme = member_of(store, world, "acme")

        assert acme.visible_domains() == {"acme.example.com", "www.acme.example.com"}
        assert ScopedStore(Scope.everything(), store).visible_domains() is None


class TestTheAmbientScope:
    def test_outside_a_request_it_is_the_instances(self) -> None:
        assert current_scope().sees_all

    def test_what_is_bound_is_what_the_accessor_asks_with(
        self, store: NoustStore, world: dict[str, Target]
    ) -> None:
        roles = {world["acme"].team_id: "admin"}

        with bind(Scope(team_roles=roles)):
            inside = scoped_store().list_apps()
        outside = scoped_store().list_apps()

        assert [a.domain for a in inside] == ["acme.example.com"]
        assert len(outside) == 2

    def test_a_block_does_not_leak_its_scope(self) -> None:
        with bind(Scope.nothing()):
            assert current_scope().restricted
        assert not current_scope().restricted
