# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A deployment's start and end as a status on the commit, and a preview as a comment on its pull request.

The sibling of :mod:`noust.integrations.github.statuses` for the hosts that have no App:
GitLab, Gitea, Forgejo, Gogs (comments only: it has no commit statuses) and Bitbucket Cloud.
A default subscriber of :mod:`noust.deployers.deploy_events`: when an application whose source
is a repository on one of them starts deploying, its commit is marked running; when it ends, the
same check is marked success, failure (with "rolled back" when what served before was put back)
or cancelled.

Nothing here may slow or fail a deployment. The subscriber only reads the store and hands the
event to one worker (:mod:`noust.core.background`), which finds the credential, talks to the host
in order - an end is never reported before its start - and logs whatever goes wrong. What signs
in is the credential the operator gave for cloning the repository, or a GitLab connection's:
without either there is nothing to say it with, and nothing is said.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlsplit

from noust.core.background import BackgroundQueue
from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.deployers.deploy_events import DeployEvent, DeployEventKind
from noust.deployers.importers.remote import Opener
from noust.integrations.forges import api
from noust.integrations.forges.hosts import GITHUB
from noust.integrations.webhook import forge_of
from noust.managers.source_credentials import GitCredentialManager

logger = logging.getLogger(__name__)

#: Deployments remembered between their start and their end, oldest forgotten first.
_REMEMBERED = 256

#: The name of the check a deployment appears as on the commit.
CONTEXT = "noust/deploy"
PREVIEW_CONTEXT = "noust/preview"


@dataclass(frozen=True)
class Target:
    """
    Where to report one application's deployments.

    Attributes:
        repo: The repository.
        url: The repository's clone URL, which is what the credential is found by.
        context: The name of the check.
        environment_url: The application's address.
        console_url: The page of the application in the console, when it has a public address.
    """

    repo: api.ForgeRepo
    url: str
    context: str
    environment_url: str
    console_url: str | None


def repo_of(source: str | None) -> api.ForgeRepo | None:
    """
    Find the repository a source names, on a host Noust writes back to.

    Args:
        source: An application's source.

    Returns:
        The repository; None for a source that is not a repository, is GitHub's (its App
        does that) or is on a host nothing says the kind of (declare it in
        ``integrations.forge_hosts``).
    """
    info = forge_of(source)
    if info.forge is None or info.forge == GITHUB or not info.host or not info.repository:
        return None
    authority = info.host
    parts = urlsplit((source or "").split("#", 1)[0])
    if parts.scheme in ("http", "https") and parts.port:
        authority = f"{info.host}:{parts.port}"
    return api.ForgeRepo(kind=info.forge, authority=authority, repository=info.repository)


def target_for(domain: str) -> Target | None:
    """
    Decide whether an application's deployments are reported, and where.

    Args:
        domain: The application's domain.

    Returns:
        The target, or None when the application is unknown or its source is not on a host
        Noust reports to.
    """
    try:
        app = get_store().get_app(domain)
    except (NoustError, sqlite3.Error) as exc:
        logger.debug("Could not read %s for commit statuses: %s", domain, exc)
        return None
    if app is None or not app.source:
        return None
    repo = repo_of(app.source)
    if repo is None:
        return None
    base = str(Config().get("web.public_url", "") or "").rstrip("/")
    return Target(
        repo=repo,
        url=app.source.split("#", 1)[0],
        context=PREVIEW_CONTEXT if app.preview_parent else CONTEXT,
        environment_url=f"https://{app.domain}",
        console_url=f"{base}/apps/{app.domain}" if base else None,
    )


def _describe_failure(error: str | None) -> str:
    """
    Args:
        error: The recorded error, secrets already scrubbed.

    Returns:
        Its first line, or a generic description.
    """
    text = (error or "").strip()
    return text.splitlines()[0] if text else "Deploy failed"


