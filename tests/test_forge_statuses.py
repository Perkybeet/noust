# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Commit statuses and pull request comments on GitLab, Gitea, Forgejo, Gogs and Bitbucket Cloud (A-21).

The reporter decides from the store alone, hands the network to its own thread, marks the commit
a deployment started and ends on, signs in with the credential the repository is cloned with,
and never raises into the deploy. The comment of a preview is created once and edited after.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import IntegrationError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from noust.integrations.forges import api, statuses
from noust.managers.source_credentials import GitCredentialManager
from tests.forge_support import FakeForges

SECRET = "tok-1234567890"


class FakeCredentials(GitCredentialManager):
    """What signs in, without the secret store."""

    def __init__(self, found: tuple[str, str] | None) -> None:
        self.found = found
        self.asked: list[str] = []

    def credential_for(self, url: str) -> tuple[str, str] | None:
        self.asked.append(url)
        return self.found


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()
    Config.reset_instance()


@pytest.fixture
def forges() -> FakeForges:
    return FakeForges()


def app_on(store: NoustStore, source: str, domain: str = "a.example.com", **fields: Any) -> App:
    return store.create_app(
        App(domain=domain, app_type="nodejs", app_path=f"/var/www/apps/{domain}", port=3000,
            status="running", source=source, **fields)
    )  # fmt: skip


def event(kind: DeployEventKind, **extra: Any) -> DeployEvent:
    fields: dict[str, Any] = {
        "domain": "a.example.com",
        "deployment_id": 5,
        "commit": "abc123",
        "branch": "main",
    }
    fields.update(extra)
    return DeployEvent(kind=kind, **fields)


def reporter(forges: FakeForges, found: tuple[str, str] | None = ("oauth2", SECRET)):
    return statuses.StatusReporter(opener=forges.opener, credentials=FakeCredentials(found))


def run(rep: statuses.StatusReporter, *events: DeployEvent) -> None:
    for item in events:
        target = rep.resolve(item)
        if target is not None:
            rep.submit(target, item)
    assert rep.drain(10)


