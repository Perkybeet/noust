# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a PagerDuty Events API v2 event (checklist K-29).

A chat message is something to read; a page is something to answer for, and
what pages have that messages do not is *state*: the incident opens when the
thing breaks, a person acknowledges it, it escalates to the next on call when
nobody does, and it resolves when the thing is fine again. PagerDuty keeps that
state and the escalation policy; Noust's part is to tell it, with one stable
``dedup_key`` per thing, when it breaks (``trigger``), when somebody
acknowledges it from Noust (``acknowledge``) and when it recovers
(``resolve``).

Which notification is which is decided here, by its kind and its state
(:func:`plan`):

- a kind that has a way back (an application unreachable and recovered, a disk
  over its limit and under it, a node, a unit, an alert rule, a deploy that
  failed and the next that worked) shares one key between its two sides, a
  problem state *triggers* and an ``ok`` state *resolves*;
- an alert somebody took in Noust (``alert_acknowledged``) *acknowledges* the
  incident its firing opened, which stops PagerDuty escalating it;
- any other problem (a certificate about to expire, patches waiting) triggers
  under a key of its own and stays open until a person closes it;
- what is not a problem and closes nothing (a deploy started, a heartbeat, a
  report) is not an event for a pager and is not sent.

The routing key is part of the body, as PagerDuty defines it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Any

from noust.core.notifications.grouping import FAMILY, thing_key
from noust.core.notifications.model import (
    DIGEST_KIND,
    REPORT_KIND,
    TEST_KIND,
    Notification,
    State,
)
from noust.core.notifications.render.common import clip, http_url

SUMMARY_LIMIT = 1024
DETAIL_LIMIT = 500

#: The two regions' endpoints.
ENDPOINTS: dict[str, str] = {
    "us": "https://events.pagerduty.com/v2/enqueue",
    "eu": "https://events.eu.pagerduty.com/v2/enqueue",
}

#: What is never a page: the monitor's report and the digest of what quiet hours
#: held are summaries, and an approval is a person asking another for a decision.
NEVER_A_PAGE: frozenset[str] = frozenset(
    {REPORT_KIND, DIGEST_KIND, "approval_requested", "approval_decided"}
)

#: A page's incident is the thing the notification is about: the key lives with the grouping
#: (:func:`~noust.core.notifications.grouping.thing_key`), and the pager calls it its dedup key.
dedup_key = thing_key

#: The one kind that says somebody took an incident, from Noust's side.
ACKNOWLEDGED_KIND = "alert_acknowledged"

#: PagerDuty's severities, by the state of the notification.
SEVERITY = {State.FAILED: "critical", State.WARNING: "warning"}


@dataclass(frozen=True)
class Plan:
    """
    What a notification means to a pager.

    Attributes:
        action: ``trigger``, ``acknowledge`` or ``resolve``.
        dedup_key: The incident it is about.
    """

    action: str
    dedup_key: str


def plan(n: Notification) -> Plan | None:
    """
    Decide what a notification is to a pager.

    Args:
        n: The notification.

    Returns:
        Its action and incident, or None when it is not an event for a pager
        (nothing is wrong, and it closes nothing).
    """
    if n.kind == TEST_KIND:
        return Plan("trigger", thing_key(n))
    if n.kind in NEVER_A_PAGE:
        return None
    if n.kind == ACKNOWLEDGED_KIND:
        return Plan("acknowledge", thing_key(n))
    if n.state in SEVERITY:
        return Plan("trigger", thing_key(n))
    if n.state is State.OK and n.kind in FAMILY:
        return Plan("resolve", thing_key(n))
    return None


def render(n: Notification, routing_key: str, decided: Plan) -> dict[str, Any]:
    """
    Render a notification as an event.

    Args:
        n: The notification.
        routing_key: The integration's key.
        decided: :func:`plan` of the notification.

    Returns:
        The JSON body of ``POST /v2/enqueue``.
    """
    body: dict[str, Any] = {
        "routing_key": routing_key,
        "event_action": decided.action,
        "dedup_key": decided.dedup_key,
    }
    if decided.action in ("resolve", "acknowledge"):
        return body
    details: dict[str, Any] = {fact.key: clip(fact.value, DETAIL_LIMIT) for fact in n.facts}
    if n.command and n.command.value:
        details["command"] = clip(n.command.value, DETAIL_LIMIT)
    if n.excerpt is not None and n.excerpt.lines:
        details["excerpt"] = "\n".join(n.excerpt.lines)[-2000:]
    payload: dict[str, Any] = {
        "summary": clip(f"{n.headline}: {n.summary}" if n.summary else n.headline, SUMMARY_LIMIT),
        "source": clip(n.server or "noust", 255),
        "severity": SEVERITY.get(n.state, "info"),
        "timestamp": n.ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "group": n.kind,
        "class": n.code,
        "custom_details": details,
    }
    if n.domain:
        payload["component"] = clip(n.domain, 255)
    body["payload"] = payload
    body["client"] = "Noust"
    link = n.console_link
    if link is not None and http_url(link.url):
        body["client_url"] = link.url
        body["links"] = [{"href": link.url, "text": clip(link.label, 100)}]
    return body


def acknowledge(routing_key: str, key: str) -> dict[str, Any]:
    """
    Args:
        routing_key: The integration's key.
        key: The incident's ``dedup_key``.

    Returns:
        The event that says somebody has taken it, which stops PagerDuty
        escalating it.
    """
    return {"routing_key": routing_key, "event_action": "acknowledge", "dedup_key": key}
