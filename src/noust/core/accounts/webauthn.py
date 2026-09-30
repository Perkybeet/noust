# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
WebAuthn verification, written for Noust: the protocol here, the curves in ``cryptography``.

No WebAuthn library is packaged on every distribution Noust ships to
(``research/3.1/passkeys.md`` §3), so the protocol - a CBOR subset,
``authenticatorData``, ``clientDataJSON``, COSE keys and the checks of both
ceremonies - is written here, and only the signature itself is delegated to
``cryptography``, which every target packages (``python3-cryptography``).
It is an optional dependency of the console, imported when a passkey is
verified and never before: :func:`load_crypto` says what to install when it
is missing, the way the fleet's ``load_httpx`` does.

What this verifies, and what it deliberately does not:

- Attestation conveyance is ``none``. The attestation statement is read as a
  map and ignored whatever its format, as the specification allows a Relying
  Party to do when it asked for ``none``: the AAGUID it carries is a label,
  never a proof.
- ES256 and RS256 always; EdDSA (Ed25519) when the installed ``cryptography``
  has it (:func:`supported_algorithms`). RSA keys under 2048 bits and points
  off the curve are refused.
- User presence and user verification are required, checked here from the
  authenticator's own flags: asking the browser for verification is not the
  same as getting it.
- The challenge is checked by a callback the caller supplies, called before
  anything else, so a caller that spends it there spends it whether or not the
  rest verifies.
- ``clientDataJSON`` is hashed as received, never re-serialised.
- A signature counter that does not move forward, once either side has
  counted, is refused as a possible clone (:class:`WebAuthnError` with reason
  ``counter`` and the credential id, so the caller can record it).

Every refusal is a :class:`WebAuthnError` with a ``reason`` for the audit log;
the decoder raises its subclass :class:`CborError` and nothing else, whatever
the bytes (a property test holds it to that).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import struct
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from noust.core.exceptions import DependencyError

#: COSE algorithm identifiers (RFC 9053, IANA COSE registry).
ALG_ES256 = -7
ALG_EDDSA = -8
ALG_RS256 = -257

#: COSE key types and the one curve of each Noust accepts.
KTY_OKP = 1
KTY_EC2 = 2
KTY_RSA = 3
CRV_P256 = 1
CRV_ED25519 = 6

#: Flags of ``authenticatorData`` (WebAuthn §6.1).
FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40
FLAG_ED = 0x80

#: Bounds on what an anonymous caller can make this code parse. The console
#: already refuses any body over 1 MiB; these keep each piece to what a real
#: authenticator produces, with room to spare.
MAX_CBOR_BYTES = 64 * 1024
MAX_CBOR_DEPTH = 8
MAX_CLIENT_DATA_BYTES = 16 * 1024
MAX_CREDENTIAL_ID_BYTES = 1023
MAX_USER_HANDLE_BYTES = 64
MAX_SIGNATURE_BYTES = 2048
MIN_RSA_BITS = 2048
MAX_RSA_BITS = 16384

#: Transport hints a browser may report; anything else is dropped.
KNOWN_TRANSPORTS = frozenset({"usb", "nfc", "ble", "smart-card", "hybrid", "internal", "cable"})

_B64URL = re.compile(r"[A-Za-z0-9_-]*")


class WebAuthnError(ValueError):
    """
    A credential or a ceremony was refused.

    Attributes:
        reason: Why, for the audit log: ``malformed``, ``type``,
            ``challenge``, ``origin``, ``rp_id``, ``user_presence``,
            ``user_verification``, ``attested_data``, ``algorithm``, ``key``,
            ``signature``, ``counter``, ``user_handle``,
            ``unknown_credential`` or ``backup_eligibility``.
        credential_id: The credential it concerns, when one was identified.
    """

    def __init__(self, reason: str, message: str, *, credential_id: bytes | None = None) -> None:
        """
        Args:
            reason: The machine-readable reason.
            message: One sentence saying what was wrong.
            credential_id: The credential it concerns, when known.
        """
        super().__init__(message)
        self.reason = reason
        self.credential_id = credential_id


