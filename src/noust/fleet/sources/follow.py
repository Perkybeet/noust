# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central writes the status of the commit and the comment of the pull request (item 90, D6).

A server deploys what an event asks for, but GitHub's App is the central's, so the server has
nothing to write the commit's status or the pull request's comment with. The central does, and
needs no road from the server to itself: it follows what the server queued, asking the server's own
API every few seconds until it ends (:class:`~noust.fleet.sources.follows.Follows` keeps what to
look at).

- **A deployment** is a job of the server. While it waits the commit's deployment on GitHub is
  ``queued``, while it builds ``in_progress``, and when it ends it is what
  :func:`~noust.integrations.github.statuses.deployment_state` says of how it ended: the one
  function the server's own deployments are reported with, so a deployment ends in the same words
  whichever machine reported it.
- **A preview** is a record of the server that goes from queued to ready or failed. The comment of
  the pull request is :func:`~noust.managers.previews.preview_comment`, the very text a server
  with its own App would have written, edited as the preview changes and never said twice.
- **A server that stops answering** is looked at for three hours; after that the central says on
  GitHub that it lost track, instead of leaving a deployment in progress for ever.
- **A failure to write to GitHub never loses the follow**: it is tried again at the next look.

Nothing here touches a server's lease: these are the central's own credentials, installation tokens
of the whole installation, which are never given to a server.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from noust.core.deploy_events import DeployEvent, DeployEventKind
from noust.core.exceptions import NoustError
from noust.core.store import PreviewRecord
from noust.fleet.aggregate import Asker, Failure, NodeSource
from noust.fleet.nodes import NodeManager
from noust.fleet.sources.follows import Follow, Follows
from noust.fleet.sources.keeper import ACTOR, NODE_TIMEOUT
from noust.integrations.github.app import load_app
from noust.integrations.github.comments import upsert_pr_comment
from noust.integrations.github.statuses import (
    Target,
    create_deployment,
    deployment_state,
    post_status,
)

logger = logging.getLogger(__name__)

__all__ = ["LOST_AFTER", "FollowReport", "Follower"]

#: Seconds after which a deployment or a preview the central cannot see through is given up on.
LOST_AFTER = 3 * 3600

#: What a preview's status is when it will change no more without a push.
_PREVIEW_DONE = ("ready", "failed", "awaiting", "stopped")


@dataclass(frozen=True)
class FollowReport:
    """
    What one look at a follow came to.

    Attributes:
        follow: The follow's number.
        node: The server.
        domain: The application.
        outcome: ``waiting`` (nothing new), ``reported`` (something was written on GitHub),
            ``done`` (it ended) or ``lost`` (the central gave up on seeing it through).
        said: What was written on GitHub, such as the deployment's state or the comment's status.
    """

    follow: int
    node: str
    domain: str
    outcome: str
    said: str | None = None


