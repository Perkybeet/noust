# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

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


@pytest.fixture(autouse=True)
def split_roles(real_config: Any, app: Any, store: NoustStore) -> None:
    """
    Four-eyes approvals are what a server with the roles split uses (ENS op.acc.3).

    The administrator is saved without the permissions of governing, as the ENS review decided it;
    a framework no longer takes them from it behind the operator's back.
    """
    from noust.core.access.role_manager import RoleManager
    from noust.web.permissions.roles import ADMIN

    real_config.set("security.roles_split", True)
    RoleManager().update("admin", permissions=sorted(ADMIN))


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

    def put(self, path: str, body: Any, **headers: str) -> Any:
        return self.client.put(path, json=body, headers=self.headers(**headers))

    def delete(self, path: str, **headers: str) -> Any:
        return self.client.delete(path, headers=self.headers(**headers))


def allow_root_equivalent_from_centrals() -> None:
    """A cron command runs as root: a central reaches it only with host access."""
    from noust.fleet.policy import FleetAccess, set_access

    set_access(FleetAccess("admin", host_access=True))


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


def role_changes() -> list[dict[str, Any]]:
    return [entry for entry in audit_actions() if entry["action"] == "user.role_change"]


class TestSeveralRolesWaitForASecondPerson:
    """
    The roles of an account are one thing however they arrive.

    ``PATCH`` with ``role`` always waited; ``PUT .../roles`` and ``PATCH`` with ``roles`` did not,
    so whoever manages accounts could hand out any role, ``owner`` included, alone.
    """

    ROLES = "/api/auth/accounts/maria/roles"
    ACCOUNT = "/api/auth/accounts/maria"

    @staticmethod
    def maria_holds() -> tuple[str, ...]:
        return AccountManager().require("maria").roles

    def test_setting_the_roles_waits_for_another_person_and_runs_once(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        wanted = {"roles": ["operator", "admin"]}

        asked = sam.put(self.ROLES, wanted)
        assert asked.status_code == 202, asked.text
        assert asked.json()["error"] == "approval_required"
        approval_id = asked.headers["X-Noust-Approval-Request"]
        assert self.maria_holds() == ("operator",)
        assert role_changes() == []
        request = sam.client.get(f"/api/approvals/{approval_id}").json()
        assert request["action"] == "user.role_change" and request["kind"] == "role_change"
        assert request["method"] == "PUT" and request["path"] == self.ROLES

        tess.elevate()
        assert tess.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 200
        done = sam.put(self.ROLES, wanted, **{"X-Noust-Approval": approval_id})
        assert done.status_code == 200, done.text
        assert self.maria_holds() == ("operator", "admin")
        assert len(role_changes()) == 1
        assert "operator -> operator, admin" in role_changes()[0]["detail"]

        # Sent again with the roles already in place it changes nothing, so it records nothing...
        again = sam.put(self.ROLES, wanted, **{"X-Noust-Approval": approval_id})
        assert again.status_code == 200, again.text
        assert len(role_changes()) == 1
        # ...but the approval does not run the change a second time once the roles are other again.
        get_token_manager().accounts.set_roles("maria", ["operator"])
        replay = sam.put(self.ROLES, wanted, **{"X-Noust-Approval": approval_id})
        assert replay.status_code == 409 and replay.json()["error"] == "approval_used"
        assert self.maria_holds() == ("operator",)
        assert len(role_changes()) == 1
        use = [entry for entry in audit_actions() if entry["action"] == "approval.use"]
        assert len(use) == 1 and use[0]["details"]["approved_by"]["name"] == "tess"

    def test_the_approval_is_for_those_roles_and_no_others(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        tess.elevate()
        approval_id = sam.put(self.ROLES, {"roles": ["operator", "viewer"]}).headers[
            "X-Noust-Approval-Request"
        ]
        tess.post(f"/api/approvals/{approval_id}/approve", {})

        swapped = sam.put(
            self.ROLES, {"roles": ["operator", "admin"]}, **{"X-Noust-Approval": approval_id}
        )
        assert swapped.status_code == 409 and swapped.json()["error"] == "approval_mismatch"
        assert self.maria_holds() == ("operator",)

    @pytest.mark.parametrize(
        "body",
        [
            {"roles": ["operator", "admin"]},
            # The two fields at once: whichever the handler applies, the one that changes
            # something is enough, and neither can be used to slip the other through.
            {"role": "operator", "roles": ["operator", "admin"]},
            {"role": "admin", "roles": ["operator", "admin"]},
            {"role": "admin", "roles": ["operator"]},
            {"role": "admin"},
            {"display_name": "Maria", "roles": ["operator", "admin"]},
        ],
    )
    def test_the_account_patch_waits_for_roles_as_it_does_for_a_role(
        self, app: Any, policy: dict[str, Any], body: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        sam.elevate()
        asked = sam.patch(self.ACCOUNT, body)
        assert asked.status_code == 202, asked.text
        assert self.maria_holds() == ("operator",)
        assert AccountManager().require("maria").display_name != "Maria"
        assert role_changes() == []

    def test_a_patch_with_roles_runs_once_approved(self, app: Any, policy: dict[str, Any]) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        body = {"roles": ["operator", "admin"]}
        approval_id = sam.patch(self.ACCOUNT, body).headers["X-Noust-Approval-Request"]
        tess.elevate()
        tess.post(f"/api/approvals/{approval_id}/approve", {})
        done = sam.patch(self.ACCOUNT, body, **{"X-Noust-Approval": approval_id})
        assert done.status_code == 200, done.text
        assert self.maria_holds() == ("operator", "admin")
        assert len(role_changes()) == 1

    @pytest.mark.parametrize(
        "roles",
        [["operator"], [" Operator "], ["operator", "operator"]],
    )
    def test_roles_that_stay_as_they_are_ask_nothing(
        self, app: Any, policy: dict[str, Any], roles: list[str]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        sam.elevate()
        for call in (
            sam.put(self.ROLES, {"roles": roles}),
            sam.patch(self.ACCOUNT, {"roles": roles}),
            sam.patch(self.ACCOUNT, {"role": "operator"}),
        ):
            assert call.status_code == 200, call.text
        assert sam.client.get("/api/approvals").json()["approvals"] == []
        assert self.maria_holds() == ("operator",)

    def test_putting_back_the_roles_an_account_holds_asks_nothing_either(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        get_token_manager().accounts.set_roles("maria", ["operator", "admin"])
        sam = Person(app, "sam", "security")
        sam.elevate()
        same = sam.put(self.ROLES, {"roles": ["operator", "admin"]})
        assert same.status_code == 200, same.text
        assert sam.client.get("/api/approvals").json()["approvals"] == []

    def test_taking_a_role_away_or_reordering_them_is_a_change_too(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        # The role an account is known by decides who it is to an approval (a security officer
        # decides, an operator does not), so a reorder or a removal is not a harmless edit.
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        get_token_manager().accounts.set_roles("maria", ["operator", "admin"])
        sam = Person(app, "sam", "security")
        sam.elevate()
        for roles in (["admin", "operator"], ["operator"]):
            asked = sam.put(self.ROLES, {"roles": roles})
            assert asked.status_code == 202, (roles, asked.text)
        assert self.maria_holds() == ("operator", "admin")

    def test_a_request_that_the_handler_would_refuse_is_not_filed(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        sam.elevate()
        unknown = sam.put("/api/auth/accounts/nobody/roles", {"roles": ["admin"]})
        assert unknown.status_code == 404, unknown.text
        no_roles = sam.put(self.ROLES, {"roles": []})
        assert no_roles.status_code == 422, no_roles.text
        assert sam.client.get("/api/approvals").json()["approvals"] == []

    def test_an_admin_never_approves_it(self, app: Any, policy: dict[str, Any]) -> None:
        policy["approvers"] = ("security", "admin")
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        alice = Person(app, "alice", "admin")
        sam.elevate()
        alice.elevate()
        approval_id = sam.put(self.ROLES, {"roles": ["admin"]}).headers["X-Noust-Approval-Request"]
        refused = alice.post(f"/api/approvals/{approval_id}/approve", {})
        assert refused.status_code in (403, 404), refused.text
        assert self.maria_holds() == ("operator",)

    def test_the_owner_role_is_handed_over_through_a_second_person_and_by_an_owner(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        olga = Person(app, "olga", "owner")
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        olga.elevate()
        sam.elevate()
        tess.elevate()
        wanted = {"roles": ["operator", "owner"]}

        # An owner asks, a security officer decides: nobody holds the owner role alone.
        asked = olga.put(self.ROLES, wanted)
        assert asked.status_code == 202, asked.text
        approval_id = asked.headers["X-Noust-Approval-Request"]
        assert self.maria_holds() == ("operator",)
        assert sam.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 200
        done = olga.put(self.ROLES, wanted, **{"X-Noust-Approval": approval_id})
        assert done.status_code == 200, done.text
        assert self.maria_holds() == ("operator", "owner")

        # A second person's word does not replace the owner rule: a security officer who is not an
        # owner still cannot hand the role over, approved or not.
        get_token_manager().accounts.create("nora", "operator", password=PASSWORD)
        nora = {"roles": ["operator", "owner"]}
        ask = sam.put("/api/auth/accounts/nora/roles", nora)
        assert ask.status_code == 202, ask.text
        second = ask.headers["X-Noust-Approval-Request"]
        assert tess.post(f"/api/approvals/{second}/approve", {}).status_code == 200
        refused = sam.put("/api/auth/accounts/nora/roles", nora, **{"X-Noust-Approval": second})
        assert refused.status_code == 400, refused.text
        assert AccountManager().require("nora").roles == ("operator",)

    def test_nothing_changes_while_approvals_are_off(self, app: Any) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        sam.elevate()
        done = sam.put(self.ROLES, {"roles": ["operator", "admin"]})
        assert done.status_code == 200, done.text
        assert self.maria_holds() == ("operator", "admin")

    def test_the_policy_names_the_routes_and_what_they_wait_for(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        viewer = Person(app, "vera", "viewer")
        rules = viewer.client.get("/api/approvals/policy").json()["rules"]
        by_route = {(rule["method"], rule["template"]): rule for rule in rules}
        for route in (
            ("PUT", "/api/auth/accounts/{username}/roles"),
            ("PATCH", "/api/auth/accounts/{username}"),
        ):
            assert by_route[route]["action"] == "user.role_change"
            assert by_route[route]["kind"] == "role_change"
            assert "roles" in by_route[route]["when"]


def audit_count(action: str) -> int:
    return len([entry for entry in audit_actions() if entry["action"] == action])


def approve_and_resend(
    asker: Person, decider: Person, send: Any, asked: Any, *args: Any, **kwargs: Any
) -> Any:
    """Have a second person approve what was asked, and send the call again under it."""
    assert asked.status_code == 202, asked.text
    approval_id = asked.headers["X-Noust-Approval-Request"]
    decider.elevate()
    assert decider.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 200
    return send(*args, **kwargs, **{"X-Noust-Approval": approval_id})


class TestTeamGrantsWaitForASecondPerson:
    """
    A role on a team has two doors, the access bindings and the team's own members and access.

    ``POST /api/access/bindings`` waited; the team's routes, which make the same grants through the
    same store calls, did not.
    """

    @pytest.fixture(autouse=True)
    def acme(self, app: Any) -> None:
        from noust.core.teams import TeamStore

        TeamStore().create_team("Acme", slug="acme")
        get_token_manager().accounts.create(
            "maria", "operator", password=PASSWORD, join_default_team=False
        )

    @staticmethod
    def role_of_maria() -> str | None:
        from noust.core.teams import TeamStore

        members = {member.username: member.role for member in TeamStore().list_members("acme")}
        return members.get("maria")

    def test_adding_a_member_waits_for_another_person_and_runs_once(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        body = {"username": "maria", "role": "operator"}

        asked = alice.post("/api/teams/acme/members", body)
        assert asked.status_code == 202, asked.text
        assert self.role_of_maria() is None
        request = alice.client.get(f"/api/approvals/{asked.headers['X-Noust-Approval-Request']}")
        assert request.json()["action"] == "team.member"
        assert request.json()["kind"] == "role_change"

        done = approve_and_resend(alice, sam, alice.post, asked, "/api/teams/acme/members", body)
        assert done.status_code == 201, done.text
        assert self.role_of_maria() == "operator"
        assert audit_count("team.member.add") == 1

    def test_the_approval_is_for_that_role_and_no_other(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        asked = alice.post("/api/teams/acme/members", {"username": "maria", "role": "viewer"})
        swapped = approve_and_resend(
            alice,
            sam,
            alice.post,
            asked,
            "/api/teams/acme/members",
            {"username": "maria", "role": "owner"},
        )
        assert swapped.status_code == 409 and swapped.json()["error"] == "approval_mismatch"
        assert self.role_of_maria() is None

    def test_raising_a_member_waits_and_lowering_one_does_not(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        from noust.core.teams import TeamStore

        TeamStore().add_member("acme", "maria", "operator")
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        member = "/api/teams/acme/members/maria"

        raised = alice.patch(member, {"role": "admin"})
        assert raised.status_code == 202, raised.text
        upsert = alice.post("/api/teams/acme/members", {"username": "maria", "role": "owner"})
        assert upsert.status_code == 202, upsert.text
        assert self.role_of_maria() == "operator"

        done = approve_and_resend(alice, sam, alice.patch, raised, member, {"role": "admin"})
        assert done.status_code == 200, done.text
        assert self.role_of_maria() == "admin"

        # Taking power away, or saying what is already so, asks nobody.
        assert alice.patch(member, {"role": "viewer"}).status_code == 200
        assert alice.patch(member, {"role": "viewer"}).status_code == 200
        assert (
            alice.post(
                "/api/teams/acme/members", {"username": "maria", "role": "viewer"}
            ).status_code
            == 201
        )
        assert self.role_of_maria() == "viewer"

    def test_taking_a_member_out_asks_nobody(self, app: Any, policy: dict[str, Any]) -> None:
        from noust.core.teams import TeamStore

        TeamStore().add_member("acme", "maria", "admin")
        alice = Person(app, "alice", "admin")
        alice.elevate()
        assert alice.delete("/api/teams/acme/members/maria").status_code == 200
        assert self.role_of_maria() is None

    def test_what_the_handler_would_refuse_is_not_filed(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        alice = Person(app, "alice", "admin")
        alice.elevate()
        for path, body, status in (
            ("/api/teams/nowhere/members", {"username": "maria", "role": "viewer"}, 404),
            ("/api/teams/acme/members", {"username": "nobody", "role": "viewer"}, 404),
            ("/api/teams/acme/members", {"username": "maria", "role": "emperor"}, 400),
        ):
            refused = alice.post(path, body)
            assert refused.status_code == status, (path, refused.text)
        not_a_member = alice.patch("/api/teams/acme/members/maria", {"role": "owner"})
        assert not_a_member.status_code == 404, not_a_member.text
        assert alice.client.get("/api/approvals").json()["approvals"] == []

    def test_a_role_on_a_team_or_below_it_waits_like_the_access_route_does(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        from noust.web.permissions.access import AccessManager

        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        for body in (
            {"username": "maria", "role": "admin"},
            {"username": "maria", "role": "viewer", "project": "default"},
        ):
            asked = alice.post("/api/teams/acme/access", body)
            assert asked.status_code == 202, (body, asked.text)
        assert AccessManager().list_bindings("maria") == []
        assert self.role_of_maria() is None

        body = {"username": "maria", "role": "viewer", "project": "default"}
        asked = alice.post("/api/teams/acme/access", body)
        done = approve_and_resend(alice, sam, alice.post, asked, "/api/teams/acme/access", body)
        assert done.status_code == 201, done.text
        assert [binding.role for binding in AccessManager().list_bindings("maria")] == ["viewer"]
        assert audit_count("access.bind") == 1

    def test_taking_a_role_away_asks_nobody(self, app: Any, policy: dict[str, Any]) -> None:
        from noust.web.permissions.access import AccessManager

        binding = AccessManager().bind("maria", "viewer", team="acme", project="default")
        alice = Person(app, "alice", "admin")
        sam = Person(app, "sam", "security")
        alice.elevate()
        sam.elevate()
        assert alice.delete(f"/api/teams/acme/access/{binding.id}").status_code == 200
        again = AccessManager().bind("maria", "viewer", team="acme", project="default")
        assert sam.delete(f"/api/access/bindings/{again.id}").status_code == 200
        assert sam.client.get("/api/approvals").json()["approvals"] == []

    def test_the_policy_names_every_door(self, app: Any, policy: dict[str, Any]) -> None:
        viewer = Person(app, "vera", "viewer")
        rules = viewer.client.get("/api/approvals/policy").json()["rules"]
        by_route = {(rule["method"], rule["template"]): rule for rule in rules}
        assert by_route[("POST", "/api/teams/{team}/access")]["action"] == "access.grant"
        assert by_route[("POST", "/api/teams/{team}/members")]["action"] == "team.member"
        assert by_route[("PATCH", "/api/teams/{team}/members/{username}")]["kind"] == "role_change"


class TestWhatARoleHoldsWaitsForASecondPersonWhenItGrowsAndNotWhenItShrinks:
    """
    A role is held by everyone who has it, from their next request: adding a permission to it is
    granting that permission to each of them.
    """

    HOLDS = ["apps.read", "apps.operate"]
    MORE = ["apps.read", "apps.operate", "apps.deploy"]

    @pytest.fixture(autouse=True)
    def release_manager(self, app: Any) -> None:
        from noust.core.access.role_manager import RoleManager

        RoleManager().create("release-manager", self.HOLDS)
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        get_token_manager().accounts.set_roles("maria", ["operator", "release-manager"])

    @staticmethod
    def held(name: str = "release-manager") -> set[str]:
        from noust.core.access.roles import RoleStore

        return set(RoleStore().require(name).held) - {"self"}

    @pytest.mark.parametrize(
        "path", ["/api/roles/release-manager", "/api/access/roles/release-manager"]
    )
    def test_adding_a_permission_waits_for_another_person_and_runs_once(
        self, app: Any, policy: dict[str, Any], path: str
    ) -> None:
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        body = {"permissions": self.MORE}

        asked = sam.patch(path, body)
        assert asked.status_code == 202, asked.text
        assert self.held() == set(self.HOLDS)
        request = sam.client.get(f"/api/approvals/{asked.headers['X-Noust-Approval-Request']}")
        assert request.json()["action"] == "access.role_widen"
        assert request.json()["kind"] == "role_change"

        done = approve_and_resend(sam, tess, sam.patch, asked, path, body)
        assert done.status_code == 200, done.text
        assert self.held() == set(self.MORE)
        assert audit_count("access.role.update") == 1

    def test_the_approval_is_for_those_permissions_and_no_others(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        path = "/api/roles/release-manager"
        asked = sam.patch(path, {"permissions": self.MORE})
        swapped = approve_and_resend(
            sam, tess, sam.patch, asked, path, {"permissions": [*self.MORE, "apps.manage"]}
        )
        assert swapped.status_code == 409 and swapped.json()["error"] == "approval_mismatch"
        assert self.held() == set(self.HOLDS)

    def test_a_role_nobody_holds_yet_cannot_be_widened_after_the_grant_was_approved(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        from noust.core.access.role_manager import RoleManager

        RoleManager().create("unused", ["apps.read"])
        sam = Person(app, "sam", "security")
        sam.elevate()
        asked = sam.patch("/api/roles/unused", {"permissions": ["apps.read", "apps.manage"]})
        assert asked.status_code == 202, asked.text
        assert self.held("unused") == {"apps.read"}

    @pytest.mark.parametrize(
        "body",
        [
            {"permissions": ["apps.read"]},
            {"permissions": ["apps.read", "apps.operate"]},
            {"permissions": ["apps.operate", "apps.read", "self"]},
            {"label": "Release manager", "description": "Ships what is ready."},
        ],
    )
    def test_a_role_that_holds_the_same_or_less_asks_nothing(
        self, app: Any, policy: dict[str, Any], body: dict[str, Any]
    ) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        for path in ("/api/roles/release-manager", "/api/access/roles/release-manager"):
            changed = sam.patch(path, body)
            assert changed.status_code == 200, (path, changed.text)
        assert sam.client.get("/api/approvals").json()["approvals"] == []

    def test_a_role_noust_ships_is_widened_through_a_second_person_too(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        from noust.core.access.builtin import OPERATOR

        sam = Person(app, "sam", "security")
        sam.elevate()
        body = {"permissions": sorted(OPERATOR | {"apps.manage"})}
        asked = sam.patch("/api/roles/operator", body)
        assert asked.status_code == 202, asked.text
        assert "apps.manage" not in self.held("operator")

    def test_what_the_handler_would_refuse_is_not_filed(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        for path, body, status in (
            ("/api/roles/release-manager", {"permissions": [*self.HOLDS, "root.everything"]}, 400),
            ("/api/roles/owner", {"permissions": ["apps.read"]}, 400),
            ("/api/roles/nowhere", {"permissions": ["apps.read", "apps.manage"]}, 404),
        ):
            refused = sam.patch(path, body)
            assert refused.status_code == status, (path, refused.text)
        assert sam.client.get("/api/approvals").json()["approvals"] == []

    def test_putting_a_role_back_waits_when_it_gives_permissions_back(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        from noust.core.access.builtin import OPERATOR
        from noust.core.access.role_manager import RoleManager

        RoleManager().update("operator", permissions=["apps.read", "apps.operate"])
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        asked = sam.post("/api/roles/operator/reset")
        assert asked.status_code == 202, asked.text
        assert self.held("operator") == {"apps.read", "apps.operate"}

        done = approve_and_resend(sam, tess, sam.post, asked, "/api/roles/operator/reset")
        assert done.status_code == 200, done.text
        assert self.held("operator") == set(OPERATOR) - {"self"}
        assert audit_count("access.role.reset") == 1

    def test_putting_a_role_back_asks_nothing_when_it_only_takes_permissions_away(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        from noust.core.access.builtin import OPERATOR
        from noust.core.access.role_manager import RoleManager

        RoleManager().update("operator", permissions=sorted(OPERATOR | {"backups.manage"}))
        sam = Person(app, "sam", "security")
        sam.elevate()
        assert sam.post("/api/roles/operator/reset").status_code == 200
        assert sam.post("/api/roles/viewer/reset").status_code == 200
        assert sam.client.get("/api/approvals").json()["approvals"] == []

    def test_taking_what_a_newer_release_offers_waits_and_leaving_it_does_not(
        self, app: Any, policy: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.access import builtin
        from noust.core.access.role_manager import RoleManager

        RoleManager().update("operator", permissions=["apps.read", "apps.operate"])
        shipped = builtin.SHIPPED["operator"]
        monkeypatch.setitem(
            builtin.SHIPPED,
            "operator",
            builtin.Shipped(
                "operator",
                shipped.label,
                shipped.description,
                shipped.permissions | {"backups.manage", "databases.manage"},
            ),
        )
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        path = "/api/roles/operator/offers"

        asked = sam.post(path, {"accept": ["backups.manage"]})
        assert asked.status_code == 202, asked.text
        assert "backups.manage" not in self.held("operator")
        not_offered = sam.post(path, {"accept": ["apps.manage"]})
        assert not_offered.status_code == 400, not_offered.text

        done = approve_and_resend(sam, tess, sam.post, asked, path, {"accept": ["backups.manage"]})
        assert done.status_code == 200, done.text
        assert "backups.manage" in self.held("operator")
        left = sam.post(path, {"dismiss": ["databases.manage"]})
        assert left.status_code == 200, left.text
        assert "databases.manage" not in self.held("operator")

    def test_making_copying_and_deleting_a_role_nobody_holds_ask_nobody(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        # Nothing changes for anybody until somebody is given the role, which is where the second
        # person is asked; so the owner's permissions copied into a new role are inert too.
        sam = Person(app, "sam", "security")
        sam.elevate()
        assert (
            sam.post("/api/roles", {"name": "fresh", "permissions": ["apps.manage"]}).status_code
            == 201
        )
        assert (
            sam.post(
                "/api/access/roles", {"name": "second", "permissions": ["apps.read"]}
            ).status_code
            == 201
        )
        assert sam.post("/api/roles/owner/duplicate", {"name": "everything"}).status_code == 201
        assert sam.delete("/api/roles/fresh").status_code == 200
        assert sam.delete("/api/access/roles/second").status_code == 200
        assert sam.client.get("/api/approvals").json()["approvals"] == []

        # ...and giving that role to somebody is what is asked.
        give = sam.put("/api/auth/accounts/maria/roles", {"roles": ["operator", "everything"]})
        assert give.status_code == 202, give.text

    def test_the_policy_names_every_route(self, app: Any, policy: dict[str, Any]) -> None:
        viewer = Person(app, "vera", "viewer")
        rules = viewer.client.get("/api/approvals/policy").json()["rules"]
        by_route = {(rule["method"], rule["template"]): rule for rule in rules}
        for route in (
            ("PATCH", "/api/roles/{name}"),
            ("PATCH", "/api/access/roles/{role}"),
            ("POST", "/api/roles/{name}/reset"),
            ("POST", "/api/roles/{name}/offers"),
        ):
            assert by_route[route]["action"] == "access.role_widen", route
            assert "permission" in by_route[route]["when"]


def seed_source(protocol: str, **settings: Any) -> None:
    """A sign-in source of each kind, as it would be after an earlier set-up."""
    if protocol == "oidc":
        from noust.core.accounts.oidc_store import OidcProvider, OidcStore

        OidcStore().save_provider(
            OidcProvider("corp", "Corp", "https://idp.example.com", "noust", **settings)
        )
    elif protocol == "saml":
        from noust.core.accounts.saml_store import SamlProvider, SamlStore
        from tests.support.saml_support import make_identity

        _key, pem = make_identity()
        SamlStore().save_provider(
            SamlProvider(
                "corp",
                "Corp",
                "https://idp.example.com",
                "https://idp.example.com/sso",
                certificates=(pem,),
                **settings,
            )
        )
    elif protocol == "ldap":
        from noust.core.accounts.directory_store import DirectoryStore, LdapDirectory

        DirectoryStore().save_directory(
            LdapDirectory(
                "corp",
                "Corp",
                "ldap.example.com",
                "ou=people,dc=example,dc=com",
                "(uid={username})",
                **settings,
            )
        )
    else:
        from noust.core.accounts.scim_store import ScimSource, ScimStore

        ScimStore().save_source(ScimSource("corp", "Corp", "0" * 64, "abcd", 0.0, **settings))


SOURCE_PATHS = {
    "oidc": "/api/auth/oidc/config/corp",
    "saml": "/api/auth/saml/config/corp",
    "ldap": "/api/auth/ldap/directories/corp",
    "scim": "/api/auth/scim/sources/corp",
}


def stored_source(protocol: str) -> Any:
    if protocol == "oidc":
        from noust.core.accounts.oidc_store import OidcStore

        return OidcStore().get_provider("corp")
    if protocol == "saml":
        from noust.core.accounts.saml_store import SamlStore

        return SamlStore().get_provider("corp")
    if protocol == "ldap":
        from noust.core.accounts.directory_store import DirectoryStore

        return DirectoryStore().get_directory("corp")
    from noust.core.accounts.scim_store import ScimStore

    return ScimStore().get_source("corp")


@pytest.mark.parametrize("protocol", ["oidc", "saml", "ldap", "scim"])
class TestWhatASourceHandsOutWaitsForASecondPerson:
    """
    An identity provider decides which roles the people it sends get: the role grant of creating
    an account, made once for everyone it will ever send.
    """

    def test_a_default_role_waits_for_another_person_and_runs_once(
        self, app: Any, policy: dict[str, Any], protocol: str
    ) -> None:
        seed_source(protocol)
        path = SOURCE_PATHS[protocol]
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        body = {"default_role": "operator"}

        asked = sam.put(path, body)
        assert asked.status_code == 202, asked.text
        assert stored_source(protocol).default_role == "viewer"
        request = sam.client.get(f"/api/approvals/{asked.headers['X-Noust-Approval-Request']}")
        assert request.json()["action"] == "auth.source_roles"
        assert request.json()["kind"] == "role_change"

        done = approve_and_resend(sam, tess, sam.put, asked, path, body)
        assert done.status_code == 200, done.text
        assert stored_source(protocol).default_role == "operator"

    @pytest.mark.parametrize(
        "body",
        [
            {"group_sync": True},
            {"group_mappings": [{"group": "ops", "role": "admin"}]},
            {"link_by_email": True},
            {"default_team": "acme"},
            {"label": "Corp", "default_role": "admin"},
        ],
    )
    def test_each_thing_it_decides_about_roles_waits(
        self, app: Any, policy: dict[str, Any], protocol: str, body: dict[str, Any]
    ) -> None:
        from noust.core.teams import TeamStore

        TeamStore().create_team("Acme", slug="acme")
        seed_source(protocol)
        sam = Person(app, "sam", "security")
        sam.elevate()
        asked = sam.put(SOURCE_PATHS[protocol], body)
        assert asked.status_code == 202, (body, asked.text)
        assert stored_source(protocol).label == "Corp"

    def test_saying_again_what_it_already_does_asks_nothing(
        self, app: Any, policy: dict[str, Any], protocol: str
    ) -> None:
        seed_source(protocol, default_role="operator")
        sam = Person(app, "sam", "security")
        sam.elevate()
        for body in (
            {"default_role": "operator"},
            {"default_role": " Operator ", "group_mappings": [], "link_by_email": False},
            {"label": "Corporate", "enabled": True},
            {},
        ):
            sent = sam.put(SOURCE_PATHS[protocol], body)
            assert sent.status_code == 200, (body, sent.text)
        assert sam.client.get("/api/approvals").json()["approvals"] == []
        assert stored_source(protocol).default_role == "operator"

    def test_a_source_that_does_not_exist_yet_waits(
        self, app: Any, policy: dict[str, Any], protocol: str
    ) -> None:
        # Its defaults are roles too: a SCIM source creates the accounts it is told about.
        sam = Person(app, "sam", "security")
        sam.elevate()
        asked = sam.put(SOURCE_PATHS[protocol], {"label": "New"})
        assert asked.status_code == 202, asked.text
        assert stored_source(protocol) is None

    def test_a_value_the_handler_refuses_is_not_filed(
        self, app: Any, policy: dict[str, Any], protocol: str
    ) -> None:
        seed_source(protocol)
        sam = Person(app, "sam", "security")
        sam.elevate()
        refused = sam.put(SOURCE_PATHS[protocol], {"default_role": "emperor"})
        assert refused.status_code == 400, refused.text
        owner = sam.put(
            SOURCE_PATHS[protocol], {"group_mappings": [{"group": "ops", "role": "owner"}]}
        )
        assert owner.status_code == 400, owner.text
        assert sam.client.get("/api/approvals").json()["approvals"] == []

    def test_taking_the_source_away_or_off_asks_nobody(
        self, app: Any, policy: dict[str, Any], protocol: str
    ) -> None:
        seed_source(protocol, default_role="operator")
        sam = Person(app, "sam", "security")
        sam.elevate()
        assert sam.put(SOURCE_PATHS[protocol], {"enabled": False}).status_code == 200
        assert sam.delete(SOURCE_PATHS[protocol]).status_code == 200
        assert stored_source(protocol) is None


class TestTheAnswerSaysWhichControlAsks:
    """
    The owner, holding every permission, was asked for an approval and for a reason and read it as a
    problem with their permissions. Both come from controls that apply to everybody, and the answer
    says which control, who turned it on, and the command that turns it off, as sentences with a
    code (``coded`` and ``notes_coded``) that the console translates.
    """

    def test_an_approval_names_the_control_the_operator_switched_on(
        self, app: Any, policy: dict[str, Any], real_config: Any
    ) -> None:
        real_config.set("approval.enabled", True)
        alice = Person(app, "alice", "admin")
        alice.elevate()

        asked = alice.post("/api/cron", JOB, **{"X-Noust-Reason": "ticket 42"})

        assert asked.status_code == 202, asked.text
        body = asked.json()
        assert "whoever you are" in body["detail"]
        assert "noust compliance set approvals off" in body["detail"]
        assert "Settings > Controls" in body["detail"]
        assert body["coded"]["code"] == "approvals.asked_waiting"
        assert body["coded"]["params"]["request"] == int(body["fields"]["approval"])
        assert body["notes_coded"] == [
            {
                "code": "controls.asked_by_setting",
                "params": {
                    "control": "approvals",
                    "command": "noust compliance set approvals off",
                },
            }
        ]

    def test_the_fields_are_the_two_a_release_shipped_and_no_more(
        self, app: Any, policy: dict[str, Any], real_config: Any
    ) -> None:
        real_config.set("approval.enabled", True)
        alice = Person(app, "alice", "admin")
        alice.elevate()

        body = alice.post("/api/cron", JOB, **{"X-Noust-Reason": "ticket 42"}).json()

        assert set(body["fields"]) == {"approval", "state"}

    def test_an_approval_a_framework_asks_for_names_the_framework(
        self, app: Any, policy: dict[str, Any], real_config: Any
    ) -> None:
        real_config.set("security.frameworks", ["ens-media"])
        alice = Person(app, "alice", "admin")
        alice.elevate()

        asked = alice.post("/api/cron", JOB, **{"X-Noust-Reason": "ticket 42"})

        body = asked.json()
        assert "ens-media" in body["detail"]
        [note] = body["notes_coded"]
        assert note["code"] == "controls.asked_by_framework"
        assert note["params"]["frameworks"] == "ens-media"
        assert note["params"]["command"].startswith("noust compliance disable ens-media")

    def test_the_reason_names_its_own_control(
        self, app: Any, policy: dict[str, Any], real_config: Any
    ) -> None:
        real_config.set("security.change_reasons", True)
        policy["reason_required"] = True
        alice = Person(app, "alice", "admin")
        alice.elevate()

        refused = alice.post("/api/cron", JOB)

        assert refused.status_code == 400
        body = refused.json()
        assert body["error"] == "approval_reason_required"
        assert body["fields"] is None
        assert body["coded"] == {
            "code": "approvals.reason_required",
            "params": {"header": "X-Noust-Reason"},
        }
        [note] = body["notes_coded"]
        assert note["code"] == "controls.asked_by_setting"
        assert note["params"]["control"] == "change_reasons"
        assert "noust compliance set change_reasons off" in body["detail"]

    def test_what_a_client_that_knows_no_codes_reads_is_the_same_english(
        self, app: Any, policy: dict[str, Any], real_config: Any
    ) -> None:
        from noust.core.server_text import say

        real_config.set("security.change_reasons", True)
        policy["reason_required"] = True
        alice = Person(app, "alice", "admin")
        alice.elevate()

        body = alice.post("/api/cron", JOB).json()

        asked = say("approvals.reason_required", header="X-Noust-Reason")
        assert body["detail"].startswith(asked.text)
        assert body["hint"] == asked.hint

    def test_an_answer_nobody_coded_says_so_with_nothing(self, app: Any) -> None:
        alice = Person(app, "alice", "admin")

        missing = alice.client.get("/api/apps/no-such-app.example.com")

        assert missing.json().get("coded") is None
        assert missing.json().get("notes_coded", []) == []

    def test_an_approval_already_given_says_to_send_the_call_now(self) -> None:
        from types import SimpleNamespace

        from noust.web.api.approvals import asked_sentence

        policy = ApprovalPolicy(enabled=True)
        waiting = asked_sentence(SimpleNamespace(id=7, state="requested"), policy)  # type: ignore[arg-type]
        given = asked_sentence(SimpleNamespace(id=7, state="approved"), policy)  # type: ignore[arg-type]

        assert waiting.code == "approvals.asked_waiting" and waiting.params["request"] == 7
        assert given.code == "approvals.asked_approved"
        assert "send the call again" in given.text
        assert given.hint is not None and "X-Noust-Approval: 7" in given.hint


class TestACentralThatDidNotVouch:
    """A node asks for the second person's word on the central, in a sentence the console can translate."""

    def test_the_refusal_is_a_sentence_with_a_code_and_the_control_that_asks(
        self, app: Any, cron: Any, policy: dict[str, Any], real_config: Any
    ) -> None:
        real_config.set("approval.enabled", True)
        allow_root_equivalent_from_centrals()
        token = str(get_token_manager().create_fleet_token("fleet-nas")["token"])
        client = TestClient(app, client=("127.0.0.1", 50000))

        refused = client.post(
            "/api/cron",
            json=JOB,
            headers={
                "Authorization": f"Bearer {token}",
                FLEET_ACTOR_HEADER: "maria",
                FLEET_ACTOR_ROLE_HEADER: "admin",
                FLEET_ELEVATED_HEADER: "1",
            },
        )

        assert refused.status_code == 403, refused.text
        body = refused.json()
        assert body["error"] == "approval_required"
        assert body["coded"] == {"code": "approvals.central_missing", "params": {}}
        assert body["fields"] is None
        [note] = body["notes_coded"]
        assert note["code"] == "controls.asked_by_setting"
        assert "second person's approval on the central" in body["detail"]
        assert body["hint"].startswith("Ask for it on the central")
        assert cron.created == []


class TestFleet:
    def test_a_central_s_call_needs_the_central_s_word(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        allow_root_equivalent_from_centrals()
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


def token_for(username: str, *permissions: str) -> dict[str, str]:
    """An API token of an account's own, as a Bearer header."""
    manager = get_token_manager()
    owner = manager.accounts.require(username)
    issued = manager.create_api_token(
        f"{username}-token",
        "read",
        owner=owner,
        permissions=list(permissions),
        allow_elevated=True,
    )
    return {"Authorization": f"Bearer {issued['token']}"}


class TestAccountsCannotBeMadeWithoutASecondPerson:
    """Security review 3.1, finding 1: creating or recovering an account is a role grant."""

    def test_creating_an_account_needs_approval(self, app: Any, policy: dict[str, Any]) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        asked = sam.post(
            "/api/auth/accounts", {"username": "shadow", "role": "admin", "password": PASSWORD}
        )
        assert asked.status_code == 202, asked.text
        assert AccountManager().find("shadow") is None
        approval_id = asked.headers["X-Noust-Approval-Request"]
        request = sam.client.get(f"/api/approvals/{approval_id}").json()
        assert request["kind"] == "role_change"

        # Only a security officer decides a role change, never an admin.
        policy["approvers"] = ("security", "admin")
        alice = Person(app, "alice", "admin")
        alice.elevate()
        assert alice.post(f"/api/approvals/{approval_id}/approve", {}).status_code in (403, 404)

    def test_inviting_a_new_account_needs_approval(self, app: Any, policy: dict[str, Any]) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        asked = sam.post("/api/auth/invitations", {"username": "shadow", "role": "admin"})
        assert asked.status_code == 202, asked.text
        assert AccountManager().find("shadow") is None

    def test_recovering_an_existing_admin_needs_approval(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("alice", "admin", password=PASSWORD)
        sam = Person(app, "sam", "security")
        tess = Person(app, "tess", "security")
        sam.elevate()
        asked = sam.post("/api/auth/invitations", {"username": "alice"})
        assert asked.status_code == 202, asked.text
        approval_id = asked.headers["X-Noust-Approval-Request"]

        tess.elevate()
        assert tess.post(f"/api/approvals/{approval_id}/approve", {}).status_code == 200
        done = sam.post(
            "/api/auth/invitations", {"username": "alice"}, **{"X-Noust-Approval": approval_id}
        )
        assert done.status_code == 201, done.text

    def test_the_first_account_needs_nobody_else(self, app: Any, policy: dict[str, Any]) -> None:
        token = get_token_manager().generate_master_token()
        client = TestClient(app, client=("testclient", 50000))
        created = client.post(
            "/api/auth/accounts",
            json={"username": "first", "role": "security", "password": PASSWORD},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert created.status_code == 201, created.text

    def test_nobody_recovers_their_own_account(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        refused = sam.post("/api/auth/invitations", {"username": "sam"})
        assert refused.status_code == 400, refused.text
        assert "your own account" in refused.json()["detail"]

    def test_nobody_recovers_another_account_of_theirs_with_a_role_they_may_not_hold(
        self, app: Any, real_config: Any
    ) -> None:
        # Saved, as `noust config set` saves it: issuing an invitation reads the mail settings
        # afresh from the file, which drops what was only ever set in memory.
        real_config.set("security.separation_of_duties", True)
        real_config.save()
        accounts = get_token_manager().accounts
        accounts.add_exception("person-1", "Single operator for the migration", days=1)
        accounts.create("sam-ops", "admin", password=PASSWORD, person_ref="person-1")
        sam = Person(app, "sam", "security", person_ref="person-1")
        accounts.revoke_exception(accounts.list_exceptions()[0].id)
        sam.elevate()
        refused = sam.post("/api/auth/invitations", {"username": "sam-ops"})
        assert refused.status_code == 400, refused.text
        assert "belongs to you" in refused.json()["detail"]


class TestNoSelfApprovalThroughAToken:
    """Security review 3.1, finding 2: a token is its owner, for deciding."""

    def test_the_owner_of_the_requesting_token_cannot_approve(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        sam = Person(app, "sam", "security")
        bearer = token_for("sam", "accounts.manage", "accounts.read")
        client = TestClient(app, client=("testclient", 50000))
        asked = client.patch("/api/auth/accounts/maria", json={"role": "admin"}, headers=bearer)
        assert asked.status_code == 202, asked.text
        approval_id = asked.headers["X-Noust-Approval-Request"]

        sam.elevate()
        own = sam.post(f"/api/approvals/{approval_id}/approve", {})
        assert own.status_code == 403, own.text
        assert own.json()["error"] == "approval_denied"

    def test_another_account_of_the_same_person_cannot_approve(
        self, app: Any, policy: dict[str, Any]
    ) -> None:
        get_token_manager().accounts.create("maria", "operator", password=PASSWORD)
        Person(app, "sam", "security", person_ref="person-1")
        tess = Person(app, "sam2", "security", person_ref="person-1")
        bearer = token_for("sam", "accounts.manage", "accounts.read")
        client = TestClient(app, client=("testclient", 50000))
        asked = client.patch("/api/auth/accounts/maria", json={"role": "admin"}, headers=bearer)
        approval_id = asked.headers["X-Noust-Approval-Request"]

        tess.elevate()
        refused = tess.post(f"/api/approvals/{approval_id}/approve", {})
        assert refused.status_code == 403, refused.text


class TestNoOwnRoleChangeThroughAToken:
    """Security review 3.1, finding 3: a token names its owner, not a username."""

    def test_a_security_officer_cannot_change_their_own_role_with_a_token(self, app: Any) -> None:
        Person(app, "sam", "security")
        bearer = token_for("sam", "accounts.manage", "accounts.read")
        client = TestClient(app, client=("testclient", 50000))
        response = client.patch("/api/auth/accounts/sam", json={"role": "admin"}, headers=bearer)
        assert response.status_code == 400, response.text
        assert "your own account" in response.json()["detail"]
        assert AccountManager().require("sam").role == "security"


class TestDatabaseWritesNeedApproval:
    """Security review 3.1, finding 7: every way of writing through the console."""

    def test_explain_analyze_runs_the_statement(self) -> None:
        explain = approvals_module.rule_for(
            "POST", "/api/databases/query/explain", ["databases.read"], {"analyze": True}, {}
        )
        assert explain is not None and explain.kind == "infrastructure"
        assert (
            approvals_module.rule_for(
                "POST", "/api/databases/query/explain", ["databases.read"], {"analyze": False}, {}
            )
            is None
        )

    @pytest.mark.parametrize(
        ("method", "template"),
        [
            ("POST", "/api/databases/databases/{engine}/{name}/rows"),
            ("PATCH", "/api/databases/databases/{engine}/{name}/rows"),
            ("POST", "/api/databases/databases/{engine}/{name}/rows/delete"),
        ],
    )
    def test_row_edits_write(self, method: str, template: str) -> None:
        rule = approvals_module.rule_for(method, template, ["databases.write"], {}, {})
        assert rule is not None and rule.kind == "infrastructure"


class TestFleetApprovalRecords:
    """Security review 3.1, low: a central's approval is recorded with both people."""

    HEADERS = {
        FLEET_ACTOR_HEADER: "maria",
        FLEET_ACTOR_ROLE_HEADER: "admin",
        FLEET_ELEVATED_HEADER: "1",
    }

    def client(self, app: Any) -> tuple[TestClient, dict[str, str]]:
        allow_root_equivalent_from_centrals()
        token = str(get_token_manager().create_fleet_token("fleet-nas")["token"])
        return TestClient(app, client=("127.0.0.1", 50000)), {
            "Authorization": f"Bearer {token}",
            **self.HEADERS,
        }

    def test_both_identities_are_on_record(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        client, headers = self.client(app)
        vouched = client.post(
            "/api/cron",
            json=JOB,
            headers={**headers, "X-Noust-Approved-By": "sam", "X-Noust-Approval": "7"},
        )
        assert vouched.status_code == 201, vouched.text
        use = [entry for entry in audit_actions() if entry["action"] == "approval.use"][-1]
        assert use["details"]["approved_by"]["name"] == "sam"
        assert use["details"]["requested_by"]["name"] == "maria"
        assert use["details"]["requested_by"]["central"] == "fleet-nas"

    def test_a_central_cannot_say_the_requester_approved_it(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        client, headers = self.client(app)
        refused = client.post(
            "/api/cron",
            json=JOB,
            headers={**headers, "X-Noust-Approved-By": "maria", "X-Noust-Approval": "7"},
        )
        assert refused.status_code == 403, refused.text
        assert cron.created == []

    def test_a_malformed_approval_is_refused(
        self, app: Any, cron: Any, policy: dict[str, Any]
    ) -> None:
        client, headers = self.client(app)
        refused = client.post(
            "/api/cron",
            json=JOB,
            headers={**headers, "X-Noust-Approved-By": "sam", "X-Noust-Approval": "7; drop"},
        )
        assert refused.status_code == 403, refused.text
