# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Multi-channel notifications for operational events.

One transport, stdlib only. Deploys, the monitor, the backup jobs and the
fleet all build a :class:`~noust.core.notifications.model.Notification` (a
structured message: state, one sentence, facts, a verbatim excerpt, a link -
see :mod:`noust.core.notifications`) and publish it here instead of growing
their own delivery code; a pure renderer per channel
(:mod:`noust.core.notifications.render`) lays it out, and the panel's settings
page configures the channels. The per-channel "send a test" button is
:meth:`Notifier.test_channel`.

Channels are *instances* of a type (``notifications.channels.<name>``, see
:mod:`noust.core.notifications.instances`): there can be a Slack for production
and another for staging, each with its own event switches, route (by
application, project, environment or tag) and quiet hours
(:mod:`noust.core.notifications.routing`). A name that is a type's own
(``slack``) is that type's default instance, as it always was.

Every delivery is recorded in the ledger (:mod:`noust.core.notification_ledger`)
and one that failed for a reason that may pass is tried again by
:meth:`Notifier.deliver_due` with a wait that grows; a failure to notify never
reaches the caller, and one that cannot be fixed by waiting is an audit event.

The types and what each one receives:

- **webhook** - the operator's own endpoint. POST JSON, ``version: 1``: the
  keys it always had (``event``, ``title``, ``body``, ``domain``, ``ts``) and
  the structure of the message besides. With ``notifications.channels.webhook.secret``
  set, every delivery is signed (``X-Noust-Signature: sha256=...``).
  Contract: :mod:`noust.core.notifications.render.webhook`.
- **slack** - Slack incoming webhook: Block Kit in an attachment with the
  state's colour strip.
- **discord** - Discord webhook: one embed, colour by severity. A role and/or a
  user the operator names (``mention_role_id``, ``mention_user_id``) is pinged
  by a critical event, and only by one; if Discord refuses the embed the alert
  is sent once more as plain text.
- **telegram** - Bot API ``sendMessage`` in HTML, the console link as a button,
  silent unless it needs attention; retried once as plain text if Telegram
  refuses the markup. A forum supergroup's topic (``thread_id``, or one per
  event in ``threads``) keeps deploys, backups and alerts apart.
