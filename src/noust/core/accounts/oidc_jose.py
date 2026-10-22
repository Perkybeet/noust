# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JSON Web Signatures for single sign-in: the one verifier of an ID token.

An identity provider tells Noust who signed in by signing a small document, the
ID token, with a key it publishes (its JWKS). Everything that decides whether to
believe that document is here and nowhere else, written for Noust the way
:mod:`noust.core.accounts.webauthn` is: the protocol is ours, the curves are in
``cryptography``, which every target packages for passkeys already, so single
sign-in adds no dependency.

What is refused, on purpose:

- **Every algorithm that is not an asymmetric signature.** ``none`` and the
  HMAC family are how a token is forged by presenting the server's own public
  key as a shared secret; they are never in the table, whatever the header says
  and whatever the provider advertises.
- **A key the token brings with it.** ``jwk``, ``jku`` and ``x5u`` headers are
  ignored: only keys the provider published, fetched from the address Noust
  discovered, can verify a token.
- **A header that asks for something this verifier does not understand**
  (``crit``), as RFC 7515 requires.
- **A key that does not fit the algorithm**: another key type, a curve other
  than the algorithm's, a ``use`` other than ``sig``, an ``alg`` that
  contradicts the header, an RSA key under 2048 bits.

Verifying a signature is not judging the token: whether the issuer, audience,
expiry and nonce are right is :mod:`noust.core.accounts.oidc_client`'s business.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from noust.core.accounts.webauthn import (
    MAX_RSA_BITS,
    MIN_RSA_BITS,
    WebAuthnError,
    WebAuthnUnavailable,
    b64url_decode,
    load_crypto,
)
from noust.core.exceptions import DependencyError, SecurityError

#: Largest token read. ID tokens carry a person's groups; 64 KiB is far past
#: any real one and well under what could be used to make the server work.
MAX_TOKEN_BYTES = 64 * 1024

#: Most keys tried for a token whose header names none.
MAX_KEYS_TRIED = 8

#: Algorithm to (key type, hash, padding or curve, size of one ECDSA integer).
#: RFC 7518 section 3: ES* signatures are the two integers side by side, not DER.
_ALGORITHMS: dict[str, tuple[str, str, str, int]] = {
    "RS256": ("RSA", "SHA256", "pkcs1", 0),
    "RS384": ("RSA", "SHA384", "pkcs1", 0),
    "RS512": ("RSA", "SHA512", "pkcs1", 0),
    "PS256": ("RSA", "SHA256", "pss", 0),
    "PS384": ("RSA", "SHA384", "pss", 0),
    "PS512": ("RSA", "SHA512", "pss", 0),
    "ES256": ("EC", "SHA256", "P-256", 32),
    "ES384": ("EC", "SHA384", "P-384", 48),
    "ES512": ("EC", "SHA512", "P-521", 66),
}

#: What a token may be signed with. RS256 and ES256 are what providers use;
#: the rest are the same families with other hashes.
SUPPORTED_ALGORITHMS: tuple[str, ...] = tuple(_ALGORITHMS)


class JwtError(SecurityError):
    """
    A token is not one this server will believe.

    Attributes:
        reason: Why, for the audit log: ``malformed``, ``algorithm``,
            ``unknown_key``, ``key``, ``signature`` or ``unavailable``. Whoever
            presented the token is told nothing more than that it failed.
    """

    def __init__(self, reason: str, message: str, details: str = "") -> None:
        """
        Args:
            reason: The machine-readable reason.
            message: One sentence saying what was wrong.
            details: How to fix it, when the operator can.
        """
        super().__init__(message, details)
        self.reason = reason


@dataclass(frozen=True)
class VerifiedJwt:
    """
    A token whose signature checked out.

    Attributes:
        header: The protected header.
        claims: The payload, a JSON object.
        algorithm: What it was signed with.
        key_id: The ``kid`` of the key that verified it, when it has one.
    """

    header: dict[str, Any]
    claims: dict[str, Any]
    algorithm: str
    key_id: str | None


def _crypto() -> SimpleNamespace:
    """
    The parts of ``cryptography`` a verification needs.

    Returns:
        The namespace :func:`~noust.core.accounts.webauthn.load_crypto` builds,
        with ``utils`` added for the ECDSA encoding.

    Raises:
        DependencyError: The library is not installed (single sign-in names it).
    """
    try:
        crypto = load_crypto()
    except WebAuthnUnavailable as exc:
        raise DependencyError(
            "Single sign-in needs the cryptography library, which is not installed here",
            details=(
                "Install python3-cryptography (python311-cryptography on openSUSE Leap), "
                "or pip install 'noust[web]', then restart noust-web."
            ),
        ) from exc
    from cryptography.hazmat.primitives.asymmetric import utils

    crypto.utils = utils
    return crypto


