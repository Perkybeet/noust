# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Hiding what looks like a secret in free text (checklist N-33).

``redact_secret_shapes`` is the complement of ``Scrubber``: it does not know the secret, it
knows what one looks like. It is the last pass over a support report, which an operator
pastes into a public issue.
"""

from __future__ import annotations

import pytest

from noust.core.config import REDACTED
from noust.core.secret_detection import redact_secret_shapes

PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


def test_a_private_key_block_is_hidden_whole() -> None:
    text = f"before\n{PEM}\nafter"

    assert redact_secret_shapes(text) == f"before\n{REDACTED}\nafter"


def test_a_private_key_cut_off_by_a_line_limit_is_hidden_to_the_end() -> None:
    text = "key follows\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890"

    result = redact_secret_shapes(text)

    assert "MIIEow" not in result
    assert result.startswith("key follows")


@pytest.mark.parametrize(
    "secret",
    [
        "noust_tok_Zk3j9sdflkj234sdf",
        "wasm_tok_Zk3j9sdflkj234sdf",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "glpat-abcdefghij0123456789",
        "xoxb-1234567890-abcdefghij",
        "AKIAABCDEFGHIJKLMNOP",
        "sk_live_abcdefghij0123456789",
    ],
)
def test_the_shape_of_a_token_is_enough(secret: str) -> None:
    result = redact_secret_shapes(f"deploy used {secret} to push")

    assert secret not in result
    assert result == f"deploy used {REDACTED} to push"


def test_an_authorization_header_loses_its_credential_and_keeps_its_scheme() -> None:
    result = redact_secret_shapes("GET /api HTTP/1.1\nAuthorization: Bearer abcdef0123456789xyz")

    assert "abcdef0123456789xyz" not in result
    assert f"Bearer {REDACTED}" in result


def test_a_json_web_token_is_hidden() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl"

    assert jwt not in redact_secret_shapes(f"session {jwt} expired")


def test_the_password_of_a_connection_string_is_hidden_and_the_rest_kept() -> None:
    result = redact_secret_shapes(
        "connect postgres://app:hunter2hunter2@db.internal:5432/shop failed"
    )

    assert "hunter2" not in result
    assert "app:" in result and "@db.internal:5432/shop" in result


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("DB_PASSWORD=correct-horse-battery", "DB_PASSWORD="),
        ("Environment=STRIPE_SECRET_KEY=abc123def456", "STRIPE_SECRET_KEY="),
        ('{"api_key": "abcd1234efgh"}', '"api_key": '),
        ("api_token: 0123456789abcdef", "api_token: "),
        ("--password 'p4ss' --user x", "--password 'p4ss'"),
    ],
)
def test_the_value_of_a_secret_named_assignment_is_hidden(text: str, kept: str) -> None:
    result = redact_secret_shapes(text)

    for leaked in ("correct-horse-battery", "abc123def456", "abcd1234efgh", "0123456789abcdef"):
        assert leaked not in result
    if "--password" not in text:
        assert kept in result
        assert REDACTED in result


def test_an_ordinary_line_is_returned_unchanged() -> None:
    line = "2026-10-13T10:00:01+0000 web-01 noust-web[412]: GET /api/apps 200 12ms"

    assert redact_secret_shapes(line) == line


def test_a_placeholder_is_not_hidden_twice() -> None:
    text = f"DB_PASSWORD={REDACTED}"

    assert redact_secret_shapes(text) == text


def test_the_address_of_a_url_without_credentials_is_not_mistaken_for_an_assignment() -> None:
    text = "listening on http://127.0.0.1:8080/api?page=2&sort=name"

    assert redact_secret_shapes(text) == text