class CborError(WebAuthnError):
    """Bytes that are not the CBOR a WebAuthn authenticator writes."""

    def __init__(self, message: str) -> None:
        """
        Args:
            message: What was wrong with the bytes.
        """
        super().__init__("malformed", message)


class WebAuthnUnavailable(DependencyError):
    """The ``cryptography`` library passkeys verify signatures with is not installed."""


# ---------------------------------------------------------------- cryptography


def load_crypto() -> SimpleNamespace:
    """
    Import the parts of ``cryptography`` a signature check needs.

    Imported here, when a passkey is verified, and never when the console
    starts: a server without the library runs its console and its CLI, and
    only passkeys say what is missing.

    Returns:
        A namespace with ``hashes``, ``ec``, ``rsa``, ``padding``,
        ``ed25519`` (None when this build has no Ed25519), ``InvalidSignature``
        and ``UnsupportedAlgorithm``.

    Raises:
        WebAuthnUnavailable: The library is not installed.
    """
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    except ImportError as exc:
        raise WebAuthnUnavailable(
            "Passkeys need the cryptography library, which is not installed here",
            details=(
                "Install python3-cryptography (python311-cryptography on openSUSE Leap), "
                "or pip install 'noust[web]', then restart noust-web. Python said: "
                f"{exc}"
            ),
        ) from exc
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError:
        ed25519 = None  # type: ignore[assignment]
    return SimpleNamespace(
        hashes=hashes,
        ec=ec,
        rsa=rsa,
        padding=padding,
        ed25519=ed25519,
        InvalidSignature=InvalidSignature,
        UnsupportedAlgorithm=UnsupportedAlgorithm,
    )


@lru_cache(maxsize=1)
def supported_algorithms() -> tuple[int, ...]:
    """
    The COSE algorithms this server can verify, most preferred first.

    ES256 first (every authenticator has it), then EdDSA when the installed
    ``cryptography`` and OpenSSL have Ed25519, then RS256, which Windows
    Hello needs.

    Returns:
        The algorithm identifiers, for ``pubKeyCredParams``.

    Raises:
        WebAuthnUnavailable: The library is not installed.
    """
    crypto = load_crypto()
    algorithms = [ALG_ES256]
    if crypto.ed25519 is not None:
        try:
            crypto.ed25519.Ed25519PrivateKey.generate()
        except crypto.UnsupportedAlgorithm:
            pass
        else:
            algorithms.append(ALG_EDDSA)
    algorithms.append(ALG_RS256)
    return tuple(algorithms)


# --------------------------------------------------------------------- base64


def b64url_encode(data: bytes) -> str:
    """
    Args:
        data: Raw bytes.

    Returns:
        Unpadded URL-safe base64, as WebAuthn's JSON serialisation writes it.
    """
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text: Any, *, limit: int = MAX_CBOR_BYTES, what: str = "value") -> bytes:
    """
    Decode unpadded URL-safe base64, strictly.

    Args:
        text: What the client sent.
        limit: Largest decoded size accepted.
        what: Name of the field, for the message.

    Returns:
        The bytes.

    Raises:
        WebAuthnError: When it is not a base64url string, or decodes to more
            than ``limit`` bytes.
    """
    if not isinstance(text, str) or not _B64URL.fullmatch(text) or len(text) % 4 == 1:
        raise WebAuthnError("malformed", f"The {what} is not base64url")
    if len(text) > (limit * 4 + 2) // 3 + 4:
        raise WebAuthnError("malformed", f"The {what} is too long")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise WebAuthnError("malformed", f"The {what} is not base64url") from exc


# ----------------------------------------------------------------------- CBOR


def _argument(data: bytes, offset: int, info: int) -> tuple[int, int]:
    """
    Read the argument of a CBOR head.

    Args:
        data: The input.
        offset: Where the argument starts, just after the initial byte.
        info: The initial byte's additional information.

    Returns:
        The argument and the offset after it.

    Raises:
        CborError: For an indefinite or reserved length, or truncated input.
    """
    if info < 24:
        return info, offset
    sizes = {24: 1, 25: 2, 26: 4, 27: 8}
    size = sizes.get(info)
    if size is None:
        raise CborError("Indefinite lengths and reserved values are not accepted")
    if offset + size > len(data):
        raise CborError("The CBOR input ends in the middle of a value")
    return int.from_bytes(data[offset : offset + size], "big"), offset + size


