# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What 3.4 added after teams holds the scope too: deploy keys, an application's
https credential, databases open to the network, the engine on offer, an
application's build block, container settings and recipe, and the backups.

``tests/test_cross_tenant.py`` pins the promise for applications, sites,
databases and their history; the surfaces here came from other areas of the
same release, and each one names applications or databases of its own:

- a deploy key is found by repository, so one key can reach the applications of
  several teams. A team member is shown the keys of its own repositories, each
  limited to them, cannot assign a key it was never shown, and cannot rotate
  one that also serves a repository outside its teams;
- what the server's ``known_hosts`` has pinned and opening a database to the
  network are the host's, whoever's database it is;
- installing an engine as part of creating a database is a package install as
  root, so it is the server's too, though the rest of that route is a team's;
- the build block, the container settings and the recipe an application came
  from are reached through the application, so another team's reads as absent;
- volume backups, database backup policies, database imports and Noust's own
  backup each write a root timer or load data into an engine, and name
  destinations the server owns: they are the server's, for every team.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.app_build_store import get_document, set_document
from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.database_exposures import ExposureStore
from noust.core.deploy_keys import DeployKeyStore
from noust.core.exceptions import ValidationError
from noust.core.repositories import repository_identity
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore
from noust.managers.database.public_access import PublicAccess
from noust.managers.database.service import DatabaseService
from noust.recipes import get_recipe
from noust.recipes.installed import record_install
from noust.web.api.openapi import api_routes
from tests.deploy_key_support import DeployKeyRunner
from tests.fleet_support import ed25519_line, fingerprint
from tests.teams_support import World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"
ACME_SOURCE = "git@github.com:acme/shop.git"
GLOBEX_SOURCE = "git@github.com:globex/blog.git"
ACME_REPOSITORY = repository_identity(ACME_SOURCE) or ""
GLOBEX_REPOSITORY = repository_identity(GLOBEX_SOURCE) or ""
#: A second application of each team, whose repository one machine key reaches.
ACME_API = "api.acme.example"
GLOBEX_WEB = "web.globex.example"
ACME_API_REPOSITORY = repository_identity("git@github.com:acme/api.git") or ""
GLOBEX_WEB_REPOSITORY = repository_identity("git@github.com:globex/web.git") or ""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    """Two teams, their resources and their people, served by the console."""
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def set_source(world: World, domain: str, source: str) -> None:
    """
    Build an application from a repository.

    Args:
        world: The world.
        domain: The application.
        source: Its git URL.
    """
    app = world.store.get_app(domain)
    assert app is not None
    app.source = source
    world.store.update_app(app)


def add_key(world: World, name: str, seed: int, *repositories: str) -> None:
    """
    Record a deploy key that reaches some repositories.

    Args:
        world: The world.
        name: The key's name.
        seed: Which key it is.
        *repositories: The repositories it is assigned to.
    """
    line = ed25519_line(seed, name)
    keys = DeployKeyStore(world.store)
    keys.create(
        name=name,
        origin="generated",
        key_type="ssh-ed25519",
        fingerprint=fingerprint(line),
        public_key=line,
        created_by="test",
    )
    for repository in repositories:
        keys.assign(name, repository)


def add_app(world: World, domain: str, team: str, source: str) -> None:
    """
    Record an application of a team, built from a repository.

    Args:
        world: The world.
        domain: Its domain.
        team: The team it lives in.
        source: Its git URL.
    """
    target = world.targets[team]
    world.store.create_app(
        App(
            domain=domain,
            app_path=f"/srv/{domain}",
            source=source,
            project_id=target.project_id,
            environment_id=target.environment_id,
        )
    )


@pytest.fixture
def keyed(world: World) -> World:
    """Each team builds from its own repository, and has a key for it."""
    set_source(world, ACME, ACME_SOURCE)
    set_source(world, GLOBEX, GLOBEX_SOURCE)
    add_key(world, "acme-key", 1, ACME_REPOSITORY)
    add_key(world, "globex-key", 2, GLOBEX_REPOSITORY)
    return world


