# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/config/approval-policy``: reading the table and changing it (item 184).

The route reads the table with what the release ships, what the operator chose and what the floor fixes for
each cell, so that a screen never offers what cannot be; it changes it in sudo mode and with
``security.manage``, and giving a token or the assistant more freedom than it has waits for a second person
where the server asks for one, while taking it away does not.
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
from noust.core.caller_policy import CallerPolicy
from noust.core.operation_classes import CHANGE_PERMISSIONS
from noust.core.store import NoustStore
from noust.web.auth import SecurityConfig
from noust.web.server import create_app
from tests.support.policy_people import Person, forget_assistants, issue

URL = "/api/config/approval-policy"


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
def clean(real_config: Any) -> Iterator[None]:
    forget_assistants()
    yield
    forget_assistants()


@pytest.fixture
def app(sandbox: Path) -> Any:
    config = SecurityConfig(
        state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
    )
    return create_app(config)


def cell(table: dict[str, Any], permission: str, caller: str, environment: str) -> dict[str, Any]:
    (row,) = [r for r in table["rows"] if r["permission"] == permission]
    (found,) = [
        c for c in row["cells"] if c["caller"] == caller and c["environment"] == environment
    ]
    found_cell: dict[str, Any] = found
    return found_cell


def change(caller: str, environment: str, permission: str, value: str) -> dict[str, Any]:
    return {
        "cells": [
            {
                "caller": caller,
                "environment": environment,
                "permission": permission,
                "value": value,
            }
        ]
    }


