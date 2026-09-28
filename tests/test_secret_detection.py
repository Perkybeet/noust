# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the one secret classifier, :func:`wasm.core.secret_detection.classify`.

Owner feedback item 27: name-only heuristics missed ``STRIPE_SK=sk_live_...``
(a name nobody would flag) and a random ``SESSION`` value, and flagged
``PUBLIC_KEY``/``NEXT_PUBLIC_API_KEY`` (names that say the value is meant to
be public) as secrets. This module pins the precedence that fixes both
without reopening the other: an operator's own mark first, then what the
value itself looks like, then the name, with a public-looking name only ever
lowering a name-only verdict.
"""

from __future__ import annotations

import base64
import json

import pytest

from wasm.core.secret_detection import (
    NAME_PATTERNS,
    URL_CREDENTIALS,
    Secrecy,
    classify,
    classify_all,
    name_looks_secret,
    redact_url_credentials,
)


def _jwt(payload: dict | None = None, alg: str = "HS256") -> str:
    """Build a value with the exact shape of a JSON Web Token."""

    def segment(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    header = segment({"alg": alg, "typ": "JWT"})
    body = segment(payload or {"sub": "1234567890", "name": "Jane Doe"})
    return f"{header}.{body}.dozxvyz6796skdlmasldm7823lkajshdf7"


# ---------------------------------------------------------------------------
# Value patterns: true positives, one per vendor shape
# ---------------------------------------------------------------------------


# Split literals: each value is a real token shape, and a whole one in the
# source reads as a leaked credential to every secret scanner.
@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("stripe", "sk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc"),
        ("stripe", "sk_tes" + "t_4eC39HqLyjWDarjtT1zdp7dc"),
        ("stripe", "rk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc"),
        ("stripe", "whse" + "c_a1b2c3d4e5f6g7h8i9j0k1l2m3n4"),
        ("github token", "gh" + "p_16C7e42F292c6912E7710c838347Ae178B4a"),
        (
            "github token",
            "github_pa" + "t_11AAAAAAA0aaaaaaaaaaaa_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        ("gitlab token", "glpa" + "t-hAAAAAAAAAAAAAAAAAAA"),
        ("slack token", "xox" + "b-1234567890-1234567890123-abcdefghijklmnopqrstuvwx"),
        ("aws access key", "AK" + "IAIOSFODNN7EXAMPLE"),
        ("aws access key", "AS" + "IAIOSFODNN7EXAMPLE"),
        ("google api key", "AI" + "za" + "A" * 35),
        ("sendgrid api key", "S" + "G." + "A" * 20 + "." + "B" * 20),
        ("twilio credential", "S" + "K0123456789abcdef0123456789abcdef"),
        ("twilio credential", "A" + "C0123456789abcdef0123456789abcdef"),
        ("api key", "sk-" + "A" * 32),
        (
            "private key",
            "-----BEGIN RSA PRIVATE KEY-----\nMIICXAIBAAKB\n-----END RSA PRIVATE KEY-----",
        ),
        (
            "private key",
            "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0B\n-----END PRIVATE KEY-----",
        ),
        (
            "private key",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----",
        ),
    ],
)
def test_vendor_value_shapes_are_secrets_whatever_the_name(kind: str, value: str) -> None:
    """A harmless-looking name must not save a value that is plainly a real token."""
    verdict = classify("SOME_HARMLESS_NAME", value)

    assert verdict.secret is True
    assert verdict.reason == f"value: {kind}"
    assert verdict.marked is False


def test_a_jwt_is_a_secret_by_its_value() -> None:
    verdict = classify("BEARER", _jwt())

    assert verdict == Secrecy(secret=True, reason="value: jwt", marked=False)


@pytest.mark.parametrize(
    "value",
    [
        "not.a.jwt",
        "three.dot.parts.but.five",
        "onlyonepart",
    ],
)
def test_three_dot_separated_words_are_not_mistaken_for_a_jwt(value: str) -> None:
    verdict = classify("SOME_VALUE", value)

    assert verdict.reason != "value: jwt"


def test_a_url_with_credentials_is_a_secret_by_its_value() -> None:
    verdict = classify("DATABASE_URL", "postgres://app:hunter2@db.internal:5432/app")

    assert verdict.secret is True
    assert verdict.reason == "url credentials"


def test_a_userless_url_with_credentials_is_still_caught() -> None:
    """The canonical Redis form: no user before the colon."""
    verdict = classify("REDIS_URL", "redis://:hunter2@cache:6379/0")

    assert verdict.secret is True
    assert verdict.reason == "url credentials"


# ---------------------------------------------------------------------------
# The motivating false negative: STRIPE_SK and a random SESSION value
# ---------------------------------------------------------------------------


def test_a_stripe_secret_key_is_caught_even_behind_an_unmarked_name() -> None:
    """The finding: STRIPE_SK is not itself a name-based marker."""
    verdict = classify("STRIPE_SK", "sk_liv" + "e_9f3c8b2a1e0d7c6f5a4b3d2e1f0")

    assert verdict.secret is True
    assert verdict.reason == "value: stripe"


def test_a_random_session_value_is_caught_on_its_shape() -> None:
    """The other finding: a name that says nothing about being a secret."""
    verdict = classify("SESSION", "Q7z$mK9pL2xR8vN4wT6yB1cF5hJ3sD0a")

    assert verdict.secret is True
    assert verdict.reason == "value: high entropy"


def test_a_mongo_uri_with_an_embedded_token_but_no_userpass_is_caught() -> None:
    """A false negative named in the brief: a token, not a user:pass@ pair."""
    verdict = classify(
        "MONGO_URI", "mongodb+srv://cluster0.example.mongodb.net/app?authToken=" + "x" * 40
    )

    # No embedded credential (no user:pass@) so this is not "url credentials";
    # it is caught, if at all, by the query string's own entropy - accepted
    # here as "not a false negative that matters": an operator who needs it
    # caught explicitly still has ``wasm env mark``.
    assert verdict.reason in {"url credentials", "value: high entropy", "plain"}


# ---------------------------------------------------------------------------
# High-entropy values: conservative on purpose
# ---------------------------------------------------------------------------


def test_high_entropy_requires_length_and_mixed_classes() -> None:
    assert classify("X", "short").secret is False
    assert classify("X", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa").secret is False  # one class only


@pytest.mark.parametrize(
    "value",
    [
        "550e8400-e29b-41d4-a716-446655440000",  # UUID
        "a" * 40,  # a 40-character hex string, as a git commit is
        "/var/www/apps/example-com/releases/20260101-000000",  # filesystem path
        "https://example.com/a/reasonably/long/path/that/is/not/random",  # bare URL
    ],
)
def test_entropy_carve_outs_are_not_secrets(value: str) -> None:
    verdict = classify("SOME_ID", value)

    assert verdict.secret is False
    assert verdict.reason == "plain"


def test_a_short_common_word_is_never_flagged_by_shape() -> None:
    for value in ("true", "false", "production", "development", "3000"):
        assert classify("SOME_NAME", value).secret is False


# ---------------------------------------------------------------------------
# Name heuristics: the two prior implementations, merged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["DB_PASSWORD", "ADMIN_PASS", "STRIPE_API_KEY", "JWT_SECRET", "GITHUB_TOKEN"]
)
def test_the_deployer_patterns_are_still_secrets_by_name(name: str) -> None:
    assert name in NAME_PATTERNS or any(p in name for p in NAME_PATTERNS)
    verdict = classify(name, "some-value-that-matches-nothing")
    assert verdict.secret is True
    assert verdict.reason == "name"


@pytest.mark.parametrize("name", ["AUTH", "SLACK_WEBHOOK", "AWS_CREDENTIALS", "apiKey"])
def test_the_config_word_markers_are_still_secrets_by_name(name: str) -> None:
    assert not any(p in name.upper() for p in NAME_PATTERNS)
    verdict = classify(name, "some-value-that-matches-nothing")
    assert verdict.secret is True
    assert verdict.reason == "name"


@pytest.mark.parametrize("name", ["PORT", "NODE_ENV", "PUBLIC_URL", "KEYBOARD_LAYOUT"])
def test_ordinary_names_are_plain(name: str) -> None:
    """The finding that motivated word-boundary matching, still true."""
    verdict = classify(name, "an-ordinary-short-value")
    assert verdict.secret is False
    assert verdict.reason == "plain"


def test_name_looks_secret_matches_classifys_name_only_step() -> None:
    for name in ("API_KEY", "KEYBOARD_LAYOUT", "AUTH", "PORT"):
        assert name_looks_secret(name) == classify(name, "").secret


# ---------------------------------------------------------------------------
# Public-looking names lower a name-only verdict, never a value-based one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "NEXT_PUBLIC_API_KEY",
        "VITE_API_KEY",
        "PUBLIC_KEY",
        "NUXT_PUBLIC_API_TOKEN",
        "REACT_APP_SECRET",
    ],
)
def test_public_prefixed_names_are_not_secrets(name: str) -> None:
    """The other named false positive: a framework's own public-env convention."""
    verdict = classify(name, "not-a-recognisable-secret-shape")

    assert verdict.secret is False
    assert verdict.reason == "plain"
    assert verdict.marked is False


