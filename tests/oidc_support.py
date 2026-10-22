# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake identity provider for the single sign-in tests.

Keys are generated here, on the spot, with ``cryptography``; nothing is a
fixture file. The provider speaks the three endpoints Noust uses (discovery,
JWKS, token, and userinfo when asked), checks what a real one checks (the
authorization code is single use, PKCE's S256 verifier matches, the redirect
URI is the one the authorization used, the client authenticates) and signs real
ID tokens. A test bends one thing at a time: ``idp.bend`` changes the claims of
the next token, ``idp.tamper`` its bytes.

It is reached through the ``opener`` seam of :class:`noust.core.accounts.oidc_client.OidcHttp`
(the same one the GitHub client has), so no socket is ever opened.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils


def b64(data: bytes) -> str:
    """
    Args:
        data: Raw bytes.

    Returns:
        Unpadded base64url.
    """
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def int_b64(value: int, size: int | None = None) -> str:
    """
    Args:
        value: A non-negative integer.
        size: Fixed width in bytes, for an elliptic-curve coordinate.

    Returns:
        Its big-endian base64url, as a JWK writes it.
    """
    width = size if size is not None else max(1, (value.bit_length() + 7) // 8)
    return b64(value.to_bytes(width, "big"))


class Clock:
    """A settable time source shared by the provider and the code under test."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SigningKey:
    """One key pair a provider signs with and publishes."""

    def __init__(self, algorithm: str = "RS256", key_id: str | None = None) -> None:
        self.algorithm = algorithm
        self.key_id = key_id or f"key-{secrets.token_hex(4)}"
        if algorithm.startswith(("RS", "PS")):
            self.private: Any = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        else:
            curve = {"ES256": ec.SECP256R1, "ES384": ec.SECP384R1, "ES512": ec.SECP521R1}[algorithm]
            self.private = ec.generate_private_key(curve())

    def jwk(self, **extra: Any) -> dict[str, Any]:
        """
        Returns:
            The public half as a JWK.
        """
        public = self.private.public_key().public_numbers()
        if self.algorithm.startswith(("RS", "PS")):
            body = {"kty": "RSA", "n": int_b64(public.n), "e": int_b64(public.e)}
        else:
            size = (public.curve.key_size + 7) // 8
            body = {
                "kty": "EC",
                "crv": {"secp256r1": "P-256", "secp384r1": "P-384", "secp521r1": "P-521"}[
                    public.curve.name
                ],
                "x": int_b64(public.x, size),
                "y": int_b64(public.y, size),
            }
        return {"kid": self.key_id, "use": "sig", "alg": self.algorithm, **body, **extra}

    def sign(self, signing_input: bytes, algorithm: str | None = None) -> bytes:
        """
        Args:
            signing_input: ``header.payload``.
            algorithm: Sign as this algorithm instead of the key's own.

        Returns:
            The JWS signature.
        """
        algorithm = algorithm or self.algorithm
        digest = {"256": hashes.SHA256, "384": hashes.SHA384, "512": hashes.SHA512}[algorithm[2:]]()
        if algorithm.startswith("RS"):
            return self.private.sign(signing_input, padding.PKCS1v15(), digest)
        if algorithm.startswith("PS"):
            pss = padding.PSS(mgf=padding.MGF1(digest), salt_length=digest.digest_size)
            return self.private.sign(signing_input, pss, digest)
        der = self.private.sign(signing_input, ec.ECDSA(digest))
        r, s = utils.decode_dss_signature(der)
        size = (self.private.curve.key_size + 7) // 8
        return r.to_bytes(size, "big") + s.to_bytes(size, "big")

    def private_pem(self) -> bytes:
        return self.private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )


def compact(header: dict[str, Any], claims: dict[str, Any], key: SigningKey, **sign: Any) -> str:
    """
    Args:
        header: The protected header.
        claims: The payload.
        key: What signs it.
        **sign: ``algorithm`` to sign with something other than the header says.

    Returns:
        The compact JWS.
    """
    signing_input = (
        b64(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64(json.dumps(claims, separators=(",", ":")).encode())
    )
    return signing_input + "." + b64(key.sign(signing_input.encode("ascii"), sign.get("algorithm")))


@dataclass
class AuthRequest:
    """What the browser asked the provider for, remembered until the code is spent."""

    client_id: str
    redirect_uri: str
    state: str
    nonce: str
    challenge: str
    scope: str
    sub: str = ""
    claims: dict[str, Any] = field(default_factory=dict)


class FakeResponse:
    """What ``urlopen`` returns: a body, readable in pieces, usable as a context."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, amount: int = -1) -> bytes:
        return self._body if amount < 0 else self._body[:amount]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeIdP:
    """
    An OpenID provider.

    Attributes:
        issuer: Its ``iss``.
        key: The key it signs with now.
        published: The keys its JWKS lists (the signing key, unless a test
            changes it).
        requests: Every ``(method, url)`` it was asked for, in order.
        users: ``sub`` to the claims it releases about that person.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        issuer: str = "https://idp.example.test/realms/acme",
        client_id: str = "noust-console",
        client_secret: str | None = "s3cret-value",  # noqa: S107 - a fake provider
        algorithm: str = "RS256",
    ) -> None:
        self.clock = clock or Clock()
        self.issuer = issuer
        self.client_id = client_id
        self.client_secret = client_secret
        self.key = SigningKey(algorithm)
        self.published: list[dict[str, Any]] = [self.key.jwk()]
        self.requests: list[tuple[str, str]] = []
        self.users: dict[str, dict[str, Any]] = {}
        self.codes: dict[str, AuthRequest] = {}
        self.bend: dict[str, Any] = {}
        self.tamper: Callable[[str], str] | None = None
        self.discovery_overrides: dict[str, Any] = {}
        self.offline = False
        self.token_status = 200
        self.userinfo: dict[str, Any] | None = None
        self.token_extra: dict[str, Any] = {}
        self.last_token_request: dict[str, str] = {}

    # ------------------------------------------------------------ addresses

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.issuer}/protocol/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/protocol/token"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/protocol/certs"

    @property
    def userinfo_endpoint(self) -> str:
        return f"{self.issuer}/protocol/userinfo"

    def discovery(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "issuer": self.issuer,
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "jwks_uri": self.jwks_uri,
            "userinfo_endpoint": self.userinfo_endpoint,
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": [self.key.algorithm],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        }
        document.update(self.discovery_overrides)
        return document

    # ------------------------------------------------------------- the user

    def add_user(self, sub: str, **claims: Any) -> None:
        """
        Args:
            sub: The person's subject identifier.
            **claims: What the provider releases about them (``email``,
                ``email_verified``, ``name``, ``groups``...).
        """
        self.users[sub] = claims

    def authorize(self, authorization_url: str, sub: str, **claims: Any) -> str:
        """
        Play the person approving at the provider: log in and consent.

        Args:
            authorization_url: Where Noust sent the browser.
            sub: Who signs in.
            **claims: Claims for this sign-in on top of the person's own.

        Returns:
            The callback URL the provider sends the browser to, with a fresh
            code and the state it was given.
        """
        parsed = urlparse(authorization_url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        assert parsed.geturl().startswith(self.authorization_endpoint), authorization_url
        assert query["response_type"] == "code"
        assert query["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self.codes[code] = AuthRequest(
            client_id=query["client_id"],
            redirect_uri=query["redirect_uri"],
            state=query["state"],
            nonce=query["nonce"],
            challenge=query["code_challenge"],
            scope=query.get("scope", ""),
            sub=sub,
            claims={**self.users.get(sub, {}), **claims},
        )
        return f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"

    def deny(self, authorization_url: str, error: str = "access_denied") -> str:
        """
        Returns:
            The callback URL of a person who refused, or whom the provider refused.
        """
        query = {
            key: values[0] for key, values in parse_qs(urlparse(authorization_url).query).items()
        }
        return f"{query['redirect_uri']}?{urlencode({'error': error, 'state': query['state']})}"

    # --------------------------------------------------------------- tokens

    def claims_for(self, request: AuthRequest) -> dict[str, Any]:
        now = int(self.clock())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": request.sub,
            "aud": request.client_id,
            "exp": now + 300,
            "iat": now,
            "nonce": request.nonce,
            **request.claims,
        }
        claims.update(self.bend)
        return {key: value for key, value in claims.items() if value is not _DROP}

    def id_token(self, request: AuthRequest) -> str:
        header = {"alg": self.key.algorithm, "typ": "JWT", "kid": self.key.key_id}
        header.update(self.bend.get("__header__", {}))
        claims = {k: v for k, v in self.claims_for(request).items() if k != "__header__"}
        token = compact(header, claims, self.key)
        return self.tamper(token) if self.tamper else token

    # ------------------------------------------------------------ the opener

    def opener(self, request: Any, timeout: float | None = None) -> FakeResponse:
        """
        The ``urlopen`` stand-in: answers the provider's endpoints.

        Args:
            request: A :class:`urllib.request.Request`.
            timeout: Ignored.

        Returns:
            The response.

        Raises:
            urllib.error.URLError: While ``offline``.
            urllib.error.HTTPError: For an unknown address or an error status.
        """
        url = request.full_url
        method = request.get_method()
        self.requests.append((method, url))
        if self.offline:
            raise urllib.error.URLError("connection refused")
        path = url.split("?", 1)[0]
        if path == f"{self.issuer.rstrip('/')}/.well-known/openid-configuration":
            return self._json(self.discovery())
        if path == self.jwks_uri:
            return self._json({"keys": self.published})
        if path == self.token_endpoint and method == "POST":
            return self._token(request)
        if path == self.userinfo_endpoint:
            return self._userinfo(request)
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)  # type: ignore[arg-type]

    @staticmethod
    def _json(body: Any, status: int = 200) -> FakeResponse:
        return FakeResponse(json.dumps(body).encode(), status)

    def _refuse(self, request: Any, status: int, error: str) -> None:
        raise urllib.error.HTTPError(
            request.full_url,
            status,
            error,
            {},  # type: ignore[arg-type]
            _BytesBody(json.dumps({"error": error}).encode()),  # type: ignore[arg-type]
        )

    def _token(self, request: Any) -> FakeResponse:
        form = {k: v[0] for k, v in parse_qs(request.data.decode()).items()}
        self.last_token_request = form
        header = request.headers.get("Authorization", "")
        if self.client_secret is not None:
            if header.startswith("Basic "):
                given = base64.b64decode(header[6:]).decode()
                from urllib.parse import quote_plus

                if given != f"{quote_plus(self.client_id)}:{quote_plus(self.client_secret)}":
                    self._refuse(request, 401, "invalid_client")
            elif form.get("client_secret") != self.client_secret or form.get("client_id") != (
                self.client_id
            ):
                self._refuse(request, 401, "invalid_client")
        elif form.get("client_id") != self.client_id:
            self._refuse(request, 401, "invalid_client")
        if self.token_status != 200:
            self._refuse(request, self.token_status, "server_error")
        if form.get("grant_type") != "authorization_code":
            self._refuse(request, 400, "unsupported_grant_type")
        auth = self.codes.pop(form.get("code", ""), None)  # single use
        if auth is None:
            self._refuse(request, 400, "invalid_grant")
        assert auth is not None
        if form.get("redirect_uri") != auth.redirect_uri:
            self._refuse(request, 400, "invalid_grant")
        verifier = form.get("code_verifier", "")
        if b64(hashlib.sha256(verifier.encode()).digest()) != auth.challenge:
            self._refuse(request, 400, "invalid_grant")
        body: dict[str, Any] = {
            "access_token": "access-" + secrets.token_hex(8),
            "token_type": "Bearer",
            "expires_in": 300,
            "id_token": self.id_token(auth),
            **self.token_extra,
        }
        if self.userinfo is None:
            self._userinfo_claims = {"sub": auth.sub, **auth.claims}
        return self._json(body)

    _userinfo_claims: dict[str, Any] = {}

    def _userinfo(self, request: Any) -> FakeResponse:
        if not request.headers.get("Authorization", "").startswith("Bearer access-"):
            self._refuse(request, 401, "invalid_token")
        return self._json(self.userinfo if self.userinfo is not None else self._userinfo_claims)


class _BytesBody:
    """A readable body for an ``HTTPError``."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, amount: int = -1) -> bytes:
        return self._data if amount < 0 else self._data[:amount]

    def close(self) -> None:
        return None


#: Put in ``idp.bend`` to leave a claim out of the next token.
_DROP = object()
DROP = _DROP