@pytest.fixture
def shared(keyed: World) -> World:
    """One machine key reaches a repository of each team."""
    add_app(keyed, ACME_API, "acme", "git@github.com:acme/api.git")
    add_app(keyed, GLOBEX_WEB, "globex", "git@github.com:globex/web.git")
    add_key(keyed, "ci-bot", 3, ACME_API_REPOSITORY, GLOBEX_WEB_REPOSITORY)
    return keyed


class TestDeployKeysAreShownByTheRepositoriesOfTheCallersApplications:
    def test_a_member_lists_the_keys_that_reach_its_applications_and_no_others(
        self, shared: World
    ) -> None:
        def names(who: dict[str, str]) -> set[str]:
            body = shared.client.get("/api/deploy-keys", headers=who).json()
            return {key["name"] for key in body["keys"]}

        assert names(shared.as_("alice")) == {"acme-key", "ci-bot"}
        assert names(shared.as_("bob")) == {"globex-key", "ci-bot"}
        assert names(shared.as_("admin")) == {"acme-key", "globex-key", "ci-bot"}
        assert names(shared.master) == {"acme-key", "globex-key", "ci-bot"}

    def test_a_key_reaching_two_teams_names_only_the_callers_side_of_it(
        self, shared: World
    ) -> None:
        seen = shared.client.get("/api/deploy-keys", headers=shared.as_("alice")).json()
        bot = next(key for key in seen["keys"] if key["name"] == "ci-bot")
        everything = shared.client.get("/api/deploy-keys", headers=shared.as_("admin")).json()
        whole = next(key for key in everything["keys"] if key["name"] == "ci-bot")

        assert bot["repositories"] == [ACME_API_REPOSITORY]
        assert bot["applications"] == [ACME_API]
        assert whole["repositories"] == sorted([ACME_API_REPOSITORY, GLOBEX_WEB_REPOSITORY])
        assert whole["applications"] == sorted([ACME_API, GLOBEX_WEB])

    def test_an_application_shows_its_key_limited_the_same_way(self, shared: World) -> None:
        view = shared.client.get(f"/api/apps/{ACME_API}/deploy-key", headers=shared.as_("alice"))

        assert view.status_code == 200, view.text
        key = view.json()["key"]
        assert key["name"] == "ci-bot"
        assert key["repositories"] == [ACME_API_REPOSITORY]
        assert key["applications"] == [ACME_API]

    def test_the_total_counts_only_what_the_caller_sees(self, keyed: World) -> None:
        body = keyed.client.get("/api/deploy-keys", headers=keyed.as_("alice")).json()

        assert body["total"] == len(body["keys"]) == 1

    def test_a_member_in_no_team_sees_no_key(self, keyed: World) -> None:
        keyed.teams.remove_member("acme", "dana")

        body = keyed.client.get("/api/deploy-keys", headers=keyed.as_("dana")).json()

        assert body == {"keys": [], "total": 0}

    def test_an_application_does_not_name_the_other_teams_that_share_its_repository(
        self, keyed: World
    ) -> None:
        # The key is the repository's, so a second team building from the same URL shares it.
        # Which applications those are is not for the first team to read.
        keyed.store.create_app(
            App(
                domain="mirror.globex.example",
                app_path="/srv/mirror",
                source=ACME_SOURCE,
                project_id=keyed.targets["globex"].project_id,
                environment_id=keyed.targets["globex"].environment_id,
            )
        )

        mine = keyed.client.get(f"/api/apps/{ACME}/deploy-key", headers=keyed.as_("alice")).json()
        whole = keyed.client.get(f"/api/apps/{ACME}/deploy-key", headers=keyed.as_("admin")).json()

        assert mine["shared_with"] == []
        assert whole["shared_with"] == ["mirror.globex.example"]
        assert mine["key"]["name"] == whole["key"]["name"] == "acme-key"
        assert mine["key"]["applications"] == [ACME]
        assert whole["key"]["applications"] == sorted([ACME, "mirror.globex.example"])


