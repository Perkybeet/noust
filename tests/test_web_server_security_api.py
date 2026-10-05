# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``/api/server/security``: a translation of HTTP into ServerSecurity and back.

The managers have their own tests; what is pinned here is what the endpoints
promise the console: a change a guard refuses answers 400 at once with the
guided steps in ``hint`` and queues nothing; a change that passes is a job
whose result carries the pending change; confirming without a new SSH login
is refused and confirming after one works; EPEL is a 409 with ``required``;
every route is in the permission map, every change needs sudo mode, and a
central's fleet token cannot change SSH or the firewall unless the node
allowed host access.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import NoustError
from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.managers.server import host as host_module
from noust.managers.server import security_checks
from noust.managers.server.security import ServerSecurity
from noust.managers.server.security_pending import CONFIRM_WINDOW
from noust.managers.server.security_proof import NEGATIVE_TTL
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated
from noust.web.api.server import security as security_api
from noust.web.jobs import Job, JobContext, JobStatus, JobType
from noust.web.permissions.registry import route_map
from tests.server_security_support import ED_FP, NOW, FakeHost, FakeSshd, accepted

PREFIX = "/api/server/security"


class InlineJobs:
    """Runs each job at once, on the request's thread, keeping its log."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def create_job(self, **kwargs: Any) -> Job:
        job = Job(
            id=f"job{len(self.jobs) + 1}",
            type=kwargs["job_type"],
            name=kwargs["name"],
            description=kwargs["description"],
            metadata=kwargs.get("metadata") or {},
            actor=kwargs.get("actor"),
        )
        context = JobContext(job, lambda _job: None)
        try:
            job.result = kwargs["func"](**kwargs["kwargs"], job_context=context)
            job.status = JobStatus.COMPLETED
        except NoustError as exc:
            job.error = str(exc)
            job.status = JobStatus.FAILED
        self.jobs.append(job)
        return job


@pytest.fixture(autouse=True)
def fresh() -> None:
    host_module.reset_platform_cache()
    security_checks.forget_report()
    yield
    security_checks.wait_for_refresh(timeout=30)
    host_module.reset_platform_cache()
    security_checks.forget_report()


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
def machine(tmp_path: Path, store: NoustStore, monkeypatch: pytest.MonkeyPatch):
    host = FakeHost(tmp_path / "root")
    host.write("/etc/os-release", 'ID=debian\nVERSION_ID="12"\n')
    runner = FakeSshd(host)
    runner.only_knows("apt-get", "ufw", "systemctl", "sshd", "fail2ban-client")
    runner.script(["ufw", "status", "verbose"], stdout="Status: inactive\n")
    runner.script(["ufw", "show", "added"], stdout="")
    runner.script(["fail2ban-client", "ping"], stdout="Server replied: pong\n")
    runner.script(["fail2ban-client", "status"], stdout="Status\n`- Jail list:\tsshd\n")

    # The console reads a pending change again every few seconds and the proof is remembered
    # for a few: a test that expects a new login to show moves this on.
    moment = types.SimpleNamespace(now=NOW)

    def build(*, actor: str, on_output: Any = None) -> ServerSecurity:
        return ServerSecurity(
            actor=actor,
            runner=runner,
            host=host.paths,
            changes=tmp_path / "changes",
            on_output=on_output,
            console_port=8080,
            clock=lambda: moment.now,
            python="/usr/bin/python3",
        )

    monkeypatch.setattr(security_api, "ServerSecurity", build)
    jobs = InlineJobs()
    monkeypatch.setattr(security_api, "get_job_manager", lambda: jobs)
    return types.SimpleNamespace(host=host, runner=runner, jobs=jobs, moment=moment)


@pytest.fixture
def client(machine) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(security_api.router, prefix="/api/server")
    session = {"sid": "master", "type": "master"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


class TestReads:
    def test_the_ssh_view_shows_effective_values_and_every_fix(self, client):
        response = client.get(f"{PREFIX}/ssh")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["effective"]["PasswordAuthentication"] == "yes"
        assert body["unit"]["service"] == "ssh.service"
        assert body["fixes"]["disable-passwords"]["allowed"] is False
        assert body["confirm_window"] == CONFIRM_WINDOW == 300

    def test_the_keys_view_lists_root_first(self, client):
        body = client.get(f"{PREFIX}/ssh/keys").json()

        assert [account["user"] for account in body] == ["root", "alice"]
        assert body[0]["files"][0]["keys"][0]["fingerprint"] == ED_FP

    def test_the_checks_answer_with_counts(self, client, monkeypatch):
        monkeypatch.setattr(
            ServerSecurity,
            "checks",
            lambda self, refresh=False: security_checks.run_checks(
                self.probe, risks=self.risks, host_checks=False, console_port=8080
            ),
        )

        body = client.get(f"{PREFIX}/checks").json()

        assert body["counts"]["warning"] >= 1
        check = next(item for item in body["checks"] if item["id"] == "ssh.password_auth")
        assert check["status"] == "warn" and check["fix"]["kind"] == "guided"

    def test_the_overview_before_any_run_starts_them_and_says_so(self, client):
        body = client.get(PREFIX).json()

        assert body["checked_at"] is None and body["pending"] == []
        assert body["checking"] is True
        assert security_checks.wait_for_refresh(timeout=30)
        after = client.get(PREFIX).json()
        assert after["checking"] is False
        assert after["checked_at"] is not None and after["counts"] is not None

    def test_an_unknown_fix_is_404(self, client):
        assert client.get(f"{PREFIX}/ssh/fixes/open-sesame").status_code == 404


class TestChanges:
    def test_a_refused_fix_answers_at_once_with_the_steps_and_queues_nothing(self, client, machine):
        response = client.post(f"{PREFIX}/ssh/fixes/disable-passwords")

        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "accessguarderror"
        assert "ssh-keygen -t ed25519" in body["hint"]
        assert machine.jobs.jobs == []

    def test_an_allowed_fix_is_a_job_ending_in_a_pending_change(self, client, machine):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")

        response = client.post(f"{PREFIX}/ssh/fixes/disable-passwords")

        assert response.status_code == 202, response.text
        job = machine.jobs.jobs[0]
        assert job.type == JobType.SERVER_SECURITY and job.status == JobStatus.COMPLETED
        change = job.result["change"]
        assert change["status"] == "pending" and change["expires_at"] == NOW + CONFIRM_WINDOW
        assert any("undoes itself" in entry.message for entry in job.logs)
        assert any(entry.message == "$ sshd -t" for entry in job.logs)

    def test_confirming_needs_a_new_login(self, client, machine):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")
        client.post(f"{PREFIX}/ssh/fixes/disable-passwords")
        change_id = machine.jobs.jobs[0].result["change"]["id"]

        early = client.post(f"{PREFIX}/changes/{change_id}/confirm")
        machine.runner.script(
            ["journalctl"], stdout=accepted("root", ED_FP, at=NOW + 40, port=61000) + "\n"
        )
        later = client.post(f"{PREFIX}/changes/{change_id}/confirm")

        assert early.status_code == 400 and "open a NEW SSH session" in early.json()["hint"]
        assert later.status_code == 200 and later.json()["status"] == "confirmed"
        listed = client.get(f"{PREFIX}/changes").json()
        assert [item["status"] for item in listed] == ["confirmed"]

    def test_the_listing_says_whether_the_login_that_keeps_a_change_is_on_record(
        self, client, machine
    ):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")
        client.post(f"{PREFIX}/ssh/fixes/disable-passwords")
        change_id = machine.jobs.jobs[0].result["change"]["id"]

        before = client.get(f"{PREFIX}/changes").json()[0]
        # The central's own tunnel reconnecting is not an operator getting in.
        machine.runner.script(
            ["journalctl"], stdout=accepted("noust-tunnel", ED_FP, at=NOW + 20) + "\n"
        )
        machine.moment.now += NEGATIVE_TTL
        tunnel = client.get(f"{PREFIX}/changes").json()[0]
        machine.runner.script(
            ["journalctl"],
            stdout=accepted("root", ED_FP, at=NOW + 40, source="203.0.113.5", port=61000) + "\n",
        )
        machine.moment.now += NEGATIVE_TTL
        after = client.get(f"{PREFIX}/changes").json()[0]
        overview = client.get(PREFIX).json()["pending"][0]

        assert before["id"] == change_id and before["status"] == "pending"
        assert (before["proof_seen"], before["proof_login"]) == (False, None)
        assert before["proof_readable"] is True and before["proof_error"] == ""
        assert tunnel["proof_seen"] is False
        assert after["proof_seen"] is True
        assert after["proof_login"] == {"user": "root", "source": "203.0.113.5", "at": NOW + 40}
        # The summary the console's tabs read says the same, from the same code.
        assert overview["proof_seen"] is True
        # And Keep, which checks the same thing, then works.
        assert client.post(f"{PREFIX}/changes/{change_id}/confirm").status_code == 200

    def test_the_listing_says_when_the_login_history_cannot_be_read(self, client, machine):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")
        client.post(f"{PREFIX}/ssh/fixes/disable-passwords")
        machine.runner.script(["journalctl"], stderr="No journal files were found.", exit_code=1)

        change = client.get(f"{PREFIX}/changes").json()[0]

        assert change["proof_seen"] is False and change["proof_readable"] is False
        assert "No journal files were found." in change["proof_error"]

    def test_a_settled_change_is_not_looked_up_in_the_journal(self, client, machine):
        machine.runner.script(["journalctl"], stdout=accepted("root", ED_FP, at=NOW - 3600) + "\n")
        client.post(f"{PREFIX}/ssh/fixes/disable-passwords")
        change_id = machine.jobs.jobs[0].result["change"]["id"]
        client.post(f"{PREFIX}/changes/{change_id}/revert")
        reads = len(machine.runner.calls_to("journalctl"))

        listed = client.get(f"{PREFIX}/changes").json()[0]

        assert listed["status"] == "reverted" and listed["proof_seen"] is False
        assert len(machine.runner.calls_to("journalctl")) == reads

    def test_a_pending_change_blocks_the_next_one_at_once(self, client, machine):
        client.post(f"{PREFIX}/ssh/fixes/verbose-logging")

        response = client.post(f"{PREFIX}/firewall/enable")

        assert response.status_code == 400
        assert "waiting for confirmation" in response.json()["detail"]
        assert len(machine.jobs.jobs) == 1

    def test_reverting_puts_things_back(self, client, machine):
        client.post(f"{PREFIX}/ssh/fixes/verbose-logging")
        change_id = machine.jobs.jobs[0].result["change"]["id"]

        response = client.post(f"{PREFIX}/changes/{change_id}/revert")

        assert response.status_code == 200 and response.json()["status"] == "reverted"
        assert machine.host.read("/etc/ssh/sshd_config.d/00-noust.conf") is None

    def test_a_rule_that_closes_ssh_is_refused_at_once(self, client, machine):
        response = client.post(f"{PREFIX}/firewall/rules", json={"action": "deny", "port": 22})

        assert response.status_code == 400
        assert response.json()["error"] == "accessguarderror"
        assert machine.jobs.jobs == []

    def test_a_malformed_rule_is_a_validation_error(self, client):
        response = client.post(
            f"{PREFIX}/firewall/rules", json={"action": "allow", "port": 80, "source": "x; y"}
        )

        assert response.status_code == 400
        assert response.json()["fields"] == {"source": "'x; y' is not an address or a network"}

    def test_epel_is_a_409_with_what_to_confirm(self, client, machine):
        machine.host.write(
            "/etc/os-release", 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.4"\n'
        )
        machine.runner.only_knows("dnf", "systemctl", "sshd")

        response = client.post(f"{PREFIX}/fail2ban/install", json={})

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "confirmation_required"
        assert body["required"]["epel"] is True
        assert machine.jobs.jobs == []

    def test_a_key_is_added_through_a_job(self, client, machine):
        from tests.server_security_support import RSA_3072

        response = client.post(f"{PREFIX}/ssh/keys", json={"user": "alice", "public_key": RSA_3072})

        assert response.status_code == 202
        assert machine.jobs.jobs[0].result["added"] is True

    def test_a_bad_key_is_refused_before_a_job(self, client, machine):
        response = client.post(f"{PREFIX}/ssh/keys", json={"user": "alice", "public_key": "nope"})

        assert response.status_code == 400 and machine.jobs.jobs == []


class TestRisks:
    def test_accepting_and_withdrawing_a_risk(self, client, monkeypatch):
        response = client.put(
            f"{PREFIX}/risks/fw.inactive",
            json={
                "reason": "The provider's firewall filters this network",
                "expires_at": "2026-12-31",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["expires_at"] == "2026-12-31T23:59:59+00:00"
        assert [item["check_id"] for item in client.get(f"{PREFIX}/risks").json()] == [
            "fw.inactive"
        ]
        assert client.delete(f"{PREFIX}/risks/fw.inactive").status_code == 200
        assert client.delete(f"{PREFIX}/risks/fw.inactive").status_code == 404

    def test_a_date_that_is_not_one(self, client):
        response = client.put(
            f"{PREFIX}/risks/fw.inactive",
            json={"reason": "long enough reason", "expires_at": "soon"},
        )

        assert response.status_code == 400
        assert response.json()["fields"] == {"expires_at": "'soon' is not a date"}


class TestContract:
    def test_every_route_is_in_the_permission_map(self, client):
        from noust.web.api.openapi import api_routes

        routes = {
            (method, str(route.path_format))
            for route in api_routes(client.app.routes)
            for method in route.methods
            if str(route.path_format).startswith(PREFIX)
        }
        permissions = route_map()

        assert len(routes) == 24
        assert routes <= set(permissions)

    def test_every_access_change_needs_host_access(self):
        permissions = route_map()
        access = {
            ("POST", f"{PREFIX}/ssh/fixes/{{fix}}"),
            ("POST", f"{PREFIX}/ssh/keys"),
            ("POST", f"{PREFIX}/ssh/keys/remove"),
            ("POST", f"{PREFIX}/firewall/rules"),
            ("DELETE", f"{PREFIX}/firewall/rules/{{rule_id}}"),
            ("POST", f"{PREFIX}/firewall/enable"),
            ("POST", f"{PREFIX}/firewall/disable"),
            ("POST", f"{PREFIX}/checks/{{check_id}}/fix"),
        }

        assert {permissions[key] for key in access} == {"server.host_access"}
        assert permissions[("GET", f"{PREFIX}/ssh")] == "server.read"

    def test_every_change_but_a_refresh_asks_for_sudo_mode(self, client):
        from noust.web.api.openapi import ELEVATION_EXTENSION, annotate_elevation

        schema = annotate_elevation(client.app.openapi(), client.app.routes)
        unelevated = {
            (method, path)
            for path, operations in schema["paths"].items()
            for method, operation in operations.items()
            if method in {"post", "put", "delete"} and not operation.get(ELEVATION_EXTENSION)
        }

        assert unelevated == {("post", f"{PREFIX}/checks/refresh")}


class TestFleet:
    """A central's fleet token, through the real admission, on a node that never granted host access."""

    @pytest.fixture
    def node(self, sandbox: Path, runner, monkeypatch: pytest.MonkeyPatch, machine):
        from noust.core.config import Config
        from noust.web.server import create_app, get_token_manager
        from tests.test_web_auth import make_config

        monkeypatch.setattr(
            "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
        )
        Config.reset_instance()
        app = create_app(make_config(sandbox))
        token = str(get_token_manager().create_fleet_token("fleet-hub")["token"])
        return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False), token

    def _headers(self, token: str) -> dict[str, str]:
        from noust.web.auth import (
            FLEET_ACTOR_HEADER,
            FLEET_ACTOR_SCOPE_HEADER,
            FLEET_ELEVATED_HEADER,
        )

        return {
            "Authorization": f"Bearer {token}",
            FLEET_ACTOR_HEADER: "ops@hub",
            FLEET_ACTOR_SCOPE_HEADER: "admin",
            FLEET_ELEVATED_HEADER: "1",
        }

    def test_the_central_reads_but_cannot_change_ssh_or_the_firewall(self, node):
        client, token = node

        read = client.get(f"{PREFIX}/ssh/keys", headers=self._headers(token))
        change = client.post(f"{PREFIX}/ssh/fixes/verbose-logging", headers=self._headers(token))
        rule = client.post(
            f"{PREFIX}/firewall/rules",
            json={"action": "allow", "port": 8443},
            headers=self._headers(token),
        )

        assert read.status_code == 200, read.text
        assert change.status_code == 403 and rule.status_code == 403
        assert "server.host_access" in change.text
