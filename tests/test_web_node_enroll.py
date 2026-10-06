# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Guided enrollment over HTTP: ``/api/fleet/enroll``.

The enrollment itself is tested with the runner (``tests/test_fleet_enroll.py``);
what is pinned here is what the HTTP layer owes: the host key step, sudo mode,
checks before a job exists, a job whose record and log never hold the
credential it was started with, and an approval that is made over everything
but it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.accounts.approvals import one_time_fields, rule_for, snapshot
from noust.core.config import Config
from noust.core.exceptions import NodeError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.web.api import node_enroll as api
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.jobs import JobStatus, JobType, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.registry import permission_for_route
from noust.web.server import create_app, get_token_manager
from tests.test_web_auth import make_config

PASSWORD = "correct-horse-battery-1234"
FINGERPRINT = "SHA256:" + "k" * 43


class FakeEnroller:
    """Records what the routes ask of the enrollment."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.validated: list[Any] = []
        self.refuse: NodeError | None = None
        self.scans: list[tuple[str, int]] = []
        self.leak = False

    def scan_host_key(self, host: str, port: int = 22) -> Any:
        self.scans.append((host, port))
        return SimpleNamespace(
            host=host,
            port=port,
            fingerprint=FINGERPRINT,
            to_dict=lambda: {
                "host": host,
                "port": port,
                "key_type": "ssh-ed25519",
                "fingerprint": FINGERPRINT,
            },
        )

    def validate(self, request: Any) -> None:
        self.validated.append(request)
        if self.refuse is not None:
            raise self.refuse

    def enroll(self, request: Any, *, actor: str | None, log: Any) -> Any:
        self.requests.append((request, actor))
        log("Connecting to the server", "info")
        if self.leak:
            # What a careless step might print: the job's own scrubber must catch it.
            log(f"authenticating with {request.credential.password}", "info")
        log("web-2 is enrolled", "success")
        return SimpleNamespace(to_dict=lambda: {"name": request.name, "status": "reachable"})


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def enroller(monkeypatch: pytest.MonkeyPatch) -> FakeEnroller:
    fake = FakeEnroller()
    monkeypatch.setattr(api, "_enroller", lambda: fake)
    return fake


@pytest.fixture
def client(sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def sign_in(client: TestClient) -> str:
    master = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master


def elevate(client: TestClient, master: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master})
    assert response.status_code == 200, response.text


def wait_for(job_id: str) -> Any:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = get_job_manager().get_job(job_id)
        if job is not None and job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
            return job
        time.sleep(0.05)
    raise AssertionError("the job never finished")


BODY = {
    "name": "web-2",
    "host": "web2.example.com",
    "host_key_fingerprint": FINGERPRINT,
    "password": PASSWORD,
    "access": "deploy",
}


def test_the_host_key_step_shows_what_answers(client: TestClient, enroller: FakeEnroller) -> None:
    sign_in(client)

    response = client.post(
        "/api/fleet/enroll/host-key", json={"host": "web2.example.com", "port": 2222}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "host": "web2.example.com",
        "port": 2222,
        "key_type": "ssh-ed25519",
        "fingerprint": FINGERPRINT,
    }
    assert enroller.scans == [("web2.example.com", 2222)]


def test_nobody_anonymous_enrolls_or_scans(client: TestClient, enroller: FakeEnroller) -> None:
    assert client.post("/api/fleet/enroll", json=BODY).status_code == 401
    assert (
        client.post("/api/fleet/enroll/host-key", json={"host": "web2.example.com"}).status_code
        == 401
    )


def test_enrolling_asks_for_sudo_mode_first(client: TestClient, enroller: FakeEnroller) -> None:
    sign_in(client)

    response = client.post("/api/fleet/enroll", json=BODY)

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"
    assert enroller.validated == []


def test_the_checks_that_need_no_connection_run_before_a_job_exists(
    client: TestClient, enroller: FakeEnroller
) -> None:
    enroller.refuse = NodeError(
        "This central may not register servers yet", details="Two-factor sign-in is not enabled."
    )
    elevate(client, sign_in(client))

    response = client.post("/api/fleet/enroll", json=BODY)

    assert response.status_code in (400, 409), response.text
    assert "Two-factor" in response.text
    assert enroller.requests == []


def test_enrolling_is_a_job_and_the_credential_is_in_nothing_it_leaves(
    client: TestClient, enroller: FakeEnroller
) -> None:
    enroller.leak = True
    elevate(client, sign_in(client))

    response = client.post("/api/fleet/enroll", json=BODY)

    assert response.status_code == 202, response.text
    assert PASSWORD not in response.text
    job = wait_for(response.json()["job_id"])
    assert job.status == JobStatus.COMPLETED
    assert job.type == JobType.NODE_ENROLL
    request, actor = enroller.requests[0]
    assert request.credential.password == PASSWORD
    assert request.access == "deploy" and request.name == "web-2"
    assert actor
    # Everything the job keeps: its record, its log, its result, what the console shows.
    kept = json.dumps(job.to_dict(), default=str) + json.dumps(job.result, default=str)
    kept += "\n".join(entry.message for entry in job.logs)
    assert PASSWORD not in kept
    assert "Connecting to the server" in kept and "web-2 is enrolled" in kept
    assert job.result == {"name": "web-2", "status": "reachable"}
    persisted = get_job_manager()._jobs[job.id]
    assert PASSWORD not in json.dumps(persisted.to_dict(), default=str)


def test_a_private_key_is_carried_the_same_way(client: TestClient, enroller: FakeEnroller) -> None:
    elevate(client, sign_in(client))
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
    body = {k: v for k, v in BODY.items() if k != "password"} | {
        "private_key": key,
        "passphrase": "p4ss-phrase",
    }

    response = client.post("/api/fleet/enroll", json=body)

    assert response.status_code == 202, response.text
    job = wait_for(response.json()["job_id"])
    request, _actor = enroller.requests[0]
    assert request.credential.private_key == key
    assert request.credential.passphrase == "p4ss-phrase"
    assert "p4ss-phrase" not in json.dumps(job.to_dict(), default=str)
    assert "BEGIN OPENSSH" not in json.dumps(job.to_dict(), default=str)


def test_a_malformed_body_is_refused_without_echoing_it(
    client: TestClient, enroller: FakeEnroller
) -> None:
    elevate(client, sign_in(client))

    response = client.post("/api/fleet/enroll", json={**BODY, "port": 99999, "access": "owner"})

    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert enroller.requests == []


def test_a_body_prints_without_its_credential() -> None:
    body = api.EnrollBody(**BODY)

    assert PASSWORD not in repr(body) and PASSWORD not in str(body)


def test_changing_the_fleet_is_fleet_manage() -> None:
    assert permission_for_route("POST", "/api/fleet/enroll") == Permission.FLEET_MANAGE
    assert permission_for_route("POST", "/api/fleet/enroll/host-key") == Permission.FLEET_MANAGE


# ---------------------------------------------------------------------------
# The second person approves everything but the credential
# ---------------------------------------------------------------------------


def test_enrolling_needs_a_second_person_when_the_policy_asks_and_not_for_the_credential() -> None:
    rule = rule_for("POST", "/api/fleet/enroll", [Permission.FLEET_MANAGE], BODY, {})

    assert rule is not None and rule.action == "fleet.node.enroll"
    assert one_time_fields("POST", "/api/fleet/enroll") == ("password", "private_key", "passphrase")


def test_what_is_approved_does_not_depend_on_the_password_and_never_shows_it() -> None:
    first = json.dumps({**BODY, "password": "one-password"}).encode()
    second = json.dumps({**BODY, "password": "another-password"}).encode()
    omit = one_time_fields("POST", "/api/fleet/enroll")

    digest_one, shown_one = snapshot("POST", "/api/fleet/enroll", [], first, omit=omit)
    digest_two, shown_two = snapshot("POST", "/api/fleet/enroll", [], second, omit=omit)

    assert digest_one == digest_two, "the approval is for the server, not for a password"
    assert "one-password" not in json.dumps(shown_one) and "another-password" not in json.dumps(
        shown_two
    )
    assert shown_one["body"]["host"] == "web2.example.com"
    # Another server is another call.
    other = json.dumps({**BODY, "host": "web3.example.com"}).encode()
    assert snapshot("POST", "/api/fleet/enroll", [], other, omit=omit)[0] != digest_one


def test_a_route_without_one_time_fields_is_fingerprinted_whole() -> None:
    body = json.dumps({"password": "x"}).encode()

    with_all = snapshot("POST", "/api/other", [], body)
    omitted = snapshot("POST", "/api/other", [], body, omit=())

    assert with_all == omitted
