# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every response of the console is hardened, not only the ones that have a route
(checklist L-34, L-35).

A header added to the handlers and not to the refusals made before routing is a
header an attacker finds by asking for something that does not exist. The
middleware sets them in one place; these tests ask for every kind of answer and
for the transport-dependent one (HSTS), and they pin the policy's text so that
loosening it is a change someone reviews.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noust.web.server import (
    CONTENT_SECURITY_POLICY,
    HARDENING_HEADERS,
    HSTS_VALUE,
    PERMISSIONS_POLICY,
    create_app,
)
from tests.test_web_auth import make_config


def client_for(sandbox: Path, peer: str = "203.0.113.9", **config: object) -> TestClient:
    return TestClient(create_app(make_config(sandbox, **config)), client=(peer, 50000))


ALWAYS = [*HARDENING_HEADERS, "Cache-Control"]


def assert_hardened(response: object, what: str) -> None:
    headers = response.headers  # type: ignore[attr-defined]
    missing = [name for name in ALWAYS if name not in headers]
    assert not missing, f"{what} lacks {missing}"
    for name, value in HARDENING_HEADERS.items():
        assert headers[name] == value, f"{what}: {name}"
    assert "server" not in headers, f"{what} names the software that answers"


class TestEveryKindOfAnswer:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/health"),
            ("GET", "/"),
            ("GET", "/some/console/address"),
            ("GET", "/api/nope"),
            ("GET", "/api/apps"),
            ("POST", "/api/apps"),
            ("PUT", "/api/apps"),
            ("DELETE", "/api/apps/example.com"),
            ("GET", "/assets/missing.js"),
            ("GET", "/events"),
            ("POST", "/hooks/deploy/example.com"),
            ("POST", "/api/auth/login"),
            ("GET", "/api/v1/apps"),
            ("GET", "/api/openapi.json"),
        ],
    )
    def test_routed_and_unrouted_alike(self, sandbox: Path, method: str, path: str) -> None:
        response = client_for(sandbox).request(method, path, json={} if method != "GET" else None)

        assert_hardened(response, f"{method} {path} ({response.status_code})")

    def test_a_body_that_is_too_large_is_refused_hardened(self, sandbox: Path) -> None:
        response = client_for(sandbox).post("/api/apps", content=b"x" * (2 * 1024 * 1024))

        assert response.status_code == 413
        assert_hardened(response, "413")

    def test_a_dot_segment_is_refused_hardened(self, sandbox: Path) -> None:
        response = client_for(sandbox).get("/%2e%2e/etc/passwd")

        assert response.status_code == 400
        assert_hardened(response, "400")

    def test_an_address_that_is_not_allowed_is_refused_hardened(self, sandbox: Path) -> None:
        response = client_for(sandbox, ip_whitelist=["10.9.9.9"]).get("/health")

        assert response.status_code == 403
        assert_hardened(response, "403 (address)")

    def test_a_rate_limited_client_is_refused_hardened_and_told_when_to_return(
        self, sandbox: Path
    ) -> None:
        client = client_for(sandbox, rate_limit_requests=1)
        client.get("/api/apps")

        response = client.get("/api/apps")

        assert response.status_code == 429
        assert_hardened(response, "429")
        assert int(response.headers["Retry-After"]) >= 1
        assert response.headers["X-RateLimit-Remaining"] == "0"


class TestTheTransport:
    def test_cleartext_has_no_hsts(self, sandbox: Path) -> None:
        assert "Strict-Transport-Security" not in client_for(sandbox).get("/health").headers

    def test_tls_has_hsts(self, sandbox: Path) -> None:
        client = TestClient(
            create_app(make_config(sandbox)),
            base_url="https://testserver",
            client=("203.0.113.9", 50000),
        )

        assert client.get("/health").headers["Strict-Transport-Security"] == HSTS_VALUE

    def test_a_declared_proxy_can_say_the_request_was_tls(self, sandbox: Path) -> None:
        client = client_for(sandbox, peer="127.0.0.1", trusted_proxies=["127.0.0.1"])

        response = client.get("/health", headers={"X-Forwarded-Proto": "https"})

        assert response.headers["Strict-Transport-Security"] == HSTS_VALUE

    def test_anyone_else_saying_so_changes_nothing(self, sandbox: Path) -> None:
        response = client_for(sandbox).get("/health", headers={"X-Forwarded-Proto": "https"})

        assert "Strict-Transport-Security" not in response.headers


class TestTheCaching:
    def test_a_built_asset_is_immutable_and_everything_else_is_not_stored(
        self, sandbox: Path
    ) -> None:
        from noust.web.server import ASSETS_DIR

        client = client_for(sandbox)
        assets = sorted(ASSETS_DIR.glob("*.js"))
        if assets:
            served = client.get(f"/assets/{assets[0].name}")
            assert served.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        for path in ("/", "/health", "/api/apps", "/assets/missing.js"):
            assert client.get(path).headers["Cache-Control"] == "no-store", path


class TestThePolicyItself:
    """Pinned: loosening any of these is a change that is reviewed on purpose."""

    def test_the_content_security_policy_allows_no_inline_code_and_no_framing(self) -> None:
        directives = {
            part.split(" ", 1)[0]: part.split(" ", 1)[1] if " " in part else ""
            for part in CONTENT_SECURITY_POLICY.split("; ")
        }

        assert directives["default-src"] == "'self'"
        assert directives["script-src"] == "'self'"
        assert directives["style-src"] == "'self'"
        assert directives["frame-ancestors"] == "'none'"
        assert directives["object-src"] == "'none'"
        assert directives["base-uri"] == "'none'"
        assert directives["require-trusted-types-for"] == "'script'"
        assert directives["connect-src"] == "'self'"
        assert "unsafe-inline" not in CONTENT_SECURITY_POLICY
        assert "unsafe-eval" not in CONTENT_SECURITY_POLICY
        # The one origin a form may post to besides this one: GitHub's App manifest flow.
        assert directives["form-action"] == "'self' https://github.com"

    def test_the_features_the_console_never_uses_are_off(self) -> None:
        disabled = {entry.split("=")[0] for entry in PERMISSIONS_POLICY.split(", ")}

        assert {"camera", "microphone", "geolocation", "payment", "usb", "serial"} <= disabled
        # What the console does use is not switched off.
        assert "clipboard-write" not in disabled and "fullscreen" not in disabled

    def test_isolation_between_origins(self) -> None:
        assert HARDENING_HEADERS["X-Frame-Options"] == "DENY"
        assert HARDENING_HEADERS["Cross-Origin-Opener-Policy"] == "same-origin"
        assert HARDENING_HEADERS["Cross-Origin-Resource-Policy"] == "same-origin"
        assert HARDENING_HEADERS["Referrer-Policy"] == "no-referrer"
        assert HARDENING_HEADERS["X-Content-Type-Options"] == "nosniff"
