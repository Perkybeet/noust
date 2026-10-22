# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GitLab as a source: connections, the project and branch picker, OAuth, and cloning (checklist A-06).

GitLab was a webhook sender and a URL to paste. A connection is a user's access to one host; it
lists the projects and branches the console offers and clones the private projects of its host.
The tests pin what protects the token: it is a secret and never a column, an answer or an
argument; a token GitLab does not accept is not kept; an OAuth sign-in is PKCE, single use and
expires; a refresh is done before the access token expires and rotates the refresh token; and the
only requests ever made are guarded https ones.
"""

from __future__ import annotations

import base64
import io
import itertools
import json
import re
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from noust.core import gitlab_store
from noust.core.config import Config
from noust.core.exceptions import IntegrationError, ValidationError
from noust.core.fs import RealFileSystem
from noust.core.gitlab_store import GitLabStore
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.integrations.gitlab import service as gitlab_service
from noust.integrations.gitlab.service import GitLabService
from noust.managers.source_credentials import GitCredentialManager

HOST = "git.example.com"
TOKEN = "glpat-" + "A" * 20
CLIENT_SECRET = "gloas-" + "B" * 20


def signed_in_as(environment: dict[str, str]) -> str:
    """
    Args:
        environment: What git is given for a repository.

    Returns:
        The ``user:secret`` its Authorization header signs in as.
    """
    header = next(v for v in environment.values() if v.lower().startswith("authorization"))
    match = re.search(r"basic (\S+)", header, re.IGNORECASE)
    assert match is not None
    return base64.b64decode(match.group(1)).decode()


class FakeResponse:
    """What urlopen returns: readable, and a context manager."""

    def __init__(self, body: object) -> None:
        self._raw = json.dumps(body).encode()

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._raw if size < 0 else self._raw[:size]


class FakeGitLab:
    """A GitLab that answers by path, records what it was asked, and can fail."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], Any] = {}
        self.failing: dict[tuple[str, str], int] = {}

    def route(self, method: str, path: str, body: Any) -> None:
        self.routes[(method, path)] = body

    def fail(self, method: str, path: str, status: int) -> None:
        self.failing[(method, path)] = status

    def __call__(self, request: Any, timeout: float = 0) -> FakeResponse:
        parts = urllib.parse.urlsplit(request.full_url)
        path = parts.path
        key = (request.get_method(), path)
        self.requests.append(
            {
                "method": key[0],
                "url": request.full_url,
                "path": path,
                "query": dict(urllib.parse.parse_qsl(parts.query)),
                "headers": {k.lower(): v for k, v in request.header_items()},
                "form": dict(urllib.parse.parse_qsl(request.data.decode())) if request.data else {},
            }
        )
        if key in self.failing:
            raise HTTPError(request.full_url, self.failing[key], "denied", {}, io.BytesIO(b"{}"))
        if key not in self.routes:
            raise HTTPError(request.full_url, 404, "no route", {}, io.BytesIO(b"{}"))
        body = self.routes[key]
        return FakeResponse(body(request) if callable(body) else body)

    def calls(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"] == path]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets", fs=RealFileSystem())


@pytest.fixture
def gitlab() -> FakeGitLab:
    fake = FakeGitLab()
    fake.route("GET", "/api/v4/user", {"id": 7, "username": "ana"})
    return fake


@pytest.fixture
def clock() -> list[float]:
    return [1_000_000.0]


@pytest.fixture
def service(
    store: NoustStore, secrets: SecretStore, gitlab: FakeGitLab, clock: list[float]
) -> GitLabService:
    gitlab_service.states.clear()
    return GitLabService(GitLabStore(store), secrets, opener=gitlab, clock=lambda: clock[0])