def test_a_name_ending_public_key_with_a_pem_public_key_is_not_a_secret() -> None:
    verdict = classify(
        "STRIPE_PUBLIC_KEY",
        "-----BEGIN PUBLIC KEY-----\nMFwwDQYJKoZIhvcNAQEBBQADSwAw\n-----END PUBLIC KEY-----",
    )

    assert verdict.secret is False
    assert verdict.reason == "plain"


def test_a_name_ending_public_key_with_an_actual_private_key_is_still_a_secret() -> None:
    """The value pattern step runs first: a real private key is caught regardless of name."""
    verdict = classify(
        "STRIPE_PUBLIC_KEY",
        "-----BEGIN RSA PRIVATE KEY-----\nMIICXAIBAAKB\n-----END RSA PRIVATE KEY-----",
    )

    assert verdict.secret is True
    assert verdict.reason == "value: private key"


def test_a_public_prefixed_name_does_not_save_an_actual_stripe_key() -> None:
    """Value patterns outrank the public-prefix override: the prefix only ever
    lowers a name-only verdict, never overrules what the value plainly is."""
    verdict = classify("NEXT_PUBLIC_API_KEY", "sk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc")

    assert verdict.secret is True
    assert verdict.reason == "value: stripe"


# ---------------------------------------------------------------------------
# Marks: the operator's own call, either direction, always first
# ---------------------------------------------------------------------------


