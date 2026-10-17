# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The HTTP requests the push-style channels send.

:mod:`noust.core.notifier` delivers; this module only *builds* the request for
the channels that are one request to a fixed API: Pushover, ntfy, Gotify,
Microsoft Teams, Mattermost, Matrix, Lark and PagerDuty. Building does no I/O, so every function is tested by reading
the :class:`urllib.request.Request` it returns, and the SSRF guard, the
deadline and the redirect rule stay where they were: in the notifier's one
``_send``, which every request passes through.

Credentials never go in a URL (a URL is written into proxy logs): Pushover's
token and user key are form fields, ntfy's and Gotify's tokens are headers.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request

from noust import __version__
from noust.core.notifications.model import Notification
from noust.core.notifications.render import (
    gotify,
    lark,
    matrix,
    mattermost,
    ntfy,
    pagerduty,
    pushover,
    teams,
)

#: Identifies Noust to the receiving endpoint.
USER_AGENT = f"noust-notifier/{__version__}"

#: Pushover's one endpoint.
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


class CheckedRequest(Request):
    """
    A request whose success answer still has to be read.

    Some services answer an error with HTTP 200 and say so in the body (Lark
    does). ``check_reply`` is called with the start of the body of a 2xx answer
    and raises when it is a refusal; the one sender
    (:meth:`~noust.core.notifier.Notifier._send`) is what calls it.

    Attributes:
        check_reply: Judges the answer's body.
    """

    def __init__(self, *args: Any, check_reply: Callable[[bytes], None], **kwargs: Any) -> None:
        """
        Args:
            args: The arguments of :class:`urllib.request.Request`.
            check_reply: Judges the body of a 2xx answer; raises on a refusal.
            kwargs: The keyword arguments of :class:`urllib.request.Request`.
        """
        super().__init__(*args, **kwargs)
        self.check_reply = check_reply


#: The most of an answer that is read to be judged.
MAX_REPLY_BYTES = 16 * 1024


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
    method: str = "POST",
    check_reply: Callable[[bytes], None] | None = None,
) -> Request:
    """
    Build a request with this module's identity.

    Args:
        url: Destination URL, already validated.
        body: The exact bytes to send.
        extra_headers: Headers to add (a delivery id, a signature, a token).
        content_type: The body's type.
        method: The HTTP method; ``POST`` unless the service wants another.
        check_reply: Judges the body of a success answer, for a service that
            reports its errors there (see :class:`CheckedRequest`).

    Returns:
        The request, ready for the opener.
    """
    headers = {"Content-Type": content_type, "User-Agent": USER_AGENT}
    headers.update(extra_headers or {})
    if check_reply is not None:
        return CheckedRequest(
            url, data=body, headers=headers, method=method, check_reply=check_reply
        )
    return Request(url, data=body, headers=headers, method=method)


def json_request(
    url: str,
    payload: dict[str, Any],
    extra_headers: dict[str, str] | None = None,
    *,
    method: str = "POST",
    check_reply: Callable[[bytes], None] | None = None,
) -> Request:
    """
    Build a JSON request from a payload.

    Args:
        url: Destination URL, already validated.
        payload: JSON-serialisable body.
        extra_headers: Headers to add.
        method: The HTTP method.
        check_reply: See :func:`post`.

    Returns:
        The request, ready for the opener.
    """
    return post(
        url,
        json.dumps(payload).encode("utf-8"),
        extra_headers,
        method=method,
        check_reply=check_reply,
    )


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


def mattermost_request(url: str, notification: Notification) -> Request:
    """
    Build the Mattermost incoming-webhook request.

    Args:
        url: The webhook's URL.
        notification: The notification to deliver.

    Returns:
        The request.

    Raises:
        ValueError: When the URL is not HTTP(S).
    """
    require_http_url(url, "notifications.channels.<mattermost>.webhook_url")
    return json_request(url, mattermost.render(notification))


#: ``!opaque:server`` (the id Element shows under Advanced), with the port a
#: homeserver may carry. An alias (``#room:server``) is not accepted: sending
#: needs the id, and resolving an alias is another request to another place.
MATRIX_ROOM_ID = re.compile(r"![A-Za-z0-9._~=+/-]+:[A-Za-z0-9.-]+(?::\d{1,5})?")