class TestAnotherTeamsApplicationHasNoDeployKeyOrCredentialToTouch:
    @pytest.mark.parametrize(
        ("method", "suffix", "body"),
        [
            ("GET", "deploy-key", None),
            ("POST", "deploy-key", {}),
            ("POST", "deploy-key/import", {"private_key": "x"}),
            ("DELETE", "deploy-key", None),
            ("POST", "deploy-key/rotate", None),
            ("DELETE", "deploy-key/rotate", None),
            ("POST", "deploy-key/test", None),
            ("GET", "git-credential", None),
            ("PUT", "git-credential", {"username": "u", "secret": "s"}),
            ("DELETE", "git-credential", None),
        ],
    )
    def test_it_is_a_404_and_changes_nothing(
        self, keyed: World, method: str, suffix: str, body: Any
    ) -> None:
        before = DeployKeyStore(keyed.store).keys()

        response = keyed.client.request(
            method, f"/api/apps/{GLOBEX}/{suffix}", json=body, headers=keyed.as_("alice")
        )

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"
        assert DeployKeyStore(keyed.store).keys() == before

    def test_its_own_application_answers_it(self, keyed: World) -> None:
        response = keyed.client.get(f"/api/apps/{ACME}/deploy-key", headers=keyed.as_("alice"))

        assert response.status_code == 200, response.text
        assert response.json()["key"]["name"] == "acme-key"

    def test_the_manager_refuses_an_application_it_may_not_see_even_without_the_router(
        self, keyed: World
    ) -> None:
        # Rule 4: the guard is where the application is read, not only in the route above it.
        from noust.core.teams import bind
        from noust.managers.source_credentials import GitCredentialManager
        from noust.managers.source_keys import DeployKeyManager

        scope = keyed.teams.scope_for(_account_id(keyed, "alice"), "viewer")
        with bind(scope):
            with pytest.raises(ValidationError, match="Application not found"):
                DeployKeyManager(store=keyed.store).view(GLOBEX)
            with pytest.raises(ValidationError, match="Application not found"):
                GitCredentialManager(store=keyed.store).view(GLOBEX)
            assert DeployKeyManager(store=keyed.store).view(ACME).domain == ACME


def _account_id(world: World, username: str) -> int:
    """
    Args:
        world: The world.
        username: An account.

    Returns:
        Its id.
    """
    with world.store._transaction() as cursor:
        cursor.execute("SELECT id FROM accounts WHERE username = ?", (username,))
        return int(cursor.fetchone()[0])