class TestGitLab:
    def test_a_deployment_is_marked_running_then_success(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git#main")

        run(reporter(forges), event(DeployEventKind.STARTED), event(DeployEventKind.SUCCEEDED))

        started, ended = forges.writes("gitlab.example.com")
        assert started.path == "/api/v4/projects/acme%2Fshop/statuses/abc123"
        assert started.form["state"] == "running"
        assert started.form["name"] == "noust/deploy"
        assert ended.form["state"] == "success"
        assert ended.form["target_url"] == "https://a.example.com"
        assert ended.headers["authorization"] == f"Bearer {SECRET}"

    def test_a_failure_says_why_in_one_line(self, store: NoustStore, forges: FakeForges) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        run(
            reporter(forges),
            event(DeployEventKind.STARTED),
            event(DeployEventKind.FAILED, error="npm ERR! build failed\nmore\nlines"),
        )

        assert forges.writes()[-1].form["state"] == "failed"
        assert forges.writes()[-1].form["description"] == "npm ERR! build failed"

    def test_a_rolled_back_deployment_is_a_failure_that_says_so(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.STARTED), event(DeployEventKind.ROLLED_BACK))

        assert forges.writes()[-1].form["state"] == "failed"
        assert forges.writes()[-1].form["description"] == "Rolled back"

    def test_a_cancelled_deployment_is_not_a_failure(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        run(
            reporter(forges),
            event(DeployEventKind.STARTED),
            event(DeployEventKind.FAILED, cancelled=True),
        )

        assert forges.writes()[-1].form["state"] == "canceled"

    def test_a_project_in_subgroups_is_one_encoded_path(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/team/shop.git")

        run(reporter(forges), event(DeployEventKind.SUCCEEDED))

        assert forges.writes()[0].path == "/api/v4/projects/acme%2Fteam%2Fshop/statuses/abc123"

    def test_a_token_that_cannot_write_is_told_what_scope_it_needs(
        self, store: NoustStore, forges: FakeForges, caplog: pytest.LogCaptureFixture
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")
        forges.write_allowed = False
        caplog.set_level(logging.WARNING)

        run(reporter(forges), event(DeployEventKind.SUCCEEDED))

        assert "api scope" in caplog.text
        assert SECRET not in caplog.text


class TestGitea:
    def test_a_status_is_json_with_a_token_header(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitea.example.com/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.STARTED), event(DeployEventKind.SUCCEEDED))

        started, ended = forges.writes("gitea.example.com")
        assert started.path == "/api/v1/repos/acme/shop/statuses/abc123"
        assert started.body["state"] == "pending"
        assert started.body["context"] == "noust/deploy"
        assert ended.body["state"] == "success"
        assert ended.body["target_url"] == "https://a.example.com"
        assert started.headers["authorization"] == f"token {SECRET}"
        assert started.headers["content-type"] == "application/json"

    def test_failure_and_cancellation_have_their_own_states(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitea.example.com/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.FAILED, error="boom"))
        run(reporter(forges), event(DeployEventKind.FAILED, cancelled=True, deployment_id=6))

        assert [call.body["state"] for call in forges.writes()] == ["failure", "error"]

    def test_forgejo_is_gitea_to_write_to(self, store: NoustStore, forges: FakeForges) -> None:
        app_on(store, "https://codeberg.org/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.SUCCEEDED))

        (call,) = forges.writes("codeberg.org")
        assert call.path == "/api/v1/repos/acme/shop/statuses/abc123"

    def test_gogs_has_no_commit_statuses_so_nothing_is_sent(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gogs.example.com/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.STARTED), event(DeployEventKind.SUCCEEDED))

        assert forges.calls == []

    def test_a_host_the_operator_named_is_what_they_said(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://git.example.com:3000/acme/shop.git")
        assert statuses.target_for("a.example.com") is None  # nothing says what it is

        Config().set("integrations.forge_hosts", ["git.example.com=gitea"])
        run(reporter(forges), event(DeployEventKind.SUCCEEDED))

        (call,) = forges.writes()
        assert call.host == "git.example.com:3000"


class TestBitbucket:
    def test_a_build_status_is_keyed_so_a_new_state_replaces_the_last(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://bitbucket.org/acme/shop.git")

        run(
            reporter(forges, ("alice", "app-password")),
            event(DeployEventKind.STARTED),
            event(DeployEventKind.SUCCEEDED),
        )

        started, ended = forges.writes("api.bitbucket.org")
        assert started.path == "/2.0/repositories/acme/shop/commit/abc123/statuses/build"
        assert (started.body["key"], started.body["state"]) == ("noust/deploy", "INPROGRESS")
        assert ended.body["state"] == "SUCCESSFUL"
        assert ended.body["url"] == "https://a.example.com"
        assert started.headers["authorization"].startswith("Basic ")

    def test_an_access_token_is_a_bearer(self, store: NoustStore, forges: FakeForges) -> None:
        app_on(store, "https://bitbucket.org/acme/shop.git")

        run(reporter(forges, ("x-token-auth", "repo-token")), event(DeployEventKind.SUCCEEDED))

        assert forges.writes()[0].headers["authorization"] == "Bearer repo-token"

    def test_failure_and_cancellation(self, store: NoustStore, forges: FakeForges) -> None:
        app_on(store, "https://bitbucket.org/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.FAILED, error="boom"))
        run(reporter(forges), event(DeployEventKind.FAILED, cancelled=True, deployment_id=6))

        assert [call.body["state"] for call in forges.writes()] == ["FAILED", "STOPPED"]


class TestWhenNothingIsSaid:
    def test_a_repository_with_no_credential_gets_nothing(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        run(
            reporter(forges, None), event(DeployEventKind.STARTED), event(DeployEventKind.SUCCEEDED)
        )

        assert forges.calls == []

    def test_an_event_with_no_commit_has_nothing_to_mark(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.STARTED, commit=None))

        assert forges.calls == []

    def test_the_end_uses_the_commit_the_start_named(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        run(
            reporter(forges),
            event(DeployEventKind.STARTED),
            event(DeployEventKind.FAILED, commit=None, error="boom"),
        )

        assert forges.writes()[-1].path.endswith("/statuses/abc123")

    def test_github_is_the_apps_to_tell_and_not_this(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://github.com/acme/shop.git")

        assert statuses.target_for("a.example.com") is None

    def test_a_source_that_is_not_a_repository_is_not_reported(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "/srv/code/shop")

        assert statuses.target_for("a.example.com") is None

    def test_an_unknown_application_is_not_reported(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        assert statuses.target_for("ghost.example.com") is None

    def test_a_preview_is_its_own_check(self, store: NoustStore, forges: FakeForges) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git", preview_parent="shop.example.com")

        run(reporter(forges), event(DeployEventKind.SUCCEEDED))

        assert forges.writes()[0].form["name"] == "noust/preview"

    def test_the_console_is_where_details_lead_when_it_has_an_address(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        Config().set("web.public_url", "https://noust.example.com")
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        run(reporter(forges), event(DeployEventKind.FAILED, error="boom"))

        assert (
            forges.writes()[0].form["target_url"] == "https://noust.example.com/apps/a.example.com"
        )


class TestNeverIntoTheDeploy:
    def test_an_unreachable_host_is_logged_not_raised(
        self, store: NoustStore, forges: FakeForges, caplog: pytest.LogCaptureFixture
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")
        forges.offline = True
        caplog.set_level(logging.WARNING)

        run(reporter(forges), event(DeployEventKind.SUCCEEDED))

        assert "was not written to gitlab.example.com" in caplog.text
        assert SECRET not in caplog.text

    def test_the_subscriber_returns_before_the_network_is_touched(
        self, store: NoustStore, forges: FakeForges, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")
        rep = reporter(forges)
        monkeypatch.setattr(statuses, "reporter", rep)

        statuses.on_deploy_event(event(DeployEventKind.SUCCEEDED))
        assert rep.drain(10)

        assert len(forges.writes()) == 1


class TestTheCommentOfAPreview:
    def _comment(
        self, forges: FakeForges, repository: str, number: int, body: str, ref: str | None,
        found: tuple[str, str] | None = ("oauth2", SECRET), parent: str = "a.example.com",
    ) -> str | None:  # fmt: skip
        return statuses.upsert_pr_comment(
            parent, repository, number, body, ref,
            opener=forges.opener, credentials=FakeCredentials(found),
        )  # fmt: skip

    @pytest.mark.parametrize(
        ("source", "host", "created", "edited"),
        [
            (
                "https://gitlab.example.com/acme/shop.git",
                "gitlab.example.com",
                "/api/v4/projects/acme%2Fshop/merge_requests/7/notes",
                "/api/v4/projects/acme%2Fshop/merge_requests/7/notes/1001",
            ),
            (
                "https://gitea.example.com/acme/shop.git",
                "gitea.example.com",
                "/api/v1/repos/acme/shop/issues/7/comments",
                "/api/v1/repos/acme/shop/issues/comments/1001",
            ),
            (
                "https://bitbucket.org/acme/shop.git",
                "api.bitbucket.org",
                "/2.0/repositories/acme/shop/pullrequests/7/comments",
                "/2.0/repositories/acme/shop/pullrequests/7/comments/1001",
            ),
        ],
    )
    def test_it_is_created_once_and_edited_after(
        self,
        store: NoustStore,
        forges: FakeForges,
        source: str,
        host: str,
        created: str,
        edited: str,
    ) -> None:
        app_on(store, source)

        first = self._comment(forges, "acme/shop", 7, "one", None)
        second = self._comment(forges, "acme/shop", 7, "two", first)

        assert first == second == "1001"
        first_call, second_call = forges.writes(host)
        assert (first_call.method, first_call.path) == ("POST", created)
        assert second_call.path == edited

    def test_a_comment_somebody_deleted_is_made_again(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitea.example.com/acme/shop.git")
        first = self._comment(forges, "acme/shop", 7, "one", None)
        assert first is not None
        forges.deleted.add(first)

        again = self._comment(forges, "acme/shop", 7, "two", first)

        assert again is not None and again != first

    def test_the_body_is_what_the_host_wants(self, store: NoustStore, forges: FakeForges) -> None:
        app_on(store, "https://bitbucket.org/acme/shop.git")

        self._comment(forges, "acme/shop", 7, "**hi**", None)

        assert forges.writes()[0].body == {"content": {"raw": "**hi**"}}

    def test_no_credential_means_no_comment(self, store: NoustStore, forges: FakeForges) -> None:
        app_on(store, "https://gitlab.example.com/acme/shop.git")

        assert self._comment(forges, "acme/shop", 7, "one", None, found=None) is None
        assert forges.calls == []

    def test_an_application_that_is_not_on_such_a_host_gets_none(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://github.com/acme/shop.git")

        assert self._comment(forges, "acme/shop", 7, "one", None) is None

    def test_a_refusal_is_raised_for_the_caller_to_log(
        self, store: NoustStore, forges: FakeForges
    ) -> None:
        app_on(store, "https://gitea.example.com/acme/shop.git")
        forges.write_allowed = False

        with pytest.raises(IntegrationError):
            self._comment(forges, "acme/shop", 7, "one", None)


class TestThroughThePreviewsOwnHelper:
    def test_a_non_github_comment_goes_to_the_forge_and_failures_do_not_escape(
        self, store: NoustStore, forges: FakeForges, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import previews

        app_on(store, "https://gitea.example.com/acme/shop.git")
        monkeypatch.setattr(
            statuses,
            "upsert_pr_comment",
            lambda *a, **k: (_ for _ in ()).throw(IntegrationError("boom")),
        )

        kept = previews._upsert_comment(
            "gitea", "acme/shop", 7, "body", "55", parent_domain="a.example.com"
        )

        assert kept == "55"

    def test_the_helper_hands_the_forge_the_application_it_previews(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import previews

        seen: list[Any] = []
        monkeypatch.setattr(statuses, "upsert_pr_comment", lambda *a, **k: seen.append(a) or "77")

        posted = previews._upsert_comment(
            "gitlab", "acme/shop", 7, "body", None, parent_domain="a.example.com"
        )

        assert posted == "77"
        assert seen == [("a.example.com", "acme/shop", 7, "body", None)]

    def test_without_the_application_nothing_is_written(self, store: NoustStore) -> None:
        from noust.managers import previews

        assert previews._upsert_comment("gitlab", "acme/shop", 7, "body", "9") == "9"


class TestTheRealCredential:
    def test_the_credential_a_repository_is_cloned_with_signs_in_the_api(
        self, store: NoustStore, forges: FakeForges, tmp_path: Path
    ) -> None:
        app_on(store, "https://gitea.example.com/acme/shop.git")
        manager = GitCredentialManager(store=store, secrets=SecretStore(root=tmp_path / "secrets"))
        manager.put("a.example.com", "ci-bot", SECRET)
        rep = statuses.StatusReporter(opener=forges.opener, credentials=manager)

        run(rep, event(DeployEventKind.SUCCEEDED))

        assert forges.writes()[0].headers["authorization"] == f"token {SECRET}"

    def test_the_api_clients_are_the_four_kinds(self) -> None:
        repo = api.ForgeRepo("forgejo", "codeberg.org", "acme/shop")

        assert repo.family == "gitea" and repo.host == "codeberg.org"
        assert api.api_for(repo, ("u", "s")).supports_statuses is True
        assert (
            api.api_for(api.ForgeRepo("gogs", "g.test", "a/b"), ("u", "s")).supports_statuses
            is False
        )
        with pytest.raises(IntegrationError):
            api.api_for(api.ForgeRepo("github", "github.com", "a/b"), ("u", "s"))