- **email** - delegates to :class:`noust.monitor.email_notifier.EmailNotifier`,
  so there is exactly one SMTP implementation and one email layout (the
  monitor's observation report uses it too).
- **pushover**, **ntfy**, **gotify**, **teams** - push services and Microsoft
  Teams (a Workflows webhook): one POST each, built in
  :mod:`noust.core.notifier_requests`, with the credentials in a form field or
  a header and never in the URL.
- **mattermost**, **matrix**, **lark** - the other team chats: Mattermost's
  attachment (Block Kit is not read there), a Matrix room message with a
  formatted body, and a Lark (Feishu) card, signed when the bot asks for it.
- **pagerduty** - Events API v2 for the person on call: a problem triggers an
  incident, a recovery resolves it by the same key, and what is neither is not
  sent (:mod:`noust.core.notifications.render.pagerduty`).

Four rules hold everywhere:

- Every request has a deadline (:data:`NOTIFY_TIMEOUT`). An endpoint that
  stopped answering must not stall the deploy that fired the event.
- A failing channel is logged and skipped. The other channels still get the
  event and the caller never sees the failure.
- Secrets never reach a log. The Telegram bot token is part of the request
  URL and urllib quotes the URL in some of its errors, so error text is
  scrubbed before it is logged or returned. The webhook's signing secret is
  scrubbed the same way.
- Every channel has its own limit and none of them may cost the message its
  link: each renderer stays inside its platform's limits by shortening the
  excerpt first, never by cutting the end.

A fourth rule is enforced by :func:`_require_public_destination` rather than
by convention: a notification channel is *configured* by whoever can write to
``/etc/noust/config.yaml``, but *delivering* one means this process making an
outbound request with attacker-influenced content, from the same machine that
runs systemd as root. A webhook URL of ``http://169.254.169.254/latest/...``
or ``http://127.0.0.1:8080/api/...`` would turn "send a notification" into a
way to reach the cloud metadata service or the panel's own loopback-only
surface, so every destination is resolved and checked against
:data:`_FORBIDDEN_NETWORKS` before a request is built - and every redirect
hop is checked again by :class:`_SafeRedirectHandler`, because a destination
that starts out public and then answers with a 302 would otherwise reach this
guard exactly once. An operator who genuinely wants to notify a private
address (a webhook on the same network) lists the host under
``notifications.allow_private_hosts``.

:meth:`Notifier.test_channel` additionally never returns the remote response
body, only the status: unlike :func:`_describe_error`\\ 's use in
:meth:`Notifier.notify` (whose result only ever reaches a log an operator
already trusts), the test button's answer is rendered straight into the
settings page, and reflecting an arbitrary response body there is a second
way for a reachable-but-not-quite-blocked destination to hand content back to
whoever is looking at the panel.

Tests inject ``opener`` instead of opening sockets, and monkeypatch
:func:`_resolve_host` instead of resolving a real name; the suite never talks
to the network.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from http.client import HTTPException
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from noust.core import audit, outbound
from noust.core.background import BackgroundQueue
from noust.core.config import Config
from noust.core.exceptions import EmailRefused, NoustError
from noust.core.messages import Locale
from noust.core.notification_ledger import Delivery, DeliveryLedger, backoff
from noust.core.notification_placement import store_placement
from noust.core.notifications.composers import compose_test, server_fact
from noust.core.notifications.composers_ops import compose_digest
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.excerpt import make_excerpt
from noust.core.notifications.instances import (
    TYPE_NAMES,
    TYPES,
    ChannelInstance,
    resolve_instances,
)
from noust.core.notifications.model import (
    EVENT_KINDS,
    OFF_BY_DEFAULT,
    TEST_KIND,
    Notification,
    State,
)
from noust.core.notifications.render import discord, pagerduty, slack, telegram, webhook
from noust.core.notifications.routing import Placement, Routing, decide
from noust.core.notifier_requests import (
    MAX_REPLY_BYTES,
    USER_AGENT,
    gotify_request,
    json_request,
    lark_request,
    matrix_request,
    mattermost_request,
    ntfy_request,
    pagerduty_request,
    post,
    pushover_request,
    require_http_url,
    teams_request,
)
from noust.validators.discord import validate_discord_id
from noust.validators.telegram import validate_telegram_chat_id

if TYPE_CHECKING:
    from noust.monitor.email_notifier import EmailNotifier

logger = logging.getLogger(__name__)

#: Deadline for every notification POST, in seconds. These endpoints answer in
#: well under a second when healthy; anything slower is an outage on their
#: side, and the caller's work must not wait on it.
NOTIFY_TIMEOUT = 10

#: Held notifications released together are sent as one digest from this many.
DIGEST_MINIMUM = 4

#: The ledger is tidied at most this often (seconds).
PURGE_EVERY_SECONDS = 3_600

#: What a ledger write can raise. A notification is never lost to a record that
#: cannot be written: the notifier logs this and delivers anyway.
_LEDGER_ERRORS = (sqlite3.Error, OSError, NoustError)

#: The kinds an operator can switch off under ``notifications.events`` live in
#: :mod:`noust.core.notifications.model` (:data:`EVENT_KINDS`, imported above and
#: re-exported from here for the callers that always imported it from this
#: module). ``DEFAULT_CONFIG["notifications"]["events"]`` spells out the same
#: names; config.py cannot import this module (this module reads its settings
#: from config.py), so the agreement is pinned by a test in tests/test_notifier.py.

__all__ = [
    "CHANNELS",
    "EVENT_KINDS",
    "NOTIFICATION_QUEUE",
    "NOTIFY_TIMEOUT",
    "TEST_KIND",
    "USER_AGENT",
    "NotificationEvent",
    "Notifier",
    "SweepReport",
    "TelegramChat",
    "fresh_config",
    "guarded_opener",
    "is_transient",
    "legacy_notification",
    "notify_composed",
    "notify_in_background",
    "notify_now",
    "send_guarded",
    "validate_telegram_bot_token",
]

#: The channel types, in delivery order. Each is also the name of that type's
#: default instance under ``notifications.channels``; any other name there is a
#: further instance of one of them (see :mod:`noust.core.notifications.instances`).
CHANNELS: tuple[str, ...] = TYPE_NAMES

#: ``<bot id>:<secret>``, the only shape the Bot API issues. The token becomes
#: part of the request path, so anything else is refused before it can reshape
#: the URL. Always applied with ``fullmatch``: ``$`` also matches just before a
#: trailing newline, which let a token ending in a line break through.
_TELEGRAM_TOKEN_RE = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")

_TELEGRAM_API = "https://api.telegram.org"


def validate_telegram_bot_token(value: str) -> str:
    """
    Check that a value is, in full, the shape of a Bot API token.

    The one rule for a token's shape: the settings endpoint applies it on
    save, and the notifier again before the token becomes part of a URL, for
    a value written some other way.

    Args:
        value: The bot token.

    Returns:
        The value, unchanged.

    Raises:
        ValueError: When it is not ``<digits>:<secret>`` from its first
            character to its last. The value itself is never quoted: it is a
            credential.
    """
    if not _TELEGRAM_TOKEN_RE.fullmatch(value):
        raise ValueError(
            "notifications.channels.telegram.bot_token does not look like a "
            "Telegram bot token (expected <digits>:<secret>)"
        )
    return value


_REDACTED = "***"

#: What delivery can raise; the per-channel guard catches exactly this and
#: nothing broader. OSError covers URLError, HTTPError and timeouts;
#: ValueError is urllib refusing a malformed URL (quoting it in the message);
#: HTTPException is the server breaking the protocol mid-response; NoustError
#: is the email transport reporting a delivery problem.
_DELIVERY_ERRORS = (OSError, ValueError, HTTPException, NoustError)


def is_transient(error: BaseException) -> bool:
    """
    Say whether a delivery that failed may work if tried again later.

    A destination that is down, slow or momentarily unresolvable will come
    back; one that answered 4xx (the token is wrong, the webhook was deleted),
    that is forbidden by the SSRF guard or whose configured URL is malformed
    will not, and trying again would only repeat the refusal.

    Args:
        error: What delivery raised.

    Returns:
        True for a timeout, a connection problem, a 408, 425, 429 or 5xx
        answer, a broken HTTP exchange, an unresolvable name or an email
        transport failure; False for anything else.
    """
    if isinstance(error, HTTPError):
        return error.code in (408, 425, 429) or error.code >= 500
    if isinstance(error, _Unresolvable):
        return True
    if isinstance(error, (ValueError, EmailRefused)):
        return False
    return isinstance(error, (OSError, HTTPException, NoustError))


@dataclass
class SweepReport:
    """
    What one pass over the ledger's due deliveries did.

    Attributes:
        delivered: Deliveries that went out.
        retried: Deliveries that failed again and wait for a later attempt.
        failed: Deliveries given up on.
        digests: Digest messages sent for notifications the quiet hours held.
    """

    delivered: int = 0
    retried: int = 0
    failed: int = 0
    digests: int = 0

    def to_dict(self) -> dict[str, int]:
        """Returns: the counts, for the CLI and the API."""
        return {
            "delivered": self.delivered,
            "retried": self.retried,
            "failed": self.failed,
            "digests": self.digests,
        }


def _on_by_default(kind: str) -> bool:
    """
    Args:
        kind: An event kind.

    Returns:
        Whether it is on when no switch names it.
    """
    return kind not in OFF_BY_DEFAULT


def _recipients(value: Any) -> list[str]:
    """
    Args:
        value: An email channel's ``recipients``: a list or a comma-separated string.

    Returns:
        The addresses, without blanks.
    """
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _telegram_thread(settings: Mapping[str, Any], kind: str) -> int | None:
    """
    Pick the forum topic a Telegram message is posted in.

    Args:
        settings: The Telegram channel's settings.
        kind: The event kind being sent.

    Returns:
        The topic of that event (``threads``), else the channel's ``thread_id``,
        else None (the chat's general thread). A value that is not a topic
        number (a hand edit) is ignored: the alert matters more than its place.
    """
    threads = settings.get("threads")
    for candidate in (
        threads.get(kind) if isinstance(threads, Mapping) else None,
        settings.get("thread_id"),
    ):
        if candidate in (None, ""):
            continue
        try:
            number = int(str(candidate).strip())
        except ValueError:
            continue
        if number > 0:
            return number
    return None


def _setting_hint(instance: ChannelInstance) -> str:
    """
    Name the settings the test-button error asks for when a channel is not configured.

    Args:
        instance: The channel.

    Returns:
        ``notifications.channels.telegram.bot_token and chat_id``: the keys the
        type needs, under the instance's own name.
    """
    prefix = f"notifications.channels.{instance.name}"
    if instance.type == "email":
        return f"{prefix}.enabled and monitor.smtp.*"
    keys = [field.key for field in TYPES[instance.type].fields if field.required]
    if not keys:
        return prefix
    return f"{prefix}.{keys[0]}" + "".join(f" and {key}" for key in keys[1:])


#: Anything with urlopen's calling convention: ``opener(request, timeout=...)``
#: returning a closeable response. Tests inject one; the suite never opens a
#: real socket.
Opener = Callable[..., Any]

#: Address family alias, matching noust.core.net's.
_IPAddress = outbound.IPAddress

#: The forbidden networks, the resolver and the address judgement are
#: :mod:`noust.core.outbound`'s: the one guard every feature that sends a
#: request to a configured host shares. These names stay because the notifier
#: is where the guard was written and its tests read them here. The resolver is
#: looked up through the other module on every call, so replacing
#: ``outbound.resolve_host`` or this module's ``_resolve_host`` both work.
_FORBIDDEN_NETWORKS = outbound.FORBIDDEN_NETWORKS
_NAT64_PREFIX = outbound.NAT64_PREFIX
_embedded_ipv4 = outbound.embedded_ipv4
_is_forbidden = outbound.is_forbidden


class _Unresolvable(ValueError):
    """
    A destination whose name could not be resolved.

    A ``ValueError`` like every other reason a destination is refused, so the
    callers that already catch it keep working; a subclass so the ledger can
    tell a name server having a bad minute (try again) from a destination that
    is forbidden or malformed (never will work).
    """


def _resolve_host(host: str) -> tuple[str, ...]:
    """
    Resolve a destination host to the literal addresses it points at.

    Args:
        host: Hostname or address literal from the destination URL.

    Returns:
        Every address the name resolves to.

    Raises:
        OSError: When the name cannot be resolved.
    """
    return outbound.resolve_host(host)


def _require_public_destination(
    url: str, config: Config, purpose: str = "notification destination"
) -> None:
    """
    Refuse a destination that resolves inside the machine's own networks.

    This is the SSRF guard: see the module docstring for the attack it
    closes. It is called once for the destination a request is built for, and
    again - by :class:`_SafeRedirectHandler` - for every redirect the
    endpoint answers with, so a public host that 302s to a private one is
    refused exactly as if it had been configured that way from the start.

    Args:
        url: The destination URL, already scheme-checked.
        config: Configuration to read ``notifications.allow_private_hosts``
            from.
        purpose: What the server is contacting, for the message: a webhook's
            destination by default, or a code host or another platform's API.

    Raises:
        ValueError: When the URL carries no host, the host cannot be
            resolved, or every address it resolves to is inside a forbidden
            network and the host is not on the allowlist.
    """
    host = urlparse(url).hostname
    if not host:
        raise ValueError(f"{url!r} has no host to validate")

    allowed = {
        str(entry).lower() for entry in (config.get("notifications.allow_private_hosts") or [])
    } | set(outbound.trusted_hosts(config))
    try:
        outbound.require_public_host(host, allowed=allowed, resolver=_resolve_host)
    except outbound.DestinationRefused as exc:
        if exc.reason == "unresolved":
            # A name that does not resolve now may resolve later: the ledger retries it.
            raise _Unresolvable(f"Could not resolve {purpose} {host!r}: {exc.__cause__}") from exc
        if exc.reason == "no_address":
            raise ValueError(f"The {purpose} {host!r} did not resolve to any address") from exc
        verb = "notify" if purpose == "notification destination" else "contact"
        raise ValueError(
            f"Refusing to {verb} {host!r}: resolves to {exc.address}, inside a private or "
            "internal network. List it under notifications.allow_private_hosts to allow it."
        ) from exc


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """
    Re-applies the SSRF guard to every redirect a destination answers with.

    ``urlopen`` follows redirects transparently, so a webhook endpoint that
    starts out public and 302s to ``http://169.254.169.254/`` would meet
    :func:`_require_public_destination` exactly once, at the address that
    passed. Every hop is validated again here before urllib is allowed to
    follow it.
    """

    def __init__(self, config: Config) -> None:
        """
        Args:
            config: Configuration to read the allowlist from, forwarded to
                every redirect check.
        """
        self._config = config

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        """
        Validate a redirect target before deciding whether to follow it.

        Args:
            req: The request that received the redirect.
            fp: The response file object.
            code: The redirect's HTTP status.
            msg: The redirect's reason phrase.
            headers: The redirect's response headers.
            newurl: Where the redirect points.

        Returns:
            The next request to send, exactly as the base class builds it.

        Raises:
            ValueError: When the redirect target is not a public destination.
        """
        _require_public_destination(newurl, self._config)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class NotificationEvent:
    """
    The pre-3.1 shape of a notification: a title and a body of text.

    Kept so code that still builds one keeps working; nothing in Noust does.
    :meth:`Notifier.notify` turns it into a
    :class:`~noust.core.notifications.model.Notification` whose title is the
    ``title`` and whose excerpt is the ``body``. Build a ``Notification`` with
    :mod:`noust.core.notifications.composers` instead: it says who, what and
    how it went in a way every channel can lay out.

    Attributes:
        kind: One of :data:`EVENT_KINDS`, or :data:`TEST_KIND`.
        title: One-line summary, e.g. ``"Deploy failed: example.com"``.
        body: The detail the operator acts on. May be empty.
        domain: Domain the event is about, when it is about one.
        ts: When it happened. Timezone-aware UTC by default.
    """

    kind: str
    title: str
    body: str
    domain: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        """
        Refuse a kind nothing can filter.

        A typo'd kind would default to "send" and silently bypass the
        operator's ``notifications.events`` switches.

        Raises:
            ValueError: When ``kind`` is not a known event kind.
        """
        if self.kind not in EVENT_KINDS and self.kind != TEST_KIND:
            known = ", ".join(EVENT_KINDS)
            raise ValueError(f"Unknown notification kind {self.kind!r}; expected one of: {known}")


#: How a legacy event's kind reads as a state.
_LEGACY_STATES: dict[str, State] = {
    "deploy_started": State.PROGRESS,
    "deploy_success": State.OK,
    "restore_success": State.OK,
    "backup_success": State.OK,
    "node_recovered": State.OK,
    "backup_missing": State.WARNING,
    "destination_unreachable": State.WARNING,
    "deploy_rolled_back": State.WARNING,
    "cert_expiring": State.WARNING,
    "disk_threshold": State.WARNING,
    TEST_KIND: State.INFO,
}


def legacy_notification(event: NotificationEvent, ctx: NotificationContext) -> Notification:
    """
    Turn a pre-3.1 event into a notification every channel can lay out.

    Args:
        event: The title-and-body event.
        ctx: The context, for the language and the server's name.

    Returns:
        A notification whose title is the event's, whose excerpt is its body
        (verbatim, whole lines) and whose state follows its kind.
    """
    return Notification(
        kind=event.kind,
        code=f"legacy.{event.kind}",
        state=_LEGACY_STATES.get(event.kind, State.FAILED),
        locale=ctx.locale,
        title=event.title,
        subject="",
        summary="",
        server=ctx.server,
        facts=(server_fact(ctx),),
        excerpt=make_excerpt(event.body, label=""),
        domain=event.domain,
        ts=event.ts,
    )


@dataclass(frozen=True)
class TelegramChat:
    """
    One chat the configured Telegram bot has seen an update from.

    Finding a chat id today means calling the Bot API's ``getUpdates`` by hand
    and reading raw JSON; :meth:`Notifier.list_telegram_chats` is that lookup,
    and this is one row of its answer.

    Attributes:
        id: The chat id - what ``notifications.channels.telegram.chat_id``
            wants.
        type: Telegram's own chat type: ``private``, ``group``,
            ``supergroup`` or ``channel``.
        title: The chat's own name, for a group, supergroup or channel.
        username: The chat's, or the user's, ``@username``, when it has one.
    """

    id: int
    type: str
    title: str | None = None
    username: str | None = None


#: These three used to live here; the push channels' requests share them.
_require_http_url = require_http_url
_post = post
_json_request = json_request


def _webhook_request(url: str, notification: Notification, secret: str = "") -> Request:
    """
    Build the generic webhook POST.

    The body is the documented contract of
    :mod:`noust.core.notifications.render.webhook`; the signature, when there
    is a secret, covers the exact bytes sent.

    Args:
        url: The operator's endpoint.
        notification: The notification to deliver.
        secret: ``notifications.channels.webhook.secret``; empty for none.

    Returns:
        The request.

    Raises:
        ValueError: When the URL is not HTTP(S).
    """
    _require_http_url(url, "notifications.channels.webhook.webhook_url")
    body = webhook.encode(webhook.render(notification))
    return _post(url, body, webhook.headers(notification, body, secret))


def _slack_request(url: str, notification: Notification) -> Request:
    """
    Build the Slack incoming-webhook POST.

    Args:
        url: The Slack webhook URL.
        notification: The notification to deliver.

    Returns:
        The request.

    Raises:
        ValueError: When the URL is not HTTP(S).
    """
    _require_http_url(url, "notifications.channels.slack.webhook_url")
    return _json_request(url, slack.render(notification))


def _discord_mention(channel: Mapping[str, Any]) -> discord.Mention | None:
    """
    Read whom Discord pings from the channel's settings.

    A value that is not an id (the file was edited by hand) is ignored and
    logged, never sent: the alert matters more than the ping, and an id is
    never put into a message unless it is one.

    Args:
        channel: The ``notifications.channels.discord`` block.

    Returns:
        The mention, or None when nobody is named.
    """
    ids: dict[str, str] = {}
    for key, field_name in (("mention_role_id", "role_id"), ("mention_user_id", "user_id")):
        value = str(channel.get(key) or "").strip()
        if not value:
            continue
        try:
            validate_discord_id(value, key)
        except ValueError:
            logger.warning(
                "Ignoring notifications.channels.discord.%s: it is not a Discord id", key
            )
            continue
        ids[field_name] = value
    return discord.Mention(**ids) if ids else None


def _discord_request(
    url: str,
    notification: Notification,
    mention: discord.Mention | None = None,
    *,
    plain: bool = False,
) -> Request:
    """
    Build the Discord webhook POST.

    Args:
        url: The Discord webhook URL.
        notification: The notification to deliver.
        mention: Whom a critical notification pings; nobody by default.
        plain: Send text instead of an embed, for the retry after Discord
            refused the embed.

    Returns:
        The request.

    Raises:
        ValueError: When the URL is not HTTP(S).
    """
    _require_http_url(url, "notifications.channels.discord.webhook_url")
    render = discord.render_plain if plain else discord.render
    return _json_request(url, render(notification, mention=mention))


def _telegram_request(
    bot_token: str,
    chat_id: str,
    notification: Notification,
    *,
    plain: bool = False,
    thread_id: int | None = None,
) -> Request:
    """
    Build the Bot API ``sendMessage`` POST.

    Args:
        bot_token: The bot's token; it becomes part of the request path.
        chat_id: Destination chat, travels in the JSON body.
        notification: The notification to deliver.
        plain: Send without markup, for the retry after Telegram refused it.
        thread_id: The forum topic to post in; the chat's general one when None.

    Returns:
        The request.

    Raises:
        ValueError: When the token does not have the Bot API shape, or the
            chat id does not look like one Telegram would recognise. Neither
            is ever included in the message.
    """
    validate_telegram_bot_token(bot_token)
    chat_id = validate_telegram_chat_id(chat_id)
    url = f"{_TELEGRAM_API}/bot{bot_token}/sendMessage"
    return _json_request(
        url, telegram.render(notification, chat_id, plain=plain, thread_id=thread_id)
    )


def _describe_error(exc: BaseException, *, include_body: bool = True) -> str:
    """
    Return a failure in the server's own words.

    A system error is never paraphrased: the response body of an HTTP
    rejection is what Slack or Telegram actually said, and it is the message
    the operator can act on - in a log they already trust. ``test_channel``
    answers the settings page instead, which renders whatever string comes
    back, so it asks for ``include_body=False``: the destination's response
    is not this endpoint's data to repeat, especially once the SSRF guard's
    allowlist has let through a host that is private but not entirely
    untrusted.

    Args:
        exc: The exception delivery raised.
        include_body: Whether to read and include the response body of an
            HTTP rejection. Only :meth:`Notifier.notify`'s log line does.

    Returns:
        Human-readable failure text.
    """
    if isinstance(exc, HTTPError):
        status = f"HTTP {exc.code} {exc.reason}"
        if not include_body:
            return status
        try:
            # Bounded read: the error page of a misbehaving endpoint must not
            # be buffered wholesale into a log line.
            body = exc.read(2048).decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            body = ""
        return f"{status}: {body}" if body else status
    if isinstance(exc, URLError):
        return str(exc.reason)
    return str(exc)


_DESCRIPTION_ATTR = "_noust_telegram_description"


def _telegram_error_description(exc: HTTPError) -> str | None:
    """
    Read the Bot API's own ``description`` field out of a rejected request.

    ``test_channel`` never echoes a remote response body back to the settings
    page for any other channel - see :func:`_describe_error` - because that
    URL is whatever the operator configured, and a reachable-but-not-quite
    -blocked private destination could use the test button to read its own
    answer back through the panel. ``api.telegram.org`` is not that: it is a
    fixed, hardcoded host this module dials itself, never one the operator's
    configuration can redirect elsewhere, so its own explanation of a 400 -
    "Bad Request: chat not found" - is exactly the fact an operator needs to
    fix a wrong chat id, not attacker-influenced content.

    Args:
        exc: The HTTP error the Bot API answered with.

    Returns:
        The ``description`` field, or None when the body is not the JSON
        shape the Bot API always answers with.
    """
    # The body can only be read once; the plain-text retry and the test button
    # both ask, so the answer is kept on the error itself.
    if hasattr(exc, _DESCRIPTION_ATTR):
        return getattr(exc, _DESCRIPTION_ATTR)  # type: ignore[no-any-return]
    description: str | None = None
    try:
        body = exc.read(2048).decode("utf-8", "replace")
        data = json.loads(body)
    except (OSError, ValueError):
        data = None
    found = data.get("description") if isinstance(data, dict) else None
    if isinstance(found, str) and found:
        description = found
    setattr(exc, _DESCRIPTION_ATTR, description)
    return description


def _chat_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    """
    Find the ``chat`` object inside one ``getUpdates`` entry.

    An update carries the chat under a different key depending on what kind
    of update it is - ``message``, ``edited_message``, ``channel_post``,
    ``my_chat_member`` and others all nest one - so every value is checked
    rather than a fixed key, which would silently miss whichever update types
    the Bot API adds next.

    Args:
        update: One entry of ``getUpdates``'s ``result``.

    Returns:
        The chat object, or None when this update carries none.
    """
    for value in update.values():
        if not isinstance(value, dict):
            continue
        chat = value.get("chat")
        if isinstance(chat, dict):
            return chat
    return None


def _parse_telegram_chats(payload: Any) -> list[TelegramChat]:
    """
    Turn a ``getUpdates`` response into the chats it mentions.

    Args:
        payload: The Bot API's decoded JSON response.

    Returns:
        Every distinct chat found, in the order first seen.

    Raises:
        ValueError: When the response is not the Bot API's own success shape.
    """
    if not isinstance(payload, dict) or not payload.get("ok"):
        description = payload.get("description") if isinstance(payload, dict) else None
        raise ValueError(description or "Telegram did not report success.")

    seen: dict[int, TelegramChat] = {}
    for update in payload.get("result") or []:
        if not isinstance(update, dict):
            continue
        chat = _chat_from_update(update)
        if chat is None:
            continue
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            continue
        seen[chat_id] = TelegramChat(
            id=chat_id,
            type=str(chat.get("type") or "unknown"),
            title=chat.get("title"),
            username=chat.get("username"),
        )
    return list(seen.values())


class Notifier:
    """
    Publishes events to every configured notification channel.

    Reads ``notifications.*`` from the configuration on every call, so a
    settings change in the panel applies to the next event without a restart.
    Each *channel instance* (see :mod:`noust.core.notifications.instances`)
    decides for itself, from its own event switches, route and quiet hours,
    whether it hears an event (:func:`noust.core.notifications.routing.decide`),
    and every delivery is written to the ledger
    (:mod:`noust.core.notification_ledger`) so a failure that may pass is tried
    again by :meth:`deliver_due` and one that cannot is on record.
    """

    def __init__(
        self,
        config: Config | None = None,
        *,
        opener: Opener | None = None,
        email_notifier: EmailNotifier | None = None,
        ledger: DeliveryLedger | None = None,
        record: bool = True,
        clock: Callable[[], datetime] | None = None,
        placement: Callable[[str], Placement | None] | None = None,
    ) -> None:
        """
        Args:
            config: Configuration to read ``notifications.*`` from. Defaults
                to the global one.
            opener: Replacement for :func:`urllib.request.urlopen`. Tests
                inject one so the suite never opens a socket.
            email_notifier: Replacement email transport. Defaults to the
                monitor's :class:`EmailNotifier`, created on first use.
            ledger: Where deliveries are recorded. The store's, opened when
                first needed, when None.
            record: False to deliver without a record at all (a one-shot
                caller that has no store).
            clock: The current moment, timezone-aware, for the quiet hours.
            placement: Where an application lives (its project, environment
                and tags), for routes that ask. The store's by default.
        """
        self._config = config or Config()
        # The default opener carries its own redirect handler, so a
        # destination that answers with a 302 is re-checked by
        # _require_public_destination at every hop instead of only at the
        # address that was configured.
        self._opener: Opener = (
            opener or urllib.request.build_opener(_SafeRedirectHandler(self._config)).open
        )
        self._email_notifier = email_notifier
        self._ledger = ledger
        self._record = record
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._placement_of = placement or store_placement
        self._last_purge = 0.0

    # -- publishing -----------------------------------------------------------

    def notify(self, event: Notification | NotificationEvent) -> None:
        """
        Publish a notification to every channel that wants it.

        Never raises for a delivery problem: a dead channel is logged, put in
        the ledger to be tried again, and the remaining channels still get it,
        because the caller is a deploy or the monitor loop and its work matters
        more than the announcement of it.

        Args:
            event: What happened. A pre-3.1 :class:`NotificationEvent` is
                accepted and turned into a notification.
        """
        settings = self._settings()
        if not settings.get("enabled", False):
            return
        notification = self._structured(event)
        switches = settings.get("events") or {}
        channels: dict[str, Any] = settings.get("channels") or {}
        now = self._clock()
        placements: dict[str, Placement | None] = {}

        def placement(domain: str) -> Placement | None:
            if domain not in placements:
                placements[domain] = self._placement_of(domain)
            return placements[domain]

        for instance in resolve_instances(channels):
            if not instance.enabled or not instance.configured:
                continue
            wants = instance.spec.wants
            if wants is not None and not wants(notification):
                continue
            routing, judged = self._judged(instance, notification, switches)
            decision = decide(
                routing,
                judged,
                now=now,
                server_events=switches,
                default_on=_on_by_default,
                placement_of=placement,
            )
            if decision.action == "skip":
                logger.debug(
                    "Notification %s not sent to %s: %s",
                    notification.kind,
                    instance.name,
                    decision.reason,
                )
                continue
            if decision.action == "hold" and self._hold(instance, notification, decision):
                continue
            self._deliver(instance, notification, channels)

    @staticmethod
    def _judged(
        instance: ChannelInstance, notification: Notification, switches: Mapping[str, bool]
    ) -> tuple[Routing, Notification]:
        """
        Choose the routing and the notification a channel's decision is made on.

        For a type that closes incidents (a pager) a recovery is not judged
        like a message: it must arrive even when the channel's severity floor
        or quiet hours would hold an ``ok``, and it follows the switch of the
        problem it closes (a channel that hears ``backup_failed`` hears the
        backup that fixed it, though ``backup_success`` ships off), or an
        incident opened would stay open.

        Args:
            instance: The channel.
            notification: What happened.
            switches: ``notifications.events``.

        Returns:
            The routing to decide with, and the notification to decide on.
        """
        routing = instance.routing
        if not instance.spec.closes_incidents or notification.state is not State.OK:
            return routing, notification
        judged = notification
        for kind in pagerduty.opening_kinds(notification):
            wanted = routing.events.get(kind)
            if wanted is None:
                wanted = switches.get(kind, _on_by_default(kind))
            if wanted:
                judged = replace(notification, kind=kind)
                break
        floor = replace(routing.route, min_severity=None)
        return replace(routing, route=floor, quiet=None), judged

    def test_channel(self, name: str) -> str | None:
        """
        Send a test event through one channel, ignoring the on/off switches.

        The settings-page button exists to try a channel before the operator
        enables notifications, so neither ``notifications.enabled`` nor the
        per-kind filter applies here. A test is not recorded in the ledger:
        its answer is the answer.

        Args:
            name: A channel instance's name (``slack``, ``slack-staging``).

        Returns:
            None when the channel accepted the message, otherwise the failure
            in the server's own words, with configured secrets scrubbed.
        """
        channels: dict[str, Any] = self._settings().get("channels") or {}
        instances = {instance.name: instance for instance in resolve_instances(channels)}
        if name not in instances:
            known = ", ".join(instances)
            return f"Unknown notification channel {name!r}; expected one of: {known}"

        instance = instances[name]
        notification = compose_test(instance.type, self._context(), instance=instance.name)
        try:
            sent = self._dispatch(instance, notification)
        except _DELIVERY_ERRORS as exc:
            if instance.type == "telegram" and isinstance(exc, HTTPError):
                description = _telegram_error_description(exc)
                if description:
                    return _scrub(f"HTTP {exc.code} {exc.reason}: {description}", channels)
            # include_body=False: this string is rendered on the settings
            # page, and the endpoint's response body is not ours to repeat -
            # except for Telegram above, whose host is not the operator's to
            # redirect.
            return _scrub(_describe_error(exc, include_body=False), channels)
        if not sent:
            return f"Channel {name} is not configured; set {_setting_hint(instance)} first."
        return None

    def list_telegram_chats(self, name: str = "telegram") -> list[TelegramChat]:
        """
        List the chats a configured Telegram bot has seen.

        Finding a chat id today means opening the Bot API's ``getUpdates`` URL
        by hand and reading raw JSON. This is that lookup, using the bot token
        already saved, through the same HTTP path - and so the same SSRF
        guard and timeout - as every other channel; ``api.telegram.org`` is
        fixed and always public, so the guard is a formality here, but one
        chokepoint means it is never a decision a new caller has to remember
        to make again.

        Telegram only queues an update the bot has not already been asked
        for, so a chat will not appear here until it has sent the bot a
        message, or the bot has been added to it, since the last time this
        was called (or the token's webhook, if any, last polled it).

        Args:
            name: The Telegram channel instance whose bot is asked.

        Returns:
            Every chat found, deduplicated by chat id.

        Raises:
            ValueError: When no bot token is configured, or the Bot API's
                response could not be parsed as its own success shape.
            OSError: When the Bot API could not be reached or answered with
                an HTTP error.
        """
        channels: dict[str, Any] = self._settings().get("channels") or {}
        telegram: dict[str, Any] = channels.get(name) or {}
        bot_token = str(telegram.get("bot_token") or "")
        if not bot_token:
            raise ValueError(f"notifications.channels.{name}.bot_token must be set first.")
        validate_telegram_bot_token(bot_token)

        url = f"{_TELEGRAM_API}/bot{bot_token}/getUpdates"
        _require_public_destination(url, self._config)
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self._opener(request, timeout=NOTIFY_TIMEOUT) as response:
            body = response.read()

        payload = json.loads(body.decode("utf-8", "replace"))
        return _parse_telegram_chats(payload)

    # -- retries and the quiet hours' end ------------------------------------

    def deliver_due(self) -> SweepReport:
        """
        Try again what failed for a reason that may pass, and release what the
        quiet hours held.

        Run by the monitor on every scan and by ``noust notify retry``. A
        delivery whose channel is gone, off or no longer configured is given
        up on with that said; one that fails again waits longer
        (:data:`~noust.core.notification_ledger.RETRY_DELAYS`) until the
        attempts run out, and then it is a failed delivery on record and an
        audit event.

        Returns:
            What was done.
        """
        ledger = self._open_ledger()
        if ledger is None:
            return SweepReport()
        try:
            due = ledger.due()
        except _LEDGER_ERRORS as exc:
            logger.warning("The delivery ledger could not be read: %s", exc)
            return SweepReport()
        channels: dict[str, Any] = self._settings().get("channels") or {}
        instances = {instance.name: instance for instance in resolve_instances(channels)}

        report = SweepReport()
        held: dict[str, list[Delivery]] = {}
        for delivery in due:
            instance = instances.get(delivery.channel)
            if instance is None or not instance.enabled or not instance.configured:
                self._ledger_do(
                    ledger.failed,
                    delivery.id,
                    "The channel is no longer configured",
                    delivery.attempts,
                )
                report.failed += 1
                continue
            if delivery.state == "held":
                held.setdefault(delivery.channel, []).append(delivery)
                continue
            self._retry(ledger, instance, delivery, channels, report)
        for name, items in held.items():
            self._release(ledger, instances[name], items, channels, report)
        self._purge(ledger)
        return report

    def _retry(
        self,
        ledger: DeliveryLedger,
        instance: ChannelInstance,
        delivery: Delivery,
        channels: dict[str, Any],
        report: SweepReport,
    ) -> None:
        """
        Try a failed delivery once more.

        Args:
            ledger: The ledger the delivery is on.
            instance: The channel it goes to.
            delivery: The delivery.
            channels: The ``notifications.channels`` block, for scrubbing.
            report: Filled with the outcome.
        """
        try:
            notification = delivery.notification()
        except ValueError as exc:
            self._ledger_do(ledger.failed, delivery.id, str(exc), delivery.attempts)
            report.failed += 1
            return
        attempts = delivery.attempts + 1
        try:
            sent = self._dispatch(instance, notification)
        except _DELIVERY_ERRORS as exc:
            error = _scrub(_describe_error(exc), channels)
            wait = backoff(attempts) if is_transient(exc) else None
            logger.warning(
                "Notification channel %s failed again for event %s (attempt %d): %s",
                instance.name,
                notification.kind,
                attempts,
                error,
            )
            if wait is None:
                self._ledger_do(ledger.failed, delivery.id, error, attempts)
                self._audit_failure(instance, notification, error, attempts)
                report.failed += 1
            else:
                self._ledger_do(ledger.retry_later, delivery.id, error, attempts, wait)
                report.retried += 1
            return
        if sent:
            self._ledger_do(ledger.sent, delivery.id)
            report.delivered += 1
        else:
            self._ledger_do(ledger.failed, delivery.id, "The channel is not configured", attempts)
            report.failed += 1

    def _release(
        self,
        ledger: DeliveryLedger,
        instance: ChannelInstance,
        items: list[Delivery],
        channels: dict[str, Any],
        report: SweepReport,
    ) -> None:
        """
        Deliver what a channel's quiet hours held, now that they ended.

        More than a few are sent as one digest when the channel's quiet hours
        say so; fewer one by one. If the quiet hours are on again (the window
        was edited while these waited) they wait for the new end.

        Args:
            ledger: The ledger the deliveries are on.
            instance: The channel.
            items: Its held deliveries, oldest first.
            channels: The ``notifications.channels`` block, for scrubbing.
            report: Filled with the outcome.
        """
        quiet = instance.routing.quiet
        now = self._clock()
        if quiet is not None and quiet.active(now):
            until = quiet.ends_at(now).timestamp()
            for item in items:
                self._ledger_do(ledger.reschedule, item.id, until)
            return
        notifications: list[tuple[Delivery, Notification]] = []
        for item in items:
            try:
                notifications.append((item, item.notification()))
            except ValueError as exc:
                self._ledger_do(ledger.failed, item.id, str(exc), item.attempts)
                report.failed += 1
        digest = quiet is not None and quiet.digest and len(notifications) >= DIGEST_MINIMUM
        if digest:
            self._release_digest(ledger, instance, notifications, channels, report)
            return
        for item, _held in notifications:
            self._retry(ledger, instance, item, channels, report)

    def _release_digest(
        self,
        ledger: DeliveryLedger,
        instance: ChannelInstance,
        notifications: list[tuple[Delivery, Notification]],
        channels: dict[str, Any],
        report: SweepReport,
    ) -> None:
        """
        Send what was held as one message.

        Args:
            ledger: The ledger the deliveries are on.
            instance: The channel.
            notifications: The held deliveries and what each one says.
            channels: The ``notifications.channels`` block, for scrubbing.
            report: Filled with the outcome.
        """
        digest = compose_digest([n for _item, n in notifications], self._context())
        try:
            sent = self._dispatch(instance, digest)
        except _DELIVERY_ERRORS as exc:
            error = _scrub(_describe_error(exc), channels)
            logger.warning(
                "Notification channel %s failed for the digest of %d held events: %s",
                instance.name,
                len(notifications),
                error,
            )
            for item, _n in notifications:
                attempts = item.attempts + 1
                wait = backoff(attempts) if is_transient(exc) else None
                if wait is None:
                    self._ledger_do(ledger.failed, item.id, error, attempts)
                    report.failed += 1
                else:
                    self._ledger_do(ledger.retry_later, item.id, error, attempts, wait)
                    report.retried += 1
            return
        for item, _n in notifications:
            if sent:
                self._ledger_do(ledger.sent, item.id)
                report.delivered += 1
            else:
                self._ledger_do(ledger.failed, item.id, "The channel is not configured", 1)
                report.failed += 1
        if sent:
            report.digests += 1

    # -- the ledger -------------------------------------------------------------

    def _open_ledger(self) -> DeliveryLedger | None:
        """
        Returns:
            The ledger deliveries are recorded in, or None when this notifier
            records nothing or the store cannot be opened (then delivery goes
            on without a record).
        """
        if not self._record:
            return None
        if self._ledger is None:
            self._ledger = DeliveryLedger()
        return self._ledger

    def _ledger_do(self, action: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Run a ledger write; a ledger that cannot be written never costs the delivery.

        Args:
            action: The ledger method.
            args: Its positional arguments.
            kwargs: Its keyword arguments.

        Returns:
            Its result, or None when it failed (and was logged).
        """
        try:
            return action(*args, **kwargs)
        except _LEDGER_ERRORS as exc:
            logger.warning("The delivery ledger could not be written: %s", exc)
            return None

    def _purge(self, ledger: DeliveryLedger) -> None:
        """
        Forget old deliveries, at most once an hour.

        Args:
            ledger: The ledger.
        """
        now = time.monotonic()
        if self._last_purge and now - self._last_purge < PURGE_EVERY_SECONDS:
            return
        self._last_purge = now
        self._ledger_do(ledger.purge)

    def _hold(self, instance: ChannelInstance, notification: Notification, decision: Any) -> bool:
        """
        Keep a notification until a channel's quiet hours end.

        Args:
            instance: The channel.
            notification: What is held.
            decision: The routing decision, with the moment to release.

        Returns:
            True when it was recorded; False when there is no ledger to hold
            it in, and the caller must deliver it now (a notification is
            never lost to a record that could not be written).
        """
        ledger = self._open_ledger()
        if ledger is None or decision.release_at is None:
            return False
        held = self._ledger_do(
            ledger.hold,
            notification,
            instance.name,
            instance.type,
            decision.release_at.timestamp(),
        )
        return held is not None

    def _deliver(
        self, instance: ChannelInstance, notification: Notification, channels: dict[str, Any]
    ) -> bool:
        """
        Deliver one notification through one channel and record the outcome.

        Args:
            instance: The channel.
            notification: What to deliver.
            channels: The ``notifications.channels`` block, for scrubbing.

        Returns:
            True when a message went out.
        """
        ledger = self._open_ledger()
        try:
            sent = self._dispatch(instance, notification)
        except _DELIVERY_ERRORS as exc:
            # The request URL never reaches the log: urllib quotes it in
            # some errors, and the Telegram one embeds the bot token.
            error = _scrub(_describe_error(exc), channels)
            logger.warning(
                "Notification channel %s failed for event %s: %s",
                instance.name,
                notification.kind,
                error,
            )
            wait = backoff(1) if is_transient(exc) else None
            if ledger is not None:
                self._ledger_do(
                    ledger.record_failure,
                    notification,
                    instance.name,
                    instance.type,
                    error,
                    retry_in=wait,
                )
            if wait is None:
                self._audit_failure(instance, notification, error, 1)
            return False
        if sent and ledger is not None:
            self._ledger_do(ledger.record_sent, notification, instance.name, instance.type)
        return sent

    @staticmethod
    def _audit_failure(
        instance: ChannelInstance, notification: Notification, error: str, attempts: int
    ) -> None:
        """
        Put a delivery that will not be tried again on the audit trail.

        Args:
            instance: The channel.
            notification: What could not be delivered.
            error: Why, scrubbed of credentials.
            attempts: Tries made.
        """
        audit.record(
            "notify.delivery_failed",
            target=f"channel:{instance.name}",
            outcome="failure",
            details={
                "event": notification.kind,
                "code": notification.code,
                "subject": notification.subject,
                "attempts": attempts,
                "error": error,
            },
        )

    # -- reading the configuration -------------------------------------------

    def _settings(self) -> dict[str, Any]:
        """
        Read the ``notifications`` block, tolerating a sparse config file.

        Returns:
            The block, possibly empty.
        """
        settings = self._config.get("notifications", {})
        return settings if isinstance(settings, dict) else {}

    def _context(self) -> NotificationContext:
        """
        Who is speaking and in which language, as the configuration says.

        Returns:
            The language of ``notifications.language`` (normalised), the name
            of the server (``server.name``, the hostname when unset) and the
            base of the console links (``web.public_url``).
        """
        return NotificationContext.from_config(self._config)

    def _locale(self) -> Locale:
        """
        Language this instance's own notification texts render in.

        Returns:
            ``notifications.language``, normalised by
            :func:`noust.core.messages.normalize_locale` -
            :data:`~noust.core.messages.DEFAULT_LOCALE` for anything unset or
            not one of the two catalogued locales.
        """
        return self._context().locale

    def _structured(self, event: Notification | NotificationEvent) -> Notification:
        """
        Args:
            event: A notification, or a pre-3.1 event.

        Returns:
            The notification.
        """
        if isinstance(event, Notification):
            return event
        return legacy_notification(event, self._context())

    # -- delivery ------------------------------------------------------------

    def _dispatch(self, instance: ChannelInstance, notification: Notification) -> bool:
        """
        Deliver one notification through one channel instance.

        Args:
            instance: The channel.
            notification: The notification to deliver.

        Returns:
            True when a message went out, False when the channel is not
            configured.

        Raises:
            OSError: When the endpoint is unreachable or rejects the message.
            ValueError: When the configured URL or token is malformed, or the
                destination resolves inside a forbidden network.
            NoustError: When the email transport reports a problem.
        """
        if instance.type == "email":
            return self._send_email(notification, instance)

        request = self._request_for(instance, notification)
        if request is None:
            return False
        try:
            self._send(request)
        except HTTPError as exc:
            retry = self._plain_retry(instance, exc, notification)
            if retry is None:
                raise
            self._send(retry)
        return True

    def _send(self, request: Request) -> None:
        """
        Send one request, behind the SSRF guard.

        Args:
            request: The request to send.

        Raises:
            OSError: When the endpoint is unreachable or rejects it.
            ValueError: When the destination resolves inside a forbidden
                network.
        """
        send_guarded(request, self._config, self._opener)

    def _plain_retry(
        self,
        instance: ChannelInstance,
        error: HTTPError,
        notification: Notification,
    ) -> Request | None:
        """
        Build the one retry a chat deserves when it refuses the formatting.

        A message whose entities Telegram cannot parse is answered with 400
        ``can't parse entities``, and an embed Discord does not accept with a
        400 ``Invalid Form Body``. An alert must not be lost to a formatting
        quirk, so it is sent once more, as plain text.

        Args:
            instance: The channel that failed.
            error: What it answered.
            notification: What was being delivered.

        Returns:
            The plain-text request, or None when this failure is not that one.
        """
        settings = instance.settings
        if instance.type == "discord" and error.code == 400:
            return _discord_request(
                str(settings.get("webhook_url") or ""),
                notification,
                _discord_mention(settings),
                plain=True,
            )
        if instance.type != "telegram" or error.code != 400:
            return None
        description = _telegram_error_description(error) or ""
        if "can't parse entities" not in description.lower():
            return None
        return _telegram_request(
            str(settings.get("bot_token") or ""),
            str(settings.get("chat_id") or ""),
            notification,
            plain=True,
            thread_id=_telegram_thread(settings, notification.kind),
        )

    def _request_for(self, instance: ChannelInstance, notification: Notification) -> Request | None:
        """
        Build the request one HTTP channel would send, if it is configured.

        Args:
            instance: The channel; any type but email.
            notification: The notification to deliver.

        Returns:
            The request, or None when the channel is not configured.

        Raises:
            ValueError: When the configured URL or token is malformed.
        """
        settings = instance.settings
        kind = instance.type
        if kind == "telegram":
            bot_token = str(settings.get("bot_token") or "")
            chat_id = str(settings.get("chat_id") or "")
            if not bot_token or not chat_id:
                return None
            return _telegram_request(
                bot_token,
                chat_id,
                notification,
                thread_id=_telegram_thread(settings, notification.kind),
            )
        if kind == "matrix":
            homeserver = str(settings.get("homeserver") or "")
            room_id = str(settings.get("room_id") or "")
            access_token = str(settings.get("access_token") or "")
            if not homeserver or not room_id or not access_token:
                return None
            return matrix_request(homeserver, room_id, access_token, notification, instance.name)
        if kind == "pagerduty":
            routing_key = str(settings.get("routing_key") or "")
            if not routing_key:
                return None
            return pagerduty_request(routing_key, notification, str(settings.get("region") or ""))
        if kind == "pushover":
            user_key = str(settings.get("user_key") or "")
            app_token = str(settings.get("app_token") or "")
            if not user_key or not app_token:
                return None
            return pushover_request(
                user_key, app_token, notification, str(settings.get("device") or "")
            )

        url = str(settings.get("webhook_url") or "")
        if not url:
            return None
        token = str(settings.get("token") or "")
        if kind == "webhook":
            return _webhook_request(url, notification, str(settings.get("secret") or ""))
        if kind == "discord":
            return _discord_request(url, notification, _discord_mention(settings))
        if kind == "ntfy":
            return ntfy_request(url, notification, token)
        if kind == "gotify":
            return gotify_request(url, notification, token) if token else None
        if kind == "teams":
            return teams_request(url, notification)
        if kind == "mattermost":
            return mattermost_request(url, notification)
        if kind == "lark":
            return lark_request(url, notification, str(settings.get("secret") or ""))
        return _slack_request(url, notification)

    def _send_email(self, notification: Notification, instance: ChannelInstance) -> bool:
        """
        Deliver through the monitor's SMTP implementation.

        Args:
            notification: The notification to deliver.
            instance: The email channel; its ``recipients`` replace
                ``monitor.email_recipients`` when it names some.

        Returns:
            True when a message was handed to the server, False when the
            channel is off or SMTP is not configured.

        Raises:
            NoustError: When the transport refuses the settings or delivery
                fails.
        """
        if not instance.enabled:
            return False

        # Deferred import: the monitor publishes events to this module, so a
        # module-level import in both directions would be a cycle.
        from noust.monitor.email_notifier import EmailNotifier

        recipients = _recipients(instance.settings.get("recipients"))
        notifier = self._email_notifier
        if recipients:
            notifier = EmailNotifier(recipients=recipients)
        elif notifier is None:
            notifier = self._email_notifier = EmailNotifier()
        if not notifier.is_configured:
            return False
        # One SMTP implementation and one email layout: the transport renders
        # the notification itself, the same way for an event and for the
        # monitor's observation report.
        notifier.send_notification(notification)
        return True


def _scrub(text: str, channels: dict[str, Any]) -> str:
    """
    Replace configured secrets in error text before it is logged or shown.

    Args:
        text: Failure text that may quote a request URL.
        channels: The ``notifications.channels`` block the secrets live in.

    Returns:
        The text with every configured secret replaced by ``***``.
    """
    for secret in _channel_secrets(channels):
        text = text.replace(secret, _REDACTED)
    return text


def _channel_secrets(channels: dict[str, Any]) -> tuple[str, ...]:
    """
    Collect the values that must never appear in a log or an error message.

    Args:
        channels: The ``notifications.channels`` block.

    Returns:
        The non-empty credentials of every channel instance: the Telegram bot
        token, every webhook URL (Slack and Discord embed theirs in the path),
        the webhook's signing secret, Pushover's keys and the push services'
        tokens.
    """
    found: list[str] = []
    for instance in resolve_instances(channels):
        found.extend(instance.secrets)
    return tuple(dict.fromkeys(found))


#: The one worker every notification of this process is delivered on, first
#: in first out: a thread per event let a fast "failed" reach the channel
#: before the "Deploying" published a moment earlier. It is drained, under a
#: hard cap, when the process exits (noust.core.background).
NOTIFICATION_QUEUE = BackgroundQueue("noust-notify")


def notify_in_background(event: Notification | NotificationEvent) -> None:
    """
    Deliver an event on the notification worker and return at once.

    The configuration is read afresh from disk for each notification, so a
    settings change applies to the next event without a restart - but into
    a detached :meth:`~noust.core.config.Config.snapshot`, never by reloading
    the instance every other thread of the process is reading.

    Args:
        event: What happened.
    """
    NOTIFICATION_QUEUE.submit(lambda: notify_now(event))


def notify_composed(build: Callable[[NotificationContext], Notification]) -> None:
    """
    Compose a notification on the notification worker and deliver it.

    The way an emitter that only has facts sends one: it hands over a function
    from the context (language, server name, console URL) to the
    notification, and both the configuration read and the composition happen
    on the worker, in publication order, never on the thread that is busy
    deploying or scanning.

    Args:
        build: Builds the notification from the context; usually
            ``lambda ctx: compose_x(..., ctx)``.
    """
    NOTIFICATION_QUEUE.submit(lambda: _compose_and_notify(build))


def _compose_and_notify(build: Callable[[NotificationContext], Notification]) -> None:
    """
    Compose and deliver over the configuration as it stands on disk.

    Args:
        build: Builds the notification from the context.
    """
    config = fresh_config()
    Notifier(config).notify(build(NotificationContext.from_config(config)))


def notify_now(event: Notification | NotificationEvent) -> None:
    """
    Deliver an event now, over the configuration as it stands on disk.

    Args:
        event: What happened.
    """
    Notifier(fresh_config()).notify(event)


def fresh_config() -> Config:
    """
    Read the configuration on disk for one notification.

    Returns:
        A detached snapshot, or the shared instance as it is when the file
        cannot be read - a notification loses its freshness, not its delivery.
    """
    try:
        return Config.snapshot()
    except (OSError, NoustError) as exc:
        logger.warning("Configuration could not be re-read for a notification: %s", exc)
        return Config()


def send_guarded(request: Request, config: Config, opener: Opener | None = None) -> None:
    """
    Send one request behind the SSRF guard: the one sender of the notifier.

    The guard (see the module docstring) is checked here, once, for every HTTP
    channel and for the email providers' APIs, rather than in each request
    builder: one chokepoint a new feature cannot forget to pass through. The
    default opener checks every redirect hop again.

    Args:
        request: The request to send.
        config: The configuration, for ``notifications.allow_private_hosts``.
        opener: Replacement for the default opener; tests inject one so the
            suite never opens a socket.

    Raises:
        OSError: When the endpoint is unreachable or rejects the request.
        ValueError: When the destination resolves inside a forbidden network,
            or the answer of a request that judges its answer is a refusal.
    """
    _require_public_destination(request.full_url, config)
    send = opener or guarded_opener(config)
    # urlopen raises HTTPError for any non-2xx answer, so reaching the answer
    # means the endpoint accepted the message. Only a request that says its
    # service reports a refusal in a success answer has that answer read.
    response = send(request, timeout=NOTIFY_TIMEOUT)
    try:
        check = getattr(request, "check_reply", None)
        if check is not None:
            check(response.read(MAX_REPLY_BYTES))
    finally:
        response.close()


def guarded_opener(config: Config) -> Opener:
    """
    Build the opener every outbound request of an operator-typed address uses.

    It follows redirects like ``urlopen`` does, but asks the SSRF guard about
    every hop, so a public address that answers with a redirect to the cloud
    metadata service is refused at the redirect.

    Args:
        config: The configuration, for ``notifications.allow_private_hosts``.

    Returns:
        A callable with ``urlopen``'s calling convention.
    """
    return urllib.request.build_opener(_SafeRedirectHandler(config)).open


def require_public_destination(
    url: str, purpose: str = "server", *, unresolved_ok: bool = False
) -> None:
    """
    Refuse to contact a server that resolves inside the machine's own networks.

    The one SSRF guard (see the module docstring), for every outbound request
    whose address an operator typed: a code host's API, another platform's.
    A private host the operator trusts is listed under
    ``notifications.allow_private_hosts``, the same allowlist.

    Args:
        url: The address about to be requested.
        purpose: What it is, for the message ("GitHub Enterprise server").
        unresolved_ok: Let a name that does not resolve pass: an address saved
            while its DNS is down is judged again when it is used.

    Raises:
        ValueError: The URL has no host, cannot be resolved, or resolves to a
            private or internal address that is not allowed.
    """
    try:
        _require_public_destination(url, Config(), purpose)
    except _Unresolvable:
        if not unresolved_ok:
            raise
