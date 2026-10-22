# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GitLab (gitlab.com or self-hosted) as a source: connect, pick a project and a branch, clone it (A-06).

Before 3.4 GitLab was a webhook sender and a URL to paste. A *connection* is a GitLab user's access
to one host, made one of two ways:

- **A token** the operator creates (personal, group or project access token with ``read_api`` and
  ``read_repository``) and gives Noust once.
- **OAuth**, for people who should not handle tokens: the operator registers an *application* in
  GitLab (Admin area, Applications; scopes ``read_api`` and ``read_repository``, redirect URI
  ``<console URL>/integrations/gitlab/callback``) and gives its id and secret to Noust, then anyone
  with access to the console signs in to GitLab and Noust keeps the tokens GitLab hands back,
  refreshing them before they expire.

What a connection does: lists the projects and groups its user can read, with a filter by group
and a search, lists a project's branches (the console's picker), and clones private projects over
https. The last is one more place a credential reaches git, and it goes through the one that
already exists (:meth:`noust.managers.source_credentials.GitCredentialManager.auth_for`): a
repository with a credential of its own keeps it, one without is cloned with the connection of its
host. A connection's token therefore reaches every project of its host that has none of its own;
give it the narrowest scopes and a user that can read only what should be deployed.

Every request goes through :class:`noust.deployers.importers.remote.RemoteClient`: https only, behind
the SSRF guard (a GitLab in a private network is listed under ``notifications.allow_private_hosts``),
no redirect followed, a timeout and a size limit on every call. The tokens are secrets, never in a
log, an argument or an answer of this server.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as token_source
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from noust.core import audit
from noust.core.config import Config
from noust.core.exceptions import ConfigError, IntegrationError, ValidationError
from noust.core.github_host import validate_host
from noust.core.gitlab_store import (
    GitLabConnection,
    GitLabStore,
    OAuthApplication,
    check_name,
    connection_secret,
    oauth_secret,
)
from noust.core.secrets import SecretStore
from noust.deployers.importers.remote import RemoteClient, read_token

#: What a connection may read: the projects, and their code.
SCOPES = "read_api read_repository"

#: Projects, groups and branches one page holds, and how many pages are read at most.
PAGE_SIZE = 100
MAX_PAGES = 20

#: Seconds an OAuth sign-in may take between the redirect and its callback.
STATE_LIFETIME = 600

#: A token is refreshed this many seconds before it expires.
REFRESH_MARGIN = 120

#: Where the console receives GitLab's redirect, under ``web.public_url``.
CALLBACK_PATH = "/integrations/gitlab/callback"

#: The user name git signs in with when the secret is an access token.
CLONE_USER = "oauth2"

_HINTS = {
    401: "GitLab did not accept the token. Create one with the scopes read_api and "
    "read_repository (Preferences, Access tokens) and connect again.",
    403: "The token may not read this. It needs the scopes read_api and read_repository.",
    404: "GitLab has no such project or group, or the token cannot see it.",
}

Opener = Callable[..., Any]


@dataclass
class _Pending:
    """An OAuth sign-in that was started and not finished."""

    name: str
    host: str
    verifier: str
    redirect_uri: str
    created_at: float


class _States:
    """The OAuth sign-ins in flight: one use each, ten minutes, in this process's memory."""

    def __init__(self) -> None:
        self._items: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def issue(self, pending: _Pending) -> str:
        """
        Remember a sign-in and give it a state GitLab sends back.

        Args:
            pending: What the callback needs.

        Returns:
            The unguessable state.
        """
        state = token_source.token_urlsafe(32)
        with self._lock:
            self._expire()
            self._items[state] = pending
        return state

    def consume(self, state: str) -> _Pending | None:
        """
        Take a sign-in out, once.

        Args:
            state: What came back.

        Returns:
            What was remembered; None when unknown, expired or used.
        """
        with self._lock:
            self._expire()
            return self._items.pop(state, None)

    def clear(self) -> None:
        """Forget everything (tests)."""
        with self._lock:
            self._items.clear()

    def _expire(self) -> None:
        """Drop what is older than :data:`STATE_LIFETIME`."""
        limit = time.time() - STATE_LIFETIME
        for key in [key for key, item in self._items.items() if item.created_at < limit]:
            del self._items[key]


states = _States()

#: One refresh at a time: GitLab rotates the refresh token, so a second request with the old one fails.
_refresh_lock = threading.Lock()


