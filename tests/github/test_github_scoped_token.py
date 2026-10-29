# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The token a central asks GitHub for to lease to a server: only its repositories, only reading.

Tested against :mod:`tests.support.github_api_fake`, which behaves as GitHub's reference says. What
is pinned: the request names the repositories (by name, as GitHub takes them) and the one
permission; what GitHub gives back is checked before it can be leased (a token that can write, or
that reaches more than was asked, is refused here and never leaves the central); and what such a
token can and cannot do at GitHub is the point of leasing it.
"""

from __future__ import annotations

import base64
import time
import urllib.request
from typing import Any
from urllib.error import HTTPError

import pytest

from noust.core.exceptions import IntegrationError
from noust.core.store import NoustStore
from noust.integrations.github import app as github_app
from noust.integrations.github.client import GitHubAPIError
from tests.support.github_api_fake import FakeGitHubApi
from tests.support.github_api_fixtures import docs_github, record_installation  # noqa: F401

READ = {"contents": "read"}


@pytest.fixture
def installed(docs_github: FakeGitHubApi, store: NoustStore) -> FakeGitHubApi:  # noqa: F811
    docs_github.install("you", ["app", "api", "secret-project"], installation_id=77)
    record_installation(store, docs_github, 77)
    return docs_github


def app() -> github_app.GitHubApp:
    loaded = github_app.load_app()
    assert loaded is not None
    return loaded


class TestWhatIsAskedOfGitHub:
    def test_it_names_the_repositories_by_name_and_the_one_permission(
        self, installed: FakeGitHubApi
    ) -> None:
        app().scoped_installation_token(77, ["you/app", "you/api"], READ)

        (body,) = installed.bodies("POST", r"/app/installations/77/access_tokens")
        assert body == {"repositories": ["app", "api"], "permissions": {"contents": "read"}}

    def test_the_app_authenticates_with_its_own_token_not_an_installations(
        self, installed: FakeGitHubApi
    ) -> None:
        app().scoped_installation_token(77, ["you/app"], READ)

        request = installed.requests[-1]
        assert request.headers["authorization"].startswith("Bearer ")
        assert request.headers["authorization"].count(".") == 2

    def test_what_comes_back_is_how_long_it_lives_and_what_it_reaches(
        self, installed: FakeGitHubApi
    ) -> None:
        issued = app().scoped_installation_token(77, ["you/app", "you/api"], READ)

        assert issued.token.startswith("ghs_")
        assert 3500 < issued.expires_in <= 3600
        assert issued.repositories == ("you/api", "you/app")
        assert issued.permissions == {"contents": "read", "metadata": "read"}

    def test_it_is_not_the_cached_token_of_the_installation(self, installed: FakeGitHubApi) -> None:
        wide = app().installation_token(77)
        narrow = app().scoped_installation_token(77, ["you/app"], READ)

        assert wide != narrow.token
        assert installed.tokens[wide].repositories is None
        assert installed.tokens[narrow.token].repositories == frozenset({"app"})


class TestWhatTheTokenCanDoAtGitHub:
    """The proof that leasing it gives a server nothing but the reading of its repositories."""

    def reach(self, fake: FakeGitHubApi, token: str, repository: str) -> int:
        credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        request = urllib.request.Request(
            f"{fake_url(fake)}/{repository}.git/info/refs?service=git-upload-pack",
            headers={"Authorization": f"Basic {credentials}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return int(response.status)
        except HTTPError as error:
            return error.code

    def call(self, token: str, method: str, path: str, body: Any = None) -> int:
        github = github_app.get_client()
        try:
            github.request(method, path, authorization=f"token {token}", body=body)
        except GitHubAPIError as error:
            return error.status
        return 200

    def test_it_clones_its_repositories_and_no_other_of_the_same_installation(
        self, installed: FakeGitHubApi
    ) -> None:
        issued = app().scoped_installation_token(77, ["you/app", "you/api"], READ)

        assert self.reach(installed, issued.token, "you/app") == 200
        assert self.reach(installed, issued.token, "you/api") == 200
        assert self.reach(installed, issued.token, "you/secret-project") == 401
        # The installation's own token reaches all three: that is what leasing it would have given.
        wide = app().installation_token(77)
        assert self.reach(installed, wide, "you/secret-project") == 200

    def test_it_writes_nothing_the_app_may(self, installed: FakeGitHubApi) -> None:
        issued = app().scoped_installation_token(77, ["you/app"], READ)

        assert self.call(issued.token, "POST", "/repos/you/app/deployments", {"ref": "main"}) == 403
        assert (
            self.call(issued.token, "POST", "/repos/you/app/issues/1/comments", {"body": "x"})
            == 403
        )
        wide = app().installation_token(77)
        assert self.call(wide, "POST", "/repos/you/app/deployments", {"ref": "main"}) == 200

    def test_it_does_not_see_the_other_repositories_at_all(self, installed: FakeGitHubApi) -> None:
        issued = app().scoped_installation_token(77, ["you/app"], READ)

        assert self.call(issued.token, "GET", "/repos/you/app") == 200
        assert self.call(issued.token, "GET", "/repos/you/api") == 404

    def test_it_stops_working_when_it_ends(self, installed: FakeGitHubApi) -> None:
        issued = app().scoped_installation_token(77, ["you/app"], READ)
        installed.clock = lambda: time.time() + 3601

        assert self.reach(installed, issued.token, "you/app") == 401


class TestWhatIsRefusedBeforeItCanBeLeased:
    def test_a_repository_the_installation_does_not_have_is_githubs_422(
        self, installed: FakeGitHubApi
    ) -> None:
        with pytest.raises(GitHubAPIError) as raised:
            app().scoped_installation_token(77, ["you/app", "you/not-there"], READ)

        assert raised.value.status == 422

    def test_a_permission_the_installation_never_granted_is_githubs_422(
        self, installed: FakeGitHubApi
    ) -> None:
        installed.installations[77].permissions = {"metadata": "read"}

        with pytest.raises(GitHubAPIError) as raised:
            app().scoped_installation_token(77, ["you/app"], READ)

        assert raised.value.status == 422

    def test_more_than_five_hundred_repositories_are_not_asked_for(
        self, installed: FakeGitHubApi
    ) -> None:
        names = [f"you/r{n}" for n in range(501)]

        with pytest.raises(IntegrationError):
            app().scoped_installation_token(77, names, READ)

        assert installed.bodies("POST", r"/app/installations/77/access_tokens") == []

    def test_nothing_to_read_is_not_a_request(self, installed: FakeGitHubApi) -> None:
        with pytest.raises(IntegrationError):
            app().scoped_installation_token(77, [], READ)

        assert installed.requests == []

    def test_a_repository_that_is_not_one_is_not_sent(self, installed: FakeGitHubApi) -> None:
        with pytest.raises(Exception, match="Not a GitHub repository"):
            app().scoped_installation_token(77, ["you/app", "../x"], READ)

        assert installed.requests == []

    def test_a_token_that_can_write_is_never_handed_on(
        self, installed: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A GitHub that ignored the narrowing it was asked for.
        original = installed.handle

        def wide(method: str, target: str, headers: dict[str, str], body: Any):  # type: ignore[no-untyped-def]
            status, answer = original(method, target, headers, body)
            if status == 201:
                answer["permissions"] = {"contents": "write", "metadata": "read"}
            return status, answer

        monkeypatch.setattr(installed, "handle", wide)

        with pytest.raises(IntegrationError, match="more than"):
            app().scoped_installation_token(77, ["you/app"], READ)

    def test_a_token_that_reaches_more_than_was_asked_is_never_handed_on(
        self, installed: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = installed.handle

        def wide(method: str, target: str, headers: dict[str, str], body: Any):  # type: ignore[no-untyped-def]
            status, answer = original(method, target, headers, body)
            if status == 201:
                answer["repository_selection"] = "all"
                answer["repositories"] = [
                    {"name": n, "full_name": f"you/{n}"} for n in ("app", "api", "secret-project")
                ]
            return status, answer

        monkeypatch.setattr(installed, "handle", wide)

        with pytest.raises(IntegrationError, match="more than"):
            app().scoped_installation_token(77, ["you/app"], READ)

    def test_a_token_that_is_about_to_end_is_not_leased(
        self, installed: FakeGitHubApi, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed.clock = lambda: time.time() - 3560

        with pytest.raises(IntegrationError, match="about to end"):
            app().scoped_installation_token(77, ["you/app"], READ)


def fake_url(fake: FakeGitHubApi) -> str:
    """The base URL the client of the fixture points at."""
    return github_app.get_client().base_url
