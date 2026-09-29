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

from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.messages import DEFAULT_LOCALE, Locale, message, normalize_locale
from noust.core.notifier import NOTIFICATION_QUEUE, NotificationEvent, Notifier, fresh_config
from noust.core.store import get_store
from noust.deployers.deploy_events import DeployEvent, DeployEventKind

logger = logging.getLogger(__name__)

#: What each moment in a deployment's life is announced as. Every value here
#: is also a member of notifier.EVENT_KINDS and a key of
#: DEFAULT_CONFIG["notifications"]["events"] - pinned by a test in
#: tests/test_deploy_notifications.py, the same pattern notifier.py itself
#: uses to keep EVENT_KINDS and the config defaults from drifting apart.
_KIND_MAP: Final[dict[DeployEventKind, str]] = {
    DeployEventKind.STARTED: "deploy_started",
    DeployEventKind.SUCCEEDED: "deploy_success",
    DeployEventKind.FAILED: "deploy_failed",
    DeployEventKind.ROLLED_BACK: "deploy_rolled_back",
}

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
    kind = _KIND_MAP.get(event.kind)
    if kind is None:
        # DeployEventKind is a closed enum; reaching this means a member was
        # added there with no corresponding notification kind here.
        logger.warning("No notification kind for deploy event %r; not sent", event.kind)
        return
    NOTIFICATION_QUEUE.submit(lambda: _send(event, kind))


def _title(event: DeployEvent, locale: Locale = DEFAULT_LOCALE) -> str:
    """
    Build the notification's one-line headline.

    Args:
        event: What happened.
        locale: Language to render Noust's own words in; the commit and
            branch that follow "deployed" are technical identifiers, not
            translated. Defaults to English so every existing call site -
            and its tests - is unaffected by 2.3's ``notifications.language``.

    Returns:
        E.g. ``"Deploying shop.example.com"``,
        ``"shop.example.com deployed abc1234 (main)"``,
        ``"shop.example.com failed to deploy"`` or
        ``"shop.example.com rolled back"``.
    """
    if event.kind is DeployEventKind.STARTED:
        return message("deploy_started_title", locale, domain=event.domain)
    if event.kind is DeployEventKind.SUCCEEDED:
        detail = f" {event.commit}" if event.commit else ""
        detail += f" ({event.branch})" if event.branch else ""
        return message("deploy_succeeded_title", locale, domain=event.domain, detail=detail)
    if event.kind is DeployEventKind.FAILED:
        return message("deploy_failed_title", locale, domain=event.domain)
    return message("deploy_rolled_back_title", locale, domain=event.domain)


def _preview_context(domain: str, locale: Locale) -> str | None:
    """
    Describe a preview application in terms of the pull request it answers.

    Best-effort: a store that cannot be read costs the notification this one
    line, never the notification itself.

    Args:
        domain: The application's domain, as the deploy event carries it.
        locale: Language to render Noust's own words in.

    Returns:
        ``"Preview of <parent> #<number>."`` when the pull request number is
        known, ``"Preview of <parent>."`` when it is not, or None when the
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

    if number is not None:
        return message(
            "deploy_preview_with_number", locale, parent=app.preview_parent, number=number
        )
    return message("deploy_preview", locale, parent=app.preview_parent)


def _console_link(event: DeployEvent, config: Config) -> str | None:
    """
    Build a link to this deployment's page in the console, when one exists.

    Args:
        event: What happened.
        config: Configuration to read ``web.public_url`` from.

    Returns:
        The link, or None when no public URL is configured, or the event
        carries no deployment id (a rehearsal, or a row that could not be
        recorded).
    """
    public_url = str(config.get("web.public_url", "") or "")
    if not public_url or event.deployment_id is None:
        return None
    return f"{public_url}/apps/{event.domain}/deployments/{event.deployment_id}"


def _body(event: DeployEvent, config: Config) -> str:
    """
    Build the notification's detail.

    Args:
        event: What happened.
        config: Configuration to read the console's public URL from, and,
            since 2.3, ``notifications.language`` to render Noust's own
            sentences in.

    Returns:
        The trigger and commit, the health gate's evidence verbatim for a
        failure or a rollback, and a console link when one is configured -
        each on its own paragraph, so every channel's rendering keeps them
        apart.
    """
    locale = normalize_locale(config.get("notifications.language"))
    parts: list[str] = []

    preview = _preview_context(event.domain, locale)
    if preview:
        parts.append(preview)

    facts: list[str] = []
    if event.trigger:
        facts.append(message("deploy_trigger", locale, trigger=event.trigger))
    if event.commit:
        commit = event.commit + (f" ({event.branch})" if event.branch else "")
        facts.append(message("deploy_commit", locale, commit=commit))
    if facts:
        parts.append("\n".join(facts))

    if event.error:
        # The health gate's own evidence, already scrubbed of secrets by the
        # deployment recorder - never paraphrased, the same rule a system
        # error follows everywhere else in Noust.
        parts.append(event.error)

    link = _console_link(event, config)
    if link:
        parts.append(link)

    return "\n\n".join(parts)


def _send(event: DeployEvent, kind: str) -> None:
    """
    Build the notification and hand it to the notifier.

    Runs on the notification worker. The configuration is read afresh here,
    once per notification, so a settings change saved in the panel or made
    with the CLI applies to the next deploy without a restart - into a
    detached copy, never by reloading the instance other threads are reading.

    Args:
        event: What happened.
        kind: The notification kind :func:`on_deploy_event` mapped it to.
    """
    config = fresh_config()
    locale = normalize_locale(config.get("notifications.language"))
    notification = NotificationEvent(
        kind=kind, title=_title(event, locale), body=_body(event, config), domain=event.domain
    )
    Notifier(config).notify(notification)
