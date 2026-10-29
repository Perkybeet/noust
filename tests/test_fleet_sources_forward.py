# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central hands the servers that deploy a repository the events GitHub delivered to it.

Real: the central's store, the registry of servers, the aggregator that lists their applications,
the lease keeper and a GitHub that behaves as GitHub documents. Faked: the servers' own API. What is
pinned: the servers found are exactly those with an application that follows the pushed branch (the
rule the servers themselves apply); the event is written down for each before anything else, so a
server that is down when it arrives gets it when it is back; the server holds a fresh lease when it
is handed the event; a delivery that cannot be made says why, in a sentence with a code, and a
server that refuses for good is not asked again; and what the server queued is followed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.forge_events import (
    Forge,
    PullRequestAction,
    PullRequestEvent,
    PushEvent,
    TagEvent,
    event_to_dict,
)
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.sources.follows import Follows
from noust.fleet.sources.forward import GIVE_UP_AFTER, Forwarder
from noust.fleet.sources.forwards import Forwards
from noust.fleet.sources.keeper import LeaseKeeper
from tests.support.fleet_views_support import Central, build_central
from tests.support.github_api_fake import FakeGitHubApi
from tests.support.github_api_fixtures import github_world, record_installation
from tests.support.sources_node import SourcesNode

SHA = "d" * 40


class Time:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1", "web-2"))
    for name in built.nodes:
        FleetSettings(built.store, built.manager.secrets).clear_resync(name)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def github(central: Central):
    with github_world(central.store) as fake:
        fake.install("you", ["app", "api"], installation_id=77)
        record_installation(central.store, fake, 77)
        yield fake


@pytest.fixture
def nodes(central: Central) -> dict[str, SourcesNode]:
    return {name: SourcesNode(node) for name, node in central.nodes.items()}


@pytest.fixture
def time() -> Time:
    return Time()


@pytest.fixture
def forwarder(central: Central, github: FakeGitHubApi, time: Time) -> Forwarder:
    return Forwarder(central.manager, clock=time, keeper=LeaseKeeper(central.manager, clock=time))


def deploy(node: SourcesNode, *apps: tuple[str, str, dict[str, Any]]) -> None:
    node.node.apps = [
        {"domain": domain, "name": domain, "source": source, **fields}
        for domain, source, fields in apps
    ]


def push(branch: str = "main", repository: str = "you/app") -> PushEvent:
    return PushEvent(
        forge=Forge.GITHUB,
        repository=repository,
        clone_url=f"https://github.com/{repository}.git",
        branch=branch,
        head_sha=SHA,
        installation_id=77,
    )


