# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Teams, projects, environments and who belongs where (I-01, I-02, I-10).

The store of teams is the one reader and writer of what the 3.4 schema adds:
what is refused (a name taken, the last owner leaving, a team that still holds
something being deleted, a security officer joining a team) and what follows
from what is done (every new project has its production environment, every new
row has an owner, a move takes the sites and databases with the application).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.accounts import ROLES, AccountManager, passwords
from noust.core.accounts.model import ROLE_ADMIN, ROLE_AUDITOR, ROLE_OPERATOR, ROLE_VIEWER
from noust.core.exceptions import ValidationError
from noust.core.store import App, BackupScheduleRecord, Database, NoustStore, Site
from noust.core.teams import (
    DEFAULT_SLUG,
    INSTANCE_ONLY_ROLES,
    INSTANCE_WIDE_ROLES,
    PRODUCTION_SLUG,
    TEAM_ROLES,
    Scope,
    TeamConflictError,
    TeamNotFoundError,
    TeamStore,
    ownership_target,
)
from noust.core.teams.model import (
    TEAM_ROLE_FOR_INSTANCE_ROLE,
    slugify,
    validate_slug,
    validate_team_role,
)
from noust.core.teams.onboarding import create_in_team, invite_to_team

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A fresh store, which already has its default team."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def teams(store: NoustStore) -> TeamStore:
    """The team store over it."""
    return TeamStore(store)


@pytest.fixture
def accounts(store: NoustStore) -> AccountManager:
    """The account manager over it."""
    return AccountManager(store)


def make_app(store: NoustStore, domain: str, **fields: object) -> App:
    """
    Args:
        store: The store.
        domain: The application's domain.
        **fields: Overrides.

    Returns:
        A recorded application.
    """
    return store.create_app(
        App(domain=domain, app_type="nodejs", app_path=f"/srv/{domain}", **fields)
    )  # type: ignore[arg-type]


