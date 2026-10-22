# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Forgejo and Gogs deliver to the deploy webhook (A-07).

Forgejo is a fork of Gitea and sends Gitea's headers as well as its own; Gogs, from which Gitea
was forked, signs the body the same way in ``X-Gogs-Signature`` and names its events in
``X-Gogs-Event``. Both are verified with the application's secret, labelled as what they are in
the delivery log, and read through the one parser of the Gitea family; a pull request of Gogs, which
names its branches as flat fields and gives no commit, still becomes a preview request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.forge_events import (
    Forge,
    PullRequestAction,
    parse_pull_request,
    parse_push_change,
    parse_tag_event,
)
from tests import test_web_hooks as hooks_tests

DOMAIN = hooks_tests.DOMAIN
PUSH_MAIN = hooks_tests.PUSH_MAIN

# The fixtures of the webhook tests, under their own names.
store = hooks_tests.store
seeded = hooks_tests.seeded
secret = hooks_tests.secret
app = hooks_tests.app
client = hooks_tests.client
queued = hooks_tests.queued


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def forgejo_headers(secret: str, body: bytes, *, also_gitea: bool = True) -> dict[str, str]:
    headers = {
        "X-Forgejo-Signature": sign(secret, body),
        "X-Forgejo-Event": "push",
        "X-Forgejo-Delivery": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    if also_gitea:
        # What Forgejo keeps sending for the tools written for Gitea.
        headers.update(
            {
                "X-Gitea-Signature": sign(secret, body),
                "X-Gitea-Event": "push",
                "X-Gitea-Delivery": headers["X-Forgejo-Delivery"],
                "X-GitHub-Event": "push",
            }
        )
    return headers


def gogs_headers(secret: str, body: bytes) -> dict[str, str]:
    delivery = str(uuid.uuid4())
    return {
        "X-Gogs-Signature": sign(secret, body),
        "X-Gogs-Event": "push",
        "X-Gogs-Delivery": delivery,
        # Gogs aliases for GitHub-shaped receivers.
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }


class TestForgejo:
    def test_a_push_signed_with_forgejos_header_queues_an_update(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=forgejo_headers(secret, PUSH_MAIN)
        )

        assert response.status_code == 202, response.text
        assert queued[0]["metadata"]["provider"] == "forgejo"

    def test_forgejo_alone_needs_no_gitea_header(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=PUSH_MAIN,
            headers=forgejo_headers(secret, PUSH_MAIN, also_gitea=False),
        )

        assert response.status_code == 202, response.text
        assert queued[0]["metadata"]["provider"] == "forgejo"

    def test_a_wrong_signature_is_refused_without_details(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        headers = forgejo_headers("not-the-secret", PUSH_MAIN, also_gitea=False)

        response = client.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)

        assert response.status_code == 401
        assert response.json() == {"detail": "Unauthorized"}
        assert queued == []

    def test_a_replayed_delivery_is_a_duplicate(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        headers = forgejo_headers(secret, PUSH_MAIN)

        first = client.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)
        second = client.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)

        assert first.status_code == 202
        assert second.status_code == 200 and len(queued) == 1

    def test_a_branch_the_application_does_not_track_is_ignored(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        body = json.dumps({"ref": "refs/heads/other"}).encode()

        response = client.post(
            f"/hooks/deploy/{DOMAIN}", content=body, headers=forgejo_headers(secret, body)
        )

        assert response.status_code == 200 and queued == []


class TestGogs:
    def test_a_push_signed_with_gogss_header_queues_an_update(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=gogs_headers(secret, PUSH_MAIN)
        )

        assert response.status_code == 202, response.text
        assert queued[0]["metadata"]["provider"] == "gogs"

    def test_a_wrong_signature_is_refused(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=PUSH_MAIN,
            headers=gogs_headers("not-the-secret", PUSH_MAIN),
        )

        assert response.status_code == 401 and queued == []

    def test_a_delivery_with_no_signature_is_refused(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        headers = gogs_headers(secret, PUSH_MAIN)
        del headers["X-Gogs-Signature"]

        response = client.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)

        assert response.status_code == 401 and queued == []

    def test_a_replayed_delivery_is_a_duplicate(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        headers = gogs_headers(secret, PUSH_MAIN)

        client.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)
        again = client.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)

        assert again.status_code == 200 and len(queued) == 1


