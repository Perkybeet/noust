# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Talking to an OpenID Connect provider, and judging what it answers.

Three jobs, each in one place:

- **Reaching it**: a small JSON client over :mod:`urllib`, like the GitHub one
  (:mod:`noust.integrations.github.client`): no new dependency, a timeout and a
  size limit on every request, HTTPS only (loopback excepted), and no redirect
  followed, because an endpoint that redirects is not one Noust discovered.
- **Knowing it**: the discovery document and the key set, cached, with the key
  set refreshed when a token names a key it does not have (the provider rotated)
  but never more than once a minute, so a stream of forged tokens cannot make
  Noust fetch without end.
- **Judging a token**: issuer, audience, expiry and nonce, after the signature
  (:mod:`noust.core.accounts.oidc_jose`). A clock skew is tolerated both ways and
  no further.

What the token entitles the person to is not decided here: that is
:mod:`noust.core.accounts.oidc`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import IO, Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from noust import __version__
from noust.core.accounts.oidc_jose import (
    SUPPORTED_ALGORITHMS,
    JwtError,
    VerifiedJwt,
    verify_jws,
)
from noust.core.accounts.oidc_model import (
    IdentityClaims,
    OidcError,
    OidcProvider,
    check_endpoint_url,
)
from noust.core.exceptions import DependencyError, ValidationError

logger = logging.getLogger(__name__)

#: Seconds before a request to the provider is abandoned. The person is waiting
#: in a browser tab, and a console worker is waiting with them.
REQUEST_TIMEOUT = 10.0
#: Bytes of an answer read at most. A key set or a discovery document is kilobytes.
MAX_RESPONSE_BYTES = 1024 * 1024
#: How long a discovery document and a key set are trusted without asking again.
CACHE_SECONDS = 3600.0
#: The shortest time between two forced refreshes of one key set.
MIN_REFRESH_SECONDS = 60.0
#: Longest ``sub`` OpenID Connect allows (core 2.0, section 2).
MAX_SUBJECT = 255
#: Most groups read from a token, and longest group name.
MAX_GROUPS = 500
MAX_GROUP_NAME = 256
#: How long a verified ID token is accepted after it was issued, whatever its ``exp``.
MAX_TOKEN_AGE = 24 * 3600

