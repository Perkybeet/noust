# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Creating this server's GitHub App with GitHub's manifest flow.

The console asks for a manifest, posts it as a form to GitHub from the
operator's browser, and GitHub sends the browser back to the console with a
one-time ``code``, which the console hands here to exchange for the App's
credentials. Every redirect is the browser's, so the flow works through an SSH
tunnel: GitHub never has to reach this server.

The ``state`` WASM generates for each manifest is remembered for ten minutes
and consumed by the exchange, so a code that did not come back from a flow
this server started is refused.
"""

from __future__ import annotations

import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from wasm.core.exceptions import IntegrationError, ValidationError
from wasm.core.secrets import SecretStore
from wasm.core.store import GitHubAppRecord, get_store
from wasm.integrations.github.app import (
    CLIENT_SECRET,
    PRIVATE_KEY_SECRET,
    WEBHOOK_SECRET,
    forget_tokens,
    write_meta,
)
from wasm.integrations.github.client import WEB_URL, get_client, json_object

#: GitHub's limit on an App's name.
MAX_APP_NAME = 34

#: Seconds a manifest's state stays redeemable.
STATE_TTL = 600

#: Where GitHub sends the browser back to, on the console.
CALLBACK_PATH = "/integrations/github/callback"

#: What the App may do: read code, report deployments and statuses, comment
#: on pull requests. Nothing that writes code.
PERMISSIONS: dict[str, str] = {
    "contents": "read",
    "metadata": "read",
    "deployments": "write",
    "statuses": "write",
    "pull_requests": "write",
}

#: Events the App subscribes to. Installation events reach every App
#: without subscribing.
EVENTS: tuple[str, ...] = ("push", "pull_request")

_ORGANIZATION = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_CODE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def app_name(hostname: str | None = None) -> str:
    """
    Name the App after this server.

    Args:
        hostname: The host name; this machine's when None.

    Returns:
        ``wasm-<hostname>``, lower case, anything but letters, digits and
        hyphens made a hyphen, cut to GitHub's 34 characters.
    """
    host = (hostname or socket.gethostname() or "server").split(".")[0].lower()
    host = re.sub(r"[^a-z0-9-]+", "-", host).strip("-") or "server"
    return f"wasm-{host}"[:MAX_APP_NAME].rstrip("-")


def console_origin(value: str) -> str:
    """
    Check the console origin the browser reported.

    Args:
        value: ``scheme://host[:port]`` as the console saw its own address.

    Returns:
        The origin, without a trailing slash.

    Raises:
        ValidationError: It is not an http(s) origin.
    """
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ValidationError(
            f"Not a console origin: {value!r}",
            details="Expected scheme://host[:port], as the browser reports location.origin.",
            field="origin",
        )
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class _Issued:
    """A manifest handed out: when, and the webhook URL it named."""

    at: float
    hooks_url: str | None


class _States:
    """States of manifests handed out, redeemable once within :data:`STATE_TTL`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _Issued] = {}

    def issue(self, hooks_url: str | None) -> str:
        """
        Generate and remember a state.

        Args:
            hooks_url: The webhook URL the manifest names, if any.

        Returns:
            The state.
        """
        state = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._states = {k: v for k, v in self._states.items() if now - v.at < STATE_TTL}
            self._states[state] = _Issued(at=now, hooks_url=hooks_url)
        return state

    def consume(self, state: str) -> _Issued | None:
        """
        Redeem a state.

        Args:
            state: As GitHub handed it back.

        Returns:
            What was issued with it, or None when it was not issued here, is
            older than the TTL or was already redeemed.
        """
        with self._lock:
            issued = self._states.pop(state, None)
        if issued is None or time.monotonic() - issued.at >= STATE_TTL:
            return None
        return issued

    def clear(self) -> None:
        """Forget every state (tests)."""
        with self._lock:
            self._states.clear()


states = _States()


@dataclass(frozen=True)
class ManifestStart:
    """
    What the console posts to GitHub.

    Attributes:
        manifest: The manifest, to send as the form field ``manifest``
            (JSON-encoded).
        post_url: Where to post the form, ``state`` included.
        state: The state GitHub will hand back with the code.
    """

    manifest: dict[str, Any]
    post_url: str
    state: str


