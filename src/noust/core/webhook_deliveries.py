# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's deploy webhook received.

The audit log already says that a delivery arrived and what became of it, but
it is one stream for the whole console, and nothing in the console read it. The
guided webhook setup needs the answer per application - did GitHub's ping get
here, did the last push deploy, is somebody sending a wrong signature - so the
hook (:mod:`noust.web.api.hooks`, :mod:`noust.web.api.github_hooks`) writes one
row per delivery here and the API and ``noust app webhook deliveries`` read
them back.

The table is a log with a bound, not a record: the last :data:`KEEP_PER_APP`
rows of each application are kept, and a burst of identical refusals folds into
one row with a count. The endpoint is reachable by anyone who has the URL, so
what an outsider can make it write must not grow, and must not push the
operator's real deliveries out of view.

Nothing here can fail a delivery: a store that cannot write is logged and the
delivery is answered as if it had been recorded.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from noust.core.store import NoustStore, StoreError, get_store

logger = logging.getLogger(__name__)

#: A push to the tracked branch (or to any branch, when none is pinned) queued
#: the update. ``job_id`` names it.
DEPLOY_STARTED = "deploy_started"
#: A pull request queued a preview.
PREVIEW_STARTED = "preview_started"
#: The forge's test delivery, sent when the webhook is created.
PING = "ping"
#: A push to a branch the application does not track.
IGNORED_BRANCH = "ignored_branch"
#: A release or a tag push an application that follows tags did not deploy: a
#: draft, a pre-release, a tag its pattern does not match, one older than what
#: is deployed, or one already being deployed. ``detail`` says which.
IGNORED_TAG = "ignored_tag"
#: An event that is neither a push, a ping nor a pull request.
IGNORED_EVENT = "ignored_event"
#: A pull request event previews did not act on.
IGNORED_PULL_REQUEST = "ignored_pull_request"
#: A delivery id seen a moment ago: the forge's own retry.
DUPLICATE = "duplicate"
#: A delivery whose signature did not verify: a wrong secret at the forge, or a
#: stranger guessing.
BAD_SIGNATURE = "bad_signature"
#: A delivery refused because of too many wrong signatures.
LOCKED = "locked"

#: Every outcome there is.
OUTCOMES = (
    DEPLOY_STARTED,
    PREVIEW_STARTED,
    PING,
    IGNORED_BRANCH,
    IGNORED_TAG,
    IGNORED_EVENT,
    IGNORED_PULL_REQUEST,
    DUPLICATE,
    BAD_SIGNATURE,
    LOCKED,
)

#: Outcomes of a delivery that did not verify: not from the forge, or the forge
#: is configured with the wrong secret.
REFUSALS = frozenset({BAD_SIGNATURE, LOCKED})

#: Outcomes that folded into the previous row when it is the same outcome and
#: recent: what an outsider can repeat as often as they like.
FOLDED = frozenset({BAD_SIGNATURE, LOCKED, DUPLICATE})

#: How many rows of each application are kept.
KEEP_PER_APP = 100

#: Seconds within which a repeated :data:`FOLDED` outcome adds to the previous
#: row's ``count`` instead of adding a row.
COALESCE_SECONDS = 60

#: Longest ``detail`` kept, in characters.
MAX_DETAIL = 200

#: Longest ``event``, ``provider``, ``branch``, ``job_id`` and ``delivery_id``
#: kept: they come out of the forge's headers and payload, and a delivery that
#: verified is still not a reason to store whatever it says at any length.
MAX_FIELD = 100


@dataclass(frozen=True)
class WebhookDelivery:
    """
    One delivery an application's webhook received.

    Attributes:
        id: Row id; larger is newer.
        app_id: The application.
        received_at: When it arrived (or, for a folded row, when the last of
            the burst did), ISO 8601 without an offset, like every timestamp
            the store writes.
        provider: ``github``, ``gitea``, ``gitlab`` or ``github-app``; None
            for a delivery that never verified as any of them.
        event: The forge's own name for the event (``push``, ``ping``...).
        outcome: One of :data:`OUTCOMES`.
        branch: The branch a push named.
        detail: One short line of context. Never a secret or a signature.
        job_id: The job a push or pull request queued.
        delivery_id: The forge's id for the delivery.
        count: How many identical refusals the row stands for.
    """

    id: int
    app_id: int
    received_at: str
    provider: str | None
    event: str | None
    outcome: str
    branch: str | None
    detail: str | None
    job_id: str | None
    delivery_id: str | None
    count: int = 1


@dataclass(frozen=True)
class DeliverySummary:
    """
    What an application's recent deliveries add up to.

    Attributes:
        total: Rows kept (a folded row is one).
        last: The newest delivery.
        last_verified: The newest one that verified, whatever became of it.
        last_push: The newest one that queued an update.
        refused_since_last_verified: Refusals (wrong signatures, lockouts)
            since the last delivery that verified, counted with their folds.
    """

    total: int
    last: WebhookDelivery | None
    last_verified: WebhookDelivery | None
    last_push: WebhookDelivery | None
    refused_since_last_verified: int