def _digest(text: str) -> str:
    """
    Args:
        text: A comment's body.

    Returns:
        A short digest, to tell whether the comment changed.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class Follower:
    """
    Follows what the servers started for an event until it ends, and says it on GitHub.

    Args:
        manager: The registry of servers; the default one when None.
        clock: Epoch seconds (tests move it).
        follows: What is followed.
    """

    def __init__(
        self,
        manager: NodeManager | None = None,
        *,
        clock: Callable[[], float] = time.time,
        follows: Follows | None = None,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._follows = follows

    @property
    def manager(self) -> NodeManager:
        """The registry of servers."""
        if self._manager is None:
            self._manager = NodeManager()
        return self._manager

    @property
    def follows(self) -> Follows:
        """What is followed."""
        return self._follows or Follows(self.manager.store, self._clock)

    def step(self) -> list[FollowReport]:
        """
        Look once at everything that is followed.

        Returns:
            What each look came to.
        """
        return [self.look(follow) for follow in self.follows.active()]

    def look(self, follow: Follow) -> FollowReport:
        """
        Look at one deployment or preview and say on GitHub what changed.

        Args:
            follow: What to look at.

        Returns:
            What it came to. A server that cannot be reached, or GitHub refusing, is a look that
            finds nothing new and is made again.
        """
        try:
            report = self._preview(follow) if follow.kind == "preview" else self._deployment(follow)
            if report.outcome == "waiting" and self._age(follow) >= LOST_AFTER:
                return self._unseen(follow, "it did not end in three hours")
            return report
        except Failure as failure:
            return self._unseen(follow, failure.message)
        except NoustError as exc:
            # GitHub refused or could not be reached: the follow is kept and tried at the next look.
            logger.warning(
                "Could not say on GitHub what became of %s on %s: %s",
                follow.domain,
                follow.node,
                exc.message,
            )
            return FollowReport(follow.id, follow.node, follow.domain, "waiting")

    # ----------------------------------------------------------------------- the server

    def _source(self, follow: Follow) -> NodeSource:
        """
        Args:
            follow: What is followed.

        Returns:
            The server it is on, asked as the central asks to read.

        Raises:
            Failure: The server is no longer in the fleet.
        """
        record = self.manager.store.get_node(follow.node)
        if record is None:
            raise Failure("unreachable", f"{follow.node} is no longer in the fleet")
        return NodeSource(self.manager, record, Asker(actor=ACTOR, scope="read"), NODE_TIMEOUT)

    def _unseen(self, follow: Follow, why: str) -> FollowReport:
        """
        Decide what to do about a follow the server cannot be asked about.

        Args:
            follow: What is followed.
            why: Why it could not be asked.

        Returns:
            Waiting, or lost once it has been too long.
        """
        if self._age(follow) < LOST_AFTER:
            return FollowReport(follow.id, follow.node, follow.domain, "waiting")
        self.follows.finish(follow.id, "lost", why)
        if follow.kind == "deployment" and follow.provider_ref is not None:
            target = self._target(follow)
            app = load_app()
            if target is not None and app is not None:
                try:
                    post_status(
                        app,
                        target,
                        follow.provider_ref,
                        "error",
                        "Noust lost track of this deployment",
                    )
                except NoustError as exc:
                    logger.warning("Could not tell GitHub a deployment was lost: %s", exc.message)
        return FollowReport(follow.id, follow.node, follow.domain, "lost", "error")

    def _age(self, follow: Follow) -> float:
        """
        Args:
            follow: What is followed.

        Returns:
            Seconds it has been followed: a deployment since it began, a preview since the pull
            request last asked for it (a push to it follows the same row again).
        """
        return self._clock() - (
            follow.updated_at if follow.kind == "preview" else follow.created_at
        )

    @staticmethod
    def _target(follow: Follow) -> Target | None:
        """
        Args:
            follow: What is followed.

        Returns:
            Where it is reported on GitHub; None when no installation reaches the repository.
        """
        if follow.installation_id is None:
            return None
        return Target(
            repository=follow.repository,
            installation_id=follow.installation_id,
            environment=follow.environment,
            environment_url=follow.environment_url or f"https://{follow.domain}",
        )

    # ----------------------------------------------------------------------- a deployment

    def _deployment(self, follow: Follow) -> FollowReport:
        """
        Follow an update a server queued.

        Args:
            follow: The follow.

        Returns:
            What the look came to.

        Raises:
            Failure: The server could not be asked.
        """
        source = self._source(follow)
        job = source.get(f"/api/jobs/{quote(str(follow.job_id), safe='')}")
        if not isinstance(job, Mapping):
            raise Failure("error", f"{follow.node} answered a job that is not an object")
        state = str(job.get("state") or "")
        if state in ("queued", "running"):
            return self._say(
                follow,
                "queued" if state == "queued" else "in_progress",
                "Waiting its turn" if state == "queued" else "Deploying",
            )
        event = self._ending(source, follow, job, state)
        said, description = deployment_state(event)
        return self._say(follow, said, description, final=True)

    def _ending(
        self, source: NodeSource, follow: Follow, job: Mapping[str, Any], state: str
    ) -> DeployEvent:
        """
        Read how an update ended.

        Args:
            source: The server.
            follow: The follow.
            job: The server's job, finished.
            state: Its state.

        Returns:
            The event the deployment ended with, as the server's own would have been.
        """
        deployed: Mapping[str, Any] = {}
        deployment_id = job.get("deployment_id")
        if isinstance(deployment_id, int):
            try:
                found = source.get(f"/api/deployments/{deployment_id}")
                deployed = found if isinstance(found, Mapping) else {}
            except Failure as failure:
                logger.info(
                    "Could not read deployment %s on %s: %s",
                    deployment_id,
                    follow.node,
                    failure.message,
                )
        status = str(deployed.get("status") or "")
        error = job.get("error") or deployed.get("error")
        cancelled = state == "cancelled" or bool(deployed.get("cancelled"))
        if status == "rolled_back":
            kind = DeployEventKind.ROLLED_BACK
        elif status == "success" or (not status and state == "succeeded"):
            kind = DeployEventKind.SUCCEEDED
        else:
            kind = DeployEventKind.FAILED
        return DeployEvent(
            kind=kind,
            domain=follow.domain,
            commit=follow.ref,
            error=str(error) if error else None,
            cancelled=cancelled,
        )

    def _say(
        self, follow: Follow, state: str, description: str, *, final: bool = False
    ) -> FollowReport:
        """
        Say on GitHub what a deployment is doing, once.

        Args:
            follow: The follow.
            state: GitHub's deployment state.
            description: Shown beside it.
            final: The deployment ended.

        Returns:
            What the look came to.
        """
        target, app = self._target(follow), load_app()
        if target is None or app is None:
            self.follows.finish(follow.id, "lost", "the GitHub App does not reach the repository")
            return FollowReport(follow.id, follow.node, follow.domain, "lost")
        reported = FollowReport(follow.id, follow.node, follow.domain, "waiting")
        if follow.last_state != state:
            deployment = follow.provider_ref or create_deployment(
                app, target, ref=follow.ref, domain=follow.domain
            )
            if deployment is None:
                # GitHub declined to make a deployment (it has to merge first): nothing to follow.
                self.follows.finish(follow.id, "done", "GitHub created no deployment")
                return FollowReport(follow.id, follow.node, follow.domain, "done")
            post_status(app, target, deployment, state, description)
            self.follows.remember(follow.id, provider_ref=deployment, last_state=state)
            reported = FollowReport(follow.id, follow.node, follow.domain, "reported", state)
        if final:
            self.follows.finish(follow.id, "done", state)
            return FollowReport(follow.id, follow.node, follow.domain, "done", state)
        return reported

    # ----------------------------------------------------------------------- a preview

    def _preview(self, follow: Follow) -> FollowReport:
        """
        Follow the preview of a pull request.

        Args:
            follow: The follow.

        Returns:
            What the look came to.

        Raises:
            Failure: The server could not be asked.
        """
        from noust.managers.previews import preview_comment

        source = self._source(follow)
        listed = source.get(f"/api/apps/{quote(follow.domain, safe='')}/previews")
        items = listed.get("previews") if isinstance(listed, Mapping) else None
        found = next(
            (
                item
                for item in items or []
                if isinstance(item, Mapping) and item.get("number") == follow.number
            ),
            None,
        )
        if follow.action == "closed":
            if found is not None:
                return FollowReport(follow.id, follow.node, follow.domain, "waiting")
            if follow.comment_ref is None:
                self.follows.finish(follow.id, "done", "closed with no preview")
                return FollowReport(follow.id, follow.node, follow.domain, "done")
            record = self._record(follow, {})
            return self._comment(
                follow, preview_comment(record, removed_because="closed"), "removed", final=True
            )
        if found is None:
            self.follows.finish(follow.id, "done", "no preview was made")
            return FollowReport(follow.id, follow.node, follow.domain, "done")
        record = self._record(follow, found)
        status = str(found.get("status") or "pending")
        return self._comment(follow, preview_comment(record), status, final=status in _PREVIEW_DONE)

    @staticmethod
    def _record(follow: Follow, item: Mapping[str, Any]) -> PreviewRecord:
        """
        Args:
            follow: The follow.
            item: The preview as the server lists it; empty for one that was removed.

        Returns:
            The record the comment of a preview is written from.
        """
        return PreviewRecord(
            parent_domain=follow.domain,
            domain=str(item.get("domain") or follow.domain),
            number=int(follow.number or 0),
            branch=str(item.get("branch") or ""),
            provider="github",
            expires_at=str(item.get("expires_at") or ""),
            head_sha=item.get("head_sha") or follow.ref,
            repository=follow.repository,
            comment_ref=follow.comment_ref,
            status=str(item.get("status") or "pending"),
        )

    def _comment(self, follow: Follow, body: str, label: str, *, final: bool) -> FollowReport:
        """
        Write or edit the comment of the pull request, unless it says what it already says.

        Args:
            follow: The follow.
            body: The comment.
            label: What the preview is, for the report.
            final: The preview will change no more.

        Returns:
            What the look came to.
        """
        digest = _digest(body)
        reported = FollowReport(follow.id, follow.node, follow.domain, "waiting")
        if follow.last_state != digest:
            posted = upsert_pr_comment(
                follow.repository, int(follow.number or 0), body, follow.comment_ref
            )
            if posted is None:
                self.follows.finish(
                    follow.id, "lost", "the GitHub App does not reach the repository"
                )
                return FollowReport(follow.id, follow.node, follow.domain, "lost")
            self.follows.remember(follow.id, comment_ref=posted, last_state=digest)
            reported = FollowReport(follow.id, follow.node, follow.domain, "reported", label)
        if final:
            self.follows.finish(follow.id, "done", label)
            return FollowReport(follow.id, follow.node, follow.domain, "done", label)
        return reported