class StatusReporter:
    """
    Report deployment events to the code host, one at a time, in order.

    Args:
        opener: Replacement for the HTTP opener (tests).
        credentials: Finds what signs in to a repository; the credential manager by default.
    """

    def __init__(
        self,
        opener: Opener | None = None,
        credentials: GitCredentialManager | None = None,
    ) -> None:
        self._opener = opener
        self._credentials = credentials
        self._targets: OrderedDict[tuple[str, int], Target] = OrderedDict()
        self._commits: OrderedDict[tuple[str, int], str] = OrderedDict()
        self._queue = BackgroundQueue("noust-forge-statuses")
        self._lock = threading.Lock()

    def resolve(self, event: DeployEvent) -> Target | None:
        """
        Decide where an event is reported, remembering it from the start.

        Called in the deploying thread. A first deploy that fails forgets the application
        before the end is published, so the target of a deployment with an id is looked up at
        its start and kept until its end.

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
            # None of it may end the worker, which the next deployment still needs. The
            # message names the host and the refusal; a credential is never in either.
            logger.warning(
                "The status of %s (%s) was not written to %s: %s %s",
                event.domain,
                event.kind.value,
                target.repo.authority,
                getattr(exc, "message", exc),
                getattr(exc, "details", "") or "",
            )

    def _commit(self, event: DeployEvent) -> str | None:
        """
        Args:
            event: What happened.

        Returns:
            The commit to mark: the event's, else the one the deployment's start named.
        """
        key = (event.domain, event.deployment_id or 0)
        if event.commit:
            with self._lock:
                self._commits[key] = event.commit
                while len(self._commits) > _REMEMBERED:
                    self._commits.popitem(last=False)
            return event.commit
        with self._lock:
            return self._commits.get(key)

    def report(self, target: Target, event: DeployEvent) -> None:
        """
        Report one event now, in the calling thread.

        Args:
            target: Where to report.
            event: What happened.

        Raises:
            IntegrationError: The host refused or could not be reached.
        """
        sha = self._commit(event)
        if not sha:
            logger.debug("No commit for %s; no status", event.domain)
            return
        manager = self._credentials or GitCredentialManager()
        credential = manager.credential_for(target.url)
        if credential is None:
            logger.debug("No credential for %s; no status", target.repo.repository)
            return
        client = api.api_for(target.repo, credential, opener=self._opener)
        if not client.supports_statuses:
            return
        state, description, link = self._state(event, target)
        client.set_status(sha, state, description=description, url=link, context=target.context)

    @staticmethod
    def _state(event: DeployEvent, target: Target) -> tuple[str, str, str]:
        """
        Args:
            event: What happened.
            target: Where it is reported.

        Returns:
            The state, a line for beside it and where "details" leads.
        """
        if event.kind is DeployEventKind.STARTED:
            return api.RUNNING, "Deploying", target.console_url or target.environment_url
        if event.kind is DeployEventKind.SUCCEEDED:
            return api.SUCCESS, "Deployed", target.environment_url
        link = target.console_url or target.environment_url
        if event.kind is DeployEventKind.ROLLED_BACK:
            return api.FAILURE, "Rolled back", link
        if event.cancelled:
            # Not a failure: it never ran.
            return api.CANCELLED, "Cancelled", link
        return api.FAILURE, _describe_failure(event.error), link


reporter = StatusReporter()


def on_deploy_event(event: DeployEvent) -> None:
    """
    Report a deployment event to the code host in the background.

    Reads the store to decide whether there is anything to report, then returns; the
    network work happens on the reporter's thread.

    Args:
        event: What happened.
    """
    target = reporter.resolve(event)
    if target is not None:
        reporter.submit(target, event)


def upsert_pr_comment(
    parent_domain: str,
    repository: str,
    number: int,
    body: str,
    comment_ref: str | None = None,
    *,
    opener: Opener | None = None,
    credentials: GitCredentialManager | None = None,
) -> str | None:
    """
    Create, or edit, Noust's comment on a pull request or merge request.

    Args:
        parent_domain: The application previewed, whose source says which host it is.
        repository: ``owner/repo`` of the request, as the webhook named it.
        number: The pull (merge) request number.
        body: Markdown to show.
        comment_ref: The id an earlier call returned, to edit that comment.
        opener: Replacement for the HTTP opener (tests).
        credentials: Finds what signs in; the credential manager by default.

    Returns:
        The comment's id; None when this host cannot be written to (no credential, a kind
        Noust does not write to).

    Raises:
        IntegrationError: The host refused or could not be reached.
    """
    app = get_store().get_app(parent_domain)
    repo = repo_of(app.source) if app is not None else None
    if app is None or repo is None or not app.source:
        return None
    manager = credentials or GitCredentialManager()
    credential = manager.credential_for(app.source.split("#", 1)[0])
    if credential is None:
        return None
    # The request lives in the repository the webhook named: the application's own host,
    # that repository.
    where = api.ForgeRepo(repo.kind, repo.authority, repository)
    return api.api_for(where, credential, opener=opener).upsert_comment(number, body, comment_ref)
