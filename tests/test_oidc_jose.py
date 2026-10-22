# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The token verifier: signatures for the algorithms providers use, and every way
a token is refused.

Keys are generated here with ``cryptography``; each test bends one thing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("cryptography")

from noust.core.accounts.oidc_jose import (
    SUPPORTED_ALGORITHMS,
    JwtError,
    public_key_from_jwk,
    split_token,
    verify_jws,
)
from tests.oidc_support import SigningKey, b64, compact

CLAIMS = {"iss": "https://idp.example.test", "sub": "u1", "aud": "console"}


def token(key: SigningKey, **header: Any) -> str:
    return compact({"alg": key.algorithm, "kid": key.key_id, **header}, CLAIMS, key)


@pytest.mark.parametrize("algorithm", ["RS256", "ES256", "ES384", "PS256", "RS512"])
def test_a_token_signed_by_a_published_key_verifies(algorithm: str) -> None:
    key = SigningKey(algorithm)
    verified = verify_jws(token(key), [key.jwk()])

    assert verified.claims == CLAIMS
    assert verified.algorithm == algorithm
    assert verified.key_id == key.key_id


def test_every_supported_algorithm_is_asymmetric() -> None:
    assert not any(name.startswith(("HS", "none")) for name in SUPPORTED_ALGORITHMS)
    assert {"RS256", "ES256"} <= set(SUPPORTED_ALGORITHMS)


def test_a_token_signed_by_another_key_is_refused() -> None:
    key, other = SigningKey("RS256", "k1"), SigningKey("RS256", "k1")

    with pytest.raises(JwtError) as caught:
        verify_jws(token(other), [key.jwk()])
    assert caught.value.reason == "signature"


def test_a_changed_payload_is_refused() -> None:
    key = SigningKey("ES256")
    header, payload, signature = token(key).split(".")
    forged = {**CLAIMS, "sub": "admin"}
    swapped = ".".join([header, b64(json.dumps(forged).encode()), signature])

    with pytest.raises(JwtError) as caught:
        verify_jws(swapped, [key.jwk()])
    assert caught.value.reason == "signature"


def test_a_flipped_signature_bit_is_refused() -> None:
    key = SigningKey("RS256")
    header, payload, signature = token(key).split(".")
    raw = bytearray(signature.encode())
    raw[10] = ord("A") if raw[10] != ord("A") else ord("B")

    with pytest.raises(JwtError) as caught:
        verify_jws(".".join([header, payload, raw.decode()]), [key.jwk()])
    assert caught.value.reason == "signature"


def test_alg_none_is_refused_even_when_the_signature_is_empty() -> None:
    key = SigningKey("RS256")
    unsigned = (
        b64(json.dumps({"alg": "none"}).encode()) + "." + b64(json.dumps(CLAIMS).encode()) + "."
    )

    with pytest.raises(JwtError) as caught:
        verify_jws(unsigned, [key.jwk()])
    assert caught.value.reason in {"algorithm", "malformed"}


def test_an_hmac_token_made_with_the_public_key_is_refused() -> None:
    # The classic confusion: HS256 keyed with the provider's public modulus.
    key = SigningKey("RS256")
    forged = (
        b64(json.dumps({"alg": "HS256", "kid": key.key_id}).encode())
        + "."
        + b64(json.dumps(CLAIMS).encode())
        + "."
        + b64(b"x" * 32)
    )

    with pytest.raises(JwtError) as caught:
        verify_jws(forged, [key.jwk()])
    assert caught.value.reason == "algorithm"


def test_the_caller_can_narrow_the_algorithms_and_the_header_cannot_widen_them() -> None:
    key = SigningKey("ES256")

    with pytest.raises(JwtError) as caught:
        verify_jws(token(key), [key.jwk()], algorithms=("RS256",))
    assert caught.value.reason == "algorithm"


def test_an_unknown_kid_says_so_so_the_caller_can_refresh_its_keys() -> None:
    key = SigningKey("RS256", "new")
    published = SigningKey("RS256", "old")

    with pytest.raises(JwtError) as caught:
        verify_jws(token(key), [published.jwk()])
    assert caught.value.reason == "unknown_key"


