"""
Tests for ``/api/server``: the translation of HTTP into the managers and back.

An endpoint here does three things and the tests are about those three: it refuses
what can be refused before a job exists (a busy host, a removal nobody read, a
container asked for swap), it queues the right job with the right arguments, and
it records that somebody asked. What the managers do with the machine is tested
where the managers are; the tools' outputs are the captured ones in
``tests/fixtures/server``.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.managers.server.storage import Analysis, Candidate
from noust.web.api import server as server_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.api.server import common
from noust.web.api.server.common import set_server_context
from noust.web.jobs import JobType
from noust.web.permissions.registry import route_map
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    RecordingAudit,
    fixture,
    make_machine,
    no_package_manager_running,
    platform_for,
)

pytestmark = pytest.mark.usefixtures("no_package_manager_running")


class FakeJob:
    """A queued job, as far as the endpoints look at one."""

    def __init__(self, job_id: str, kwargs: dict[str, Any]) -> None:
        self.id = job_id
        self.type = kwargs["job_type"]
        self.name = kwargs["name"]
        self.status = types.SimpleNamespace(value="pending")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": getattr(self.type, "value", self.type), "name": self.name}


class FakeJobs:
    """Stands in for the job manager: records what is queued, runs nothing."""

    def __init__(self) -> None:
        self.queued: list[dict[str, Any]] = []
        self.active: list[Any] = []

    def create_job(self, **kwargs: Any) -> FakeJob:
        self.queued.append(kwargs)
        return FakeJob(f"job{len(self.queued)}", kwargs)

    def get_active_jobs(self) -> list[Any]:
        return self.active


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "api.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store):
    """The router over a server made of fixtures, with auth stubbed and jobs recorded."""
    machine = make_machine(tmp_path, monkeypatch, store)
    jobs = FakeJobs()
    audit = RecordingAudit()
    for module in (
        "noust.web.api.server.common",
        "noust.web.api.server.updates",
        "noust.web.api.server.storage",
    ):
        monkeypatch.setattr(f"{module}.get_job_manager", lambda: jobs)
    monkeypatch.setattr("noust.core.audit.record", audit.record)
    # As the console builds it: what its job queue is running is what an update
    # or a reboot must not overlap.
    machine.ctx.blockers = common.running_job_names
    set_server_context(machine.ctx)

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(server_api.router, prefix="/api/server")
    session = {"sid": "operator", "type": "master"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield types.SimpleNamespace(
            client=client, jobs=jobs, audit=audit, machine=machine, app=app, session=session
        )
    finally:
        set_server_context(None)


class TestPermissionsAreDeclared:
    def test_every_route_of_the_router_is_in_the_permission_map(self, api) -> None:
        schema = api.app.openapi()
        routes = {
            (method.upper(), path)
            for path, operations in schema["paths"].items()
            for method in operations
        }
        mapped = set(route_map())

        assert routes - mapped == set()

    def test_shutdown_needs_host_access_and_the_journal_needs_secrets(self) -> None:
        permissions = route_map()

        assert permissions[("POST", "/api/server/power/shutdown")] == "server.host_access"
        assert permissions[("GET", "/api/server/logs")] == "secrets.reveal"
        assert permissions[("POST", "/api/server/updates/apply")] == "server.manage"
        assert permissions[("GET", "/api/server/updates")] == "server.read"

    def test_every_write_asks_for_sudo_mode_except_cancelling_and_measuring(self, api) -> None:
        schema = api.app.openapi()
        from noust.web.api.openapi import ELEVATION_EXTENSION, annotate_elevation

        annotated = annotate_elevation(schema, api.app.routes)
        exempt = {
            ("delete", "/api/server/power/scheduled"),
            ("post", "/api/server/storage/analyze"),
            ("post", "/api/server/security/checks/refresh"),
        }
        unelevated = {
            (method, path)
            for path, operations in annotated["paths"].items()
            for method, operation in operations.items()
            if method in {"post", "put", "delete"} and not operation.get(ELEVATION_EXTENSION)
        }

        assert unelevated == exempt


class TestSummary:
    def test_the_summary_is_served_in_one_answer(self, api) -> None:
        response = api.client.get("/api/server/summary")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["hostname"] == "vps-1"
        assert body["os"]["eol"]["status"] == "ok"
        assert body["capabilities"]["packages"] == "apt"
        assert "checked_at" in body
        assert body["hardening"] is None

    def test_capabilities_alone(self, api) -> None:
        response = api.client.get("/api/server/capabilities")

        assert response.status_code == 200
        assert response.json()["updates"] is True
        assert response.json()["container"] is None


class TestUpdatesListing:
    def test_pending_updates_come_with_security_marked_and_the_reboot_and_the_services(
        self, api
    ) -> None:
        body = api.client.get("/api/server/updates").json()

        assert body["supported"] is True
        assert body["pending"] == 9
        assert body["security"] == 7
        by_name = {p["name"]: p for p in body["packages"]}
        assert by_name["openssl"]["security"] is True
        assert by_name["bash"]["security"] is False
        assert by_name["linux-image-6.8.0-100-generic"]["kernel"] is True
        assert body["kept_back"] == ["ubuntu-drivers-common"]
        assert body["reboot"]["required"] is True
        assert body["stale_services"] == ["cron.service", "nginx.service", "php8.3-fpm.service"]
        assert body["auto"]["enabled"] is True
        assert body["running"] is None
        assert body["checked_at"]

    def test_a_probe_that_failed_says_so_and_the_page_still_answers(self, api) -> None:
        api.machine.runner.script(["apt-get", "-s"], stderr="E: broken lists", exit_code=100)

        response = api.client.get("/api/server/updates")

        assert response.status_code == 200
        assert "broken lists" in response.json()["error"]
        assert response.json()["pending"] == 0

    def test_a_system_where_updates_are_not_managed_explains_it(self, api) -> None:
        api.machine.ctx._platform = platform_for("none")
        api.machine.ctx._built.clear()

        body = api.client.get("/api/server/updates").json()

        assert body["supported"] is False
        assert body["reason"]
        assert body["pending"] == 0

    def test_a_run_in_progress_is_reported(self, api) -> None:
        from noust.managers.server.updates import UpdateRecord

        api.machine.ctx.records.write(UpdateRecord(id="0a1b2c3d", scope="all", status="running"))

        assert api.client.get("/api/server/updates").json()["running"]["id"] == "0a1b2c3d"

    def test_the_plan_lists_the_security_packages_and_the_command(self, api) -> None:
        body = api.client.get("/api/server/updates/plan?scope=security").json()

        assert "openssl" in {p["name"] for p in body["packages"]}
        assert "bash" not in {p["name"] for p in body["packages"]}
        assert "--only-upgrade" in body["command"]
        assert "kernel" in body["impact"]
        assert body["removals"] == []

    def test_a_scope_that_is_not_one_is_a_400_on_the_field(self, api) -> None:
        response = api.client.get("/api/server/updates/plan?scope=everything")

        assert response.status_code == 400
        assert response.json()["fields"] == {"scope": "Unknown scope: 'everything'"}

    def test_runs_are_listed_and_read_one_by_one(self, api) -> None:
        from noust.managers.server.updates import UpdateRecord

        api.machine.ctx.records.write(UpdateRecord(id="0a1b2c3d", scope="all", status="completed"))

        listed = api.client.get("/api/server/updates/runs").json()
        one = api.client.get("/api/server/updates/runs/0a1b2c3d")
        missing = api.client.get("/api/server/updates/runs/ffffffff")
        invalid = api.client.get("/api/server/updates/runs/..%2F..%2Fetc")

        assert [r["id"] for r in listed] == ["0a1b2c3d"]
        assert one.status_code == 200
        assert missing.status_code == 404
        assert invalid.status_code in (400, 404)


class TestApplyingUpdates:
    def _apply(self, api, **body: Any):
        return api.client.post("/api/server/updates/apply", json={"scope": "security", **body})

    def test_it_queues_a_job_that_runs_in_its_own_unit_and_says_so_in_the_audit_log(
        self, api
    ) -> None:
        response = self._apply(api)

        assert response.status_code == 202, response.text
        assert response.json()["job_id"] == "job1"
        queued = api.jobs.queued[0]
        assert queued["job_type"] is JobType.OS_UPDATE
        assert queued["kwargs"] == {
            "scope": "security",
            "full": False,
            "allow_removals": False,
            "actor": "operator",
        }
        assert queued["actor"] == "operator"
        assert api.audit.events() == ["server.update"]
        assert api.audit.records[0]["target"] == "packages"
        assert api.audit.records[0]["details"] == {
            "stage": "queued",
            "job": "job1",
            "scope": "security",
            "full": False,
            "allow_removals": False,
            "packages": 7,
        }

    def test_another_package_manager_running_is_a_409_with_who_and_no_job(
        self, api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.server.pkg.base.running_processes",
            lambda names, **_: ["unattended-upgr"],
        )

        response = self._apply(api)

        assert response.status_code == 409
        assert response.json()["error"] == "host_busy"
        assert response.json()["holders"] == ["unattended-upgr"]
        assert api.jobs.queued == []

    def test_an_update_already_queued_refuses_a_second(self, api) -> None:
        api.jobs.active = [
            types.SimpleNamespace(type=JobType.OS_UPDATE, name="Apply security updates")
        ]

        response = self._apply(api)

        assert response.status_code == 409
        assert response.json()["error"] == "host_busy"
        assert "Apply security updates" in response.json()["detail"]

    def test_a_deploy_in_progress_is_a_preflight_failure(self, api) -> None:
        api.jobs.active = [
            types.SimpleNamespace(type=JobType.DEPLOY, name="Deploy shop.example.com")
        ]

        response = self._apply(api)

        assert response.status_code == 409
        assert response.json()["error"] == "preflight_failed"
        assert response.json()["blockers"] == ["Deploy shop.example.com is running"]

    def test_removals_are_a_question_with_the_list_and_no_job_until_allowed(self, api) -> None:
        api.machine.runner.script(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"],
            stdout=fixture("apt", "full-upgrade-removals.txt"),
        )

        refused = self._apply(api, scope="all", full=True)
        allowed = self._apply(api, scope="all", full=True, allow_removals=True)

        assert refused.status_code == 409
        assert refused.json()["error"] == "confirmation_required"
        assert refused.json()["required"] == {"removals": ["libfoo1t64", "python3-oldthing"]}
        assert allowed.status_code == 202
        assert len(api.jobs.queued) == 1

    def test_security_with_nothing_marked_is_a_409_not_an_empty_job(self, api) -> None:
        api.machine.runner.script(
            ["apt-get", "-s"], stdout=fixture("apt", "upgrade-simulation-clean.txt")
        )

        response = self._apply(api)

        assert response.status_code == 409
        assert response.json()["error"] == "preflight_failed"
        assert api.jobs.queued == []

    def test_a_system_where_updates_are_not_managed_is_a_501_with_the_reason(self, api) -> None:
        api.machine.ctx._platform = platform_for("none")
        api.machine.ctx._built.clear()

        response = self._apply(api)

        assert response.status_code == 501
        assert response.json()["error"] == "unsupported_platform"
        assert response.json()["hint"]

    def test_an_unknown_scope_is_a_400(self, api) -> None:
        assert self._apply(api, scope="some").status_code == 400

    def test_the_refresh_is_a_job_and_the_repair_is_a_job(self, api) -> None:
        refresh = api.client.post("/api/server/updates/refresh")
        repair = api.client.post("/api/server/updates/repair")

        assert refresh.status_code == 202 and repair.status_code == 202
        assert [q["job_type"] for q in api.jobs.queued] == [JobType.OS_REFRESH, JobType.OS_UPDATE]
        assert api.audit.events() == ["server.refresh", "server.update"]
        assert api.audit.records[1]["details"]["action"] == "repair"

    def test_there_is_nothing_to_repair_on_dnf(self, api) -> None:
        api.machine.ctx._platform = platform_for("dnf")
        api.machine.ctx._built.clear()

        response = api.client.post("/api/server/updates/repair")

        assert response.status_code == 501

    def test_the_automatic_updates_are_read_and_changed_by_a_job(self, api) -> None:
        read = api.client.get("/api/server/updates/auto")
        change = api.client.put("/api/server/updates/auto", json={"enabled": False})

        assert read.json()["mechanism"] == "unattended-upgrades"
        assert read.json()["security_only"] is True
        assert change.status_code == 202
        assert api.jobs.queued[0]["kwargs"] == {
            "enabled": False,
            "security_only": True,
            "actor": "operator",
        }
        assert api.audit.records[0]["details"]["action"] == "auto"
        assert api.audit.records[0]["details"]["enabled"] is False

    def test_a_transactional_system_cannot_change_them(self, api) -> None:
        api.machine.ctx._platform = platform_for("zypper", transactional=True)
        api.machine.ctx._built.clear()

        response = api.client.put("/api/server/updates/auto", json={"enabled": True})

        assert response.status_code == 501


class TestPower:
    def test_the_status_carries_the_checks(self, api) -> None:
        body = api.client.get("/api/server/power").json()

        assert body["scheduled"] is None
        assert body["boot_id"] == "d875e599869f472296c96f9b3e1b5356"
        assert {c["id"] for c in body["checks"]} == {"jobs", "console", "apps", "fstab", "ramdisk"}

    def _power(self, api) -> None:
        api.machine.ctx.power._apps_check = lambda runner: []

    def test_a_reboot_without_a_time_leaves_a_minute_to_cancel(self, api) -> None:
        self._power(api)

        response = api.client.post("/api/server/power/reboot", json={})

        assert response.status_code == 200, response.text
        assert response.json()["action"] == "reboot"
        assert response.json()["requested_by"] == "operator"
        assert any(call[:3] == ("shutdown", "-r", "+1") for call in api.machine.runner.calls)
        assert api.audit.events() == ["server.reboot"]
        assert api.audit.records[0]["details"]["action"] == "reboot"

    def test_now_is_still_a_minute_from_the_api(self, api) -> None:
        self._power(api)

        api.client.post("/api/server/power/reboot", json={"in_minutes": 0})

        assert any(call[:3] == ("shutdown", "-r", "+1") for call in api.machine.runner.calls)

    def test_a_time_in_the_past_is_a_400(self, api) -> None:
        self._power(api)

        response = api.client.post(
            "/api/server/power/reboot", json={"at": "2020-01-01T00:00:00+00:00"}
        )

        assert response.status_code == 400
        assert "already passed" in response.json()["detail"]

    def test_a_time_that_is_not_a_time_is_a_400_on_the_field(self, api) -> None:
        response = api.client.post("/api/server/power/reboot", json={"at": "next tuesday"})

        assert response.status_code == 400
        assert "at" in response.json()["fields"]

    def test_a_warning_is_a_409_with_the_warnings_until_it_is_forced(self, api) -> None:
        api.machine.ctx.power._apps_check = lambda runner: ["shop-example-com"]

        refused = api.client.post("/api/server/power/reboot", json={})
        forced = api.client.post("/api/server/power/reboot", json={"force": True})

        assert refused.status_code == 409
        assert refused.json()["error"] == "preflight_failed"
        assert "shop-example-com" in refused.json()["blockers"][0]
        assert forced.status_code == 200

    def test_a_shutdown_needs_the_host_name_typed(self, api) -> None:
        self._power(api)

        wrong = api.client.post("/api/server/power/shutdown", json={"confirm_hostname": "other"})
        right = api.client.post("/api/server/power/shutdown", json={"confirm_hostname": "vps-1"})

        assert wrong.status_code == 400
        assert wrong.json()["fields"]["confirm_hostname"] == "The host name does not match"
        assert right.status_code == 200
        assert right.json()["action"] == "poweroff"
        assert any(call[:2] == ("shutdown", "-P") for call in api.machine.runner.calls)
        assert api.audit.events() == ["server.reboot"]
        assert api.audit.records[0]["details"]["action"] == "shutdown"

    def test_cancelling_reports_whether_there_was_something(self, api) -> None:
        self._power(api)
        api.client.post("/api/server/power/reboot", json={})
        api.machine.host.shutdown_scheduled.write_text("MODE=reboot\n")

        first = api.client.delete("/api/server/power/scheduled")
        api.machine.host.shutdown_scheduled.unlink()
        second = api.client.delete("/api/server/power/scheduled")

        assert first.json() == {"cancelled": True}
        assert second.json() == {"cancelled": False}
        assert ("shutdown", "-c") in api.machine.runner.calls


class TestStorage:
    def test_the_page_lists_mounts_and_the_quick_candidates(self, api) -> None:
        api.machine.runner.script(
            ["journalctl", "--disk-usage"], stdout=fixture("journal", "disk-usage.txt")
        )
        api.machine.runner.script(
            ["docker", "system", "df"], stdout=fixture("docker", "system-df.json")
        )

        body = api.client.get("/api/server/storage").json()

        assert [m["mount_point"] for m in body["mounts"]] == ["/data", "/"]
        assert body["worst"]["mount_point"] == "/data"
        assert {c["id"] for c in body["candidates"]} >= {"journal", "docker-build-cache"}
        assert body["analysis_at"] is None

    def test_measuring_is_a_job_and_the_answer_is_kept_for_the_page(self, api) -> None:
        response = api.client.post("/api/server/storage/analyze")

        assert response.status_code == 202
        assert api.jobs.queued[0]["job_type"] is JobType.DISK_SCAN

        api.machine.ctx.cache.put(
            "storage.analysis",
            Analysis(
                measured_at="2026-09-29T20:00:00+00:00",
                candidates=[Candidate("releases", 5, 2, "releases")],
            ),
        )
        latest = api.client.get("/api/server/storage/analyze/latest").json()
        page = api.client.get("/api/server/storage").json()

        assert latest["measured_at"] == "2026-09-29T20:00:00+00:00"
        assert any(c["id"] == "releases" for c in page["candidates"])
        assert page["analysis_at"] == "2026-09-29T20:00:00+00:00"

    def test_before_any_scan_the_latest_is_empty(self, api) -> None:
        assert api.client.get("/api/server/storage/analyze/latest").json()["measured_at"] is None

    def test_cleanup_is_a_job_with_its_parameters(self, api) -> None:
        response = api.client.post(
            "/api/server/storage/cleanup", json={"action": "journal", "size_mb": 100}
        )

        assert response.status_code == 202
        queued = api.jobs.queued[0]
        assert queued["job_type"] is JobType.CLEANUP
        assert queued["kwargs"]["action"] == "journal"
        assert queued["kwargs"]["params"] == {"size_mb": 100}
        assert api.audit.records[0]["event"] == "server.storage"
        assert api.audit.records[0]["details"]["cleanup"] == "journal"
        assert api.audit.records[0]["details"]["size_mb"] == 100

    def test_an_action_that_is_not_on_the_list_is_a_400_and_never_a_job(self, api) -> None:
        response = api.client.post("/api/server/storage/cleanup", json={"action": "rm -rf /"})

        assert response.status_code == 400
        assert api.jobs.queued == []

    def test_a_docker_action_is_a_409_with_what_it_takes_until_confirmed(self, api) -> None:
        refused = api.client.post(
            "/api/server/storage/cleanup", json={"action": "docker-build-cache"}
        )
        confirmed = api.client.post(
            "/api/server/storage/cleanup", json={"action": "docker-build-cache", "confirm": True}
        )

        assert refused.status_code == 409
        assert refused.json()["error"] == "confirmation_required"
        assert refused.json()["required"]["commands"] == [
            "docker builder prune -f --filter until=168h"
        ]
        assert confirmed.status_code == 202
        assert len(api.jobs.queued) == 1

    def test_the_plan_says_what_an_action_would_do_without_doing_it(self, api) -> None:
        body = api.client.get("/api/server/storage/cleanup/plan?action=journal&size_mb=100").json()

        assert body["commands"] == ["journalctl --rotate", "journalctl --vacuum-size=100M"]
        assert body["needs_confirmation"] is False
        assert api.jobs.queued == []
        assert not api.machine.runner.ran("journalctl", "--rotate")

    def test_a_target_that_is_not_an_image_id_is_a_400(self, api) -> None:
        response = api.client.post(
            "/api/server/storage/cleanup",
            json={"action": "docker-image", "target": "--all", "confirm": True},
        )

        assert response.status_code == 400
        assert api.jobs.queued == []

    def test_removing_one_image_is_a_job_audited_with_the_image(self, api) -> None:
        # Owner item 53: the image id travelled as "target" next to the audit's
        # own target=, a TypeError after the job was queued - a 500 that said
        # "nothing changed" while the image was removed, unaudited.
        api.machine.runner.script(
            ["docker", "image", "ls"],
            stdout='{"Containers":"0","ID":"728109567b7e","Repository":"old","Size":"5MB","Tag":"1"}',
        )

        response = api.client.post(
            "/api/server/storage/cleanup",
            json={"action": "docker-image", "target": "728109567b7e", "confirm": True},
        )

        assert response.status_code == 202
        assert api.jobs.queued[0]["kwargs"]["params"] == {"target": "728109567b7e"}
        record = api.audit.records[0]
        assert record["target"] == "storage"
        assert record["details"]["cleanup"] == "docker-image"
        assert record["details"]["image"] == "728109567b7e"

    def test_unused_images_are_listed(self, api) -> None:
        api.machine.runner.script(
            ["docker", "image", "ls"],
            stdout='{"Containers":"0","ID":"dddddddddddd","Repository":"old","Size":"5MB","Tag":"1"}',
        )

        body = api.client.get("/api/server/storage/docker/images").json()

        assert body == [
            {
                "id": "dddddddddddd",
                "repository": "old",
                "tag": "1",
                "size": "5MB",
                "containers": "0",
            }
        ]


class TestSwap:
    @pytest.fixture(autouse=True)
    def roomy(self, monkeypatch: pytest.MonkeyPatch):
        import shutil

        usage = shutil._ntuple_diskusage(total=100 * 1024**3, used=30 * 1024**3, free=70 * 1024**3)
        monkeypatch.setattr(shutil, "disk_usage", lambda path: usage)

    def test_the_swap_of_the_machine(self, api) -> None:
        body = api.client.get("/api/server/swap").json()

        assert body["devices"] == []
        assert body["recommended"] is True
        assert body["supported"] is True
        assert body["suggested_bytes"] == 2 * 1024**3

    def test_making_swap_is_a_job(self, api) -> None:
        response = api.client.post("/api/server/swap", json={"size_mb": 2048})

        assert response.status_code == 202
        queued = api.jobs.queued[0]
        assert queued["job_type"] is JobType.SWAP
        assert queued["kwargs"] == {
            "action": "create",
            "size_mb": 2048,
            "swappiness": None,
            "actor": "operator",
        }
        assert api.audit.events() == ["server.storage"]
        assert api.audit.records[0]["details"]["action"] == "swap-create"

    def test_a_container_cannot_have_swap_made_in_it(self, api) -> None:
        api.machine.ctx._platform = platform_for("apt", container="lxc")
        api.machine.ctx._built.clear()

        response = api.client.post("/api/server/swap", json={"size_mb": 2048})

        assert response.status_code == 501
        assert "lxc" in response.json()["hint"]
        assert api.jobs.queued == []

    def test_a_size_that_would_fill_the_disk_is_a_400_before_any_job(self, api) -> None:
        response = api.client.post("/api/server/swap", json={"size_mb": 80 * 1024})

        assert response.status_code == 400
        assert api.jobs.queued == []

    def test_removal_is_a_job_and_swappiness_is_immediate(self, api) -> None:
        removal = api.client.delete("/api/server/swap")
        swappiness = api.client.put("/api/server/swap/swappiness", json={"value": 20})
        invalid = api.client.put("/api/server/swap/swappiness", json={"value": 500})

        assert removal.status_code == 202
        assert swappiness.status_code == 200
        assert swappiness.json()["steps"] == ["Set vm.swappiness to 20, now and at boot"]
        assert invalid.status_code == 400
        assert [r["details"]["action"] for r in api.audit.records] == ["swap-remove", "swappiness"]


class TestSystem:
    def test_the_clock(self, api) -> None:
        body = api.client.get("/api/server/time").json()

        assert body["timezone"] == "Atlantic/Canary"
        assert body["ntp_supported"] is True

    def test_changing_the_zone_names_the_timers_it_moves_and_is_audited(
        self, api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("zoneinfo.available_timezones", lambda: {"Europe/Madrid"})
        api.machine.runner.script(
            ["systemctl", "list-timers"],
            stdout="Wed 2026-09-30 03:00:00 UTC 5h left n/a n/a noust-backup-shop.timer x.service\n",
        )

        response = api.client.put("/api/server/time", json={"timezone": "Europe/Madrid"})

        assert response.status_code == 200, response.text
        assert response.json()["previous_timezone"] == "Atlantic/Canary"
        assert response.json()["moved_timers"] == ["noust-backup-shop.timer"]
        assert api.audit.events() == ["server.time"]
        assert api.audit.records[0]["details"]["action"] == "timezone"

    def test_a_change_of_nothing_is_a_400(self, api) -> None:
        assert api.client.put("/api/server/time", json={}).status_code == 400

    def test_a_zone_that_is_not_one_is_a_400(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("zoneinfo.available_timezones", lambda: {"Europe/Madrid"})

        response = api.client.put("/api/server/time", json={"timezone": "Europe/Madrid; reboot"})

        assert response.status_code == 400
        assert not api.machine.runner.ran("timedatectl", "set-timezone")

    def test_ntp_without_a_daemon_says_to_allow_installing_one(self, api) -> None:
        api.machine.runner.script(
            ["timedatectl", "set-ntp"], stderr="NTP not supported", exit_code=1
        )

        response = api.client.put("/api/server/time", json={"ntp": True})

        assert response.status_code == 500
        assert "chrony" in response.json()["hint"]
        assert "NTP not supported" in response.json()["output"]

    def test_identity(self, api) -> None:
        body = api.client.get("/api/server/identity").json()

        assert body["hostname"]["hostname"] == "vps-1"
        assert body["os_name"] == "Ubuntu 24.04.5 LTS"
        assert body["eol"]["status"] == "ok"
        assert len(body["load"]) == 3

    def test_renaming_and_a_name_that_is_a_command(self, api) -> None:
        good = api.client.put("/api/server/identity/hostname", json={"hostname": "web-2"})
        bad = api.client.put("/api/server/identity/hostname", json={"hostname": "web;reboot"})

        assert good.status_code == 200
        assert good.json()["steps"][0] == "Set the host name to web-2"
        assert bad.status_code == 400
        assert not api.machine.runner.ran("hostnamectl", "set-hostname", "web;reboot")
        assert api.audit.events() == ["server.hostname"]

    def _processes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            types.SimpleNamespace(
                info={
                    "pid": 1,
                    "name": "nginx",
                    "cpu_percent": 3.0,
                    "memory_percent": 1.0,
                    "memory_info": types.SimpleNamespace(rss=10 * 1024**2),
                    "status": "sleeping",
                    "username": "www-data",
                    "cmdline": ["nginx", "--token=hunter2"],
                }
            )
        ]
        fake = types.SimpleNamespace(
            process_iter=lambda fields: iter(rows),
            NoSuchProcess=KeyError,
            AccessDenied=KeyError,
            ZombieProcess=KeyError,
        )
        monkeypatch.setattr("noust.managers.server.processes._psutil", lambda: fake)

    def test_command_lines_are_only_for_a_credential_that_may_read_them(
        self, api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._processes(monkeypatch)
        api.app.dependency_overrides[get_current_session] = lambda: {"sid": "v", "scope": "read"}
        hidden = api.client.get("/api/server/processes").json()
        api.app.dependency_overrides[get_current_session] = lambda: {"sid": "a", "scope": "admin"}
        shown = api.client.get("/api/server/processes").json()

        assert hidden["processes"][0]["command"] is None
        assert "--token=hunter2" in shown["processes"][0]["command"]
        assert shown["total"] == 1

    def test_grouping_by_unit_and_a_grouping_that_does_not_exist(
        self, api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._processes(monkeypatch)

        grouped = api.client.get("/api/server/processes?group=unit")
        bad = api.client.get("/api/server/processes?group=user")

        assert grouped.status_code == 200
        assert grouped.json()["processes"] == []
        assert bad.status_code == 400


class TestLogs:
    def test_filters_reach_journalctl_checked_and_glued(self, api) -> None:
        api.machine.runner.script(
            ["journalctl"],
            stdout='{"__CURSOR":"c1","MESSAGE":"Connection refused","PRIORITY":"3","_SYSTEMD_UNIT":"nginx.service"}',
        )

        response = api.client.get(
            "/api/server/logs?unit=nginx&priority=err&since=-2h&lines=50&q=refused&boot=-1"
        )

        assert response.status_code == 200, response.text
        argv = next(c for c in api.machine.runner.calls if c[0] == "journalctl")
        assert "--unit=nginx" in argv
        assert "--priority=3" in argv
        assert "--since=-2h" in argv
        assert "--boot=-1" in argv
        assert response.json()["entries"][0]["message"] == "Connection refused"
        assert response.json()["next_cursor"] == "c1"

    @pytest.mark.parametrize(
        "query", ["unit=--all", "unit=a%20b", "since=yesterday", "priority=loud", "cursor=;rm"]
    )
    def test_a_filter_that_is_not_valid_is_a_400_and_never_reaches_journalctl(
        self, api, query: str
    ) -> None:
        response = api.client.get(f"/api/server/logs?{query}")

        assert response.status_code == 400
        assert not api.machine.runner.ran("journalctl")

    def test_the_units_come_failed_first(self, api, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeServiceManager:
            def __init__(self, runner=None) -> None:
                pass

            def list_services(self, all_services: bool = False) -> list[dict[str, Any]]:
                return [
                    {"name": "cron", "active": "active", "sub": "running"},
                    {"name": "nginx", "active": "failed", "sub": "failed"},
                    {"name": "atd", "active": "active", "sub": "running"},
                ]

        monkeypatch.setattr("noust.web.api.server.logs.ServiceManager", FakeServiceManager)

        body = api.client.get("/api/server/logs/units").json()

        assert [u["name"] for u in body] == ["nginx", "atd", "cron"]
        assert body[0]["failed"] is True

    def test_the_boots(self, api) -> None:
        api.machine.runner.script(
            ["journalctl", "--list-boots"],
            stdout="  0 fedcba9876543210fedcba9876543210 Tue 2026-09-29 06:12:00 WEST Tue 2026-09-29 22:13:35 WEST\n",
        )

        body = api.client.get("/api/server/logs/boots").json()

        assert body == [
            {
                "index": 0,
                "boot_id": "fedcba9876543210fedcba9876543210",
                "first": "Tue 2026-09-29 06:12:00 WEST",
                "last": "Tue 2026-09-29 22:13:35 WEST",
            }
        ]


class TestErrors:
    def test_a_refusal_of_the_managers_keeps_its_code_and_anything_else_the_shared_contract(
        self, api
    ) -> None:
        # ValidationError is answered by the API's one contract (400); a bare
        # ServerError, a tool that failed, is a 500 with the tool's words.
        bad = api.client.put("/api/server/identity/hostname", json={"hostname": "A B"})
        failing = api.client.get("/api/server/time")
        api.machine.runner.script(["timedatectl"], stderr="Failed to connect to bus", exit_code=1)
        failing = api.client.get("/api/server/time")

        assert bad.status_code == 400
        assert bad.json()["error"] == "validationerror"
        assert failing.status_code == 500
        assert "Failed to connect to bus" in failing.json()["output"]