def _decode(data: bytes, offset: int, depth: int) -> tuple[Any, int]:
    """
    Decode one CBOR item.

    Args:
        data: The input.
        offset: Where the item starts.
        depth: How many containers enclose it.

    Returns:
        The item and the offset after it.

    Raises:
        CborError: For anything outside the subset WebAuthn uses.
    """
    if offset >= len(data):
        raise CborError("The CBOR input ends in the middle of a value")
    initial = data[offset]
    major, info = initial >> 5, initial & 0x1F
    offset += 1
    if major == 7:
        simple = {20: False, 21: True, 22: None}
        if info not in simple:
            raise CborError("Floats and simple values other than true, false and null are refused")
        return simple[info], offset
    if major == 6:
        raise CborError("CBOR tags are not accepted")
    argument, offset = _argument(data, offset, info)
    if major == 0:
        return argument, offset
    if major == 1:
        return -1 - argument, offset
    if major in (2, 3):
        end = offset + argument
        if end > len(data):
            raise CborError("A CBOR string is longer than the input")
        raw = data[offset:end]
        if major == 2:
            return bytes(raw), end
        try:
            return raw.decode("utf-8"), end
        except UnicodeDecodeError as exc:
            raise CborError("A CBOR text string is not UTF-8") from exc
    if depth >= MAX_CBOR_DEPTH:
        raise CborError("The CBOR input is nested too deeply")
    remaining = len(data) - offset
    if major == 4:
        if argument > remaining:
            raise CborError("A CBOR array is longer than the input")
        items = []
        for _ in range(argument):
            item, offset = _decode(data, offset, depth + 1)
            items.append(item)
        return items, offset
    # major == 5, the only one left.
    if argument * 2 > remaining:
        raise CborError("A CBOR map is longer than the input")
    result: dict[Any, Any] = {}
    for _ in range(argument):
        key, offset = _decode(data, offset, depth + 1)
        if isinstance(key, bool) or not isinstance(key, (int, str)):
            raise CborError("A CBOR map key must be an integer or a text string")
        if key in result:
            raise CborError("A CBOR map repeats a key")
        result[key], offset = _decode(data, offset, depth + 1)
    return result, offset


def cbor_decode_prefix(data: bytes) -> tuple[Any, int]:
    """
    Decode the CBOR item at the start of some bytes.

    Args:
        data: The input; what follows the first item is left alone.

    Returns:
        The item and how many bytes it took.

    Raises:
        CborError: For input over :data:`MAX_CBOR_BYTES` or outside the
            subset: integers, byte and text strings, arrays, maps with integer
            or text keys, true, false and null, with definite lengths only.
    """
    if len(data) > MAX_CBOR_BYTES:
        raise CborError("The CBOR input is too large")
    return _decode(bytes(data), 0, 0)


def cbor_decode(data: bytes) -> Any:
    """
    Decode exactly one CBOR item.

    Args:
        data: The input.

    Returns:
        The item.

    Raises:
        CborError: As :func:`cbor_decode_prefix`, and for bytes after the item.
    """
    value, end = cbor_decode_prefix(data)
    if end != len(data):
        raise CborError("Unexpected bytes after the CBOR item")
    return value


# ------------------------------------------------------ authenticator data


@dataclass(frozen=True)
class AuthenticatorData:
    """
    The authenticator's own statement about a ceremony (WebAuthn §6.1).

    Attributes:
        raw: The bytes, as signed.
        rp_id_hash: SHA-256 of the RP ID the authenticator used.
        flags: The flags byte.
        sign_count: The signature counter.
        aaguid: The authenticator model, for a registration.
        credential_id: The new credential's id, for a registration.
        credential_public_key: Its COSE key, as the CBOR bytes it arrived as.
        extensions: The extension outputs, when flagged.
    """

    raw: bytes
    rp_id_hash: bytes
    flags: int
    sign_count: int
    aaguid: bytes | None = None
    credential_id: bytes | None = None
    credential_public_key: bytes | None = None
    extensions: dict[Any, Any] | None = None

    @property
    def user_present(self) -> bool:
        """UP: someone touched the authenticator."""
        return bool(self.flags & FLAG_UP)

    @property
    def user_verified(self) -> bool:
        """UV: the authenticator checked a PIN or a biometric."""
        return bool(self.flags & FLAG_UV)

    @property
    def backup_eligible(self) -> bool:
        """BE: the credential may be synced (a label under attestation ``none``)."""
        return bool(self.flags & FLAG_BE)

    @property
    def backup_state(self) -> bool:
        """BS: the credential is synced right now."""
        return bool(self.flags & FLAG_BS)

    @property
    def attested(self) -> bool:
        """AT: attested credential data follows."""
        return bool(self.flags & FLAG_AT)