def test_a_token_without_a_kid_is_tried_against_the_keys_of_its_type() -> None:
    rsa_key, ec_key = SigningKey("RS256", "a"), SigningKey("ES256", "b")
    bare = compact({"alg": "ES256"}, CLAIMS, ec_key)

    assert verify_jws(bare, [rsa_key.jwk(), ec_key.jwk()]).claims == CLAIMS


def test_a_key_of_another_type_than_the_algorithm_is_not_used() -> None:
    key = SigningKey("ES256", "k")
    claimed = {**key.jwk(), "kty": "RSA", "n": "AQAB", "e": "AQAB"}

    with pytest.raises(JwtError) as caught:
        verify_jws(token(key), [claimed])
    assert caught.value.reason == "key"


def test_a_key_for_another_algorithm_or_use_is_not_used() -> None:
    key = SigningKey("RS256", "k")

    for bent in ({"alg": "RS384"}, {"use": "enc"}, {"key_ops": ["encrypt"]}):
        with pytest.raises(JwtError) as caught:
            verify_jws(token(key), [{**key.jwk(), **bent}])
        assert caught.value.reason == "key"


def test_a_weak_rsa_key_is_refused() -> None:
    weak = {"kty": "RSA", "n": b64((2**1023 + 1).to_bytes(128, "big")), "e": "AQAB"}

    with pytest.raises(JwtError) as caught:
        public_key_from_jwk(weak, "RS256")
    assert caught.value.reason == "key"


def test_an_elliptic_point_off_the_curve_is_refused() -> None:
    key = SigningKey("ES256")
    jwk = key.jwk()
    jwk["y"] = b64(b"\x01" * 32)

    with pytest.raises(JwtError) as caught:
        public_key_from_jwk(jwk, "ES256")
    assert caught.value.reason == "key"


def test_a_curve_that_is_not_the_algorithms_is_refused() -> None:
    key = SigningKey("ES384")

    with pytest.raises(JwtError) as caught:
        public_key_from_jwk(key.jwk(), "ES256")
    assert caught.value.reason == "key"


def test_keys_the_token_brings_are_never_used() -> None:
    key, attacker = SigningKey("RS256", "k"), SigningKey("RS256", "k")
    forged = compact(
        {"alg": "RS256", "kid": "k", "jwk": attacker.jwk(), "jku": "https://evil.test/jwks"},
        CLAIMS,
        attacker,
    )

    with pytest.raises(JwtError):
        verify_jws(forged, [key.jwk()])


def test_a_critical_header_this_verifier_does_not_understand_is_refused() -> None:
    key = SigningKey("RS256")

    with pytest.raises(JwtError) as caught:
        verify_jws(token(key, crit=["exp"], exp=1), [key.jwk()])
    assert caught.value.reason == "algorithm"


@pytest.mark.parametrize(
    "text",
    ["", "a.b", "a.b.c.d", "...", "not base64!.e30.sig", "e30.e30"],
)
def test_garbage_is_malformed(text: str) -> None:
    with pytest.raises(JwtError) as caught:
        split_token(text)
    assert caught.value.reason == "malformed"


def test_a_payload_that_is_not_an_object_is_malformed() -> None:
    key = SigningKey("RS256")
    header = b64(json.dumps({"alg": "RS256"}).encode())
    payload = b64(json.dumps([1, 2]).encode())

    with pytest.raises(JwtError) as caught:
        verify_jws(f"{header}.{payload}.{b64(b'x')}", [key.jwk()])
    assert caught.value.reason == "malformed"


def test_an_oversized_token_is_refused_before_it_is_parsed() -> None:
    with pytest.raises(JwtError) as caught:
        split_token("a." * 40_000 + "b")
    assert caught.value.reason == "malformed"


def test_an_es256_signature_of_the_wrong_length_does_not_verify() -> None:
    key = SigningKey("ES256")
    header, payload, _ = token(key).split(".")

    with pytest.raises(JwtError) as caught:
        verify_jws(f"{header}.{payload}.{b64(b'short')}", [key.jwk()])
    assert caught.value.reason == "signature"
