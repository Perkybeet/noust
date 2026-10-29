# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central leases each server the GitHub access to clone what it serves, and keeps it fresh.

Real: the central's store, the registry of servers, the aggregator that reads each server's
applications, and a GitHub that behaves as GitHub documents. Faked: the servers' own API. What is
pinned: a server gets one token scoped to the repositories of its applications and to reading, and
nothing else is ever sent to it; the lease is renewed before it ends and not before; it follows the
applications (a new one widens it at once, one that is gone narrows it); a server that cannot take
it says why in a sentence with a code and does not stop the others; GitHub refusing one repository
does not leave the others without; and no token is ever in what the central reports or records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.sources.grants import LeaseGrants
from noust.fleet.sources.keeper import RENEW_BEFORE, LeaseKeeper
from tests.support.fleet_views_support import Central, build_central
from tests.support.github_api_fake import FakeGitHubApi
from tests.support.github_api_fixtures import github_world, record_installation
from tests.support.sources_node import SourcesNode


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
        fake.install("you", ["app", "api", "secret"], installation_id=77)
        record_installation(central.store, fake, 77)
        yield fake


@pytest.fixture
def nodes(central: Central) -> dict[str, SourcesNode]:
    return {name: SourcesNode(node) for name, node in central.nodes.items()}


@pytest.fixture
def time() -> Time:
    return Time()


@pytest.fixture
def keeper(central: Central, github: FakeGitHubApi, time: Time) -> LeaseKeeper:
    return LeaseKeeper(central.manager, clock=time)


def deploy(node: SourcesNode, *sources: str) -> None:
    node.node.apps = [
        {"domain": f"app{n}.example.com", "name": f"app{n}", "source": source}
        for n, source in enumerate(sources)
    ]


def tokens(github: FakeGitHubApi) -> list[Any]:
    return github.bodies("POST", r"/app/installations/\d+/access_tokens")


