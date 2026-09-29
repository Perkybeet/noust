# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
This server's GitHub App as credentials: its JWT and its installation tokens.

GitHub authenticates an App with a JSON Web Token signed RS256 by the App's
private key, valid for ten minutes, and an installation with a token that App
JWT exchanges for, valid for an hour. Both are made here and nowhere else.

The JWT is assembled in Python and only the signature is left to ``openssl
dgst -sha256 -sign``, run through the command runner with the signing input on
standard input: no cryptography dependency, and the private key never leaves
its 0600 file. What the command line carries is the key's path, which every
local user may know; the key itself never is in argv, a log or a message.

Installation tokens are cached in memory until five minutes before they
expire, per installation and thread-safely, so a deploy that runs git a dozen
times asks GitHub once.

The private key, the webhook secret and the client secret are secret files
under ``github/`` (:class:`~noust.core.secrets.SecretStore`); the App's public
description is the ``github_app`` row of the store.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from noust.core.exceptions import IntegrationError, NoustError
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import GitHubAppRecord, get_store
from noust.integrations.github.client import GitHubClient, get_client
from noust.validators.source import GITHUB_HOST, github_repository

logger = logging.getLogger(__name__)

#: Namespace of every GitHub secret file.
SECRET_NAMESPACE = "github"  # noqa: S105 - a file name, not a credential
PRIVATE_KEY_SECRET = "github/private-key.pem"  # noqa: S105 - a file name, not a credential
WEBHOOK_SECRET = "github/webhook-secret"  # noqa: S105 - a file name, not a credential
CLIENT_SECRET = "github/client-secret"  # noqa: S105 - a file name, not a credential
#: What Noust knows about the App that is neither secret nor a store column:
#: the owner's account type and the webhook it configured.
META_SECRET = "github/meta.json"  # noqa: S105 - a file name, not a credential

#: Seconds the JWT is backdated, for a clock slightly ahead of GitHub's.
JWT_BACKDATE = 60
#: Seconds the JWT lives from now; GitHub refuses more than ten minutes.
JWT_LIFETIME = 540
#: Seconds before its expiry an installation token is replaced.
TOKEN_REFRESH_MARGIN = 300

#: Seconds ``openssl`` may take to sign.
SIGN_TIMEOUT = 15

Clock = Callable[[], float]


