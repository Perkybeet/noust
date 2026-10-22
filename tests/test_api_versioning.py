# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The API's version: the ``/api/v1`` address, the version header, the schema's
``info`` block and the deprecation policy (checklist J-03).

``/api/v1/...`` is not a second set of routes. It is the address of the one
set, so everything that judges a request - the permission map, the audit
trail, the rate limiter, the node proxy - judges the canonical ``/api/...``
path and cannot disagree with itself about a route that has two names.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from noust import __version__
from noust.core.accounts import AuthPolicy, passwords
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.permissions import PUBLIC, Permission
from noust.web.permissions.registry import match_path, proxied_path
from noust.web.server import create_app, get_token_manager
from noust.web.versioning import (
    API_VERSION,
    API_VERSION_HEADER,
    MINIMUM_NOTICE_DAYS,
    VERSIONED_PREFIX,
    ApiVersionMiddleware,
    canonical_api_path,
    deprecated,
    versioned_api_path,
)
from tests.test_permission_routes import signed_in
from tests.test_web_auth import build_client, login


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; what is under test does not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def signed(sandbox: Path, runner: FakeRunner) -> TestClient:
    """A client signed in with the master token."""
    client = build_client(sandbox)
    login(client, get_token_manager().generate_master_token())
    return client


class TestTheAddress:
    @pytest.mark.parametrize(
        ("path", "canonical"),
        [
            ("/api/v1", "/api"),
            ("/api/v1/", "/api/"),
            ("/api/v1/apps", "/api/apps"),
            ("/api/v1/apps/shop.example.com/restart", "/api/apps/shop.example.com/restart"),
            ("/api/v1/nodes/nas/api/v1/apps", "/api/nodes/nas/api/v1/apps"),
            # One pass: what is left after the prefix is what the router sees.
            ("/api/v1/v1/apps", "/api/v1/apps"),
        ],
    )
    def test_the_versioned_prefix_is_removed(self, path: str, canonical: str) -> None:
        assert canonical_api_path(path) == canonical

    @pytest.mark.parametrize(
        "path",
        [
            "/api",
            "/api/",
            "/api/apps",
            "/api/v10/apps",
            "/api/v1apps",
            "/api/v2/apps",
            "/api/apps/v1",
            "/apix/v1/apps",
            "/v1/apps",
            "/health",
            "/events",
            "/hooks/deploy/shop.example.com",
            "",
            "/",
        ],
    )
    def test_nothing_else_is_touched(self, path: str) -> None:
        assert canonical_api_path(path) == path

    @pytest.mark.parametrize(
        ("path", "versioned"),
        [
            ("/api/apps", "/api/v1/apps"),
            ("/api/apps/{domain}/restart", "/api/v1/apps/{domain}/restart"),
            ("/api", "/api/v1"),
        ],
    )
    def test_the_versioned_address_of_a_canonical_path(self, path: str, versioned: str) -> None:
        assert versioned_api_path(path) == versioned
        assert canonical_api_path(versioned) == path

    def test_a_path_that_is_not_the_api_has_no_versioned_address(self) -> None:
        with pytest.raises(ValueError, match="/api/"):
            versioned_api_path("/events")

    def test_the_prefix_is_the_published_version(self) -> None:
        assert VERSIONED_PREFIX == f"/api/v{API_VERSION}"


