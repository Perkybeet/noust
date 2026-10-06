# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust team``, ``noust project``, ``noust environment`` and ``noust app move``.

They are a front end over the same :class:`~noust.core.teams.TeamStore` the
console's team screens call, so what is made here is what the console shows;
what these tests pin is the surface an operator types: the commands, what they
print and refuse, and that a list can be narrowed to a team, a project and an
environment.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands.team_common import placed_in_a_project
from noust.core.accounts import AccountManager, passwords
from noust.core.store import App, Database, NoustStore, Site
from noust.core.teams import DEFAULT_SLUG, Scope, TeamStore
from noust.web.auth import STATE_DIR_ENV

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The console's state (and the audit trail) inside the test's directory."""
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def cli() -> CliRunner:
    """A Click runner."""
    return CliRunner()


def run(cli: CliRunner, *args: str, code: int = 0, stdin: str | None = None) -> str:
    """
    Run a command and check how it ended.

    Args:
        cli: The runner.
        *args: The command line after ``noust``.
        code: The exit code expected.
        stdin: What to pipe in.

    Returns:
        Its output.
    """
    result = cli.invoke(root_cli, list(args), input=stdin)
    assert result.exit_code == code, f"{result.output} {result.exception}"
    # A refusal is the error the command raised: the root group prints it, the runner holds it.
    return f"{result.output}{result.exception or ''}"


def as_json(cli: CliRunner, *args: str) -> dict:
    """
    Args:
        cli: The runner.
        *args: The command line after ``noust``.

    Returns:
        The JSON the command printed.
    """
    return json.loads(run(cli, "--json", *args))


class TestTeams:
    def test_a_server_starts_with_the_default_team(self, cli: CliRunner) -> None:
        body = as_json(cli, "team", "list")

        assert [t["slug"] for t in body["teams"]] == [DEFAULT_SLUG]

    def test_create_makes_a_team_with_its_project(self, cli: CliRunner, store: NoustStore) -> None:
        output = run(cli, "team", "create", "Acme Corp")

        assert "Team created: acme-corp (Acme Corp)" in output
        teams = TeamStore(store)
        assert [p.slug for p in teams.list_projects(team="acme-corp")] == ["default"]

    def test_a_slug_may_be_chosen(self, cli: CliRunner) -> None:
        run(cli, "team", "create", "Acme Corp", "--slug", "acme")

        assert {t["slug"] for t in as_json(cli, "team", "list")["teams"]} == {"default", "acme"}

    def test_a_taken_slug_is_refused_with_the_way_out(self, cli: CliRunner) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")

        output = run(cli, "team", "create", "Acme", "--slug", "acme", code=1)

        assert "already exists" in output

    def test_rename_and_delete(self, cli: CliRunner, store: NoustStore) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")

        run(cli, "team", "rename", "acme", "Acme Inc")
        run(cli, "team", "delete", "acme", "--yes")

        assert TeamStore(store).get_team("acme") is None

    def test_the_default_team_is_not_deleted(self, cli: CliRunner) -> None:
        output = run(cli, "team", "delete", "default", "--yes", code=1)

        assert "default team cannot be deleted" in output

    def test_a_team_that_holds_an_application_is_not_deleted(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")
        store.create_app(App(domain="a.example.com", app_path="/srv/a"))
        run(cli, "app", "move", "a.example.com", "--team", "acme")

        output = run(cli, "team", "delete", "acme", "--yes", code=1)

        assert "still holds 1 application" in output

    def test_members_are_added_listed_and_removed(self, cli: CliRunner, store: NoustStore) -> None:
        AccountManager(store).create("maria", "viewer", password=PASSWORD)
        run(cli, "team", "create", "Acme", "--slug", "acme")

        added = run(cli, "team", "add-member", "acme", "maria", "--role", "operator")
        members = as_json(cli, "team", "members", "acme")["members"]
        run(cli, "team", "remove-member", "acme", "maria")

        assert "maria is operator of the team acme" in added
        assert [(m["username"], m["role"]) for m in members] == [("maria", "operator")]
        assert as_json(cli, "team", "members", "acme")["members"] == []

    def test_an_auditor_is_not_a_member(self, cli: CliRunner, store: NoustStore) -> None:
        AccountManager(store).create("aurora", "auditor", password=PASSWORD)
        run(cli, "team", "create", "Acme", "--slug", "acme")

        output = run(cli, "team", "add-member", "acme", "aurora", code=1)

        assert "auditor account" in output

    def test_the_last_owner_stays(self, cli: CliRunner, store: NoustStore) -> None:
        AccountManager(store).create("maria", "viewer", password=PASSWORD)
        run(cli, "team", "create", "Acme", "--slug", "acme")
        run(cli, "team", "add-member", "acme", "maria", "--role", "owner")

        output = run(cli, "team", "remove-member", "acme", "maria", code=1)

        assert "last owner" in output

    def test_a_person_is_invited_into_a_team(self, cli: CliRunner, store: NoustStore) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")

        output = run(
            cli,
            "user",
            "invite",
            "maria",
            "--role",
            "viewer",
            "--team",
            "acme",
            "--team-role",
            "admin",
        )

        assert "Invitation issued for maria" in output
        teams = TeamStore(store)
        account = AccountManager(store).require("maria")
        assert list(teams.memberships_of(account.id).values()) == ["admin"]
        assert teams.require_team("acme").id in teams.memberships_of(account.id)

    def test_a_person_is_created_in_a_team(self, cli: CliRunner, store: NoustStore) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")

        run(
            cli,
            "user",
            "create",
            "maria",
            "--role",
            "viewer",
            "--team",
            "acme",
            "--stdin",
            stdin=PASSWORD + "\n",
        )

        teams = TeamStore(store)
        account = AccountManager(store).require("maria")
        assert teams.memberships_of(account.id) == {teams.require_team("acme").id: "viewer"}

    def test_a_person_created_the_old_way_is_in_the_default_team(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        run(cli, "user", "create", "maria", "--role", "operator", "--stdin", stdin=PASSWORD + "\n")

        teams = TeamStore(store)
        account = AccountManager(store).require("maria")
        assert teams.memberships_of(account.id) == {teams.require_team(DEFAULT_SLUG).id: "operator"}


class TestProjectsAndEnvironments:
    def test_a_project_is_made_in_a_team_with_production(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")

        output = run(cli, "project", "create", "Mobile app", "--team", "acme")

        assert "Project created: acme/mobile-app (environment: production)" in output
        listed = as_json(cli, "project", "list", "--team", "acme")["projects"]
        assert {(p["slug"], tuple(p["environments"])) for p in listed} == {
            ("default", ("production",)),
            ("mobile-app", ("production",)),
        }

    def test_without_a_team_the_default_team_is_meant(self, cli: CliRunner) -> None:
        run(cli, "project", "create", "Web")

        assert {(p["team"], p["slug"]) for p in as_json(cli, "project", "list")["projects"]} == {
            ("default", "default"),
            ("default", "web"),
        }

    def test_an_environment_is_made_listed_and_deleted(self, cli: CliRunner) -> None:
        run(cli, "environment", "create", "Staging")

        listed = as_json(cli, "environment", "list")["environments"]
        run(cli, "environment", "delete", "staging", "--yes")

        assert {e["slug"] for e in listed} == {"production", "staging"}
        assert {e["slug"] for e in as_json(cli, "environment", "list")["environments"]} == {
            "production"
        }

    def test_a_project_and_an_environment_are_renamed(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")
        run(cli, "project", "create", "Mobile", "--team", "acme")
        run(cli, "environment", "create", "Staging", "--team", "acme", "--project", "mobile")

        run(cli, "project", "rename", "mobile", "Mobile app", "--team", "acme")
        run(
            cli,
            "environment",
            "rename",
            "staging",
            "Pre-production",
            "--team",
            "acme",
            "--project",
            "mobile",
        )

        teams = TeamStore(store)
        assert teams.require_project("acme", "mobile").name == "Mobile app"
        assert teams.require_environment("acme", "mobile", "staging").name == "Pre-production"

    def test_the_last_environment_stays(self, cli: CliRunner) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")

        output = run(cli, "environment", "delete", "production", "--team", "acme", "--yes", code=1)

        assert "only environment" in output

    def test_a_project_that_holds_something_is_not_deleted(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        run(cli, "team", "create", "Acme", "--slug", "acme")
        run(cli, "project", "create", "Web", "--team", "acme")
        store.create_app(App(domain="a.example.com", app_path="/srv/a"))
        run(cli, "app", "move", "a.example.com", "--team", "acme", "--project", "web")

        output = run(cli, "project", "delete", "web", "--team", "acme", "--yes", code=1)

        assert "still holds 1 application" in output

    def test_an_unknown_team_is_not_found(self, cli: CliRunner) -> None:
        output = run(cli, "project", "list", "--team", "nowhere", code=1)

        assert "No team named 'nowhere'" in output


class TestMovingAnApplication:
    def make(self, store: NoustStore) -> None:
        app = store.create_app(App(domain="a.example.com", app_path="/srv/a"))
        store.create_site(Site(app_id=app.id, domain="a.example.com", config_path="/x"))
        store.create_database(Database(app_id=app.id, name="a_db", engine="mysql"))

    def test_it_takes_what_belongs_to_it(self, cli: CliRunner, store: NoustStore) -> None:
        self.make(store)
        run(cli, "team", "create", "Acme", "--slug", "acme")
        run(cli, "project", "create", "Web", "--team", "acme")
        run(cli, "environment", "create", "Staging", "--team", "acme", "--project", "web")

        output = run(
            cli,
            "app",
            "move",
            "a.example.com",
            "--team",
            "acme",
            "--project",
            "web",
            "--env",
            "staging",
        )

        assert "a.example.com moved to acme/web/staging" in output
        teams = TeamStore(store)
        for row in (
            store.get_app("a.example.com"),
            store.get_site("a.example.com"),
            store.get_database("a_db", "mysql"),
        ):
            assert row is not None
            assert teams.owner_labels().of(row.project_id, row.environment_id) == (
                "acme",
                "web",
                "staging",
            )

    def test_it_says_what_moved_as_json(self, cli: CliRunner, store: NoustStore) -> None:
        self.make(store)
        run(cli, "team", "create", "Acme", "--slug", "acme")

        body = as_json(cli, "app", "move", "a.example.com", "--team", "acme")

        assert body["moved"] == {"apps": 1, "sites": 1, "databases": 1, "backup_schedules": 0}
        assert (body["team"], body["project"], body["environment"]) == (
            "acme",
            "default",
            "production",
        )

    def test_it_refuses_a_move_that_goes_nowhere(self, cli: CliRunner, store: NoustStore) -> None:
        self.make(store)

        output = run(cli, "app", "move", "a.example.com", code=1)

        assert "Say where" in output

    def test_it_refuses_an_unknown_application_and_an_unknown_place(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        self.make(store)

        assert "not found" in run(
            cli, "app", "move", "ghost.example.com", "--team", "default", code=1
        )
        assert "No team named" in run(cli, "app", "move", "a.example.com", "--team", "nope", code=1)
        assert "no environment named" in run(
            cli, "app", "move", "a.example.com", "--env", "qa", code=1
        )


class TestListsCanBeNarrowed:
    def arrange(self, store: NoustStore) -> None:
        teams = TeamStore(store)
        for slug in ("acme", "globex"):
            teams.create_team(slug.title(), slug=slug)
            target = teams.resolve_target(Scope.everything(), team=slug)
            domain = f"{slug}.example.com"
            app = store.create_app(
                App(
                    domain=domain,
                    app_path=f"/srv/{slug}",
                    project_id=target.project_id,
                    environment_id=target.environment_id,
                )
            )
            store.create_site(Site(app_id=app.id, domain=domain, config_path=f"/etc/{slug}"))
            store.create_database(Database(app_id=app.id, name=f"{slug}_db", engine="mysql"))

    def test_the_application_list_follows_the_team_project_and_environment(
        self, cli: CliRunner, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.arrange(store)
        from noust.cli.commands import webapp
        from noust.core.app_state import AppState

        monkeypatch.setattr(
            webapp,
            "resolve_states",
            lambda apps, manager: {a.domain: AppState("Running", True, None) for a in apps},
        )

        every = as_json(cli, "list")["items"]
        acme = as_json(cli, "list", "--team", "acme")["items"]
        none = as_json(cli, "list", "--project", "nope")["items"]
        staged = as_json(cli, "list", "--env", "staging")["items"]

        assert {i["domain"] for i in every} == {"acme.example.com", "globex.example.com"}
        assert [i["domain"] for i in acme] == ["acme.example.com"]
        assert acme[0]["team"] == "acme"
        assert none == [] and staged == []

    def test_a_server_nobody_organised_lists_as_it_always_did(
        self, cli: CliRunner, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.create_app(App(domain="a.example.com", app_path="/srv/a"))
        from noust.cli.commands import webapp
        from noust.core.app_state import AppState

        monkeypatch.setattr(
            webapp,
            "resolve_states",
            lambda apps, manager: {a.domain: AppState("Running", True, None) for a in apps},
        )

        item = as_json(cli, "list")["items"][0]

        assert "team" not in item and "project" not in item

    def test_the_site_database_and_schedule_lists_follow_too(
        self, cli: CliRunner, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.arrange(store)
        from noust.cli.commands import db as db_commands
        from noust.cli.commands import site as site_commands
        from noust.managers.database.service import DatabaseListing, DatabaseService, DatabaseView

        monkeypatch.setattr(
            site_commands.NginxManager,
            "is_installed",
            lambda self: True,
        )
        monkeypatch.setattr(
            site_commands.NginxManager,
            "list_sites",
            lambda self: [
                {"domain": "acme.example.com", "enabled": True, "config_path": "/a"},
                {"domain": "globex.example.com", "enabled": True, "config_path": "/g"},
            ],
        )
        monkeypatch.setattr(site_commands.ApacheManager, "is_installed", lambda self: False)
        monkeypatch.setattr(
            DatabaseService,
            "listing",
            lambda self, engine=None: DatabaseListing(
                [
                    DatabaseView(name="acme_db", engine="mysql", tracked=True),
                    DatabaseView(name="globex_db", engine="mysql", tracked=True),
                ],
                [],
            ),
        )

        sites = as_json(cli, "site", "list", "--team", "globex")
        databases = json.loads(run(cli, "--json", "db", "list", "--team", "acme"))

        assert [s["name"] for s in sites["sites"]] == ["globex.example.com"]
        assert [d["name"] for d in databases] == ["acme_db"]
        assert db_commands is not None


class TestCreatingNamesWhereItGoes:
    """``noust create`` runs the deploy inside the project it is told, and says nothing else."""

    def command(self, store: NoustStore) -> click.Command:

        @click.command()
        @click.option("--domain", required=True)
        @placed_in_a_project
        def create(domain: str) -> None:
            # What a deployer does in the middle of a deploy: record the application.
            store.create_app(App(domain=domain, app_path=f"/srv/{domain}"))

        return create

    def test_what_is_created_belongs_to_the_project_asked_for(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        teams = TeamStore(store)
        acme = teams.create_team("Acme", slug="acme")
        teams.create_project("acme", "Web", slug="web")
        teams.create_environment("acme", "web", "Staging")
        command = self.command(store)

        result = cli.invoke(
            command,
            [
                "--domain",
                "a.example.com",
                "--team",
                "acme",
                "--project",
                "web",
                "--environment",
                "staging",
            ],
        )

        assert result.exit_code == 0, result.output
        app = store.get_app("a.example.com")
        assert app is not None
        assert teams.owner_labels().of(app.project_id, app.environment_id) == (
            "acme",
            "web",
            "staging",
        )
        assert teams.project_by_id(app.project_id or 0).team_id == acme.id  # type: ignore[union-attr]

    def test_without_them_it_goes_where_it_always_did(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        command = self.command(store)

        result = cli.invoke(command, ["--domain", "a.example.com"])

        assert result.exit_code == 0, result.output
        app = store.get_app("a.example.com")
        teams = TeamStore(store)
        assert app is not None
        assert teams.owner_labels().of(app.project_id, app.environment_id) == (
            "default",
            "default",
            "production",
        )

    def test_a_place_that_is_not_there_stops_it_before_anything_is_created(
        self, cli: CliRunner, store: NoustStore
    ) -> None:
        command = self.command(store)

        result = cli.invoke(command, ["--domain", "a.example.com", "--team", "nowhere"])

        assert result.exit_code != 0
        assert store.get_app("a.example.com") is None

    def test_noust_create_offers_them(self, cli: CliRunner) -> None:
        output = run(cli, "create", "--help")

        assert "--team" in output and "--project" in output and "--environment" in output
