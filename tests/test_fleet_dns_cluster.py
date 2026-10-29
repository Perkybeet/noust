# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust DNS on several servers of the fleet (4.1, item 104).

The servers do the work (each installs and sets itself up through its own API and its own
permissions); the central orders it and carries one thing between them, the key. What is pinned:
the primary goes first and alone, a secondary reads the primary's address and key and tells the
primary where it answers, the key reaches no log and no record of the job, a server that runs a
Noust without Noust DNS is left out and said so, and a primary that fails stops the rest.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import Asker, set_aggregator
from noust.web import fleet_jobs
from noust.web.fleet_dns_actions import ACTION, cluster_request
from noust.web.fleet_jobs import FleetJobs, Runner, plan
from tests.support.fleet_dns import KEY, DnsNode
from tests.support.fleet_views_support import SCHEMA_30, Central, build_central

ADMIN = Asker(actor="ana", scope="admin", role="admin", elevated=True)
NAMES = ("dns-1", "dns-2", "dns-3", "old-1")

SERVERS: dict[str, dict[str, Any]] = {
    "dns-1": {"hostname": "ns1.example.net", "listen": ["203.0.113.10"]},
    "dns-2": {"hostname": "ns2.example.net", "listen": ["198.51.100.7", "2001:db8:2::7"]},
    "dns-3": {"hostname": "ns3.example.org", "listen": None},
}


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    built = build_central(tmp_path, NAMES)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


class Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.published: list[str] = []

    def log(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        self.published.append(json.dumps(snapshot, default=str) + step)


@pytest.fixture
def dns(central: Central) -> dict[str, DnsNode]:
    # Installing a program and opening port 53 change the host, which a node lets its central do
    # only when it was authorized to (noust fleet authorize --allow-host-access).
    for name in ("dns-1", "dns-2", "dns-3"):
        central.store.set_node_access(name, "admin", True)
    return {name: DnsNode(central.nodes[name]) for name in ("dns-1", "dns-2", "dns-3")}


def run(central: Central, fleet_request: Any) -> tuple[dict[str, Any], Recorder]:
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
    return snapshot, recorder


def states(snapshot: dict[str, Any]) -> dict[str, str]:
    return {node["node"]: node["state"] for node in snapshot["nodes"]}


class TestTheRequest:
    def test_the_primary_goes_alone_first_and_the_rest_one_at_a_time(self) -> None:
        request = cluster_request("dns-1", SERVERS)

        assert (request.action, request.canary, request.serial) == (ACTION, "dns-1", 1)
        assert request.nodes == ["dns-1", "dns-2", "dns-3"]
        assert request.options["primary"] == "dns-1" and request.options["epel"] is False

    def test_it_is_in_the_catalog_with_the_operations_a_server_must_offer(self) -> None:
        spec = fleet_jobs.all_actions()[ACTION]

        assert spec.peer_option == "primary" and spec.max_failures == 0
        assert ("GET", "/api/dns/noust/server/key") in spec.operations
        assert ("POST", "/api/dns/noust/server/secondaries") in spec.operations


class TestTheOptions:
    @pytest.mark.parametrize(
        "options",
        [
            {},
            {"primary": "dns-1"},
            {"primary": "dns-9", "servers": SERVERS},
            {"primary": "dns-1", "servers": {}},
            {"primary": "dns-1", "servers": {"dns-1": {"hostname": "not a host"}}},
            {"primary": "dns-1", "servers": {"dns-1": {"hostname": "ns1"}}},
            {
                "primary": "dns-1",
                "servers": {"dns-1": {"hostname": "ns1.example.net", "listen": []}},
            },
            {
                "primary": "dns-1",
                "servers": {"dns-1": {"hostname": "ns1.example.net", "listen": ["x"]}},
            },
            {
                "primary": "dns-1",
                "servers": {
                    "dns-1": {"hostname": "ns1.example.net"},
                    "dns-2": {"hostname": "NS1.example.net."},
                },
            },
            {"primary": "dns-1", "servers": {"dns-1": {"hostname": "ns1.example.net", "x": 1}}},
            {"primary": "dns-1", "servers": SERVERS, "epel": "yes"},
            {"primary": "dns-1", "servers": SERVERS, "other": 1},
        ],
    )
    def test_what_is_not_a_cluster_is_refused_before_anything_runs(
        self, central: Central, dns: dict[str, DnsNode], options: dict[str, Any]
    ) -> None:
        request = cluster_request("dns-1", SERVERS)
        request.options = options

        with pytest.raises(ValidationError):
            plan(request, manager=central.manager, asker=ADMIN)

        assert all(node.calls == [] for node in dns.values())


class TestSettingItUp:
    def test_the_primary_first_then_each_secondary_with_the_primarys_address_and_key(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        snapshot, _ = run(central, cluster_request("dns-1", SERVERS))

        assert snapshot["status"] == "succeeded", snapshot
        assert states(snapshot) == {
            "dns-1": "succeeded",
            "dns-2": "succeeded",
            "dns-3": "succeeded",
        }
        (primary_body,) = dns["dns-1"].bodies[("POST", "/api/dns/noust/server")]
        assert primary_body == {
            "role": "primary",
            "hostname": "ns1.example.net",
            "epel": False,
            "listen": ["203.0.113.10"],
        }
        (second_body,) = dns["dns-2"].bodies[("POST", "/api/dns/noust/server")]
        assert second_body["role"] == "secondary"
        assert second_body["primary_address"] == "203.0.113.10"
        assert second_body["transfer_key"] == {"secret": KEY}
        assert second_body["listen"] == ["198.51.100.7", "2001:db8:2::7"]
        (third_body,) = dns["dns-3"].bodies[("POST", "/api/dns/noust/server")]
        assert "listen" not in third_body

    def test_the_primary_is_told_where_each_secondary_answers(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        run(central, cluster_request("dns-1", SERVERS))

        told = dns["dns-1"].bodies[("POST", "/api/dns/noust/server/secondaries")]
        assert told == [
            {"address": "198.51.100.7", "hostname": "ns2.example.net"},
            {"address": "2001:db8:2::7", "hostname": "ns2.example.net"},
            {"address": "203.0.113.10", "hostname": "ns3.example.org"},
        ]

    def test_the_order_is_the_primary_then_the_secondaries_one_at_a_time(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        run(central, cluster_request("dns-1", SERVERS))

        paths = [r for r in central.nodes["dns-1"].requests if r.url.path.startswith("/api/dns")]
        setup = next(
            i for i, r in enumerate(paths) if r.method == "POST" and r.url.path.endswith("/server")
        )
        key = next(i for i, r in enumerate(paths) if r.url.path.endswith("/server/key"))
        first_secondary = next(
            i for i, r in enumerate(paths) if r.url.path.endswith("/secondaries")
        )
        assert setup < key < first_secondary

    def test_the_key_is_in_a_request_body_and_nowhere_the_job_keeps(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        snapshot, recorder = run(central, cluster_request("dns-1", SERVERS))

        everything = (
            json.dumps(snapshot, default=str) + repr(recorder.lines) + "".join(recorder.published)
        )
        assert KEY not in everything
        stored = json.dumps(FleetJobs(central.store).list(), default=str)
        assert KEY not in stored

    def test_a_server_that_is_already_set_up_is_asked_again_and_the_node_decides(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        run(central, cluster_request("dns-1", SERVERS))
        snapshot, _ = run(central, cluster_request("dns-1", SERVERS))

        assert snapshot["status"] == "succeeded"
        assert len(dns["dns-1"].bodies[("POST", "/api/dns/noust/server")]) == 2


class TestWhenSomethingIsWrong:
    def test_a_primary_that_fails_stops_the_secondaries(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        dns["dns-1"].fail_setup = "Installing failed: apt-get install -y knot"

        snapshot, _ = run(central, cluster_request("dns-1", SERVERS))

        assert states(snapshot)["dns-1"] == "failed"
        assert states(snapshot)["dns-2"] in ("skipped", "queued")
        assert dns["dns-2"].calls == [] and dns["dns-3"].calls == []
        assert "Installing failed" in json.dumps(snapshot, default=str)

    def test_a_secondary_cannot_go_before_a_primary_that_is_not_set_up(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        # The primary is a server the job does not run: it was never set up.
        request = cluster_request(
            "dns-1",
            {k: v for k, v in SERVERS.items() if k != "dns-1"} | {"dns-1": SERVERS["dns-1"]},
        )
        request.nodes = ["dns-2"]
        request.canary = None

        snapshot, _ = run(central, request)

        assert states(snapshot) == {"dns-2": "failed"}
        assert "is not a primary of Noust DNS yet" in json.dumps(snapshot, default=str)
        assert ("POST", "/api/dns/noust/server") not in dns["dns-2"].bodies

    def test_a_server_that_runs_a_noust_without_noust_dns_is_left_out_and_said_so(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        central.nodes["dns-3"].schema = dict(SCHEMA_30)

        the_plan = plan(cluster_request("dns-1", SERVERS), manager=central.manager, asker=ADMIN)
        snapshot, _ = run(central, cluster_request("dns-1", SERVERS))

        by_node = {n["node"]: n for n in the_plan.to_dict()["nodes"]}
        assert (
            by_node["dns-3"]["state"] == "skipped" and by_node["dns-3"]["reason"] == "unsupported"
        )
        assert "does not offer" in by_node["dns-3"]["error"]["message"]
        assert states(snapshot)["dns-3"] == "skipped"
        assert states(snapshot)["dns-1"] == "succeeded" and states(snapshot)["dns-2"] == "succeeded"
        assert dns["dns-3"].calls == []

    def test_the_key_route_is_the_one_that_asks_for_sudo_mode(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        the_plan = plan(cluster_request("dns-1", SERVERS), manager=central.manager, asker=ADMIN)

        assert the_plan.requires_elevation is True

    def test_a_server_that_did_not_authorize_host_access_is_left_out_with_the_command_that_raises_it(
        self, central: Central, dns: dict[str, DnsNode]
    ) -> None:
        central.store.set_node_access("dns-2", "admin", False)

        the_plan = plan(cluster_request("dns-1", SERVERS), manager=central.manager, asker=ADMIN)
        snapshot, _ = run(central, cluster_request("dns-1", SERVERS))

        by_node = {n["node"]: n for n in the_plan.to_dict()["nodes"]}
        assert by_node["dns-2"]["state"] == "skipped" and by_node["dns-2"]["reason"] == "policy"
        error = by_node["dns-2"]["error"]
        assert "needs server.host_access" in error["message"]
        assert "noust fleet access" in error["hint"]
        assert states(snapshot)["dns-2"] == "skipped" and dns["dns-2"].calls == []
