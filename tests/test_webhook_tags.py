# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the webhooks that deploy by tag (3.2, spec 1.9).

An application that follows tags (``apps.follow_tags``) is updated by what a
forge announces about tags, never by a push to a branch:

- a **release** published on GitHub (or Gitea) deploys its tag; a pre-release
  and a draft are ignored, with the reason in the delivery log;
- a **tag push** (``refs/tags/v1.3.0``) deploys it too, on every forge;
- a tag that does not match the pattern, is not a version, or is not newer
  than the one deployed is ignored and recorded, with the sentence that says
  why;
- the **job carries the tag**, so the update deploys exactly that commit;
- GitHub sends a push and a release for one version: the second is not a
  second deploy;
- an application that follows a branch is exactly what it was.

Both endpoints are covered: an application's own webhook
(``/hooks/deploy/{domain}``) and the GitHub App's (``/hooks/github``).
"""

# ruff: noqa: E402

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import webhook_deliveries as deliveries
from noust.core.forge_events import Forge, TagEvent, parse_tag_event
from noust.core.store import App, NoustStore

SHA = "0123456789abcdef0123456789abcdef01234567"
ZEROS = "0" * 40


# ---------------------------------------------------------------------------
# Reading the payloads
# ---------------------------------------------------------------------------


def push(ref: str, **extra: Any) -> dict[str, Any]:
    return {
        "ref": ref,
        "after": SHA,
        "repository": {
            "full_name": "you/app",
            "clone_url": "https://github.com/you/app.git",
        },
        **extra,
    }


def release(action: str = "published", **flags: Any) -> dict[str, Any]:
    return {
        "action": action,
        "release": {"tag_name": "v1.3.0", "prerelease": False, "draft": False, **flags},
        "repository": {"full_name": "you/app", "clone_url": "https://github.com/you/app.git"},
        "installation": {"id": 7},
    }


class TestParseTagEvent:
    def test_a_github_tag_push_names_its_tag_and_commit(self) -> None:
        event = parse_tag_event("github", "push", push("refs/tags/v1.3.0"))
        assert event == TagEvent(
            forge=Forge.GITHUB,
            tag="v1.3.0",
            source="push",
            repository="you/app",
            clone_url="https://github.com/you/app.git",
            sha=SHA,
        )

    def test_a_push_to_a_branch_is_no_tag(self) -> None:
        assert parse_tag_event("github", "push", push("refs/heads/main")) is None

    def test_deleting_a_tag_deploys_nothing(self) -> None:
        assert parse_tag_event("github", "push", push("refs/tags/v1.3.0", deleted=True)) is None
        assert parse_tag_event("gitlab", "push", {**push("refs/tags/v1"), "after": ZEROS}) is None

    def test_a_gitlab_tag_push_reads_its_project(self) -> None:
        payload = {
            "ref": "refs/tags/v2.0.0",
            "checkout_sha": SHA,
            "project": {"path_with_namespace": "grp/app", "git_http_url": "https://g/grp/app.git"},
        }
        event = parse_tag_event("gitlab", "push", payload)
        assert event is not None
        assert (event.forge, event.tag, event.repository, event.sha) == (
            Forge.GITLAB,
            "v2.0.0",
            "grp/app",
            SHA,
        )

    def test_a_published_release_names_its_tag(self) -> None:
        event = parse_tag_event("github", "release", release())
        assert event is not None
        assert (event.tag, event.source, event.skip, event.installation_id) == (
            "v1.3.0",
            "release",
            None,
            7,
        )

    def test_a_promoted_prerelease_deploys(self) -> None:
        event = parse_tag_event("github", "release", release("released"))
        assert event is not None and event.skip is None

    def test_a_prerelease_is_read_but_marked_to_skip(self) -> None:
        event = parse_tag_event("github", "release", release(prerelease=True))
        assert event is not None
        assert event.skip is not None and "pre-release" in event.skip

    def test_a_draft_is_read_but_marked_to_skip(self) -> None:
        event = parse_tag_event("github", "release", release(draft=True))
        assert event is not None
        assert event.skip is not None and "draft" in event.skip

    @pytest.mark.parametrize(
        "action", ["created", "edited", "deleted", "unpublished", "prereleased"]
    )
    def test_a_release_action_that_publishes_nothing_is_not_an_event(self, action: str) -> None:
        assert parse_tag_event("github", "release", release(action)) is None

    def test_gitea_releases_have_the_same_shape(self) -> None:
        event = parse_tag_event("gitea", "release", release())
        assert event is not None and event.forge is Forge.GITEA

    @pytest.mark.parametrize(
        "payload",
        [{}, {"release": "v1"}, {"action": "published"}, {"action": "published", "release": {}}],
    )
    def test_a_release_without_a_tag_is_not_an_event(self, payload: dict[str, Any]) -> None:
        assert parse_tag_event("github", "release", payload) is None

    def test_a_ref_that_is_not_text_is_not_an_event(self) -> None:
        assert parse_tag_event("github", "push", {"ref": 5}) is None


# ---------------------------------------------------------------------------
# An application's own webhook
# ---------------------------------------------------------------------------

# The fixtures of the branch webhook are imported rather than replicated, so
# there stays one definition of the seeded application and its client.
# ruff: noqa: F811

from noust.core.store import DeploymentTrigger
from noust.deployers import lifecycle
from noust.web.api import hooks as hooks_module
from noust.web.api.hooks import webhook_update_job
from noust.web.jobs import JobType
from tests.test_web_hooks import (  # noqa: F401
    DOMAIN,
    app,
    audit_entries,
    client,
    fresh_delivery_cache,
    gitea_signature,
    github_headers,
    secret,
    seeded,
    store,
)


class Jobs:
    """
    A job manager that keeps what it was asked and reports the active ones.

    Attributes:
        created: Each job description.
        active: What ``get_active_jobs`` answers: the jobs queued and not done.
    """

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.active: list[Any] = []

    def create_job(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        job = SimpleNamespace(
            id=f"job-{len(self.created)}",
            type=kwargs["job_type"],
            metadata=kwargs["metadata"],
            status=SimpleNamespace(value="pending"),
        )
        self.active.append(job)
        return job

    def get_active_jobs(self) -> list[Any]:
        return list(self.active)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> Jobs:
    """Capture the update jobs instead of running them."""
    manager = Jobs()
    monkeypatch.setattr(hooks_module, "get_job_manager", lambda: manager)
    return manager


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    Say which tag the application serves, instead of reading a clone.

    The one place the webhook asks is :func:`lifecycle.deployed_tag`, so this
    stands in for the repository without faking the decision itself.
    """
    state = SimpleNamespace(tag="v1.2.0")
    monkeypatch.setattr(lifecycle, "deployed_tag", lambda app, **kw: state.tag)
    return state


