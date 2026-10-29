# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central writes the status of the commit and the comment of the pull request.

Real: the central's store, the registry of servers, and a GitHub that behaves as GitHub documents
(a deployment needs its permission, a comment too). Faked: the servers' own API, which says how a
job and a preview stand. What is pinned: GitHub's deployment goes from queued to in progress to the
state the deployment ended in, each said once, in the words the server's own deployments are
reported with; a server that stays away is given up on after three hours and GitHub is told, not
left saying "in progress"; a preview has one comment, edited as the preview changes and never
repeated; and a refusal of GitHub loses nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.sources.follow import LOST_AFTER, Follower
from noust.fleet.sources.follows import Follow, Follows
from tests.support.fleet_views_support import Central, build_central
from tests.support.github_api_fake import FakeGitHubApi
from tests.support.github_api_fixtures import github_world, record_installation
from tests.support.sources_node import SourcesNode

SHA = "e" * 40


class Time:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1",))
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def github(central: Central):
    with github_world(central.store) as fake:
        fake.install("you", ["app"], installation_id=77)
        record_installation(central.store, fake, 77)
        yield fake


@pytest.fixture
def node(central: Central) -> SourcesNode:
    return SourcesNode(central.nodes["web-1"])


@pytest.fixture
def time() -> Time:
    return Time()


@pytest.fixture
def follower(central: Central, github: FakeGitHubApi, time: Time) -> Follower:
    return Follower(central.manager, clock=time)


@pytest.fixture
def follows(central: Central, time: Time) -> Follows:
    return Follows(central.store, time)


def updating(follows: Follows, job: str = "j1") -> Follow:
    return follows.deployment(
        "f1",
        "web-1",
        "shop.example.com",
        job,
        repository="you/app",
        installation_id=77,
        ref=SHA,
        environment_url="https://shop.example.com",
    )


def states(github: FakeGitHubApi) -> list[tuple[str, str]]:
    """The state and description of every status written, oldest first."""
    return [
        (status["state"], status.get("description", ""))
        for (_, _), statuses in github.deployment_statuses.items()
        for status in statuses
    ]


class TestADeployment:
    def test_it_is_queued_then_in_progress_then_what_it_ended_in(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "queued")
        assert [r.outcome for r in follower.step()] == ["reported"]
        node.job("j1", "running")
        assert [r.outcome for r in follower.step()] == ["reported"]
        node.job("j1", "succeeded", deployment_id=9)
        node.deployment(9, status="success")
        (ended,) = follower.step()

        assert ended.outcome == "done" and ended.said == "success"
        assert states(github) == [
            ("queued", "Waiting its turn"),
            ("in_progress", "Deploying"),
            ("success", "Deployed"),
        ]
        (deployment,) = github.deployments["you/app"]
        assert deployment["ref"] == SHA and deployment["environment"] == "production"
        assert follows.active() == []

    def test_each_state_is_said_once(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "running")

        for _ in range(4):
            follower.step()

        assert states(github) == [("in_progress", "Deploying")]
        assert len(github.deployments["you/app"]) == 1

    def test_the_success_carries_where_the_application_answers(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "succeeded", deployment_id=9)
        node.deployment(9, status="success")

        follower.step()

        (status,) = next(iter(github.deployment_statuses.values()))[-1:]
        assert status["environment_url"] == "https://shop.example.com"

    def test_a_failure_says_the_first_line_of_why(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "failed", deployment_id=9, error="npm run build failed\nsecond line")
        node.deployment(9, status="failed", error="npm run build failed\nsecond line")

        (ended,) = follower.step()

        assert ended.outcome == "done"
        assert states(github) == [("failure", "npm run build failed")]

    def test_a_deployment_that_went_back_says_so(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "failed", deployment_id=9, error="health gate failed")
        node.deployment(9, status="rolled_back", error="health gate failed")

        follower.step()

        assert states(github) == [("failure", "rolled back")]

    def test_one_an_operator_cancelled_never_ran_and_is_not_a_failure(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "cancelled")

        follower.step()

        assert states(github) == [("inactive", "Cancelled")]

    def test_an_update_that_deployed_nothing_new_is_a_success(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "succeeded")

        follower.step()

        assert states(github) == [("success", "Deployed")]

    def test_the_statuses_are_written_with_the_centrals_own_token_not_a_lease(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "running")

        follower.step()

        wrote = next(r for r in github.requests if r.path.endswith("/statuses"))
        assert github.tokens[wrote.headers["authorization"].split()[1]].repositories is None


class TestAServerThatIsNotThere:
    def test_a_server_that_does_not_answer_is_looked_at_again(
        self, follower: Follower, follows: Follows, node: SourcesNode, central: Central
    ) -> None:
        follow = updating(follows)
        central.nodes["web-1"].down = True

        (first,) = follower.step()

        assert first.outcome == "waiting"
        assert [f.id for f in follows.active()] == [follow.id]

    def test_after_three_hours_github_is_told_it_was_lost(
        self,
        follower: Follower,
        follows: Follows,
        node: SourcesNode,
        central: Central,
        github: FakeGitHubApi,
        time: Time,
    ) -> None:
        updating(follows)
        node.job("j1", "running")
        follower.step()
        central.nodes["web-1"].down = True

        time.now += LOST_AFTER + 1
        (lost,) = follower.step()

        assert lost.outcome == "lost"
        assert states(github)[-1] == ("error", "Noust lost track of this deployment")
        assert follows.active() == []

    def test_a_job_the_server_no_longer_has_is_waited_for_and_then_lost(
        self, follower: Follower, follows: Follows, node: SourcesNode, time: Time
    ) -> None:
        updating(follows, job="gone")

        assert [r.outcome for r in follower.step()] == ["waiting"]
        time.now += LOST_AFTER + 1
        assert [r.outcome for r in follower.step()] == ["lost"]

    def test_a_server_no_longer_in_the_fleet_is_waited_for_and_then_lost(
        self, follower: Follower, follows: Follows, node: SourcesNode, central: Central, time: Time
    ) -> None:
        updating(follows)
        central.store.delete_node("web-1")

        assert [r.outcome for r in follower.step()] == ["waiting"]
        time.now += LOST_AFTER + 1
        assert [r.outcome for r in follower.step()] == ["lost"]