def parse_authenticator_data(raw: bytes) -> AuthenticatorData:
    """
    Parse ``authenticatorData``.

    Args:
        raw: The bytes.

    Returns:
        The parsed data.

    Raises:
        WebAuthnError: When it is truncated, has bytes nobody accounts for,
            claims backup state without eligibility, or carries a credential
            id over 1023 bytes.
    """
    if len(raw) < 37:
        raise WebAuthnError("malformed", "The authenticator data is too short")
    rp_id_hash, flags = raw[:32], raw[32]
    (sign_count,) = struct.unpack(">I", raw[33:37])
    if flags & FLAG_BS and not flags & FLAG_BE:
        raise WebAuthnError(
            "malformed", "The authenticator says the passkey is synced but cannot be"
        )
    offset = 37
    aaguid = credential_id = public_key = None
    if flags & FLAG_AT:
        if len(raw) < offset + 18:
            raise WebAuthnError("malformed", "The attested credential data is truncated")
        aaguid = raw[offset : offset + 16]
        (length,) = struct.unpack(">H", raw[offset + 16 : offset + 18])
        offset += 18
        if not 1 <= length <= MAX_CREDENTIAL_ID_BYTES or len(raw) < offset + length:
            raise WebAuthnError("malformed", "The credential id is missing, too long or truncated")
        credential_id = raw[offset : offset + length]
        offset += length
        _key, used = cbor_decode_prefix(raw[offset:])
        public_key = raw[offset : offset + used]
        offset += used
    extensions = None
    if flags & FLAG_ED:
        decoded = cbor_decode(raw[offset:])
        if not isinstance(decoded, dict):
            raise WebAuthnError("malformed", "The authenticator's extensions are not a map")
        extensions = decoded
        offset = len(raw)
    if offset != len(raw):
        raise WebAuthnError("malformed", "Unexpected bytes after the authenticator data")
    return AuthenticatorData(
        raw=bytes(raw),
        rp_id_hash=bytes(rp_id_hash),
        flags=flags,
        sign_count=sign_count,
        aaguid=bytes(aaguid) if aaguid is not None else None,
        credential_id=bytes(credential_id) if credential_id is not None else None,
        credential_public_key=bytes(public_key) if public_key is not None else None,
        extensions=extensions,
    )


# ------------------------------------------------------------------ COSE keys


@dataclass(frozen=True)
class CoseKey:
    """
    A credential public key, as the authenticator described it.

    Attributes:
        raw: The CBOR bytes, as stored.
        alg: The COSE algorithm.
        kty: The COSE key type.
        params: The key's parameters, by COSE label.
    """

    raw: bytes
    alg: int
    kty: int
    params: dict[int, Any] = field(repr=False)


#: What each algorithm's key must look like: its key type, its curve (None
#: for RSA), and the byte length of each coordinate that has a fixed one.
_KEY_SHAPES: dict[int, tuple[int, int | None, dict[int, int]]] = {
    ALG_ES256: (KTY_EC2, CRV_P256, {-2: 32, -3: 32}),
    ALG_EDDSA: (KTY_OKP, CRV_ED25519, {-2: 32}),
    ALG_RS256: (KTY_RSA, None, {}),
}