def matrix_request(
    homeserver: str,
    room_id: str,
    access_token: str,
    notification: Notification,
    channel: str,
) -> Request:
    """
    Build the Matrix ``send`` request.

    Args:
        homeserver: The homeserver's base URL, ``https://matrix.example.org``.
        room_id: The room's id.
        access_token: A token of the account that posts.
        notification: The notification to deliver.
        channel: The channel's name; with the notification's id it is the
            transaction id, so a retry of one delivery is one message and two
            channels sharing a room and a token are two.

    Returns:
        The request; the token is a bearer header.

    Raises:
        ValueError: When the URL is not HTTP(S), the room is not an id or
            there is no token.
    """
    require_http_url(homeserver, "notifications.channels.<matrix>.homeserver")
    if not MATRIX_ROOM_ID.fullmatch(room_id):
        raise ValueError(
            "notifications.channels.<matrix>.room_id must be a room id such as "
            "!abcdefg:example.org (Room settings, Advanced)"
        )
    if not access_token:
        raise ValueError("notifications.channels.<matrix>.access_token is the posting account's")
    transaction = quote(f"noust-{notification.id}-{channel}", safe="")
    url = (
        f"{homeserver.rstrip('/')}/_matrix/client/v3/rooms/{quote(room_id, safe='')}"
        f"/send/m.room.message/{transaction}"
    )
    return json_request(
        url,
        matrix.render(notification),
        {"Authorization": f"Bearer {access_token}"},
        method="PUT",
    )


def lark_request(
    url: str, notification: Notification, secret: str = "", *, now: float | None = None
) -> Request:
    """
    Build the Lark (Feishu) custom-bot request.

    Args:
        url: The bot's webhook URL.
        notification: The notification to deliver.
        secret: The bot's signing secret, when signature verification is on.
        now: Epoch seconds; the clock by default.

    Returns:
        The request, which judges Lark's answer: a refusal comes with HTTP 200.

    Raises:
        ValueError: When the URL is not HTTP(S).
    """
    require_http_url(url, "notifications.channels.<lark>.webhook_url")
    body = lark.render(notification)
    if secret:
        body = lark.sign(body, int(time.time() if now is None else now), secret)
    return json_request(url, body, check_reply=lark.check_reply)


def _check_pagerduty(routing_key: str, region: str) -> str:
    """
    Args:
        routing_key: The integration's key.
        region: ``us``, ``eu`` or empty.

    Returns:
        The endpoint of the region.

    Raises:
        ValueError: When the key is not the 32 characters PagerDuty issues, or
            the region is not one.
    """
    if not routing_key.isalnum() or len(routing_key) != 32:
        raise ValueError(
            "notifications.channels.<pagerduty>.routing_key must be the 32-character "
            "integration key of a service (Events API v2)"
        )
    endpoint = pagerduty.ENDPOINTS.get(region or "us")
    if endpoint is None:
        raise ValueError("notifications.channels.<pagerduty>.region must be us or eu")
    return endpoint


def pagerduty_request(
    routing_key: str, notification: Notification, region: str = "us"
) -> Request | None:
    """
    Build the PagerDuty Events API v2 request.

    Args:
        routing_key: The integration's key.
        notification: The notification to deliver.
        region: ``us`` or ``eu``, the service region of the account.

    Returns:
        The request, or None when the notification is not an event for a pager.

    Raises:
        ValueError: When the key or the region is not valid.
    """
    endpoint = _check_pagerduty(routing_key, region)
    decided = pagerduty.plan(notification)
    if decided is None:
        return None
    return json_request(endpoint, pagerduty.render(notification, routing_key, decided))


def pagerduty_acknowledge_request(routing_key: str, key: str, region: str = "us") -> Request:
    """
    Build the request that acknowledges an incident.

    Args:
        routing_key: The integration's key.
        key: The incident's ``dedup_key``.
        region: ``us`` or ``eu``.

    Returns:
        The request.

    Raises:
        ValueError: When the key or the region is not valid.
    """
    endpoint = _check_pagerduty(routing_key, region)
    return json_request(endpoint, pagerduty.acknowledge(routing_key, key))
