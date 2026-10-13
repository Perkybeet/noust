# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Secrets are masked in logs by what they look like, not only by what Noust knows.

Checklist G-12: one redactor for build logs, deployment logs, job logs and the
runtime logs the console streams, tested with secrets of known shape. The
value-based half (a secret Noust was told about) is :class:`~noust.core.redact.Scrubber`'s
own; this is the half for the token a program printed that nobody listed.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.redact import Scrubber
from noust.core.secret_detection import mask_secret_shapes
from noust.core.store import App, NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

KNOWN: dict[str, str] = {
    "github token": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "github fine-grained": "github_pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789ABCDEFGHIJ",
    "gitlab token": "glpat-abcdefghij0123456789",
    "slack token": "xoxb-1234567890-abcdefghijkl",
    "aws access key": "AKIAIOSFODNN7EXAMPLE",
    "stripe key": "sk_live_4eC39HqLyjWDarjtT1zdp7dc",
    "stripe webhook": "whsec_abcdefghijklmnopqrstuvwxyz",
    "google api key": "AIzaSyA-1234567890abcdefghijklmnopqrstu",
    "sendgrid key": "SG.abcdefghijklmnop.qrstuvwxyz0123456789",
    "npm token": "npm_" + "a1B2c3D4" * 4 + "e5F6",
    "telegram bot token": "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
    "noust token": "noust_tok_abcdefghijklmnop0123456789",
    "legacy token": "wasm_tok_abcdefghijklmnop0123456789",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
}


@pytest.mark.parametrize(("kind", "secret"), list(KNOWN.items()))
def test_a_vendors_token_is_masked_wherever_it_appears(kind: str, secret: str) -> None:
    for line in (
        f"cloning with {secret} done",
        f"{secret}",
        f"curl -u {secret} https://x.example.test",
        f"export WHATEVER={secret}",
    ):
        masked = mask_secret_shapes(line)

        assert secret not in masked, f"{kind} survived in: {line}"
        assert "***" in masked


def test_a_password_in_a_url_is_masked_and_the_rest_of_the_line_is_not() -> None:
    masked = mask_secret_shapes("connecting to postgres://app:hunter22@db.internal:5432/shop")

    assert masked == "connecting to postgres://app:***@db.internal:5432/shop"


def test_a_redis_url_with_only_a_password_is_masked() -> None:
    assert mask_secret_shapes("redis://:s3cr3tvalue@cache:6379/0") == "redis://:***@cache:6379/0"


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        ("Authorization: Bearer abcdefghijklmnop12345", "Authorization: Bearer ***"),
        ("authorization: basic dXNlcjpwYXNzd29yZA==", "authorization: basic ***"),
        ("curl -H 'Authorization: Bearer abcdefghijklmnop12345' x", "Bearer ***"),
        ("sent Bearer abcdefghijklmnopqrstuvwxyz to the api", "Bearer *** to the api"),
        ("DB_PASSWORD=hunter2222", "DB_PASSWORD=***"),
        ("password: 'hunter2222'", "password: '***'"),
        ('{"password": "hunter2222", "user": "bob"}', '{"password": "***", "user": "bob"}'),
        ("X-Api-Key: 0123456789abcdef", "X-Api-Key: ***"),
        ("client_secret=abcdefghijklmnop", "client_secret=***"),
        (
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "AWS_SECRET_ACCESS_KEY=***",
        ),
        ("access_token=abcdef123456&state=ok", "access_token=***&state=ok"),
    ],
)
def test_the_value_of_a_credential_assignment_is_masked_and_its_name_kept(
    line: str, kept: str
) -> None:
    masked = mask_secret_shapes(line)

    assert kept in masked
    for leaked in ("hunter2222", "abcdefghijklmnop12345", "dXNlcjpwYXNzd29yZA", "0123456789abcdef"):
        assert leaked not in masked


def test_a_private_key_block_is_masked_whole() -> None:
    text = (
        "before\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmU\n"
        "QyNTUxOQAAACD\n-----END OPENSSH PRIVATE KEY-----\nafter"
    )

    assert mask_secret_shapes(text) == "before\n***\nafter"


