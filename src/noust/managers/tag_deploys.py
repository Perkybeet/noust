# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploying every application that carries a tag.

``noust app tag deploy prod`` finds the applications the caller sees that carry
the tag and updates each the way a single update is made
(:func:`noust.deployers.lifecycle.update_app`), one after the other in this
process: the command line has no console to queue jobs in. The console and the
API queue the same group as jobs through ``POST /api/jobs/update-many``
(:func:`noust.web.api.jobs.update_group`), which ``POST /api/tags/{tag}/deploy``
calls with the tag. Nothing here builds anything.

An application with nothing new on its branch is skipped, as the single update
refuses it, unless ``force`` says to rebuild it anyway. A preview carries no
tags of its own and is never deployed from here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from noust.core.app_tags import AppTags, validate_tag
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.store import App
from noust.core.teams import scoped_store

#: What came of one application.
QUEUED = "queued"
UPDATED = "updated"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class TagOutcome:
    """
    What came of one application of a tag.

    Attributes:
        domain: The application.
        outcome: ``queued`` (a job will update it), ``updated``, ``skipped`` or ``failed``.
        detail: Why, for a skip or a failure.
        job_id: The job, when one was queued.
    """

    domain: str
    outcome: str
    detail: str = ""
    job_id: str | None = None


def applications_with(tag: str) -> list[App]:
    """
    Find the applications that carry a tag, among those the caller sees.

    Args:
        tag: The tag.

    Returns:
        Them, by domain. Previews are not among them.
    """
    wanted = validate_tag(tag)
    tagged = {domain for domain, tags in AppTags().by_application().items() if wanted in tags}
    return sorted(
        (
            app
            for app in scoped_store().list_apps()
            if app.domain in tagged and not app.preview_parent
        ),
        key=lambda app: app.domain,
    )


def _nothing_new(domain: str, logger: Logger | None) -> str | None:
    """
    Ask whether the branch has anything the live build does not.

    Args:
        domain: The application.
        logger: Where a failure to ask is reported.

    Returns:
        The sentence saying there is nothing new, or None (there is, or it
        could not be told).
    """
    from noust.deployers.lifecycle import check_upstream

    state = check_upstream(domain, logger=logger)
    if state is not None and not state.has_new_commits:
        return state.summary
    return None


def deploy_tag_now(
    tag: str,
    *,
    logger: Logger,
    force: bool = False,
    stop_on_failure: bool = False,
    update: Callable[[str], object] | None = None,
) -> list[TagOutcome]:
    """
    Update every application that carries a tag, one after the other, in this process.

    What ``noust app tag deploy`` runs: the CLI has no console to queue jobs in.

    Args:
        tag: The tag.
        logger: Where progress is reported.
        force: Rebuild an application even when its branch has nothing new.
        stop_on_failure: Leave the rest alone after the first failure.
        update: Updates one application; :func:`noust.deployers.lifecycle.update_app`
            by default. A test passes its own.

    Returns:
        One outcome per application; the ones not reached after a failure are skipped.
    """
    from noust.deployers.lifecycle import update_app

    run = update or (lambda domain: update_app(domain, logger=logger))
    outcomes: list[TagOutcome] = []
    stopped = False
    for app in applications_with(tag):
        if stopped:
            outcomes.append(TagOutcome(app.domain, SKIPPED, "an earlier application failed"))
            continue
        if not force:
            reason = _nothing_new(app.domain, logger)
            if reason is not None:
                outcomes.append(TagOutcome(app.domain, SKIPPED, reason))
                continue
        logger.info(f"Updating {app.domain}")
        try:
            run(app.domain)
        except NoustError as exc:
            outcomes.append(TagOutcome(app.domain, FAILED, exc.message))
            logger.error(f"{app.domain}: {exc.message}")
            stopped = stop_on_failure
            continue
        outcomes.append(TagOutcome(app.domain, UPDATED))
    return outcomes
