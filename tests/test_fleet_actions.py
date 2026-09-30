"""
Bulk actions over the fleet: the plan, the batches, a result per node, retrying.

The nodes are fakes answering the node API as a real one does (a job queued,
then read back with its log); the engine is the real one. What is pinned: who
is skipped and why, read from each node's own schema and ceiling; sudo mode
decided by the node's schema; serial batches, the canary and ``max_failures``;
every node's words kept verbatim; what a retry selects; and each action's
calls to the node.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import Asker, set_aggregator
from noust.fleet.labels import NodeLabels
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetJobs, FleetRequest, Runner, batch_size, plan, retry_request
from tests.fleet_views_support import SCHEMA_30, Central, build_central

ADMIN = Asker(actor="ana", scope="admin", role="admin", elevated=True)


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1", "web-2", "web-3"))
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


class Recorder:
    """A reporter that keeps what it is told."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.snapshots: list[dict[str, Any]] = []

    def log(self, message: str, level: str = "info") -> None:
        self.lines.append((level, message))

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        self.snapshots.append(snapshot)


def _run(
    central: Central, request: FleetRequest, **kw: Any
) -> tuple[dict[str, Any], Recorder, str]:
    the_plan = plan(request, manager=central.manager, asker=ADMIN)
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
        elevated_until=kw.pop("elevated_until", float("inf")),
        jobs=jobs,
        poll=0,
        sleep=lambda seconds: None,
    ).run()
    return snapshot, recorder, job_id