def parse_cose_key(raw: bytes, allowed: Iterable[int] | None = None) -> CoseKey:
    """
    Parse a COSE key and check it is one of the kinds this server offered.

    Args:
        raw: The CBOR bytes.
        allowed: The algorithms to accept; every one this module knows by
            default.

    Returns:
        The key. Its point or modulus is checked by :func:`load_public_key`.

    Raises:
        WebAuthnError: ``algorithm`` for an algorithm not allowed, ``key`` for
            a key whose type, curve or parameters contradict it.
    """
    decoded = cbor_decode(raw)
    if not isinstance(decoded, dict):
        raise WebAuthnError("key", "The credential public key is not a COSE map")
    alg, kty = decoded.get(3), decoded.get(1)
    permitted = tuple(allowed) if allowed is not None else tuple(_KEY_SHAPES)
    if not isinstance(alg, int) or isinstance(alg, bool) or alg not in permitted:
        raise WebAuthnError(
            "algorithm",
            f"The passkey uses COSE algorithm {alg!r}, which this server does not accept",
        )
    shape = _KEY_SHAPES.get(alg)
    if shape is None:
        raise WebAuthnError("algorithm", f"COSE algorithm {alg} is not supported")
    expected_kty, expected_crv, lengths = shape
    if kty != expected_kty:
        raise WebAuthnError("key", "The key type does not match the algorithm")
    if expected_crv is not None and decoded.get(-1) != expected_crv:
        raise WebAuthnError("key", "The key's curve does not match the algorithm")
    for label, length in lengths.items():
        value = decoded.get(label)
        if not isinstance(value, bytes) or len(value) != length:
            raise WebAuthnError("key", "A coordinate of the key has the wrong size")
    if alg == ALG_RS256:
        for label in (-1, -2):
            if not isinstance(decoded.get(label), bytes) or not decoded[label]:
                raise WebAuthnError("key", "The RSA key has no modulus or exponent")
    params = {label: value for label, value in decoded.items() if isinstance(label, int)}
    return CoseKey(raw=bytes(raw), alg=alg, kty=kty, params=params)


def load_public_key(key: CoseKey) -> Any:
    """
    Turn a COSE key into a ``cryptography`` public key, validating it.

    Args:
        key: The parsed key.

    Returns:
        The public key object.

    Raises:
        WebAuthnError: ``key`` for a point off the curve, or an RSA key
            under 2048 bits or with an even or trivial exponent.
        WebAuthnUnavailable: The library is not installed.
    """
    crypto = load_crypto()
    try:
        if key.alg == ALG_ES256:
            point = b"\x04" + key.params[-2] + key.params[-3]
            return crypto.ec.EllipticCurvePublicKey.from_encoded_point(crypto.ec.SECP256R1(), point)
        if key.alg == ALG_EDDSA:
            if crypto.ed25519 is None:
                raise WebAuthnError("algorithm", "This server's cryptography has no Ed25519")
            return crypto.ed25519.Ed25519PublicKey.from_public_bytes(key.params[-2])
        modulus = int.from_bytes(key.params[-1], "big")
        exponent = int.from_bytes(key.params[-2], "big")
        if not MIN_RSA_BITS <= modulus.bit_length() <= MAX_RSA_BITS:
            raise WebAuthnError("key", f"RSA keys must have {MIN_RSA_BITS} to {MAX_RSA_BITS} bits")
        if exponent < 3 or exponent % 2 == 0:
            raise WebAuthnError("key", "The RSA key's exponent is not usable")
        return crypto.rsa.RSAPublicNumbers(exponent, modulus).public_key()
    except crypto.UnsupportedAlgorithm as exc:
        raise WebAuthnError("algorithm", "This server's cryptography cannot use that key") from exc
    except ValueError as exc:
        if isinstance(exc, WebAuthnError):
            raise
        raise WebAuthnError("key", "The credential public key is not a valid key") from exc


