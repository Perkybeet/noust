# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central hands the servers that deploy a repository the events GitHub delivered to it (item 90, D6).

GitHub delivers a push, a tag or a pull request to the central's ``/hooks/github``: the App, and so
its webhook, are the central's, and a server managed by it has no address GitHub could call. The
central therefore does what the server's own webhook would have done, one step before:

- **It finds the servers** that deploy the repository (:meth:`Forwarder.targets`): it asks the
  servers' applications through the aggregator the console's pages use, and applies to each row the
  very rule the servers apply to their own (:func:`~noust.integrations.github.webhooks.follows_push`),
  so a push to a branch no application follows reaches nobody.
- **It writes the event down for each, before it answers GitHub** (:meth:`Forwarder.enqueue`), so a
  server that is down when a push arrives gets it when it is back, and a delivery that could not be
  made says why.
- **It hands it over** (:meth:`Forwarder.deliver`), after making sure the server holds access to
  clone the repository (:class:`~noust.fleet.sources.keeper.LeaseKeeper`): ``POST
  /api/sources/events`` with the fleet token at the ``deploy`` scope, naming the delivery as the
  actor so the server's audit trail says which push it was. The server queues the update exactly as
  it does for its own webhook and answers which jobs it queued.
- **It follows what the server queued** (:mod:`~noust.fleet.sources.follow`), because the status of
  the commit and the comment of the pull request are the central's to write.

A server that is independent is handed nothing, one that is down is tried again for an hour, and
one that refuses (an older Noust, a ceiling that only reads, an event it will not take) is not asked
again: each says why with a code.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from noust.core.forge_events import (
    PullRequestEvent,
    PushEvent,
    TagEvent,
    event_kind,
    event_to_dict,
)
from noust.core.github_host import hosts_of
from noust.core.server_text import ServerText, say
from noust.fleet.aggregate import Asker, Failure, NodeSource, decode_answer, gather
from noust.fleet.client import actor_label
from noust.fleet.nodes import NodeManager
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.sources.follows import Follows
from noust.fleet.sources.forwards import Forward, Forwards
from noust.fleet.sources.keeper import NODE_TIMEOUT, LeaseKeeper
from noust.integrations.github.app import installation_for, load_app
from noust.integrations.github.webhooks import follows_push, follows_tags, repository_of

logger = logging.getLogger(__name__)

__all__ = ["EVENTS_PATH", "GIVE_UP_AFTER", "Forwarder"]

#: Where a server takes an event.
EVENTS_PATH = "/api/sources/events"

#: Seconds a server is tried again before the central gives up on an event.
GIVE_UP_AFTER = 3600

#: Seconds the central waits to read the servers' applications while GitHub waits for its answer.
FIND_DEADLINE = 5.0


def _backoff(attempts: int) -> float:
    """
    Args:
        attempts: How many times the delivery was tried.

    Returns:
        Seconds before the next try: twenty, then doubling, never more than five minutes.
    """
    return float(min(20 * 2 ** max(attempts, 0), 300))