class TestWhenGitHubRefuses:
    def test_nothing_is_lost_and_the_next_look_says_it(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        updating(follows)
        node.job("j1", "running")
        github.installations[77].permissions = {"metadata": "read"}

        (refused,) = follower.step()
        assert refused.outcome == "waiting"
        assert [f.state for f in follows.active()] == ["active"]

        github.installations[77].permissions = {
            "metadata": "read",
            "deployments": "write",
            "contents": "read",
        }
        from noust.integrations.github import app as github_app

        github_app.forget_tokens()
        (said,) = follower.step()
        assert said.outcome == "reported"
        assert states(github) == [("in_progress", "Deploying")]


def previewing(follows: Follows, action: str = "opened") -> Follow:
    return follows.preview(
        "f1",
        "web-1",
        "shop.example.com",
        4,
        repository="you/app",
        installation_id=77,
        ref=SHA,
        action=action,
    )


def preview(status: str, **fields: Any) -> dict[str, Any]:
    return {
        "domain": "pr-4-shop.example.com",
        "number": 4,
        "branch": "feature/x",
        "head_sha": SHA,
        "status": status,
        "expires_at": "2026-11-05T10:00:00+00:00",
        **fields,
    }


class TestAPreview:
    def test_one_comment_edited_as_the_preview_changes(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        previewing(follows)
        node.previews["shop.example.com"] = [preview("deploying")]
        (first,) = follower.step()
        assert first.outcome == "reported" and first.said == "deploying"
        (comment_id,) = github.comments["you/app"]
        assert "Building" in github.comments["you/app"][comment_id]

        node.previews["shop.example.com"] = [preview("ready")]
        (ready,) = follower.step()

        assert ready.outcome == "done"
        assert list(github.comments["you/app"]) == [comment_id]
        body = github.comments["you/app"][comment_id]
        assert "Ready" in body and "https://pr-4-shop.example.com" in body and SHA[:12] in body
        assert follows.active() == []

    def test_the_text_is_the_one_a_server_with_its_own_app_writes(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        from noust.core.store import PreviewRecord
        from noust.managers.previews import preview_comment

        previewing(follows)
        node.previews["shop.example.com"] = [preview("failed")]

        follower.step()

        (body,) = github.comments["you/app"].values()
        assert body == preview_comment(
            PreviewRecord(
                parent_domain="shop.example.com",
                domain="pr-4-shop.example.com",
                number=4,
                branch="feature/x",
                provider="github",
                expires_at="2026-11-05T10:00:00+00:00",
                head_sha=SHA,
                status="failed",
            )
        )
        assert "The build failed" in body

    def test_a_comment_is_not_said_twice(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        previewing(follows)
        node.previews["shop.example.com"] = [preview("deploying")]

        for _ in range(3):
            follower.step()

        assert len(github.bodies("POST", r"/repos/you/app/issues/4/comments")) == 1
        assert github.bodies("PATCH", r"/repos/you/app/issues/comments/\d+") == []

    def test_a_commit_that_waits_for_an_operator_is_said_and_followed_no_more(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        previewing(follows)
        node.previews["shop.example.com"] = [preview("awaiting")]

        (done,) = follower.step()

        assert done.outcome == "done"
        assert "Waiting for approval" in next(iter(github.comments["you/app"].values()))

    def test_a_push_to_the_pull_request_edits_the_same_comment(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        previewing(follows)
        node.previews["shop.example.com"] = [preview("ready")]
        follower.step()
        (comment_id,) = github.comments["you/app"]

        previewing(follows, "updated")
        node.previews["shop.example.com"] = [preview("deploying", head_sha="f" * 40)]
        follower.step()

        assert list(github.comments["you/app"]) == [comment_id]
        assert "Building" in github.comments["you/app"][comment_id]

    def test_closing_the_pull_request_says_the_preview_was_removed(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        previewing(follows)
        node.previews["shop.example.com"] = [preview("ready")]
        follower.step()
        (comment_id,) = github.comments["you/app"]

        previewing(follows, "closed")
        assert [r.outcome for r in follower.step()] == ["waiting"]
        node.previews["shop.example.com"] = []
        (done,) = follower.step()

        assert done.outcome == "done"
        assert "the pull request was closed" in github.comments["you/app"][comment_id]

    def test_closing_a_pull_request_that_never_had_a_preview_says_nothing(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        previewing(follows, "closed")
        node.previews["shop.example.com"] = []

        (done,) = follower.step()

        assert done.outcome == "done" and github.comments == {}

    def test_a_pull_request_nobody_built_a_preview_for_is_followed_no_more(
        self, follower: Follower, follows: Follows, node: SourcesNode, github: FakeGitHubApi
    ) -> None:
        previewing(follows)
        node.previews["shop.example.com"] = []

        (done,) = follower.step()

        assert done.outcome == "done" and github.comments == {}
