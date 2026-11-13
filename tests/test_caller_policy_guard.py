# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The approval policy for a caller that is not a person, at the guard of the API (item 184).

Driven over HTTP against the whole console with ``POST /api/apps/{domain}/restart`` (``apps.operate``),
whose manager is replaced by a recorder, so that what is pinned is the guard's: a token whose cell says
``alone`` runs the call, ``propose`` makes it a request a person decides in the console and the same call,
made again, runs once, and ``never`` refuses it with the sentence of the policy that forbids it. The
built-in assistant is a token whose payload carries the mark W4 gives it. What must hold: nothing changes
for a token until somebody touches the table, a token never approves (nor its owner what the second-person
rule holds), the environment is the resource's and never a parameter, and the MCP, which calls the API with
the caller's own token, comes through the same place.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core import audit
from noust.core.accounts import AuthPolicy, passwords
from noust.core.accounts import approvals as approvals_module
from noust.core.accounts.approvals import ApprovalPolicy
from noust.core.caller_policy import ALONE, NEVER, OTHER, PRODUCTION, PROPOSE, CallerPolicy
from noust.core.callers import ASSISTANT, TOKEN
from noust.core.credential_clients import CredentialClients
from noust.core.store import NoustStore
from noust.core.teams import Scope, TeamStore
from noust.web.api import apps as apps_api
from noust.web.api.apps import AppActionResponse
from noust.web.auth import SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.support.policy_people import Person, forget_assistants, issue
from tests.support.teams_support import make_app

PASSWORD = "correct horse battery staple"  # the one the helpers sign in with
SHOP = "shop.example.com"
BETA = "beta.example.com"
RESTART = f"/api/apps/{SHOP}/restart"


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


@pytest.fixture(autouse=True)
def real_config(real_config: Any) -> Any:
    return real_config


class Restarts:
    """Stands in for the restart of an application: records what was asked."""

    asked: list[str] = []


@pytest.fixture(autouse=True)
def restarts(monkeypatch: pytest.MonkeyPatch) -> type[Restarts]:
    Restarts.asked = []

    def action(domain: str, verb: str, past: str) -> AppActionResponse:
        Restarts.asked.append(f"{verb} {domain}")
        return AppActionResponse(
            success=True, message=f"Application {past}: {domain}", domain=domain
        )

    monkeypatch.setattr(apps_api, "_service_action", action)
    return Restarts


@pytest.fixture
def app(sandbox: Path) -> Any:
    config = SecurityConfig(
        state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
    )
    return create_app(config)


@pytest.fixture
def client(app: Any) -> TestClient:
    return TestClient(app, client=("testclient", 50000))


@pytest.fixture
def teams(store: NoustStore, tmp_path: Path) -> TeamStore:
    """Two applications of the default project: one in production, one in staging."""
    teams = TeamStore(store)
    teams.create_environment("default", "default", "Staging")
    production = teams.resolve_target(Scope.everything(), team="default")
    staging = teams.resolve_target(Scope.everything(), team="default", environment="staging")
    make_app(store, SHOP, production, tmp_path)
    make_app(store, BETA, staging, tmp_path)
    return teams


@pytest.fixture
def policy(store: NoustStore) -> CallerPolicy:
    return CallerPolicy(store)


@pytest.fixture(autouse=True)
def forget_marked() -> Iterator[None]:
    forget_assistants()
    yield
    forget_assistants()


@pytest.fixture
def marta(app: Any, teams: TeamStore) -> Person:
    return Person(app, "marta", "admin")


def denied_events() -> list[dict[str, Any]]:
    path = audit.get_log().path
    if not path.exists():
        return []
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [entry for entry in lines if entry.get("action") == "http.denied.approval"]


class TestAServerThatUpdatesChangesNothingForItsTokens:
    def test_a_token_does_what_it_did(
        self, client: TestClient, marta: Person, teams: TeamStore, restarts: type[Restarts]
    ) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        for domain in (SHOP, BETA):
            response = client.post(f"/api/apps/{domain}/restart", headers=ci)
            assert response.status_code == 200, response.text
        assert restarts.asked == [f"restart {SHOP}", f"restart {BETA}"]