def verify_signature(key: CoseKey, message: bytes, signature: bytes) -> bool:
    """
    Check a signature: the one place this module trusts the library.

    Args:
        key: The credential's key.
        message: What was signed: ``authenticatorData || SHA-256(clientDataJSON)``.
        signature: DER ECDSA for ES256, PKCS#1 v1.5 for RS256, raw for EdDSA.

    Returns:
        True when it verifies.

    Raises:
        WebAuthnError: When the key itself is unusable.
        WebAuthnUnavailable: The library is not installed.
    """
    crypto = load_crypto()
    public = load_public_key(key)
    try:
        if key.alg == ALG_ES256:
            public.verify(signature, message, crypto.ec.ECDSA(crypto.hashes.SHA256()))
        elif key.alg == ALG_RS256:
            public.verify(signature, message, crypto.padding.PKCS1v15(), crypto.hashes.SHA256())
        else:
            public.verify(signature, message)
    except (crypto.InvalidSignature, ValueError):
        return False
    return True


# ----------------------------------------------------------- the ceremonies


@dataclass(frozen=True)
class ClientData:
    """
    What the browser says about a ceremony, as it said it.

    Attributes:
        raw: ``clientDataJSON`` as received; its hash is what was signed.
        type: ``webauthn.create`` or ``webauthn.get``.
        challenge: The challenge the page passed in.
        origin: The page's origin.
    """

    raw: bytes
    type: str
    challenge: bytes
    origin: str


def _client_data(
    encoded: Any,
    expected_type: str,
    challenge_ok: Callable[[bytes], bool],
    origins: Iterable[str],
) -> ClientData:
    """
    Parse and check ``clientDataJSON``, the challenge first.

    Args:
        encoded: The base64url field.
        expected_type: ``webauthn.create`` or ``webauthn.get``.
        challenge_ok: Says whether a challenge is one this server issued and
            has not seen spent; called before any other check.
        origins: Every origin a page of this console may have.

    Returns:
        The client data.

    Raises:
        WebAuthnError: ``malformed``, ``challenge``, ``type`` or ``origin``.
    """
    raw = b64url_decode(encoded, limit=MAX_CLIENT_DATA_BYTES, what="clientDataJSON")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebAuthnError("malformed", "clientDataJSON is not JSON") from exc
    if not isinstance(data, dict):
        raise WebAuthnError("malformed", "clientDataJSON is not a JSON object")
    challenge = b64url_decode(data.get("challenge"), limit=1024, what="challenge")
    if not challenge_ok(challenge):
        raise WebAuthnError(
            "challenge", "The challenge is not one this server issued, or it expired or was used"
        )
    if data.get("type") != expected_type:
        raise WebAuthnError("type", f"This is not a {expected_type} ceremony")
    origin = data.get("origin")
    allowed = {item.lower().rstrip("/") for item in origins}
    if not isinstance(origin, str) or origin.lower() not in allowed:
        raise WebAuthnError(
            "origin",
            f"The browser ran the ceremony on {origin!r}, which is not this console "
            f"({', '.join(sorted(allowed)) or 'no origin configured'})",
        )
    if data.get("crossOrigin") not in (None, False) or data.get("topOrigin") is not None:
        raise WebAuthnError("origin", "The ceremony ran in a frame of another site")
    return ClientData(raw=raw, type=expected_type, challenge=challenge, origin=origin)


def _envelope(credential: Any) -> tuple[bytes, Mapping[str, Any]]:
    """
    Check a PublicKeyCredential's JSON envelope.

    Args:
        credential: The JSON object the browser's ``toJSON()`` produced.

    Returns:
        The raw credential id and the ``response`` object.

    Raises:
        WebAuthnError: ``malformed`` when a member is missing, of the wrong
            type, or ``id`` and ``rawId`` disagree.
    """
    if not isinstance(credential, Mapping):
        raise WebAuthnError("malformed", "The credential is not a JSON object")
    if credential.get("type") != "public-key":
        raise WebAuthnError("malformed", "The credential is not a public-key credential")
    raw_id = b64url_decode(credential.get("rawId"), limit=MAX_CREDENTIAL_ID_BYTES, what="rawId")
    if not raw_id or credential.get("id") != credential.get("rawId"):
        raise WebAuthnError("malformed", "The credential's id and rawId disagree")
    response = credential.get("response")
    if not isinstance(response, Mapping):
        raise WebAuthnError("malformed", "The credential carries no response")
    return raw_id, response


