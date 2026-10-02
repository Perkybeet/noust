# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console does about the server when it starts.

Two things happened while it was not running, and both are its business:

- **The machine restarted.** Whether the operator scheduled the reboot here,
  typed ``reboot`` in a terminal or the provider did it, the boot identifier is
  different from the last one the console saw, and the console says the server
  is back: a notification through the operator's channels (``server_back`` for
  a reboot asked from Noust, ``server_rebooted``, a warning, for one nobody
  asked for here), and a line in the audit log. Not a notice on the event
  stream: at start-up no console is listening yet. A restart of the console
  alone changes nothing and says nothing.
- **An update kept running.** The update was in its own systemd unit and did not
  die with the console. The job that started it is reconciled with that unit by
  the job manager (:mod:`noust.web.job_reconcile`); what is left here is a run
  no console job owns (started from a terminal), which is followed by a new job.

:func:`on_console_start` is called once from the server's startup. Nothing in it
may stop the console from starting: what fails is logged and left for the next start.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.managers.server.power import Returned
from noust.web.api.server.common import audit_event, get_server_context
from noust.web.api.server.jobs import os_follow_job
from noust.web.jobs import JobType, get_job_manager

log = logging.getLogger(__name__)

#: What starting up can fail with. Named, so a bug stays loud.
_STARTUP_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, sqlite3.Error, ValueError)


def describe_return(returned: Returned) -> str:
    """
    Say that the server is back, in a sentence.

    Args:
        returned: What the power manager found.

    Returns:
        One sentence for the notice and the audit log.
    """
    what = "restarted" if returned.action == "reboot" else "was powered back on"
    if not returned.planned:
        return f"The server {what}: it was not restarted from Noust"
    took = ""
    if returned.took_seconds is not None:
        minutes, seconds = divmod(returned.took_seconds, 60)
        took = f" ({minutes} min {seconds} s)" if minutes else f" ({seconds} s)"
    by = f", as {returned.requested_by} asked" if returned.requested_by else ""
    return f"The server {what}{by}{took}"


def _moment(text: str | None) -> datetime | None:
    """
    Read an ISO 8601 moment of the power records.

    Args:
        text: The moment, or None.

    Returns:
        It, or None when it is not one.
    """
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def notify_return(returned: Returned) -> None:
    """
    Send the operator the notification that the server is back.

    Args:
        returned: What the power manager found.
    """
    from noust.core.notifications.composers import compose_server_back, compose_server_rebooted
    from noust.core.notifier import notify_composed

    back = _moment(returned.returned_at)
    if returned.planned:
        notify_composed(
            lambda ctx: compose_server_back(
                ctx,
                requested_by=returned.requested_by,
                scheduled_for=_moment(returned.scheduled_for),
                took_s=returned.took_seconds,
                returned_at=back,
            )
        )
    else:
        notify_composed(lambda ctx: compose_server_rebooted(ctx, returned_at=back))


def announce_return() -> None:
    """Tell the operator, if the machine restarted since the console last looked."""
    returned = get_server_context().power.detect_return()
    if returned is None:
        return
    text = describe_return(returned)
    notify_return(returned)
    audit_event(
        "server.reboot",
        target="power",
        action="returned",
        planned=returned.planned,
        requested_by=returned.requested_by,
        took_seconds=returned.took_seconds,
        detail=text,
    )


def reattach_updates() -> None:
    """Find again the updates the restart interrupted, and follow those no job owns."""
    ctx = get_server_context()
    outcome = ctx.unit.reconcile()
    store = get_store()
    for record in outcome.reattach:
        if record.job_id and store.get_job(record.job_id) is not None:
            # Its own job is reconciled with the unit (noust.web.job_reconcile):
            # a second job following the same run would say it twice.
            continue
        job = get_job_manager().create_job(
            job_type=JobType.OS_UPDATE,
            name="Follow the update that kept running",
            description=f"Update {record.id} kept running while the console restarted",
            func=os_follow_job,
            kwargs={"update_id": record.id, "actor": record.actor},
            metadata={"update_id": record.id, "resumed": True},
            actor=record.actor,
        )
        log.info("Following update %s again in job %s", record.id, job.id)


def on_console_start() -> None:
    """
    Do what a starting console owes the server. Never raises.

    Each step is its own error boundary: a store that cannot be read must not
    keep an update from being followed, and neither may stop the console.
    """
    for step in (announce_return, reattach_updates):
        try:
            step()
        except _STARTUP_ERRORS as exc:
            log.warning("Server start-up step %s failed: %s", step.__name__, exc)
