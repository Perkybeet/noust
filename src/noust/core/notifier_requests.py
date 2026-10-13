# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The HTTP requests the push-style channels send.

:mod:`noust.core.notifier` delivers; this module only *builds* the request for
the channels that are one POST to a fixed API: Pushover, ntfy, Gotify and
Microsoft Teams. Building does no I/O, so every function is tested by reading
the :class:`urllib.request.Request` it returns, and the SSRF guard, the
deadline and the redirect rule stay where they were: in the notifier's one
``_send``, which every request passes through.

Credentials never go in a URL (a URL is written into proxy logs): Pushover's
token and user key are form fields, ntfy's and Gotify's tokens are headers.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request

from noust import __version__
from noust.core.notifications.model import Notification
from noust.core.notifications.render import gotify, ntfy, pushover, teams

#: Identifies Noust to the receiving endpoint.
USER_AGENT = f"noust-notifier/{__version__}"

#: Pushover's one endpoint.
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def require_http_url(url: str, setting: str) -> str:
    """
    Refuse a URL whose scheme is not plain HTTP(S).

    ``urlopen`` also follows ``file://`` and ``ftp://``; a configuration value
    must not be able to turn a notification into a local file read. Bandit's
    S310 is ignored project-wide on the promise that every call site validates
    the scheme, and this is that validation.

    Args:
        url: The configured URL.
        setting: Dotted configuration path, for the error message.

    Returns:
        The URL, unchanged.

    Raises:
        ValueError: When the scheme is anything but http or https.
    """
    if urlparse(url).scheme.lower() not in ("http", "https"):
        raise ValueError(f"{setting} must be an http:// or https:// URL")
    return url


def post(
    url: str,
    body: bytes,
    extra_headers: dict[str, str] | None = None,
    *,
    content_type: str = "application/json",
) -> Request:
    """
    Build a POST with this module's identity.

    Args:
        url: Destination URL, already validated.
        body: The exact bytes to send.
        extra_headers: Headers to add (a delivery id, a signature, a token).
        content_type: The body's type.

    Returns:
        The request, ready for the opener.
    """
    headers = {"Content-Type": content_type, "User-Agent": USER_AGENT}
    headers.update(extra_headers or {})
    return Request(url, data=body, headers=headers, method="POST")


def json_request(
    url: str, payload: dict[str, Any], extra_headers: dict[str, str] | None = None
) -> Request:
    """
    Build a JSON POST from a payload.

    Args:
        url: Destination URL, already validated.
        payload: JSON-serialisable body.
        extra_headers: Headers to add.

    Returns:
        The request, ready for the opener.
    """
    return post(url, json.dumps(payload).encode("utf-8"), extra_headers)


def pushover_request(
    user_key: str, app_token: str, notification: Notification, device: str = ""
) -> Request:
    """
    Build the Pushover message request.

    Args:
        user_key: The user's (or group's) key.
        app_token: The application's token.
        notification: The notification to deliver.
        device: A device name to deliver to; all of the user's when empty.

    Returns:
        The request. The credentials are form fields, not part of the URL.

    Raises:
        ValueError: When a key or a token is empty or has the shape of
            something that is not one (Pushover's are 30 letters and digits).
    """
    for label, value in (("user_key", user_key), ("app_token", app_token)):
        if not value or not value.isalnum():
            raise ValueError(
                f"notifications.channels.<pushover>.{label} must be the 30 letters and digits "
                "Pushover shows for it"
            )
    fields = {"token": app_token, "user": user_key, **pushover.render(notification)}
    if device:
        fields["device"] = device
    return post(
        PUSHOVER_URL,
        urlencode(fields).encode("utf-8"),
        content_type="application/x-www-form-urlencoded",
    )


def ntfy_request(url: str, notification: Notification, token: str = "") -> Request:
    """
    Build the ntfy publish request.

    Args:
        url: The topic's URL, ``https://ntfy.sh/alerts`` or the operator's own
            server, a sub-path included (``https://example.com/ntfy/alerts``).
        notification: The notification to deliver.
        token: An access token for a topic that needs one.

    Returns:
        The request: JSON to the server's root, the topic in the body.

    Raises:
        ValueError: When the URL is not HTTP(S) or names no topic.
    """
    setting = "notifications.channels.<ntfy>.webhook_url"
    require_http_url(url, setting)
    parsed = urlparse(url)
    prefix, _, topic = parsed.path.strip("/").rpartition("/")
    if not topic:
        raise ValueError(f"{setting} must end with the topic, such as https://ntfy.sh/alerts")
    root = f"{parsed.scheme}://{parsed.netloc}/" + (f"{prefix}/" if prefix else "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return json_request(root, ntfy.render(notification, topic), headers)


def gotify_request(url: str, notification: Notification, token: str) -> Request:
    """
    Build the Gotify message request.

    Args:
        url: The server's URL, ``https://gotify.example.com``; a URL that
            already ends in ``/message`` is used as it is.
        notification: The notification to deliver.
        token: The application's token.

    Returns:
        The request; the token is the ``X-Gotify-Key`` header.

    Raises:
        ValueError: When the URL is not HTTP(S) or there is no token.
    """
    require_http_url(url, "notifications.channels.<gotify>.webhook_url")
    if not token:
        raise ValueError("notifications.channels.<gotify>.token is the application's token")
    target = url.rstrip("/")
    if not target.endswith("/message"):
        target += "/message"
    return json_request(target, gotify.render(notification), {"X-Gotify-Key": token})


def teams_request(url: str, notification: Notification) -> Request:
    """
    Build the Microsoft Teams (Workflows webhook) request.

    Args:
        url: The workflow's URL.
        notification: The notification to deliver.

    Returns:
        The request.

    Raises:
        ValueError: When the URL is not HTTP(S).
    """
    require_http_url(url, "notifications.channels.<teams>.webhook_url")
    return json_request(url, teams.render(notification))
