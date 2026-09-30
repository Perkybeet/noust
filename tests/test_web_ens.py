# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/ens``: the console's Compliance view over :mod:`noust.core.ens`.

What is pinned here is what the endpoints promise the console: the shape of
the check and the report, the Markdown and CSV downloads, the inventory
update, the lockdown state, that reading the check or the report is recorded,
and that every route is in the permission map with ``compliance.read`` held
by exactly ``admin``, ``security`` and ``auditor``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.ens import checks, incident
from noust.core.store import App, NoustStore
from noust.web.api import ens as ens_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers
from noust.web.permissions import Permission
from noust.web.permissions.registry import route_map
from noust.web.permissions.roles import ROLE_PERMISSIONS

PREFIX = "/api/ens"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(ens_api, "record", lambda event, **kw: events.append((event, kw)))
    return events


@pytest.fixture
def client(store, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def run(refresh: bool) -> tuple[checks.ComplianceCheck, checks.Facts]:
        facts = checks.Facts(profile="ens-medium", now=datetime(2026, 9, 30, tzinfo=timezone.utc))
        result = checks.ComplianceCheck(
            profile="ens-medium",
            checked_at="2026-09-30T00:00:00+00:00",
            host="central-1",
            version="3.1.0",
            findings=[
                checks.Finding(
                    "ENS-ACC-02",
                    "Second factor",
                    "fail",
                    ("op.acc.6.r2",),
                    "1 without.",
                    ("juan",),
                    "Enrol.",
                ),
                checks.Finding("ENS-LOG-01", "Audit log intact", "ok", ("op.exp.8",), "Holds."),
            ],
            indicators={"mfa_percent": 50.0},
        )
        return result, facts

    monkeypatch.setattr(ens_api, "_run", run)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(ens_api.router, prefix=PREFIX)
    app.dependency_overrides[get_current_session] = lambda: {
        "sid": "abc",
        "type": "session",
        "role": "auditor",
    }
    return TestClient(app, raise_server_exceptions=False)


class TestTheCheck:
    def test_the_check_answers_findings_counts_and_verdict(self, client, recorded) -> None:
        response = client.get(f"{PREFIX}/check")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verdict"] == "fail"
        assert body["counts"] == {"fail": 1, "warning": 0, "ok": 1, "n/a": 0}
        assert body["findings"][0]["measures"] == ["op.acc.6.r2"]
        assert body["indicators"]["mfa_percent"] == 50.0
        assert recorded[0][0] == "compliance.read" and recorded[0][1]["target"] == "ens:check"

    def test_the_report_carries_its_digest(self, client, recorded) -> None:
        body = client.get(f"{PREFIX}/report").json()

        assert len(body["sha256"]) == 64
        assert body["report"]["sha256"] == body["sha256"]
        assert body["report"]["measures"][0]["measure"] == "op.acc.6"
        assert recorded[0][1]["details"]["sha256"] == body["sha256"]

    def test_the_report_downloads_as_markdown(self, client, recorded) -> None:
        response = client.get(f"{PREFIX}/report", params={"format": "markdown"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "attachment" in response.headers["content-disposition"]
        assert response.text.startswith("# Informe ENS")
        assert len(response.headers["x-noust-report-sha256"]) == 64

    def test_the_profile_lists_the_baseline(self, client) -> None:
        body = client.get(f"{PREFIX}/profile").json()

        assert body["profile"] in ("standard", "ens-medium")
        assert any(item["key"] == "auth.session.idle_minutes" for item in body["baseline"])


class TestTheInventory:
    def test_update_then_read_and_export(self, client, store, monkeypatch) -> None:
        from noust.core.ens import inventory as inventory_module

        monkeypatch.setattr(inventory_module, "record", lambda *a, **k: None)
        store.create_app(
            App(domain="shop.example.com", app_type="nodejs", source="x", app_path="/x")
        )

        updated = client.put(
            f"{PREFIX}/inventory/shop.example.com",
            json={"owner": "Ventas", "criticality": "high", "classification": "internal"},
        )
        listed = client.get(f"{PREFIX}/inventory").json()
        exported = client.get(f"{PREFIX}/inventory", params={"format": "csv"})

        assert updated.status_code == 200, updated.text
        assert updated.json()["complete"] is True
        assert listed["applications"][0]["owner"] == "Ventas"
        assert listed["criticalities"] == ["low", "medium", "high"]
        assert exported.headers["content-type"].startswith("text/csv")
        assert "Ventas" in exported.text

    def test_a_bad_level_is_a_400_naming_the_field(self, client, store) -> None:
        store.create_app(
            App(domain="shop.example.com", app_type="nodejs", source="x", app_path="/x")
        )

        response = client.put(
            f"{PREFIX}/inventory/shop.example.com", json={"criticality": "extreme"}
        )

        assert response.status_code == 400
        assert "low, medium, high" in response.json()["hint"]


class TestTheLockdown:
    def test_it_says_whether_the_console_is_locked(self, client, store, monkeypatch) -> None:
        monkeypatch.setattr(incident, "record", lambda *a, **k: None)
        assert client.get(f"{PREFIX}/incident").json() == {
            "locked": False,
            "since": None,
            "by": None,
            "reason": None,
            "package": None,
        }

        incident.lock_down(actor="cli:root", reason="INC-3", store=store)

        body = client.get(f"{PREFIX}/incident").json()
        assert body["locked"] is True and body["reason"] == "INC-3"


class TestPermissions:
    def test_every_route_is_in_the_permission_map(self, client) -> None:
        from noust.web.api.openapi import api_routes

        routes = {
            (method, str(route.path_format))
            for route in api_routes(client.app.routes)
            for method in route.methods
            if str(route.path_format).startswith(PREFIX)
        }

        assert len(routes) == 8
        assert routes <= set(route_map())

    def test_compliance_is_read_by_admin_security_and_auditor_only(self) -> None:
        holders = {
            role for role, held in ROLE_PERMISSIONS.items() if Permission.COMPLIANCE_READ in held
        }

        assert holders == {"admin", "security", "auditor"}
        assert route_map()[("GET", f"{PREFIX}/check")] == Permission.COMPLIANCE_READ
        assert route_map()[("GET", f"{PREFIX}/report")] == Permission.COMPLIANCE_READ

    def test_a_read_only_central_may_read_a_nodes_compliance(self) -> None:
        from noust.fleet.policy import FleetAccess, permits

        assert permits(FleetAccess("read", False), Permission.COMPLIANCE_READ)


class TestTheAccessReview:
    @pytest.fixture
    def tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ens_api, "_tokens", lambda: [])

    def test_the_list_then_its_attestation(self, client, store, tokens, monkeypatch) -> None:
        from noust.core.accounts import AccountManager, AuthPolicy, passwords

        monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
        AccountManager(store, policy=AuthPolicy()).create(
            "sofia", "security", password="correct horse battery staple"
        )

        listed = client.get(f"{PREFIX}/access-review").json()
        attested = client.post(
            f"{PREFIX}/access-review", json={"digest": listed["digest"], "notes": "Q3"}
        )

        assert listed["accounts"][0]["username"] == "sofia"
        assert attested.status_code == 200, attested.text
        assert attested.json()["digest"] == listed["digest"]

    def test_a_list_that_changed_is_not_attested(self, client, store, tokens) -> None:
        response = client.post(f"{PREFIX}/access-review", json={"digest": "0" * 64})

        assert response.status_code == 400
        assert response.json()["fields"] is None or "digest" in (response.json()["fields"] or {})
