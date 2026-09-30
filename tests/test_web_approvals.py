"""
Four-eyes approvals at the chokepoint: the call that becomes a request, and runs once approved.

Driven over HTTP against the whole console, with the one root-equivalent
route that is simplest to fake (``POST /api/cron``, whose manager is replaced
by a recorder), and a role change, which is an account's own route. What is
pinned is the promise of the spec (§2.5): with approvals on, such a call does
not run; a second person decides; the same requester then runs exactly that
call, once, and both people are on record.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from noust.core import audit, totp
from noust.core.accounts import AccountManager, AuthPolicy, passwords
from noust.core.accounts import approvals as approvals_module
from noust.core.accounts.approvals import ApprovalPolicy
from noust.core.store import NoustStore
from noust.web.auth import (
    CSRF_HEADER_NAME,
    FLEET_ACTOR_HEADER,
    FLEET_ACTOR_ROLE_HEADER,
    FLEET_ELEVATED_HEADER,
    SecurityConfig,
)
from noust.web.server import create_app, get_token_manager

PASSWORD = "correct horse battery staple"
JOB = {"name": "nightly", "command": "/usr/bin/true", "schedule": "daily"}


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@dataclass
class Created:
    name: str


class RecordingCron:
    """Stands in for CronManager: records the jobs it was asked to create."""

    created: list[Any] = []

    def __init__(self, verbose: bool = False) -> None:
        pass

    def create_job(self, job: Any) -> Created:
        RecordingCron.created.append(job)
        return Created(job.name)

    def get_job(self, name: str) -> None:
        return None


@pytest.fixture(autouse=True)
def cron(monkeypatch: pytest.MonkeyPatch) -> type[RecordingCron]:
    RecordingCron.created = []
    monkeypatch.setattr("noust.web.api.cron.CronManager", RecordingCron)
    return RecordingCron


@pytest.fixture
def policy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The approval policy in force, editable by a test."""
    settings: dict[str, Any] = {"enabled": True}
    monkeypatch.setattr(
        approvals_module, "load_approval_policy", lambda: ApprovalPolicy(**settings)
    )
    return settings


@pytest.fixture
def app(sandbox: Path) -> Any:
    config = SecurityConfig(
        state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
    )
    return create_app(config)


