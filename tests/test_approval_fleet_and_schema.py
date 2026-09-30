"""
Four-eyes approvals across the fleet and in the schema.

- A central forwards a call it consumed an approval for with the headers a node
  with approvals on accepts (``X-Noust-Approval`` and ``X-Noust-Approved-By``),
  exactly as it vouches for sudo mode: only from its own verdict, never from
  what the browser sent. The reason goes with it.
- Every route a rule covers says so in the OpenAPI schema
  (``x-noust-requires-approval``), with the rule's action, kind and condition,
  so the console and a central know in advance what will ask for a second person.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from noust.core.accounts import AuthPolicy
from noust.core.accounts.approvals import ApprovalActor, ApprovalRequest
from noust.web.api.approvals import (
    APPROVAL_HEADER,
    APPROVED_BY_HEADER,
    REASON_HEADER,
    central_approval_headers,
)
from noust.web.api.node_proxy import Upstream, _forwarded_headers
from noust.web.api.openapi import APPROVAL_EXTENSION
from noust.web.auth import SecurityConfig
from noust.web.server import create_app


def approval(*, decider: str | None = "sam.security", state: str = "executed") -> ApprovalRequest:
    return ApprovalRequest(
        id=41,
        action="root_equivalent",
        kind="infrastructure",
        method="POST",
        path="/api/nodes/web-2/api/cron",
        parameters={},
        fingerprint="f" * 64,
        reason="Weekly report",
        state=state,
        requester=ApprovalActor(kind="account", id="1", name="alice", role="admin"),
        created_at=1.0,
        expires_at=2.0,
        decided_at=1.5,
        decider=ApprovalActor(kind="account", id="2", name=decider, role="security")
        if decider
        else None,
        decision_comment=None,
        execute_by=3.0,
        executed_at=1.6,
    )


def request_with(headers: dict[str, str], consumed: ApprovalRequest | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/nodes/web-2/api/cron",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "state": {},
    }
    request = Request(scope)
    if consumed is not None:
        request.state.noust_approval = consumed
    return request


UPSTREAM = Upstream(base_url="http://127.0.0.1:1", headers={"authorization": "Bearer t"})


class TestTheCentralVouches:
    def test_an_approval_the_central_consumed_goes_to_the_node(self) -> None:
        headers = _forwarded_headers(
            request_with({REASON_HEADER: "Weekly%20report"}, approval()), UPSTREAM
        )

        assert headers[APPROVAL_HEADER] == "41"
        assert headers[APPROVED_BY_HEADER] == "sam.security"
        assert headers[REASON_HEADER] == "Weekly%20report"

    def test_what_the_browser_sends_never_reaches_the_node(self) -> None:
        forged = {APPROVAL_HEADER: "99", APPROVED_BY_HEADER: "mallory"}

        headers = _forwarded_headers(request_with(forged), UPSTREAM)

        assert APPROVAL_HEADER not in headers
        assert APPROVED_BY_HEADER not in headers

    def test_the_approver_is_a_label_the_node_accepts(self) -> None:
        assert central_approval_headers(approval(decider="Sam Security <sam>"))[
            APPROVED_BY_HEADER
        ] == ("Sam-Security--sam-")

    def test_a_reason_that_is_not_percent_encoded_is_left_out(self) -> None:
        headers = _forwarded_headers(request_with({REASON_HEADER: "x" * 5000}), UPSTREAM)

        assert REASON_HEADER not in headers


@pytest.fixture
def schema(sandbox: Path) -> dict[str, Any]:
    app = create_app(
        SecurityConfig(
            state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
        )
    )
    return app.openapi()


class TestTheSchemaSaysSo:
    def test_a_route_that_always_needs_a_second_person(self, schema: dict[str, Any]) -> None:
        assert schema["paths"]["/api/nodes"]["post"][APPROVAL_EXTENSION] == {
            "action": "fleet.node.add",
            "kind": "infrastructure",
            "when": "always",
        }

    def test_a_route_that_needs_one_by_its_body(self, schema: dict[str, Any]) -> None:
        marked = schema["paths"]["/api/databases/query"]["post"][APPROVAL_EXTENSION]

        assert marked["action"] == "db.query.write"
        assert marked["when"] == "mode is 'write'"

    def test_a_root_equivalent_route(self, schema: dict[str, Any]) -> None:
        assert schema["paths"]["/api/cron"]["post"][APPROVAL_EXTENSION]["action"] == (
            "root_equivalent"
        )

    def test_nothing_else(self, schema: dict[str, Any]) -> None:
        assert APPROVAL_EXTENSION not in schema["paths"]["/api/apps/{domain}/restart"]["post"]
        assert APPROVAL_EXTENSION not in schema["paths"]["/api/services/verify"]["post"]
        assert all(
            APPROVAL_EXTENSION not in operation
            for operations in schema["paths"].values()
            for method, operation in operations.items()
            if method == "get" and isinstance(operation, dict)
        )
