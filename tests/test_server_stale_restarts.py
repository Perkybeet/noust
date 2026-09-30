"""
Restarting the services an update left running on replaced libraries.

needrestart, ``dnf needs-restarting -s`` and ``zypper ps -sss`` list the units
still running code that an update replaced on disk. Restarting them is how they
take the fix without a reboot, but some of them must never be restarted from a
web page: D-Bus, logind, a getty, a user session, the network, the container
runtime. The guard is in ServiceManager, the chokepoint every restart passes
(rule 4): only a unit the probe reported, and never one of those. The console's
own unit is restarted last, without blocking, and the plan says so first.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import ServiceError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.server.restarts import plan_service_restarts, restart_services
from noust.managers.service_manager import ServiceManager, outdated_restart_refusal
from noust.web.api import server as server_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.api.server import jobs
from noust.web.api.server.common import set_server_context
from noust.web.jobs import Job, JobContext, JobType
from noust.web.permissions.routes_server import ROUTES
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    RecordingAudit,
    make_machine,
    no_package_manager_running,
)

pytestmark = pytest.mark.usefixtures("no_package_manager_running")

NEEDRESTART = (
    "NEEDRESTART-VER: 3.6\n"
    "NEEDRESTART-KSTA: 1\n"
    "NEEDRESTART-SVC: cron.service\n"
    "NEEDRESTART-SVC: dbus.service\n"
    "NEEDRESTART-SVC: nginx.service\n"
    "NEEDRESTART-SVC: noust-web.service\n"
    "NEEDRESTART-SVC: user@0.service\n"
)


# ---------------------------------------------------------------------------
# The guard, in ServiceManager
# ---------------------------------------------------------------------------


class TestTheGuard:
    def test_a_reported_unit_that_is_not_noust_s_is_restarted(self) -> None:
        runner = FakeRunner()

        ServiceManager(runner=runner).restart_outdated(
            "nginx.service", outdated=["nginx.service", "cron.service"]
        )

        assert runner.calls == [("systemctl", "restart", "nginx.service")]

    def test_a_unit_the_probe_did_not_report_is_refused(self) -> None:
        runner = FakeRunner()

        with pytest.raises(ServiceError) as refused:
            ServiceManager(runner=runner).restart_outdated(
                "ssh.service", outdated=["nginx.service"]
            )

        assert "does not run replaced libraries" in refused.value.message
        assert runner.calls == []

    @pytest.mark.parametrize(
        "unit",
        [
            "dbus.service",
            "systemd-logind.service",
            "getty@tty1.service",
            "user@0.service",
            "systemd-networkd.service",
            "NetworkManager.service",
            "docker.service",
            "containerd.service",
        ],
    )
    def test_what_ends_sessions_the_network_or_containers_is_never_restarted(
        self, unit: str
    ) -> None:
        runner = FakeRunner()

        with pytest.raises(ServiceError) as refused:
            ServiceManager(runner=runner).restart_outdated(unit, outdated=[unit])

        assert outdated_restart_refusal(unit) is not None
        assert "reboot" in (refused.value.details or "").lower()
        assert runner.calls == []

    def test_a_name_that_is_not_a_unit_is_refused(self) -> None:
        runner = FakeRunner()

        with pytest.raises(ServiceError):
            ServiceManager(runner=runner).restart_outdated(
                "nginx.service; rm", outdated=["nginx.service; rm"]
            )

        assert runner.calls == []

    def test_systemctl_s_words_are_the_error(self) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "restart"], stderr="Job for nginx.service failed.", exit_code=1)

        with pytest.raises(ServiceError) as failed:
            ServiceManager(runner=runner).restart_outdated(
                "nginx.service", outdated=["nginx.service"]
            )

        assert failed.value.details == "Job for nginx.service failed."

    def test_without_blocking_when_asked(self) -> None:
        runner = FakeRunner()

        ServiceManager(runner=runner).restart_outdated(
            "noust-web.service", outdated=["noust-web.service"], no_block=True
        )

        assert runner.calls == [("systemctl", "restart", "--no-block", "noust-web.service")]


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


class TestPlan:
    def test_every_reported_unit_but_the_refused_ones_with_the_console_last(self) -> None:
        plan = plan_service_restarts(
            ["noust-web.service", "cron.service", "dbus.service", "nginx.service"]
        )

        assert plan.restart == ("cron.service", "nginx.service", "noust-web.service")
        assert set(plan.refused) == {"dbus.service"}
        assert plan.restarts_console is True

    def test_a_choice_is_limited_to_what_the_probe_reported(self) -> None:
        plan = plan_service_restarts(
            ["cron.service", "nginx.service"], requested=["nginx.service", "ssh.service"]
        )

        assert plan.restart == ("nginx.service",)
        assert "does not run replaced libraries" in plan.refused["ssh.service"]
        assert plan.restarts_console is False

    def test_the_restarts_go_through_the_guard_and_one_failure_does_not_stop_the_rest(
        self,
    ) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "restart", "cron.service"], stderr="cron: failed", exit_code=1)
        said: list[str] = []
        plan = plan_service_restarts(["cron.service", "nginx.service", "noust-web.service"])

        outcome = restart_services(plan, ServiceManager(runner=runner), on_line=said.append)

        assert outcome.restarted == ("nginx.service", "noust-web.service")
        assert outcome.failed == {"cron.service": "cron: failed"}
        assert runner.calls[-1] == ("systemctl", "restart", "--no-block", "noust-web.service")
        assert any("console" in line for line in said)


# ---------------------------------------------------------------------------
# The API and the job
# ---------------------------------------------------------------------------


class FakeJobs:
    def __init__(self) -> None:
        self.queued: list[dict[str, Any]] = []

    def create_job(self, **kwargs: Any) -> Any:
        self.queued.append(kwargs)
        job = types.SimpleNamespace(
            id=f"job{len(self.queued)}",
            type=kwargs["job_type"],
            name=kwargs["name"],
            status=types.SimpleNamespace(value="pending"),
        )
        job.to_dict = lambda: {"id": job.id, "type": job.type.value, "name": job.name}
        return job

    def get_active_jobs(self) -> list[Any]:
        return []


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
    machine = make_machine(tmp_path, monkeypatch, store)
    machine.runner.script(["needrestart", "-b"], stdout=NEEDRESTART)
    queued = FakeJobs()
    audit = RecordingAudit()
    for module in ("noust.web.api.server.common", "noust.web.api.server.updates"):
        monkeypatch.setattr(f"{module}.get_job_manager", lambda: queued)
    monkeypatch.setattr("noust.core.audit.record", audit.record)
    set_server_context(machine.ctx)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(server_api.router, prefix="/api/server")
    session = {"sid": "operator", "type": "master"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    try:
        yield types.SimpleNamespace(
            client=TestClient(app, raise_server_exceptions=False),
            jobs=queued,
            audit=audit,
            machine=machine,
        )
    finally:
        set_server_context(None)


class TestTheApi:
    def test_the_plan_says_what_restarts_what_does_not_and_why(self, api) -> None:
        body = api.client.get("/api/server/updates/restarts").json()

        assert body["services"] == [
            "cron.service",
            "dbus.service",
            "nginx.service",
            "noust-web.service",
            "user@0.service",
        ]
        assert body["restart"] == ["cron.service", "nginx.service", "noust-web.service"]
        assert {entry["unit"] for entry in body["refused"]} == {"dbus.service", "user@0.service"}
        assert body["restarts_console"] is True

    def test_restarting_is_a_job_that_says_when_the_console_restarts(self, api) -> None:
        response = api.client.post(
            "/api/server/updates/restarts", json={"services": ["nginx.service"]}
        )

        assert response.status_code == 202, response.text
        [queued] = api.jobs.queued
        assert queued["job_type"] is JobType.SERVICE_ACTION
        assert queued["kwargs"]["services"] == ["nginx.service"]
        assert response.json()["restarts_console"] is False

    def test_nothing_restartable_is_a_409_with_the_reasons(self, api) -> None:
        response = api.client.post(
            "/api/server/updates/restarts", json={"services": ["dbus.service"]}
        )

        assert response.status_code == 409, response.text
        assert api.jobs.queued == []
        assert "dbus.service" in response.text

    def test_the_routes_are_in_the_permission_map(self) -> None:
        assert ROUTES[("GET", "/api/server/updates/restarts")] == "server.read"
        assert ROUTES[("POST", "/api/server/updates/restarts")] == "server.manage"

    def test_the_job_restarts_and_audits(self, api, monkeypatch) -> None:
        set_server_context(api.machine.ctx)
        job = Job(id="j", type=JobType.SERVICE_ACTION, name="n", description="d")
        context = JobContext(job, lambda _job: None)

        result = jobs.restart_services_job(
            services=["nginx.service", "cron.service"], actor="yago", job_context=context
        )

        assert result["restarted"] == ["cron.service", "nginx.service"]
        assert ("systemctl", "restart", "nginx.service") in api.machine.runner.calls
        assert "server.update" in api.audit.events()


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


class TestTheCommand:
    def test_the_command_restarts_what_may_restart_and_names_what_it_leaves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store
    ) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli
        from noust.cli.commands import server as server_cli

        machine = make_machine(tmp_path, monkeypatch, store)
        machine.runner.script(["needrestart", "-b"], stdout=NEEDRESTART)
        monkeypatch.setattr(server_cli, "build_context", lambda: machine.ctx)
        monkeypatch.setattr(server_cli, "check_root", lambda: True)

        result = CliRunner().invoke(root_cli, ["server", "updates", "restart-services", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Left as it is: dbus.service" in result.output
        assert ("systemctl", "restart", "cron.service") in machine.runner.calls
        assert ("systemctl", "restart", "--no-block", "noust-web.service") in machine.runner.calls
        assert not any(
            "dbus.service" in call for call in machine.runner.calls if call[0] == "systemctl"
        )