def _states(snapshot: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    return {node["node"]: (node["state"], node["reason"]) for node in snapshot["nodes"]}


def _renewal(node: Any, failed: bool) -> Any:
    def answer(request: Any) -> Any:
        if failed:
            return node.queue(status="failed", error="certbot said no\nDNS problem")
        return node.queue()

    return answer


def _renews(central: Central, *failing: str) -> None:
    for name, node in central.nodes.items():
        node.handlers[("POST", "/api/certs/renew-all")] = _renewal(node, name in failing)


class TestPlan:
    def test_by_name_and_by_label_each_once_in_registry_order(self, central):
        NodeLabels(central.store).change("web-3", set_labels={"env": "prod"})
        NodeLabels(central.store).change("web-2", set_labels={"env": "prod"})

        the_plan = plan(
            FleetRequest("certs_renew", nodes=["web-3", "web-1"], labels={"env": "prod"}),
            manager=central.manager,
            asker=ADMIN,
        )

        assert [node.node for node in the_plan.nodes] == ["web-1", "web-2", "web-3"]

    def test_unknown_servers_and_empty_selections_are_refused(self, central):
        with pytest.raises(ValidationError, match="No node named nope"):
            plan(FleetRequest("certs_renew", nodes=["nope"]), manager=central.manager, asker=ADMIN)
        with pytest.raises(ValidationError, match="No server is selected"):
            plan(
                FleetRequest("certs_renew", labels={"env": "none"}),
                manager=central.manager,
                asker=ADMIN,
            )
        with pytest.raises(ValidationError, match="Unknown fleet action"):
            plan(
                FleetRequest("format_disks", nodes=["web-1"]), manager=central.manager, asker=ADMIN
            )
        with pytest.raises(ValidationError, match="Unknown option"):
            plan(
                FleetRequest("certs_renew", nodes=["web-1"], options={"rm": True}),
                manager=central.manager,
                asker=ADMIN,
            )

    def test_batches_the_canary_goes_alone_first(self, central):
        the_plan = plan(
            FleetRequest(
                "certs_renew", nodes=["web-1", "web-2", "web-3"], serial=2, canary="web-3"
            ),
            manager=central.manager,
            asker=ADMIN,
        )

        assert the_plan.to_dict()["batches"] == [["web-3"], ["web-1", "web-2"]]

    @pytest.mark.parametrize(
        ("serial", "total", "expected"), [(None, 5, 4), (1, 5, 1), ("25%", 5, 2), ("100%", 3, 3)]
    )
    def test_serial(self, serial, total, expected):
        assert batch_size(serial, total, 4) == expected

    @pytest.mark.parametrize("serial", [0, "0%", "150%", "many"])
    def test_serial_nonsense(self, serial):
        with pytest.raises(ValidationError):
            batch_size(serial, 3, 1)

    def test_a_node_too_old_is_skipped_as_unsupported(self, central):
        central.nodes["web-2"].schema = dict(SCHEMA_30)
        for node in central.nodes.values():
            node.responses["/api/system/version"] = (
                200,
                {"current_version": "3.0.1", "update_state": "update_available"},
            )

        the_plan = plan(
            FleetRequest("noust_update", nodes=["web-1", "web-2"]),
            manager=central.manager,
            asker=ADMIN,
        )
        described = the_plan.to_dict()

        assert _states(described)["web-2"] == ("skipped", "unsupported")
        assert "does not offer GET /api/system/update" in described["nodes"][1]["step"]
        assert described["batches"] == [["web-1"]]
        # The node's schema marks the update as needing sudo mode.
        assert described["requires_elevation"] is True
        assert described["notes"]

    def test_a_certificate_renewal_needs_no_sudo_mode_where_the_node_says_so(self, central):
        the_plan = plan(
            FleetRequest("certs_renew", nodes=["web-1"]), manager=central.manager, asker=ADMIN
        )
        assert the_plan.requires_elevation is False

    def test_the_nodes_ceiling_skips_it_as_policy(self, central):
        central.store.set_node_access("web-2", "deploy", False)

        described = plan(
            FleetRequest("os_updates", nodes=["web-1", "web-2"]),
            manager=central.manager,
            asker=ADMIN,
        ).to_dict()

        assert _states(described)["web-2"] == ("skipped", "policy")
        assert "at most 'deploy" in described["nodes"][1]["step"]

    def test_a_node_with_nothing_to_do_is_skipped_as_not_needed(self, central):
        central.nodes["web-1"].responses["/api/system/version"] = (
            200,
            {"current_version": "3.1.0", "update_state": "up_to_date"},
        )

        described = plan(
            FleetRequest("noust_update", nodes=["web-1"]), manager=central.manager, asker=ADMIN
        ).to_dict()

        assert _states(described)["web-1"] == ("skipped", "not_needed")

    def test_an_unreachable_node_is_shown_so(self, central):
        central.nodes["web-2"].down = True

        described = plan(
            FleetRequest("certs_renew", nodes=["web-1", "web-2"]),
            manager=central.manager,
            asker=ADMIN,
        ).to_dict()

        assert _states(described)["web-2"][0] == "unreachable"
        assert described["summary"] == {"run": 1, "skipped": 1}


class TestRun:
    def test_every_node_runs_and_its_log_is_relayed(self, central):
        _renews(central)

        snapshot, recorder, job_id = _run(
            central, FleetRequest("certs_renew", nodes=["web-1", "web-2"], options={"force": True})
        )

        assert snapshot["status"] == "succeeded"
        assert snapshot["summary"]["succeeded"] == 2
        assert ("info", "[web-1] [x] [INFO] done") in recorder.lines
        request = next(
            r for r in central.nodes["web-1"].requests if r.url.path == "/api/certs/renew-all"
        )
        assert json.loads(request.content) == {"force": True}
        assert request.headers["X-Noust-Elevated"] == "1"
        assert request.headers["X-Noust-Actor-Role"] == "admin"
        record = FleetJobs(central.store).get(job_id)
        assert record["status"] == "succeeded"
        assert record["nodes"][0]["node_jobs"] == ["j001"]
        assert record["nodes"][0]["href"] == "/n/web-1/activity"

    def test_a_failure_keeps_the_nodes_words_and_max_failures_stops_the_rest(self, central):
        _renews(central, "web-1")

        snapshot, _, job_id = _run(
            central,
            FleetRequest(
                "certs_renew", nodes=["web-1", "web-2", "web-3"], serial=1, max_failures=0
            ),
        )

        assert snapshot["status"] == "aborted"
        assert _states(snapshot) == {
            "web-1": ("failed", None),
            "web-2": ("skipped", "aborted"),
            "web-3": ("skipped", "aborted"),
        }
        failed = snapshot["nodes"][0]
        assert "certbot said no" in failed["error"]["message"]
        assert "DNS problem" in failed["output"]
        assert not any(
            r.url.path == "/api/certs/renew-all" for r in central.nodes["web-2"].requests
        )

        retry = retry_request(job_id, FleetJobs(central.store))
        assert retry.nodes == ["web-1", "web-2", "web-3"]
        assert retry.retry_of == job_id

    def test_failures_within_the_threshold_do_not_stop_anything(self, central):
        _renews(central, "web-1")

        snapshot, _, job_id = _run(
            central, FleetRequest("certs_renew", nodes=["web-1", "web-2", "web-3"], serial=1)
        )

        assert snapshot["status"] == "failed"
        assert snapshot["summary"]["succeeded"] == 2
        assert retry_request(job_id, FleetJobs(central.store)).nodes == ["web-1"]

    def test_a_failed_canary_stops_everything(self, central):
        _renews(central, "web-2")

        snapshot, _, _ = _run(
            central,
            FleetRequest(
                "certs_renew", nodes=["web-1", "web-2", "web-3"], canary="web-2", max_failures=-1
            ),
        )

        assert snapshot["status"] == "aborted"
        assert _states(snapshot)["web-1"] == ("skipped", "aborted")

    def test_an_unreachable_node_is_its_own_state(self, central):
        _renews(central)
        central.nodes["web-2"].down = True
        # Planned while it answered, down when its turn comes.
        the_plan = plan(
            FleetRequest("certs_renew", nodes=["web-1"]), manager=central.manager, asker=ADMIN
        )
        assert the_plan

        snapshot, _, _ = _run(central, FleetRequest("certs_renew", nodes=["web-1", "web-2"]))

        assert _states(snapshot)["web-2"][0] == "unreachable"

    def test_a_node_another_fleet_job_holds_is_skipped_as_busy(self, central):
        _renews(central)
        fleet_jobs._claim("web-1", "other")
        try:
            snapshot, _, _ = _run(central, FleetRequest("certs_renew", nodes=["web-1", "web-2"]))
        finally:
            fleet_jobs._release("web-1", "other")

        assert _states(snapshot)["web-1"] == ("skipped", "busy")
        assert _states(snapshot)["web-2"] == ("succeeded", None)

    def test_an_elevated_step_after_sudo_mode_expired_is_skipped(self, central):
        for node in central.nodes.values():
            node.responses["/api/system/version"] = (
                200,
                {"current_version": "3.1.0", "update_state": "update_available"},
            )

        snapshot, _, _ = _run(
            central, FleetRequest("noust_update", nodes=["web-1"]), elevated_until=0.0
        )

        assert _states(snapshot)["web-1"] == ("skipped", "elevation_expired")
        assert not any(
            r.url.path == "/api/system/update" and r.method == "POST"
            for r in central.nodes["web-1"].requests
        )


class TestActions:
    def test_backups_run_and_verify_each_application(self, central):
        node = central.nodes["web-1"]
        node.apps = [{"domain": "a.example.com"}, {"domain": "b.example.com"}]
        backups = iter(["bk-a", "bk-b"])
        node.handlers[("POST", "/api/backups")] = lambda request: node.queue(
            {"backup_id": next(backups)}
        )
        node.handlers[("POST", "/api/backups/bk-a/verify")] = lambda request: httpx.Response(
            200, json={"valid": True}
        )
        node.handlers[("POST", "/api/backups/bk-b/verify")] = lambda request: httpx.Response(
            200, json={"valid": False, "errors": ["checksum mismatch"]}
        )

        snapshot, _, _ = _run(central, FleetRequest("backups_run", nodes=["web-1"]))

        (result,) = snapshot["nodes"]
        assert result["state"] == "failed"
        items = {item["domain"]: item for item in result["items"]}
        assert (items["a.example.com"]["state"], items["a.example.com"]["verified"]) == (
            "succeeded",
            True,
        )
        assert items["b.example.com"]["state"] == "failed"
        assert "checksum mismatch" in result["output"]

    def test_apps_update_skips_what_has_nothing_new(self, central):
        node = central.nodes["web-1"]
        node.apps = [{"domain": "a.example.com"}, {"domain": "b.example.com"}]

        def update(request: httpx.Request) -> httpx.Response:
            if json.loads(request.content)["domain"] == "a.example.com":
                return httpx.Response(
                    409, json={"error": "nothing_new", "detail": "a is at abc123"}
                )
            return node.queue({"deployment_id": 7})

        node.handlers[("POST", "/api/jobs/update")] = update

        snapshot, _, _ = _run(
            central,
            FleetRequest(
                "apps_update",
                nodes=["web-1"],
                options={"domains": ["a.example.com", "b.example.com"]},
            ),
        )

        (result,) = snapshot["nodes"]
        assert result["state"] == "succeeded"
        items = {item["domain"]: item for item in result["items"]}
        assert (items["a.example.com"]["state"], items["a.example.com"]["reason"]) == (
            "skipped",
            "not_needed",
        )
        assert items["b.example.com"]["deployment_id"] == 7

    def test_apps_restart_reports_one_that_did_not(self, central):
        node = central.nodes["web-1"]
        node.apps = [{"domain": "a.example.com"}]
        node.handlers[("POST", "/api/apps/a.example.com/restart")] = lambda request: httpx.Response(
            200,
            json={
                "success": False,
                "message": "Job for a.service failed",
                "domain": "a.example.com",
            },
        )

        snapshot, _, _ = _run(central, FleetRequest("apps_restart", nodes=["web-1"]))

        assert snapshot["nodes"][0]["state"] == "failed"
        assert "Job for a.service failed" in snapshot["nodes"][0]["output"]

    def test_noust_update_waits_for_the_new_version(self, central):
        node = central.nodes["web-1"]
        calls = {"n": 0, "updated": False}
        node.handlers[("GET", "/api/system/version")] = lambda request: httpx.Response(
            200,
            json={
                "current_version": "3.1.1" if calls["updated"] else "3.1.0",
                "update_state": "update_available",
            },
        )
        node.handlers[("POST", "/api/system/update")] = lambda request: node.queue(status="running")

        def status(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("the console is restarting", request=request)
            calls["updated"] = True
            return httpx.Response(
                200, json={"last_run": {"job_id": "j001", "status": "succeeded", "tail": []}}
            )

        node.handlers[("GET", "/api/system/update")] = status

        snapshot, _, _ = _run(central, FleetRequest("noust_update", nodes=["web-1"]))

        (result,) = snapshot["nodes"]
        assert result["state"] == "succeeded", result
        assert result["step"] == "Noust 3.1.1 confirmed"
        assert result["items"] == [
            {"from_version": "3.1.0", "to_version": "3.1.1", "state": "succeeded"}
        ]

    def test_noust_update_that_failed_carries_the_installations_words(self, central):
        node = central.nodes["web-1"]
        node.responses["/api/system/version"] = (
            200,
            {"current_version": "3.1.0", "update_state": "update_available"},
        )
        node.handlers[("POST", "/api/system/update")] = lambda request: node.queue(status="running")
        node.handlers[("GET", "/api/system/update")] = lambda request: httpx.Response(
            200,
            json={
                "last_run": {
                    "job_id": "j001",
                    "status": "failed",
                    "error": "apt-get failed",
                    "tail": ["E: Unable to locate package noust"],
                }
            },
        )

        snapshot, _, _ = _run(central, FleetRequest("noust_update", nodes=["web-1"]))

        (result,) = snapshot["nodes"]
        assert result["state"] == "failed"
        assert result["error"]["message"] == "apt-get failed"
        assert "Unable to locate package" in result["output"]

    def test_os_updates_never_reboot_and_skip_a_node_with_nothing_pending(self, central):
        busy, idle = central.nodes["web-1"], central.nodes["web-2"]
        busy.responses["/api/server/summary"] = (200, {"updates": {"pending": 3, "security": 2}})
        idle.responses["/api/server/summary"] = (200, {"updates": {"pending": 3, "security": 0}})
        busy.handlers[("POST", "/api/server/updates/apply")] = lambda request: busy.queue()
        busy.responses["/api/server/updates/runs"] = (
            200,
            [
                {
                    "job_id": "j001",
                    "status": "completed",
                    "packages": ["openssl", "curl"],
                    "reboot_required": True,
                }
            ],
        )

        snapshot, _, _ = _run(central, FleetRequest("os_updates", nodes=["web-1", "web-2"]))

        assert _states(snapshot) == {
            "web-1": ("succeeded", None),
            "web-2": ("skipped", "not_needed"),
        }
        applied = next(r for r in busy.requests if r.url.path == "/api/server/updates/apply")
        assert json.loads(applied.content) == {"scope": "security"}
        result = snapshot["nodes"][0]
        assert result["items"][0]["reboot_required"] is True
        assert "reboot is due" in result["step"]
        assert not any("reboot" in r.url.path for r in busy.requests)


class TestRecord:
    def test_a_job_whose_worker_is_gone_reads_as_interrupted(self, central, monkeypatch):
        the_plan = plan(
            FleetRequest("certs_renew", nodes=["web-1"]), manager=central.manager, asker=ADMIN
        )
        jobs = FleetJobs(central.store)
        jobs.create("abc12345", the_plan, created_by="ana")
        monkeypatch.setattr(fleet_jobs, "_pid_alive", lambda pid: False)

        job = jobs.get("abc12345")

        assert job["status"] == "interrupted"
        assert job["nodes"][0]["state"] == "interrupted"
        assert retry_request("abc12345", jobs).nodes == ["web-1"]

    def test_nothing_to_retry(self, central):
        _renews(central)
        _, _, job_id = _run(central, FleetRequest("certs_renew", nodes=["web-1"]))

        with pytest.raises(ValidationError, match="no server to retry"):
            retry_request(job_id, FleetJobs(central.store))

    def test_the_list_is_newest_first(self, central):
        _renews(central)
        _, _, first = _run(central, FleetRequest("certs_renew", nodes=["web-1"]))
        _, _, second = _run(central, FleetRequest("certs_renew", nodes=["web-2"]))

        listing = FleetJobs(central.store).list()

        assert [job["job_id"] for job in listing][:2] == [second, first]
        assert "nodes" not in listing[0]
