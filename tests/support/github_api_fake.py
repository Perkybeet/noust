# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A GitHub whose API is the one GitHub documents, for the tests of what the central does with its App.

Not canned answers by route: a small state that behaves the way GitHub's reference says it does,
so that a test of the central proves what the central sends and what GitHub would do with it.
Built from the REST reference of GitHub Apps (apps/installations "Create an installation access
token for an app", repos/deployments, repos/deployments statuses, issues/comments) and the
documentation of authenticating as an installation:

- An App authenticates with a JSON Web Token: three parts, ``RS256``, an issuer that is the App's id,
  issued at and expiring at times at most ten minutes apart. The signature is the one thing the fake
  does not verify, because the tests fake ``openssl``.
- ``POST /app/installations/{id}/access_tokens`` answers 201 with ``token``, ``expires_at`` an hour
  away, ``permissions`` (always with ``metadata: read``), ``repository_selection`` and the
  ``repositories`` the token reaches. ``repositories`` in the body (names, not full names, at most
  500) narrows it; each must be a repository of the installation or the answer is 422. ``permissions``
  in the body narrows it too; asking for more than the installation holds is 422. Asking for
  nothing gives all the installation's repositories and permissions.
- A token reaches only its repositories (anything else is a 404, GitHub does not say a repository
  exists to a token that cannot see it) and does only what its permissions let it (the rest is a
  403 ``Resource not accessible by integration``). An expired or unknown token is a 401.
- Deployments, their statuses and issue comments, with the states and the 140 characters of a
  description GitHub allows.
- ``/{owner}/{repo}.git/info/refs`` with ``Authorization: Basic x-access-token:<token>``: what a clone
  starts with. 200 for a token that reads the repository's contents, 401 otherwise.

It serves from a thread (:func:`serve`) so that the client under test makes real HTTP requests, and
needs nothing but the standard library, so it also runs inside the integration harness' containers.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

#: What the App Noust creates asks for (``noust.integrations.github.manifest.PERMISSIONS``).
APP_PERMISSIONS: dict[str, str] = {
    "contents": "read",
    "metadata": "read",
    "deployments": "write",
    "statuses": "write",
    "pull_requests": "write",
}

#: A token lives an hour.
TOKEN_LIFETIME = 3600

#: At most this many repositories in the body of a token request.
MAX_TOKEN_REPOSITORIES = 500

_LEVELS = {"read": 1, "write": 2, "admin": 3}
_STATES = ("error", "failure", "inactive", "in_progress", "queued", "pending", "success")


@dataclass
class Recorded:
    """One request the fake received."""

    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class Installation:
    """An account the App is installed on."""

    id: int
    account: str
    repositories: list[str]
    selection: str = "selected"
    permissions: dict[str, str] = field(default_factory=lambda: dict(APP_PERMISSIONS))


@dataclass
class Token:
    """An installation access token, as issued."""

    installation: int
    repositories: frozenset[str] | None
    permissions: dict[str, str]
    expires_at: float


@dataclass
class FakeGitHubApi:
    """
    GitHub, with one App.

    Attributes:
        app_id: The App's id (the JWT's issuer).
        clock: Epoch seconds (tests move it).
        installations: The accounts the App is installed on.
        tokens: Every token issued.
        requests: Every request received, in order.
        deployments: ``owner/repo`` to the deployments created in it.
        deployment_statuses: ``(owner/repo, deployment id)`` to its statuses, oldest first.
        comments: ``owner/repo`` to ``{comment id: body}``.
    """

    app_id: int = 4242
    clock: Callable[[], float] = time.time
    installations: dict[int, Installation] = field(default_factory=dict)
    tokens: dict[str, Token] = field(default_factory=dict)
    requests: list[Recorded] = field(default_factory=list)
    deployments: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    deployment_statuses: dict[tuple[str, int], list[dict[str, Any]]] = field(default_factory=dict)
    comments: dict[str, dict[int, str]] = field(default_factory=dict)
    _ids: int = 1000

    # -- what a test sets up ---------------------------------------------------------------------

    def install(
        self,
        account: str,
        repositories: list[str],
        *,
        installation_id: int | None = None,
        selection: str = "selected",
        permissions: dict[str, str] | None = None,
    ) -> int:
        """
        Install the App on an account.

        Args:
            account: The account's login.
            repositories: The names (without the owner) of the repositories the installation has.
            installation_id: Its id; the next free one by default.
            selection: ``all`` or ``selected``.
            permissions: What the account granted; what the App asks for by default.

        Returns:
            The installation's id.
        """
        number = installation_id or 77 + len(self.installations)
        self.installations[number] = Installation(
            number,
            account,
            list(repositories),
            selection,
            dict(permissions or APP_PERMISSIONS),
        )
        return number

    def paths(self, method: str | None = None) -> list[str]:
        """
        Args:
            method: Only this method's.

        Returns:
            The paths requested, query strings cut.
        """
        return [r.path.split("?")[0] for r in self.requests if method in (None, r.method)]

    def bodies(self, method: str, pattern: str) -> list[Any]:
        """
        Args:
            method: The method.
            pattern: A regular expression the whole path (query cut) must match.

        Returns:
            The bodies of the requests that matched, oldest first.
        """
        return [
            r.body
            for r in self.requests
            if r.method == method and re.fullmatch(pattern, r.path.split("?")[0])
        ]

    # -- how GitHub answers ----------------------------------------------------------------------

    def handle(
        self, method: str, target: str, headers: dict[str, str], body: Any
    ) -> tuple[int, Any]:
        """
        Answer one request.

        Args:
            method: The HTTP method.
            target: Path and query.
            headers: The request's headers, names in lower case.
            body: The decoded JSON body, or None.

        Returns:
            The status and the JSON to answer with.
        """
        self.requests.append(Recorded(method, target, headers, body))
        path = urlsplit(target).path
        authorization = headers.get("authorization", "")
        found = re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
        if found and method == "POST":
            return self._issue(int(found.group(1)), authorization, body)
        git = re.fullmatch(r"/([^/]+)/([^/]+)\.git/info/refs", path)
        if git and method == "GET":
            return self._advertise(git.group(1), git.group(2), authorization)
        token = self._token(authorization)
        if isinstance(token, tuple):
            return token
        if path == "/installation/repositories" and method == "GET":
            return 200, self._reachable(token)
        repo = re.fullmatch(r"/repos/([^/]+)/([^/]+)(/.*)?", path)
        if repo is None:
            return 404, {"message": "Not Found"}
        full = f"{repo.group(1)}/{repo.group(2)}".lower()
        if not self._reaches(token, full):
            return 404, {"message": "Not Found"}
        return self._repo(method, full, repo.group(3) or "", token, body)

    def _now(self) -> float:
        return self.clock()

    def _next_id(self) -> int:
        self._ids += 1
        return self._ids

    def _issue(self, installation_id: int, authorization: str, body: Any) -> tuple[int, Any]:
        """Create an installation access token for an app."""
        problem = self._app_jwt(authorization)
        if problem is not None:
            return 401, {"message": problem}
        installation = self.installations.get(installation_id)
        if installation is None:
            return 404, {"message": "Not Found"}
        asked = body if isinstance(body, dict) else {}
        owned = {name.lower(): name for name in installation.repositories}
        names = asked.get("repositories")
        if names is not None:
            if (
                not isinstance(names, list)
                or not all(isinstance(n, str) for n in names)
                or len(names) > MAX_TOKEN_REPOSITORIES
            ):
                return 422, {
                    "message": "Validation Failed",
                    "errors": [{"message": "repositories is a list of at most 500 names"}],
                }
            missing = [n for n in names if n.lower() not in owned]
            if missing:
                return 422, {
                    "message": "Validation Failed",
                    "errors": [
                        {"message": f"The repository '{n}' is not available to this installation"}
                        for n in missing
                    ],
                }
        granted = dict(installation.permissions)
        wanted = asked.get("permissions")
        if wanted is None:
            permissions = granted
        else:
            if not isinstance(wanted, dict) or any(
                _LEVELS.get(str(level), 99) > _LEVELS.get(granted.get(str(name), ""), 0)
                for name, level in wanted.items()
            ):
                return 422, {
                    "message": "The permissions requested are not granted to this installation."
                }
            permissions = {str(k): str(v) for k, v in wanted.items()}
            permissions.setdefault("metadata", "read")
        scope = None if names is None else frozenset(n.lower() for n in names)
        value = "ghs_" + secrets.token_hex(18)
        expires = self._now() + TOKEN_LIFETIME
        self.tokens[value] = Token(installation_id, scope, permissions, expires)
        reached = (
            [owned[n] for n in sorted(scope)]
            if scope is not None
            else sorted(installation.repositories)
        )
        return 201, {
            "token": value,
            "expires_at": datetime.fromtimestamp(expires, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "permissions": permissions,
            "repository_selection": "selected" if scope is not None else installation.selection,
            "repositories": [
                {"name": n, "full_name": f"{installation.account}/{n}", "private": True}
                for n in reached
            ],
        }

    def _app_jwt(self, authorization: str) -> str | None:
        """Why an App JWT is not one GitHub accepts, or None."""
        scheme, _, jwt = authorization.partition(" ")
        if scheme.lower() not in ("bearer",) or jwt.count(".") != 2:
            return "A JSON web token could not be decoded"
        head, payload, _signature = jwt.split(".")
        try:
            header = json.loads(_unb64(head))
            claims = json.loads(_unb64(payload))
        except (ValueError, binascii.Error):
            return "A JSON web token could not be decoded"
        if header.get("alg") != "RS256" or claims.get("iss") != self.app_id:
            return "A JSON web token could not be decoded"
        issued, expires = claims.get("iat"), claims.get("exp")
        now = self._now()
        if (
            not isinstance(issued, int | float)
            or not isinstance(expires, int | float)
            or expires - issued > 600
        ):
            return "'Expiration time' claim ('exp') must be a numeric value representing the future time at which the assertion expires"
        if expires <= now:
            return "'Expiration time' claim ('exp') is too far in the past"
        return None

    def _token(self, authorization: str) -> Token | tuple[int, Any]:
        """The token a request presents, or the 401 GitHub answers."""
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() not in ("token", "bearer"):
            return 401, {"message": "Requires authentication"}
        known = self.tokens.get(value.strip())
        if known is None or known.expires_at <= self._now():
            return 401, {"message": "Bad credentials"}
        return known

    def _owner(self, token: Token) -> Installation:
        return self.installations[token.installation]

    def _reaches(self, token: Token, full: str) -> bool:
        """Whether a token sees ``owner/repo``."""
        installation = self._owner(token)
        owner, _, name = full.partition("/")
        if owner != installation.account.lower():
            return False
        owned = {n.lower() for n in installation.repositories}
        if name not in owned:
            return False
        return token.repositories is None or name in token.repositories

    def _reachable(self, token: Token) -> dict[str, Any]:
        installation = self._owner(token)
        names = [
            n
            for n in sorted(installation.repositories)
            if token.repositories is None or n.lower() in token.repositories
        ]
        return {
            "total_count": len(names),
            "repository_selection": "selected" if token.repositories else installation.selection,
            "repositories": [
                {"name": n, "full_name": f"{installation.account}/{n}", "private": True}
                for n in names
            ],
        }

    def _advertise(self, owner: str, repo: str, authorization: str) -> tuple[int, Any]:
        """What a clone starts with: the refs, for a token that reads the contents."""
        scheme, _, encoded = authorization.partition(" ")
        if scheme.lower() != "basic":
            return 401, {"message": "Requires authentication"}
        try:
            user, _, value = base64.b64decode(encoded).decode().partition(":")
        except (ValueError, binascii.Error):
            return 401, {"message": "Requires authentication"}
        known = self.tokens.get(value)
        if user != "x-access-token" or known is None or known.expires_at <= self._now():
            return 401, {"message": "Bad credentials"}
        if not self._reaches(known, f"{owner}/{repo}".lower()):
            return 401, {"message": "Not Found"}
        if known.permissions.get("contents") not in ("read", "write"):
            return 401, {"message": "Resource not accessible by integration"}
        return 200, {"advertised": f"{owner}/{repo}"}

    def _need(self, token: Token, permission: str, level: str = "write") -> tuple[int, Any] | None:
        held = _LEVELS.get(token.permissions.get(permission, ""), 0)
        if held < _LEVELS[level]:
            return 403, {
                "message": "Resource not accessible by integration",
                "documentation_url": "https://docs.github.com/rest",
            }
        return None

    def _repo(self, method: str, full: str, rest: str, token: Token, body: Any) -> tuple[int, Any]:
        """Deployments, their statuses and issue comments."""
        if rest == "/deployments" and method == "POST":
            denied = self._need(token, "deployments")
            if denied:
                return denied
            if not isinstance(body, dict) or not body.get("ref"):
                return 422, {"message": "Validation Failed", "errors": [{"field": "ref"}]}
            number = self._next_id()
            record = {
                "id": number,
                "ref": body["ref"],
                "sha": str(body["ref"])[:40],
                "environment": body.get("environment", "production"),
                "description": body.get("description"),
            }
            self.deployments.setdefault(full, []).append(record)
            return 201, record
        found = re.fullmatch(r"/deployments/(\d+)/statuses", rest)
        if found and method == "POST":
            denied = self._need(token, "deployments")
            if denied:
                return denied
            number = int(found.group(1))
            if not any(d["id"] == number for d in self.deployments.get(full, [])):
                return 404, {"message": "Not Found"}
            state = body.get("state") if isinstance(body, dict) else None
            description = body.get("description", "") if isinstance(body, dict) else ""
            if state not in _STATES or len(str(description)) > 140:
                return 422, {"message": "Validation Failed"}
            status = {"id": self._next_id(), "state": state, **(body or {})}
            self.deployment_statuses.setdefault((full, number), []).append(status)
            return 201, status
        found = re.fullmatch(r"/issues/(\d+)/comments", rest)
        if found and method == "POST":
            denied = self._need(token, "pull_requests")
            if denied:
                return denied
            number = self._next_id()
            self.comments.setdefault(full, {})[number] = str((body or {}).get("body", ""))
            return 201, {"id": number, "body": self.comments[full][number]}
        found = re.fullmatch(r"/issues/comments/(\d+)", rest)
        if found and method == "PATCH":
            denied = self._need(token, "pull_requests")
            if denied:
                return denied
            number = int(found.group(1))
            if number not in self.comments.get(full, {}):
                return 404, {"message": "Not Found"}
            self.comments[full][number] = str((body or {}).get("body", ""))
            return 200, {"id": number, "body": self.comments[full][number]}
        if rest == "" and method == "GET":
            return 200, {"full_name": full, "private": True}
        return 404, {"message": "Not Found"}


def _unb64(part: str) -> bytes:
    """Decode a JWT's base64url segment."""
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def serve(
    fake: FakeGitHubApi, host: str = "127.0.0.1", port: int = 0, tls: ssl.SSLContext | None = None
) -> tuple[ThreadingHTTPServer, str]:
    """
    Serve the fake over HTTP, in a thread.

    Args:
        fake: The GitHub to serve.
        host: The address to listen on.
        port: The port; 0 for any.
        tls: A server-side context to serve HTTPS with.

    Returns:
        The server, to shut down, and its base URL.
    """

    class Handler(BaseHTTPRequestHandler):
        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = None
            headers = {key.lower(): value for key, value in self.headers.items()}
            status, answer = fake.handle(self.command, self.path, headers, body)
            data = json.dumps(answer).encode() if answer is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _serve
        do_POST = _serve
        do_PATCH = _serve
        do_PUT = _serve
        do_DELETE = _serve

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the output quiet."""

    server = ThreadingHTTPServer((host, port), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    scheme = "https" if tls is not None else "http"
    return server, f"{scheme}://{host}:{server.server_address[1]}"
