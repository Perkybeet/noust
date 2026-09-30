# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turns a deployment's own announcements into a notification, in every process.

:mod:`noust.deployers.deploy_events` is the one place every deployment passes
through - the CLI, the console's jobs and the webhook alike - and publishes a
:class:`~noust.deployers.deploy_events.DeployEvent` for each of its four
moments. This module is one of its default subscribers
(``DEFAULT_SUBSCRIBERS``): imported lazily, by name, the first time a
deployment publishes anything, so it can import the rest of Noust - config,
the notifier, the store - without deploy_events.py importing any of it back.

Deploy notifications used to come from :mod:`noust.web.server`, built out of a
finished console job. That meant a deploy started from the CLI, and the very
start of any deploy, never notified anyone; ``web.server`` no longer builds
them for a deploy or an update, to avoid saying the same thing twice - see
its ``DEPLOY_JOB_TYPES``. A backup restore is not a deployment and still
comes from there, alongside a failed backup.

Delivery never touches the deploying thread beyond queueing: every
notification of the process goes on the notifier's one worker
(:data:`~noust.core.notifier.NOTIFICATION_QUEUE`), first in first out, so a
deployment's "failed" never reaches a channel before its "Deploying", and a
deployment must not wait on Slack the way it must not wait on npm. A CLI
process can exit within milliseconds of the deployment finishing, so that
worker is drained at process exit under a hard cap
(:data:`noust.core.background.DRAIN_TIMEOUT`): enough for one slow channel's
own timeout to be felt, not enough to hang a `noust` command that has already
told the operator what happened.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Final

from noust.core.exceptions import NoustError
from noust.core.notifications.composers import PreviewOf, compose_deploy
from noust.core.notifications.context import NotificationContext
from noust.core.notifier import NOTIFICATION_QUEUE, Notifier, fresh_config
from noust.core.store import get_store
from noust.deployers.deploy_events import DeployEvent, DeployEventKind

logger = logging.getLogger(__name__)

#: The moments of a deployment's life this module announces. Every one is
#: composed by :func:`~noust.core.notifications.composers.compose_deploy` into
#: a notification whose kind is a member of ``notifier.EVENT_KINDS`` and a key
#: of ``DEFAULT_CONFIG["notifications"]["events"]`` - pinned by a test in
#: tests/test_deploy_notifications.py, the same pattern notifier.py itself
#: uses to keep EVENT_KINDS and the config defaults from drifting apart.
_MOMENTS: Final[frozenset[DeployEventKind]] = frozenset(DeployEventKind)

#: Errors a best-effort store lookup may raise; anything else is a bug and
#: must be seen. Matches noust.deployers.recorder's own tuple for the same
#: reason: sqlite3.Error and OSError are how a locked or unreadable database
#: surfaces here.
_STORE_LOOKUP_ERRORS = (NoustError, OSError, sqlite3.Error)


def on_deploy_event(event: DeployEvent) -> None:
    """
    Announce one deployment moment to every configured channel.

    Registered as a default subscriber of
    :mod:`noust.deployers.deploy_events`; called once per event, in the
    deploying thread. Building the notification and sending it both happen
    on the notification worker, in publication order, so this returns
    immediately.

    Args:
        event: What happened.
    """
    if event.kind not in _MOMENTS:
        # DeployEventKind is a closed enum; reaching this means a member was
        # added there with no corresponding notification here.
        logger.warning("No notification for deploy event %r; not sent", event.kind)
        return
    NOTIFICATION_QUEUE.submit(lambda: _send(event))


def _preview_of(domain: str) -> PreviewOf | None:
    """
    Describe a preview application in terms of the pull request it answers.

    Best-effort: a store that cannot be read costs the notification this one
    fact, never the notification itself.

    Args:
        domain: The application's domain, as the deploy event carries it.

    Returns:
        The parent application and the pull request number when it is a
        preview (the number is None when it is not known), or None when the
        application is not a preview, or none of this could be determined.
    """
    try:
        store = get_store()
        app = store.get_app(domain)
    except _STORE_LOOKUP_ERRORS as exc:
        logger.debug("Could not look up %s for a deploy notification: %s", domain, exc)
        return None
    if app is None or not app.preview_parent:
        return None

    number: int | None = None
    try:
        preview = store.get_preview_by_domain(domain)
    except _STORE_LOOKUP_ERRORS as exc:
        logger.debug("Could not look up the preview number for %s: %s", domain, exc)
        preview = None
    if preview is not None:
        number = preview.number
    return PreviewOf(app.preview_parent, number)


def _send(event: DeployEvent) -> None:
    """
    Compose the notification and hand it to the notifier.

    Runs on the notification worker. The configuration is read afresh here,
    once per notification, so a settings change saved in the panel or made
    with the CLI applies to the next deploy without a restart - into a
    detached copy, never by reloading the instance other threads are reading.

    Args:
        event: What happened.
    """
    config = fresh_config()
    notification = compose_deploy(
        event, NotificationContext.from_config(config), preview=_preview_of(event.domain)
    )
    Notifier(config).notify(notification)