def b64url(data: bytes) -> str:
    """
    Encode bytes as JWT's unpadded base64url.

    Args:
        data: The bytes.

    Returns:
        The encoding, without ``=`` padding.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _compact_json(value: dict[str, Any]) -> bytes:
    """
    Serialise a JWT header or payload.

    Args:
        value: The mapping.

    Returns:
        Compact JSON bytes, keys in a stable order.
    """
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def jwt_signing_input(app_id: int, now: float) -> str:
    """
    Build the part of an App JWT the signature covers.

    Args:
        app_id: The App's id, its issuer.
        now: The current UNIX time.

    Returns:
        ``base64url(header).base64url(payload)``.
    """
    header = {"alg": "RS256", "typ": "JWT"}
    issued = int(now)
    payload = {"iat": issued - JWT_BACKDATE, "exp": issued + JWT_LIFETIME, "iss": app_id}
    return f"{b64url(_compact_json(header))}.{b64url(_compact_json(payload))}"


def sign_rs256(signing_input: str, key_path: Path, runner: CommandRunner | None = None) -> str:
    """
    Sign a JWT's input with an RSA key through ``openssl``.

    Args:
        signing_input: The header and payload, as :func:`jwt_signing_input`
            builds them.
        key_path: The PEM private key file.
        runner: Command runner; the process-wide one by default.

    Returns:
        The signature, base64url.

    Raises:
        IntegrationError: openssl failed or answered something that is not
            a hexadecimal signature.
    """
    result = (runner or get_runner()).run(
        ["openssl", "dgst", "-sha256", "-sign", str(key_path), "-hex"],
        input=signing_input,
        timeout=SIGN_TIMEOUT,
    )
    if not result.success:
        raise IntegrationError(
            "Could not sign the GitHub App's token with its private key",
            details=f"openssl failed; check that {key_path} holds the App's PEM key.",
            output=(result.stderr or result.stdout).strip(),
        )
    # ``SHA2-256(stdin)= 3f8d...`` (openssl 3) or ``RSA-SHA256(stdin)= ...``.
    _, _, hexdigits = result.stdout.strip().rpartition("= ")
    try:
        signature = bytes.fromhex(hexdigits.strip())
    except ValueError:
        signature = b""
    if not signature:
        raise IntegrationError(
            "openssl did not answer a signature",
            details="Expected 'openssl dgst -hex' output.",
            output=result.stdout.strip(),
        )
    return b64url(signature)


@dataclass(frozen=True)
class _CachedToken:
    """An installation token and when it stops being worth using."""

    token: str
    refresh_at: float


class GitHubApp:
    """
    This server's App, able to authenticate as itself and as its installations.

    Args:
        record: The App as the store keeps it.
        secrets: Where its secret files are.
        client: The API client.
        runner: The command runner ``openssl`` runs through.
        clock: The time source (tests).
    """

    def __init__(
        self,
        record: GitHubAppRecord,
        *,
        secrets: SecretStore | None = None,
        client: GitHubClient | None = None,
        runner: CommandRunner | None = None,
        clock: Clock = time.time,
    ) -> None:
        self.record = record
        self.secrets = secrets or SecretStore()
        self.client = client or get_client()
        self.runner = runner
        self.clock = clock

    @property
    def app_id(self) -> int:
        """The App's numeric id."""
        return self.record.app_id

    @property
    def slug(self) -> str:
        """The App's URL name."""
        return self.record.slug

    def jwt(self) -> str:
        """
        Make a fresh App JWT.

        Returns:
            The token, valid for about nine minutes.

        Raises:
            IntegrationError: The private key is missing or cannot sign.
        """
        key_path = self.secrets.path(PRIVATE_KEY_SECRET)
        if self.secrets.read(PRIVATE_KEY_SECRET) is None:
            raise IntegrationError(
                "The GitHub App's private key is missing",
                details=f"Expected it at {key_path}. Remove the integration and create it again.",
            )
        signing_input = jwt_signing_input(self.app_id, self.clock())
        return f"{signing_input}.{sign_rs256(signing_input, key_path, self.runner)}"

    def app_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """
        Call the API as the App itself.

        Args:
            method: HTTP method.
            path: API path.
            body: JSON body.

        Returns:
            GitHub's decoded answer.

        Raises:
            IntegrationError: As :meth:`GitHubClient.request` does.
        """
        return self.client.request(method, path, authorization=f"Bearer {self.jwt()}", body=body)

    def app_paginate(self, path: str, *, key: str | None = None) -> list[Any]:
        """
        List as the App itself.

        Args:
            path: API path.
            key: As for :meth:`GitHubClient.paginate`.

        Returns:
            Every item.

        Raises:
            IntegrationError: As :meth:`GitHubClient.request` does.
        """
        return self.client.paginate(path, authorization=f"Bearer {self.jwt()}", key=key)

    def installation_token(self, installation_id: int) -> str:
        """
        Return a token of an installation, from the cache while it lasts.

        Args:
            installation_id: The installation.

        Returns:
            A token valid for at least five more minutes.

        Raises:
            IntegrationError: GitHub refused or could not be reached.
        """
        return _tokens.get(self, installation_id)

    def as_installation(
        self,
        installation_id: int,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """
        Call the API as one installation.

        Args:
            installation_id: The installation.
            method: HTTP method.
            path: API path.
            body: JSON body.

        Returns:
            GitHub's decoded answer.

        Raises:
            IntegrationError: As :meth:`GitHubClient.request` does.
        """
        token = self.installation_token(installation_id)
        return self.client.request(method, path, authorization=f"token {token}", body=body)

    def paginate_as_installation(
        self, installation_id: int, path: str, *, key: str | None = None
    ) -> list[Any]:
        """
        List as one installation.

        Args:
            installation_id: The installation.
            path: API path.
            key: As for :meth:`GitHubClient.paginate`.

        Returns:
            Every item.

        Raises:
            IntegrationError: As :meth:`GitHubClient.request` does.
        """
        token = self.installation_token(installation_id)
        return self.client.paginate(path, authorization=f"token {token}", key=key)


def _expiry(value: Any, fallback: float) -> float:
    """
    Read GitHub's ``expires_at``.

    Args:
        value: ``2026-09-28T12:00:00Z`` or anything else.
        fallback: What to use when it cannot be read.

    Returns:
        The expiry as a UNIX time.
    """
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return fallback


class InstallationTokens:
    """Installation tokens, cached per installation until shortly before they expire."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[int, int], _CachedToken] = {}

    def get(self, app: GitHubApp, installation_id: int) -> str:
        """
        Return a cached token, or exchange the App's JWT for a new one.

        The lock is held across the exchange, so two deploys starting at
        once do not both ask GitHub; a token request takes well under the
        client's timeout.

        Args:
            app: The App.
            installation_id: The installation.

        Returns:
            The token.

        Raises:
            IntegrationError: GitHub refused or could not be reached.
        """
        key = (app.app_id, int(installation_id))
        with self._lock:
            now = app.clock()
            cached = self._cache.get(key)
            if cached is not None and now < cached.refresh_at:
                return cached.token
            answer = app.app_request(
                "POST", f"/app/installations/{int(installation_id)}/access_tokens"
            )
            token = answer.get("token") if isinstance(answer, dict) else None
            if not isinstance(token, str) or not token:
                raise IntegrationError(
                    f"GitHub answered no token for installation {installation_id}",
                    details="Retry; if it persists, reinstall the App on that account.",
                )
            expires = _expiry(answer.get("expires_at"), now + 3600)
            self._cache[key] = _CachedToken(token=token, refresh_at=expires - TOKEN_REFRESH_MARGIN)
            return token

    def forget(self, installation_id: int | None = None) -> None:
        """
        Drop cached tokens.

        Args:
            installation_id: Only this installation's; None for all.
        """
        with self._lock:
            if installation_id is None:
                self._cache.clear()
                return
            for key in [k for k in self._cache if k[1] == int(installation_id)]:
                del self._cache[key]


_tokens = InstallationTokens()


def forget_tokens(installation_id: int | None = None) -> None:
    """
    Drop cached installation tokens (an installation removed, the App deleted, tests).

    Args:
        installation_id: Only this installation's; None for all.
    """
    _tokens.forget(installation_id)


def load_app(
    *, client: GitHubClient | None = None, runner: CommandRunner | None = None
) -> GitHubApp | None:
    """
    Load this server's App, when there is one.

    Args:
        client: API client override.
        runner: Command runner override.

    Returns:
        The App, or None when none was created, or the store cannot be read
        (which is logged: a store that cannot be read has already failed
        whatever needed it).
    """
    try:
        record = get_store().get_github_app()
    except (NoustError, sqlite3.Error) as exc:
        logger.debug("Could not read the GitHub App from the store: %s", exc)
        return None
    if record is None:
        return None
    return GitHubApp(record, client=client, runner=runner)


def installation_for(repository: str, installation_id: int | None = None) -> int | None:
    """
    Choose the installation that reaches a repository.

    The application's own link wins; then any application of the same
    repository that has one; then the installation on the repository
    owner's account, which is what lets the console inspect a repository
    before any application exists.

    Args:
        repository: ``owner/repo``.
        installation_id: The installation the caller already knows, such
            as the application's ``github_installation_id``.

    Returns:
        The installation id, or None when no stored installation covers the
        repository's owner.
    """
    if installation_id is not None:
        return int(installation_id)
    try:
        store = get_store()
        wanted = repository.lower()
        for app in store.list_apps():
            linked = app.github_installation_id
            if linked is not None and (github_repository(app.source) or "").lower() == wanted:
                return int(linked)
        owner = wanted.split("/", 1)[0]
        for installation in store.list_github_installations():
            if installation.account.lower() == owner:
                return installation.installation_id
    except (NoustError, sqlite3.Error) as exc:
        logger.debug("Could not read the GitHub installations: %s", exc)
    return None


def is_github_https(url: str) -> bool:
    """
    Tell whether a URL is an https repository URL on github.com.

    Args:
        url: A repository URL.

    Returns:
        True for ``https://github.com/owner/repo[.git]``.
    """
    return url.strip().lower().startswith(f"https://{GITHUB_HOST}/") and bool(
        github_repository(url)
    )


def git_auth_environment(
    url: str, *, installation_id: int | None = None, config_index: int = 0
) -> dict[str, str]:
    """
    Build the git environment that lets one clone or fetch use an installation token.

    ``http.https://github.com/.extraheader`` set through ``GIT_CONFIG_COUNT``
    and its pairs: the token is only ever in git's environment, which only
    root can read, and never in argv, the URL, ``.git/config`` or a log.
    The header is scoped to github.com, so a submodule elsewhere never
    receives it.

    Args:
        url: The repository URL git is given.
        installation_id: The installation to use; resolved by
            :func:`installation_for` when None.
        config_index: The first free ``GIT_CONFIG_KEY_<n>`` index (the
            operator's own environment may already use some).

    Returns:
        The variables to add to git's environment; empty when the URL is not
        an https github.com repository, no App exists, or no installation
        covers the repository.

    Raises:
        IntegrationError: An installation covers the repository but GitHub
            would not give a token for it.
    """
    if not is_github_https(url):
        return {}
    repository = github_repository(url)
    app = load_app()
    if app is None or repository is None:
        return {}
    chosen = installation_for(repository, installation_id)
    if chosen is None:
        return {}
    token = app.installation_token(chosen)
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    return {
        "GIT_CONFIG_COUNT": str(config_index + 1),
        f"GIT_CONFIG_KEY_{config_index}": f"http.https://{GITHUB_HOST}/.extraheader",
        f"GIT_CONFIG_VALUE_{config_index}": f"AUTHORIZATION: basic {basic}",
    }


def github_app_configured() -> bool:
    """
    Tell whether this server has a GitHub App, cheaply.

    Returns:
        True when the store holds one.
    """
    try:
        return get_store().get_github_app() is not None
    except (NoustError, sqlite3.Error) as exc:
        logger.debug("Could not read the GitHub App from the store: %s", exc)
        return False


def read_meta() -> dict[str, Any]:
    """
    Read what Noust remembers about the App besides the store row.

    Returns:
        ``owner_type`` and ``webhook_url`` / ``webhook_active`` when known.
    """
    try:
        return SecretStore().read_json(META_SECRET)
    except NoustError as exc:
        logger.warning("Could not read %s: %s", META_SECRET, exc)
        return {}


def write_meta(**values: Any) -> None:
    """
    Update what Noust remembers about the App besides the store row.

    Args:
        **values: Keys to set.
    """
    meta = read_meta()
    meta.update(values)
    SecretStore().write_json(META_SECRET, meta)
