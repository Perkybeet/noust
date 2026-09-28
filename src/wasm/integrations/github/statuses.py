# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GitHub deployments and deployment statuses for WASM's deployments.

A default subscriber of :mod:`wasm.deployers.deploy_events`: when an
application whose source is a github.com repository covered by this server's
App starts deploying, a GitHub deployment is created for the commit (or
branch) and marked in progress; when it ends, the same deployment is marked
success, or failure (with "rolled back" when what served before was put
back).

Nothing here may slow or fail a deployment. The subscriber only reads the
store and hands the event to one worker thread, which talks to GitHub in
order - a deployment's end is never reported before its start - and logs
whatever goes wrong. Which GitHub deployment belongs to which WASM deployment
is remembered in memory: the events of one deployment are published by the
process that runs it.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from wasm.core.exceptions import WASMError
from wasm.core.store import get_store
from wasm.deployers.deploy_events import DeployEvent, DeployEventKind
from wasm.integrations.github.app import GitHubApp, installation_for, load_app
from wasm.validators.source import github_repository

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
    except (WASMError, sqlite3.Error) as exc:
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
        self._queue: queue.Queue[tuple[Target, DeployEvent]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, target: Target, event: DeployEvent) -> None:
        """
        Queue an event for the worker, starting it if needed.

        Args:
            target: Where to report.
            event: What happened.
        """
        self._queue.put((target, event))
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run, name="wasm-github-statuses", daemon=True
                )
                self._worker.start()

    def drain(self) -> None:
        """Wait until every queued event was reported (tests, shutdown)."""
        self._queue.join()

    def _run(self) -> None:
        """Report queued events until the process ends."""
        while True:
            target, event = self._queue.get()
            try:
                self.report(target, event)
            except (WASMError, sqlite3.Error, OSError, ValueError) as exc:
                # IntegrationError is a WASMError; the rest is a store or a
                # secret file that could not be read. None of it may end the
                # worker, which the next deployment still needs.
                logger.warning(
                    "GitHub deployment status for %s (%s) not reported: %s",
                    event.domain,
                    event.kind.value,
                    exc,
                )
            finally:
                self._queue.task_done()

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
        Remember which GitHub deployment a WASM deployment is.

        Args:
            key: The domain and WASM deployment id.
            deployment: GitHub's deployment id.
        """
        self._deployments[key] = deployment
        while len(self._deployments) > _REMEMBERED:
            self._deployments.popitem(last=False)

    def _create(self, app: GitHubApp, target: Target, event: DeployEvent) -> int | None:
        """
        Create the GitHub deployment for a WASM deployment.

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
                "description": f"WASM deploy of {event.domain}"[:_MAX_DESCRIPTION],
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
    target = target_for(event.domain)
    if target is not None:
        reporter.submit(target, event)