@pytest.fixture
def following(store: NoustStore, seeded: App) -> App:
    """The seeded application, following ``v*`` instead of the ``main`` branch."""
    assert store.set_app_follow_tags(DOMAIN, "v*")
    stored = store.get_app(DOMAIN)
    assert stored is not None
    return stored


def deliver_hook(
    client: TestClient,
    secret: str,
    payload: dict[str, Any],
    *,
    event: str | None = "push",
    delivery: str | None = None,
) -> Any:
    """Post a signed GitHub delivery to the application's own webhook."""
    body = json.dumps(payload).encode()
    headers = github_headers(secret, body, delivery)
    if event is not None:
        headers["X-GitHub-Event"] = event
    return client.post(f"/hooks/deploy/{DOMAIN}", content=body, headers=headers)


def delivery_log(store: NoustStore) -> list[deliveries.WebhookDelivery]:
    app = store.get_app(DOMAIN)
    assert app is not None and app.id is not None
    return deliveries.list_deliveries(app.id)


@pytest.mark.usefixtures("following", "deployed")
class TestAnApplicationsOwnWebhook:
    def test_a_published_release_deploys_exactly_its_tag(
        self, client: TestClient, secret: str, jobs: Jobs, store: NoustStore
    ) -> None:
        response = deliver_hook(client, secret, release(), event="release")

        assert response.status_code == 202, response.text
        assert response.json() == {"job_id": "job-1", "status": "pending"}
        [job] = jobs.created
        assert job["func"] is webhook_update_job
        assert job["kwargs"] == {"domain": DOMAIN, "tag": "v1.3.0"}
        assert job["job_type"] == JobType.UPDATE
        assert job["metadata"]["trigger"] == DeploymentTrigger.WEBHOOK.value
        assert job["metadata"]["tag"] == "v1.3.0"
        assert job["actor"] == "webhook"
        [row] = delivery_log(store)
        assert (row.outcome, row.event, row.job_id) == (
            deliveries.DEPLOY_STARTED,
            "release",
            "job-1",
        )
        assert "v1.3.0" in (row.detail or "")

    @pytest.mark.parametrize(
        ("flags", "word"), [({"prerelease": True}, "pre-release"), ({"draft": True}, "draft")]
    )
    def test_a_prerelease_or_a_draft_is_ignored_with_its_reason(
        self, client: TestClient, secret: str, jobs: Jobs, store: NoustStore, flags: dict, word: str
    ) -> None:
        response = deliver_hook(client, secret, release(**flags), event="release")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ignored"
        assert jobs.created == []
        [row] = delivery_log(store)
        assert row.outcome == deliveries.IGNORED_TAG
        assert word in (row.detail or "")

    def test_a_tag_push_deploys_its_tag(self, client: TestClient, secret: str, jobs: Jobs) -> None:
        response = deliver_hook(client, secret, push("refs/tags/v1.3.0"))

        assert response.status_code == 202, response.text
        assert jobs.created[0]["kwargs"] == {"domain": DOMAIN, "tag": "v1.3.0"}

    def test_a_tag_push_that_names_no_event_is_still_a_push(
        self, client: TestClient, secret: str, jobs: Jobs
    ) -> None:
        """Before 2.2 every forge sent push deliveries with no event header."""
        response = deliver_hook(client, secret, push("refs/tags/v1.3.0"), event=None)

        assert response.status_code == 202, response.text
        assert jobs.created[0]["kwargs"]["tag"] == "v1.3.0"

    def test_a_tag_below_the_deployed_one_is_ignored_and_recorded(
        self,
        client: TestClient,
        secret: str,
        jobs: Jobs,
        store: NoustStore,
        deployed: SimpleNamespace,
        tmp_path: Path,
    ) -> None:
        deployed.tag = "v1.3.0"

        response = deliver_hook(client, secret, push("refs/tags/v1.2.0"))

        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "tag"
        assert jobs.created == []
        [row] = delivery_log(store)
        assert row.outcome == deliveries.IGNORED_TAG
        assert "v1.2.0" in (row.detail or "") and "v1.3.0" in (row.detail or "")
        results = {e["result"] for e in audit_entries(tmp_path) if e["action"] == "hooks.deploy"}
        assert results == {"ignored"}

    def test_the_order_is_semantic_not_alphabetical(
        self, client: TestClient, secret: str, jobs: Jobs, deployed: SimpleNamespace
    ) -> None:
        deployed.tag = "v1.9.0"

        response = deliver_hook(client, secret, push("refs/tags/v1.10.0"))

        assert response.status_code == 202, response.text

    def test_a_tag_the_pattern_does_not_match_is_ignored(
        self, client: TestClient, secret: str, jobs: Jobs, store: NoustStore
    ) -> None:
        response = deliver_hook(client, secret, push("refs/tags/nightly"))

        assert response.status_code == 200, response.text
        assert jobs.created == []
        [row] = delivery_log(store)
        assert row.outcome == deliveries.IGNORED_TAG
        assert "v*" in (row.detail or "")

    def test_the_deployed_tag_announced_again_is_ignored(
        self, client: TestClient, secret: str, jobs: Jobs, store: NoustStore
    ) -> None:
        response = deliver_hook(client, secret, push("refs/tags/v1.2.0"))

        assert response.status_code == 200, response.text
        assert "already deployed" in delivery_log(store)[0].detail  # type: ignore[operator]

    def test_a_push_and_a_release_of_one_version_are_one_deploy(
        self, client: TestClient, secret: str, jobs: Jobs, store: NoustStore
    ) -> None:
        """GitHub sends both when a release is published from a new tag."""
        first = deliver_hook(client, secret, push("refs/tags/v1.3.0"))
        second = deliver_hook(client, secret, release(), event="release")

        assert first.status_code == 202
        assert second.status_code == 200
        assert len(jobs.created) == 1
        assert "already being deployed" in (delivery_log(store)[0].detail or "")

    def test_a_branch_push_deploys_nothing_to_an_application_that_follows_tags(
        self, client: TestClient, secret: str, jobs: Jobs, store: NoustStore
    ) -> None:
        response = deliver_hook(client, secret, push("refs/heads/main"))

        assert response.status_code == 200, response.text
        assert jobs.created == []
        [row] = delivery_log(store)
        assert row.outcome == deliveries.IGNORED_BRANCH
        assert "v*" in (row.detail or "")

    def test_a_gitlab_tag_push_deploys_its_tag(
        self, client: TestClient, secret: str, jobs: Jobs
    ) -> None:
        body = json.dumps({"object_kind": "tag_push", "ref": "refs/tags/v1.3.0"}).encode()
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=body,
            headers={
                "X-Gitlab-Token": secret,
                "X-Gitlab-Event": "Tag Push Hook",
                "X-Gitlab-Event-UUID": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 202, response.text
        assert jobs.created[0]["kwargs"]["tag"] == "v1.3.0"

    def test_a_release_action_that_publishes_nothing_is_ignored(
        self, client: TestClient, secret: str, jobs: Jobs, store: NoustStore
    ) -> None:
        response = deliver_hook(client, secret, release("edited"), event="release")

        assert response.status_code == 200, response.text
        assert jobs.created == []


class TestAnApplicationThatFollowsABranchIsExactlyWhatItWas:
    def test_a_branch_push_still_deploys(self, client: TestClient, secret: str, jobs: Jobs) -> None:
        response = deliver_hook(client, secret, push("refs/heads/main"))

        assert response.status_code == 202, response.text
        assert jobs.created[0]["kwargs"] == {"domain": DOMAIN}

    def test_a_tag_push_is_still_ignored_as_no_branch(
        self, client: TestClient, secret: str, jobs: Jobs
    ) -> None:
        response = deliver_hook(client, secret, push("refs/tags/v9.9.9"))

        assert response.status_code == 200, response.text
        assert jobs.created == []

    def test_a_release_is_still_an_event_that_is_not_acted_on(
        self, client: TestClient, secret: str, jobs: Jobs
    ) -> None:
        response = deliver_hook(client, secret, release(), event="release")

        assert response.status_code == 202, response.text
        assert response.json()["status"] == "ignored"
        assert jobs.created == []


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


class TestTheUpdateJob:
    def test_it_hands_the_tag_to_the_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[dict[str, Any]] = []
        monkeypatch.setattr(hooks_module, "run_update", lambda domain, **kw: seen.append(kw) or {})
        monkeypatch.setattr(hooks_module, "why_tag_is_ignored", lambda app, tag, **kw: None)
        monkeypatch.setattr(
            hooks_module,
            "get_store",
            lambda: SimpleNamespace(get_app=lambda d: SimpleNamespace(domain=d)),
        )

        webhook_update_job(DOMAIN, tag="v1.3.0")

        assert seen == [{"trigger": "webhook", "job_context": None, "tag": "v1.3.0"}]

    def test_a_tag_that_stopped_being_newer_while_it_waited_is_not_deployed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another job deployed a newer tag between the delivery and this job's turn."""
        ran: list[Any] = []
        monkeypatch.setattr(hooks_module, "run_update", lambda domain, **kw: ran.append(kw) or {})
        monkeypatch.setattr(
            hooks_module,
            "why_tag_is_ignored",
            lambda app, tag, **kw: f"{tag} is older than v1.4.0, which is deployed",
        )
        monkeypatch.setattr(
            hooks_module,
            "get_store",
            lambda: SimpleNamespace(get_app=lambda d: SimpleNamespace(domain=d)),
        )

        result = webhook_update_job(DOMAIN, tag="v1.3.0")

        assert ran == []
        assert result["status"] == "ignored" and "v1.4.0" in result["reason"]

    def test_without_a_tag_it_is_the_update_it_always_was(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[dict[str, Any]] = []
        monkeypatch.setattr(hooks_module, "run_update", lambda domain, **kw: seen.append(kw) or {})

        webhook_update_job(DOMAIN)

        assert seen == [{"trigger": "webhook", "job_context": None, "tag": None}]