def test_a_key_header_alone_is_masked() -> None:
    assert mask_secret_shapes("-----BEGIN RSA PRIVATE KEY-----") == "***"


@pytest.mark.parametrize(
    "harmless",
    [
        "commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 on main",
        "serving /var/www/apps/shop-example-com/current/public",
        "request id 550e8400-e29b-41d4-a716-446655440000 took 12ms",
        "the tokenizer=spacy_model_big loaded; bypass=allowed-for-all",
        "password: true",
        "token_expiration_hours=12",
        "GET /api/auth/tokens?limit=10 200",
        "-----BEGIN PUBLIC KEY-----",
        "-----BEGIN CERTIFICATE-----",
        "listening on http://localhost:3000",
        "",
    ],
)
def test_ordinary_text_is_left_exactly_as_it_was(harmless: str) -> None:
    assert mask_secret_shapes(harmless) == harmless


def test_masking_twice_changes_nothing() -> None:
    text = "DB_PASSWORD=hunter2222 and postgres://a:b1b1b1b1@h/db with ghp_" + "a" * 36

    once = mask_secret_shapes(text)

    assert mask_secret_shapes(once) == once


def test_a_hostile_line_does_not_make_it_slow() -> None:
    lines = [
        "password" * 50_000,
        "token=" + "a" * 500_000,
        "-----BEGIN RSA PRIVATE KEY-----" + "A" * 500_000,
        "Bearer " + "x" * 500_000,
        ("a:" * 100_000) + "@",
        "://" * 100_000,
    ]

    started = time.monotonic()
    for line in lines:
        mask_secret_shapes(line)

    assert time.monotonic() - started < 5.0


class TestTheScrubberUsesIt:
    def test_it_masks_the_shapes_with_no_value_ever_listed(self) -> None:
        scrubber = Scrubber()

        assert scrubber.scrub("pulling with ghp_" + "a" * 36) == "pulling with ***"
        assert not scrubber  # nothing listed, yet it masked

    def test_a_listed_value_and_a_shape_are_both_masked(self) -> None:
        scrubber = Scrubber(["my-internal-key-1234"])

        masked = scrubber.scrub("my-internal-key-1234 and AKIAIOSFODNN7EXAMPLE")

        assert masked == "*** and ***"

    def test_nothing_to_hide_returns_the_text_untouched(self) -> None:
        text = "Compiled successfully in 4.2s"

        assert Scrubber().scrub(text) is text or Scrubber().scrub(text) == text


# -- the runtime logs ----------------------------------------------------------------

DOMAIN = "shop.example.com"
TOKEN = "ghp_" + "b" * 36


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def client(tmp_path: Path, store: NoustStore, runner: Any) -> TestClient:
    app: FastAPI = create_app(
        SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000)
    )
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


class TestTheJournalsLeaveMasked:
    def test_an_applications_logs(
        self, client: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.create_app(
            App(
                domain=DOMAIN,
                app_type="nodejs",
                port=3000,
                env_vars={"STRIPE_KEY": "sk_value_unlisted_by_shape_xyz"},
            )
        )

        class Service:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def logs(self, name: str, lines: int = 100) -> str:
                return (
                    f"server started\nclone failed for {TOKEN}\n"
                    "using STRIPE_KEY sk_value_unlisted_by_shape_xyz now\n"
                )

        monkeypatch.setattr("noust.web.api.apps.ServiceManager", Service)

        body = client.get(f"/api/apps/{DOMAIN}/logs").json()

        assert "server started" in body["logs"]
        assert TOKEN not in body["logs"]
        assert "sk_value_unlisted_by_shape_xyz" not in body["logs"]
        assert body["logs"].count("***") == 2

    def test_a_services_logs(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        class Service:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def logs(self, name: str, lines: int = 100) -> str:
                return f"Authorization: Bearer {'z' * 24}\nok"

        monkeypatch.setattr("noust.web.api.services.ServiceManager", Service)
        monkeypatch.setattr(
            "noust.web.api.services._resolve_unit", lambda name: ("nginx.service", Path("/x"))
        )

        body = client.get("/api/services/nginx/logs").json()

        assert "z" * 24 not in body["logs"]
        assert "Authorization: Bearer ***" in body["logs"]
        assert body["logs"].endswith("ok")
