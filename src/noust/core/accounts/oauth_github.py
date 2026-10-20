# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Signing in with GitHub: OAuth 2.0 without OpenID Connect (checklist I-16).

GitHub gives no ID token, no discovery document and no signing keys. What it gives is the
authorization-code flow and an API, so the pieces of an OpenID sign-in that can be taken as
given there have to be done here instead:

- **The endpoints are fixed**, derived from one address: ``https://github.com``, or the
  GitHub Enterprise Server the operator names. They are never read from a document a
  network could rewrite.
- **The person is read from the API** with the access token the code buys, and the token is
  forgotten as soon as it has been used: it is never stored, logged or put in a ticket.
- **The subject is GitHub's numeric id**, which never changes. A login can be renamed and then
  taken by somebody else, so it is only ever a hint for a new account's name.
- **Only an address GitHub says is verified is believed**, and only the primary one: the
  public address of a profile is whatever its owner typed, so it is ignored.
- **Groups** (when the provider maps them) are the organisations the person belongs to
  (``acme``) and their teams (``acme/web``), which needs the ``read:org`` scope.

Everything after that, who may come in and as whom, is the admission every identity source
goes through (:mod:`noust.core.accounts.oidc`); this module only turns GitHub's answers into
the :class:`~noust.core.accounts.oidc_model.IdentityClaims` that admission judges.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from noust.core.accounts.oidc_client import MAX_GROUP_NAME, MAX_GROUPS, OidcHttp, pkce_challenge
from noust.core.accounts.oidc_model import IdentityClaims, OidcError, OidcProvider

logger = logging.getLogger(__name__)

#: The GitHub every provider of this kind talks to unless it names an Enterprise Server.
GITHUB_HOST = "https://github.com"

#: What a sign-in asks for: the person's profile and their addresses. Nothing that writes.
BASE_SCOPES = "read:user user:email"

#: What reading organisations and teams adds.
GROUP_SCOPE = "read:org"

#: Sent with every API call, as GitHub asks.
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

#: Most organisations and teams read: a person belongs to a handful, and the page is a hundred.
PAGE = 100


def scopes_for(group_sync: bool) -> str:
    """
    Args:
        group_sync: Whether the provider maps the person's organisations and teams.

    Returns:
        The scopes the sign-in asks GitHub for.
    """
    return f"{BASE_SCOPES} {GROUP_SCOPE}" if group_sync else BASE_SCOPES


@dataclass(frozen=True)
class Endpoints:
    """
    Where a GitHub is.

    Attributes:
        authorize: Where the browser is sent.
        token: Where the code is exchanged.
        api: The API's base address, without a trailing slash.
    """

    authorize: str
    token: str
    api: str


def endpoints(host: str) -> Endpoints:
    """
    Args:
        host: ``https://github.com`` or the address of a GitHub Enterprise Server.

    Returns:
        Its three addresses.
    """
    base = host.rstrip("/")
    if urlsplit(base).hostname == "github.com":
        api = "https://api.github.com"
    else:
        api = f"{base}/api/v3"
    return Endpoints(f"{base}/login/oauth/authorize", f"{base}/login/oauth/access_token", api)


