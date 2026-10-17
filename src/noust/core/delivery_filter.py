# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which pushes and pull requests an application declines, and why.

The one place the application's rules (its skip markers and its watch paths,
:mod:`noust.core.app_source_store`) meet what a delivery says
(:class:`~noust.core.forge_events.PushChange`), so the per-application webhook,
the GitHub App's endpoint and the previews decline for the same reasons and
the delivery log words them the same way (rule 3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from noust.core import app_source_store, webhook_deliveries
from noust.core.forge_events import PullRequestEvent, PushChange
from noust.core.source_rules import (
    pull_request_skip_reason,
    push_skip_reason,
    watch_paths_trigger,
)
from noust.core.store import App


@dataclass(frozen=True)
class Declined:
    """
    A delivery an application does not act on.

    Attributes:
        outcome: ``ignored_skip`` or ``ignored_paths``
            (:mod:`noust.core.webhook_deliveries`).
        reason: One sentence for the delivery log and the response.
    """

    outcome: str
    reason: str

    @property
    def short(self) -> str:
        """The word the hook's JSON answer uses: ``skip`` or ``paths``."""
        return "skip" if self.outcome == webhook_deliveries.IGNORED_SKIP else "paths"


def why_push_is_declined(app: App, change: PushChange) -> Declined | None:
    """
    Decide whether an application declines a push.

    Skip markers are looked at first: a push the author marked is not a push to
    weigh against paths.

    Args:
        app: The application the push would update.
        change: What the push's commits say and touch.

    Returns:
        Why it is declined, or None when it deploys.
    """
    options = app_source_store.get_options(app.domain)
    if options.skip_markers:
        reason = push_skip_reason(change.messages, complete=change.messages_complete)
        if reason is not None:
            return Declined(webhook_deliveries.IGNORED_SKIP, f"not deployed: {reason}")
    if options.watch_paths and not watch_paths_trigger(options.watch_paths, change.files):
        shown = ", ".join(options.watch_paths[:3]) + ("..." if len(options.watch_paths) > 3 else "")
        return Declined(
            webhook_deliveries.IGNORED_PATHS,
            f"not deployed: no changed file is under the watch paths ({shown})",
        )
    return None


def why_pull_request_is_declined(
    app: App, event: PullRequestEvent, *, files: Sequence[str] | None = None
) -> Declined | None:
    """
    Decide whether an application declines to build a preview of a pull request.

    The title and the head commit are weighed against the skip markers. A pull request's
    file list is not in its payload, so the watch paths are weighed only when the caller
    read it from the code host (``files``): a preview of an application that deploys one
    directory of a monorepo is built for the pull requests that change that directory, and
    for any when the list could not be read.

    Args:
        app: The previewed application.
        event: An opened or updated pull request.
        files: The paths the whole pull request changes; None when they are not known.

    Returns:
        Why it is declined, or None when it is built.
    """
    options = app_source_store.get_options(app.domain)
    if options.skip_markers:
        reason = pull_request_skip_reason(event.title, event.head_message)
        if reason is not None:
            return Declined(webhook_deliveries.IGNORED_SKIP, f"no preview: {reason}")
    if (
        files is not None
        and options.watch_paths
        and not watch_paths_trigger(options.watch_paths, list(files))
    ):
        shown = ", ".join(options.watch_paths[:3]) + ("..." if len(options.watch_paths) > 3 else "")
        return Declined(
            webhook_deliveries.IGNORED_PATHS,
            f"no preview: the pull request changes no file under the watch paths ({shown})",
        )
    return None


__all__ = ["Declined", "why_pull_request_is_declined", "why_push_is_declined"]
