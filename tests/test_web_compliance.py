# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/compliance``: the frameworks, the controls and how to change them, over HTTP.

What is pinned: anybody signed in sees which frameworks protect the server; changing them is a
security setting, so an administrator can by default and a viewer cannot; turning a framework on
or a control stricter needs nothing more, while turning one off or loosening needs sudo mode;
warnings must be acknowledged; every change is recorded with the person behind it; and a
central never changes a node's frameworks.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import totp
from noust.core.accounts import AuthPolicy, passwords
from noust.core.compliance import resolve
from noust.core.config import Config
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

PASSWORD = "correct horse battery staple"


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


@pytest.fixture
def app(sandbox: Path, real_config: Config) -> Any:
    return create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
        )
    )


class Person:
    """A signed-in account in its own browser."""

    def __init__(self, app: Any, username: str, role: str) -> None:
        manager = get_token_manager().accounts
        account = manager.create(username, role, password=PASSWORD)
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

    def headers(self) -> dict[str, str]:
        return {CSRF_HEADER_NAME: self.csrf}

    def elevate(self) -> None:
        response = self.client.post(
            "/api/auth/elevate",
            json={"password": PASSWORD, "code": self.codes.pop()},
            headers=self.headers(),
        )
        assert response.status_code == 200, response.text

    def get(self, path: str) -> Any:
        return self.client.get(path)

    def put(self, body: dict[str, Any]) -> Any:
        return self.client.put("/api/compliance", json=body, headers=self.headers())

    def plan(self, body: dict[str, Any]) -> Any:
        return self.client.post("/api/compliance/plan", json=body, headers=self.headers())


def audited(action: str) -> list[dict[str, Any]]:
    from noust.core.audit import get_log

    return [e for e in get_log().read(limit=1000) if e["action"] == action]


class TestReading:
    def test_anybody_signed_in_sees_what_protects_the_server(self, app: Any) -> None:
        viewer = Person(app, "vera", "viewer")

        response = viewer.get("/api/compliance")

        assert response.status_code == 200, response.text
        body = response.json()
        assert not any(f["enabled"] for f in body["frameworks"])
        assert {f["id"] for f in body["frameworks"]} >= {"ens-media", "iso-27001", "nis2"}
        approvals = next(c for c in body["controls"] if c["id"] == "approvals")
        assert approvals["forced_by"] == [] and approvals["enabled"] is False

    def test_nobody_anonymous_does(self, app: Any) -> None:
        anonymous = TestClient(app, client=("testclient", 50000))

        assert anonymous.get("/api/compliance").status_code == 401

    def test_a_framework_that_is_off_lists_what_it_would_change(self, app: Any) -> None:
        body = Person(app, "vera", "viewer").get("/api/compliance").json()

        gdpr = next(f for f in body["frameworks"] if f["id"] == "gdpr-32")
        changed = {change["control"]: change for change in gdpr["changes"]}
        assert changed["backup_encryption"]["after"] == "required"
        assert changed["backup_encryption"]["clauses"] == ["Art. 32(1)(a)"]
        assert gdpr["organisational"] and gdpr["reference"].startswith("Regulation")