def _decode(text: Any, what: str) -> bytes:
    """
    Args:
        text: A base64url string from a token or a key.
        what: What it is, for the message.

    Returns:
        The bytes.

    Raises:
        JwtError: ``malformed`` when it is not base64url.
    """
    try:
        return b64url_decode(text, limit=MAX_TOKEN_BYTES, what=what)
    except WebAuthnError as exc:
        raise JwtError("malformed", str(exc)) from exc


def _json_object(raw: bytes, what: str) -> dict[str, Any]:
    """
    Args:
        raw: Bytes that should hold a JSON object.
        what: What it is, for the message.

    Returns:
        The object.

    Raises:
        JwtError: ``malformed`` when it is not UTF-8 JSON, or not an object.
    """
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise JwtError("malformed", f"The token's {what} is not JSON") from exc
    if not isinstance(value, dict):
        raise JwtError("malformed", f"The token's {what} is not a JSON object")
    return value


def split_token(token: str) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    """
    Take a compact JWS apart without trusting any of it.

    Args:
        token: Three base64url parts joined by dots.

    Returns:
        The header, the claims, the signing input (``header.payload`` as sent)
        and the signature.

    Raises:
        JwtError: ``malformed`` for anything that is not three parts of a
            token of a sane size, with a JSON header and payload.
    """
    if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES:
        raise JwtError("malformed", "The token is missing or too large")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts[:2]):
        raise JwtError("malformed", "The token is not a compact signed document")
    header = _json_object(_decode(parts[0], "header"), "header")
    claims = _json_object(_decode(parts[1], "payload"), "payload")
    signature = _decode(parts[2], "signature")
    return header, claims, f"{parts[0]}.{parts[1]}".encode("ascii"), signature


def public_key_from_jwk(jwk: dict[str, Any], algorithm: str) -> Any:
    """
    Turn a published key into a ``cryptography`` public key, if it may verify ``algorithm``.

    Args:
        jwk: One member of a JWKS.
        algorithm: The token's ``alg``, one of :data:`SUPPORTED_ALGORITHMS`.

    Returns:
        The public key object.

    Raises:
        JwtError: ``key`` when the key does not fit the algorithm or is not a
            sound key (an RSA key under 2048 bits, a point off its curve);
            ``algorithm`` for an algorithm this verifier does not do.
    """
    spec = _ALGORITHMS.get(algorithm)
    if spec is None:
        raise JwtError("algorithm", f"The algorithm {algorithm!r} is not accepted")
    key_type, _digest, shape, _size = spec
    if jwk.get("kty") != key_type:
        raise JwtError("key", "The key's type does not match the token's algorithm")
    if jwk.get("use") not in (None, "sig"):
        raise JwtError("key", "The key is not for signatures")
    operations = jwk.get("key_ops")
    if operations is not None and (not isinstance(operations, list) or "verify" not in operations):
        raise JwtError("key", "The key is not for verifying")
    if jwk.get("alg") not in (None, algorithm):
        raise JwtError("key", "The key is for another algorithm than the token's")
    crypto = _crypto()
    try:
        if key_type == "RSA":
            modulus = int.from_bytes(_decode(jwk.get("n"), "RSA modulus"), "big")
            exponent = int.from_bytes(_decode(jwk.get("e"), "RSA exponent"), "big")
            if not MIN_RSA_BITS <= modulus.bit_length() <= MAX_RSA_BITS:
                raise JwtError("key", f"RSA keys must have {MIN_RSA_BITS} to {MAX_RSA_BITS} bits")
            if exponent < 3 or exponent % 2 == 0:
                raise JwtError("key", "The RSA key's exponent is not usable")
            return crypto.rsa.RSAPublicNumbers(exponent, modulus).public_key()
        if jwk.get("crv") != shape:
            raise JwtError("key", "The key's curve does not match the token's algorithm")
        curve = {
            "P-256": crypto.ec.SECP256R1,
            "P-384": crypto.ec.SECP384R1,
            "P-521": crypto.ec.SECP521R1,
        }[shape]()
        size = (curve.key_size + 7) // 8
        x = _decode(jwk.get("x"), "EC coordinate")
        y = _decode(jwk.get("y"), "EC coordinate")
        if len(x) != size or len(y) != size:
            raise JwtError("key", "A coordinate of the key has the wrong size")
        return crypto.ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), curve
        ).public_key()
    except crypto.UnsupportedAlgorithm as exc:
        raise JwtError("key", "This server's cryptography cannot use that key") from exc
    except ValueError as exc:
        raise JwtError("key", "The published key is not a valid key") from exc