@pytest.fixture(autouse=True)
def audited(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr("noust.core.audit.record", lambda *a, **k: seen.append((a, k)) or None)
    return seen


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    yield Config()
    Config.reset_instance()


class TestAToken:
    def test_the_token_is_checked_with_gitlab_then_kept_as_a_secret(
        self, service: GitLabService, gitlab: FakeGitLab, secrets: SecretStore
    ) -> None:
        connection = service.connect_with_token("work", "GIT.example.com", TOKEN, actor="alice")

        assert (connection.name, connection.host, connection.kind) == ("work", HOST, "token")
        assert connection.username == "ana" and connection.last_status == "ok"
        assert connection.created_by == "alice"
        (asked,) = gitlab.calls("/api/v4/user")
        assert asked["headers"]["private-token"] == TOKEN
        assert secrets.read_json("gitlab/connections/work") == {"token": TOKEN}
        assert TOKEN not in json.dumps(connection.to_dict())

    def test_a_token_gitlab_does_not_accept_is_not_kept(
        self, service: GitLabService, gitlab: FakeGitLab, secrets: SecretStore
    ) -> None:
        gitlab.fail("GET", "/api/v4/user", 401)

        with pytest.raises(IntegrationError) as caught:
            service.connect_with_token("work", HOST, TOKEN)

        assert "read_repository" in (caught.value.details or "")
        assert service.connections.get("work") is None
        assert secrets.read_json("gitlab/connections/work") == {}

    @pytest.mark.parametrize("name", ["", "Work", "a b", "-x", "x" * 64])
    def test_names(self, service: GitLabService, name: str) -> None:
        with pytest.raises(ValidationError) as caught:
            service.connect_with_token(name, HOST, TOKEN)
        assert caught.value.field == "name"

    @pytest.mark.parametrize("host", ["", "https://git.example.com", "git.example.com/x", "a b"])
    def test_hosts(self, service: GitLabService, host: str) -> None:
        with pytest.raises(ValidationError) as caught:
            service.connect_with_token("work", host, TOKEN)
        assert caught.value.field == "host"

    def test_a_name_is_used_once(self, service: GitLabService) -> None:
        service.connect_with_token("work", HOST, TOKEN)
        with pytest.raises(ValidationError, match="already"):
            service.connect_with_token("work", "other.example.com", TOKEN)

    def test_every_request_is_https_to_the_hosts_api(
        self, service: GitLabService, gitlab: FakeGitLab
    ) -> None:
        service.connect_with_token("work", "git.example.com:8443", TOKEN)

        assert gitlab.requests[0]["url"] == "https://git.example.com:8443/api/v4/user"

    def test_check_records_what_gitlab_says(
        self, service: GitLabService, gitlab: FakeGitLab
    ) -> None:
        service.connect_with_token("work", HOST, TOKEN)
        gitlab.fail("GET", "/api/v4/user", 401)

        checked = service.check("work")

        assert checked.last_status == "failed"
        assert checked.last_error and "401" in checked.last_error
        gitlab.failing.clear()
        assert service.check("work").last_status == "ok"

    def test_remove_forgets_the_token(self, service: GitLabService, secrets: SecretStore) -> None:
        service.connect_with_token("work", HOST, TOKEN)

        assert service.remove("work") is True
        assert service.remove("work") is False
        assert secrets.read_json("gitlab/connections/work") == {}
        with pytest.raises(ValidationError, match="no GitLab connection"):
            service.client_for("work")

    def test_the_connect_and_the_remove_are_audited_without_the_token(
        self, service: GitLabService, audited: list[Any]
    ) -> None:
        service.connect_with_token("work", HOST, TOKEN)
        service.remove("work")

        names = [call[0][0] for call in audited]
        assert names == ["integration.gitlab.connect", "integration.gitlab.remove"]
        assert TOKEN not in json.dumps(audited, default=str)


class TestThePicker:
    @pytest.fixture
    def connected(self, service: GitLabService, gitlab: FakeGitLab) -> GitLabService:
        service.connect_with_token("work", HOST, TOKEN)
        gitlab.requests.clear()
        return service

    def test_projects_the_user_can_read(self, connected: GitLabService, gitlab: FakeGitLab) -> None:
        gitlab.route(
            "GET",
            "/api/v4/projects",
            [
                {
                    "id": 11,
                    "path_with_namespace": "acme/shop",
                    "default_branch": "main",
                    "http_url_to_repo": "https://git.example.com/acme/shop.git",
                    "web_url": "https://git.example.com/acme/shop",
                },
                "not a project",
            ],
        )

        projects = connected.projects("work", search="sh")

        assert projects == [
            {
                "id": 11,
                "path": "acme/shop",
                "default_branch": "main",
                "clone_url": "https://git.example.com/acme/shop.git",
                "web_url": "https://git.example.com/acme/shop",
            }
        ]
        asked = gitlab.calls("/api/v4/projects")[0]
        assert asked["query"]["membership"] == "true" and asked["query"]["search"] == "sh"
        assert asked["query"]["archived"] == "false" and asked["query"]["simple"] == "true"

    def test_a_group_filters_and_includes_subgroups(
        self, connected: GitLabService, gitlab: FakeGitLab
    ) -> None:
        gitlab.route("GET", "/api/v4/groups/acme%2Fplatform/projects", [])

        connected.projects("work", group="/acme/platform/")

        asked = gitlab.requests[-1]
        assert (
            asked["path"] == "/api/v4/groups/acme/platform/projects"
            or "acme%2Fplatform" in asked["url"]
        )
        assert asked["query"]["include_subgroups"] == "true"
        assert "membership" not in asked["query"]

    def test_groups(self, connected: GitLabService, gitlab: FakeGitLab) -> None:
        gitlab.route(
            "GET",
            "/api/v4/groups",
            [{"id": 3, "full_path": "acme", "name": "Acme"}, {"id": 4}],
        )

        assert connected.groups("work") == [{"id": 3, "full_path": "acme", "name": "Acme"}]

    def test_branches(self, connected: GitLabService, gitlab: FakeGitLab) -> None:
        gitlab.route(
            "GET",
            "/api/v4/projects/11/repository/branches",
            [
                {"name": "main", "default": True, "commit": {"id": "abc"}},
                {"name": "dev", "default": False},
            ],
        )

        assert connected.branches("work", 11) == [
            {"name": "main", "default": True, "commit": "abc"},
            {"name": "dev", "default": False, "commit": None},
        ]

    def test_a_listing_is_read_page_by_page_up_to_the_limit(
        self, connected: GitLabService, gitlab: FakeGitLab
    ) -> None:
        pages = {"1": [{"id": i, "path_with_namespace": f"a/p{i}"} for i in range(100)]}
        pages["2"] = [{"id": 100 + i, "path_with_namespace": f"a/q{i}"} for i in range(5)]
        gitlab.route(
            "GET",
            "/api/v4/projects",
            lambda request: pages[
                dict(urllib.parse.parse_qsl(request.full_url.split("?", 1)[1]))["page"]
            ],
        )

        assert len(connected.projects("work", limit=1000)) == 105
        assert len(connected.projects("work", limit=10)) == 10

    def test_something_that_is_not_a_list_is_an_error(
        self, connected: GitLabService, gitlab: FakeGitLab
    ) -> None:
        gitlab.route("GET", "/api/v4/projects", {"message": "x"})
        with pytest.raises(IntegrationError, match="not a list"):
            connected.projects("work")

    def test_gitlabs_refusals_say_what_to_do(
        self, connected: GitLabService, gitlab: FakeGitLab
    ) -> None:
        gitlab.fail("GET", "/api/v4/projects", 403)
        with pytest.raises(IntegrationError) as caught:
            connected.projects("work")
        assert "read_api" in (caught.value.details or "")


class TestOAuth:
    @pytest.fixture
    def ready(self, service: GitLabService, config: Config) -> GitLabService:
        config.set("web.public_url", "https://console.example.com")
        service.save_oauth_application(HOST, "app-id", CLIENT_SECRET, actor="alice")
        return service

    def token_answer(self, gitlab: FakeGitLab, clock: list[float], name: str = "a") -> None:
        issued = itertools.count(1)

        def answer(_request: Any) -> dict[str, Any]:
            number = next(issued)
            return {
                "access_token": f"access-{name}-{number}",
                "refresh_token": f"refresh-{name}-{number}",
                "token_type": "Bearer",
                "expires_in": 7200,
                "created_at": int(clock[0]),
            }

        gitlab.route("POST", "/oauth/token", answer)

    def test_the_application_is_kept_with_its_secret_apart(
        self, ready: GitLabService, secrets: SecretStore
    ) -> None:
        application = ready.connections.oauth_application(HOST)

        assert application is not None and application.client_id == "app-id"
        assert secrets.read("gitlab/oauth/git.example.com") == CLIENT_SECRET
        assert CLIENT_SECRET not in json.dumps(application.to_dict())

    def test_the_redirect_is_the_consoles_callback(self, ready: GitLabService) -> None:
        assert ready.redirect_uri() == "https://console.example.com/integrations/gitlab/callback"

    def test_without_a_public_address_there_is_nowhere_to_come_back_to(
        self, service: GitLabService, config: Config
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            service.redirect_uri()
        assert caught.value.field == "web.public_url"

    def test_the_authorize_url_is_pkce_and_asks_for_read_scopes(self, ready: GitLabService) -> None:
        started = ready.oauth_start("alice", HOST)

        url = urllib.parse.urlsplit(started["authorize_url"])
        query = dict(urllib.parse.parse_qsl(url.query))
        assert f"{url.scheme}://{url.netloc}{url.path}" == "https://git.example.com/oauth/authorize"
        assert query["client_id"] == "app-id" and query["response_type"] == "code"
        assert query["scope"] == "read_api read_repository"
        assert query["redirect_uri"] == "https://console.example.com/integrations/gitlab/callback"
        assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43
        assert query["state"] == started["state"]
        assert CLIENT_SECRET not in started["authorize_url"]

    def test_finishing_keeps_the_tokens_and_names_the_user(
        self,
        ready: GitLabService,
        gitlab: FakeGitLab,
        clock: list[float],
        secrets: SecretStore,
    ) -> None:
        self.token_answer(gitlab, clock)
        started = ready.oauth_start("alice", HOST)
        challenge = dict(urllib.parse.parse_qsl(started["authorize_url"].split("?", 1)[1]))[
            "code_challenge"
        ]

        connection = ready.oauth_finish("the-code", started["state"])

        assert (connection.name, connection.kind, connection.username) == ("alice", "oauth", "ana")
        (exchange,) = gitlab.calls("/oauth/token")
        assert exchange["form"]["grant_type"] == "authorization_code"
        assert exchange["form"]["code"] == "the-code"
        assert exchange["form"]["client_secret"] == CLIENT_SECRET
        import base64
        import hashlib

        verifier = exchange["form"]["code_verifier"]
        assert (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
            == challenge
        )
        who = gitlab.calls("/api/v4/user")[-1]
        assert who["headers"]["authorization"].startswith("Bearer access-a-1")
        stored = secrets.read_json("gitlab/connections/alice")
        assert stored["access_token"] == "access-a-1" and stored["refresh_token"] == "refresh-a-1"

    def test_a_callback_is_used_once(
        self, ready: GitLabService, gitlab: FakeGitLab, clock: list[float]
    ) -> None:
        self.token_answer(gitlab, clock)
        state = ready.oauth_start("alice", HOST)["state"]
        ready.oauth_finish("code", state)

        with pytest.raises(ValidationError, match="does not belong"):
            ready.oauth_finish("code", state)

    def test_a_state_that_was_never_issued_is_refused_before_gitlab_is_asked(
        self, ready: GitLabService, gitlab: FakeGitLab
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            ready.oauth_finish("code", "made-up")
        assert caught.value.field == "state"
        assert gitlab.requests == []

    def test_a_sign_in_expires(
        self, ready: GitLabService, gitlab: FakeGitLab, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = ready.oauth_start("alice", HOST)["state"]
        now = gitlab_service.time.time()
        monkeypatch.setattr(
            gitlab_service.time, "time", lambda: now + gitlab_service.STATE_LIFETIME + 1
        )

        with pytest.raises(ValidationError, match="does not belong"):
            ready.oauth_finish("code", state)

    def test_a_host_without_an_application_cannot_start(
        self, service: GitLabService, config: Config
    ) -> None:
        config.set("web.public_url", "https://console.example.com")
        with pytest.raises(ValidationError, match="no OAuth application"):
            service.oauth_start("alice", HOST)

    def test_a_code_gitlab_refuses_leaves_nothing_behind(
        self, ready: GitLabService, gitlab: FakeGitLab, secrets: SecretStore
    ) -> None:
        gitlab.fail("POST", "/oauth/token", 400)
        state = ready.oauth_start("alice", HOST)["state"]

        with pytest.raises(IntegrationError):
            ready.oauth_finish("bad", state)

        assert ready.connections.get("alice") is None
        assert secrets.read_json("gitlab/connections/alice") == {}

    def test_the_access_token_is_used_until_it_is_about_to_expire_then_refreshed_and_rotated(
        self,
        ready: GitLabService,
        gitlab: FakeGitLab,
        clock: list[float],
        secrets: SecretStore,
    ) -> None:
        self.token_answer(gitlab, clock)
        ready.oauth_finish("code", ready.oauth_start("alice", HOST)["state"])
        gitlab.route("GET", "/api/v4/projects", [])
        gitlab.requests.clear()

        ready.projects("alice")
        assert gitlab.requests[-1]["headers"]["authorization"] == "Bearer access-a-1"
        assert gitlab.calls("/oauth/token") == []

        clock[0] += 7200 - gitlab_service.REFRESH_MARGIN + 1
        ready.projects("alice")

        (refresh,) = gitlab.calls("/oauth/token")
        assert refresh["form"]["grant_type"] == "refresh_token"
        assert refresh["form"]["refresh_token"] == "refresh-a-1"
        assert gitlab.requests[-1]["headers"]["authorization"] == "Bearer access-a-2"
        assert secrets.read_json("gitlab/connections/alice")["refresh_token"] == "refresh-a-2"

    def test_a_sign_in_that_cannot_be_renewed_says_to_connect_again(
        self,
        ready: GitLabService,
        gitlab: FakeGitLab,
        clock: list[float],
        secrets: SecretStore,
    ) -> None:
        self.token_answer(gitlab, clock)
        ready.oauth_finish("code", ready.oauth_start("alice", HOST)["state"])
        secrets.write_json("gitlab/connections/alice", {"access_token": "x", "expires_at": 0})

        with pytest.raises(IntegrationError, match="cannot be renewed"):
            ready.projects("alice")

    def test_removing_the_application(self, ready: GitLabService, secrets: SecretStore) -> None:
        assert ready.remove_oauth_application(HOST) is True
        assert ready.remove_oauth_application(HOST) is False
        assert secrets.read("gitlab/oauth/git.example.com") is None


class TestCloning:
    def test_a_connection_clones_the_private_projects_of_its_host(
        self, service: GitLabService
    ) -> None:
        service.connect_with_token("work", HOST, TOKEN)

        found = service.credential_for_url("https://git.example.com/acme/shop.git")

        assert found == ("oauth2", TOKEN)

    @pytest.mark.parametrize(
        "url",
        [
            "https://other.example.com/acme/shop.git",
            "http://git.example.com/acme/shop.git",
            "git@git.example.com:acme/shop.git",
            "https://user:pw@git.example.com/acme/shop.git",
            "https://git.example.com:8443/acme/shop.git",
        ],
    )
    def test_and_nothing_else(self, service: GitLabService, url: str) -> None:
        service.connect_with_token("work", HOST, TOKEN)
        assert service.credential_for_url(url) is None

    def test_git_gets_it_in_its_environment_like_every_other_credential(
        self, service: GitLabService, store: NoustStore, secrets: SecretStore
    ) -> None:
        service.connect_with_token("work", HOST, TOKEN)
        manager = GitCredentialManager(store=store, secrets=secrets)

        assert manager.any_stored() is True
        environment = manager.auth_for("https://git.example.com/acme/shop.git")

        assert environment
        # In git's environment, under the host's own header; never in a key.
        assert TOKEN not in " ".join(environment)
        assert any("git.example.com" in value for value in environment.values())

    def test_a_credential_of_the_repositorys_own_wins(
        self, service: GitLabService, store: NoustStore, secrets: SecretStore, tmp_path: Path
    ) -> None:
        from noust.core.store import App

        service.connect_with_token("work", HOST, TOKEN)
        store.create_app(
            App(
                domain="shop.example.com",
                app_type="nodejs",
                source="https://git.example.com/acme/shop.git",
                app_path=str(tmp_path / "shop"),
            )
        )
        manager = GitCredentialManager(store=store, secrets=secrets)
        manager.put("shop.example.com", "deploy", "own-" + "S" * 20)

        environment = manager.auth_for("https://git.example.com/acme/shop.git")

        assert signed_in_as(environment) == f"deploy:own-{'S' * 20}"
        other = manager.auth_for("https://git.example.com/acme/other.git")
        assert signed_in_as(other) == f"oauth2:{TOKEN}"


def test_the_tables_hold_no_credential(service: GitLabService, store: NoustStore) -> None:
    service.connect_with_token("work", HOST, TOKEN)
    service.save_oauth_application(HOST, "app-id", CLIENT_SECRET)

    with store._transaction() as cursor:
        cursor.execute("SELECT * FROM gitlab_connections")
        rows = [tuple(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM gitlab_oauth_apps")
        rows += [tuple(row) for row in cursor.fetchall()]

    assert TOKEN not in json.dumps(rows) and CLIENT_SECRET not in json.dumps(rows)


def test_a_connection_name_and_its_secret_name(tmp_path: Path) -> None:
    assert gitlab_store.connection_secret("work") == "gitlab/connections/work"
    assert gitlab_store.oauth_secret("git.example.com:8443") == "gitlab/oauth/git.example.com_8443"
