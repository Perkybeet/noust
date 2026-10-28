# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Building an application on one server and running it on others (checklist A-49).

The servers do the work (the build, the checks, the activation behind the health gate, each through
its own API and its own permissions); the central picks the builder, asks it for the bundle once,
carries it in chunks through the two tunnels to each target, and puts the targets that moved back
when the job stops. What these tests pin is each step that goes wrong without anybody noticing:
the build is made once however many servers receive it, a bundle is sent exactly as the builder
reported it, a transfer that breaks resumes where the target stands, a server that is not ready is
refused before anything is built, and the builder lets go of the bundle only when every server has
it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from noust.core.exceptions import ValidationError
from noust.fleet.builders import Builders
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetJobs, plan, retry_request
from noust.web.fleet_ship_actions import ship_request
from tests.support.fleet_ship import (  # noqa: F401
    ADMIN,
    BUNDLE_ID,
    CHUNK,
    COMMIT,
    DOMAIN,
    PAYLOAD,
    RELEASE,
    SHA,
    Builder,
    Target,
    builder,
    central,
    row,
    run,
    states,
    targets,
)
from tests.support.fleet_views_support import SCHEMA_30, Central

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811


def request(*nodes: str, **overrides: Any):
    values: dict[str, Any] = {"builder": "build-1", "rollback_on_failure": True}
    values.update(overrides)
    return ship_request(DOMAIN, list(nodes or ("web-1", "web-2")), **values)