class TestAssigningAKeyNeedsToHaveBeenShownIt:
    @pytest.fixture
    def spare(self, keyed: World) -> World:
        """An application of acme with a repository nothing reaches yet."""
        keyed.store.create_app(
            App(
                domain="api.acme.example",
                app_path="/srv/api",
                source="git@github.com:acme/api.git",
                project_id=keyed.targets["acme"].project_id,
                environment_id=keyed.targets["acme"].environment_id,
            )
        )
        return keyed

    def test_another_teams_key_cannot_be_assigned_by_naming_it(self, spare: World) -> None:
        response = spare.client.post(
            "/api/apps/api.acme.example/deploy-key",
            json={"key": "globex-key"},
            headers=spare.as_("alice"),
        )

        assert response.status_code == 400, response.text
        assert "no deploy key named globex-key" in response.text
        assert DeployKeyStore(spare.store).get("globex-key").repositories == (  # type: ignore[union-attr]
            GLOBEX_REPOSITORY,
        )

    def test_the_answer_is_the_one_for_a_key_that_does_not_exist(self, spare: World) -> None:
        hidden = spare.client.post(
            "/api/apps/api.acme.example/deploy-key",
            json={"key": "globex-key"},
            headers=spare.as_("alice"),
        )
        missing = spare.client.post(
            "/api/apps/api.acme.example/deploy-key",
            json={"key": "nobody-made-this"},
            headers=spare.as_("alice"),
        )

        assert hidden.status_code == missing.status_code == 400
        assert hidden.json()["error"] == missing.json()["error"]

    def test_a_key_of_the_callers_own_repositories_can_be_assigned(self, spare: World) -> None:
        response = spare.client.post(
            "/api/apps/api.acme.example/deploy-key",
            json={"key": "acme-key"},
            headers=spare.as_("alice"),
        )

        assert response.status_code == 201, response.text
        assert response.json()["key"]["name"] == "acme-key"

    def test_an_administrator_assigns_any_key(self, spare: World) -> None:
        response = spare.client.post(
            "/api/apps/api.acme.example/deploy-key",
            json={"key": "globex-key"},
            headers=spare.as_("admin"),
        )

        assert response.status_code == 201, response.text


class TestWhatTheServerTrustsIsTheServers:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/deploy-keys/hosts"),
            ("DELETE", "/api/deploy-keys/hosts/github.com"),
        ],
    )
    def test_a_team_member_is_refused(self, keyed: World, method: str, path: str) -> None:
        response = keyed.client.request(method, path, headers=keyed.as_("alice"))

        # Reading is refused for being the server's; forgetting a host key also needs a
        # permission no team role holds, and that is what the route answers first.
        assert response.status_code == 403, response.text
        assert response.json()["error"] in {"instance_scope_required", "permission_denied"}

    def test_reading_them_says_why(self, keyed: World) -> None:
        response = keyed.client.get("/api/deploy-keys/hosts", headers=keyed.as_("alice"))

        assert response.json()["error"] == "instance_scope_required"

    def test_an_administrator_is_not_refused(self, keyed: World) -> None:
        response = keyed.client.get("/api/deploy-keys/hosts", headers=keyed.as_("admin"))

        assert response.status_code != 403, response.text