Opener = Callable[..., IO[bytes]]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect: the answer of a 3xx is an error, not another request."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class OidcHttp:
    """
    JSON over HTTPS to the endpoints of one provider.

    Args:
        timeout: Seconds per request.
        opener: Replacement for ``urlopen``'s calling convention
            (``opener(request, timeout=...)``); the tests' fake provider.
    """

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT, opener: Opener | None = None) -> None:
        self.timeout = timeout
        self._opener: Opener = opener or urllib.request.build_opener(_NoRedirects).open

    def get_json(self, url: str, *, bearer: str | None = None) -> dict[str, Any]:
        """
        Args:
            url: An endpoint the provider published.
            bearer: An access token to send, for the userinfo endpoint.

        Returns:
            The JSON object it answered.

        Raises:
            OidcError: The provider is unreachable, answered an error status,
                or answered something that is not a JSON object.
        """
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        return self._send(urllib.request.Request(url, headers=headers, method="GET"))

    def post_form(
        self, url: str, form: Mapping[str, str], *, basic: tuple[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Args:
            url: The token endpoint.
            form: The form fields.
            basic: A client id and secret to authenticate with HTTP Basic.

        Returns:
            The JSON object it answered.

        Raises:
            OidcError: As :meth:`get_json`; the provider's own ``error`` code
                is in the reason of a refusal.
        """
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if basic is not None:
            # RFC 6749, section 2.3.1: both halves are form-encoded before Basic.
            pair = f"{quote_plus(basic[0])}:{quote_plus(basic[1])}"
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode("ascii")
        request = urllib.request.Request(
            url, data=urlencode(dict(form)).encode(), headers=headers, method="POST"
        )
        return self._send(request)

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        """
        Args:
            request: The request, with its URL already checked.

        Returns:
            The decoded JSON object.

        Raises:
            OidcError: ``idp_unreachable``, ``idp_<status>``, ``token_<error>``
                for an OAuth error answer, or ``idp_bad_response``.
        """
        request.add_header("User-Agent", f"noust/{__version__}")
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise self._refusal(exc) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            raise OidcError(
                "idp_unreachable",
                "The identity provider could not be reached",
                details=(
                    "Check this server's outbound HTTPS access to the provider "
                    f"({urlsplit(request.full_url).hostname}). It said: {reason}"
                ),
                public="unavailable",
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OidcError("idp_bad_response", "The identity provider's answer is too large")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise OidcError(
                "idp_bad_response", "The identity provider did not answer JSON"
            ) from None
        if not isinstance(body, dict):
            raise OidcError("idp_bad_response", "The identity provider did not answer an object")
        return body

    @staticmethod
    def _refusal(exc: HTTPError) -> OidcError:
        """
        Args:
            exc: urllib's error, carrying the response.

        Returns:
            The error to raise, with the provider's ``error`` code when it gave one.
        """
        code = ""
        try:
            body = json.loads(exc.read(64 * 1024) or b"{}")
            if isinstance(body, dict):
                code = "".join(
                    char for char in str(body.get("error") or "") if char.isalnum() or char in "_-"
                )[:48]
        except (OSError, ValueError, AttributeError):
            code = ""
        return OidcError(
            f"token_{code}" if code else f"idp_http_{exc.code}",
            f"The identity provider refused the request ({exc.code})",
            details=f"Its error: {code}" if code else "",
        )


@dataclass(frozen=True)
class Discovery:
    """
    What a provider's ``.well-known/openid-configuration`` says Noust needs.

    Attributes:
        issuer: Its issuer, equal to the configured one.
        authorization_endpoint: Where the browser is sent.
        token_endpoint: Where the code is exchanged.
        jwks_uri: Where the signing keys are.
        userinfo_endpoint: Where the person's claims can be asked for, if anywhere.
        signing_algorithms: The ``alg`` values its ID tokens use; empty when it does not say.
        token_auth_methods: How it lets a client authenticate; empty when it does not say.
    """

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None = None
    signing_algorithms: tuple[str, ...] = ()
    token_auth_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenResponse:
    """
    What the token endpoint gave in exchange for the code.

    Attributes:
        id_token: The signed ID token.
        access_token: The access token, used only to ask userinfo, never kept.
    """

    id_token: str
    access_token: str | None


def pkce_challenge(verifier: str) -> str:
    """
    Args:
        verifier: A PKCE code verifier.

    Returns:
        Its S256 challenge (RFC 7636): the digest, unpadded base64url.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def new_pkce_verifier() -> str:
    """
    Returns:
        A fresh verifier of 64 characters (RFC 7636 asks for 43 to 128).
    """
    return secrets.token_urlsafe(48)


def _texts(value: Any) -> tuple[str, ...]:
    """
    Args:
        value: A JSON value that should list strings.

    Returns:
        The strings; a lone string is a list of one.
    """
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _number(value: Any) -> float | None:
    """
    Args:
        value: A JSON value that should be a number.

    Returns:
        It, or None for a bool, a string or anything else (a ``true`` is not 1).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _verified_flag(value: Any) -> bool | None:
    """
    Args:
        value: ``email_verified`` as a provider wrote it.

    Returns:
        True or False; None when it says nothing. Some providers write the
        boolean as the string ``"true"``; nothing else counts as verified.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _printable(value: Any, limit: int) -> str | None:
    """
    Args:
        value: A claim that should be a short piece of text.
        limit: The longest kept.

    Returns:
        The text without control characters, trimmed and cut; None when there is none.
    """
    if not isinstance(value, str):
        return None
    text = "".join(char for char in value if char.isprintable()).strip()[:limit]
    return text or None


def claim_at(claims: Mapping[str, Any], path: str) -> Any:
    """
    Args:
        claims: A token's claims.
        path: A claim, or nested claims joined by ``>`` (``realm_access>roles``).

    Returns:
        What is there, or None.
    """
    node: Any = claims
    for segment in path.split(">"):
        if not isinstance(node, Mapping) or segment not in node:
            return None
        node = node[segment]
    return node


def identity_from_claims(provider: OidcProvider, claims: Mapping[str, Any]) -> IdentityClaims:
    """
    Read the person out of the claims of a verified token.

    Args:
        provider: The provider, for the groups claim.
        claims: The token's claims, with the userinfo merged in when there was one.

    Returns:
        The identity. Nothing is trusted that is not the right type.
    """
    email = claims.get("email")
    clean_email = email.strip().lower() if isinstance(email, str) and email.strip() else None
    groups = tuple(
        group
        for group in _texts(claim_at(claims, provider.groups_claim))[:MAX_GROUPS]
        if 0 < len(group) <= MAX_GROUP_NAME
    )
    name = _printable(claims.get("name"), 128)
    username = _printable(claims.get("preferred_username"), 128)
    acr = claims.get("acr")
    return IdentityClaims(
        issuer=str(claims["iss"]),
        subject=str(claims["sub"]),
        email=clean_email if clean_email and len(clean_email) <= 254 else None,
        email_verified=_verified_flag(claims.get("email_verified")),
        name=name,
        username=username,
        groups=groups,
        amr=tuple(item[:32] for item in _texts(claims.get("amr"))[:16]),
        acr=acr[:64] if isinstance(acr, str) else None,
    )


class OidcClient:
    """
    The provider-facing half of single sign-in, with its caches.

    Args:
        http: How to reach providers; the real one by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(self, http: OidcHttp | None = None, *, clock: Callable[[], float] | None = None):
        self.http = http or OidcHttp()
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._discovery: dict[str, tuple[Discovery, float]] = {}
        self._keys: dict[str, tuple[list[dict[str, Any]], float, float]] = {}

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    # ----------------------------------------------------------- discovery

    def discovery(self, provider: OidcProvider, *, refresh: bool = False) -> Discovery:
        """
        Read the provider's discovery document, or the cached one.

        Args:
            provider: The provider.
            refresh: Ask again whatever the cache holds (``noust auth oidc test``).

        Returns:
            What Noust needs of it.

        Raises:
            OidcError: ``idp_unreachable`` or ``idp_bad_response``;
                ``issuer_mismatch`` when the document's issuer is not the
                configured one (the first thing to check when it is wrong);
                ``discovery_invalid`` when an endpoint is missing or not an
                address Noust will use.
        """
        with self._lock:
            cached = self._discovery.get(provider.issuer)
        if cached is not None and not refresh and self.now() - cached[1] < CACHE_SECONDS:
            return cached[0]
        url = provider.issuer.rstrip("/") + "/.well-known/openid-configuration"
        document = self.http.get_json(url)
        found = self._parse_discovery(provider, document)
        with self._lock:
            self._discovery[provider.issuer] = (found, self.now())
        return found

    @staticmethod
    def _parse_discovery(provider: OidcProvider, document: Mapping[str, Any]) -> Discovery:
        """
        Args:
            provider: The provider the document was fetched for.
            document: The discovery document.

        Returns:
            The checked endpoints.

        Raises:
            OidcError: ``issuer_mismatch`` or ``discovery_invalid``.
        """
        if document.get("issuer") != provider.issuer:
            raise OidcError(
                "issuer_mismatch",
                "The provider's issuer is not the one configured",
                details=(
                    f"Configured {provider.issuer!r}, the provider says "
                    f"{str(document.get('issuer'))[:200]!r}. Set the issuer exactly as the "
                    "provider writes it (some end in a slash)."
                ),
            )
        endpoints: dict[str, str] = {}
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = document.get(key)
            if not isinstance(value, str):
                raise OidcError(
                    "discovery_invalid", f"The provider's discovery document has no {key}"
                )
            try:
                endpoints[key] = check_endpoint_url(value, key)
            except ValidationError as exc:
                raise OidcError("discovery_invalid", exc.message, details=exc.details) from exc
        methods = _texts(document.get("code_challenge_methods_supported"))
        if methods and "S256" not in methods:
            raise OidcError(
                "pkce_unsupported",
                "The provider does not support PKCE with S256",
                details="Noust only signs in through the authorization code flow with PKCE.",
            )
        userinfo = document.get("userinfo_endpoint")
        userinfo_url: str | None = None
        if isinstance(userinfo, str):
            try:
                userinfo_url = check_endpoint_url(userinfo, "userinfo_endpoint")
            except ValidationError:
                userinfo_url = None
        return Discovery(
            issuer=provider.issuer,
            authorization_endpoint=endpoints["authorization_endpoint"],
            token_endpoint=endpoints["token_endpoint"],
            jwks_uri=endpoints["jwks_uri"],
            userinfo_endpoint=userinfo_url,
            signing_algorithms=_texts(document.get("id_token_signing_alg_values_supported")),
            token_auth_methods=_texts(document.get("token_endpoint_auth_methods_supported")),
        )

    # ---------------------------------------------------------------- keys

    def keys(self, discovery: Discovery, *, force: bool = False) -> list[dict[str, Any]]:
        """
        Read the provider's key set, or the cached one.

        Args:
            discovery: The provider's discovery.
            force: Ask again because a token named a key the cache lacks. Honoured
                at most once per :data:`MIN_REFRESH_SECONDS`; a forced call inside
                that window gets the cache.

        Returns:
            The keys of the set.

        Raises:
            OidcError: ``idp_unreachable`` or ``idp_bad_response``, or
                ``jwks_invalid`` when the set has no keys.
        """
        now = self.now()
        with self._lock:
            cached = self._keys.get(discovery.jwks_uri)
        if cached is not None:
            keys, fetched, forced_at = cached
            fresh = now - fetched < CACHE_SECONDS
            if (fresh and not force) or (force and now - forced_at < MIN_REFRESH_SECONDS):
                return keys
        body = self.http.get_json(discovery.jwks_uri)
        raw = body.get("keys")
        keys = [key for key in raw if isinstance(key, dict)] if isinstance(raw, list) else []
        if not keys:
            raise OidcError("jwks_invalid", "The provider published no signing keys")
        with self._lock:
            self._keys[discovery.jwks_uri] = (keys, now, now if force else 0.0)
        return keys

    # ---------------------------------------------------------- the browser

    @staticmethod
    def authorization_url(
        provider: OidcProvider,
        discovery: Discovery,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        verifier: str,
    ) -> str:
        """
        Build where the browser is sent to sign in.

        Args:
            provider: The provider.
            discovery: Its discovery.
            redirect_uri: This console's callback, as the provider registered it.
            state: The value the provider echoes back.
            nonce: The value the ID token must carry.
            verifier: The PKCE verifier whose challenge is sent.

        Returns:
            The authorization URL.
        """
        parts = urlsplit(discovery.authorization_endpoint)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query += [
            ("response_type", "code"),
            ("client_id", provider.client_id),
            ("redirect_uri", redirect_uri),
            ("scope", provider.scopes),
            ("state", state),
            ("nonce", nonce),
            ("code_challenge", pkce_challenge(verifier)),
            ("code_challenge_method", "S256"),
        ]
        return urlunsplit(parts._replace(query=urlencode(query)))

    # ------------------------------------------------------------ the token

    def exchange_code(
        self,
        provider: OidcProvider,
        discovery: Discovery,
        secret: str | None,
        *,
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> TokenResponse:
        """
        Trade the authorization code for the ID token.

        Args:
            provider: The provider.
            discovery: Its discovery.
            secret: The client secret; None for a public client (PKCE alone).
            code: The authorization code the provider sent back.
            redirect_uri: The callback the authorization used, repeated exactly.
            verifier: The PKCE verifier.

        Returns:
            The tokens.

        Raises:
            OidcError: The provider refused the code (``token_invalid_grant``...),
                is unreachable, or answered without an ID token.
        """
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        basic: tuple[str, str] | None = None
        if secret:
            methods = discovery.token_auth_methods
            if (
                not methods
                or "client_secret_basic" in methods
                or "client_secret_post" not in methods
            ):
                basic = (provider.client_id, secret)
            else:
                form["client_id"] = provider.client_id
                form["client_secret"] = secret
        else:
            form["client_id"] = provider.client_id
        body = self.http.post_form(discovery.token_endpoint, form, basic=basic)
        id_token = body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcError("no_id_token", "The provider answered without an ID token")
        access = body.get("access_token")
        return TokenResponse(id_token, access if isinstance(access, str) and access else None)

    def userinfo(self, discovery: Discovery, access_token: str) -> dict[str, Any]:
        """
        Ask the provider for the claims the ID token left out.

        Args:
            discovery: Its discovery, which must have a userinfo endpoint.
            access_token: The access token of this sign-in.

        Returns:
            The claims it answered.

        Raises:
            OidcError: The provider could not be asked.
        """
        if discovery.userinfo_endpoint is None:
            raise OidcError("no_userinfo", "The provider has no userinfo endpoint")
        return self.http.get_json(discovery.userinfo_endpoint, bearer=access_token)

    # ---------------------------------------------------------------- judge

    def verify_id_token(
        self, provider: OidcProvider, discovery: Discovery, token: str, *, nonce: str
    ) -> dict[str, Any]:
        """
        Believe an ID token, or say why not.

        The signature first, against the provider's published keys (refreshing
        them once when the token names a key that is not cached); then the
        issuer, the audience, the expiry and the nonce. The claims are returned
        only when every check passed.

        Args:
            provider: The provider the sign-in was started with.
            discovery: Its discovery.
            token: The ID token.
            nonce: The nonce of this sign-in.

        Returns:
            The token's claims.

        Raises:
            OidcError: ``token_<reason>``: ``malformed``, ``algorithm``,
                ``unknown_key``, ``key``, ``signature``, ``issuer``,
                ``audience``, ``authorized_party``, ``expired``, ``not_yet_valid``,
                ``issued_in_future``, ``nonce`` or ``subject``.
        """
        allowed = tuple(
            alg
            for alg in SUPPORTED_ALGORITHMS
            if not discovery.signing_algorithms or alg in discovery.signing_algorithms
        )
        try:
            verified = self._verified(discovery, token, allowed)
        except DependencyError:
            raise
        except JwtError as exc:
            raise OidcError(
                f"token_{exc.reason}",
                "The provider's ID token was refused",
                details=exc.message,
                public="failed",
            ) from exc
        self._check_claims(provider, verified, nonce)
        return verified.claims

    def _verified(self, discovery: Discovery, token: str, allowed: tuple[str, ...]) -> VerifiedJwt:
        """
        Args:
            discovery: The provider's discovery.
            token: The ID token.
            allowed: The algorithms acceptable for it.

        Returns:
            The verified token.

        Raises:
            JwtError: When no key, cached or refreshed, verifies it.
        """
        try:
            return verify_jws(token, self.keys(discovery), algorithms=allowed)
        except JwtError as exc:
            if exc.reason not in ("unknown_key", "key", "signature"):
                raise
            # A provider that rotated its keys signs with one the cache lacks;
            # one refresh (itself rate limited) settles it.
            refreshed = self.keys(discovery, force=True)
        return verify_jws(token, refreshed, algorithms=allowed)

    def _check_claims(self, provider: OidcProvider, verified: VerifiedJwt, nonce: str) -> None:
        """
        Args:
            provider: The provider.
            verified: The token whose signature checked out.
            nonce: The nonce of this sign-in.

        Raises:
            OidcError: ``token_<reason>`` for the first claim that is not right.
        """
        claims = verified.claims
        now = self.now()
        skew = provider.clock_skew_seconds

        def refuse(reason: str, message: str) -> OidcError:
            return OidcError(
                f"token_{reason}", "The provider's ID token was refused", details=message
            )

        if claims.get("iss") != provider.issuer:
            raise refuse("issuer", "The token's issuer is not the configured provider's")
        audience = claims.get("aud")
        audiences = _texts(audience)
        if provider.client_id not in audiences:
            raise refuse("audience", "The token was not issued for this application")
        authorized = claims.get("azp")
        if (len(audiences) > 1 or authorized is not None) and authorized != provider.client_id:
            raise refuse("authorized_party", "The token was authorized for another client")
        expires = _number(claims.get("exp"))
        if expires is None or expires + skew <= now:
            raise refuse("expired", "The token has expired")
        issued = _number(claims.get("iat"))
        if claims.get("iat") is not None and (issued is None or issued - skew > now):
            raise refuse("issued_in_future", "The token says it was issued in the future")
        if issued is not None and now - issued > MAX_TOKEN_AGE:
            raise refuse("expired", "The token was issued too long ago")
        if "nbf" in claims:
            not_before = _number(claims.get("nbf"))
            if not_before is None or not_before - skew > now:
                raise refuse("not_yet_valid", "The token is not valid yet")
        given = claims.get("nonce")
        if not isinstance(given, str) or not hmac.compare_digest(
            given.encode("utf-8"), nonce.encode("utf-8")
        ):
            raise refuse("nonce", "The token does not answer this sign-in")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not 0 < len(subject) <= MAX_SUBJECT:
            raise refuse("subject", "The token has no usable subject")

    @staticmethod
    def merge_userinfo(claims: Mapping[str, Any], info: Mapping[str, Any]) -> dict[str, Any]:
        """
        Complete the ID token's claims with the provider's userinfo.

        Only what the token left out is taken, and only when the userinfo is
        about the same person (the same ``sub``): the token stays the authority.

        Args:
            claims: The verified token's claims.
            info: What userinfo answered.

        Returns:
            The claims, completed; the token's own when the subject differs.
        """
        if info.get("sub") != claims.get("sub"):
            logger.warning("Userinfo answered for another subject than the ID token; ignored")
            return dict(claims)
        merged = dict(claims)
        for key, value in info.items():
            if key not in merged:
                merged[key] = value
        return merged


_client: OidcClient | None = None
_client_lock = threading.Lock()


def get_client() -> OidcClient:
    """
    Returns:
        The process-wide client, whose caches are shared by every sign-in.
    """
    global _client
    with _client_lock:
        if _client is None:
            _client = OidcClient()
        return _client


def set_client(client: OidcClient | None) -> None:
    """
    Replace the process-wide client, for the tests' fake provider.

    Args:
        client: The client, or None to build a real one on next use.
    """
    global _client
    with _client_lock:
        _client = client