class TestShipping:
    def test_the_release_is_built_once_and_every_server_gets_those_bytes(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert snapshot["status"] == "succeeded", snapshot
        assert states(snapshot) == {"web-1": "succeeded", "web-2": "succeeded"}
        # One build, however many servers: that is what a build server is for.
        assert len(builder.builds) == 1
        for name in ("web-1", "web-2"):
            target = targets[name]
            assert bytes(target.received) == PAYLOAD
            assert target.uploads == [{"size": len(PAYLOAD), "sha256": SHA}]
            assert target.receives[0]["upload_id"] == "u1"
            assert target.receives[0]["schema_changed_ok"] is False
            assert target.commit == COMMIT

    def test_the_first_server_goes_alone_and_the_rest_are_sent_what_it_pinned(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        the_plan = plan(
            request("web-1", "web-2", "web-3", serial=3), manager=central.manager, asker=ADMIN
        )

        described = the_plan.to_dict()
        assert the_plan.request.canary == "web-1"
        assert [node["batch"] for node in described["nodes"]] == [0, 1, 1]
        assert any("goes first and alone" in note for note in described["notes"])

    def test_it_is_carried_in_chunks_the_builder_never_holds_back(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        run(central, request("web-1"))

        # The target said how big a chunk may be, and no more than that was sent at once.
        assert len(builder.reads) >= 1
        assert all(length <= 4 * 1024 * 1024 for _, length in builder.reads)
        assert builder.reads[0][0] == 0
        puts = [r for r in central.nodes["web-1"].requests if r.method == "PUT"]
        assert len(puts) == -(-len(PAYLOAD) // CHUNK)
        assert all(len(r.content) <= CHUNK for r in puts)

    def test_the_builder_lets_go_of_the_bundle_when_everyone_has_it(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        run(central, request("web-1", "web-2"))

        assert builder.deleted == [BUNDLE_ID]

    def test_the_commit_and_the_confirmations_travel(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        run(
            central,
            request("web-1", commit="feed123", schema_changed_ok=True, frozen_ok=True, keep=True),
        )

        assert builder.builds == [{"keep": True, "commit": "feed123"}]
        assert targets["web-1"].receives[0]["schema_changed_ok"] is True
        assert targets["web-1"].receives[0]["frozen_ok"] is True

    def test_a_server_already_on_the_commit_is_left_alone(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].commit = COMMIT
        targets["web-2"].refresh()

        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert states(snapshot) == {"web-1": "succeeded", "web-2": "skipped"}
        assert targets["web-2"].uploads == []

    def test_each_server_records_where_the_release_came_from(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        snapshot, _, _ = run(central, request("web-1"))

        item = snapshot["nodes"][0]["items"][0]
        assert item["bundle"]["built_on"] == "build-1"
        assert item["bundle"]["release_id"] == RELEASE and item["bundle"]["sha256"] == SHA
        assert item["state"] == "succeeded" and item["changed"] is True
        assert item["before"]["commit"] == "aaa1111"
        assert item["after"]["commit"] == COMMIT


class TestBeforeAnythingIsBuilt:
    def test_a_ship_names_its_builder(self, central: Central, targets: dict[str, Target]) -> None:
        with pytest.raises(ValidationError) as caught:
            plan(request(builder=None), manager=central.manager, asker=ADMIN)

        assert "Say which server builds" in caught.value.message
        assert "noust fleet builder set" in (caught.value.details or "")

    def test_the_recorded_builder_is_used_when_none_is_named(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        Builders(central.store).set(DOMAIN, "build-1")

        snapshot, _, _ = run(central, request("web-1", builder=None))

        assert snapshot["status"] == "succeeded", snapshot
        assert snapshot["options"]["builder"] == "build-1"

    def test_the_builder_cannot_also_receive(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            plan(request("build-1", "web-1"), manager=central.manager, asker=ADMIN)

        assert "also a server to ship it to" in caught.value.message

    def test_a_builder_that_is_not_a_node_is_refused(
        self, central: Central, targets: dict[str, Target]
    ) -> None:
        with pytest.raises(Exception, match="No node named ghost"):
            plan(request("web-1", builder="ghost"), manager=central.manager, asker=ADMIN)

    def test_the_builder_needs_the_application(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["build-1"].apps = []

        with pytest.raises(ValidationError) as caught:
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

        assert "not installed on build-1" in caught.value.message
        assert "noust fleet placement copy" in (caught.value.details or "")
        assert builder.builds == []

    def test_the_builder_needs_the_release_layout(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["build-1"].apps = [row(layout="inplace")]

        with pytest.raises(ValidationError, match="release layout on build-1"):
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

    def test_a_target_of_another_type_is_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].app_type = "python"
        targets["web-2"].refresh()

        with pytest.raises(ValidationError) as caught:
            plan(request("web-1", "web-2"), manager=central.manager, asker=ADMIN)

        assert "python on web-2 and nodejs on build-1" in caught.value.message
        assert builder.builds == []

    def test_a_target_without_the_application_is_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["web-2"].apps = []

        with pytest.raises(ValidationError, match="not installed on web-2"):
            plan(request("web-1", "web-2"), manager=central.manager, asker=ADMIN)

    def test_a_target_in_place_is_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].layout = "inplace"
        targets["web-1"].refresh()

        with pytest.raises(ValidationError, match="release layout on web-1"):
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

    def test_a_builder_that_cannot_ship_is_named_as_too_old(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["build-1"].schema = dict(SCHEMA_30)

        with pytest.raises(ValidationError, match="does not offer"):
            plan(request("web-1"), manager=central.manager, asker=ADMIN)

    def test_a_target_that_cannot_receive_is_skipped_as_too_old(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["web-2"].schema = dict(SCHEMA_30)

        described = plan(request("web-1", "web-2"), manager=central.manager, asker=ADMIN).to_dict()

        assert {n["node"]: n.get("reason") for n in described["nodes"]}["web-2"] == "unsupported"

    @pytest.mark.parametrize(
        "options",
        [
            {"domain": "https://x.example.com", "builder": "build-1"},
            {"domain": DOMAIN, "builder": "build-1", "commit": "main"},
            {"domain": DOMAIN, "builder": "build-1", "frozen_ok": "yes"},
            {"domain": DOMAIN, "builder": "build-1", "extra": 1},
            {"domain": DOMAIN, "builder": "build-1", "bundle": {"id": 1}},
        ],
    )
    def test_options_that_are_not_valid_are_refused(
        self, central: Central, builder: Builder, targets: dict[str, Target], options: Any
    ) -> None:
        with pytest.raises(ValidationError):
            plan(
                fleet_jobs.FleetRequest("placement_ship", nodes=["web-1"], options=options),
                manager=central.manager,
                asker=ADMIN,
            )


class TestWhenSomethingGoesWrong:
    def test_a_build_that_fails_stops_everything_with_the_builders_words(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        builder.fail_build = "npm ERR! missing script: build"

        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert snapshot["status"] == "aborted"
        assert states(snapshot) == {"web-1": "failed", "web-2": "skipped"}
        assert "npm ERR! missing script: build" in (snapshot["nodes"][0]["output"] or "")
        assert "build-1" in snapshot["nodes"][0]["error"]["message"]
        assert targets["web-1"].uploads == [] and builder.deleted == []

    def test_a_broken_transfer_resumes_where_the_target_stands(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].break_after = CHUNK * 5

        snapshot, recorder, _ = run(central, request("web-1"))

        assert snapshot["status"] == "succeeded", snapshot
        assert targets["web-1"].breaks == 1
        assert bytes(targets["web-1"].received) == PAYLOAD
        assert any("resuming" in line for _, line in recorder.lines)

    def test_a_transfer_that_keeps_breaking_gives_up_and_drops_the_upload(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        central.nodes["web-1"].handlers[("PUT", f"/api/apps/{DOMAIN}/releases/uploads/u1")] = (
            lambda request: (_ for _ in ()).throw(
                httpx.ReadError("the tunnel dropped", request=request)
            )
        )

        snapshot, _, _ = run(central, request("web-1"))

        assert states(snapshot) == {"web-1": "failed"}
        assert "failed 6 times in a row" in snapshot["nodes"][0]["error"]["message"]
        assert targets["web-1"].dropped == ["u1"]
        assert targets["web-1"].receives == []
        # The bundle stays on the builder: a retry sends it, it does not build again.
        assert builder.deleted == []

    def test_a_release_that_does_not_answer_keeps_the_servers_words(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets[
            "web-1"
        ].fail_receive = "Release 20261016-101500-feed123 did not pass its health check"

        snapshot, _, _ = run(central, request("web-1"))

        assert states(snapshot) == {"web-1": "failed"}
        assert "did not pass its health check" in (snapshot["nodes"][0]["output"] or "")

    def test_a_server_that_does_not_serve_afterwards_is_a_failure(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].status_after = "failed"

        snapshot, _, _ = run(central, request("web-1", rollback_on_failure=False))

        assert states(snapshot) == {"web-1": "failed"}
        assert "failed on web-1 after the release" in snapshot["nodes"][0]["error"]["message"]
        assert targets["web-1"].activations == []

    def test_and_it_is_put_back_by_the_release_that_served_when_that_is_asked(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-1"].status_after = "failed"

        snapshot, _, _ = run(central, request("web-1"))

        assert states(snapshot) == {"web-1": "rolled_back"}
        assert targets["web-1"].activations == ["r1"]

    def test_the_servers_already_moved_are_put_back_when_a_later_one_fails(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].fail_receive = "disk full"

        snapshot, _, _ = run(central, request("web-1", "web-2"))

        assert states(snapshot) == {"web-1": "rolled_back", "web-2": "failed"}
        assert targets["web-1"].activations == ["r1"] and targets["web-1"].commit == "aaa1111"

    def test_a_retry_sends_the_bundle_the_failed_job_pinned_and_builds_nothing(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        targets["web-2"].fail_receive = "disk full"
        snapshot, _, job_id = run(central, request("web-1", "web-2", rollback_on_failure=False))
        assert states(snapshot) == {"web-1": "succeeded", "web-2": "failed"}
        targets["web-2"].fail_receive = None

        retried = retry_request(job_id, FleetJobs(central.store))
        again, _, _ = run(central, retried)

        assert again["status"] == "succeeded", again
        assert len(builder.builds) == 1, "the retry used the build the job had made"
        assert bytes(targets["web-2"].received) == PAYLOAD

    def test_a_bundle_the_builder_no_longer_has_is_built_again(
        self, central: Central, builder: Builder, targets: dict[str, Target]
    ) -> None:
        pinned = request("web-1")
        pinned.options["bundle"] = Builder.bundle()
        builder.kept = False

        snapshot, recorder, _ = run(central, pinned)

        assert snapshot["status"] == "succeeded", snapshot
        assert len(builder.builds) == 1
        assert any("no longer on build-1" in line for _, line in recorder.lines)