class TestLeasingWhatAServerServes:
    def test_a_server_gets_a_lease_for_the_repositories_of_its_applications_and_nothing_else(
        self,
        keeper: LeaseKeeper,
        nodes: dict[str, SourcesNode],
        github: FakeGitHubApi,
        time: Time,
    ) -> None:
        deploy(nodes["web-1"], "github:you/app", "https://github.com/you/api.git#dev", "/srv/x")

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "leased"
        (put,) = nodes["web-1"].puts
        assert put.method == "PUT" and put.path == "/api/sources/leases"
        assert put.scope == "deploy"
        body = put.body
        assert body["repositories"] == ["you/api", "you/app"]
        assert body["host"] == "github.com" and body["installation_id"] == 77
        assert body["permissions"] == {"contents": "read"}
        assert 3400 < body["expires_in"] <= 3600
        assert tokens(github) == [
            {"repositories": ["api", "app"], "permissions": {"contents": "read"}}
        ]

    def test_the_token_the_server_is_handed_reaches_only_those_repositories_and_reads(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")

        keeper.sweep(["web-1"])

        handed = nodes["web-1"].puts[0].body["token"]
        issued = github.tokens[handed]
        assert issued.repositories == frozenset({"app"})
        assert issued.permissions == {"contents": "read", "metadata": "read"}

    def test_each_server_gets_its_own_token_for_its_own_repositories(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        deploy(nodes["web-2"], "github:you/api")

        keeper.sweep()

        one, two = (nodes[n].puts[0].body for n in ("web-1", "web-2"))
        assert one["token"] != two["token"]
        assert one["repositories"] == ["you/app"] and two["repositories"] == ["you/api"]

    def test_nothing_is_asked_of_github_for_applications_that_are_not_on_it(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "https://gitlab.com/you/app.git", "/srv/x", "docker://nginx")

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "none" and report.leases == []
        assert tokens(github) == [] and nodes["web-1"].puts == []

    def test_the_token_is_in_the_request_to_the_server_and_in_nothing_the_central_keeps(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")

        (report,) = keeper.sweep(["web-1"])

        token = nodes["web-1"].puts[0].body["token"]
        assert token not in str(report.to_dict())
        rows = central.store._get_connection().execute("SELECT * FROM source_lease_grants")
        assert token not in " ".join(str(v) for row in rows.fetchall() for v in tuple(row))
        assert token not in repr(keeper.reports())


class TestKeepingItFresh:
    def test_a_lease_far_from_its_end_is_left_alone(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi, time: Time
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        keeper.sweep(["web-1"])

        time.now += 600
        (report,) = keeper.sweep(["web-1"])

        assert report.state == "leased" and report.leases[0].state == "current"
        assert len(tokens(github)) == 1 and len(nodes["web-1"].puts) == 1

    def test_it_is_renewed_before_it_ends_and_not_before(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi, time: Time
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        keeper.sweep(["web-1"])
        first = nodes["web-1"].puts[0].body["token"]
        lives = nodes["web-1"].puts[0].body["expires_in"]

        time.now += lives - RENEW_BEFORE - 5
        keeper.sweep(["web-1"])
        assert len(nodes["web-1"].puts) == 1

        time.now += 10
        (report,) = keeper.sweep(["web-1"])

        assert report.leases[0].state == "issued"
        assert len(nodes["web-1"].puts) == 2
        second = nodes["web-1"].puts[1].body
        assert second["key"] == nodes["web-1"].puts[0].body["key"]
        assert second["token"] != first

    def test_a_new_application_widens_the_lease_at_once(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        keeper.sweep(["web-1"])

        deploy(nodes["web-1"], "github:you/app", "github:you/api")
        keeper.sweep(["web-1"], force=False)

        assert nodes["web-1"].puts[-1].body["repositories"] == ["you/api", "you/app"]
        assert len(nodes["web-1"].leases) == 1

    def test_an_application_that_is_gone_narrows_it(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], "github:you/app", "github:you/api")
        keeper.sweep(["web-1"])

        deploy(nodes["web-1"], "github:you/api")
        keeper.sweep(["web-1"])

        assert nodes["web-1"].puts[-1].body["repositories"] == ["you/api"]

    def test_the_last_application_going_lets_the_lease_go_on_the_server_too(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        keeper.sweep(["web-1"])
        key = nodes["web-1"].puts[0].body["key"]

        deploy(nodes["web-1"], "/srv/x")
        (report,) = keeper.sweep(["web-1"])

        assert report.state == "none"
        assert nodes["web-1"].deletes == [key]
        assert LeaseGrants(central.store).list("web-1") == []

    def test_force_renews_what_is_not_due(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        keeper.sweep(["web-1"])

        keeper.sweep(["web-1"], force=True)

        assert len(nodes["web-1"].puts) == 2

    def test_two_accounts_are_two_leases(
        self,
        keeper: LeaseKeeper,
        nodes: dict[str, SourcesNode],
        github: FakeGitHubApi,
        central: Central,
    ) -> None:
        github.install("acme", ["site"], installation_id=88)
        record_installation(central.store, github, 88)
        deploy(nodes["web-1"], "github:you/app", "github:acme/site")

        (report,) = keeper.sweep(["web-1"])

        assert sorted(lease.installation_id for lease in report.leases) == [77, 88]
        assert {p.body["key"] for p in nodes["web-1"].puts} == {
            "github.com:77:0",
            "github.com:88:0",
        }

    def test_a_server_that_serves_more_than_a_token_can_hold_gets_several(
        self,
        keeper: LeaseKeeper,
        nodes: dict[str, SourcesNode],
        github: FakeGitHubApi,
        central: Central,
    ) -> None:
        github.install("big", [f"r{n:03d}" for n in range(520)], installation_id=99)
        record_installation(central.store, github, 99)
        deploy(nodes["web-1"], *(f"github:big/r{n:03d}" for n in range(520)))

        (report,) = keeper.sweep(["web-1"])

        sizes = sorted(len(p.body["repositories"]) for p in nodes["web-1"].puts)
        assert sizes == [20, 500]
        assert report.state == "leased"


class TestWhenAServerCannotTakeIt:
    def test_an_independent_server_is_left_alone(
        self,
        keeper: LeaseKeeper,
        nodes: dict[str, SourcesNode],
        github: FakeGitHubApi,
        central: Central,
    ) -> None:
        FleetSettings(central.store, central.manager.secrets).set_mode(
            "web-1", "independent", person="ana"
        )
        deploy(nodes["web-1"], "github:you/app")

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "independent"
        assert tokens(github) == [] and nodes["web-1"].puts == []

    def test_a_server_before_4_1_is_said_to_be_one(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        nodes["web-1"].lease_status = 404

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "unsupported"
        assert report.error is not None and report.error.sentence.code == "sources.node_unsupported"

    def test_a_server_that_lets_its_central_only_read_says_so_with_the_way_out(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        nodes["web-1"].ceiling = "read"

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "ceiling"
        assert report.error is not None
        assert report.error.sentence.code == "sources.node_ceiling"
        assert "noust fleet access --level deploy" in str(report.error.sentence.params.values())

    def test_a_server_that_is_down_is_tried_again_and_does_not_stop_the_others(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], central: Central
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        deploy(nodes["web-2"], "github:you/api")
        central.nodes["web-1"].down = True

        reports = {r.node: r for r in keeper.sweep()}

        assert reports["web-1"].state == "unreachable"
        assert reports["web-2"].state == "leased"

    def test_what_a_server_refuses_is_said_in_its_own_words(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode]
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        nodes["web-1"].lease_status = 422

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "failed"
        assert report.error is not None
        assert report.error.sentence.code == "sources.node_refused"
        assert "no" in str(report.error.sentence.params["reason"])

    def test_a_failure_keeps_the_end_of_the_lease_the_server_still_holds(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], central: Central, time: Time
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        keeper.sweep(["web-1"])
        held = LeaseGrants(central.store).list("web-1")[0].expires_at

        nodes["web-1"].lease_status = 422
        time.now += 3000
        keeper.sweep(["web-1"])

        grant = LeaseGrants(central.store).list("web-1")[0]
        assert grant.state == "failed" and grant.expires_at == held
        assert grant.error is not None

    def test_a_failure_is_not_tried_again_at_every_look(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi, time: Time
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        nodes["web-1"].lease_status = 422
        keeper.sweep(["web-1"])
        before = len(tokens(github))

        time.now += 30
        keeper.sweep(["web-1"])
        assert len(tokens(github)) == before

        time.now += 400
        keeper.sweep(["web-1"])
        assert len(tokens(github)) == before + 1


class TestWhenGitHubCannotReachAll:
    def test_a_repository_the_app_cannot_read_does_not_leave_the_others_without(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:you/app", "github:you/ghost", "github:you/api")

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "leased"
        assert nodes["web-1"].puts[-1].body["repositories"] == ["you/api", "you/app"]
        assert report.uncovered == ("you/ghost",)
        assert report.problems and report.problems[0].sentence.code == "sources.not_reachable"
        assert report.problems[0].sentence.params["repository"] == "you/ghost"

    def test_a_repository_it_could_not_read_is_not_asked_for_again_at_every_look(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi, time: Time
    ) -> None:
        deploy(nodes["web-1"], "github:you/app", "github:you/ghost")
        keeper.sweep(["web-1"])
        asked = len(tokens(github))

        time.now += 60
        keeper.sweep(["web-1"])

        assert len(tokens(github)) == asked

    def test_an_owner_with_no_installation_is_said_so(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:stranger/thing", "github:you/app")

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "leased"
        assert report.uncovered == ("stranger/thing",)
        assert report.problems[0].sentence.code == "sources.no_installation"

    def test_github_refusing_the_app_is_said_in_githubs_words(
        self, keeper: LeaseKeeper, nodes: dict[str, SourcesNode], github: FakeGitHubApi
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")
        github.installations.clear()

        (report,) = keeper.sweep(["web-1"])

        assert report.state == "failed"
        assert report.error is not None
        assert report.error.sentence.code == "sources.github_refused"
        assert "Not Found" in (report.error.output or "")


class TestWithoutAnApp:
    def test_a_central_with_no_app_leases_nothing(
        self, central: Central, nodes: dict[str, SourcesNode], time: Time
    ) -> None:
        deploy(nodes["web-1"], "github:you/app")

        reports = LeaseKeeper(central.manager, clock=time).sweep()

        assert [r.state for r in reports] == []
        assert nodes["web-1"].puts == []