def _actor() -> str:
    """
    Returns:
        The audit label of whoever is acting, for the rows.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def check_host(host: str) -> str:
    """
    Check a GitLab host the operator typed.

    Args:
        host: A host name with an optional port; no scheme, no path.

    Returns:
        The host, lowercased.

    Raises:
        ValidationError: Not a host.
    """
    try:
        checked = validate_host(host)
    except ConfigError as exc:
        raise ValidationError(
            f"{host!r} is not a GitLab host",
            details="Give the host alone: gitlab.com or git.example.com (add :port when it "
            "is not 443).",
            field="host",
        ) from exc
    if not checked:
        raise ValidationError(
            "A GitLab host is needed", details="For example gitlab.com.", field="host"
        )
    return checked


class GitLabService:
    """What this server does with GitLab connections."""

    def __init__(
        self,
        store: GitLabStore | None = None,
        secrets: SecretStore | None = None,
        *,
        opener: Opener | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Args:
            store: The connections; the process-wide store's by default.
            secrets: Where the tokens are kept.
            opener: Replacement for the HTTP opener (tests).
            clock: The time, for token expiry.
        """
        self.connections = store or GitLabStore()
        self.secrets = secrets or SecretStore()
        self._opener = opener
        self._clock = clock

    # Clients ---------------------------------------------------------------------------

    def _client(self, host: str, token: str, *, bearer: bool) -> RemoteClient:
        """
        Args:
            host: The GitLab host.
            token: What signs in.
            bearer: An OAuth token (``Authorization: Bearer``) rather than an access token.

        Returns:
            A client on the host's API.
        """
        header = ("Authorization", f"Bearer {token}") if bearer else ("PRIVATE-TOKEN", token)
        return RemoteClient(
            f"https://{host}/api/v4",
            header,
            platform="GitLab",
            opener=self._opener,
            hints=_HINTS,
        )

    def _form_client(self, host: str) -> RemoteClient:
        """
        Args:
            host: The GitLab host.

        Returns:
            A client with no token, for GitLab's OAuth endpoint.
        """
        return RemoteClient(
            f"https://{host}", None, platform="GitLab", opener=self._opener, hints=_HINTS
        )

    def _token_of(self, connection: GitLabConnection) -> str:
        """
        Read a connection's token, refreshing an OAuth one that is about to expire.

        Args:
            connection: The connection.

        Returns:
            The token to sign in with.

        Raises:
            IntegrationError: The credentials are missing, or GitLab refused the refresh.
        """
        stored = self.secrets.read_json(connection.secret_name)
        if connection.kind == "token":
            token = stored.get("token")
            if not isinstance(token, str) or not token:
                raise IntegrationError(
                    f"The token of {connection.name} is missing",
                    details=f"Connect again: noust gitlab connect {connection.name} --host "
                    f"{connection.host}.",
                )
            return token
        access = stored.get("access_token")
        expires = stored.get("expires_at")
        if (
            isinstance(access, str)
            and access
            and (not isinstance(expires, int | float) or self._clock() < expires - REFRESH_MARGIN)
        ):
            return access
        return self._refresh(connection)

    def _refresh(self, connection: GitLabConnection) -> str:
        """
        Exchange an OAuth connection's refresh token for new tokens.

        Args:
            connection: The connection.

        Returns:
            The new access token.

        Raises:
            IntegrationError: There is no refresh token or application, or GitLab refused.
        """
        with _refresh_lock:
            stored = self.secrets.read_json(connection.secret_name)
            expires = stored.get("expires_at")
            access = stored.get("access_token")
            # Another thread refreshed while this one waited.
            if (
                isinstance(access, str)
                and isinstance(expires, int | float)
                and self._clock() < expires - REFRESH_MARGIN
            ):
                return access
            refresh = stored.get("refresh_token")
            application = self.connections.oauth_application(connection.host)
            if not isinstance(refresh, str) or application is None:
                raise IntegrationError(
                    f"The sign-in of {connection.name} cannot be renewed",
                    details="Connect again from the console: Integrations, GitLab.",
                )
            client_secret = self.secrets.read(application.secret_name)
            if not client_secret:
                raise IntegrationError(
                    f"The OAuth application of {connection.host} has no secret",
                    details=f"Register it again: noust gitlab oauth-app set --host {connection.host}.",
                )
            answer = self._form_client(connection.host).post_form(
                "/oauth/token",
                {
                    "client_id": application.client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh,
                    "grant_type": "refresh_token",
                    "redirect_uri": self.redirect_uri(),
                },
            )
            return self._keep_tokens(connection.secret_name, answer)

    def _keep_tokens(self, secret_name: str, answer: Any) -> str:
        """
        Store what GitLab's token endpoint answered.

        Args:
            secret_name: Where the connection's tokens are kept.
            answer: The decoded answer.

        Returns:
            The access token.

        Raises:
            IntegrationError: The answer has no access token.
        """
        if not isinstance(answer, dict) or not isinstance(answer.get("access_token"), str):
            raise IntegrationError(
                "GitLab's answer to the sign-in has no access token",
                details="Check the application's scopes and redirect URI in GitLab.",
            )
        created = answer.get("created_at")
        lifetime = answer.get("expires_in")
        base = float(created) if isinstance(created, int | float) else self._clock()
        self.secrets.write_json(
            secret_name,
            {
                "access_token": answer["access_token"],
                "refresh_token": answer.get("refresh_token"),
                "expires_at": base + float(lifetime) if isinstance(lifetime, int | float) else None,
            },
        )
        return str(answer["access_token"])

    def client_for(self, name: str) -> RemoteClient:
        """
        Build a client for a connection.

        Args:
            name: The connection.

        Returns:
            A client signed in as it.

        Raises:
            ValidationError: There is no such connection.
            IntegrationError: Its credentials cannot be had.
        """
        connection = self._known(name)
        return self._client(
            connection.host, self._token_of(connection), bearer=connection.kind == "oauth"
        )

    def _known(self, name: str) -> GitLabConnection:
        """
        Args:
            name: A connection's name.

        Returns:
            The connection.

        Raises:
            ValidationError: There is none.
        """
        connection = self.connections.get(name)
        if connection is None:
            raise ValidationError(
                f"There is no GitLab connection named {name}",
                details="List them with: noust gitlab list.",
                field="name",
            )
        return connection

    # Connections -----------------------------------------------------------------------

    def connect_with_token(
        self, name: str, host: str, token: str, *, actor: str | None = None
    ) -> GitLabConnection:
        """
        Add a connection that signs in with an access token.

        GitLab is asked who the token belongs to first, so a token it does not accept is
        refused before anything is stored.

        Args:
            name: What to call it.
            host: The GitLab host.
            token: The access token.
            actor: Who adds it.

        Returns:
            The connection.

        Raises:
            ValidationError: The name or the host is not valid, or the name is taken.
            IntegrationError: GitLab could not be reached or refused the token.
        """
        check_name(name)
        checked = check_host(host)
        secret = read_token(token, source="the token")
        if self.connections.get(name) is not None:
            raise ValidationError(
                f"There is a GitLab connection named {name} already",
                details="Remove it first: noust gitlab remove " + name,
                field="name",
            )
        user = self._client(checked, secret, bearer=False).get("/user")
        username = user.get("username") if isinstance(user, dict) else None
        self.secrets.write_json(connection_secret(name), {"token": secret})
        try:
            connection = self.connections.add(
                name, checked, "token", username=username, created_by=actor or _actor()
            )
        except ValidationError:
            self.secrets.delete(connection_secret(name))
            raise
        self.connections.record_check(name, ok=True, username=username, error=None)
        audit.record(
            "integration.gitlab.connect",
            target=f"gitlab:{name}",
            details={"host": checked, "kind": "token", "username": username},
        )
        return self.connections.get(name) or connection

    def check(self, name: str) -> GitLabConnection:
        """
        Ask GitLab who a connection is, and record the answer.

        Args:
            name: The connection.

        Returns:
            The connection, with its new status.

        Raises:
            ValidationError: There is no such connection.
        """
        connection = self._known(name)
        try:
            user = self.client_for(name).get("/user")
            username = user.get("username") if isinstance(user, dict) else None
            self.connections.record_check(name, ok=True, username=username, error=None)
        except IntegrationError as exc:
            self.connections.record_check(
                name, ok=False, username=None, error=exc.output or exc.message
            )
        audit.record("integration.gitlab.check", target=f"gitlab:{connection.name}")
        return self._known(name)

    def remove(self, name: str) -> bool:
        """
        Forget a connection and its credentials.

        Args:
            name: The connection.

        Returns:
            Whether there was one.
        """
        connection = self.connections.get(name)
        removed = self.connections.remove(name)
        if connection is not None:
            self.secrets.delete(connection.secret_name)
            audit.record("integration.gitlab.remove", target=f"gitlab:{name}")
        return removed

    # Listing ---------------------------------------------------------------------------

    def _paginate(
        self, client: RemoteClient, path: str, query: dict[str, Any], limit: int
    ) -> list[Any]:
        """
        Read a GitLab listing, page by page.

        Args:
            client: The client.
            path: The listing's path.
            query: Its parameters.
            limit: How many items to return at most.

        Returns:
            The items.

        Raises:
            IntegrationError: GitLab answered something that is not a list.
        """
        items: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            batch = client.get(path, {**query, "per_page": PAGE_SIZE, "page": page})
            if not isinstance(batch, list):
                raise IntegrationError(f"GitLab's answer to {path} is not a list")
            items.extend(batch)
            if len(batch) < PAGE_SIZE or len(items) >= limit:
                break
        return items[:limit]

    def groups(self, name: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """
        List the groups a connection's user belongs to.

        Args:
            name: The connection.
            limit: How many at most.

        Returns:
            ``id``, ``full_path`` and ``name`` of each, by path.

        Raises:
            IntegrationError: GitLab refused or could not be reached.
        """
        items = self._paginate(
            self.client_for(name), "/groups", {"min_access_level": 10, "order_by": "path"}, limit
        )
        return [
            {"id": item["id"], "full_path": item["full_path"], "name": item.get("name")}
            for item in items
            if isinstance(item, dict) and "id" in item and "full_path" in item
        ]

    def projects(
        self,
        name: str,
        *,
        group: str | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        List the projects a connection's user can read.

        Args:
            name: The connection.
            group: A group's full path, to list only what is in it (and its subgroups).
            search: Part of a project's name.
            limit: How many at most.

        Returns:
            Each project: ``id``, ``path`` (``group/project``), ``default_branch``, ``clone_url``
            (https) and ``web_url``; most recently active first.

        Raises:
            IntegrationError: GitLab refused or could not be reached.
        """
        query: dict[str, Any] = {
            "simple": "true",
            "archived": "false",
            "order_by": "last_activity_at",
            "sort": "desc",
        }
        if search:
            query["search"] = search
        if group:
            path = f"/groups/{quote(group.strip('/'), safe='')}/projects"
            query["include_subgroups"] = "true"
        else:
            path = "/projects"
            query["membership"] = "true"
        items = self._paginate(self.client_for(name), path, query, limit)
        return [
            {
                "id": item["id"],
                "path": item.get("path_with_namespace"),
                "default_branch": item.get("default_branch"),
                "clone_url": item.get("http_url_to_repo"),
                "web_url": item.get("web_url"),
            }
            for item in items
            if isinstance(item, dict) and "id" in item
        ]

    def branches(
        self, name: str, project_id: int | str, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """
        List a project's branches.

        Args:
            name: The connection.
            project_id: The project's id, or its path.
            limit: How many at most.

        Returns:
            Each branch: ``name``, ``default`` and ``commit``.

        Raises:
            IntegrationError: GitLab refused or could not be reached.
        """
        items = self._paginate(
            self.client_for(name),
            f"/projects/{quote(str(project_id), safe='')}/repository/branches",
            {},
            limit,
        )
        return [
            {
                "name": item["name"],
                "default": bool(item.get("default")),
                "commit": (item.get("commit") or {}).get("id"),
            }
            for item in items
            if isinstance(item, dict) and "name" in item
        ]

    # OAuth -----------------------------------------------------------------------------

    def redirect_uri(self) -> str:
        """
        Say where GitLab sends the browser back: the console's callback.

        Returns:
            ``<web.public_url>/integrations/gitlab/callback``.

        Raises:
            ValidationError: The console has no public URL to be sent back to.
        """
        base = str(Config().get("web.public_url") or "").rstrip("/")
        if not base:
            raise ValidationError(
                "The console has no public address for GitLab to send the browser back to",
                details="Set it: noust config set web.public_url https://console.example.com",
                field="web.public_url",
            )
        return f"{base}{CALLBACK_PATH}"

    def save_oauth_application(
        self, host: str, client_id: str, client_secret: str, *, actor: str | None = None
    ) -> OAuthApplication:
        """
        Record the OAuth application registered in GitLab for a host.

        Args:
            host: The GitLab host.
            client_id: The application id.
            client_secret: Its secret.
            actor: Who records it.

        Returns:
            The application, without its secret.

        Raises:
            ValidationError: Something is missing or the host is not one.
        """
        checked = check_host(host)
        if not client_id.strip() or not client_secret.strip():
            raise ValidationError(
                "The application id and its secret are both needed",
                details="GitLab shows them when the application is created.",
                field="client_id",
            )
        self.secrets.write(oauth_secret(checked), client_secret.strip())
        application = self.connections.save_oauth_application(
            checked, client_id.strip(), created_by=actor or _actor()
        )
        audit.record(
            "integration.gitlab.oauth_app", target=f"gitlab:{checked}", details={"action": "set"}
        )
        return application

    def remove_oauth_application(self, host: str) -> bool:
        """
        Forget the OAuth application of a host.

        Args:
            host: The GitLab host.

        Returns:
            Whether there was one.
        """
        checked = check_host(host)
        removed = self.connections.remove_oauth_application(checked)
        self.secrets.delete(oauth_secret(checked))
        if removed:
            audit.record(
                "integration.gitlab.oauth_app",
                target=f"gitlab:{checked}",
                details={"action": "remove"},
            )
        return removed

    def oauth_start(self, name: str, host: str) -> dict[str, str]:
        """
        Begin signing in to GitLab: where to send the browser.

        Uses PKCE, so the code GitLab hands back is useless to anyone who did not start
        the sign-in.

        Args:
            name: What to call the connection once it exists.
            host: The GitLab host, which must have an OAuth application.

        Returns:
            ``authorize_url`` and ``state``.

        Raises:
            ValidationError: The name is taken or invalid, the host has no application, or the
                console has no public address.
        """
        check_name(name)
        checked = check_host(host)
        if self.connections.get(name) is not None:
            raise ValidationError(
                f"There is a GitLab connection named {name} already", field="name"
            )
        application = self.connections.oauth_application(checked)
        if application is None:
            raise ValidationError(
                f"There is no OAuth application registered for {checked}",
                details="Register it first: noust gitlab oauth-app set --host " + checked,
                field="host",
            )
        redirect = self.redirect_uri()
        verifier = token_source.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = states.issue(_Pending(name, checked, verifier, redirect, time.time()))
        query = urlencode(
            {
                "client_id": application.client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "state": state,
                "scope": SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return {"authorize_url": f"https://{checked}/oauth/authorize?{query}", "state": state}

    def oauth_finish(self, code: str, state: str) -> GitLabConnection:
        """
        Finish signing in: exchange GitLab's code for tokens and keep them.

        Args:
            code: The ``code`` GitLab's redirect carried.
            state: The ``state`` it carried back.

        Returns:
            The new connection.

        Raises:
            ValidationError: The state was not issued here, expired or was used.
            IntegrationError: GitLab refused the code.
        """
        pending = states.consume(state)
        if pending is None:
            raise ValidationError(
                "This GitLab callback does not belong to a sign-in started here",
                details="Start again from the console: a sign-in expires after ten minutes and "
                "its callback can be used once.",
                field="state",
            )
        if not code or len(code) > 512:
            raise ValidationError("Not an authorization code", field="code")
        application = self.connections.oauth_application(pending.host)
        client_secret = self.secrets.read(application.secret_name) if application else None
        if application is None or not client_secret:
            raise IntegrationError(
                f"The OAuth application of {pending.host} was removed during the sign-in"
            )
        answer = self._form_client(pending.host).post_form(
            "/oauth/token",
            {
                "client_id": application.client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": pending.redirect_uri,
                "code_verifier": pending.verifier,
            },
        )
        secret_name = connection_secret(pending.name)
        access = self._keep_tokens(secret_name, answer)
        try:
            user = self._client(pending.host, access, bearer=True).get("/user")
            username = user.get("username") if isinstance(user, dict) else None
            connection = self.connections.add(
                pending.name, pending.host, "oauth", username=username, created_by=_actor()
            )
        except (IntegrationError, ValidationError):
            self.secrets.delete(secret_name)
            raise
        self.connections.record_check(pending.name, ok=True, username=username, error=None)
        audit.record(
            "integration.gitlab.connect",
            target=f"gitlab:{pending.name}",
            details={"host": pending.host, "kind": "oauth", "username": username},
        )
        return self.connections.get(pending.name) or connection

    # Cloning ---------------------------------------------------------------------------

    def credential_for_url(self, url: str) -> tuple[str, str] | None:
        """
        Find what clones a private repository of a host a connection signs in to.

        Args:
            url: The URL git is about to use.

        Returns:
            ``(user, token)``; None when the URL is not https or no connection is of its host.
        """
        parts = urlsplit(url.split("#", 1)[0])
        if parts.scheme.lower() != "https" or not parts.hostname or "@" in parts.netloc:
            return None
        authority = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
        connection = self.connections.for_host(authority) or (
            self.connections.for_host(parts.hostname) if parts.port is None else None
        )
        if connection is None:
            return None
        try:
            return CLONE_USER, self._token_of(connection)
        except IntegrationError:
            return None


__all__ = [
    "CALLBACK_PATH",
    "SCOPES",
    "GitLabService",
    "check_host",
    "states",
]
