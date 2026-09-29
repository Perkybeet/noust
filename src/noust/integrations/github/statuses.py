# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GitHub deployments and deployment statuses for Noust's deployments.

A default subscriber of :mod:`noust.deployers.deploy_events`: when an
application whose source is a github.com repository covered by this server's
App starts deploying, a GitHub deployment is created for the commit (or
branch) and marked in progress; when it ends, the same deployment is marked
success, or failure (with "rolled back" when what served before was put
back).

Nothing here may slow or fail a deployment. The subscriber only reads the
store and hands the event to one worker (:mod:`noust.core.background`), which
talks to GitHub in order - a deployment's end is never reported before its
start - logs whatever goes wrong, and is drained, under a hard cap, when the
process exits, so a CLI deploy's statuses are not abandoned mid-request.
Which GitHub deployment belongs to which Noust deployment, and where it is
reported, is remembered in memory from the start: the events of one
deployment are published by the process that runs it, and a first deploy
that fails forgets the application's records before its end is published.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from noust.core.background import BackgroundQueue
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from noust.integrations.github.app import GitHubApp, installation_for, load_app
from noust.validators.source import github_repository

logger = logging.getLogger(__name__)

#: GitHub deployments remembered, oldest forgotten first.
_REMEMBERED = 256

#: GitHub truncates nothing and refuses a description over 140 characters.
_MAX_DESCRIPTION = 140


@dataclass(frozen=True)
class Target:
    """
    Where to report one application's deployments.

    Attributes:
        repository: ``owner/repo``.
        installation_id: The installation that reaches it.
        environment: ``production``, or ``preview`` for a preview.
        environment_url: The application's address.
    """

    repository: str
    installation_id: int
    environment: str
    environment_url: str


def target_for(domain: str) -> Target | None:
    """
    Decide whether an application's deployments are reported, and where.

    Args:
        domain: The application's domain.

    Returns:
        The target, or None when the application is unknown, its source is
        not on github.com, or no installation of the App covers it.
    """
    try:
        store = get_store()
        if store.get_github_app() is None:
            return None
        app = store.get_app(domain)
    except (NoustError, sqlite3.Error) as exc:
        logger.debug("Could not read %s for GitHub statuses: %s", domain, exc)
        return None
    if app is None:
        return None
    repository = github_repository(app.source)
    if repository is None:
        return None
    installation = installation_for(repository, app.github_installation_id)
    if installation is None:
        return None
    return Target(
        repository=repository,
        installation_id=installation,
        environment="preview" if app.preview_parent else "production",
        environment_url=f"https://{app.domain}",
    )


