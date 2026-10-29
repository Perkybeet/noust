# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fake GitHub the central's tests and the integration harness talk to behaves as it claims.

A fake that is wrong makes every test built on it prove nothing, so what it promises is pinned
here: the rules of GitHub's installation token (scoped to repositories and permissions, an hour
unless told otherwise), the prefix a GitHub Enterprise Server serves its API under, the record a
harness reads from another container, and a git client that is not let in without a token that
reaches the repository.
"""

from __future__ import annotations

import base64
import time

from noust.integrations.github import app as github_app
from noust.integrations.github.client import GitHubClient, set_client
from tests.support.github_api_fake import APP_PERMISSIONS, FakeGitHubApi, serve
from tests.support.github_api_fixtures import docs_github, record_installation  # noqa: F401


class TestAnEnterpriseServer:
    def test_the_api_is_served_under_its_prefix_and_the_app_works_through_it(
        self,
        store,
        docs_github: FakeGitHubApi,  # noqa: F811
    ) -> None:
        docs_github.api_prefix = "/api/v3"
        docs_github.install("you", ["app"], installation_id=77)
        record_installation(store, docs_github, 77)
        server, url = serve(docs_github)
        set_client(GitHubClient(f"{url}/api/v3", timeout=5))
        try:
            app = github_app.load_app()
            assert app is not None

            issued = app.scoped_installation_token(77, ["you/app"], {"contents": "read"})
        finally:
            server.shutdown()
            server.server_close()

        assert issued.repositories == ("you/app",)
        assert docs_github.paths("POST")[-1] == "/api/v3/app/installations/77/access_tokens"


class TestTheLifeOfAToken:
    def test_it_is_an_hour_unless_it_is_told_otherwise(
        self,
        store,
        docs_github: FakeGitHubApi,  # noqa: F811
    ) -> None:
        docs_github.install("you", ["app"], installation_id=77)
        record_installation(store, docs_github, 77)
        app = github_app.load_app()
        assert app is not None
        assert (
            3500
            < app.scoped_installation_token(77, ["you/app"], {"contents": "read"}).expires_in
            <= 3600
        )

        docs_github.token_lifetime = 300
        assert (
            250
            < app.scoped_installation_token(77, ["you/app"], {"contents": "read"}).expires_in
            <= 300
        )

    def test_a_token_ends_when_its_life_does(
        self,
        store,
        docs_github: FakeGitHubApi,  # noqa: F811
    ) -> None:
        docs_github.install("you", ["app"], installation_id=77)
        record_installation(store, docs_github, 77)
        docs_github.token_lifetime = 200
        app = github_app.load_app()
        assert app is not None
        issued = app.scoped_installation_token(77, ["you/app"], {"contents": "read"})
        now = time.time()

        docs_github.clock = lambda: now + 201
        status, _ = docs_github.handle(
            "GET", "/repos/you/app", {"authorization": f"token {issued.token}"}, None
        )

        assert status == 401


class TestTheRecordAHarnessReads:
    def test_it_holds_what_was_written_and_what_each_token_reaches_but_never_a_token(
        self,
        store,
        docs_github: FakeGitHubApi,  # noqa: F811
    ) -> None:
        docs_github.install("you", ["app", "api"], installation_id=77)
        record_installation(store, docs_github, 77)
        app = github_app.load_app()
        assert app is not None
        issued = app.scoped_installation_token(77, ["you/app"], {"contents": "read"})
        wide = app.installation_token(77)
        app.as_installation(77, "POST", "/repos/you/app/deployments", {"ref": "main"})

        status, state = docs_github.handle("GET", "/__fake/state", {}, None)

        assert status == 200
        assert len(state["deployments"]["you/app"]) == 1
        reached = sorted((t["repositories"] or ["*"] for t in state["tokens"]), key=lambda r: r[0])
        assert reached == [["*"], ["app"]]
        assert issued.token not in str(state) and wide not in str(state)


class TestGitWithoutAToken:
    def basic(self, token: str) -> dict[str, str]:
        credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        return {"authorization": f"Basic {credentials}"}

    def test_a_repository_is_not_served_to_a_token_that_does_not_reach_it(
        self,
        store,
        docs_github: FakeGitHubApi,  # noqa: F811
    ) -> None:
        docs_github.install("you", ["app", "api"], installation_id=77)
        record_installation(store, docs_github, 77)
        docs_github.git_root = "/nonexistent"
        app = github_app.load_app()
        assert app is not None
        issued = app.scoped_installation_token(77, ["you/app"], {"contents": "read"})

        wrong = docs_github.serve_git(
            "GET", "/you/api.git/info/refs?service=git-upload-pack", self.basic(issued.token), b""
        )
        none = docs_github.serve_git(
            "GET", "/you/app.git/info/refs?service=git-upload-pack", {}, b""
        )

        for refused in (wrong, none):
            assert refused is not None
            assert refused[0] == 401
            assert ("WWW-Authenticate", 'Basic realm="GitHub"') in refused[1]
        assert [entry["status"] for entry in docs_github.git_log] == [401, 401]

    def test_nothing_is_served_without_a_root(
        self,
        store,
        docs_github: FakeGitHubApi,  # noqa: F811
    ) -> None:
        assert docs_github.serve_git("GET", "/you/app.git/info/refs", self.basic("x"), b"") is None

    def test_the_permissions_the_app_asks_for_are_the_ones_the_manifest_asks_for(self) -> None:
        from noust.integrations.github.manifest import PERMISSIONS

        assert APP_PERMISSIONS == PERMISSIONS