class TestServedUnderBothAddresses:
    def test_a_read_answers_the_same_under_both(self, signed: TestClient) -> None:
        plain = signed.get("/api/apps")
        versioned = signed.get("/api/v1/apps")

        assert plain.status_code == versioned.status_code == 200
        assert plain.json() == versioned.json()

    def test_a_public_route_answers_under_both(self, signed: TestClient) -> None:
        assert signed.get("/api/v1/auth/session").json() == signed.get("/api/auth/session").json()

    def test_a_route_with_a_parameter_answers_under_both(self, signed: TestClient) -> None:
        plain = signed.get("/api/apps/types")
        versioned = signed.get("/api/v1/apps/types")

        assert plain.status_code == versioned.status_code == 200
        assert plain.json() == versioned.json()

    def test_the_schema_is_reachable_under_both(self, signed: TestClient) -> None:
        assert signed.get("/api/v1/openapi.json").json() == signed.get("/api/openapi.json").json()

    def test_a_query_string_survives(self, signed: TestClient) -> None:
        plain = signed.get("/api/deployments?limit=1")
        versioned = signed.get("/api/v1/deployments?limit=1")

        assert plain.status_code == versioned.status_code == 200
        assert plain.json() == versioned.json()

    def test_an_anonymous_call_is_refused_under_both(
        self, sandbox: Path, runner: FakeRunner
    ) -> None:
        client = build_client(sandbox)

        plain = client.get("/api/apps")
        versioned = client.get("/api/v1/apps")

        assert plain.status_code == versioned.status_code == 401
        assert plain.json() == versioned.json()

    def test_an_unknown_route_is_a_json_error_under_both(self, signed: TestClient) -> None:
        plain = signed.get("/api/nope")
        versioned = signed.get("/api/v1/nope")

        assert plain.status_code == versioned.status_code == 404
        assert versioned.headers["content-type"].startswith("application/json")
        assert versioned.json()["error"] == "not_found"

    def test_a_later_version_is_not_served_yet(self, signed: TestClient) -> None:
        response = signed.get("/api/v2/apps")

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_a_dot_segment_is_still_refused(self, signed: TestClient) -> None:
        """The prefix is removed from the front only: ``..`` never gets to climb out."""
        response = signed.get("/api/v1/../hooks/x")

        assert response.status_code in (400, 404)
        assert response.status_code == signed.get("/api/../hooks/x").status_code

    def test_the_audit_trail_names_the_canonical_path(
        self, sandbox: Path, runner: FakeRunner
    ) -> None:
        """One trail, one spelling: whoever reads it does not search twice."""
        client = build_client(sandbox)
        login(client, get_token_manager().generate_master_token())
        csrf = client.cookies.get("noust_csrf")
        assert csrf

        client.post(
            "/api/v1/auth/logout", headers={CSRF_HEADER_NAME: csrf}
        )  # any mutation the middleware audits

        from tests.test_web_auth import read_audit

        paths = [str(event.get("resource", "")) for event in read_audit(sandbox)]
        assert not any(p.startswith("/api/v1") for p in paths), paths


class TestThePermissionsAreTheSame:
    """A route with two names is one route: its permission is not looked up twice."""

    def test_a_viewer_is_refused_a_change_under_both_addresses(
        self, sandbox: Path, runner: FakeRunner
    ) -> None:
        app = create_app(
            SecurityConfig(
                state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
            )
        )
        client, csrf = signed_in(app, "viewer")
        headers = {CSRF_HEADER_NAME: csrf}

        plain = client.post("/api/apps/shop.example.com/restart", json={}, headers=headers)
        versioned = client.post("/api/v1/apps/shop.example.com/restart", json={}, headers=headers)

        assert plain.status_code == versioned.status_code == 403
        assert plain.json() == versioned.json()
        assert versioned.json()["error"] == "permission_denied"

    def test_a_viewer_reads_under_both_addresses(self, sandbox: Path, runner: FakeRunner) -> None:
        app = create_app(
            SecurityConfig(
                state_dir=sandbox / "state", rate_limit_requests=100_000, auth_policy=AuthPolicy()
            )
        )
        client, _csrf = signed_in(app, "viewer")

        assert client.get("/api/v1/apps").status_code == client.get("/api/apps").status_code == 200

    def test_a_node_path_in_the_new_spelling_needs_what_the_old_spelling_needs(self) -> None:
        assert match_path("POST", "/api/v1/apps/shop.example.com/restart") == match_path(
            "POST", "/api/apps/shop.example.com/restart"
        )
        assert match_path("GET", "/api/v1/apps/types") == ("/api/apps/types", Permission.APPS_READ)

    def test_the_path_after_a_node_is_canonical(self) -> None:
        assert proxied_path("/api/nodes/nas/api/v1/apps/x/restart") == "/api/apps/x/restart"
        assert proxied_path("/api/nodes/nas/api/apps/x/restart") == "/api/apps/x/restart"

    def test_the_legacy_scope_rule_reads_a_versioned_node_path_like_a_plain_one(self) -> None:
        from noust.web.auth import required_scope

        assert required_scope("POST", "/api/nodes/nas/api/v1/apps/x/update") == required_scope(
            "POST", "/api/nodes/nas/api/apps/x/update"
        )
        assert required_scope("POST", "/api/nodes/nas/api/v1/apps/x/delete") == "admin"

    def test_every_public_route_stays_public_through_the_alias(self, signed: TestClient) -> None:
        anonymous = TestClient(signed.app, client=("testclient", 50001))

        assert anonymous.get("/api/v1/auth/session").status_code == 200
        assert anonymous.get("/api/auth/session").status_code == 200
        assert PUBLIC  # the map itself is unchanged: no /api/v1 entries exist

    def test_the_maps_name_no_versioned_route(self) -> None:
        from noust.web.permissions.registry import route_map

        assert not [key for key in route_map() if key[1].startswith(VERSIONED_PREFIX)]