def build_manifest(
    origin: str, *, hooks_url: str | None, name: str | None = None
) -> dict[str, Any]:
    """
    Build the App's manifest.

    Args:
        origin: The console's origin.
        hooks_url: The public URL of ``/hooks/github``, or None when this
            server has none yet (``wasm web expose-hooks``): the App is then
            created without a webhook, and one is added when it gets one.
        name: The App's name; :func:`app_name` by default.

    Returns:
        The manifest.
    """
    callback = f"{origin}{CALLBACK_PATH}"
    manifest: dict[str, Any] = {
        "name": name or app_name(),
        "url": origin,
        "description": "WASM on this server: deploys, previews and deployment statuses.",
        "redirect_url": callback,
        # GitHub sends the browser here after an installation, with
        # installation_id and setup_action; the same console page handles both.
        "setup_url": callback,
        "setup_on_update": True,
        "public": False,
        "default_permissions": dict(PERMISSIONS),
        "default_events": list(EVENTS),
    }
    if hooks_url:
        manifest["hook_attributes"] = {"url": hooks_url, "active": True}
    return manifest


def start(origin: str, *, hooks_url: str | None, organization: str | None = None) -> ManifestStart:
    """
    Begin creating the App: a manifest, a state and where to post them.

    Args:
        origin: The console's origin, as the browser reports it.
        hooks_url: Public URL of ``/hooks/github``, or None.
        organization: Create the App owned by this organisation instead of
            the operator's personal account.

    Returns:
        What the console posts to GitHub.

    Raises:
        ValidationError: The origin or organisation is malformed.
    """
    checked = console_origin(origin)
    if organization is not None and not _ORGANIZATION.match(organization):
        raise ValidationError(
            f"Not a GitHub organisation name: {organization!r}",
            details="An organisation's login: letters, digits and single hyphens.",
            field="organization",
        )
    state = states.issue(hooks_url)
    base = (
        f"{WEB_URL}/organizations/{organization}/settings/apps/new"
        if organization
        else f"{WEB_URL}/settings/apps/new"
    )
    return ManifestStart(
        manifest=build_manifest(checked, hooks_url=hooks_url),
        post_url=f"{base}?{urlencode({'state': state})}",
        state=state,
    )


def convert(code: str, state: str) -> GitHubAppRecord:
    """
    Exchange the code GitHub handed back for the App's credentials, and keep them.

    Args:
        code: The ``code`` query parameter of the callback.
        state: The ``state`` query parameter of the callback.

    Returns:
        The App as stored.

    Raises:
        ValidationError: The state was not issued here, expired or was used,
            or the code is malformed.
        IntegrationError: GitHub refused the code (they are single-use and
            expire after an hour) or answered no credentials.
    """
    issued = states.consume(state)
    if issued is None:
        raise ValidationError(
            "This GitHub callback does not belong to an App creation started here",
            details="Start again from the console: a creation expires after ten minutes "
            "and its callback can be used once.",
            field="state",
        )
    if not _CODE.match(code):
        raise ValidationError("Not a manifest code", field="code")

    answer = get_client().request("POST", f"/app-manifests/{code}/conversions")
    if not isinstance(answer, dict):
        raise IntegrationError("GitHub answered no App for the code")
    try:
        app_id = int(answer["id"])
        slug = str(answer["slug"])
        pem = str(answer["pem"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationError(
            "GitHub's answer to the App creation lacks its credentials",
            details=f"Missing or malformed field: {exc}",
        ) from None

    store = SecretStore()
    store.write(PRIVATE_KEY_SECRET, pem)
    if answer.get("webhook_secret"):
        store.write(WEBHOOK_SECRET, str(answer["webhook_secret"]))
    else:
        store.delete(WEBHOOK_SECRET)
    if answer.get("client_secret"):
        store.write(CLIENT_SECRET, str(answer["client_secret"]))
    else:
        store.delete(CLIENT_SECRET)

    owner = json_object(answer.get("owner"))
    forget_tokens()
    record = get_store().save_github_app(
        GitHubAppRecord(
            app_id=app_id,
            slug=slug,
            name=answer.get("name"),
            owner=owner.get("login"),
            html_url=answer.get("html_url"),
            client_id=answer.get("client_id"),
        )
    )
    write_meta(
        owner_type=owner.get("type"),
        webhook_url=issued.hooks_url,
        webhook_active=bool(issued.hooks_url) and bool(answer.get("webhook_secret")),
    )
    return record
