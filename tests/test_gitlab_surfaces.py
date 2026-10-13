"""
``noust gitlab`` and ``/api/integrations/gitlab`` (checklist A-06).

The surfaces parse, present and ask for sudo mode; the work is the service. Pinned here: the token
is read from standard input or the environment and never an argument, nothing a surface prints
holds a token, and the routes are held to the permissions and the scope of the other credentials of
the server.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.gitlab_store import GitLabStore
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.integrations.gitlab import service as gitlab_service
from noust.integrations.gitlab.service import GitLabService
from tests.test_gitlab_connections import (  # noqa: F401 - fixtures
    CLIENT_SECRET,
    HOST,
    TOKEN,
    FakeGitLab,
    audited,
    clock,
    gitlab,
    secrets,
    store,
)


@pytest.fixture
def service(
    store: NoustStore,  # noqa: F811
    secrets: SecretStore,  # noqa: F811
    gitlab: FakeGitLab,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[GitLabService]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    gitlab_service.states.clear()
    built = GitLabService(GitLabStore(store), secrets, opener=gitlab)
    monkeypatch.setattr("noust.cli.commands.gitlab.GitLabService", lambda: built)
    monkeypatch.setattr("noust.web.api.gitlab._service", lambda: built)
    yield built
    Config.reset_instance()


def invoke(*args: str, input: str | None = None, env: dict[str, str] | None = None) -> Any:
    from click.testing import CliRunner

    from noust.cli.app import cli

    return CliRunner().invoke(cli, list(args), input=input, env=env)


class TestTheCommands:
    def test_connect_reads_the_token_from_standard_input(
        self,
        service: GitLabService,
        gitlab: FakeGitLab,  # noqa: F811
    ) -> None:
        done = invoke(
            "gitlab", "connect", "work", "--host", HOST, "--token-stdin", input=TOKEN + "\n"
        )

        assert done.exit_code == 0, done.output
        assert "Connected work to git.example.com as ana" in done.output
        assert TOKEN not in done.output
        assert gitlab.requests[0]["headers"]["private-token"] == TOKEN

    def test_or_from_the_environment(self, service: GitLabService) -> None:
        done = invoke(
            "gitlab", "connect", "work", "--host", HOST, "--json", env={"NOUST_GITLAB_TOKEN": TOKEN}
        )

        assert done.exit_code == 0, done.output
        assert json.loads(done.output)["username"] == "ana"
        assert TOKEN not in done.output

    def test_there_is_no_way_to_give_it_as_an_argument(self, service: GitLabService) -> None:
        refused = invoke("gitlab", "connect", "work", "--host", HOST, "--token", TOKEN)
        assert refused.exit_code != 0

    def test_without_a_token_it_says_how_to_give_one(self, service: GitLabService) -> None:
        refused = invoke("gitlab", "connect", "work", "--host", HOST)

        assert refused.exit_code != 0
        assert "NOUST_GITLAB_TOKEN" in (refused.exception.details or "")

    def test_a_dry_run_asks_nothing(self, service: GitLabService, gitlab: FakeGitLab) -> None:  # noqa: F811
        done = invoke("--dry-run", "gitlab", "connect", "work", "--host", HOST)

        assert done.exit_code == 0 and "Would connect work" in done.output
        assert gitlab.requests == []

    def test_list_test_and_remove(self, service: GitLabService, gitlab: FakeGitLab) -> None:  # noqa: F811
        service.connect_with_token("work", HOST, TOKEN)

        listed = invoke("gitlab", "list", "--json")
        tested = invoke("gitlab", "test", "work")
        removed = invoke("gitlab", "remove", "work")
        again = invoke("gitlab", "remove", "work")

        assert [c["name"] for c in json.loads(listed.output)["connections"]] == ["work"]
        assert TOKEN not in listed.output
        assert tested.exit_code == 0 and "ok" in tested.output
        assert "Removed work" in removed.output and "no GitLab connection" in again.output

    def test_the_picker(self, service: GitLabService, gitlab: FakeGitLab) -> None:  # noqa: F811
        service.connect_with_token("work", HOST, TOKEN)
        gitlab.route(
            "GET",
            "/api/v4/projects",
            [
                {
                    "id": 1,
                    "path_with_namespace": "acme/shop",
                    "default_branch": "main",
                    "http_url_to_repo": "https://git.example.com/acme/shop.git",
                }
            ],
        )
        gitlab.route("GET", "/api/v4/groups", [{"id": 3, "full_path": "acme"}])
        gitlab.route(
            "GET", "/api/v4/projects/1/repository/branches", [{"name": "main", "default": True}]
        )

        projects = invoke("gitlab", "projects", "work")
        groups = invoke("gitlab", "groups", "work")
        branches = invoke("gitlab", "branches", "work", "1", "--json")

        assert "acme/shop  [main]  https://git.example.com/acme/shop.git" in projects.output
        assert "acme  (id 3)" in groups.output
        assert json.loads(branches.output)["items"][0]["name"] == "main"

    def test_the_oauth_application(self, service: GitLabService, secrets: SecretStore) -> None:  # noqa: F811
        Config().set("web.public_url", "https://console.example.com")

        done = invoke(
            "gitlab",
            "oauth-app",
            "set",
            "--host",
            HOST,
            "--client-id",
            "app-id",
            "--client-secret-stdin",
            input=CLIENT_SECRET,
        )

        assert done.exit_code == 0, done.output
        assert "https://console.example.com/integrations/gitlab/callback" in done.output
        assert CLIENT_SECRET not in done.output
        assert secrets.read("gitlab/oauth/git.example.com") == CLIENT_SECRET
        removed = invoke("gitlab", "oauth-app", "remove", "--host", HOST)
        assert removed.exit_code == 0 and secrets.read("gitlab/oauth/git.example.com") is None


class TestTheApi:
    @pytest.fixture
    def client(self, service: GitLabService) -> Any:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import gitlab as gitlab_api
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers, require_elevated

        server = FastAPI()
        install_error_handlers(server)
        server.include_router(gitlab_api.router, prefix="/api/integrations")
        session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
        server.dependency_overrides[get_current_session] = lambda: session
        server.dependency_overrides[require_elevated] = lambda: session
        return TestClient(server)

    def test_the_permissions_and_scopes(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_gitlab import ROUTES
        from noust.web.permissions.scopes import INSTANCE, rule_for

        assert ROUTES[("POST", "/api/integrations/gitlab/connections")] == Permission.APPS_MANAGE
        assert (
            ROUTES[("GET", "/api/integrations/gitlab/connections/{name}/projects")]
            == Permission.APPS_READ
        )
        for method, template in ROUTES:
            rule = rule_for(method, template)
            assert rule is not None and rule.scoping == INSTANCE, template

    def test_connect_list_check_and_remove(self, client: Any) -> None:
        added = client.post(
            "/api/integrations/gitlab/connections",
            json={"name": "work", "host": HOST, "token": TOKEN},
        )
        listed = client.get("/api/integrations/gitlab")
        tested = client.post("/api/integrations/gitlab/connections/work/test")
        removed = client.delete("/api/integrations/gitlab/connections/work")

        assert added.status_code == 201, added.text
        assert added.json()["username"] == "ana"
        assert TOKEN not in added.text and TOKEN not in listed.text
        assert [c["name"] for c in listed.json()["connections"]] == ["work"]
        assert tested.json()["last_status"] == "ok"
        assert removed.json() == {"removed": True}

    def test_a_bad_connection_is_a_400_naming_the_field(self, client: Any) -> None:
        got = client.post(
            "/api/integrations/gitlab/connections",
            json={"name": "Bad Name", "host": HOST, "token": TOKEN},
        )

        assert got.status_code == 400
        assert "name" in got.text

    def test_projects_groups_and_branches(self, client: Any, gitlab: FakeGitLab) -> None:  # noqa: F811
        client.post(
            "/api/integrations/gitlab/connections",
            json={"name": "work", "host": HOST, "token": TOKEN},
        )
        gitlab.route(
            "GET",
            "/api/v4/projects",
            [{"id": 1, "path_with_namespace": "acme/shop", "default_branch": "main"}],
        )
        gitlab.route("GET", "/api/v4/groups", [{"id": 3, "full_path": "acme", "name": "Acme"}])
        gitlab.route(
            "GET", "/api/v4/projects/1/repository/branches", [{"name": "main", "default": True}]
        )

        projects = client.get("/api/integrations/gitlab/connections/work/projects?search=sh")
        groups = client.get("/api/integrations/gitlab/connections/work/groups")
        branches = client.get("/api/integrations/gitlab/connections/work/projects/1/branches")
        bad = client.get("/api/integrations/gitlab/connections/work/projects/abc/branches")

        assert projects.json()["items"][0]["path"] == "acme/shop"
        assert groups.json()["items"][0]["full_path"] == "acme"
        assert branches.json()["items"][0] == {"name": "main", "default": True, "commit": None}
        assert bad.status_code == 400

    def test_the_oauth_sign_in(self, client: Any, gitlab: FakeGitLab) -> None:  # noqa: F811
        Config().set("web.public_url", "https://console.example.com")
        gitlab.route(
            "POST",
            "/oauth/token",
            {"access_token": "at", "refresh_token": "rt", "expires_in": 7200, "created_at": 1},
        )
        saved = client.put(
            "/api/integrations/gitlab/oauth-apps",
            json={"host": HOST, "client_id": "app-id", "client_secret": CLIENT_SECRET},
        )
        status = client.get("/api/integrations/gitlab").json()
        started = client.post(
            "/api/integrations/gitlab/oauth/authorize", json={"name": "alice", "host": HOST}
        )
        finished = client.post(
            "/api/integrations/gitlab/oauth/callback",
            json={"code": "c", "state": started.json()["state"]},
        )
        reused = client.post(
            "/api/integrations/gitlab/oauth/callback",
            json={"code": "c", "state": started.json()["state"]},
        )

        assert saved.status_code == 200 and CLIENT_SECRET not in saved.text
        assert status["redirect_uri"] == "https://console.example.com/integrations/gitlab/callback"
        assert status["oauth_apps"][0]["client_id"] == "app-id"
        assert started.json()["authorize_url"].startswith(
            "https://git.example.com/oauth/authorize?"
        )
        assert finished.status_code == 201 and finished.json()["kind"] == "oauth"
        assert reused.status_code == 400
        assert client.delete(f"/api/integrations/gitlab/oauth-apps/{HOST}").json() == {
            "removed": True
        }

    def test_without_a_public_address_the_status_says_so_and_the_sign_in_is_refused(
        self, client: Any
    ) -> None:
        client.put(
            "/api/integrations/gitlab/oauth-apps",
            json={"host": HOST, "client_id": "app-id", "client_secret": CLIENT_SECRET},
        )

        assert client.get("/api/integrations/gitlab").json()["redirect_uri"] is None
        refused = client.post(
            "/api/integrations/gitlab/oauth/authorize", json={"name": "alice", "host": HOST}
        )
        assert refused.status_code == 400 and "web.public_url" in refused.text