class TestTheNodeProxyStripsTheVersion:
    """A central forwards the canonical path, so an older node answers it too."""

    @pytest.mark.parametrize(
        ("rest", "forwarded"),
        [
            ("apps/x", "/api/apps/x"),
            ("v1/apps/x", "/api/apps/x"),
            ("apps/v1", "/api/apps/v1"),
        ],
    )
    def test_the_node_path(self, rest: str, forwarded: str) -> None:
        pytest.importorskip("httpx")
        from noust.web.api.node_proxy import node_path

        assert node_path("/api/", rest) == forwarded

    def test_a_ws_path_is_not_a_versioned_api_path(self) -> None:
        pytest.importorskip("httpx")
        from noust.web.api.node_proxy import node_path

        assert node_path("/ws/", "v1/logs") == "/ws/v1/logs"

    def test_the_version_alone_is_not_a_path(self) -> None:
        pytest.importorskip("httpx")
        from noust.web.api.node_proxy import node_path

        with pytest.raises(HTTPException):
            node_path("/api/", "v1")


class TestTheVersionHeader:
    def test_it_is_on_every_api_answer(self, signed: TestClient) -> None:
        for response in (
            signed.get("/api/apps"),
            signed.get("/api/v1/apps"),
            signed.get("/api/nope"),
            signed.get("/api/v1/nope"),
            signed.post("/api/apps/types", json={}),  # 405: a refusal before any handler
        ):
            assert response.headers[API_VERSION_HEADER] == API_VERSION, response.request.url

    def test_it_is_on_a_refusal_made_before_routing(
        self, sandbox: Path, runner: FakeRunner
    ) -> None:
        client = build_client(sandbox)

        response = client.get("/api/apps")

        assert response.status_code == 401
        assert response.headers[API_VERSION_HEADER] == "1"

    def test_it_is_on_a_refusal_the_security_layer_makes_itself(
        self, sandbox: Path, runner: FakeRunner
    ) -> None:
        """An address that is not allowed never reaches the router; the version still says so."""
        client = build_client(sandbox, ip_whitelist=["10.0.0.5"])

        for path in ("/api/apps", "/api/v1/apps"):
            response = client.get(path)

            assert response.status_code == 403, path
            assert response.headers[API_VERSION_HEADER] == "1", path

    def test_it_is_not_on_what_is_not_the_api(self, signed: TestClient) -> None:
        for path in ("/health", "/", "/assets/missing.js"):
            assert API_VERSION_HEADER not in signed.get(path).headers, path

    def test_the_header_name_is_the_documented_one(self) -> None:
        assert API_VERSION_HEADER == "X-Noust-API-Version"
        assert API_VERSION == "1"


class TestTheSchemaInfo:
    def test_the_served_schema_carries_the_api_version_and_the_product_version(
        self, signed: TestClient
    ) -> None:
        info = signed.get("/api/openapi.json").json()["info"]

        assert info["version"] == API_VERSION
        assert info["x-noust-version"] == __version__
        assert info["x-noust-api-prefix"] == VERSIONED_PREFIX


# ---------------------------------------------------------------------------
# Deprecation
# ---------------------------------------------------------------------------


def deprecation_app(**policy: Any) -> FastAPI:
    """
    A small application with the version middleware and one deprecated route.

    Args:
        **policy: Arguments for :func:`noust.web.versioning.deprecated`.

    Returns:
        The application; every route answers without a credential.
    """
    app = FastAPI(version=API_VERSION)
    router = APIRouter()

    @router.get("/old", response_model=dict[str, str], **deprecated(**policy))
    def old() -> dict[str, str]:
        return {"ok": "yes"}

    @router.get("/old-error", response_model=dict[str, str], **deprecated(**policy))
    def old_error() -> dict[str, str]:
        raise HTTPException(status_code=409, detail="busy")

    @router.get("/old-direct", **deprecated(**policy))
    def old_direct() -> JSONResponse:
        return JSONResponse({"ok": "direct"})

    @router.get("/current", response_model=dict[str, str])
    def current() -> dict[str, str]:
        return {"ok": "yes"}

    app.include_router(router, prefix="/api")
    app.add_middleware(ApiVersionMiddleware)
    return app


POLICY = {"since": "2026-10-01", "sunset": "2027-06-01", "successor": "/api/new"}


