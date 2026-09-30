"""
Tests for GET /api/audit: the append-only audit log, exposed to admins.

The log itself is :class:`noust.web.auth.AuditLogger`, JSON lines written by
every privileged action the panel performs (see tests/test_web_auth.py for
what gets audited). This module tests the read side: an admin-only endpoint
that returns it newest first, filterable and keyset-paginated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig, get_audit_logger
from noust.web.server import create_app, get_token_manager


def make_config(sandbox: Path, **overrides) -> SecurityConfig:
    """
    Args:
        sandbox: Per-test temporary directory.
        **overrides: Fields to override on the configuration.

    Returns:
        A security configuration whose state lives inside the sandbox.
    """
    params: dict[str, object] = {
        "state_dir": sandbox / "state",
        "rate_limit_requests": 5000,
    }
    params.update(overrides)
    return SecurityConfig(**params)


def build_client(sandbox: Path) -> TestClient:
    """
    Args:
        sandbox: Per-test temporary directory.

    Returns:
        A test client bound to a freshly created application.
    """
    app = create_app(make_config(sandbox))
    return TestClient(app, client=("testclient", 50000))


def login_admin(client: TestClient) -> str:
    """
    Log in with a freshly generated master token, which carries admin scope.

    Args:
        client: The client to sign in.

    Returns:
        The CSRF token for the new session, already installed on the client.
    """
    token = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf_token"]
    client.headers[CSRF_HEADER_NAME] = csrf
    return csrf


def test_unauthenticated_is_refused(sandbox: Path) -> None:
    """Anonymous access must not reach the audit log."""
    client = build_client(sandbox)
    response = client.get("/api/audit")
    assert response.status_code == 401


def test_a_read_scoped_token_is_refused(sandbox: Path) -> None:
    """Audit is sensitive: even a valid, lesser-scoped credential is refused."""
    client = build_client(sandbox)
    login_admin(client)
    reader = get_token_manager().create_api_token("reader", "read")["token"]

    response = client.get("/api/audit", headers={"Authorization": f"Bearer {reader}"})
    assert response.status_code == 403


def test_an_admin_token_is_accepted(sandbox: Path) -> None:
    """An admin-scoped Bearer credential, not only a cookie session, may read it."""
    client = build_client(sandbox)
    login_admin(client)
    admin_token = get_token_manager().create_api_token("automation", "admin")["token"]

    response = client.get("/api/audit", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200


def test_lists_entries_newest_first_with_every_field(sandbox: Path) -> None:
    """The model carries the fields the console's activity view needs."""
    client = build_client(sandbox)
    login_admin(client)
    audit = get_audit_logger()
    assert audit is not None
    for index in range(3):
        audit.record(
            action="test.action",
            result="success",
            client_ip="10.0.0.1",
            actor=f"actor{index}",
            resource="/x",
            detail=f"n{index}",
        )

    response = client.get("/api/audit?limit=50")
    assert response.status_code == 200
    items = response.json()["items"]

    ours = [item for item in items if item["action"] == "test.action"]
    assert [item["actor"] for item in ours] == ["actor2", "actor1", "actor0"]
    assert {"timestamp", "action", "result", "actor", "client_ip", "resource", "detail"} <= set(
        ours[0]
    )
    assert ours[0]["detail"] == "n2"
    assert ours[0]["client_ip"] == "10.0.0.1"


def test_filters_by_action_result_and_actor(sandbox: Path) -> None:
    """Each filter narrows the listing independently."""
    client = build_client(sandbox)
    login_admin(client)
    audit = get_audit_logger()
    assert audit is not None
    audit.record(
        action="apps.delete", result="success", client_ip="10.0.0.1", actor="sid1", resource="/a"
    )
    audit.record(
        action="apps.delete", result="denied", client_ip="10.0.0.1", actor="sid2", resource="/b"
    )
    audit.record(
        action="auth.login", result="success", client_ip="10.0.0.1", actor="sid1", resource="/c"
    )

    by_action = client.get("/api/audit?action=apps.delete").json()["items"]
    assert [item["result"] for item in by_action] == ["denied", "success"]

    by_result = client.get("/api/audit?result=denied").json()["items"]
    assert len(by_result) == 1
    assert by_result[0]["actor"] == "sid2"

    by_actor = client.get("/api/audit?actor=sid1").json()["items"]
    assert {item["action"] for item in by_actor} == {"apps.delete", "auth.login"}


def test_keyset_pagination_walks_every_entry_exactly_once(sandbox: Path) -> None:
    """``before`` from one page is the cursor for the next, until it ends."""
    client = build_client(sandbox)
    login_admin(client)
    audit = get_audit_logger()
    assert audit is not None
    for index in range(5):
        audit.record(
            action="paged", result="success", client_ip="10.0.0.1", actor=str(index), resource="/x"
        )

    first = client.get("/api/audit?limit=2&action=paged").json()
    assert [item["actor"] for item in first["items"]] == ["4", "3"]
    assert first["next_before"] is not None

    second = client.get(f"/api/audit?limit=2&action=paged&before={first['next_before']}").json()
    assert [item["actor"] for item in second["items"]] == ["2", "1"]
    assert second["next_before"] is not None

    third = client.get(f"/api/audit?limit=2&action=paged&before={second['next_before']}").json()
    assert [item["actor"] for item in third["items"]] == ["0"]
    assert third["next_before"] is None


# -- 3.1: the audit trail v2 --------------------------------------------------