def _signature_valid(
    algorithm: str, key: Any, signing_input: bytes, signature: bytes, crypto: SimpleNamespace
) -> bool:
    """
    Check one signature against one key: the one place this module trusts the library.

    Args:
        algorithm: A member of :data:`SUPPORTED_ALGORITHMS`.
        key: The public key, from :func:`public_key_from_jwk`.
        signing_input: ``header.payload`` exactly as the token carried it.
        signature: The token's third part, decoded.
        crypto: The library's parts.

    Returns:
        True when it verifies.
    """
    _kind, digest, shape, size = _ALGORITHMS[algorithm]
    hash_algorithm = getattr(crypto.hashes, digest)()
    try:
        if shape == "pkcs1":
            key.verify(signature, signing_input, crypto.padding.PKCS1v15(), hash_algorithm)
        elif shape == "pss":
            padding = crypto.padding.PSS(
                mgf=crypto.padding.MGF1(hash_algorithm), salt_length=hash_algorithm.digest_size
            )
            key.verify(signature, signing_input, padding, hash_algorithm)
        else:
            if len(signature) != 2 * size:
                return False
            der = crypto.utils.encode_dss_signature(
                int.from_bytes(signature[:size], "big"), int.from_bytes(signature[size:], "big")
            )
            key.verify(der, signing_input, crypto.ec.ECDSA(hash_algorithm))
    except (crypto.InvalidSignature, ValueError):
        return False
    return True


def verify_jws(
    token: str, keys: list[dict[str, Any]], *, algorithms: tuple[str, ...] = SUPPORTED_ALGORITHMS
) -> VerifiedJwt:
    """
    Verify a signed token against the keys a provider published.

    Args:
        token: The compact JWS.
        keys: The members of the provider's JWKS.
        algorithms: What the caller allows, a subset of
            :data:`SUPPORTED_ALGORITHMS`; the token's header cannot widen it.

    Returns:
        The token with its header and claims, once one key verified it.

    Raises:
        JwtError: ``malformed``, ``algorithm`` (not allowed, or ``crit`` asks
            for what is not understood), ``unknown_key`` (the header names a
            ``kid`` no key has: the provider may have rotated, so the caller
            refreshes its keys and tries once more), ``key`` (no published key
            fits) or ``signature``.
        DependencyError: The ``cryptography`` library is not installed.
    """
    header, claims, signing_input, signature = split_token(token)
    algorithm = header.get("alg")
    if (
        not isinstance(algorithm, str)
        or algorithm not in algorithms
        or algorithm not in _ALGORITHMS
    ):
        raise JwtError(
            "algorithm", f"The token's algorithm {str(algorithm)[:16]!r} is not accepted"
        )
    if "crit" in header:
        raise JwtError("algorithm", "The token asks for header extensions this server ignores")
    crypto = _crypto()

    key_id = header.get("kid")
    if key_id is not None and not isinstance(key_id, str):
        raise JwtError("malformed", "The token's key id is not text")
    candidates = [key for key in keys if isinstance(key, dict)]
    if key_id is not None:
        candidates = [key for key in candidates if key.get("kid") == key_id]
        if not candidates:
            raise JwtError("unknown_key", "No published key has the id the token names")
    else:
        candidates = [key for key in candidates if key.get("kty") == _ALGORITHMS[algorithm][0]]
    candidates = candidates[:MAX_KEYS_TRIED]

    last_key_error: JwtError | None = None
    for jwk in candidates:
        try:
            key = public_key_from_jwk(jwk, algorithm)
        except JwtError as exc:
            last_key_error = exc
            continue
        if _signature_valid(algorithm, key, signing_input, signature, crypto):
            return VerifiedJwt(header, claims, algorithm, key_id)
        last_key_error = JwtError("signature", "The token's signature does not verify")
    if last_key_error is not None:
        raise last_key_error
    raise JwtError("key", "The provider published no key that can verify this token")