class TestWhatThePolicyHolds:
    def test_a_call_a_token_may_not_do_alone_becomes_a_request_and_runs_once_approved(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")

        asked = client.post(RESTART, headers=ci)
        assert asked.status_code == 202, asked.text
        body = asked.json()
        assert body["error"] == "approval_required"
        assert body["coded"]["code"] == "approvals.held_by_policy_token"
        assert body["coded"]["params"]["permission"] == "apps.operate"
        assert body["coded"]["params"]["environment"] == "production"
        approval_id = asked.headers["X-Noust-Approval-Request"]
        assert restarts.asked == []

        # The person sees who called, in which environment and what it needs.
        shown = marta.get(f"/api/approvals/{approval_id}").json()
        assert shown["origin"] == "policy"
        assert shown["caller"] == "token"
        assert shown["environment"] == "production"
        assert shown["domain"] == SHOP
        assert shown["needs"] == ["apps.operate"]
        assert shown["credential"]["name"] == "ci"
        assert shown["credential"]["owner"] == "marta"
        assert shown["client"]["label"] == "pipeline-ci 1.4"
        assert shown["client"]["declared"] is True
        assert shown["owned"] is True and shown["can_decide"] is True
        assert shown["requester"]["name"] == "token:ci"

        # She is the owner of the token and decides it: that is supervising her own tool.
        decided = marta.decide(approval_id)
        assert decided.status_code == 200, decided.text
        assert decided.json()["state"] == "approved"

        # The token makes the same call again and it runs, once.
        again = {**ci, "X-Noust-Approval": approval_id}
        assert client.post(RESTART, headers=again).status_code == 200
        assert restarts.asked == [f"restart {SHOP}"]
        second = client.post(RESTART, headers=again)
        assert second.status_code == 409
        assert second.json()["error"] == "approval_used"
        assert restarts.asked == [f"restart {SHOP}"]

    def test_the_same_call_made_twice_is_one_request(
        self, client: TestClient, marta: Person, teams: TeamStore, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        first = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        second = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        assert first == second

    def test_a_call_that_is_not_the_one_approved_does_not_run(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells(
            [
                (TOKEN, PRODUCTION, "apps.operate", PROPOSE),
                (TOKEN, OTHER, "apps.operate", PROPOSE),
            ],
            by="marta",
        )
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        approval_id = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        marta.decide(approval_id)
        other = client.post(
            f"/api/apps/{BETA}/restart", headers={**ci, "X-Noust-Approval": approval_id}
        )
        assert other.status_code == 409
        assert other.json()["error"] == "approval_mismatch"
        assert restarts.asked == []

    def test_what_is_never_allowed_is_refused_with_the_policy_that_forbids_it(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", NEVER)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        refused = client.post(RESTART, headers=ci)
        assert refused.status_code == 403, refused.text
        body = refused.json()
        assert body["error"] == "approval_never"
        assert "apps.operate" in body["detail"] and "production" in body["detail"]
        assert "security.manage" in body["hint"]
        assert body["coded"]["code"] == "approvals.never_by_policy_token"
        assert restarts.asked == []
        recorded = denied_events()
        assert recorded and recorded[-1]["details"]["caller"] == "token"
        assert recorded[-1]["details"]["environment"] == "production"
        assert recorded[-1]["actor"] == "token:ci"

    def test_an_approval_does_not_open_what_the_policy_never_allows(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        approval_id = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        marta.decide(approval_id)
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", NEVER)], by="marta")
        refused = client.post(RESTART, headers={**ci, "X-Noust-Approval": approval_id})
        assert refused.status_code == 403
        assert restarts.asked == []

    def test_a_cell_set_to_alone_again_lets_the_call_through_with_no_request(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        assert client.post(RESTART, headers=ci).status_code == 202
        policy.reset_cells([(TOKEN, PRODUCTION, "apps.operate")])
        assert client.post(RESTART, headers=ci).status_code == 200
        assert restarts.asked == [f"restart {SHOP}"]


class TestTheEnvironmentIsTheResources:
    def test_production_is_held_and_another_environment_is_not(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        assert client.post(f"/api/apps/{BETA}/restart", headers=ci).status_code == 200
        assert client.post(RESTART, headers=ci).status_code == 202
        assert restarts.asked == [f"restart {BETA}"]

    def test_the_other_environments_can_be_held_too(
        self, client: TestClient, marta: Person, teams: TeamStore, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, OTHER, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        assert client.post(f"/api/apps/{BETA}/restart", headers=ci).status_code == 202
        assert client.post(RESTART, headers=ci).status_code == 200

    def test_what_a_caller_says_about_where_it_is_decides_nothing(
        self, client: TestClient, marta: Person, teams: TeamStore, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        claims = {
            **ci,
            "X-Noust-Environment": "staging",
            "X-Environment": "other",
            "X-Noust-Caller": "person",
        }
        response = client.post(
            f"{RESTART}?environment=other&env=staging",
            json={"environment": "other"},
            headers=claims,
        )
        assert response.status_code == 202, response.text

    def test_a_resource_that_does_not_exist_is_judged_as_production(
        self, client: TestClient, marta: Person, teams: TeamStore, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", NEVER)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        refused = client.post("/api/apps/ghost.example.com/restart", headers=ci)
        assert refused.status_code == 403
        assert refused.json()["error"] == "approval_never"


class TestTheBuiltInAssistant:
    def test_it_proposes_what_changes_something_in_both_environments(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        restarts: type[Restarts],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        helper = issue(
            marta.account,
            "helper",
            "apps.operate",
            "apps.read",
            assistant=True,
            monkeypatch=monkeypatch,
        )
        for domain in (SHOP, BETA):
            response = client.post(f"/api/apps/{domain}/restart", headers=helper)
            assert response.status_code == 202, response.text
            assert response.json()["coded"]["code"] == "approvals.held_by_policy_assistant"
        assert restarts.asked == []
        shown = marta.get("/api/approvals?caller=assistant").json()
        assert {item["caller"] for item in shown["approvals"]} == {"assistant"}
        assert {item["environment"] for item in shown["approvals"]} == {"production", "other"}

    def test_it_may_be_left_alone_where_it_is_narrow_and_reversible(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        policy.set_cells([(ASSISTANT, OTHER, "apps.operate", ALONE)], by="marta")
        helper = issue(
            marta.account,
            "helper",
            "apps.operate",
            "apps.read",
            assistant=True,
            monkeypatch=monkeypatch,
        )
        assert client.post(f"/api/apps/{BETA}/restart", headers=helper).status_code == 200
        assert client.post(RESTART, headers=helper).status_code == 202
        assert restarts.asked == [f"restart {BETA}"]

    def test_what_asks_for_sudo_is_never_its_own_even_when_its_token_could_hold_it(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        restarts: type[Restarts],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        helper = issue(
            marta.account,
            "helper",
            "apps.manage",
            "apps.read",
            elevated=True,
            assistant=True,
            monkeypatch=monkeypatch,
        )
        refused = client.delete(f"/api/apps/{BETA}", headers=helper)
        assert refused.status_code == 403
        assert refused.json()["error"] == "approval_floor"
        assert refused.json()["coded"]["code"] == "approvals.never_assistant_floor"
        # And the very same token, not marked, is a token and is judged as one.
        ci = issue(marta.account, "ci", "apps.read")
        assert client.delete(f"/api/apps/{BETA}", headers=ci).status_code == 403

    def test_it_never_reads_what_it_may_never_hold(
        self, client: TestClient, app: Any, teams: TeamStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        officer = get_token_manager().accounts.create("sam", "security", password=PASSWORD)
        helper = issue(
            officer, "auditing-helper", "audit.read", assistant=True, monkeypatch=monkeypatch
        )
        refused = client.get("/api/audit", headers=helper)
        assert refused.status_code == 403
        assert refused.json()["error"] == "approval_floor"
        plain = issue(officer, "auditing-script", "audit.read")
        assert client.get("/api/audit", headers=plain).status_code == 200

    def test_it_never_changes_the_policy_itself(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        helper = issue(
            marta.account,
            "helper",
            "security.manage",
            "settings.read",
            elevated=True,
            assistant=True,
            monkeypatch=monkeypatch,
        )
        body = {
            "cells": [
                {
                    "caller": "assistant",
                    "environment": "production",
                    "permission": "apps.operate",
                    "value": "alone",
                }
            ]
        }
        refused = client.put("/api/config/approval-policy", json=body, headers=helper)
        assert refused.status_code == 403
        assert refused.json()["error"] == "approval_floor"


class TestNobodyApprovesThemselves:
    def test_a_token_does_not_approve_its_own_request_nor_any_other(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        approval_id = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        # Even a token made to act in sudo mode, with everything its owner holds.
        powerful = issue(
            marta.account, "powerful", "apps.operate", "apps.read", "accounts.manage", elevated=True
        )
        for headers in (ci, powerful):
            response = client.post(
                f"/api/approvals/{approval_id}/approve", json={}, headers=headers
            )
            assert response.status_code == 403, response.text
        assert marta.get(f"/api/approvals/{approval_id}").json()["state"] == "requested"

    def test_the_master_token_does_not_approve_it_either(
        self, client: TestClient, marta: Person, teams: TeamStore, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        approval_id = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        master = {"Authorization": f"Bearer {get_token_manager().generate_master_token()}"}
        response = client.post(f"/api/approvals/{approval_id}/approve", json={}, headers=master)
        assert response.status_code == 403
        assert response.json()["error"] == "approval_denied"

    def test_the_owner_still_cannot_approve_what_the_second_person_rule_holds(
        self,
        app: Any,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The rule and the policy hold the same call: the request is of both, and the rule wins.
        monkeypatch.setattr(
            approvals_module, "load_approval_policy", lambda: ApprovalPolicy(enabled=True)
        )
        policy.set_cells([(TOKEN, PRODUCTION, "root_equivalent", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "root_equivalent", "apps.read", elevated=True)
        asked = client.post(
            "/api/cron",
            json={"name": "x", "command": "/usr/bin/true", "schedule": "daily"},
            headers=ci,
        )
        assert asked.status_code == 202, asked.text
        assert asked.json()["coded"]["code"] == "approvals.asked_waiting"
        approval_id = asked.headers["X-Noust-Approval-Request"]
        marta.elevate()
        denied = marta.post(f"/api/approvals/{approval_id}/approve", {})
        assert denied.status_code == 403
        assert "own request" in denied.json()["detail"]


class TestWhoSeesWhatThePolicyHeld:
    def test_a_person_of_the_team_who_holds_what_the_call_needs_decides_and_another_team_never_sees_it(
        self,
        app: Any,
        client: TestClient,
        store: NoustStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
        tmp_path: Path,
    ) -> None:
        teams = TeamStore(store)
        for slug in ("acme", "globex"):
            teams.create_team(slug.title(), slug=slug)
        acme = teams.resolve_target(Scope.everything(), team="acme")
        make_app(store, "shop.acme.example", acme, tmp_path)
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")

        alice = Person(app, "alice", "viewer", team="acme")
        omar = Person(app, "omar", "viewer", team="acme")
        bob = Person(app, "bob", "viewer", team="globex")
        # Alice's own token, narrowed to what she holds in her team.
        mine = issue(alice.account, "alice-ci", "apps.operate", "apps.read")
        asked = client.post("/api/apps/shop.acme.example/restart", headers=mine)
        assert asked.status_code == 202, asked.text
        approval_id = asked.headers["X-Noust-Approval-Request"]

        assert bob.get(f"/api/approvals/{approval_id}").status_code == 404
        assert approval_id not in {
            str(a["id"]) for a in bob.get("/api/approvals").json()["approvals"]
        }
        # Omar holds apps.operate in the team: another person of it reads it and decides it.
        assert omar.get(f"/api/approvals/{approval_id}").json()["can_decide"] is True
        decided = omar.decide(approval_id)
        assert decided.status_code == 200, decided.text
        assert decided.json()["decider"]["name"] == "omar"
        again = client.post(
            "/api/apps/shop.acme.example/restart", headers={**mine, "X-Noust-Approval": approval_id}
        )
        assert again.status_code == 200
        assert restarts.asked == ["restart shop.acme.example"]

    def test_a_person_without_what_the_call_needs_does_not_see_it(
        self, app: Any, client: TestClient, marta: Person, teams: TeamStore, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        approval_id = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        vera = Person(app, "vera", "viewer")
        assert vera.get(f"/api/approvals/{approval_id}").status_code == 404
        assert vera.get("/api/approvals").json()["approvals"] == []

    def test_a_request_waiting_for_a_person_counts_in_what_needs_them(
        self, client: TestClient, marta: Person, teams: TeamStore, policy: CallerPolicy
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        approval_id = client.post(RESTART, headers=ci).headers["X-Noust-Approval-Request"]
        items = marta.get("/api/attention?only=own").json()["items"]
        assert f"approval:{approval_id}" in {item["id"] for item in items}


class TestTheClientACredentialSaysItIs:
    def test_the_user_agent_of_a_token_is_noted_once_in_a_while(
        self, client: TestClient, marta: Person, teams: TeamStore
    ) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        client.get("/api/approvals", headers=ci)
        client.get("/api/approvals", headers=ci)
        seen = CredentialClients().seen("ci")
        assert [(item.name, item.version) for item in seen] == [("pipeline-ci", "1.4")]

    def test_a_person_is_not_noted(
        self, client: TestClient, marta: Person, teams: TeamStore
    ) -> None:
        marta.get("/api/approvals")
        assert CredentialClients().seen("marta") == []


def rpc(client: TestClient, headers: dict[str, str], method: str, params: dict[str, Any]) -> Any:
    response = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def audit_lines() -> list[dict[str, Any]]:
    path = audit.get_log().path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestTheMcpComesThroughTheSamePlace:
    """The MCP calls the API with the caller's own token: the policy is decided once, at the guard."""

    @pytest.fixture(autouse=True)
    def mcp_on(self, real_config: Any) -> None:
        real_config.set("mcp.enabled", True)

    def test_a_tool_call_the_policy_holds_waits_and_runs_when_it_names_the_approval(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", PROPOSE)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        rpc(
            client,
            ci,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "mcp-client", "version": "2.1.0"},
            },
        )
        arguments = {"domain": SHOP}

        waiting = rpc(
            client, ci, "tools/call", {"name": "restart_application", "arguments": arguments}
        )
        result = waiting["result"]
        assert result["isError"] is False, result["content"][0]["text"]
        assert result["content"][0]["text"].startswith("Not done.")
        assert "approval" in result["content"][0]["text"]
        assert restarts.asked == []
        (listed,) = marta.get("/api/approvals?caller=token").json()["approvals"]
        assert listed["domain"] == SHOP
        # The client the token said it was, from the handshake, as what it said.
        assert listed["client"]["label"] == "mcp-client 2.1.0"
        assert listed["client"]["source"] == "client_info"
        calls = [e for e in audit_lines() if e["action"] == "mcp.call"]
        assert calls[-1]["result"] == "proposed"

        assert marta.decide(listed["id"]).status_code == 200
        done = rpc(
            client,
            ci,
            "tools/call",
            {"name": "restart_application", "arguments": {**arguments, "approval": listed["id"]}},
        )
        assert done["result"]["isError"] is False
        assert not done["result"]["content"][0]["text"].startswith("Not done")
        assert restarts.asked == [f"restart {SHOP}"]

    def test_a_tool_call_the_policy_never_allows_is_refused_with_its_words(
        self,
        client: TestClient,
        marta: Person,
        teams: TeamStore,
        policy: CallerPolicy,
        restarts: type[Restarts],
    ) -> None:
        policy.set_cells([(TOKEN, PRODUCTION, "apps.operate", NEVER)], by="marta")
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        answer = rpc(
            client,
            ci,
            "tools/call",
            {"name": "restart_application", "arguments": {"domain": SHOP}},
        )
        assert answer["result"]["isError"] is True
        assert "approval_never" in answer["result"]["content"][0]["text"]
        assert restarts.asked == []

    def test_the_call_the_tool_makes_and_the_event_of_the_tool_are_one_in_the_audit_trail(
        self, client: TestClient, marta: Person, teams: TeamStore, restarts: type[Restarts]
    ) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.read")
        rpc(
            client,
            ci,
            "tools/call",
            {"name": "restart_application", "arguments": {"domain": SHOP}},
        )
        lines = audit_lines()
        tool_call = next(e for e in lines if e["action"] == "mcp.call")
        inner = [e for e in lines if e["action"] == "api.post" and e.get("resource") == RESTART]
        assert inner and inner[-1]["corr"] == tool_call["corr"]
        assert inner[-1]["actor"] == tool_call["actor"] == "token:ci"

    def test_the_tools_that_change_something_offer_the_approval_and_the_reads_do_not(
        self, client: TestClient, marta: Person, teams: TeamStore
    ) -> None:
        ci = issue(marta.account, "ci", "apps.operate", "apps.deploy", "apps.read")
        tools = rpc(client, ci, "tools/list", {})["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}
        assert "approval" in by_name["restart_application"]["inputSchema"]["properties"]
        reads = [t for t in tools if t["annotations"]["readOnlyHint"]]
        assert reads
        assert all("approval" not in t["inputSchema"]["properties"] for t in reads)