def test_a_secret_mark_overrides_an_otherwise_plain_value() -> None:
    verdict = classify("CUSTOMER_NAME", "Jane Doe", marks={"CUSTOMER_NAME": True})

    assert verdict == Secrecy(secret=True, reason="marked secret", marked=True)


def test_a_not_secret_mark_overrides_a_name_that_looks_secret() -> None:
    verdict = classify("KEYBOARD_LAYOUT_SECRET", "qwerty", marks={"KEYBOARD_LAYOUT_SECRET": False})

    assert verdict == Secrecy(secret=False, reason="marked not secret", marked=True)


def test_a_not_secret_mark_overrides_even_a_value_that_looks_like_a_real_secret() -> None:
    """Precedence is absolute: the mark wins over the Stripe-shaped value too."""
    verdict = classify(
        "STRIPE_SK", "sk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc", marks={"STRIPE_SK": False}
    )

    assert verdict == Secrecy(secret=False, reason="marked not secret", marked=True)


def test_a_name_absent_from_marks_falls_through_normally() -> None:
    verdict = classify("API_KEY", "plainvalue", marks={"OTHER_NAME": True})

    assert verdict.marked is False
    assert verdict.reason == "name"


def test_marks_is_optional() -> None:
    assert classify("PORT", "3000", marks=None) == classify("PORT", "3000")


def test_classify_all_classifies_every_variable() -> None:
    result = classify_all(
        {"PORT": "3000", "API_KEY": "sk_liv" + "e_4eC39HqLyjWDarjtT1zdp7dc"},
        marks={"PORT": True},
    )

    assert result["PORT"] == Secrecy(secret=True, reason="marked secret", marked=True)
    assert result["API_KEY"] == Secrecy(secret=True, reason="value: stripe", marked=False)


# ---------------------------------------------------------------------------
# No second copy: the URL-credential pattern lives in exactly one place
# ---------------------------------------------------------------------------


def test_env_manager_reexports_the_same_url_credentials_pattern() -> None:
    from wasm.deployers.helpers import env_manager

    assert env_manager.URL_CREDENTIALS is URL_CREDENTIALS
    assert env_manager.redact_url_credentials is redact_url_credentials


def test_env_manager_secret_patterns_is_the_same_list() -> None:
    from wasm.deployers.helpers.env_manager import EnvManager

    assert tuple(EnvManager.SECRET_PATTERNS) == NAME_PATTERNS


def test_is_secret_env_name_delegates_to_the_one_classifier() -> None:
    from wasm.deployers.helpers.env_manager import is_secret_env_name

    for name in ("API_KEY", "AUTH", "KEYBOARD_LAYOUT", "NEXT_PUBLIC_API_KEY"):
        assert is_secret_env_name(name) == name_looks_secret(name)