class TestWhichServersDeployIt:
    def test_the_servers_with_an_application_that_follows_the_branch(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        deploy(nodes["web-2"], ("dev.example.com", "github:you/app", {"branch": "dev"}))

        found = forwarder.targets(push("main"), host="github.com", default_branch="main")

        assert found == ["web-1"]

    def test_an_application_with_no_branch_follows_the_default_one(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": None}))

        assert forwarder.targets(push("trunk"), host="github.com", default_branch="trunk") == [
            "web-1"
        ]
        assert forwarder.targets(push("trunk"), host="github.com", default_branch="main") == []

    def test_previews_and_applications_that_follow_tags_are_not_updated_by_a_push(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(
            nodes["web-1"],
            ("pr-1.example.com", "github:you/app", {"branch": "main", "preview_parent": "shop"}),
            ("tagged.example.com", "github:you/app", {"follow_tags": "v*"}),
        )

        assert forwarder.targets(push("main"), host="github.com", default_branch="main") == []

    def test_a_tag_goes_to_the_servers_with_an_application_that_follows_tags(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("tagged.example.com", "github:you/app", {"follow_tags": "v*"}))
        deploy(nodes["web-2"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        tag = TagEvent(
            forge=Forge.GITHUB,
            tag="v1.0.0",
            source="release",
            repository="you/app",
            clone_url="https://github.com/you/app.git",
        )

        assert forwarder.targets(tag, host="github.com", default_branch=None) == ["web-1"]

    def test_a_pull_request_goes_to_every_server_that_deploys_the_repository(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        deploy(nodes["web-2"], ("api.example.com", "github:you/api", {"branch": "main"}))
        pull = PullRequestEvent(
            forge=Forge.GITHUB,
            action=PullRequestAction.OPENED,
            repository="you/app",
            clone_url="https://github.com/you/app.git",
            number=4,
            title="t",
            branch="f",
            base_branch="main",
            head_sha=SHA,
        )

        assert forwarder.targets(pull, host="github.com", default_branch=None) == ["web-1"]

    def test_another_github_host_is_another_set_of_repositories(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(
            nodes["web-1"],
            ("shop.example.com", "https://ghe.example.com/you/app.git", {"branch": "main"}),
        )

        assert forwarder.targets(push(), host="github.com", default_branch="main") == []
        assert forwarder.targets(push(), host="ghe.example.com", default_branch="main") == ["web-1"]

    def test_a_server_made_independent_is_not_handed_anything(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        FleetSettings(central.store, central.manager.secrets).set_mode(
            "web-1", "independent", person="ana"
        )

        assert forwarder.targets(push(), host="github.com", default_branch="main") == []


class TestWritingItDown:
    def test_each_server_is_owed_the_event_before_anything_is_sent(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        deploy(nodes["web-2"], ("shop2.example.com", "github:you/app", {"branch": "main"}))

        owed = forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        assert sorted(f.node for f in owed) == ["web-1", "web-2"]
        assert all(f.state == "pending" and f.attempts == 0 for f in owed)
        assert nodes["web-1"].events == [] and nodes["web-2"].events == []
        stored = Forwards(central.store).get(owed[0].id)
        assert stored is not None and stored.body["event"] == event_to_dict(push())
        assert (stored.kind, stored.repository, stored.ref, stored.commit) == (
            "push",
            "you/app",
            "main",
            SHA,
        )

    def test_nothing_deploys_it_so_nothing_is_owed(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        owed = forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        assert owed == [] and Forwards(central.store).list() == []


class TestHandingItOver:
    def seen(self, node: SourcesNode) -> Any:
        return node.events[0]

    def test_the_server_is_handed_the_event_as_the_central_wrote_it(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        (done,) = forwarder.deliver_due()

        assert done.state == "delivered" and done.attempts == 1
        got = self.seen(nodes["web-1"])
        assert (got.method, got.path, got.scope) == ("POST", "/api/sources/events", "deploy")
        assert got.body == {
            "kind": "push",
            "delivery": "d-1",
            "host": "github.com",
            "default_branch": "main",
            "event": event_to_dict(push()),
        }

    def test_the_actor_the_server_audits_names_the_delivery(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        forwarder.enqueue(push(), delivery="abc-123", host="github.com", default_branch="main")

        forwarder.deliver_due()

        assert self.seen(nodes["web-1"]).actor == "github:abc-123"

    def test_the_server_holds_a_lease_for_the_repository_when_it_is_handed_the_event(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        forwarder.deliver_due()

        (put,) = nodes["web-1"].puts
        assert put.body["repositories"] == ["you/app"]
        paths = [(r.method, r.url.path) for r in nodes["web-1"].node.requests]
        assert paths.index(("PUT", "/api/sources/leases")) < paths.index(
            ("POST", "/api/sources/events")
        )

    def test_a_lease_that_could_not_be_made_does_not_keep_the_event_from_the_server(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        nodes["web-1"].lease_status = 422
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        (done,) = forwarder.deliver_due()

        assert done.state == "delivered" and len(nodes["web-1"].events) == 1

    def test_a_delivery_is_made_once(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        forwarder.deliver_due()
        forwarder.deliver_due()

        assert len(nodes["web-1"].events) == 1

    def test_the_answer_is_kept_with_the_delivery(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        nodes["web-1"].answer = lambda _e: (
            200,
            {"status": "queued", "jobs": [{"domain": "shop.example.com", "job_id": "j1"}]},
        )
        (owed,) = forwarder.enqueue(
            push(), delivery="d-1", host="github.com", default_branch="main"
        )

        forwarder.deliver_due()

        stored = Forwards(central.store).get(owed.id)
        assert stored is not None and stored.answer is not None
        assert stored.answer["jobs"][0]["job_id"] == "j1"
        assert stored.delivered_at is not None


class TestWhenTheServerCannotTakeItNow:
    def owed(self, forwarder: Forwarder, nodes: dict[str, SourcesNode]) -> Any:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        (owed,) = forwarder.enqueue(
            push(), delivery="d-1", host="github.com", default_branch="main"
        )
        return owed

    def test_a_server_that_is_down_gets_it_when_it_is_back(
        self,
        forwarder: Forwarder,
        nodes: dict[str, SourcesNode],
        central: Central,
        time: Time,
    ) -> None:
        owed = self.owed(forwarder, nodes)
        central.nodes["web-1"].down = True

        (tried,) = forwarder.deliver_due()
        assert tried.state == "pending" and tried.attempts == 1
        assert tried.next_attempt_at is not None and tried.next_attempt_at > time.now
        assert tried.error is not None
        assert tried.error["coded"]["code"] == "sources.node_unreachable"

        assert forwarder.deliver_due() == []
        central.nodes["web-1"].down = False
        time.now += 600
        (back,) = forwarder.deliver_due()

        assert back.id == owed.id and back.state == "delivered" and back.attempts == 2
        assert len(nodes["web-1"].events) == 1

    def test_a_full_queue_is_tried_again(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], time: Time
    ) -> None:
        self.owed(forwarder, nodes)
        nodes["web-1"].answer = lambda _e: (429, {"error": "queue_full", "detail": "full"})

        (tried,) = forwarder.deliver_due()

        assert tried.state == "pending"

    def test_it_is_given_up_after_an_hour_and_says_so(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central, time: Time
    ) -> None:
        self.owed(forwarder, nodes)
        central.nodes["web-1"].down = True
        forwarder.deliver_due()

        time.now += GIVE_UP_AFTER + 1
        (gone,) = forwarder.deliver_due()

        assert gone.state == "expired"
        assert gone.error is not None
        assert gone.error["coded"]["code"] == "sources.forward_gave_up"
        assert gone.error["coded"]["params"]["node"] == "web-1"

    def test_a_server_before_4_1_is_not_asked_again(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], time: Time
    ) -> None:
        self.owed(forwarder, nodes)
        nodes["web-1"].answer = lambda _e: (404, {"error": "not_found"})

        (tried,) = forwarder.deliver_due()

        assert tried.state == "failed"
        assert (
            tried.error is not None and tried.error["coded"]["code"] == "sources.node_unsupported"
        )
        time.now += 1000
        assert forwarder.deliver_due() == []

    def test_a_server_that_lets_its_central_only_read_is_not_asked_again(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        self.owed(forwarder, nodes)
        nodes["web-1"].ceiling = "read"

        (tried,) = forwarder.deliver_due()

        assert tried.state == "failed"
        assert tried.error is not None and tried.error["coded"]["code"] == "sources.node_ceiling"

    def test_what_a_server_refuses_is_said_in_its_own_words(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode]
    ) -> None:
        self.owed(forwarder, nodes)
        nodes["web-1"].answer = lambda _e: (
            400,
            {"error": "validation_error", "detail": "A forwarded event has a wrong branch"},
        )

        (tried,) = forwarder.deliver_due()

        assert tried.state == "failed"
        assert tried.error is not None
        assert tried.error["coded"]["code"] == "sources.forward_refused"
        assert "wrong branch" in str(tried.error["coded"]["params"]["reason"])

    def test_a_server_that_became_independent_after_the_event_arrived_is_left_alone(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        self.owed(forwarder, nodes)
        FleetSettings(central.store, central.manager.secrets).set_mode(
            "web-1", "independent", person="ana"
        )

        (tried,) = forwarder.deliver_due()

        assert tried.state == "failed"
        assert (
            tried.error is not None and tried.error["coded"]["code"] == "sources.node_independent"
        )
        assert nodes["web-1"].events == []

    def test_one_server_failing_does_not_stop_the_other(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        deploy(nodes["web-2"], ("shop2.example.com", "github:you/app", {"branch": "main"}))
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")
        central.nodes["web-1"].down = True

        done = {f.node: f.state for f in forwarder.deliver_due()}

        assert done == {"web-1": "pending", "web-2": "delivered"}


class TestWhenAServerLeavesTheFleet:
    def test_what_the_central_kept_about_it_goes_with_it(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        from noust.fleet.sources.grants import LeaseGrants

        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        deploy(nodes["web-2"], ("shop2.example.com", "github:you/app", {"branch": "main"}))
        nodes["web-1"].answer = lambda _e: (
            200,
            {"status": "queued", "jobs": [{"domain": "shop.example.com", "job_id": "j1"}]},
        )
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")
        forwarder.deliver_due()
        central.nodes["web-2"].down = True
        forwarder.enqueue(push(), delivery="d-2", host="github.com", default_branch="main")

        central.manager.remove("web-1", revoke=False)

        assert LeaseGrants(central.store).list("web-1") == []
        assert [f for f in Forwards(central.store).list() if f.node == "web-1"] == []
        assert Follows(central.store).list(node="web-1") == []
        # The server that stays keeps all of it.
        assert LeaseGrants(central.store).list("web-2") != []
        assert [f.node for f in Forwards(central.store).list()] == ["web-2", "web-2"]


class TestWhatTheServerQueuedIsFollowed:
    def test_each_update_it_queued_is_followed(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        nodes["web-1"].answer = lambda _e: (
            200,
            {
                "status": "queued",
                "kind": "push",
                "jobs": [{"domain": "shop.example.com", "job_id": "j1", "merged": False}],
            },
        )
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        forwarder.deliver_due()

        (follow,) = Follows(central.store).active()
        assert (follow.node, follow.domain, follow.kind, follow.job_id) == (
            "web-1",
            "shop.example.com",
            "deployment",
            "j1",
        )
        assert follow.repository == "you/app" and follow.installation_id == 77
        assert follow.ref == SHA
        assert follow.environment_url == "https://shop.example.com"

    def test_the_preview_of_a_pull_request_is_followed_and_keeps_its_comment(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        nodes["web-1"].answer = lambda _e: (
            200,
            {
                "status": "queued",
                "kind": "pull_request",
                "previews": [{"parent": "shop.example.com", "number": 4, "action": "opened"}],
                "job_ids": ["jp"],
            },
        )
        pull = PullRequestEvent(
            forge=Forge.GITHUB,
            action=PullRequestAction.OPENED,
            repository="you/app",
            clone_url="https://github.com/you/app.git",
            number=4,
            title="t",
            branch="f",
            base_branch="main",
            head_sha=SHA,
            installation_id=77,
        )
        forwarder.enqueue(pull, delivery="d-1", host="github.com", default_branch=None)
        forwarder.deliver_due()
        (first,) = Follows(central.store).active()
        Follows(central.store).remember(first.id, comment_ref="55")

        forwarder.enqueue(pull, delivery="d-2", host="github.com", default_branch=None)
        forwarder.deliver_due()

        (again,) = Follows(central.store).active()
        assert again.id == first.id and again.comment_ref == "55"
        assert (again.kind, again.domain, again.number, again.action) == (
            "preview",
            "shop.example.com",
            4,
            "opened",
        )

    def test_a_central_with_no_app_follows_nothing(
        self, forwarder: Forwarder, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], ("shop.example.com", "github:you/app", {"branch": "main"}))
        nodes["web-1"].answer = lambda _e: (
            200,
            {"status": "queued", "jobs": [{"domain": "shop.example.com", "job_id": "j1"}]},
        )
        central.store.delete_github_app()
        forwarder.enqueue(push(), delivery="d-1", host="github.com", default_branch="main")

        forwarder.deliver_due()

        assert Follows(central.store).active() == []