class Forwarder:
    """
    Finds, remembers and hands over the events of the repositories a central's servers deploy.

    Args:
        manager: The registry of servers; the default one when None.
        clock: Epoch seconds (tests move it).
        keeper: The keeper of the access servers clone with; a default one when None.
        forwards: The deliveries owed.
        follows: What servers started for them.
        settings: The fleet's settings, for which servers are independent.
    """

    def __init__(
        self,
        manager: NodeManager | None = None,
        *,
        clock: Callable[[], float] = time.time,
        keeper: LeaseKeeper | None = None,
        forwards: Forwards | None = None,
        follows: Follows | None = None,
        settings: FleetSettings | None = None,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._keeper = keeper
        self._forwards = forwards
        self._follows = follows
        self._settings = settings

    @property
    def manager(self) -> NodeManager:
        """The registry of servers."""
        if self._manager is None:
            self._manager = NodeManager()
        return self._manager

    @property
    def keeper(self) -> LeaseKeeper:
        """The keeper of the access servers clone with."""
        if self._keeper is None:
            self._keeper = LeaseKeeper(self.manager, clock=self._clock)
        return self._keeper

    @property
    def forwards(self) -> Forwards:
        """The deliveries owed."""
        return self._forwards or Forwards(self.manager.store, self._clock)

    @property
    def follows(self) -> Follows:
        """What servers started for them."""
        return self._follows or Follows(self.manager.store, self._clock)

    @property
    def settings(self) -> FleetSettings:
        """The fleet's settings."""
        return self._settings or FleetSettings(self.manager.store, self.manager.secrets)

    # ----------------------------------------------------------------------- finding

    def targets(
        self,
        event: PushEvent | TagEvent | PullRequestEvent,
        *,
        host: str,
        default_branch: str | None,
    ) -> list[str]:
        """
        Find the servers that deploy what an event is about.

        Args:
            event: The push, the tag or the pull request.
            host: The GitHub host it came from.
            default_branch: The repository's default branch, which an application with no branch
                of its own follows.

        Returns:
            The servers, by name; those made independent are not among them.
        """
        hosts = hosts_of(host)
        seen = gather(
            "apps",
            None,
            asker=Asker(actor="noust-fleet-sources", scope="read"),
            manager=self.manager,
            node_timeout=FIND_DEADLINE,
            deadline=FIND_DEADLINE,
        )
        modes = self.settings.modes()
        found: set[str] = set()
        for row in seen.items:
            node = str(row.get("node") or "")
            if not node or modes.get(node) == "independent":
                continue
            source, preview_parent = row.get("source"), row.get("preview_parent")
            if isinstance(event, PushEvent):
                follows = follows_push(
                    source=source,
                    branch=row.get("branch"),
                    follow_tags=row.get("follow_tags"),
                    preview_parent=preview_parent,
                    push=event,
                    default_branch=default_branch,
                    hosts=hosts,
                )
            elif isinstance(event, TagEvent):
                follows = follows_tags(
                    source=source,
                    follow_tags=row.get("follow_tags"),
                    preview_parent=preview_parent,
                    repository=event.repository,
                    hosts=hosts,
                )
            else:
                follows = (
                    not preview_parent and repository_of(source, hosts) == event.repository.lower()
                )
            if follows:
                found.add(node)
        return sorted(found)

    # ----------------------------------------------------------------------- remembering

    def enqueue(
        self,
        event: PushEvent | TagEvent | PullRequestEvent,
        *,
        delivery: str,
        host: str,
        default_branch: str | None,
    ) -> list[Forward]:
        """
        Write an event down for every server that deploys what it is about.

        Args:
            event: The push, the tag or the pull request.
            delivery: The code host's id for the delivery.
            host: The GitHub host it came from.
            default_branch: The repository's default branch.

        Returns:
            The deliveries owed, pending and due now; none when no server deploys it.
        """
        owed = self.targets(event, host=host, default_branch=default_branch)
        kind = event_kind(event)
        body = {
            "kind": kind,
            "delivery": delivery,
            "host": host,
            "default_branch": default_branch,
            "event": event_to_dict(event),
        }
        ref: str | None
        number: int | None
        commit: str | None
        if isinstance(event, PushEvent):
            ref, number, commit = event.branch, None, event.head_sha
        elif isinstance(event, TagEvent):
            ref, number, commit = event.tag, None, event.sha
        else:
            ref, number, commit = None, event.number, event.head_sha or None
        return [
            self.forwards.add(
                node,
                body=body,
                delivery=delivery,
                repository=event.repository,
                ref=ref,
                number=number,
                commit=commit,
            )
            for node in owed
        ]

    # ----------------------------------------------------------------------- handing over

    def deliver_due(self) -> list[Forward]:
        """
        Hand over every event whose turn has come.

        Returns:
            Each delivery tried, as it stands after.
        """
        return [self.deliver(forward) for forward in self.forwards.due()]

    def deliver(self, forward: Forward) -> Forward:
        """
        Hand one event to its server.

        The server is first made sure to hold access to clone the repository; whether that worked
        is the keeper's to say, and the event is handed over anyway (a server may clone with a
        deploy key of its own).

        Args:
            forward: The delivery.

        Returns:
            The delivery after the try.
        """
        node = forward.node
        record = self.manager.store.get_node(node)
        if record is None:
            return self._settle(
                forward,
                "failed",
                say("sources.node_unreachable", node=node, command="noust node list"),
            )
        if self.settings.mode(node) == "independent":
            return self._settle(forward, "failed", say("sources.node_independent", node=node))
        try:
            self.keeper.sweep([node])
        except Failure as failure:  # pragma: no cover - the keeper reports instead of raising
            logger.info("Could not lease %s before an event: %s", node, failure.message)
        source = NodeSource(
            self.manager,
            record,
            Asker(actor=actor_label(f"github:{forward.delivery}"), scope="deploy"),
            NODE_TIMEOUT,
        )
        try:
            answer = decode_answer(
                node,
                "POST",
                EVENTS_PATH,
                source.request("POST", EVENTS_PATH, json_body=dict(forward.body)),
            )
        except Failure as failure:
            return self._failed(forward, failure)
        self.forwards.delivered(forward.id, answer if isinstance(answer, dict) else {})
        delivered = self.forwards.get(forward.id) or forward
        self._follow(delivered, answer if isinstance(answer, dict) else {})
        return delivered

    def _failed(self, forward: Forward, failure: Failure) -> Forward:
        """
        Decide what a server's refusal or silence means for an event.

        Args:
            forward: The delivery.
            failure: What came back.

        Returns:
            The delivery after the decision.
        """
        node = forward.node
        if failure.kind == "unsupported":
            return self._settle(
                forward, "failed", say("sources.node_unsupported", node=node), failure
            )
        if failure.kind == "forbidden":
            return self._settle(
                forward,
                "failed",
                say("sources.node_ceiling", node=node, command="noust fleet access --level deploy"),
                failure,
            )
        if failure.kind in ("unreachable", "refused", "timeout") or failure.code == "queue_full":
            return self._retry(forward, failure)
        sentence = say(
            "sources.forward_refused",
            node=node,
            kind=forward.kind.replace("_", " "),
            repository=forward.repository,
            reason=failure.message,
        )
        return self._settle(forward, "failed", sentence, failure)

    def _retry(self, forward: Forward, failure: Failure) -> Forward:
        """
        Try again later, or give up when it has been too long.

        Args:
            forward: The delivery.
            failure: Why this try did not work.

        Returns:
            The delivery after the decision.
        """
        now = self._clock()
        node = forward.node
        waited = now - forward.created_at
        if waited >= GIVE_UP_AFTER:
            sentence = say(
                "sources.forward_gave_up",
                node=node,
                kind=forward.kind.replace("_", " "),
                repository=forward.repository,
                minutes=int(waited // 60),
            )
            return self._settle(forward, "expired", sentence, failure)
        sentence = say("sources.node_unreachable", node=node, command=f"noust node test {node}")
        self.forwards.retry(
            forward.id,
            _error(sentence, failure.output or failure.message),
            now + _backoff(forward.attempts),
        )
        return self.forwards.get(forward.id) or forward

    def _settle(
        self, forward: Forward, state: str, sentence: ServerText, failure: Failure | None = None
    ) -> Forward:
        """
        Record that an event will not be delivered.

        Args:
            forward: The delivery.
            state: ``failed`` or ``expired``.
            sentence: Why.
            failure: What came back, whose own words are kept beside the sentence.

        Returns:
            The delivery after.
        """
        output = (failure.output or failure.message) if failure is not None else None
        self.forwards.settle(forward.id, state, _error(sentence, output))
        return self.forwards.get(forward.id) or forward

    # ----------------------------------------------------------------------- following

    def _follow(self, forward: Forward, answer: Mapping[str, Any]) -> None:
        """
        Begin following what the server queued for an event.

        Args:
            forward: The delivery.
            answer: What the server answered.
        """
        if load_app() is None:
            return
        installation = installation_for(forward.repository)
        if installation is None:
            return
        event = forward.body.get("event")
        event = event if isinstance(event, Mapping) else {}
        if forward.kind == "pull_request":
            for item in answer.get("previews") or []:
                if isinstance(item, Mapping) and isinstance(item.get("number"), int):
                    self.follows.preview(
                        forward.id,
                        forward.node,
                        str(item.get("parent")),
                        int(item["number"]),
                        repository=forward.repository,
                        installation_id=installation,
                        ref=forward.commit,
                        action=str(item.get("action") or "updated"),
                    )
            return
        for job in answer.get("jobs") or []:
            if isinstance(job, Mapping) and job.get("job_id") and job.get("domain"):
                domain = str(job["domain"])
                self.follows.deployment(
                    forward.id,
                    forward.node,
                    domain,
                    str(job["job_id"]),
                    repository=forward.repository,
                    installation_id=installation,
                    ref=forward.commit or forward.ref,
                    environment_url=f"https://{domain}",
                )


def _error(sentence: ServerText, output: str | None) -> dict[str, Any]:
    """
    Args:
        sentence: Why a delivery did not work.
        output: The server's own words.

    Returns:
        What a delivery keeps and the API shows of it.
    """
    return {
        "text": sentence.text,
        "hint": sentence.hint,
        "coded": sentence.to_dict(),
        "output": output,
    }
