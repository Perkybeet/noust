# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A fake GitHub for the sign-in tests: OAuth 2.0 without OpenID Connect.

It answers the four things Noust asks of GitHub (the authorization page, the
token exchange, the person, their addresses and, when groups are wanted, their
organisations and teams) through the ``opener`` seam of
:class:`noust.core.accounts.oidc_client.OidcHttp`, so no socket is opened. It checks
what GitHub checks: the code is single use and belongs to the client that asked,
the redirect URI is the one the authorization used, PKCE's S256 verifier matches when
a challenge was sent, and the API wants the token as a bearer. Like the real one it
answers an error in the token exchange with status 200 and an ``error`` field.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from tests.oidc_support import FakeResponse


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@dataclass
class GitHubUser:
    """
    One person at GitHub.

    Attributes:
        id: The numeric id, which never changes (the login can).
        login: The handle.
        name: The display name, or None.
        public_email: The address on their profile, or None.
        emails: What ``/user/emails`` answers.
        orgs: Organisations they belong to.
        teams: ``(organisation, team slug)`` pairs.
    """

    id: int
    login: str
    name: str | None = None
    public_email: str | None = None
    emails: list[dict[str, Any]] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)
    teams: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Grant:
    """What the browser asked for, remembered until the code is spent."""

    client_id: str
    redirect_uri: str
    state: str
    challenge: str | None
    scope: str
    user: GitHubUser


class FakeGitHub:
    """
    github.com, or a GitHub Enterprise Server when ``host`` says so.

    Attributes:
        host: ``https://github.com`` or the Enterprise server's address.
        users: Login to person.
        requests: Every ``(method, url)`` asked, in order.
    """

    def __init__(
        self,
        *,
        host: str = "https://github.com",
        client_id: str = "Iv1.noust",
        client_secret: str = "gh-secret-value",  # noqa: S107 - a fake provider
    ) -> None:
        self.host = host.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.users: dict[str, GitHubUser] = {}
        self.requests: list[tuple[str, str]] = []
        self.grants: dict[str, Grant] = {}
        self.tokens: dict[str, GitHubUser] = {}
        self.offline = False
        self.exchange_error: str | None = None
        self.last_token_request: dict[str, str] = {}
        self.last_headers: dict[str, str] = {}

    # ------------------------------------------------------------ addresses

    @property
    def authorize_url(self) -> str:
        return f"{self.host}/login/oauth/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.host}/login/oauth/access_token"

    @property
    def api(self) -> str:
        return (
            "https://api.github.com" if self.host == "https://github.com" else f"{self.host}/api/v3"
        )

    def add_user(self, login: str, id: int, **fields: Any) -> GitHubUser:
        user = GitHubUser(id=id, login=login, **fields)
        self.users[login] = user
        return user

    # --------------------------------------------------------------- browser

    def authorize(self, url: str, login: str) -> str:
        """
        Play the person approving at GitHub.

        Args:
            url: Where Noust sent the browser.
            login: Who signs in.

        Returns:
            The callback URL, with a fresh code and the state it was given.
        """
        parsed = urlparse(url)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        assert parsed.geturl().startswith(self.authorize_url), url
        assert query["client_id"] == self.client_id
        code = secrets.token_urlsafe(12)
        self.grants[code] = Grant(
            client_id=query["client_id"],
            redirect_uri=query["redirect_uri"],
            state=query["state"],
            challenge=query.get("code_challenge"),
            scope=query.get("scope", ""),
            user=self.users[login],
        )
        self.last_authorize_query = query
        return f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"

    last_authorize_query: dict[str, str] = {}

    # ------------------------------------------------------------ the opener

    def opener(self, request: Any, timeout: float | None = None) -> FakeResponse:
        url = request.full_url
        method = request.get_method()
        self.requests.append((method, url))
        self.last_headers = {k.lower(): v for k, v in request.headers.items()}
        if self.offline:
            raise urllib.error.URLError("connection refused")
        path = url.split("?", 1)[0]
        if path == self.token_url and method == "POST":
            return self._token(request)
        if path == f"{self.api}/":
            return FakeResponse(json.dumps({"current_user_url": f"{self.api}/user"}).encode())
        if path.startswith(self.api + "/"):
            return self._api(request, path[len(self.api) :])
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)  # type: ignore[arg-type]

    def _token(self, request: Any) -> FakeResponse:
        form = {k: v[0] for k, v in parse_qs(request.data.decode()).items()}
        self.last_token_request = form
        if self.exchange_error:
            return FakeResponse(json.dumps({"error": self.exchange_error}).encode())
        if (
            form.get("client_id") != self.client_id
            or form.get("client_secret") != self.client_secret
        ):
            return FakeResponse(json.dumps({"error": "incorrect_client_credentials"}).encode())
        grant = self.grants.pop(form.get("code", ""), None)
        if grant is None:
            return FakeResponse(json.dumps({"error": "bad_verification_code"}).encode())
        if form.get("redirect_uri") != grant.redirect_uri:
            return FakeResponse(json.dumps({"error": "redirect_uri_mismatch"}).encode())
        if grant.challenge is not None:
            verifier = form.get("code_verifier", "")
            if _b64(hashlib.sha256(verifier.encode()).digest()) != grant.challenge:
                return FakeResponse(json.dumps({"error": "bad_verification_code"}).encode())
        token = "gho_" + secrets.token_hex(10)
        self.tokens[token] = grant.user
        return FakeResponse(
            json.dumps(
                {
                    "access_token": token,
                    "token_type": "bearer",
                    "scope": grant.scope.replace(" ", ","),
                }
            ).encode()
        )

    def _api(self, request: Any, path: str) -> FakeResponse:
        header = request.headers.get("Authorization", "")
        user = (
            self.tokens.get(header.removeprefix("Bearer ").strip())
            if header.startswith("Bearer ")
            else None
        )
        if user is None:
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "bad credentials",
                {},
                _Body(b'{"message":"Bad credentials"}'),  # type: ignore[arg-type]
            )
        if path == "/user":
            return self._json(
                {"id": user.id, "login": user.login, "name": user.name, "email": user.public_email}
            )
        if path == "/user/emails":
            return self._json(user.emails)
        if path == "/user/orgs":
            return self._json([{"login": org} for org in user.orgs])
        if path == "/user/teams":
            return self._json(
                [{"slug": slug, "organization": {"login": org}} for org, slug in user.teams]
            )
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)  # type: ignore[arg-type]

    @staticmethod
    def _json(body: Any) -> FakeResponse:
        return FakeResponse(json.dumps(body).encode())


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, amount: int = -1) -> bytes:
        return self._data if amount < 0 else self._data[:amount]

    def close(self) -> None:
        return None