class GithubLogin:
    """
    The provider-facing half of a sign-in with GitHub.

    Args:
        http: How GitHub is reached (the OpenID client's, so one timeout, one size limit,
            no redirects followed and one fake in the tests).
    """

    def __init__(self, http: OidcHttp) -> None:
        self.http = http

    @staticmethod
    def authorization_url(
        provider: OidcProvider, *, redirect_uri: str, state: str, verifier: str
    ) -> str:
        """
        Build where the browser is sent to sign in.

        Args:
            provider: The provider.
            redirect_uri: This console's callback, as the application registered it.
            state: The value GitHub echoes back.
            verifier: The PKCE verifier whose challenge is sent.

        Returns:
            The authorization URL. ``allow_signup=false``: signing in here never makes
            somebody a GitHub account.
        """
        query = urlencode(
            [
                ("client_id", provider.client_id),
                ("redirect_uri", redirect_uri),
                ("scope", provider.scopes),
                ("state", state),
                ("code_challenge", pkce_challenge(verifier)),
                ("code_challenge_method", "S256"),
                ("allow_signup", "false"),
            ]
        )
        return f"{endpoints(provider.issuer).authorize}?{query}"

    def exchange_code(
        self, provider: OidcProvider, secret: str, *, code: str, redirect_uri: str, verifier: str
    ) -> str:
        """
        Trade the authorization code for an access token.

        Args:
            provider: The provider.
            secret: The application's client secret.
            code: The code GitHub sent back.
            redirect_uri: The callback the authorization used, repeated exactly.
            verifier: The PKCE verifier.

        Returns:
            The access token, to be used once and dropped.

        Raises:
            OidcError: GitHub refused the code (it answers 200 with an ``error`` field),
                is unreachable, or answered without a token.
        """
        body = self.http.post_form(
            endpoints(provider.issuer).token,
            {
                "client_id": provider.client_id,
                "client_secret": secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        refusal = body.get("error")
        if isinstance(refusal, str) and refusal:
            clean = "".join(char for char in refusal if char.isalnum() or char in "_-")[:48]
            raise OidcError(
                f"token_{clean}",
                "GitHub refused the code",
                details=f"Its error: {clean}",
            )
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise OidcError("no_access_token", "GitHub answered without an access token")
        return token

    def identity(self, provider: OidcProvider, token: str) -> IdentityClaims:
        """
        Ask GitHub who the token belongs to.

        Args:
            provider: The provider.
            token: The access token of this sign-in.

        Returns:
            The person: GitHub's numeric id as the subject, the primary verified
            address (if there is one), the name and handle, and the organisations and
            teams when the provider maps groups.

        Raises:
            OidcError: GitHub could not be asked, or did not answer what it should.
        """
        api = endpoints(provider.issuer).api
        user = self.http.get_json(f"{api}/user", bearer=token, headers=API_HEADERS)
        identifier = user.get("id")
        # bool is an int in Python; an id is a positive number and nothing else.
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
            raise OidcError("idp_bad_response", "GitHub did not say who this is")
        email = self._verified_primary(api, token)
        return IdentityClaims(
            issuer=provider.issuer,
            subject=str(identifier),
            email=email,
            email_verified=True if email else None,
            name=_text(user.get("name"), 128),
            username=_text(user.get("login"), 128),
            groups=self._groups(api, token) if provider.group_sync else (),
        )

    def _verified_primary(self, api: str, token: str) -> str | None:
        """
        Args:
            api: The API's base address.
            token: The access token.

        Returns:
            The primary address GitHub has verified, lower-case; None when it has
            none, or when the token was not allowed to read addresses.
        """
        try:
            entries = self.http.get_list(f"{api}/user/emails", bearer=token, headers=API_HEADERS)
        except OidcError as exc:
            # A token without user:email (an application registered without it) gets a
            # 404: the person simply has no address Noust can believe.
            logger.info("GitHub addresses could not be read: %s", exc.reason)
            return None
        for entry in entries:
            if (
                isinstance(entry, Mapping)
                and entry.get("primary") is True
                and entry.get("verified") is True
            ):
                address = entry.get("email")
                if isinstance(address, str) and address.strip():
                    value = address.strip().lower()
                    return value if len(value) <= 254 else None
        return None

    def _groups(self, api: str, token: str) -> tuple[str, ...]:
        """
        Args:
            api: The API's base address.
            token: The access token.

        Returns:
            The organisations the person is in (``acme``) and their teams
            (``acme/web``); nothing when the token may not read them.
        """
        groups: list[str] = []
        try:
            organisations = self.http.get_list(
                f"{api}/user/orgs?per_page={PAGE}", bearer=token, headers=API_HEADERS
            )
            teams = self.http.get_list(
                f"{api}/user/teams?per_page={PAGE}", bearer=token, headers=API_HEADERS
            )
        except OidcError as exc:
            logger.info("GitHub organisations could not be read: %s", exc.reason)
            return ()
        for entry in organisations:
            if isinstance(entry, Mapping):
                login = _text(entry.get("login"), MAX_GROUP_NAME)
                if login:
                    groups.append(login)
        for entry in teams:
            if not isinstance(entry, Mapping):
                continue
            organisation = entry.get("organization")
            owner = (
                _text(organisation.get("login"), 128) if isinstance(organisation, Mapping) else None
            )
            slug = _text(entry.get("slug"), 128)
            if owner and slug:
                groups.append(f"{owner}/{slug}")
        return tuple(dict.fromkeys(groups))[:MAX_GROUPS]

    def reachable(self, provider: OidcProvider) -> str:
        """
        Look at GitHub the way a sign-in would, without signing anybody in.

        Args:
            provider: The provider.

        Returns:
            The API address answered.

        Raises:
            OidcError: GitHub could not be reached.
        """
        api = endpoints(provider.issuer).api
        self.http.get_json(f"{api}/", headers=API_HEADERS)
        return api


def _text(value: Any, limit: int) -> str | None:
    """
    Args:
        value: Something GitHub sent.
        limit: The longest text kept.

    Returns:
        It as printable text, or None when it is not a non-empty string.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return None
    return text