class TestDeprecationHeaders:
    def test_a_deprecated_route_says_since_when_until_when_and_what_replaces_it(self) -> None:
        client = TestClient(deprecation_app(**POLICY))

        response = client.get("/api/old")

        assert response.status_code == 200
        # RFC 9745: a structured-field date, seconds since the epoch, with an @.
        assert response.headers["Deprecation"] == "@1790812800"
        assert datetime.fromtimestamp(1790812800, tz=timezone.utc).date() == date(2026, 10, 1)
        # RFC 8594: an HTTP-date.
        assert response.headers["Sunset"] == "Tue, 01 Jun 2027 00:00:00 GMT"
        assert parsedate_to_datetime(response.headers["Sunset"]).date() == date(2027, 6, 1)
        assert response.headers["Link"] == '</api/v1/new>; rel="successor-version"'
        assert response.headers[API_VERSION_HEADER] == "1"

    def test_the_headers_are_the_same_under_the_versioned_address(self) -> None:
        client = TestClient(deprecation_app(**POLICY))

        plain = client.get("/api/old")
        versioned = client.get("/api/v1/old")

        for name in ("Deprecation", "Sunset", "Link"):
            assert versioned.headers[name] == plain.headers[name]

    def test_a_deprecated_route_that_fails_still_says_so(self) -> None:
        response = TestClient(deprecation_app(**POLICY)).get("/api/old-error")

        assert response.status_code == 409
        assert "Deprecation" in response.headers
        assert "Sunset" in response.headers

    def test_a_deprecated_route_that_answers_with_its_own_response_still_says_so(self) -> None:
        response = TestClient(deprecation_app(**POLICY)).get("/api/old-direct")

        assert response.json() == {"ok": "direct"}
        assert response.headers["Deprecation"] == "@1790812800"

    def test_a_current_route_says_nothing(self) -> None:
        response = TestClient(deprecation_app(**POLICY)).get("/api/current")

        for name in ("Deprecation", "Sunset", "Link"):
            assert name not in response.headers

    def test_without_a_date_there_is_no_sunset_and_without_a_successor_no_link(self) -> None:
        response = TestClient(deprecation_app(since="2026-10-01")).get("/api/old")

        assert "Deprecation" in response.headers
        assert "Sunset" not in response.headers
        assert "Link" not in response.headers

    def test_the_schema_marks_the_operation(self) -> None:
        schema = deprecation_app(**POLICY).openapi()

        operation = schema["paths"]["/api/old"]["get"]
        assert operation["deprecated"] is True
        assert operation["x-noust-deprecation"] == {
            "since": "2026-10-01",
            "sunset": "2027-06-01",
            "successor": "/api/new",
        }
        assert "deprecated" not in schema["paths"]["/api/current"]["get"]
        assert "x-noust-deprecation" not in schema["paths"]["/api/current"]["get"]


class TestTheDeprecationPolicyIsEnforcedWhereItIsDeclared:
    def test_the_notice_is_at_least_the_published_minimum(self) -> None:
        assert MINIMUM_NOTICE_DAYS >= 180

    def test_a_sunset_too_close_is_refused(self) -> None:
        with pytest.raises(ValueError, match=str(MINIMUM_NOTICE_DAYS)):
            deprecated(since="2026-10-01", sunset="2026-12-31")

    def test_exactly_the_minimum_is_accepted(self) -> None:
        first = date(2026, 10, 1)
        last = date.fromordinal(first.toordinal() + MINIMUM_NOTICE_DAYS)

        assert deprecated(since=first.isoformat(), sunset=last.isoformat())["deprecated"] is True

    @pytest.mark.parametrize("bad", ["", "tomorrow", "2026-13-01", "01/10/2026", "2026-10"])
    def test_a_date_is_a_calendar_day(self, bad: str) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            deprecated(since=bad)
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            deprecated(since="2026-10-01", sunset=bad)

    @pytest.mark.parametrize("bad", ["", "/events", "api/new", "/api/v1/new", "https://x/api/new"])
    def test_the_successor_is_a_canonical_api_path(self, bad: str) -> None:
        with pytest.raises(ValueError, match="successor"):
            deprecated(since="2026-10-01", successor=bad)

    def test_the_application_has_no_deprecated_operation_that_is_not_announced(
        self, signed: TestClient
    ) -> None:
        """
        ``deprecated: true`` set by hand would mark the schema without telling
        a client that is not reading it; the helper is the one way to do both.
        """
        schema = signed.get("/api/openapi.json").json()

        unannounced = [
            f"{method.upper()} {path}"
            for path, operations in schema["paths"].items()
            for method, operation in operations.items()
            if isinstance(operation, dict)
            and operation.get("deprecated")
            and "x-noust-deprecation" not in operation
        ]
        assert unannounced == []