class TestChanging:
    def test_an_administrator_turns_a_framework_on_without_sudo_mode(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")

        response = maria.put({"enable": ["nis2"]})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled_after"] == ["nis2"]
        assert next(f for f in body["state"]["frameworks"] if f["id"] == "nis2")["enabled"]
        recorded = audited("security.framework.change")
        assert recorded and "nis2" in recorded[0]["detail"]

    def test_a_viewer_cannot(self, app: Any) -> None:
        vera = Person(app, "vera", "viewer")

        assert vera.put({"enable": ["iso-27001"]}).status_code == 403
        assert vera.plan({"enable": ["iso-27001"]}).status_code == 403

    def test_turning_a_framework_off_needs_sudo_mode(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")
        maria.put({"enable": ["nis2"]})

        refused = maria.put({"disable": ["nis2"]})
        maria.elevate()
        done = maria.put({"disable": ["nis2"]})

        assert refused.status_code == 403 and refused.json()["error"] == "elevation_required"
        assert done.status_code == 200 and done.json()["enabled_after"] == []
        assert resolve.enabled_ids(Config()) == []

    def test_a_control_set_stricter_needs_no_sudo_mode_and_looser_does(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")

        stricter = maria.put({"controls": {"session_idle_minutes": 10}})
        looser = maria.put({"controls": {"session_idle_minutes": 45}})
        maria.elevate()
        allowed = maria.put({"controls": {"session_idle_minutes": 45}})

        assert stricter.status_code == 200
        assert looser.status_code == 403 and looser.json()["error"] == "elevation_required"
        assert allowed.status_code == 200
        assert Config().get("auth.session.idle_minutes") == 45

    def test_a_control_cannot_be_set_looser_than_a_framework_asks(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")
        maria.put({"enable": ["ens-media"], "acknowledge": True})
        sam = Person(app, "sam", "security")

        response = sam.put({"controls": {"approvals": False}})

        assert response.status_code == 400
        assert "ens-media" in response.json()["detail"]

    def test_the_plan_writes_nothing_and_says_what_would_happen(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")

        response = maria.plan({"enable": ["soc2-security"]})

        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["enabled_after"] == ["soc2-security"] and plan["loosens"] is False
        assert {e["control"] for e in plan["effects"]} >= {"approvals", "roles_split"}
        assert resolve.enabled_ids(Config()) == []

    def test_a_warning_must_be_acknowledged(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")

        plan = maria.plan({"enable": ["ens-media"]}).json()
        refused = maria.put({"enable": ["ens-media"]})
        done = maria.put({"enable": ["ens-media"], "acknowledge": True})

        assert [w["id"] for w in plan["warnings"]] == ["no_security_officer"]
        assert refused.status_code == 400 and "confirmation" in refused.json()["detail"]
        assert done.status_code == 200

    def test_unknown_names_are_refused(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")

        assert maria.put({"enable": ["iso-27002"]}).status_code == 400
        assert maria.put({"controls": {"nothing": True}}).status_code == 400

    def test_a_control_change_is_recorded_with_the_person(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")

        maria.put({"controls": {"change_reasons": True}})

        recorded = audited("security.control.change")
        assert recorded and recorded[0]["resource"] == "control:change_reasons"
        assert recorded[0]["actor"] == "maria"


class TestOnceTheRolesAreSplit:
    def test_an_administrator_can_no_longer_change_it_but_a_security_officer_can(
        self, app: Any
    ) -> None:
        sam = Person(app, "sam", "security")
        maria = Person(app, "maria", "admin")
        sam.put({"enable": ["ens-media"], "acknowledge": True})

        assert maria.put({"controls": {"session_idle_minutes": 5}}).status_code == 403
        assert sam.put({"controls": {"session_idle_minutes": 5}}).status_code == 200

    def test_the_security_officer_leaves_ens_with_sudo_mode(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.put({"enable": ["ens-media"], "acknowledge": True})

        refused = sam.put({"disable": ["ens-media"]})
        sam.elevate()
        done = sam.put({"disable": ["ens-media"]})

        assert refused.status_code == 403
        assert done.status_code == 200 and resolve.enabled_ids(Config()) == []
        assert Config().get("security.profile") == "standard"


class TestTheCheck:
    def test_the_check_lists_the_clause_of_each_finding(
        self, app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime, timezone

        from noust.core.ens import checks

        facts = checks.Facts(profile="standard", now=datetime.now(timezone.utc))
        monkeypatch.setattr(checks, "gather", lambda sources=None: facts)
        maria = Person(app, "maria", "admin")

        response = maria.get("/api/compliance/check?framework=nis2")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["frameworks"][0]["framework"] == "nis2"
        assert body["frameworks"][0]["findings"][0]["clauses"][0].startswith("Art. 21(2)")
        assert "does not mean the server" in body["disclaimer"]

    def test_an_unknown_framework_is_refused(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")

        assert maria.get("/api/compliance/check?framework=nope").status_code == 400


class TestOneAccountIsEnough:
    """Item 83: a person running their own servers does not need three accounts."""

    def test_an_administrator_by_default_creates_a_teammates_account_and_reads_the_audit_trail(
        self, app: Any
    ) -> None:
        maria = Person(app, "maria", "admin")
        maria.elevate()

        created = maria.client.post(
            "/api/auth/accounts",
            json={"username": "lucia", "role": "operator", "password": PASSWORD},
            headers=maria.headers(),
        )

        assert created.status_code == 201, created.text
        assert maria.get("/api/auth/accounts").status_code == 200
        assert maria.get("/api/audit").status_code == 200

    def test_by_default_nothing_here_asks_for_a_second_account(self, app: Any) -> None:
        maria = Person(app, "maria", "admin")
        maria.elevate()

        # Same person, two roles: allowed unless somebody asks for the separation.
        first = maria.client.post(
            "/api/auth/accounts",
            json={
                "username": "maria-sec",
                "role": "security",
                "password": PASSWORD,
                "person_ref": "maria@example.com",
            },
            headers=maria.headers(),
        )

        assert first.status_code == 201, first.text


class TestNobodyChangesTheirOwnAccount:
    def mine(self, app: Any) -> Person:
        maria = Person(app, "maria", "admin")
        maria.elevate()
        return maria

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("patch", "/api/auth/accounts/maria", {"role": "operator"}),
            ("patch", "/api/auth/accounts/maria", {"person_ref": "elsewhere@example.com"}),
            ("post", "/api/auth/accounts/maria/disable", {}),
            ("post", "/api/auth/accounts/maria/enable", None),
            ("post", "/api/auth/accounts/maria/unlock", None),
            ("post", "/api/auth/accounts/maria/reset-mfa", None),
            ("delete", "/api/auth/accounts/maria", None),
        ],
    )
    def test_it_is_refused_whatever_the_profile(
        self, app: Any, method: str, path: str, body: Any
    ) -> None:
        maria = self.mine(app)

        response = maria.client.request(method, path, json=body, headers=maria.headers())

        assert response.status_code == 400, response.text
        assert "your own account" in response.json()["detail"]
        assert get_token_manager().accounts.find("maria").role == "admin"

    def test_another_administrator_can(self, app: Any) -> None:
        Person(app, "lucia", "admin")
        maria = self.mine(app)

        response = maria.client.post("/api/auth/accounts/lucia/reset-mfa", headers=maria.headers())

        assert response.status_code == 200, response.text

    def test_it_holds_where_the_roles_are_split_too(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.put({"enable": ["ens-media"], "acknowledge": True})
        sam.elevate()

        response = sam.client.post("/api/auth/accounts/sam/reset-mfa", headers=sam.headers())

        assert response.status_code == 400 and "your own account" in response.json()["detail"]

    def test_a_display_name_is_not_a_change_of_duty(self, app: Any) -> None:
        maria = self.mine(app)

        response = maria.client.patch(
            "/api/auth/accounts/maria", json={"display_name": "Maria L."}, headers=maria.headers()
        )

        assert response.status_code == 200, response.text