@pytest.fixture
def open_databases(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """One database of each team open to the network, with the host's side stubbed."""
    exposures = ExposureStore(world.store)
    for index, (database, user) in enumerate((("shop_db", "acme_r"), ("blog_db", "globex_r"))):
        exposures.create(
            engine="postgresql",
            db_name=database,
            port=15432 + index,
            bind_address="0.0.0.0",  # noqa: S104 - a record the test reads, nothing listens
            sources=("any",),
            tls="required",
            remote_user=user,
            profile="owner",
            unit=f"noust-db-public-{database}",
            created_by="test",
        )
    monkeypatch.setattr(PublicAccess, "listening", lambda self: set())
    monkeypatch.setattr(
        PublicAccess,
        "connection",
        lambda self, record, password: {
            "host": "db.example",
            "port": record.port,
            "database": record.db_name,
            "username": record.remote_user,
            "password": password,
            "tls": record.tls,
            "url": f"postgresql://{record.remote_user}@db.example/{record.db_name}",
            "options": {},
        },
    )
    monkeypatch.setattr(
        PublicAccess,
        "make_private",
        lambda self, engine, database: SimpleNamespace(
            engine=engine, database=database, removed=["firewall rule"], already_private=False
        ),
    )
    monkeypatch.setattr(
        PublicAccess,
        "reveal",
        lambda self, engine, database: {
            "host": "db.example",
            "port": 15432,
            "database": database,
            "username": "acme_r",
            "password": "shown-once",
            "tls": "required",
            "url": "postgresql://acme_r:shown-once@db.example/shop_db",
            "options": {},
        },
    )
    return world


class TestADatabaseOpenToTheNetworkIsItsOwnersToSee:
    def test_a_member_lists_the_open_databases_of_its_teams_only(
        self, open_databases: World
    ) -> None:
        def seen(who: dict[str, str]) -> set[str]:
            body = open_databases.client.get("/api/databases/public", headers=who).json()
            assert body["total"] == len(body["exposures"])
            return {item["database"] for item in body["exposures"]}

        assert seen(open_databases.as_("alice")) == {"shop_db"}
        assert seen(open_databases.as_("bob")) == {"blog_db"}
        assert seen(open_databases.as_("admin")) == {"shop_db", "blog_db"}
        assert seen(open_databases.as_("aud")) == {"shop_db", "blog_db"}

    def test_another_teams_database_reads_as_absent(self, open_databases: World) -> None:
        for method, path in (
            ("GET", "/api/databases/public/postgresql/blog_db"),
            ("POST", "/api/databases/public/postgresql/blog_db/reveal"),
            ("DELETE", "/api/databases/public/postgresql/blog_db"),
        ):
            response = open_databases.client.request(
                method, path, headers=open_databases.as_("alice")
            )

            assert response.status_code == 404, (method, response.text)
            assert response.json()["error"] == "not_found"
        assert ExposureStore(open_databases.store).get("postgresql", "blog_db") is not None

    def test_a_member_reads_how_to_connect_to_its_own(self, open_databases: World) -> None:
        response = open_databases.client.get(
            "/api/databases/public/postgresql/shop_db", headers=open_databases.as_("alice")
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["public"] is True
        assert body["connection"]["password"] is None

    def test_an_admin_of_the_team_reveals_and_closes_its_own(self, open_databases: World) -> None:
        revealed = open_databases.client.post(
            "/api/databases/public/postgresql/shop_db/reveal",
            headers=open_databases.as_("alice"),
        )
        closed = open_databases.client.delete(
            "/api/databases/public/postgresql/shop_db", headers=open_databases.as_("alice")
        )

        assert revealed.status_code == 200, revealed.text
        assert revealed.json()["password"] == "shown-once"
        assert closed.status_code == 200, closed.text
        assert closed.json()["removed"] == ["firewall rule"]

    def test_a_viewer_of_the_team_does_not_reveal_or_close_it(self, open_databases: World) -> None:
        for method, path in (
            ("POST", "/api/databases/public/postgresql/shop_db/reveal"),
            ("DELETE", "/api/databases/public/postgresql/shop_db"),
        ):
            response = open_databases.client.request(
                method, path, headers=open_databases.as_("dana")
            )

            assert response.status_code == 403, (method, response.text)
            assert response.json()["error"] != "instance_scope_required"

    @pytest.mark.parametrize(
        ("path", "body"),
        [
            (
                "/api/databases/public",
                {"engine": "postgresql", "database": "shop_db", "confirm": True},
            ),
            ("/api/databases/public/plan", {"engine": "postgresql", "database": "shop_db"}),
        ],
    )
    def test_opening_one_is_the_servers_even_for_the_owner_of_the_database(
        self, open_databases: World, path: str, body: dict[str, Any]
    ) -> None:
        # It opens a port, makes an account on the engine and edits the firewall.
        response = open_databases.client.post(path, json=body, headers=open_databases.as_("alice"))

        assert response.status_code == 403, response.text
        assert response.json()["error"] == "instance_scope_required"


class TestCreatingADatabaseCannotInstallAnEngineForATeam:
    @pytest.fixture
    def stubbed(self, world: World, monkeypatch: pytest.MonkeyPatch) -> World:
        """Stop the route where it would ask the engines, which are not here."""

        def choose(self: DatabaseService, engine: str, **kwargs: Any) -> Any:
            raise ValidationError("stop here", field="engine")

        monkeypatch.setattr(DatabaseService, "resolve_engine_choice", choose)
        return world

    def test_install_is_refused_to_a_team_admin_with_the_reason(self, stubbed: World) -> None:
        response = stubbed.client.post(
            f"/api/apps/{ACME}/databases",
            json={"engine": "postgresql", "install": True},
            headers=stubbed.as_("alice"),
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"] == "instance_scope_required"

    def test_the_same_request_without_install_is_a_team_admins_to_make(
        self, stubbed: World
    ) -> None:
        response = stubbed.client.post(
            f"/api/apps/{ACME}/databases",
            json={"engine": "postgresql"},
            headers=stubbed.as_("alice"),
        )

        # It got as far as choosing the engine, which this test stopped.
        assert response.status_code == 400, response.text
        assert "stop here" in response.text

    def test_an_administrator_is_not_refused_by_the_scope(self, stubbed: World) -> None:
        response = stubbed.client.post(
            f"/api/apps/{ACME}/databases",
            json={"engine": "postgresql", "install": True},
            headers=stubbed.as_("admin"),
        )

        assert response.json().get("error") != "instance_scope_required", response.text

    def test_another_teams_application_is_absent_whatever_is_asked(self, stubbed: World) -> None:
        response = stubbed.client.post(
            f"/api/apps/{GLOBEX}/databases",
            json={"engine": "postgresql", "install": True},
            headers=stubbed.as_("alice"),
        )

        assert response.status_code == 404, response.text


class TestTheEnginesOnOfferAreACatalogue:
    def test_a_team_member_reads_them(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(DatabaseService, "engine_choices", lambda self: [])

        response = world.client.get(
            "/api/databases/provisioning/choices", headers=world.as_("alice")
        )

        assert response.status_code == 200, response.text
        assert response.json() == {"choices": []}


@pytest.fixture
def keygen(world: World) -> DeployKeyRunner:
    """The runner ssh-keygen goes through, so a rotation can make its new key."""
    stand_in = DeployKeyRunner()
    set_runner(stand_in)
    return stand_in


class TestAKeyAnotherTeamAlsoUsesIsNotRotatedByOne:
    """Rotating replaces the key for every repository it reaches, so it is not one team's call."""

    def test_a_member_cannot_start_a_rotation_of_a_key_that_reaches_another_teams_repository(
        self, shared: World, keygen: DeployKeyRunner
    ) -> None:
        response = shared.client.post(
            f"/api/apps/{ACME_API}/deploy-key/rotate", headers=shared.as_("alice")
        )

        assert response.status_code == 400, response.text
        assert "outside your teams" in response.text
        held = DeployKeyStore(shared.store).get("ci-bot")
        assert held is not None and held.pending is None
        assert keygen.generated == 0

    def test_nor_give_up_one_an_administrator_started(
        self, shared: World, keygen: DeployKeyRunner
    ) -> None:
        started = shared.client.post(
            f"/api/apps/{ACME_API}/deploy-key/rotate", headers=shared.as_("admin")
        )
        assert started.status_code == 200, started.text

        response = shared.client.delete(
            f"/api/apps/{ACME_API}/deploy-key/rotate", headers=shared.as_("alice")
        )

        assert response.status_code == 400, response.text
        held = DeployKeyStore(shared.store).get("ci-bot")
        assert held is not None and held.pending is not None

    def test_a_key_that_serves_only_the_callers_repositories_is_rotated(
        self, keyed: World, keygen: DeployKeyRunner
    ) -> None:
        response = keyed.client.post(
            f"/api/apps/{ACME}/deploy-key/rotate", headers=keyed.as_("alice")
        )

        assert response.status_code == 200, response.text
        assert response.json()["key"]["pending"] is not None

    def test_an_administrator_rotates_a_key_whatever_it_reaches(
        self, shared: World, keygen: DeployKeyRunner
    ) -> None:
        response = shared.client.post(
            f"/api/apps/{ACME_API}/deploy-key/rotate", headers=shared.as_("admin")
        )

        assert response.status_code == 200, response.text
        assert response.json()["key"]["pending"] is not None

    def test_the_manager_refuses_it_even_without_the_router(
        self, shared: World, keygen: DeployKeyRunner
    ) -> None:
        # Rule 4: the guard is in the manager, which the CLI and a job reach as well.
        from noust.core.teams import bind
        from noust.managers.source_keys import DeployKeyManager

        scope = shared.teams.scope_for(_account_id(shared, "alice"), "viewer")
        with bind(scope):
            with pytest.raises(ValidationError, match="outside your teams"):
                DeployKeyManager(store=shared.store).rotate_start(ACME_API)
        # Outside a request nothing is narrowed: the CLI is the instance's.
        assert DeployKeyManager(store=shared.store).rotate_start(ACME_API).pending is not None


class TestWhatAnApplicationIsBuiltFromIsItsTeams:
    """The build block and the container settings are reached through the application."""

    @pytest.fixture
    def configured(self, world: World) -> World:
        """Both teams' applications have a build block, and each is a container application."""
        for domain in (ACME, GLOBEX):
            set_document(domain, "build:\n  build: make\n", updated_by="test", store=world.store)
            app = world.store.get_app(domain)
            assert app is not None
            app.app_type = "container"
            world.store.update_app(app)
            ContainerStore(world.store).save(
                domain, ContainerSettings(method="dockerfile"), actor="test"
            )
        return world

    @pytest.mark.parametrize(
        ("method", "suffix", "body"),
        [
            ("GET", "build", None),
            ("PUT", "build", {"build": "rm -rf /"}),
            ("DELETE", "build", None),
            ("GET", "container", None),
            ("PUT", "container", {"restart_policy": "always"}),
        ],
    )
    def test_another_teams_reads_as_absent_and_is_not_changed(
        self, configured: World, method: str, suffix: str, body: Any
    ) -> None:
        response = configured.client.request(
            method, f"/api/apps/{GLOBEX}/{suffix}", json=body, headers=configured.as_("alice")
        )

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"
        assert get_document(GLOBEX, store=configured.store) == "build:\n  build: make\n"
        kept = ContainerStore(configured.store).get(GLOBEX)
        assert kept is not None and kept.restart_policy == "unless-stopped"

    def test_the_owner_team_reads_its_own(self, configured: World) -> None:
        build = configured.client.get(f"/api/apps/{ACME}/build", headers=configured.as_("alice"))
        container = configured.client.get(
            f"/api/apps/{ACME}/container", headers=configured.as_("alice")
        )

        assert build.status_code == 200, build.text
        assert build.json()["build"] == "make"
        assert container.status_code == 200, container.text
        assert container.json()["method"] == "dockerfile"

    def test_a_viewer_of_another_team_is_told_the_same_thing(self, configured: World) -> None:
        # bob is in globex only: acme's block is as absent to him as globex's is to alice.
        response = configured.client.get(f"/api/apps/{ACME}/build", headers=configured.as_("bob"))

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"


class TestWhatWasInstalledFromARecipeIsItsTeams:
    @pytest.fixture
    def installed(self, world: World) -> World:
        """Each team's application came from a recipe."""
        for domain in (ACME, GLOBEX):
            assert record_install(domain, get_recipe("vaultwarden"), store=world.store)
        return world

    def test_a_member_lists_only_the_installs_of_its_teams(self, installed: World) -> None:
        def seen(who: dict[str, str]) -> set[str]:
            body = installed.client.get("/api/recipes/installed", headers=who).json()
            return {item["domain"] for item in body["items"]}

        assert seen(installed.as_("alice")) == {ACME}
        assert seen(installed.as_("bob")) == {GLOBEX}
        assert seen(installed.as_("admin")) == {ACME, GLOBEX}
        assert seen(installed.master) == {ACME, GLOBEX}

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", f"/api/recipes/installed/{GLOBEX}"),
            ("POST", f"/api/recipes/installed/{GLOBEX}/update"),
        ],
    )
    def test_another_teams_reads_as_absent(self, installed: World, method: str, path: str) -> None:
        response = installed.client.request(method, path, json={}, headers=installed.as_("alice"))

        assert response.status_code == 404, response.text
        assert response.json()["error"] == "not_found"

    def test_its_own_is_answered(self, installed: World) -> None:
        response = installed.client.get(
            f"/api/recipes/installed/{ACME}", headers=installed.as_("alice")
        )

        assert response.status_code == 200, response.text
        assert response.json()["recipe"] == "vaultwarden"

    def test_installing_one_is_the_servers_until_it_names_a_project(self, world: World) -> None:
        response = world.client.post(
            "/api/recipes/vaultwarden/install",
            json={"domain": "vault.acme.example"},
            headers=world.as_("alice"),
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"] == "instance_scope_required"


class TestBackupsAndImportsAreTheServersForEveryTeam:
    #: Each writes a root timer, names a destination the server owns, or loads data into an engine.
    PREFIXES = (
        "/api/volume-backups",
        "/api/self-backup",
        "/api/databases/backup-policies",
        "/api/databases/backups",
        "/api/databases/imports",
        "/api/backups",
        "/api/backup-destinations",
        "/api/apps/import",
    )

    @staticmethod
    def routes_under(world: World, prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
        """
        Args:
            world: The world.
            prefixes: Route prefixes.

        Returns:
            ``(method, path)`` for every route served under them, each path
            parameter filled with a name nothing has.
        """
        found = []
        for route in api_routes(world.client.app.routes):
            template = str(route.path_format)
            if not any(template == p or template.startswith(p + "/") for p in prefixes):
                continue
            path = re.sub(r"\{[^}]+\}", "nothing", template)
            found.extend(
                (method, path) for method in sorted(route.methods or ()) if method != "HEAD"
            )
        return found

    def test_there_are_routes_to_check(self, world: World) -> None:
        assert len(self.routes_under(world, self.PREFIXES)) > 40

    def test_every_one_refuses_a_team_member_with_the_reason(self, world: World) -> None:
        for method, path in self.routes_under(world, self.PREFIXES):
            response = world.client.request(method, path, json={}, headers=world.as_("alice"))

            # A route that also needs a permission no team role holds (the schedule of Noust's
            # own backup is root-equivalent) answers for that first; either way the answer is no.
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["error"] in {"instance_scope_required", "permission_denied"}, (
                method,
                path,
            )

    def test_the_policy_of_the_callers_own_application_is_no_exception(self, world: World) -> None:
        for path in (
            f"/api/volume-backups/policies/{ACME}",
            f"/api/volume-backups/targets/{ACME}",
            "/api/databases/backup-policies/postgresql/shop_db",
        ):
            response = world.client.get(path, headers=world.as_("alice"))

            assert response.status_code == 403, (path, response.text)
            assert response.json()["error"] == "instance_scope_required"

    def test_the_policy_of_another_teams_application_is_refused_the_same_way(
        self, world: World
    ) -> None:
        # Not "absent": nothing under these routes is a team's, so a team member learns nothing
        # about whose application it is, and nothing about whether it exists.
        owned = world.client.get(
            f"/api/volume-backups/policies/{GLOBEX}", headers=world.as_("alice")
        )
        missing = world.client.get(
            "/api/volume-backups/policies/nothing.example", headers=world.as_("alice")
        )

        assert owned.status_code == missing.status_code == 403
        assert owned.json() == missing.json()

    def test_an_administrator_is_not_refused_by_the_scope(self, world: World) -> None:
        for path in ("/api/volume-backups/policies", "/api/databases/backup-policies"):
            response = world.client.get(path, headers=world.as_("admin"))

            assert response.json().get("error") != "instance_scope_required", (path, response.text)