class TestGiteaKeepsItsName:
    def test_gitea_sends_gogss_header_too_and_is_still_gitea(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        headers = {
            "X-Gitea-Signature": sign(secret, PUSH_MAIN),
            "X-Gogs-Signature": sign(secret, PUSH_MAIN),
            "X-Gitea-Event": "push",
            "X-Gitea-Delivery": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

        client.post(f"/hooks/deploy/{DOMAIN}", content=PUSH_MAIN, headers=headers)

        assert queued[0]["metadata"]["provider"] == "gitea"


def sends_everything(own: str, secret: str, body: bytes) -> dict[str, str]:
    """
    The headers Gitea and Forgejo really send (read off a live server), GitHub's compatible ones
    included: a delivery that carries ``X-Hub-Signature-256`` is still not GitHub's.
    """
    delivery = str(uuid.uuid4())
    headers = {
        "X-Gitea-Signature": sign(secret, body),
        "X-Gogs-Signature": sign(secret, body),
        "X-Hub-Signature": "sha1=" + "0" * 40,
        "X-Hub-Signature-256": "sha256=" + sign(secret, body),
        "X-Gitea-Event": "push",
        "X-Gitea-Delivery": delivery,
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery,
        "Content-Type": "application/json",
    }
    if own == "forgejo":
        headers["X-Forgejo-Signature"] = sign(secret, body)
        headers["X-Forgejo-Event"] = "push"
    return headers


class TestTheRealServersAreNotTakenForGitHub:
    """
    Gitea and Forgejo send GitHub's signature header as well. A delivery labelled ``github`` is a
    different forge to everything after it: its pull requests are refused for lacking GitHub's
    ``author_association``, and a preview's comment goes to GitHub's API instead of the host's.
    """

    @pytest.mark.parametrize("own", ["gitea", "forgejo"])
    def test_a_delivery_is_labelled_by_the_host_that_sent_it(
        self, own: str, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=PUSH_MAIN,
            headers=sends_everything(own, secret, PUSH_MAIN),
        )

        assert response.status_code == 202, response.text
        assert queued[0]["metadata"]["provider"] == own

    def test_a_delivery_that_is_only_githubs_stays_githubs(
        self, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=PUSH_MAIN,
            headers={
                "X-Hub-Signature-256": "sha256=" + sign(secret, PUSH_MAIN),
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 202, response.text
        assert queued[0]["metadata"]["provider"] == "github"

    @pytest.mark.parametrize("own", ["gitea", "forgejo"])
    def test_a_signature_that_is_wrong_in_every_header_is_still_refused(
        self, own: str, client: TestClient, secret: str, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            f"/hooks/deploy/{DOMAIN}",
            content=PUSH_MAIN,
            headers=sends_everything(own, "not-the-secret", PUSH_MAIN),
        )

        assert response.status_code == 401
        assert queued == []


GOGS_PULL_REQUEST: dict[str, Any] = {
    "action": "opened",
    "number": 7,
    "pull_request": {
        "number": 7,
        "title": "Add a page",
        "head_branch": "feature/page",
        "head_repo": {
            "full_name": "acme/shop",
            "clone_url": "https://gogs.example.com/acme/shop.git",
        },
        "base_branch": "main",
        "base_repo": {
            "full_name": "acme/shop",
            "clone_url": "https://gogs.example.com/acme/shop.git",
        },
        "user": {"login": "maria"},
    },
    "repository": {"full_name": "acme/shop"},
    "sender": {"login": "maria"},
}


class TestWhatTheParsersMake:
    def test_a_gogs_pull_request_is_read_from_its_flat_fields(self) -> None:
        event = parse_pull_request("gogs", GOGS_PULL_REQUEST)

        assert event is not None
        assert (event.forge, event.action, event.number) == (
            Forge.GITEA,
            PullRequestAction.OPENED,
            7,
        )
        assert (event.repository, event.branch, event.base_branch) == (
            "acme/shop",
            "feature/page",
            "main",
        )
        assert event.clone_url == "https://gogs.example.com/acme/shop.git"
        assert event.from_fork is False
        assert event.head_sha == ""

    def test_a_gogs_pull_request_from_a_fork_says_so(self) -> None:
        payload = json.loads(json.dumps(GOGS_PULL_REQUEST))
        payload["pull_request"]["head_repo"]["full_name"] = "mallory/shop"

        event = parse_pull_request("gogs", payload)

        assert event is not None and event.from_fork is True

    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            ("synchronized", PullRequestAction.UPDATED),
            ("closed", PullRequestAction.CLOSED),
            ("reopened", PullRequestAction.OPENED),
        ],
    )
    def test_the_actions_a_preview_acts_on(self, action: str, expected: PullRequestAction) -> None:
        event = parse_pull_request("gogs", {**GOGS_PULL_REQUEST, "action": action})

        assert event is not None and event.action is expected

    @pytest.mark.parametrize("action", ["edited", "assigned", "labeled"])
    def test_the_rest_is_not_acted_on(self, action: str) -> None:
        assert parse_pull_request("gogs", {**GOGS_PULL_REQUEST, "action": action}) is None

    def test_a_gogs_pull_request_without_what_an_event_needs_is_nothing(self) -> None:
        broken = json.loads(json.dumps(GOGS_PULL_REQUEST))
        del broken["pull_request"]["head_branch"]

        assert parse_pull_request("gogs", broken) is None

    def test_forgejo_pull_requests_are_gitea_pull_requests(self) -> None:
        payload = {
            "action": "opened",
            "number": 3,
            "pull_request": {
                "number": 3,
                "head": {"ref": "fix", "sha": "abc", "repo": {"full_name": "acme/shop"}},
                "base": {"ref": "main", "repo": {"full_name": "acme/shop"}},
            },
            "repository": {"full_name": "acme/shop"},
        }

        assert parse_pull_request("forgejo", payload) == parse_pull_request("gitea", payload)

    @pytest.mark.parametrize("provider", ["forgejo", "gogs"])
    def test_a_push_lists_its_files_as_gitea_does(self, provider: str) -> None:
        payload = {"commits": [{"message": "m", "added": ["a"], "modified": ["b"], "removed": []}]}

        change = parse_push_change(provider, payload)

        assert change.messages == ("m",)
        assert change.files == ("a", "b")

    @pytest.mark.parametrize("provider", ["forgejo", "gogs"])
    def test_a_pushed_tag_is_a_tag(self, provider: str) -> None:
        payload = {
            "ref": "refs/tags/v1.2.0",
            "after": "abc",
            "repository": {"full_name": "acme/shop", "clone_url": "https://h/acme/shop.git"},
        }

        event = parse_tag_event(provider, "push", payload)

        assert event is not None and (event.tag, event.forge) == ("v1.2.0", Forge.GITEA)
