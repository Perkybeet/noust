# Copyright (c) 2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Rolling an application out over the servers it runs on (checklist F-18).

The guarantee being sold is "the same commit everywhere": the first server builds the head of the
branch alone, and the commit it ends on is what every other server is asked for, so a push that
lands in the middle of the rollout reaches none of them. When the rollout stops, the servers
already moved are put back by their previous release (instant, health-gated, and refused by the
server if it would pass a schema change), and what could not be put back is said, not hidden.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import Asker, set_aggregator
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetJobs, FleetRequest, Runner, plan, retry_request
from tests.fleet_views_support import SCHEMA_30, Central, build_central
from tests.placement_support import DOMAIN, Shop, Upstream

ADMIN = Asker(actor="ana", scope="admin", role="admin", elevated=True)
NAMES = ("web-1", "web-2", "web-3")


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    built = build_central(tmp_path, NAMES)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def upstream() -> Upstream:
    return Upstream(head="aaa1111")


@pytest.fixture
def shops(central: Central, upstream: Upstream) -> dict[str, Shop]:
    return {name: Shop(central.nodes[name], upstream) for name in NAMES}


class Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.snapshots: list[dict[str, Any]] = []

    def log(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        self.snapshots.append(snapshot)


def request(**overrides: Any) -> FleetRequest:
    options = {"domain": DOMAIN, **overrides.pop("options", {})}
    values: dict[str, Any] = {
        "action": "placement_rollout",
        "nodes": list(NAMES),
        "options": options,
    }
    values.update(overrides)
    return FleetRequest(**values)


def run(central: Central, fleet_request: FleetRequest) -> tuple[dict[str, Any], Recorder, str]:
    the_plan = plan(fleet_request, manager=central.manager, asker=ADMIN)
    recorder = Recorder()
    jobs = FleetJobs(central.store)
    job_id = fleet_jobs.new_job_id()
    jobs.create(job_id, the_plan, created_by="ana")
    snapshot = Runner(
        plan=the_plan,
        job_id=job_id,
        manager=central.manager,
        asker=ADMIN,
        reporter=recorder,
        elevated_until=float("inf"),
        jobs=jobs,
        poll=0,
        sleep=lambda seconds: None,
    ).run()
    return snapshot, recorder, job_id


def states(snapshot: dict[str, Any]) -> dict[str, str]:
    return {node["node"]: node["state"] for node in snapshot["nodes"]}


class TestThePlan:
    def test_the_first_server_goes_alone_and_the_plan_says_why(self, central, shops) -> None:
        described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

        assert described["batches"][0] == ["web-1"]
        assert described["strategy"]["canary"] == "web-1"
        assert any("commit it ends on" in note for note in described["notes"])

    def test_a_pinned_commit_needs_no_first_server(self, central, shops) -> None:
        described = plan(
            request(options={"commit": "bbb2222"}, serial=3), manager=central.manager, asker=ADMIN
        ).to_dict()

        assert described["batches"] == [["web-1", "web-2", "web-3"]]
        assert described["strategy"]["canary"] is None

    def test_the_canary_that_was_chosen_is_the_first_server(self, central, shops) -> None:
        described = plan(request(canary="web-3"), manager=central.manager, asker=ADMIN).to_dict()

        assert described["batches"][0] == ["web-3"]

    def test_each_server_one_at_a_time_unless_told_otherwise(self, central, shops) -> None:
        described = plan(request(), manager=central.manager, asker=ADMIN)

        assert described.serial == 1
        assert described.max_failures == 0
        assert described.to_dict()["batches"] == [["web-1"], ["web-2"], ["web-3"]]

    def test_a_server_that_does_not_have_the_application_is_a_failure_before_anything_starts(
        self, central, shops
    ) -> None:
        central.nodes["web-3"].handlers[("GET", "/api/apps")] = lambda r: httpx.Response(
            200, json={"apps": [], "total": 0}
        )

        with pytest.raises(ValidationError) as caught:
            plan(request(), manager=central.manager, asker=ADMIN)

        assert "not installed on web-3" in caught.value.message
        assert "placement copy" in caught.value.details

    def test_a_server_older_than_the_feature_is_skipped_as_unsupported(
        self, central, shops
    ) -> None:
        central.nodes["web-2"].schema = dict(SCHEMA_30)

        described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

        web2 = next(n for n in described["nodes"] if n["node"] == "web-2")
        assert (web2["state"], web2["reason"]) == ("skipped", "unsupported")

    def test_a_server_already_on_the_pinned_commit_has_nothing_to_do(self, central, shops) -> None:
        shops["web-2"].commit = "bbb2222"
        shops["web-2"].node.apps = [shops["web-2"].row()]

        described = plan(
            request(options={"commit": "bbb2222"}), manager=central.manager, asker=ADMIN
        ).to_dict()

        web2 = next(n for n in described["nodes"] if n["node"] == "web-2")
        assert (web2["state"], web2["reason"]) == ("skipped", "not_needed")
        assert "already on bbb2222" in web2["step"]

    def test_a_commit_and_a_tag_both_name_what_to_deploy(self, central, shops) -> None:
        with pytest.raises(ValidationError) as caught:
            plan(
                request(options={"commit": "bbb2222", "tag": "v2"}),
                manager=central.manager,
                asker=ADMIN,
            )
        assert "commit" in caught.value.message.lower()

    def test_the_domain_is_required_and_is_a_domain(self, central, shops) -> None:
        for options in ({}, {"domain": "https://shop.example.com"}, {"domain": 3}):
            with pytest.raises(ValidationError):
                plan(
                    FleetRequest("placement_rollout", nodes=["web-1"], options=options),
                    manager=central.manager,
                    asker=ADMIN,
                )

    def test_a_commit_is_hex_and_short_or_full(self, central, shops) -> None:
        for bad in ("--upload-pack=x", "main", "abc", "g" * 40, "a" * 41):
            with pytest.raises(ValidationError):
                plan(request(options={"commit": bad}), manager=central.manager, asker=ADMIN)


class TestTheSameCommitEverywhere:
    def test_the_commit_the_first_server_ends_on_is_what_the_others_are_asked_for(
        self, central, shops, upstream
    ) -> None:
        upstream.head = "bbb2222"

        snapshot, _, _ = run(central, request())

        assert snapshot["status"] == "succeeded"
        assert states(snapshot) == dict.fromkeys(NAMES, "succeeded")
        assert shops["web-1"].deploys == [{}], "the first server builds the head of the branch"
        assert shops["web-2"].deploys == [{"commit": "bbb2222"}]
        assert shops["web-3"].deploys == [{"commit": "bbb2222"}]
        assert {shop.commit for shop in shops.values()} == {"bbb2222"}

    def test_a_push_in_the_middle_of_the_rollout_reaches_none_of_the_other_servers(
        self, central, shops, upstream
    ) -> None:
        upstream.head = "bbb2222"
        original = shops["web-1"]._deploy

        def deploy_then_a_push_lands(request_):
            answer = original(request_)
            upstream.head = "ccc3333"
            return answer

        central.nodes["web-1"].handlers[("POST", f"/api/apps/{DOMAIN}/deploy")] = (
            deploy_then_a_push_lands
        )

        run(central, request())

        assert {shop.commit for shop in shops.values()} == {"bbb2222"}

    def test_the_pin_is_kept_in_the_job_so_a_retry_deploys_the_same_commit(
        self, central, shops, upstream
    ) -> None:
        upstream.head = "bbb2222"
        shops["web-3"].fail_deploy = "build failed on web-3"

        snapshot, _, job_id = run(
            central, request(max_failures=-1, options={"rollback_on_failure": False})
        )
        retry = retry_request(job_id, FleetJobs(central.store))

        assert states(snapshot)["web-3"] == "failed"
        assert retry.nodes == ["web-3"]
        assert retry.options["commit"] == "bbb2222"

    def test_a_commit_that_is_asked_for_is_deployed_everywhere_at_once(
        self, central, shops, upstream
    ) -> None:
        snapshot, _, _ = run(central, request(options={"commit": "ddd4444"}, serial=3))

        assert snapshot["status"] == "succeeded"
        assert [shop.deploys for shop in shops.values()] == [[{"commit": "ddd4444"}]] * 3

    def test_a_tag_is_passed_as_a_tag(self, central, shops, upstream) -> None:
        run(central, request(options={"tag": "v2.0.0"}, serial=3))

        assert shops["web-2"].deploys == [{"tag": "v2.0.0"}]

    def test_force_rebuilds_a_server_that_is_already_there(self, central, shops) -> None:
        run(central, request(options={"commit": "aaa1111", "force": True}, serial=3))

        assert shops["web-1"].deploys == [{"commit": "aaa1111", "force": True}]

    def test_when_the_first_server_has_nothing_new_the_others_come_to_its_commit(
        self, central, shops, upstream
    ) -> None:
        """The branch did not move: web-1 already serves its head, and the rest follow it."""
        shops["web-2"].commit = "old0000"
        shops["web-2"].node.apps = [shops["web-2"].row()]

        snapshot, _, _ = run(central, request())

        assert snapshot["status"] == "succeeded"
        assert states(snapshot)["web-1"] in ("skipped", "succeeded")
        assert shops["web-2"].commit == "aaa1111"


class TestWhenItStops:
    def test_the_first_server_failing_starts_nothing_else(self, central, shops, upstream) -> None:
        upstream.head = "bbb2222"
        shops["web-1"].fail_deploy = "npm run build exited 1"

        snapshot, _, _ = run(central, request())

        assert snapshot["status"] == "aborted"
        assert states(snapshot) == {"web-1": "failed", "web-2": "skipped", "web-3": "skipped"}
        assert shops["web-2"].deploys == [] and shops["web-3"].deploys == []
        failed = next(n for n in snapshot["nodes"] if n["node"] == "web-1")
        assert "npm run build exited 1" in (failed["output"] or "")

    def test_a_failure_later_puts_the_servers_already_moved_back(
        self, central, shops, upstream
    ) -> None:
        upstream.head = "bbb2222"
        shops["web-3"].fail_deploy = "migration failed"

        snapshot, recorder, _ = run(central, request())

        assert snapshot["status"] == "aborted" or snapshot["status"] == "failed"
        assert states(snapshot) == {
            "web-1": "rolled_back",
            "web-2": "rolled_back",
            "web-3": "failed",
        }
        assert shops["web-1"].commit == "aaa1111" and shops["web-2"].commit == "aaa1111"
        assert shops["web-1"].activations == ["r1?"], "by the release that served before"
        assert any("put back" in line.lower() for _, line in recorder.lines)

    def test_it_does_not_put_back_when_it_was_asked_not_to(self, central, shops, upstream) -> None:
        upstream.head = "bbb2222"
        shops["web-3"].fail_deploy = "migration failed"

        snapshot, _, _ = run(central, request(options={"rollback_on_failure": False}))

        assert states(snapshot)["web-1"] == "succeeded"
        assert shops["web-1"].commit == "bbb2222" and shops["web-1"].activations == []

    def test_a_server_that_cannot_go_back_by_a_schema_change_is_failed_with_the_servers_words(
        self, central, shops, upstream
    ) -> None:
        upstream.head = "bbb2222"
        shops["web-3"].fail_deploy = "boom"
        shops["web-2"].schema_changed = True

        snapshot, _, _ = run(central, request())

        assert states(snapshot)["web-1"] == "rolled_back"
        assert states(snapshot)["web-2"] == "failed"
        web2 = next(n for n in snapshot["nodes"] if n["node"] == "web-2")
        assert "schema" in web2["error"]["message"].lower()
        assert shops["web-2"].commit == "bbb2222", "it stays where it is: that is not hidden"

    def test_an_application_deployed_in_place_cannot_go_back_instantly_and_says_so(
        self, central, shops, upstream
    ) -> None:
        upstream.head = "bbb2222"
        shops["web-1"].layout = "inplace"
        shops["web-1"].node.apps = [shops["web-1"].row()]
        shops["web-3"].fail_deploy = "boom"

        snapshot, recorder, _ = run(central, request())

        assert states(snapshot)["web-1"] == "succeeded"
        assert any(
            "in place" in line.lower() or "inplace" in line.lower() for _, line in recorder.lines
        )
        assert states(snapshot)["web-2"] == "rolled_back"

    def test_a_server_that_does_not_serve_after_its_deploy_stops_the_rollout_and_is_put_back(
        self, central, shops, upstream
    ) -> None:
        upstream.head = "bbb2222"
        shops["web-2"].fail_health = True

        snapshot, _, _ = run(central, request())

        assert states(snapshot)["web-2"] == "rolled_back"
        web2 = next(n for n in snapshot["nodes"] if n["node"] == "web-2")
        assert "failed" in web2["error"]["message"], "why it stopped is kept"
        assert states(snapshot)["web-3"] == "skipped"
        assert shops["web-2"].commit == "aaa1111"

    def test_failures_can_be_tolerated(self, central, shops, upstream) -> None:
        upstream.head = "bbb2222"
        shops["web-2"].fail_deploy = "flaky"

        snapshot, _, _ = run(
            central, request(max_failures=1, options={"rollback_on_failure": False})
        )

        assert states(snapshot) == {
            "web-1": "succeeded",
            "web-2": "failed",
            "web-3": "succeeded",
        }
        assert shops["web-3"].commit == "bbb2222"

    def test_a_server_that_does_not_answer_is_not_hidden(self, central, shops, upstream) -> None:
        upstream.head = "bbb2222"
        central.nodes["web-3"].down = True

        described = plan(request(), manager=central.manager, asker=ADMIN).to_dict()

        web3 = next(n for n in described["nodes"] if n["node"] == "web-3")
        assert web3["state"] in ("skipped", "unreachable")
        assert web3["step"]