class StatusReporter:
    """
    Report deployment events to GitHub, one at a time, in order.

    Args:
        load: Returns the App, or None (tests inject a fake).
    """

    def __init__(self, load: Any = load_app) -> None:
        self._load = load
        self._deployments: OrderedDict[tuple[str, int | None], int] = OrderedDict()
        self._targets: OrderedDict[tuple[str, int], Target] = OrderedDict()
        self._queue = BackgroundQueue("wasm-github-statuses")
        self._lock = threading.Lock()

    def resolve(self, event: DeployEvent) -> Target | None:
        """
        Decide where an event is reported, remembering it from the start.

        Called in the deploying thread. The target of a deployment with an
        id is looked up at STARTED and kept until its end: a first deploy that
        fails forgets the application before FAILED is published, and a
        second lookup then would leave the GitHub deployment in progress.

        Args:
            event: What happened.

        Returns:
            The target, or None when there is nothing to report.
        """
        if event.deployment_id is None:
            return target_for(event.domain)
        key = (event.domain, event.deployment_id)
        if event.kind is DeployEventKind.STARTED:
            target = target_for(event.domain)
            if target is not None:
                with self._lock:
                    self._targets[key] = target
                    while len(self._targets) > _REMEMBERED:
                        self._targets.popitem(last=False)
            return target
        with self._lock:
            cached = self._targets.pop(key, None)
        return cached if cached is not None else target_for(event.domain)

    def cached_targets(self) -> int:
        """
        Count the deployments whose target is remembered (tests).

        Returns:
            How many started deployments have not ended.
        """
        with self._lock:
            return len(self._targets)

    def submit(self, target: Target, event: DeployEvent) -> None:
        """
        Queue an event for the worker.

        Args:
            target: Where to report.
            event: What happened.
        """
        self._queue.submit(lambda: self._report_logged(target, event))

    def drain(self, timeout: float | None = None) -> bool:
        """
        Wait until every queued event was reported (tests, shutdown).

        Args:
            timeout: Seconds to wait at most; None waits as long as it takes.

        Returns:
            True when everything queued was reported.
        """
        return self._queue.drain(timeout)

    def _report_logged(self, target: Target, event: DeployEvent) -> None:
        """
        Report one event on the worker, logging what goes wrong.

        Args:
            target: Where to report.
            event: What happened.
        """
        try:
            self.report(target, event)
        except (NoustError, sqlite3.Error, OSError, ValueError) as exc:
            # IntegrationError is a NoustError; the rest is a store or a
            # secret file that could not be read. None of it may end the
            # worker, which the next deployment still needs.
            logger.warning(
                "GitHub deployment status for %s (%s) not reported: %s",
                event.domain,
                event.kind.value,
                exc,
            )

    def report(self, target: Target, event: DeployEvent) -> None:
        """
        Report one event now, in the calling thread.

        Args:
            target: Where to report.
            event: What happened.

        Raises:
            IntegrationError: GitHub refused or could not be reached.
        """
        app = self._load()
        if app is None:
            return
        key = (event.domain, event.deployment_id)
        deployment = self._deployments.get(key)
        if event.kind is DeployEventKind.STARTED or deployment is None:
            deployment = self._create(app, target, event)
            if deployment is None:
                return
            self._remember(key, deployment)
        if event.kind is DeployEventKind.STARTED:
            self._status(app, target, deployment, "in_progress", "Deploying")
            return
        if event.kind is DeployEventKind.SUCCEEDED:
            self._status(app, target, deployment, "success", "Deployed")
        elif event.kind is DeployEventKind.ROLLED_BACK:
            self._status(app, target, deployment, "failure", "rolled back")
        else:
            self._status(app, target, deployment, "failure", _describe_failure(event.error))
        self._deployments.pop(key, None)

    def _remember(self, key: tuple[str, int | None], deployment: int) -> None:
        """
        Remember which GitHub deployment a Noust deployment is.

        Args:
            key: The domain and Noust deployment id.
            deployment: GitHub's deployment id.
        """
        self._deployments[key] = deployment
        while len(self._deployments) > _REMEMBERED:
            self._deployments.popitem(last=False)

    def _create(self, app: GitHubApp, target: Target, event: DeployEvent) -> int | None:
        """
        Create the GitHub deployment for a Noust deployment.

        Args:
            app: The App.
            target: Where to report.
            event: The event that needs it.

        Returns:
            GitHub's deployment id, or None when there is no ref to name or
            GitHub declined to create one (it answers 202 with a message
            when it would first have to merge).
        """
        ref = event.commit or event.branch
        if not ref:
            logger.debug("No commit or branch for %s; no GitHub deployment", event.domain)
            return None
        preview = target.environment == "preview"
        answer = app.as_installation(
            target.installation_id,
            "POST",
            f"{_repo_path(target.repository)}/deployments",
            {
                "ref": ref,
                "environment": target.environment,
                "auto_merge": False,
                "required_contexts": [],
                "description": f"Noust deploy of {event.domain}"[:_MAX_DESCRIPTION],
                "transient_environment": preview,
                "production_environment": not preview,
            },
        )
        if not isinstance(answer, dict) or answer.get("id") is None:
            message = answer.get("message") if isinstance(answer, dict) else None
            logger.info("GitHub created no deployment for %s: %s", event.domain, message)
            return None
        return int(answer["id"])

    def _status(
        self, app: GitHubApp, target: Target, deployment: int, state: str, description: str
    ) -> None:
        """
        Set a GitHub deployment's state.

        Args:
            app: The App.
            target: Where to report.
            deployment: GitHub's deployment id.
            state: ``in_progress``, ``success`` or ``failure``.
            description: Shown next to the state on GitHub.
        """
        body: dict[str, Any] = {
            "state": state,
            "description": description[:_MAX_DESCRIPTION],
            "environment": target.environment,
            "auto_inactive": state == "success",
        }
        if state == "success":
            body["environment_url"] = target.environment_url
        app.as_installation(
            target.installation_id,
            "POST",
            f"{_repo_path(target.repository)}/deployments/{deployment}/statuses",
            body,
        )


def _repo_path(repository: str) -> str:
    """
    Spell a repository's API path.

    Args:
        repository: ``owner/repo``.

    Returns:
        ``/repos/<owner>/<repo>``.
    """
    owner, _, name = repository.partition("/")
    return f"/repos/{quote(owner)}/{quote(name)}"


def _describe_failure(error: str | None) -> str:
    """
    Shorten a failure to GitHub's description limit.

    Args:
        error: The recorded error, secrets already scrubbed.

    Returns:
        Its first line, or a generic description.
    """
    first = (error or "").strip().splitlines()[0] if (error or "").strip() else ""
    return first or "Deploy failed"


reporter = StatusReporter()


def on_deploy_event(event: DeployEvent) -> None:
    """
    Report a deployment event to GitHub in the background.

    Reads the store to decide whether there is anything to report, then
    returns; the network work happens on the reporter's thread.

    Args:
        event: What happened.
    """
    target = reporter.resolve(event)
    if target is not None:
        reporter.submit(target, event)