def _check_authenticator(data: AuthenticatorData, rp_id: str) -> None:
    """
    Check what every ceremony asks of the authenticator.

    Args:
        data: Its data.
        rp_id: The RP ID this console answers as.

    Raises:
        WebAuthnError: ``rp_id``, ``user_presence`` or ``user_verification``.
    """
    if not hmac.compare_digest(data.rp_id_hash, hashlib.sha256(rp_id.encode("utf-8")).digest()):
        raise WebAuthnError("rp_id", f"The authenticator signed for another site than {rp_id}")
    if not data.user_present:
        raise WebAuthnError("user_presence", "The authenticator did not confirm anyone was there")
    if not data.user_verified:
        raise WebAuthnError(
            "user_verification",
            "The authenticator did not verify the user with a PIN or a biometric",
        )


@dataclass(frozen=True)
class VerifiedRegistration:
    """
    A new credential that passed every check.

    Attributes:
        credential_id: Its id.
        public_key: Its COSE key, as stored.
        alg: Its COSE algorithm.
        sign_count: Its counter at birth.
        backup_eligible: BE, a label (attestation is ``none``).
        backup_state: BS.
        user_verified: Always True here; kept for the record.
        aaguid: The authenticator model it claims, as hex.
        transports: The transport hints the browser reported.
        fmt: The attestation format it arrived in (ignored).
    """

    credential_id: bytes
    public_key: bytes
    alg: int
    sign_count: int
    backup_eligible: bool
    backup_state: bool
    user_verified: bool
    aaguid: str
    transports: list[str]
    fmt: str


def verify_registration(
    credential: Any,
    *,
    rp_id: str,
    origins: Iterable[str],
    challenge_ok: Callable[[bytes], bool],
    algorithms: Iterable[int] | None = None,
) -> VerifiedRegistration:
    """
    Verify a registration (``navigator.credentials.create()``).

    Args:
        credential: The RegistrationResponseJSON the browser produced.
        rp_id: The RP ID this console answers as.
        origins: The origins its pages may have.
        challenge_ok: Checks, and spends, the challenge; called first.
        algorithms: The algorithms offered; :func:`supported_algorithms` by
            default.

    Returns:
        The credential to store.

    Raises:
        WebAuthnError: For any failed check, with the reason.
        WebAuthnUnavailable: The library is not installed.
    """
    raw_id, response = _envelope(credential)
    _client_data(response.get("clientDataJSON"), "webauthn.create", challenge_ok, origins)
    attestation = cbor_decode(
        b64url_decode(response.get("attestationObject"), what="attestationObject")
    )
    if not isinstance(attestation, dict):
        raise WebAuthnError("malformed", "The attestation object is not a map")
    fmt, statement, auth_bytes = (
        attestation.get("fmt"),
        attestation.get("attStmt"),
        attestation.get("authData"),
    )
    if not isinstance(fmt, str) or not isinstance(statement, dict):
        raise WebAuthnError("malformed", "The attestation object has no format or statement")
    if not isinstance(auth_bytes, bytes):
        raise WebAuthnError("malformed", "The attestation object has no authenticator data")
    data = parse_authenticator_data(auth_bytes)
    _check_authenticator(data, rp_id)
    if not data.attested or data.credential_id is None or data.credential_public_key is None:
        raise WebAuthnError("attested_data", "The authenticator did not describe the new passkey")
    if not hmac.compare_digest(data.credential_id, raw_id):
        raise WebAuthnError("malformed", "The credential id is not the one the authenticator made")
    offered = tuple(algorithms) if algorithms is not None else supported_algorithms()
    key = parse_cose_key(data.credential_public_key, offered)
    load_public_key(key)
    transports = response.get("transports")
    hints = (
        sorted({item for item in transports if item in KNOWN_TRANSPORTS})[:8]
        if isinstance(transports, list)
        else []
    )
    return VerifiedRegistration(
        credential_id=raw_id,
        public_key=key.raw,
        alg=key.alg,
        sign_count=data.sign_count,
        backup_eligible=data.backup_eligible,
        backup_state=data.backup_state,
        user_verified=data.user_verified,
        aaguid=(data.aaguid or bytes(16)).hex(),
        transports=hints,
        fmt=fmt[:32],
    )


