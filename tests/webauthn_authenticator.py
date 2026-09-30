"""
A software WebAuthn authenticator, for the passkey tests.

It does what a security key or a platform authenticator does, with keys it
generates itself through ``cryptography``: it writes ``clientDataJSON`` the
way a browser does, ``authenticatorData`` and the CBOR ``attestationObject``
the way an authenticator does, and signs. Every piece can be bent on purpose
- another origin, another RP, no user verification, a counter that goes back,
a flipped bit in the signature - so each check of the verifier is proven to
refuse what it exists to refuse, with real signatures and nothing mocked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

ES256 = -7
EDDSA = -8
RS256 = -257

FLAG_UP = 0x01
FLAG_UV = 0x04
FLAG_BE = 0x08
FLAG_BS = 0x10
FLAG_AT = 0x40
FLAG_ED = 0x80


def b64url(data: bytes) -> str:
    """Unpadded URL-safe base64, as WebAuthn's JSON serialisation writes it."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64url(text: str) -> bytes:
    """Decode unpadded URL-safe base64."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _head(major: int, value: int) -> bytes:
    if value < 24:
        return bytes([(major << 5) | value])
    if value < 0x100:
        return bytes([(major << 5) | 24, value])
    if value < 0x10000:
        return bytes([(major << 5) | 25]) + struct.pack(">H", value)
    if value < 0x100000000:
        return bytes([(major << 5) | 26]) + struct.pack(">I", value)
    return bytes([(major << 5) | 27]) + struct.pack(">Q", value)


def cbor_encode(value: Any) -> bytes:
    """
    Encode the CBOR subset WebAuthn uses, in the order given.

    Args:
        value: An int, bytes, str, list, dict, bool or None.

    Returns:
        Its CBOR encoding, with minimal lengths.
    """
    if value is True:
        return b"\xf5"
    if value is False:
        return b"\xf4"
    if value is None:
        return b"\xf6"
    if isinstance(value, int):
        return _head(0, value) if value >= 0 else _head(1, -1 - value)
    if isinstance(value, bytes):
        return _head(2, len(value)) + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _head(3, len(encoded)) + encoded
    if isinstance(value, list):
        return _head(4, len(value)) + b"".join(cbor_encode(item) for item in value)
    if isinstance(value, dict):
        return _head(5, len(value)) + b"".join(
            cbor_encode(key) + cbor_encode(item) for key, item in value.items()
        )
    raise TypeError(f"cannot encode {type(value).__name__}")


def client_data(
    kind: str,
    challenge: str,
    origin: str,
    *,
    cross_origin: bool | None = None,
    top_origin: str | None = None,
) -> bytes:
    """
    Serialise ``clientDataJSON`` as a browser does.

    Args:
        kind: ``webauthn.create`` or ``webauthn.get``.
        challenge: The challenge, base64url, as the options carried it.
        origin: The page's origin.
        cross_origin: Written when not None.
        top_origin: Written when not None.

    Returns:
        The JSON bytes.
    """
    data: dict[str, Any] = {"type": kind, "challenge": challenge, "origin": origin}
    if cross_origin is not None:
        data["crossOrigin"] = cross_origin
    if top_origin is not None:
        data["topOrigin"] = top_origin
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def auth_data(
    rp_id: str,
    flags: int,
    sign_count: int,
    *,
    attested: tuple[bytes, bytes, bytes] | None = None,
    extensions: dict[str, Any] | None = None,
) -> bytes:
    """
    Build ``authenticatorData``.

    Args:
        rp_id: The RP ID whose hash leads it.
        flags: The flags byte.
        sign_count: The signature counter.
        attested: ``(aaguid, credential id, COSE key)`` for a registration.
        extensions: A CBOR map to append (sets nothing by itself; pass ED).

    Returns:
        The bytes.
    """
    data = hashlib.sha256(rp_id.encode("utf-8")).digest() + bytes([flags])
    data += struct.pack(">I", sign_count)
    if attested is not None:
        aaguid, credential_id, cose = attested
        data += aaguid + struct.pack(">H", len(credential_id)) + credential_id + cose
    if extensions is not None:
        data += cbor_encode(extensions)
    return data


@dataclass
class SoftwareAuthenticator:
    """
    One credential on a software authenticator.

    Attributes:
        alg: The COSE algorithm: ES256, RS256 or EdDSA.
        rsa_bits: Modulus size for RS256.
        backup_eligible: The BE flag it reports.
        backup_state: The BS flag it reports.
        sign_count: The counter; bumped by every assertion unless fixed.
        count_step: What each assertion adds to the counter (0 for a synced
            passkey, which always reports 0).
    """

    alg: int = ES256
    rsa_bits: int = 2048
    backup_eligible: bool = False
    backup_state: bool = False
    sign_count: int = 0
    count_step: int = 1
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    user_handle: bytes | None = None
    rp_id: str | None = None
    aaguid: bytes = bytes(16)

    def __post_init__(self) -> None:
        if self.alg == ES256:
            self._key: Any = ec.generate_private_key(ec.SECP256R1())
        elif self.alg == RS256:
            self._key = rsa.generate_private_key(public_exponent=65537, key_size=self.rsa_bits)
        elif self.alg == EDDSA:
            self._key = ed25519.Ed25519PrivateKey.generate()
        else:
            raise ValueError(f"unsupported alg {self.alg}")

    def cose_key(self) -> bytes:
        """The public key, as a COSE_Key in CBOR."""
        public = self._key.public_key()
        if self.alg == ES256:
            numbers = public.public_numbers()
            return cbor_encode(
                {
                    1: 2,
                    3: ES256,
                    -1: 1,
                    -2: numbers.x.to_bytes(32, "big"),
                    -3: numbers.y.to_bytes(32, "big"),
                }
            )
        if self.alg == RS256:
            numbers = public.public_numbers()
            modulus = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
            exponent = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
            return cbor_encode({1: 3, 3: RS256, -1: modulus, -2: exponent})
        raw = public.public_bytes_raw() if hasattr(public, "public_bytes_raw") else None
        if raw is None:  # cryptography before 40
            from cryptography.hazmat.primitives import serialization

            raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return cbor_encode({1: 1, 3: EDDSA, -1: 6, -2: raw})

    def sign(self, message: bytes) -> bytes:
        """Sign as the credential's algorithm does."""
        if self.alg == ES256:
            return bytes(self._key.sign(message, ec.ECDSA(hashes.SHA256())))
        if self.alg == RS256:
            return bytes(self._key.sign(message, padding.PKCS1v15(), hashes.SHA256()))
        return bytes(self._key.sign(message))

    def flags(self, *, user_verified: bool = True, user_present: bool = True) -> int:
        """The flags byte for this credential's state."""
        value = 0
        if user_present:
            value |= FLAG_UP
        if user_verified:
            value |= FLAG_UV
        if self.backup_eligible:
            value |= FLAG_BE
        if self.backup_state:
            value |= FLAG_BS
        return value

    def create(
        self,
        options: dict[str, Any],
        origin: str,
        *,
        rp_id: str | None = None,
        kind: str = "webauthn.create",
        challenge: str | None = None,
        flags: int | None = None,
        user_verified: bool = True,
        user_present: bool = True,
        cose_key: bytes | None = None,
        fmt: str = "none",
        cross_origin: bool | None = None,
        transports: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Answer ``navigator.credentials.create()`` with a RegistrationResponseJSON.

        Args:
            options: The creation options, as the server sent them.
            origin: The page's origin.
            rp_id: The RP ID to hash, instead of the options' own.
            kind: The clientData type.
            challenge: A challenge to use instead of the options' own.
            flags: The flags byte, instead of this credential's own (AT added).
            user_verified: Report UV.
            user_present: Report UP.
            cose_key: A COSE key to attest instead of this credential's.
            fmt: The attestation format.
            cross_origin: Written into clientDataJSON when not None.
            transports: The transports reported.

        Returns:
            The response, as ``credential.toJSON()`` produces it.
        """
        self.rp_id = rp_id or options["rp"]["id"]
        self.user_handle = unb64url(options["user"]["id"])
        data = client_data(
            kind, challenge or options["challenge"], origin, cross_origin=cross_origin
        )
        byte = (
            flags
            if flags is not None
            else self.flags(user_verified=user_verified, user_present=user_present)
        )
        authenticator = auth_data(
            self.rp_id,
            byte | FLAG_AT,
            self.sign_count,
            attested=(self.aaguid, self.credential_id, cose_key or self.cose_key()),
        )
        attestation = cbor_encode({"fmt": fmt, "attStmt": {}, "authData": authenticator})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(data),
                "attestationObject": b64url(attestation),
                "transports": transports if transports is not None else ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(
        self,
        options: dict[str, Any],
        origin: str,
        *,
        rp_id: str | None = None,
        kind: str = "webauthn.get",
        challenge: str | None = None,
        user_verified: bool = True,
        user_present: bool = True,
        user_handle: bytes | bool | None = True,
        sign_count: int | None = None,
        tamper_signature: bool = False,
        tamper_client_data: bool = False,
        cross_origin: bool | None = None,
        flags: int | None = None,
    ) -> dict[str, Any]:
        """
        Answer ``navigator.credentials.get()`` with an AuthenticationResponseJSON.

        Args:
            options: The request options, as the server sent them.
            origin: The page's origin.
            rp_id: The RP ID to hash, instead of the one registered.
            kind: The clientData type.
            challenge: A challenge to use instead of the options' own.
            user_verified: Report UV.
            user_present: Report UP.
            user_handle: True for the registered handle, None or False to
                omit it, or bytes to send instead.
            sign_count: The counter to report, instead of the next one.
            tamper_signature: Flip one bit of the signature.
            tamper_client_data: Change clientDataJSON after signing.
            cross_origin: Written into clientDataJSON when not None.
            flags: The flags byte, instead of this credential's own.

        Returns:
            The response, as ``credential.toJSON()`` produces it.
        """
        if sign_count is None:
            self.sign_count += self.count_step
            count = self.sign_count
        else:
            count = sign_count
        data = client_data(
            kind, challenge or options["challenge"], origin, cross_origin=cross_origin
        )
        byte = (
            flags
            if flags is not None
            else self.flags(user_verified=user_verified, user_present=user_present)
        )
        authenticator = auth_data(rp_id or self.rp_id or options.get("rpId", ""), byte, count)
        signature = self.sign(authenticator + hashlib.sha256(data).digest())
        if tamper_signature:
            signature = signature[:-1] + bytes([signature[-1] ^ 0x01])
        if tamper_client_data:
            data = data.replace(b'"type"', b'"type" ')
        response: dict[str, Any] = {
            "clientDataJSON": b64url(data),
            "authenticatorData": b64url(authenticator),
            "signature": b64url(signature),
        }
        if user_handle is True:
            if self.user_handle is not None:
                response["userHandle"] = b64url(self.user_handle)
        elif isinstance(user_handle, bytes):
            response["userHandle"] = b64url(user_handle)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