class Person:
    """A signed-in account in its own browser, with codes to confirm with."""

    def __init__(self, app: Any, username: str, role: str, person_ref: str | None = None) -> None:
        manager = get_token_manager().accounts
        account = manager.create(username, role, password=PASSWORD, person_ref=person_ref)
        secret = manager.begin_totp(account.id)
        codes = manager.confirm_totp(account.id, totp.totp_now(secret))
        assert codes is not None
        self.codes = list(codes)
        self.client = TestClient(app, client=("testclient", 50000))
        response = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": PASSWORD, "totp_code": self.codes.pop()},
        )
        assert response.status_code == 200, response.text
        self.csrf = response.json()["csrf_token"]

    def headers(self, **extra: str) -> dict[str, str]:
        return {CSRF_HEADER_NAME: self.csrf, **extra}

    def elevate(self) -> None:
        response = self.client.post(
            "/api/auth/elevate",
            json={"password": PASSWORD, "code": self.codes.pop()},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text

    def post(self, path: str, body: Any = None, **headers: str) -> Any:
        return self.client.post(path, json=body, headers=self.headers(**headers))

    def patch(self, path: str, body: Any, **headers: str) -> Any:
        return self.client.patch(path, json=body, headers=self.headers(**headers))


def audit_actions() -> list[dict[str, Any]]:
    path = audit.get_log().path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestWithoutApprovals:
    def test_nothing_changes_while_approvals_are_off(self, app: Any, cron: Any) -> None:
        alice = Person(app, "alice", "admin")
        alice.elevate()
        response = alice.post("/api/cron", JOB)
        assert response.status_code == 201, response.text
        assert len(cron.created) == 1


class TestTheFourEyes:
    def test_the_call_becomes_a_request_then_runs_once_approved(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()

        asked = alice.post(
            "/api/cron", JOB, **{"X-Noust-Reason": quote("Nightly report, ticket 42")}
        )
        assert asked.status_code == 202, asked.text
        assert asked.json()["error"] == "approval_required"
        approval_id = asked.headers["X-Noust-Approval-Request"]
        assert asked.headers["Location"] == f"/api/approvals/{approval_id}"
        assert asked.json()["fields"]["approval"] == approval_id
        assert cron.created == []

        mine = alice.client.get("/api/approvals").json()["approvals"]
        assert [item["id"] for item in mine] == [int(approval_id)]
        assert mine[0]["reason"] == "Nightly report, ticket 42"
        assert mine[0]["parameters"]["body"]["command"] == "/usr/bin/true"
        assert mine[0]["mine"] is True and mine[0]["can_decide"] is False

        waiting = sam.client.get("/api/approvals", params={"state": "requested"}).json()
        assert waiting["pending"] == 1 and waiting["approvals"][0]["can_decide"] is True
        sam.elevate()
        decided = sam.post(f"/api/approvals/{approval_id}/approve", {"comment": "Looks fine"})
        assert decided.status_code == 200, decided.text
        assert decided.json()["state"] == "approved"

        ran = alice.post("/api/cron", JOB, **{"X-Noust-Approval": approval_id})
        assert ran.status_code == 201, ran.text
        assert len(cron.created) == 1

        again = alice.post("/api/cron", JOB, **{"X-Noust-Approval": approval_id})
        assert again.status_code == 409
        assert again.json()["error"] == "approval_used"
        assert len(cron.created) == 1

        use = [entry for entry in audit_actions() if entry["action"] == "approval.use"]
        assert use and use[-1]["who"]["name"] == "alice"
        assert use[-1]["details"]["approved_by"]["name"] == "sam"

    def test_another_call_under_an_approval_is_refused(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        sam.elevate()
        approval_id = alice.post("/api/cron", JOB).headers["X-Noust-Approval-Request"]
        sam.post(f"/api/approvals/{approval_id}/approve", {})

        swapped = alice.post(
            "/api/cron", {**JOB, "command": "/bin/sh -c evil"}, **{"X-Noust-Approval": approval_id}
        )
        assert swapped.status_code == 409
        assert swapped.json()["error"] == "approval_mismatch"
        assert cron.created == []

    def test_the_requester_cannot_approve_and_an_admin_only_when_allowed(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        alice = Person(app, "alice", "admin")
        bob = Person(app, "bob", "admin")
        alice.elevate()
        bob.elevate()
        approval_id = alice.post("/api/cron", JOB).headers["X-Noust-Approval-Request"]

        own = alice.post(f"/api/approvals/{approval_id}/approve", {})
        assert own.status_code == 403
        assert own.json()["error"] == "approval_denied"
        # Not a decider, and not his request: he does not even see it.
        assert bob.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 404

        policy["approvers"] = ("security", "admin")
        assert bob.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 200

    def test_deciding_needs_sudo_mode(self, app: Any, policy: dict[str, Any]) -> None:
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        approval_id = alice.post("/api/cron", JOB).headers["X-Noust-Approval-Request"]
        refused = sam.post(f"/api/approvals/{approval_id}/approve", {})
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"

    def test_sudo_mode_comes_before_the_request(self, app: Any, policy: dict[str, Any]) -> None:
        alice = Person(app, "alice", "admin")
        response = alice.post("/api/cron", JOB)
        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert alice.client.get("/api/approvals").json()["approvals"] == []

    def test_a_rejection_is_the_end_of_it(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        sam.elevate()
        approval_id = alice.post("/api/cron", JOB).headers["X-Noust-Approval-Request"]
        rejected = sam.post(f"/api/approvals/{approval_id}/reject", {"comment": "Use a timer"})
        assert rejected.json()["state"] == "rejected"

        refused = alice.post("/api/cron", JOB, **{"X-Noust-Approval": approval_id})
        assert refused.status_code == 409
        assert refused.json()["error"] == "approval_rejected"
        assert cron.created == []

    def test_the_ens_profile_asks_for_a_reason(self, app: Any, policy: dict[str, Any]) -> None:
        policy["reason_required"] = True
        alice = Person(app, "alice", "admin")
        alice.elevate()
        refused = alice.post("/api/cron", JOB)
        assert refused.status_code == 400
        assert refused.json()["error"] == "approval_reason_required"
        assert alice.post("/api/cron", JOB, **{"X-Noust-Reason": "why"}).status_code == 202

    def test_others_see_only_their_own_requests(self, app: Any, policy: dict[str, Any]) -> None:
        alice = Person(app, "alice", "admin")
        viewer = Person(app, "vera", "viewer")
        alice.elevate()
        approval_id = alice.post("/api/cron", JOB).headers["X-Noust-Approval-Request"]
        assert viewer.client.get("/api/approvals").json()["approvals"] == []
        assert viewer.client.get(f"/api/approvals/{approval_id}").status_code == 404

    def test_the_master_token_asks_like_anyone(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        token = get_token_manager().generate_master_token()
        bearer = {"Authorization": f"Bearer {token}"}
        client = TestClient(app, client=("testclient", 50000))
        asked = client.post("/api/cron", json=JOB, headers=bearer)
        assert asked.status_code == 202, asked.text
        approval_id = asked.headers["X-Noust-Approval-Request"]

        master_decides = client.post(
            f"/api/approvals/{approval_id}/approve", json={}, headers=bearer
        )
        assert master_decides.status_code == 403

        sam = Person(app, "sam", "security")
        sam.elevate()
        assert sam.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 200
        ran = client.post(
            "/api/cron", json=JOB, headers={**bearer, "X-Noust-Approval": approval_id}
        )
        assert ran.status_code == 201, ran.text

    def test_the_policy_says_what_needs_approval(self, app: Any, policy: dict[str, Any]) -> None:
        viewer = Person(app, "vera", "viewer")
        body = viewer.client.get("/api/approvals/policy").json()
        assert body["enabled"] is True
        assert body["approvers"] == ["security"]
        actions = {rule["action"] for rule in body["rules"]}
        assert {"root_equivalent", "fleet.node.add", "user.role_change"} <= actions


class TestRoleChanges:
    def test_a_role_change_needs_another_security_officer(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()

        asked = sam.patch("/api/auth/accounts/maria", {"role": "admin"})
        assert asked.status_code == 202, asked.text
        approval_id = asked.headers["X-Noust-Approval-Request"]
        assert AccountManager().require("maria").role == "operator"

        tess.elevate()
        assert tess.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 200
        done = sam.patch(
            "/api/auth/accounts/maria", {"role": "admin"}, **{"X-Noust-Approval": approval_id}
        )
        assert done.status_code == 200, done.text
        assert AccountManager().require("maria").role == "admin"

    def test_other_account_edits_need_nothing(self, app: Any, policy: dict[str, Any]) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        sam.elevate()
        response = sam.patch("/api/auth/accounts/maria", {"display_name": "María"})
        assert response.status_code == 200, response.text


class TestFleet:
    def test_a_central_s_call_needs_the_central_s_word(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        token = str(get_token_manager().create_fleet_token("fleet-nas")["token"])
        client = TestClient(app, client=("127.0.0.1", 50000))
        headers = {
            "Authorization": f"Bearer {token}",
            FLEET_ACTOR_HEADER: "maria",
            FLEET_ACTOR_ROLE_HEADER: "admin",
            FLEET_ELEVATED_HEADER: "1",
        }
        refused = client.post("/api/cron", json=JOB, headers=headers)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"] == "approval_required"
        assert cron.created == []

        vouched = client.post(
            "/api/cron",
            json=JOB,
            headers={**headers, "X-Noust-Approved-By": "sam", "X-Noust-Approval": "7"},
        )
        assert vouched.status_code == 201, vouched.text
        use = [entry for entry in audit_actions() if entry["action"] == "approval.use"][-1]
        assert use["details"]["approved_by"]["name"] == "sam"


class TestCost:
    def test_a_call_no_rule_covers_is_left_alone_before_its_body_is_read(
        self, app: Any, policy: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.api import approvals as guard

        consulted: list[str] = []
        real = guard.approvals

        def counting() -> Any:
            consulted.append("manager")
            return real()

        monkeypatch.setattr(guard, "approvals", counting)
        alice = Person(app, "alice", "admin")
        alice.post("/api/auth/logout")
        assert consulted == []


class TestTheSwitchIsASecuritySetting:
    def test_an_admin_cannot_turn_approvals_off(self, app: Any, policy: dict[str, Any]) -> None:
        alice = Person(app, "alice", "admin")
        alice.elevate()
        response = alice.patch("/api/config", {"path": "approval.enabled", "value": False})
        assert response.status_code == 403, response.text
        assert response.json()["error"] == "permission_denied"

    def test_a_central_cannot_either(self, app: Any, policy: dict[str, Any]) -> None:
        from noust.web.auth import FLEET_PROTECTED_CONFIG_SECTIONS
        from noust.web.permissions.enforce import SECURITY_CONFIG_SECTIONS

        assert "approval" in SECURITY_CONFIG_SECTIONS
        assert "approval" in FLEET_PROTECTED_CONFIG_SECTIONS