def _row(row: sqlite3.Row) -> WebhookDelivery:
    """
    Build a delivery from a stored row.

    Args:
        row: A ``webhook_deliveries`` row.

    Returns:
        The delivery.
    """
    return WebhookDelivery(
        id=row["id"],
        app_id=row["app_id"],
        received_at=row["received_at"],
        provider=row["provider"],
        event=row["event"],
        outcome=row["outcome"],
        branch=row["branch"],
        detail=row["detail"],
        job_id=row["job_id"],
        delivery_id=row["delivery_id"],
        count=row["count"],
    )


def _one_line(text: str | None, limit: int = MAX_DETAIL) -> str | None:
    """
    Reduce a free-text field to one short line.

    Args:
        text: What the caller wants to say.
        limit: Longest result, in characters.

    Returns:
        The text with line breaks made spaces, cut to ``limit``; None for none.
    """
    if text is None:
        return None
    return " ".join(text.split())[:limit]


def record_delivery(
    app_id: int,
    outcome: str,
    *,
    provider: str | None = None,
    event: str | None = None,
    branch: str | None = None,
    detail: str | None = None,
    job_id: str | None = None,
    delivery_id: str | None = None,
    store: NoustStore | None = None,
) -> WebhookDelivery | None:
    """
    Record one delivery, folding a repeated refusal into the previous row.

    Args:
        app_id: The application whose webhook received it.
        outcome: One of :data:`OUTCOMES`.
        provider: The forge that sent it, when a credential verified.
        event: The forge's name for the event.
        branch: The branch a push named.
        detail: A short line of context; never a secret or a signature.
        job_id: The job it queued.
        delivery_id: The forge's id for the delivery.
        store: The store to write to; the process-wide one by default.

    Returns:
        The row, or None when the store could not write it: a failing log must
        not fail the delivery it describes.

    Raises:
        ValueError: When ``outcome`` is not one of :data:`OUTCOMES`.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown webhook delivery outcome: {outcome!r}")
    target = store or get_store()
    now = datetime.now()
    line = _one_line(detail)
    try:
        with target._transaction() as cursor:
            if outcome in FOLDED:
                cursor.execute(
                    "SELECT * FROM webhook_deliveries WHERE app_id = ? ORDER BY id DESC LIMIT 1",
                    (app_id,),
                )
                last = cursor.fetchone()
                if (
                    last is not None
                    and last["outcome"] == outcome
                    and (now - datetime.fromisoformat(last["received_at"])).total_seconds()
                    <= COALESCE_SECONDS
                ):
                    cursor.execute(
                        "UPDATE webhook_deliveries SET count = count + 1, received_at = ? "
                        "WHERE id = ?",
                        (now.isoformat(), last["id"]),
                    )
                    cursor.execute("SELECT * FROM webhook_deliveries WHERE id = ?", (last["id"],))
                    return _row(cursor.fetchone())
            cursor.execute(
                "INSERT INTO webhook_deliveries (app_id, received_at, provider, event, outcome, "
                "branch, detail, job_id, delivery_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    app_id,
                    now.isoformat(),
                    _one_line(provider, MAX_FIELD),
                    _one_line(event, MAX_FIELD),
                    outcome,
                    _one_line(branch, MAX_FIELD),
                    line,
                    _one_line(job_id, MAX_FIELD),
                    _one_line(delivery_id, MAX_FIELD),
                ),
            )
            inserted = cursor.lastrowid
            cursor.execute(
                "DELETE FROM webhook_deliveries WHERE app_id = ? AND id NOT IN "
                "(SELECT id FROM webhook_deliveries WHERE app_id = ? ORDER BY id DESC LIMIT ?)",
                (app_id, app_id, KEEP_PER_APP),
            )
            cursor.execute("SELECT * FROM webhook_deliveries WHERE id = ?", (inserted,))
            return _row(cursor.fetchone())
    except (sqlite3.Error, StoreError) as exc:
        logger.warning("Could not record a webhook delivery for application %s: %s", app_id, exc)
        return None


def list_deliveries(
    app_id: int, limit: int = 50, store: NoustStore | None = None
) -> list[WebhookDelivery]:
    """
    List an application's recent deliveries, newest first.

    Args:
        app_id: The application.
        limit: Most rows to return.
        store: The store to read; the process-wide one by default.

    Returns:
        The deliveries.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT * FROM webhook_deliveries WHERE app_id = ? ORDER BY id DESC LIMIT ?",
            (app_id, limit),
        )
        return [_row(row) for row in cursor.fetchall()]


def summarize(app_id: int, store: NoustStore | None = None) -> DeliverySummary:
    """
    Sum up an application's recent deliveries.

    Args:
        app_id: The application.
        store: The store to read; the process-wide one by default.

    Returns:
        The summary; empty for an application no forge has ever reached.
    """
    rows = list_deliveries(app_id, limit=KEEP_PER_APP, store=store)
    verified = next((row for row in rows if row.outcome not in REFUSALS), None)
    push = next((row for row in rows if row.outcome == DEPLOY_STARTED), None)
    refused = sum(
        row.count
        for row in rows
        if row.outcome in REFUSALS and (verified is None or row.id > verified.id)
    )
    return DeliverySummary(
        total=len(rows),
        last=rows[0] if rows else None,
        last_verified=verified,
        last_push=push,
        refused_since_last_verified=refused,
    )