class TestReadingTheTable:
    def test_it_lists_every_permission_that_changes_something_with_its_four_cells(
        self, app: Any
    ) -> None:
        sam = Person(app, "sam", "security")
        table = sam.get(URL).json()
        assert table["values"] == ["alone", "propose", "never"]
        assert table["callers"] == ["token", "assistant"]
        assert table["environments"] == ["production", "other"]
        assert {row["permission"] for row in table["rows"]} == set(CHANGE_PERMISSIONS)
        assert table["chosen"] == 0
        for row in table["rows"]:
            assert row["description"]
            assert len(row["cells"]) == 4
        areas = [row["area"] for row in table["rows"]]
        assert areas == sorted(areas)

    def test_a_token_is_alone_and_the_assistant_proposes_as_shipped(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        table = sam.get(URL).json()
        deploy = cell(table, "apps.deploy", "token", "production")
        assert (deploy["value"], deploy["default"], deploy["chosen"]) == ("alone", "alone", None)
        assert deploy["offered"] == ["alone", "propose", "never"] and deploy["fixed"] is False
        assert cell(table, "apps.deploy", "assistant", "production")["value"] == "propose"
        assert cell(table, "apps.deploy", "assistant", "production")["offered"] == [
            "alone",
            "propose",
            "never",
        ]

    def test_what_the_floor_fixes_is_said_so_a_screen_does_not_offer_it(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        table = sam.get(URL).json()
        reveal = cell(table, "secrets.reveal", "assistant", "production")
        assert reveal["value"] == "never"
        assert reveal["offered"] == ["never"] and reveal["fixed"] is True
        assert reveal["reason"] == "assistant_floor"
        assert "secrets.reveal" in table["floor"]["assistant_never"]
        assert set(table["floor"]["assistant_alone"]) == {
            "apps.operate",
            "apps.deploy",
            "backups.run",
        }
        writes = cell(table, "databases.write", "assistant", "other")
        assert writes["offered"] == ["propose", "never"]
        assert writes["reason"] == "assistant_narrow"
        # What the owner wanted the assistant able to propose is not locked by the floor.
        manage = cell(table, "apps.manage", "assistant", "production")
        assert manage["fixed"] is False and "never" in manage["offered"]

    def test_the_key_marks_the_rows_that_ask_you_to_confirm_it_is_you(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        table = sam.get(URL).json()
        rows = {r["permission"]: r for r in table["rows"]}
        assert rows["apps.manage"]["key"] is True and rows["apps.operate"]["key"] is False
        manage = cell(table, "apps.manage", "token", "production")
        # Not offered, because it asks you to confirm it is you; shipped as it always was.
        assert manage["offered"] == ["propose", "never"]
        assert (manage["reason"], manage["legacy"], manage["value"]) == ("confirm", True, "alone")
        assert cell(table, "apps.operate", "token", "production")["legacy"] is False
        sue = Person(app, "sue", "security")
        sue.elevate()
        response = sue.put(URL, change("token", "production", "apps.manage", "alone"))
        assert response.status_code == 400
        assert "confirm it is you" in response.json()["hint"]

    def test_a_row_somebody_changed_says_so_for_the_filter(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        sam.put(URL, change("token", "production", "apps.deploy", "never"))
        rows = {r["permission"]: r for r in sam.get(URL).json()["rows"]}
        assert rows["apps.deploy"]["changed"] is True
        assert rows["apps.operate"]["changed"] is False

    def test_it_counts_what_each_row_governs_and_how_much_of_it_a_person_does(
        self, app: Any
    ) -> None:
        sam = Person(app, "sam", "security")
        rows = {r["permission"]: r for r in sam.get(URL).json()["rows"]}
        assert rows["apps.operate"]["operations"]["total"] > 0
        # All but the one that only checks a candidate unit (exempt from the second person's rule).
        root = rows["root_equivalent"]["operations"]
        assert root["strong"] == root["total"] - 1
        assert rows["apps.manage"]["operations"]["strong"] > 0

    def test_the_profile_that_asks_for_it_raises_the_floor_of_a_token_in_production(
        self, app: Any, real_config: Any
    ) -> None:
        real_config.set("security.token_production_approval", True)
        sam = Person(app, "sam", "security")
        table = sam.get(URL).json()
        assert table["profile"]["token_production_approval"] is True
        production = cell(table, "apps.deploy", "token", "production")
        assert (production["value"], production["offered"]) == ("propose", ["propose", "never"])
        assert production["reason"] == "profile_floor"
        assert cell(table, "apps.deploy", "token", "other")["value"] == "alone"

    def test_a_viewer_reads_it_and_an_anonymous_caller_does_not(self, app: Any) -> None:
        vera = Person(app, "vera", "viewer")
        assert vera.get(URL).status_code == 200
        anonymous = TestClient(app, client=("testclient", 50001))
        assert anonymous.get(URL).status_code == 401


class TestChangingIt:
    def test_a_person_with_security_manage_in_sudo_mode_gives_cells_a_value(
        self, app: Any, store: NoustStore
    ) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        response = sam.put(URL, change("token", "production", "apps.deploy", "propose"))
        assert response.status_code == 200, response.text
        table = response.json()
        deploy = cell(table, "apps.deploy", "token", "production")
        assert (deploy["value"], deploy["chosen"], deploy["default"]) == (
            "propose",
            "propose",
            "alone",
        )
        assert table["chosen"] == 1
        assert CallerPolicy(store).cell("token", "production", "apps.deploy").value == "propose"
        assert (
            cell(sam.get(URL).json(), "apps.deploy", "token", "production")["chosen"] == "propose"
        )

    def test_it_asks_for_sudo_mode_and_for_the_permission(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        refused = sam.put(URL, change("token", "production", "apps.deploy", "propose"))
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"
        operator = Person(app, "olga", "operator")
        operator.elevate()
        denied = operator.put(URL, change("token", "production", "apps.deploy", "propose"))
        assert denied.status_code == 403
        assert denied.json()["error"] == "permission_denied"

    def test_a_cell_that_does_not_exist_or_a_value_it_may_not_have_is_refused_and_nothing_changes(
        self, app: Any
    ) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        bad = {
            "cells": [
                {
                    "caller": "token",
                    "environment": "production",
                    "permission": "apps.deploy",
                    "value": "propose",
                },
                {
                    "caller": "assistant",
                    "environment": "production",
                    "permission": "secrets.reveal",
                    "value": "alone",
                },
            ]
        }
        refused = sam.put(URL, bad)
        assert refused.status_code == 400, refused.text
        assert "never" in refused.json()["detail"]
        assert "value" in refused.json()["fields"]
        table = sam.get(URL).json()
        assert table["chosen"] == 0
        for body in (
            change("person", "production", "apps.deploy", "alone"),
            change("token", "staging", "apps.deploy", "alone"),
            change("token", "production", "apps.read", "alone"),
            change("token", "production", "apps.deploy", "sometimes"),
        ):
            assert sam.put(URL, body).status_code == 400

    def test_cells_are_put_back_to_what_the_release_ships(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        sam.put(URL, change("token", "production", "apps.deploy", "never"))
        back = sam.put(
            URL,
            {
                "reset": [
                    {"caller": "token", "environment": "production", "permission": "apps.deploy"}
                ]
            },
        )
        assert back.status_code == 200, back.text
        deploy = cell(back.json(), "apps.deploy", "token", "production")
        assert (deploy["value"], deploy["chosen"]) == ("alone", None)

    def test_a_cell_the_profile_floors_cannot_be_lowered(self, app: Any, real_config: Any) -> None:
        real_config.set("security.token_production_approval", True)
        sam = Person(app, "sam", "security")
        sam.elevate()
        refused = sam.put(URL, change("token", "production", "apps.deploy", "alone"))
        assert refused.status_code == 400
        assert "propose" in refused.json()["detail"]

    def test_a_body_with_too_many_cells_is_refused(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        cells = [
            {
                "caller": "token",
                "environment": "production",
                "permission": "apps.deploy",
                "value": "alone",
            }
        ] * 401
        assert sam.put(URL, {"cells": cells}).status_code == 422

    def test_the_change_is_on_the_audit_trail_with_who_and_what(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        sam.put(URL, change("assistant", "other", "apps.operate", "alone"))
        path = audit.get_log().path
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        found = [e for e in events if e["action"] == "approval.policy"]
        assert found and found[-1]["actor"] == "sam"
        (written,) = found[-1]["details"]["cells"]
        assert (written["caller"], written["environment"], written["value"]) == (
            "assistant",
            "other",
            "alone",
        )


class TestGivingMoreFreedomAsksForASecondPerson:
    @pytest.fixture(autouse=True)
    def approvals_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            approvals_module, "load_approval_policy", lambda: ApprovalPolicy(enabled=True)
        )

    def test_a_change_that_loosens_waits_for_another_security_officer(
        self, app: Any, store: NoustStore
    ) -> None:
        CallerPolicy(store).set_cells([("token", "production", "apps.deploy", "never")], by="x")
        sam = Person(app, "sam", "security")
        sue = Person(app, "sue", "security")
        sam.elevate()
        asked = sam.put(URL, change("token", "production", "apps.deploy", "propose"))
        assert asked.status_code == 202, asked.text
        approval_id = asked.headers["X-Noust-Approval-Request"]
        assert CallerPolicy(store).cell("token", "production", "apps.deploy").value == "never"
        shown = sue.get(f"/api/approvals/{approval_id}").json()
        assert shown["action"] == "approvals.policy"
        assert shown["kind"] == "role_change"
        assert sue.decide(approval_id).status_code == 200
        done = sam.put(
            URL,
            change("token", "production", "apps.deploy", "propose"),
            **{"X-Noust-Approval": approval_id},
        )
        assert done.status_code == 200, done.text
        assert CallerPolicy(store).cell("token", "production", "apps.deploy").value == "propose"

    def test_a_change_that_only_takes_freedom_away_waits_for_nobody(
        self, app: Any, store: NoustStore
    ) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        response = sam.put(URL, change("token", "production", "apps.deploy", "never"))
        assert response.status_code == 200, response.text
        assert CallerPolicy(store).cell("token", "production", "apps.deploy").value == "never"

    def test_the_schema_says_in_advance_that_a_loosening_waits(self, app: Any) -> None:
        schema = app.openapi()
        operation = schema["paths"][URL]["put"]
        assert operation["x-noust-requires-approval"]["action"] == "approvals.policy"
        assert operation["x-noust-permission"] == "security.manage"
        assert operation["x-noust-requires-elevation"] is True
        assert schema["paths"][URL]["get"]["x-noust-permission"] == "settings.read"


class TestACallerThatIsNotAPersonCannotChangeItBeyondItsOwnCell:
    def test_a_token_whose_cell_is_never_is_refused_and_an_assistant_always_is(
        self, app: Any, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sam = Person(app, "sam", "security")
        client = TestClient(app, client=("testclient", 50002))
        body = change("token", "production", "apps.deploy", "alone")
        script = issue(sam.account, "script", "security.manage", "settings.read", elevated=True)
        assert client.put(URL, json=body, headers=script).status_code == 200
        CallerPolicy(store).set_cells([("token", "production", "security.manage", "never")], by="x")
        assert client.put(URL, json=body, headers=script).status_code == 403
        helper = issue(
            sam.account,
            "helper",
            "security.manage",
            "settings.read",
            elevated=True,
            assistant=True,
            monkeypatch=monkeypatch,
        )
        refused = client.put(URL, json=body, headers=helper)
        assert refused.status_code == 403
        assert refused.json()["error"] == "approval_floor"


@pytest.fixture
def marta_tokens(app: Any, store: NoustStore, tmp_path: Path) -> dict[str, dict[str, str]]:
    """An administrator with three tokens of her own and an application in production."""
    from noust.core.teams import Scope, TeamStore
    from tests.support.teams_support import make_app

    teams = TeamStore(store)
    make_app(
        store,
        "shop.example.com",
        teams.resolve_target(Scope.everything(), team="default"),
        tmp_path,
    )
    marta = Person(app, "marta", "admin")
    tokens = {
        "mcp-portatil": issue(marta.account, "mcp-portatil", "docker.manage", "apps.read"),
        "ci": issue(marta.account, "ci", "apps.operate", "apps.read"),
        "reader": issue(marta.account, "reader", "apps.read"),
        "gone": issue(marta.account, "gone", "docker.manage"),
    }
    from noust.web.server import get_token_manager

    manager = get_token_manager()
    (gone,) = [t for t in manager.list_api_tokens() if t["name"] == "gone"]
    manager.revoke_api_token(int(gone["id"]))
    return tokens


class TestWhatSavingWouldChange:
    """The dialog that opens before saving: which cells change, which credentials behave differently."""

    def test_it_lists_the_cells_the_commands_and_the_credentials_and_writes_nothing(
        self, app: Any, marta_tokens: dict[str, dict[str, str]]
    ) -> None:
        sam = Person(app, "sam", "security")
        body = {
            "cells": [
                {
                    "caller": "assistant",
                    "environment": "production",
                    "permission": "apps.operate",
                    "value": "alone",
                },
                {
                    "caller": "token",
                    "environment": "other",
                    "permission": "docker.manage",
                    "value": "never",
                },
            ]
        }
        answer = sam.post(URL + "/preview", body)
        assert answer.status_code == 200, answer.text
        shown = answer.json()
        assert [
            (c["caller"], c["permission"], c["before"], c["after"]) for c in shown["changes"]
        ] == [
            ("assistant", "apps.operate", "propose", "alone"),
            ("token", "docker.manage", "alone", "never"),
        ]
        assert [c["loosens"] for c in shown["changes"]] == [True, False]
        assert shown["commands"] == [
            "noust approvals policy set apps.operate assistant production alone",
            "noust approvals policy set docker.manage token other never",
        ]
        kinds = {(c["kind"], c["name"]) for c in shown["credentials"]}
        # The assistant, and the live tokens that hold docker.manage: not the one that does not, and not
        # a revoked one.
        assert kinds == {("assistant", None), ("token", "mcp-portatil")}
        portable = next(c for c in shown["credentials"] if c["name"] == "mcp-portatil")
        assert portable["owner"] == "marta"
        assert portable["effects"] == [
            {
                "permission": "docker.manage",
                "environment": "other",
                "before": "alone",
                "after": "never",
            }
        ]
        assert shown["waiting"] == 0 and shown["needs_second_person"] is False
        assert sam.get(URL).json()["chosen"] == 0

    def test_a_change_to_what_a_cell_already_is_changes_nothing(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        shown = sam.post(
            URL + "/preview", change("token", "production", "apps.deploy", "alone")
        ).json()
        assert shown["changes"] == [] and shown["commands"] == [] and shown["credentials"] == []

    def test_putting_a_cell_back_is_a_reset_command(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        sam.elevate()
        sam.put(URL, change("token", "production", "apps.deploy", "never"))
        body = {
            "reset": [{"caller": "token", "environment": "production", "permission": "apps.deploy"}]
        }
        shown = sam.post(URL + "/preview", body).json()
        assert shown["commands"] == ["noust approvals policy reset apps.deploy token production"]
        assert (shown["changes"][0]["before"], shown["changes"][0]["after"]) == ("never", "alone")
        assert shown["changes"][0]["reset"] is True

    def test_it_refuses_what_the_save_would_refuse(self, app: Any) -> None:
        sam = Person(app, "sam", "security")
        refused = sam.post(
            URL + "/preview", change("assistant", "production", "secrets.reveal", "alone")
        )
        assert refused.status_code == 400
        assert "never" in refused.json()["detail"]
        key = sam.post(URL + "/preview", change("token", "production", "apps.manage", "alone"))
        assert key.status_code == 400

    def test_it_says_when_saving_asks_for_a_second_person(
        self, app: Any, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            approvals_module, "load_approval_policy", lambda: ApprovalPolicy(enabled=True)
        )
        CallerPolicy(store).set_cells([("token", "production", "apps.deploy", "never")], by="x")
        sam = Person(app, "sam", "security")
        loosening = sam.post(
            URL + "/preview", change("token", "production", "apps.deploy", "propose")
        )
        tightening = sam.post(
            URL + "/preview", change("token", "production", "apps.operate", "never")
        )
        assert loosening.json()["needs_second_person"] is True
        assert tightening.json()["needs_second_person"] is False

    def test_what_already_waits_is_counted_and_does_not_change(
        self, app: Any, marta_tokens: dict[str, dict[str, str]], store: NoustStore
    ) -> None:
        CallerPolicy(store).set_cells([("token", "production", "apps.operate", "propose")], by="x")
        client = TestClient(app, client=("testclient", 50003))
        asked = client.post("/api/apps/shop.example.com/restart", headers=marta_tokens["ci"])
        assert asked.status_code == 202
        sam = Person(app, "sam", "security")
        shown = sam.post(
            URL + "/preview", change("token", "production", "apps.operate", "never")
        ).json()
        assert shown["waiting"] == 1

    def test_it_needs_the_permission_of_changing_the_policy(self, app: Any) -> None:
        vera = Person(app, "vera", "viewer")
        denied = vera.post(URL + "/preview", change("token", "production", "apps.deploy", "never"))
        assert denied.status_code == 403
        assert denied.json()["error"] == "permission_denied"