def test_entries_carry_the_chain_and_the_structured_actor(sandbox: Path) -> None:
    """3.1 adds fields next to the 3.0 ones; the old ones keep their meaning."""
    from noust.core.audit import Actor, record

    client = build_client(sandbox)
    login_admin(client)
    record(
        "apps.delete",
        actor=Actor(kind="user", id="17", name="maria", role="admin", source="10.0.0.7"),
        target="app:shop.example.com",
        details={"why": "retired"},
        correlation_id="req-1",
    )

    item = client.get("/api/audit?action=apps.delete").json()["items"][0]

    assert item["actor"] == "maria"
    assert item["client_ip"] == "10.0.0.7"
    assert item["resource"] == "app:shop.example.com"
    assert item["detail"] == "why=retired"
    assert item["details"] == {"why": "retired"}
    assert item["category"] == "change"
    assert item["severity"] == 5
    assert item["correlation_id"] == "req-1"
    assert item["who"]["role"] == "admin"
    assert isinstance(item["seq"], int)
    by_correlation = client.get("/api/audit?correlation_id=req-1").json()["items"]
    assert [entry["action"] for entry in by_correlation] == ["apps.delete"]


def test_reading_the_log_is_itself_recorded_once_per_window(sandbox: Path) -> None:
    client = build_client(sandbox)
    login_admin(client)
    for _ in range(3):
        client.get("/api/audit")

    reads = client.get("/api/audit?action=audit.read").json()["items"]
    assert len(reads) == 1
    assert reads[0]["resource"] == "audit log"


def test_verify_reports_an_intact_chain_and_its_limit(sandbox: Path) -> None:
    client = build_client(sandbox)
    login_admin(client)

    body = client.get("/api/audit/verify").json()

    assert body["ok"] is True
    assert body["checked"] > 0
    assert "Root on this machine" in body["limitation"]
    assert client.get("/api/audit?action=audit.verify").json()["items"][0]["result"] == "ok"


def test_verify_names_a_tampered_line(sandbox: Path) -> None:
    import json

    client = build_client(sandbox)
    login_admin(client)
    path = sandbox / "state" / "web-audit.log"
    lines = path.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["result"] = "rewritten"
    lines[1] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n")

    body = client.get("/api/audit/verify").json()

    assert body["ok"] is False
    assert body["broken"]["line"] == 2
    assert "MAC" in body["broken"]["reason"]


def test_status_says_whether_the_trail_works(sandbox: Path) -> None:
    client = build_client(sandbox)
    login_admin(client)
    body = client.get("/api/audit/status").json()
    assert body["status"] == "ok", body["problems"]
    assert body["failing"] is False


def test_a_review_is_an_attestation_in_the_log(sandbox: Path) -> None:
    client = build_client(sandbox)
    login_admin(client)

    created = client.post(
        "/api/audit/reviews",
        json={"period_start": "2000-01-01", "period_end": "2100-12-31", "notes": "weekly"},
    )

    assert created.status_code == 201, created.text
    review = created.json()
    assert review["notes"] == "weekly"
    assert review["chain_ok"] is True
    assert review["events_in_period"] > 0
    listed = client.get("/api/audit/reviews").json()["items"]
    assert [item["notes"] for item in listed] == ["weekly"]


def test_a_review_of_a_reversed_period_is_refused(sandbox: Path) -> None:
    client = build_client(sandbox)
    login_admin(client)
    response = client.post(
        "/api/audit/reviews", json={"period_start": "2026-02-01", "period_end": "2026-01-01"}
    )
    assert response.status_code in (400, 422)


def test_export_is_ndjson_with_the_chain_and_is_recorded(sandbox: Path) -> None:
    import json

    client = build_client(sandbox)
    login_admin(client)

    response = client.get("/api/audit/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0]["action"] == "audit.chain_start"
    assert all("mac" in event for event in events)
    assert client.get("/api/audit?action=audit.export").json()["items"]


def test_the_catalog_is_published_for_the_console(sandbox: Path) -> None:
    client = build_client(sandbox)
    login_admin(client)
    body = client.get("/api/audit/events").json()
    assert "access" in body["categories"]
    names = {event["name"]: event for event in body["events"]}
    assert names["apps.env.reveal"]["sensitive_read"] is True


def test_a_read_token_cannot_reach_the_new_routes(sandbox: Path) -> None:
    client = build_client(sandbox)
    login_admin(client)
    reader = get_token_manager().create_api_token("reader-v2", "read")["token"]
    headers = {"Authorization": f"Bearer {reader}"}
    for path in (
        "/api/audit/verify",
        "/api/audit/status",
        "/api/audit/export",
        "/api/audit/reviews",
    ):
        assert client.get(path, headers=headers).status_code == 403, path


def test_the_events_of_one_request_share_its_correlation_id(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint's event, the middleware's and the host actions they caused are linked."""
    from noust.core.audit.ledger import install_ledger
    from noust.core.fs import get_fs
    from noust.web.api import audit as audit_api

    client = build_client(sandbox)
    login_admin(client)
    install_ledger()
    marker = sandbox / "written-by-request.txt"
    original = audit_api._day_bound

    def bound_and_touch(value: str, *, end: bool) -> str:
        # Stands in for whatever a real endpoint changes on disk.
        get_fs().write_text(marker, "x")
        return original(value, end=end)

    monkeypatch.setattr(audit_api, "_day_bound", bound_and_touch)
    response = client.post(
        "/api/audit/reviews",
        json={"period_start": "2000-01-01", "period_end": "2000-01-02"},
        headers={"X-Noust-Request-Id": "req-link-1"},
    )
    assert response.status_code == 201, response.text

    linked = client.get("/api/audit?correlation_id=req-link-1&limit=50").json()["items"]
    actions = {item["action"] for item in linked}
    assert {"audit.review", "api.post", "host.fs"} <= actions
    assert any(item["resource"] == str(marker) for item in linked)