@dataclass(frozen=True)
class StoredCredential:
    """
    What an assertion is checked against: a credential as registered here.

    Attributes:
        credential_id: Its id.
        public_key: Its COSE key.
        sign_count: The last counter it reported.
        backup_eligible: BE at registration; it may not change.
        user_handle: The WebAuthn ``user.id`` it was registered with.
        rp_id: The RP ID it was registered under.
    """

    credential_id: bytes
    public_key: bytes
    sign_count: int
    backup_eligible: bool
    user_handle: bytes
    rp_id: str


@dataclass(frozen=True)
class VerifiedAssertion:
    """
    An assertion that passed every check.

    Attributes:
        credential_id: The credential that signed.
        sign_count: The counter it reported, to store.
        backup_state: BS as it is now.
        user_verified: Always True here.
        user_handle: The ``user.id`` it returned, when it did.
    """

    credential_id: bytes
    sign_count: int
    backup_state: bool
    user_verified: bool
    user_handle: bytes | None


def verify_assertion(
    credential: Any,
    *,
    rp_id: str,
    origins: Iterable[str],
    challenge_ok: Callable[[bytes], bool],
    lookup: Callable[[bytes], StoredCredential | None],
    require_user_handle: bool = True,
) -> VerifiedAssertion:
    """
    Verify an assertion (``navigator.credentials.get()``).

    Args:
        credential: The AuthenticationResponseJSON the browser produced.
        rp_id: The RP ID this console answers as.
        origins: The origins its pages may have.
        challenge_ok: Checks, and spends, the challenge; called first.
        lookup: Finds the stored credential by id.
        require_user_handle: Refuse an assertion without ``userHandle``, as a
            discoverable sign-in must carry one.

    Returns:
        The result, with the counter to store.

    Raises:
        WebAuthnError: For any failed check. ``unknown_credential`` when no
            credential here has that id under this RP ID; ``counter``, with
            the credential id, when the counter did not move forward.
        WebAuthnUnavailable: The library is not installed.
    """
    raw_id, response = _envelope(credential)
    client = _client_data(response.get("clientDataJSON"), "webauthn.get", challenge_ok, origins)
    stored = lookup(raw_id)
    if stored is None or stored.rp_id != rp_id:
        raise WebAuthnError(
            "unknown_credential",
            "This passkey is not registered on this server",
            credential_id=raw_id,
        )
    auth_bytes = b64url_decode(response.get("authenticatorData"), what="authenticatorData")
    data = parse_authenticator_data(auth_bytes)
    if data.attested:
        raise WebAuthnError("attested_data", "An assertion carries no new credential")
    _check_authenticator(data, rp_id)
    handle_field = response.get("userHandle")
    if handle_field not in (None, ""):
        handle = b64url_decode(handle_field, limit=MAX_USER_HANDLE_BYTES, what="userHandle")
        if not hmac.compare_digest(handle, stored.user_handle):
            raise WebAuthnError(
                "user_handle", "The passkey answered for another user", credential_id=raw_id
            )
    else:
        handle = None
        if require_user_handle:
            raise WebAuthnError(
                "user_handle", "The passkey did not say whose it is", credential_id=raw_id
            )
    signature = b64url_decode(
        response.get("signature"), limit=MAX_SIGNATURE_BYTES, what="signature"
    )
    key = parse_cose_key(stored.public_key)
    message = auth_bytes + hashlib.sha256(client.raw).digest()
    if not verify_signature(key, message, signature):
        raise WebAuthnError("signature", "The signature does not verify", credential_id=raw_id)
    if data.backup_eligible != stored.backup_eligible:
        raise WebAuthnError(
            "backup_eligibility",
            "The passkey changed whether it can be synced since it was registered",
            credential_id=raw_id,
        )
    if (data.sign_count or stored.sign_count) and data.sign_count <= stored.sign_count:
        raise WebAuthnError(
            "counter",
            f"The passkey's signature counter went from {stored.sign_count} to "
            f"{data.sign_count}: it may have been cloned",
            credential_id=raw_id,
        )
    return VerifiedAssertion(
        credential_id=raw_id,
        sign_count=data.sign_count,
        backup_state=data.backup_state,
        user_verified=data.user_verified,
        user_handle=handle,
    )