class TestTheNamesAreTheAccountsOwn:
    def test_the_instance_roles_spelled_here_are_the_accounts_roles(self) -> None:
        # teams.model cannot import the accounts (they import the store, which imports teams).
        assert INSTANCE_WIDE_ROLES == {ROLE_ADMIN, "security", ROLE_AUDITOR}
        assert INSTANCE_ONLY_ROLES == {"security", ROLE_AUDITOR}
        assert set(TEAM_ROLE_FOR_INSTANCE_ROLE) == {ROLE_VIEWER, ROLE_OPERATOR, ROLE_ADMIN}
        assert INSTANCE_WIDE_ROLES | set(TEAM_ROLE_FOR_INSTANCE_ROLE) == set(ROLES)

    def test_a_team_role_has_a_matching_permission_set_in_the_console(self) -> None:
        from noust.web.permissions.team_roles import TEAM_ROLE_PERMISSIONS

        assert set(TEAM_ROLE_PERMISSIONS) == set(TEAM_ROLES)

    @pytest.mark.parametrize(
        ("name", "slug"),
        [("Acme Corp", "acme-corp"), ("  Shop EU  ", "shop-eu"), ("a__b", "a-b"), ("--x--", "x")],
    )
    def test_a_slug_is_made_from_a_name(self, name: str, slug: str) -> None:
        assert slugify(name) == slug

    @pytest.mark.parametrize("bad", ["", "-a", "a-", "a b", "a/b", "x" * 41, "é"])
    def test_a_slug_that_is_not_one_is_refused(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            validate_slug(bad)

    def test_a_team_role_that_is_not_one_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            validate_team_role("root")


class TestTheDefaultTeam:
    def test_a_fresh_store_has_it_with_its_project_and_environment(self, teams: TeamStore) -> None:
        assert [t.slug for t in teams.list_teams()] == [DEFAULT_SLUG]
        assert [(p.team, p.slug) for p in teams.list_projects()] == [(DEFAULT_SLUG, DEFAULT_SLUG)]
        assert [(e.project, e.slug) for e in teams.list_environments()] == [
            (DEFAULT_SLUG, PRODUCTION_SLUG)
        ]

    def test_a_new_row_goes_there_when_nobody_says_otherwise(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        app = make_app(store, "a.example.com")
        site = store.create_site(Site(app_id=None, domain="orphan.example.com", config_path="/x"))
        database = store.create_database(Database(name="loose", engine="mysql"))
        schedule = store.save_backup_schedule(
            BackupScheduleRecord(app_domain="nothing.example.com", schedule="daily")
        )

        default = teams.default_target()
        for row in (app, site, database, schedule):
            assert (row.project_id, row.environment_id) == (
                default.project_id,
                default.environment_id,
            )

    def test_it_cannot_be_deleted(self, teams: TeamStore) -> None:
        with pytest.raises(TeamConflictError):
            teams.delete_team(DEFAULT_SLUG)
        with pytest.raises(TeamConflictError):
            teams.delete_project(DEFAULT_SLUG, DEFAULT_SLUG)
        with pytest.raises(TeamConflictError):
            teams.delete_environment(DEFAULT_SLUG, DEFAULT_SLUG, PRODUCTION_SLUG)


class TestTeams:
    def test_a_team_starts_with_a_default_project_and_production(self, teams: TeamStore) -> None:
        team = teams.create_team("Acme Corp", created_by="test")

        assert team.slug == "acme-corp"
        assert [p.slug for p in teams.list_projects(team="acme-corp")] == [DEFAULT_SLUG]
        assert [e.slug for e in teams.list_environments(team="acme-corp")] == [PRODUCTION_SLUG]

    def test_a_slug_is_taken_once(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")

        with pytest.raises(TeamConflictError):
            teams.create_team("Another", slug="acme")

    def test_asking_for_one_that_is_not_there_is_not_found(self, teams: TeamStore) -> None:
        with pytest.raises(TeamNotFoundError):
            teams.require_team("nope")

    def test_renaming_keeps_the_slug(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")

        renamed = teams.rename_team("acme", "Acme Inc")

        assert (renamed.slug, renamed.name) == ("acme", "Acme Inc")

    def test_a_team_that_holds_something_is_not_deleted(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        with pytest.raises(TeamConflictError) as raised:
            teams.delete_team("acme")

        assert "1 application" in raised.value.message
        assert store.get_app("a.example.com") is not None

    def test_an_empty_team_is_deleted_with_its_projects_and_members(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        accounts.create("maria", ROLE_VIEWER, password=PASSWORD)
        teams.add_member("acme", "maria", "viewer")

        teams.delete_team("acme")

        assert teams.get_team("acme") is None
        assert teams.list_projects(team="acme") == []
        assert list(teams.memberships_of(accounts.require("maria").id)) == [
            teams.require_team(DEFAULT_SLUG).id
        ]


class TestProjectsAndEnvironments:
    def test_a_project_has_production_and_may_have_more(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")
        project = teams.create_project("acme", "Mobile app", created_by="test")
        teams.create_environment("acme", project.slug, "Staging", created_by="test")

        assert project.slug == "mobile-app"
        assert sorted(
            e.slug for e in teams.list_environments(team="acme", project="mobile-app")
        ) == [
            "production",
            "staging",
        ]

    def test_the_same_slug_may_exist_in_two_teams(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_team("Globex", slug="globex")

        teams.create_project("acme", "Web", slug="web")
        teams.create_project("globex", "Web", slug="web")

        assert {(p.team, p.slug) for p in teams.list_projects() if p.slug == "web"} == {
            ("acme", "web"),
            ("globex", "web"),
        }

    def test_a_duplicate_is_a_conflict(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")

        with pytest.raises(TeamConflictError):
            teams.create_project("acme", "Web again", slug="web")
        with pytest.raises(TeamConflictError):
            teams.create_environment("acme", "web", "Production again", slug="production")

    def test_the_last_environment_of_a_project_stays(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")

        with pytest.raises(TeamConflictError):
            teams.delete_environment("acme", DEFAULT_SLUG, PRODUCTION_SLUG)

    def test_what_holds_resources_is_not_deleted(self, store: NoustStore, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_environment("acme", DEFAULT_SLUG, "Staging")
        make_app(store, "a.example.com")
        teams.move_app_to(
            Scope.everything(),
            "a.example.com",
            team="acme",
            project="default",
            environment="staging",
        )

        with pytest.raises(TeamConflictError):
            teams.delete_environment("acme", DEFAULT_SLUG, "staging")
        with pytest.raises(TeamConflictError):
            teams.delete_project("acme", DEFAULT_SLUG)

    def test_the_database_itself_refuses_to_orphan_a_row(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        # Whatever a writer checked, deleting a project that an application is in fails.
        teams.create_team("Acme", slug="acme")
        make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")
        project = teams.require_project("acme", DEFAULT_SLUG)

        with pytest.raises(sqlite3.IntegrityError), store._transaction() as cursor:
            cursor.execute("DELETE FROM projects WHERE id = ?", (project.id,))

    def test_the_database_itself_refuses_an_environment_of_another_project(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_team("Globex", slug="globex")
        app = make_app(store, "a.example.com")
        other = teams.require_environment("globex", DEFAULT_SLUG, PRODUCTION_SLUG)
        mine = teams.require_project("acme", DEFAULT_SLUG)

        with pytest.raises(sqlite3.IntegrityError), store._transaction() as cursor:
            cursor.execute(
                "UPDATE apps SET project_id = ?, environment_id = ? WHERE id = ?",
                (mine.id, other.id, app.id),
            )


class TestMembers:
    def test_an_account_joins_with_a_role_and_changes_it(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)

        first = teams.add_member("acme", "maria", "viewer", created_by="test")
        second = teams.add_member("acme", "maria", "admin", created_by="test")

        assert (first.role, second.role) == ("viewer", "admin")
        assert [m.username for m in teams.list_members("acme")] == ["maria"]

    def test_the_strongest_role_is_listed_first(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        for name, role in (("zed", "owner"), ("amy", "viewer"), ("bob", "admin")):
            accounts.create(name, ROLE_VIEWER, password=PASSWORD, join_default_team=False)
            teams.add_member("acme", name, role)

        assert [m.username for m in teams.list_members("acme")] == ["zed", "bob", "amy"]

    @pytest.mark.parametrize("role", sorted(INSTANCE_ONLY_ROLES))
    def test_security_and_auditor_accounts_never_join_a_team(
        self, teams: TeamStore, accounts: AccountManager, role: str
    ) -> None:
        accounts.create("officer", role, password=PASSWORD)
        teams.create_team("Acme", slug="acme")

        with pytest.raises(ValidationError) as raised:
            teams.add_member("acme", "officer", "viewer")

        assert role in raised.value.message

    def test_an_unknown_account_or_role_is_refused(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        accounts.create("maria", ROLE_VIEWER, password=PASSWORD)

        with pytest.raises(TeamNotFoundError):
            teams.add_member("acme", "nobody", "viewer")
        with pytest.raises(ValidationError):
            teams.add_member("acme", "maria", "emperor")

    def test_the_last_owner_cannot_leave_or_be_demoted(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        accounts.create("owner1", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        accounts.create("owner2", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        teams.add_member("acme", "owner1", "owner")

        with pytest.raises(TeamConflictError):
            teams.remove_member("acme", "owner1")
        with pytest.raises(TeamConflictError):
            teams.add_member("acme", "owner1", "admin")

        teams.add_member("acme", "owner2", "owner")
        teams.remove_member("acme", "owner1")
        assert [m.username for m in teams.list_members("acme")] == ["owner2"]

    def test_removing_a_member_forgets_its_active_team(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        team = teams.create_team("Acme", slug="acme")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        teams.add_member("acme", "maria", "viewer")
        teams.set_active_team(account.id, team.id)

        teams.remove_member("acme", "maria")

        assert teams.active_team(account.id) is None

    def test_removing_an_account_removes_its_memberships(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        teams.add_member("acme", "maria", "viewer")

        accounts.remove("maria")

        assert teams.list_members("acme") == []
        assert teams.memberships_of(account.id) == {}


class TestNewAccountsAreWhereTheyWereBeforeTeams:
    @pytest.mark.parametrize(
        ("role", "team_role"),
        [("viewer", "viewer"), ("operator", "operator"), ("admin", "admin")],
    )
    def test_an_account_joins_the_default_team_with_the_matching_role(
        self, teams: TeamStore, accounts: AccountManager, role: str, team_role: str
    ) -> None:
        account = accounts.create("maria", role, password=PASSWORD)

        assert teams.memberships_of(account.id) == {teams.require_team(DEFAULT_SLUG).id: team_role}

    @pytest.mark.parametrize("role", sorted(INSTANCE_ONLY_ROLES))
    def test_a_security_officer_or_an_auditor_joins_nothing(
        self, teams: TeamStore, accounts: AccountManager, role: str
    ) -> None:
        account = accounts.create("officer", role, password=PASSWORD)

        assert teams.memberships_of(account.id) == {}

    def test_an_invitation_does_too(self, teams: TeamStore, accounts: AccountManager) -> None:
        account, _code = accounts.invite("maria", ROLE_OPERATOR)

        assert list(teams.memberships_of(account.id).values()) == ["operator"]

    def test_becoming_an_auditor_leaves_every_team(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD)
        teams.add_member("acme", "maria", "viewer")

        accounts.set_role("maria", ROLE_AUDITOR)

        assert teams.memberships_of(account.id) == {}

    def test_coming_back_from_security_joins_the_default_team(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        account = accounts.create("maria", "security", password=PASSWORD)

        accounts.set_role("maria", ROLE_OPERATOR)

        assert list(teams.memberships_of(account.id).values()) == ["operator"]

    def test_a_person_created_for_a_team_belongs_to_it_only(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")

        account, membership = create_in_team(
            accounts, "maria", ROLE_VIEWER, password=PASSWORD, team="acme", team_role="operator"
        )

        assert membership.role == "operator"
        assert teams.memberships_of(account.id) == {acme.id: "operator"}

    def test_a_person_invited_to_a_team_belongs_to_it_only(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")

        account, code, membership = invite_to_team(
            accounts, "maria", ROLE_VIEWER, team="acme", team_role="admin"
        )

        assert code
        assert account.status == "invited"
        assert teams.memberships_of(account.id) == {acme.id: "admin"}

    def test_nothing_is_written_when_the_team_or_the_role_is_refused(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        with pytest.raises(TeamNotFoundError):
            invite_to_team(accounts, "maria", ROLE_VIEWER, team="nope")
        teams.create_team("Acme", slug="acme")
        with pytest.raises(ValidationError):
            invite_to_team(accounts, "officer", "security", team="acme")
        with pytest.raises(ValidationError):
            invite_to_team(accounts, "maria", ROLE_VIEWER, team="acme", team_role="emperor")

        assert accounts.find("maria") is None
        assert accounts.find("officer") is None

    def test_an_existing_account_is_added_not_invited(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        accounts.create("maria", ROLE_VIEWER, password=PASSWORD)

        with pytest.raises(ValidationError):
            invite_to_team(accounts, "maria", ROLE_VIEWER, team="acme")


class TestWhoSeesWhat:
    def test_the_instance_roles_that_see_everything(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        for name, role in (("ann", "admin"), ("sam", "security"), ("uri", "auditor")):
            account = accounts.create(name, role, password=PASSWORD)
            assert teams.scope_for(account.id, role).sees_all

    def test_a_viewer_or_operator_sees_its_teams(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")
        teams.create_team("Globex", slug="globex")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        teams.add_member("acme", "maria", "operator")

        scope = teams.scope_for(account.id, ROLE_VIEWER)

        assert not scope.sees_all
        assert scope.team_ids == {acme.id}
        assert scope.role_in(acme.id) == "operator"

    def test_a_member_of_every_team_sees_every_team(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD)

        # A server with one team: nothing is hidden from anybody, as before teams.
        assert teams.scope_for(account.id, ROLE_VIEWER).sees_all

        teams.create_team("Acme", slug="acme")
        assert not teams.scope_for(account.id, ROLE_VIEWER).sees_all

        teams.add_member("acme", "maria", "viewer")
        assert teams.scope_for(account.id, ROLE_VIEWER).sees_all

    def test_a_team_nobody_belongs_to_is_empty_for_a_viewer(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)

        scope = teams.scope_for(account.id, ROLE_VIEWER)

        assert scope.team_ids == frozenset()


class TestResolvingWhereSomethingGoes:
    def test_a_principal_that_sees_everything_gets_the_default_unless_it_says(
        self, teams: TeamStore
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")

        default = teams.resolve_target(Scope.everything())
        named = teams.resolve_target(Scope.everything(), team="acme")

        assert default.team_id == teams.require_team(DEFAULT_SLUG).id
        assert named.team_id == acme.id

    def test_a_member_of_one_team_works_in_it(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")
        teams.create_team("Globex", slug="globex")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        teams.add_member("acme", "maria", "admin")
        scope = teams.scope_for(account.id, ROLE_VIEWER)

        assert teams.resolve_target(scope).team_id == acme.id

    def test_a_member_of_several_chooses_or_is_asked(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")
        globex = teams.create_team("Globex", slug="globex")
        teams.create_team("Initech", slug="initech")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        teams.add_member("acme", "maria", "admin")
        teams.add_member("globex", "maria", "admin")
        scope = teams.scope_for(account.id, ROLE_VIEWER)

        with pytest.raises(Exception) as raised:
            teams.resolve_target(scope)
        assert "several teams" in str(raised.value)

        teams.set_active_team(account.id, globex.id)
        assert teams.resolve_target(scope).team_id == globex.id
        assert teams.resolve_target(scope, team="acme").team_id == acme.id

    def test_a_team_the_member_is_not_in_reads_as_absent(
        self, teams: TeamStore, accounts: AccountManager
    ) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_team("Globex", slug="globex")
        account = accounts.create("maria", ROLE_VIEWER, password=PASSWORD, join_default_team=False)
        teams.add_member("acme", "maria", "admin")
        scope = teams.scope_for(account.id, ROLE_VIEWER)

        with pytest.raises(TeamNotFoundError) as hidden:
            teams.resolve_target(scope, team="globex")
        with pytest.raises(TeamNotFoundError) as missing:
            teams.resolve_target(scope, team="nowhere")

        assert str(hidden.value).replace("globex", "X") == str(missing.value).replace(
            "nowhere", "X"
        )

    def test_an_environment_must_be_the_projects(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        teams.create_environment("acme", "web", "Staging")

        target = teams.resolve_target(
            Scope.everything(), team="acme", project="web", environment="staging"
        )

        assert teams.environment_by_id(target.environment_id).slug == "staging"  # type: ignore[union-attr]
        with pytest.raises(TeamNotFoundError):
            teams.resolve_target(Scope.everything(), team="acme", project="web", environment="qa")

    def test_a_project_with_no_default_must_be_named(self, teams: TeamStore) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        teams.delete_project("acme", DEFAULT_SLUG)
        teams.create_project("acme", "Api", slug="api")

        with pytest.raises(Exception) as raised:
            teams.resolve_target(Scope.everything(), team="acme")

        assert "several projects" in str(raised.value)

    def test_the_target_bound_to_a_block_is_where_new_rows_go(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")
        target = teams.resolve_target(Scope.everything(), team="acme")

        with ownership_target(target.project_id, target.environment_id):
            inside = make_app(store, "in.example.com")
        outside = make_app(store, "out.example.com")

        assert teams.project_by_id(inside.project_id or 0).team_id == acme.id  # type: ignore[union-attr]
        assert teams.project_by_id(outside.project_id or 0).team == DEFAULT_SLUG  # type: ignore[union-attr]


class TestMoving:
    def test_the_application_takes_its_sites_databases_and_schedule_with_it(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        acme = teams.create_team("Acme", slug="acme")
        app = make_app(store, "a.example.com")
        store.create_site(Site(app_id=app.id, domain="a.example.com", config_path="/x"))
        store.create_database(Database(app_id=app.id, name="a_db", engine="mysql"))
        store.create_database(Database(app_id=None, name="loose", engine="mysql"))
        store.save_backup_schedule(
            BackupScheduleRecord(app_domain="a.example.com", schedule="daily")
        )
        make_app(store, "other.example.com")

        result = teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        assert result.moved == {"apps": 1, "sites": 1, "databases": 1, "backup_schedules": 1}
        assert (result.team, result.project, result.environment) == (
            "acme",
            "default",
            "production",
        )
        for row in (
            store.get_app("a.example.com"),
            store.get_site("a.example.com"),
            store.get_database("a_db", "mysql"),
            store.get_backup_schedule("a.example.com"),
        ):
            assert row is not None
            assert teams.project_by_id(row.project_id or 0).team_id == acme.id  # type: ignore[union-attr]
        # What is not the application's stays where it was.
        for stays in (store.get_database("loose", "mysql"), store.get_app("other.example.com")):
            assert stays is not None
            assert teams.project_by_id(stays.project_id or 0).team == DEFAULT_SLUG  # type: ignore[union-attr]

    def test_the_previews_of_an_application_follow_it(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        make_app(store, "a.example.com")
        preview = make_app(store, "pr-1.a.example.com", preview_parent="a.example.com")

        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        moved = store.get_app("pr-1.a.example.com")
        assert moved is not None
        assert teams.project_by_id(moved.project_id or 0).team == "acme"  # type: ignore[union-attr]
        assert preview.id == moved.id

    def test_a_new_preview_is_born_where_its_application_lives(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        preview = make_app(store, "pr-2.a.example.com", preview_parent="a.example.com")

        assert teams.project_by_id(preview.project_id or 0).team == "acme"  # type: ignore[union-attr]

    def test_staying_in_the_project_stays_in_the_environment(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_environment("acme", DEFAULT_SLUG, "Staging")
        make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme", environment="staging")

        # Only the team is named, and it is the one the application is in.
        result = teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        assert result.environment == "staging"

    def test_a_move_to_another_project_lands_in_its_production(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        teams.create_environment("acme", "web", "Staging")
        make_app(store, "a.example.com")

        result = teams.move_app_to(Scope.everything(), "a.example.com", team="acme", project="web")

        assert (result.project, result.environment) == ("web", "production")

    def test_it_says_where_or_it_is_refused(self, store: NoustStore, teams: TeamStore) -> None:
        make_app(store, "a.example.com")

        with pytest.raises(ValidationError):
            teams.move_app_to(Scope.everything(), "a.example.com")

    def test_an_application_that_is_not_there_is_not_found(self, teams: TeamStore) -> None:
        with pytest.raises(Exception) as raised:
            teams.move_app_to(Scope.everything(), "ghost.example.com", team="default")

        assert "not found" in str(raised.value)

    def test_the_destination_may_be_refused_before_anything_moves(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        make_app(store, "a.example.com")

        def refuse(_target: object) -> None:
            raise PermissionError("no")

        with pytest.raises(PermissionError):
            teams.move_app_to(Scope.everything(), "a.example.com", team="acme", authorize=refuse)

        app = store.get_app("a.example.com")
        assert app is not None
        assert teams.project_by_id(app.project_id or 0).team == DEFAULT_SLUG  # type: ignore[union-attr]

    def test_a_full_row_write_of_a_deploy_does_not_undo_a_move(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        read_by_the_deploy = make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        read_by_the_deploy.status = "running"
        store.update_app(read_by_the_deploy)

        now = store.get_app("a.example.com")
        assert now is not None
        assert teams.project_by_id(now.project_id or 0).team == "acme"  # type: ignore[union-attr]
        assert now.status == "running"

    def test_the_site_and_database_writes_do_not_undo_a_move_either(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        app = make_app(store, "a.example.com")
        site = store.create_site(Site(app_id=app.id, domain="a.example.com", config_path="/x"))
        database = store.create_database(Database(app_id=app.id, name="a_db", engine="mysql"))
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        site.enabled = False
        database.port = 3307
        store.update_site(site)
        store.update_database(database)

        for row in (store.get_site("a.example.com"), store.get_database("a_db", "mysql")):
            assert row is not None
            assert teams.project_by_id(row.project_id or 0).team == "acme"  # type: ignore[union-attr]

    def test_linking_a_database_to_an_application_brings_it_home(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")
        store.create_database(Database(app_id=None, name="loose", engine="mysql"))

        store.link_database_to_app("loose", "mysql", "a.example.com")

        linked = store.get_database("loose", "mysql")
        assert linked is not None
        assert teams.project_by_id(linked.project_id or 0).team == "acme"  # type: ignore[union-attr]

    def test_linking_through_the_full_row_write_brings_it_home_too(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        app = make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")
        loose = store.create_database(Database(app_id=None, name="loose", engine="mysql"))

        loose.app_id = app.id
        store.update_database(loose)

        linked = store.get_database("loose", "mysql")
        assert linked is not None
        assert teams.project_by_id(linked.project_id or 0).team == "acme"  # type: ignore[union-attr]

    def test_a_site_that_gains_its_application_comes_home(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        # The web server records a site before the deploy registers the application.
        teams.create_team("Acme", slug="acme")
        app = make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")
        site = store.create_site(Site(app_id=None, domain="pr-3.example.com", config_path="/x"))
        assert teams.project_by_id(site.project_id or 0).team == DEFAULT_SLUG  # type: ignore[union-attr]

        site.app_id = app.id
        store.update_site(site)

        home = store.get_site("pr-3.example.com")
        assert home is not None
        assert teams.project_by_id(home.project_id or 0).team == "acme"  # type: ignore[union-attr]

    def test_a_site_created_for_an_application_is_born_with_it(
        self, store: NoustStore, teams: TeamStore
    ) -> None:
        teams.create_team("Acme", slug="acme")
        app = make_app(store, "a.example.com")
        teams.move_app_to(Scope.everything(), "a.example.com", team="acme")

        site = store.create_site(Site(app_id=app.id, domain="x.example.com", config_path="/x"))

        assert teams.project_by_id(site.project_id or 0).team == "acme"  # type: ignore[union-attr]
