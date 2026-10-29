# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The keeper of leases: it gives each server the GitHub access to clone what it serves, and renews it.

A lease is an installation token scoped to the repositories of the server's applications and to
reading their contents (:mod:`noust.integrations.github.leases` is what the server does with it),
good for an hour. The keeper looks at the servers every minute:

- **It reads what each server deploys** (its applications, through the aggregator that every view
  of the fleet uses) and works out the leases that need
  (:func:`~noust.fleet.sources.plan.make_plan`).
- **It renews a lease before it ends**, :data:`RENEW_BEFORE` seconds ahead and not earlier, so
  that a central that goes quiet leaves a server with as much time as it can; and it replaces it
  at once when the applications change, so that an application created on a server can be cloned
  by the next thing that starts there.
- **It sends the token to the server it is for and to nobody**: one request to that server, with
  the central's own fleet token and a ceiling of ``deploy``, never in a log, a report, a record or
  a sentence. What the central keeps is the fact, not the token (:mod:`~noust.fleet.sources.grants`).
- **It says what it could not do, server by server, with a code**: a server on an older Noust, one
  that lets its central only read, one that is down, one made independent, a repository the App
  does not reach, GitHub's own refusal. One of them never stops the rest; a failure is tried again
  after :data:`RETRY_AFTER` seconds, not at every look; and the lease a server already holds is left
  to run to its end.

It is the only code that asks GitHub for a token to lease, which is why the check that the token is
narrow lives with that call (:meth:`~noust.integrations.github.app.GitHubApp.scoped_installation_token`).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import IntegrationError, NoustError
from noust.core.github_host import configured_host
from noust.core.server_text import ServerText, say
from noust.fleet.aggregate import Asker, Failure, NodeSource, decode_answer, gather
from noust.fleet.nodes import NodeManager
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.sources.grants import Grant, LeaseGrants
from noust.fleet.sources.plan import LeaseSpec, make_plan, repositories_of
from noust.integrations.github.app import GitHubApp, installation_for, load_app
from noust.integrations.github.client import GitHubAPIError
from noust.integrations.github.leases import LEASE_PERMISSIONS
from noust.validators.source import github_repository_parts

logger = logging.getLogger(__name__)

__all__ = [
    "ACTOR",
    "LEASES_PATH",
    "RENEW_BEFORE",
    "RESERVED_FOR",
    "RETRY_AFTER",
    "LeaseKeeper",
    "LeaseReport",
    "NodeLeases",
    "Problem",
    "start_keeper",
]

#: Who the servers' audit logs say asked, for what no person pressed.
ACTOR = "noust-fleet-sources"

#: Where a server takes a lease.
LEASES_PATH = "/api/sources/leases"

#: Seconds before a lease ends that it is replaced. GitHub's tokens live 3600: a lease is renewed
#: with 1500 left, so a central that stops calling leaves a server about 25 minutes at the least.
RENEW_BEFORE = 1500

#: Seconds before a lease that failed is tried again.
RETRY_AFTER = 300

#: Seconds before a repository GitHub would not give a token for is asked for again.
UNCOVERED_FOR = 600

#: Seconds a repository asked for ahead of its application (:meth:`LeaseKeeper.reserve`) is kept in
#: the server's lease while no application of the server names it.
RESERVED_FOR = 1800

#: Seconds between two looks at the servers.
LOOK_EVERY = 60.0

#: Seconds the keeper waits for a server to take a lease or answer what it deploys.
NODE_TIMEOUT = 15.0

#: Repositories in a request that fail one by one when GitHub refuses a group of them, at most:
#: more than this is a different problem (a revoked installation), not repositories.
ISOLATE_AT_MOST = 50


@dataclass(frozen=True)
class Problem:
    """
    Something the keeper could not do, as a sentence and the other side's own words.

    Attributes:
        sentence: What is wrong, with its code.
        output: GitHub's or the server's own words, verbatim; never a token.
    """

    sentence: ServerText
    output: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The problem as the API shows it.
        """
        return {
            "text": self.sentence.text,
            "hint": self.sentence.hint,
            "coded": self.sentence.to_dict(),
            "output": self.output,
        }


@dataclass(frozen=True)
class LeaseReport:
    """
    One lease of a server, after a look.

    Attributes:
        key: The lease's name on the server.
        installation_id: The installation its token comes from.
        repositories: What it covers.
        state: ``issued`` (a token was made and handed over now), ``current`` (the server holds
            one that is not due) or ``failed``.
        expires_at: When it ends, epoch seconds on the central's clock.
        error: Why it failed.
    """

    key: str
    installation_id: int
    repositories: tuple[str, ...]
    state: str
    expires_at: float | None = None
    error: Problem | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The lease as the API shows it.
        """
        return {
            "key": self.key,
            "installation_id": self.installation_id,
            "repositories": list(self.repositories),
            "state": self.state,
            "expires_at": self.expires_at,
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass
class NodeLeases:
    """
    Where one server stands on the access to clone with.

    Attributes:
        node: The server.
        state: ``leased`` (it holds what it needs), ``none`` (it deploys nothing from the App's
            repositories), ``independent``, ``unsupported`` (an older Noust), ``ceiling`` (the
            server lets its central only read), ``unreachable``, ``failed``, or ``no_app`` (this
            central has no GitHub App).
        leases: Its leases.
        uncovered: The repositories of its applications that the App does not reach.
        problems: Why, one for each of those.
        error: Why the whole server could not be leased, when that is the case.
        checked_at: When the keeper last looked, epoch seconds.
    """

    node: str
    state: str
    leases: list[LeaseReport] = field(default_factory=list)
    uncovered: tuple[str, ...] = ()
    problems: list[Problem] = field(default_factory=list)
    error: Problem | None = None
    checked_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The server's standing as the API shows it.
        """
        return {
            "node": self.node,
            "state": self.state,
            "leases": [lease.to_dict() for lease in self.leases],
            "uncovered": list(self.uncovered),
            "problems": [problem.to_dict() for problem in self.problems],
            "error": self.error.to_dict() if self.error else None,
            "checked_at": self.checked_at,
        }


def _repository(value: str) -> str:
    """
    Args:
        value: A repository a person or the console named.

    Returns:
        ``owner/repo`` in lower case.

    Raises:
        ValidationError: When it is not a GitHub repository.
    """
    owner, name = github_repository_parts(value)
    return f"{owner}/{name}".lower()


def _scrub(text: str | None, secret: str) -> str | None:
    """
    Args:
        text: Words that came back from a server.
        secret: A token that must not be in them.

    Returns:
        The words, without the token.
    """
    return text.replace(secret, "***") if text and secret else text


class LeaseKeeper:
    """
    Gives every server the access to clone what it serves, and keeps it.

    Args:
        manager: The registry of servers; the default one when None.
        clock: Epoch seconds (tests move it).
        grants: What was leased; the central's own table by default.
        settings: The fleet's settings, for which servers are independent.
    """

    def __init__(
        self,
        manager: NodeManager | None = None,
        *,
        clock: Callable[[], float] = time.time,
        grants: LeaseGrants | None = None,
        settings: FleetSettings | None = None,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._grants = grants
        self._settings = settings
        self._reports: dict[str, NodeLeases] = {}
        self._uncovered: dict[tuple[int, str], float] = {}
        self._reserved: dict[str, dict[str, float]] = {}
        self._lock = threading.Lock()

    @property
    def manager(self) -> NodeManager:
        """The registry of servers."""
        if self._manager is None:
            self._manager = NodeManager()
        return self._manager

    @property
    def grants(self) -> LeaseGrants:
        """What was leased."""
        return self._grants or LeaseGrants(self.manager.store, clock=self._clock)

    @property
    def settings(self) -> FleetSettings:
        """The fleet's settings."""
        return self._settings or FleetSettings(self.manager.store, self.manager.secrets)

    def reports(self) -> dict[str, NodeLeases]:
        """
        Returns:
            Where each server stood at the last look.
        """
        with self._lock:
            return dict(self._reports)

    def reserve(self, node: str, repositories: Sequence[str]) -> None:
        """
        Ask for access to repositories ahead of the applications that will deploy from them.

        An application is made by cloning its repository, so a server cannot be leased access to
        it from the applications it has: the first one does not exist yet. The console asks here
        before it creates one, and the repository stays in the server's lease for
        :data:`RESERVED_FOR` seconds, which is long enough for the application to exist and to
        name it itself.

        Args:
            node: The server.
            repositories: ``owner/repo``.

        Raises:
            ValidationError: When one is not a GitHub repository.
        """
        until = self._clock() + RESERVED_FOR
        named = [_repository(r) for r in repositories]
        with self._lock:
            held = self._reserved.setdefault(node, {})
            held.update(dict.fromkeys(named, until))

    def _reserved_for(self, node: str) -> set[str]:
        """
        Args:
            node: The server.

        Returns:
            The repositories asked for ahead of their application that have not been forgotten.
        """
        now = self._clock()
        with self._lock:
            held = self._reserved.get(node, {})
            for name in [name for name, until in held.items() if until <= now]:
                del held[name]
            return set(held)

    # ---------------------------------------------------------------------------- looking

    def sweep(self, nodes: Sequence[str] | None = None, *, force: bool = False) -> list[NodeLeases]:
        """
        Look at the servers and lease what each needs.

        Args:
            nodes: Only these; every server by default.
            force: Replace the leases that are not due.

        Returns:
            Where each server stands; nothing when this central has no GitHub App.
        """
        app = load_app()
        if app is None:
            return []
        reports = []
        for record in self.manager.list():
            if nodes is not None and record.name not in nodes:
                continue
            report = self._keep(app, record.name, force=force)
            report.checked_at = self._clock()
            with self._lock:
                self._reports[record.name] = report
            reports.append(report)
        return reports

    def _keep(self, app: GitHubApp, node: str, *, force: bool) -> NodeLeases:
        """
        Args:
            app: This central's App.
            node: The server.
            force: Replace the leases that are not due.

        Returns:
            Where the server stands.
        """
        if self.settings.mode(node) == "independent":
            return NodeLeases(node, "independent")
        asker = Asker(actor=ACTOR, scope="deploy")
        seen = gather(
            "apps",
            None,
            asker=asker,
            nodes=[node],
            manager=self.manager,
            node_timeout=NODE_TIMEOUT,
            refresh=True,
        )
        outcome = seen.outcome(node)
        if outcome is None or outcome.status != "ok":
            return NodeLeases(node, "unreachable", error=_unreachable(node, outcome))
        record = self.manager.store.get_node(node)
        if record is None:
            return NodeLeases(node, "unreachable")
        source = NodeSource(self.manager, record, asker, NODE_TIMEOUT)
        host = configured_host()
        plan = make_plan(
            repositories_of(item for item in seen.items if item.get("node") == node)
            | self._reserved_for(node),
            host=host,
            installation_of=installation_for,
        )
        report = NodeLeases(node, "none")
        report.uncovered = plan.uncovered
        report.problems = [
            Problem(
                say("sources.no_installation", repository=r, owner=r.split("/")[0], url=_site(host))
            )
            for r in plan.uncovered
        ]
        failed: LeaseReport | None = None
        for spec in plan.specs:
            lease = self._lease(app, source, spec, force=force)
            if lease is None:
                # Nothing GitHub will give a token for: every repository of it is said below.
                report.uncovered += spec.repositories
                report.problems += [
                    _unreachable_repository(r, spec.host) for r in spec.repositories
                ]
                continue
            report.leases.append(lease)
            if lease.error is None:
                missing = tuple(r for r in spec.repositories if r not in lease.repositories)
                report.uncovered += missing
                report.problems += [_unreachable_repository(r, spec.host) for r in missing]
                continue
            wide = _node_wide(lease.error)
            if wide is not None:
                report.state, report.error = wide, lease.error
                return report
            failed = failed or lease
        if failed is not None:
            report.state, report.error = "failed", failed.error
        elif report.leases:
            report.state = "leased"
        self._let_go(source, node, {s.key for s in plan.specs})
        return report

    def _let_go(self, source: NodeSource, node: str, wanted: set[str]) -> None:
        """
        Drop the leases a server no longer needs, on the server and in the record.

        Args:
            source: The server.
            node: Its name.
            wanted: The keys it needs.
        """
        stale = [g.key for g in self.grants.list(node) if g.key not in wanted]
        for key in stale:
            try:
                decode_answer(
                    node,
                    "DELETE",
                    LEASES_PATH,
                    source.request("DELETE", LEASES_PATH, params={"key": key}),
                )
            except Failure as failure:
                # The lease ends within the hour whatever the server says; the record goes.
                logger.info("%s did not drop the lease %s: %s", node, key, failure.message)
        self.grants.forget(node, stale)

    # ---------------------------------------------------------------------------- leasing

    def _lease(
        self, app: GitHubApp, source: NodeSource, spec: LeaseSpec, *, force: bool
    ) -> LeaseReport | None:
        """
        Make a lease if the server needs one, and hand it over.

        Args:
            app: This central's App.
            source: The server.
            spec: The lease it should hold.
            force: Replace it although it is not due.

        Returns:
            What came of it; None when GitHub will not give a token for any of its repositories.
        """
        now = self._clock()
        grant = self.grants.get(source.name, spec.key)
        wanted = tuple(
            r for r in spec.repositories if self._uncovered.get((spec.installation_id, r), 0) <= now
        )
        if not wanted:
            return None
        if grant is not None and not force and not _due(grant, wanted, now):
            return LeaseReport(
                spec.key,
                spec.installation_id,
                grant.repositories,
                "current" if grant.state == "leased" else "failed",
                grant.expires_at,
                _problem_of(grant),
            )
        sentence = say("sources.github_refused", installation=spec.installation_id)
        try:
            issued, covered = self._issue(app, spec, wanted, now)
        except GitHubAPIError as exc:
            return self._failed(source.name, spec, grant, sentence, exc.output or exc.message)
        except IntegrationError as exc:
            return self._failed(source.name, spec, grant, sentence, exc.message)
        if issued is None:
            return None
        body = {
            "key": spec.key,
            "host": spec.host,
            "installation_id": spec.installation_id,
            "repositories": list(covered),
            "token": issued.token,
            "expires_in": issued.expires_in,
            "permissions": dict(LEASE_PERMISSIONS),
        }
        try:
            decode_answer(
                source.name, "PUT", LEASES_PATH, source.request("PUT", LEASES_PATH, json_body=body)
            )
        except Failure as failure:
            return self._failed(
                source.name,
                spec,
                grant,
                _sentence_of(source.name, failure),
                _scrub(failure.output, issued.token),
                reason=_scrub(failure.message, issued.token),
            )
        stored = self.grants.leased(
            source.name,
            spec.key,
            host=spec.host,
            installation_id=spec.installation_id,
            repositories=covered,
            expires_in=issued.expires_in,
        )
        return LeaseReport(
            spec.key, spec.installation_id, stored.repositories, "issued", stored.expires_at
        )

    def _issue(
        self, app: GitHubApp, spec: LeaseSpec, wanted: Sequence[str], now: float
    ) -> tuple[Any, tuple[str, ...]]:
        """
        Ask GitHub for the token, leaving out the repositories it will not give one for.

        Args:
            app: This central's App.
            spec: The lease.
            wanted: The repositories to ask for.
            now: Epoch seconds.

        Returns:
            The token and the repositories it covers; no token when none can be had.

        Raises:
            GitHubAPIError: GitHub refused for a reason that is not a repository.
            IntegrationError: GitHub answered something that is not a token.
        """
        try:
            return app.scoped_installation_token(
                spec.installation_id, wanted, LEASE_PERMISSIONS
            ), tuple(wanted)
        except GitHubAPIError as exc:
            if exc.status != 422 or len(wanted) < 2 or len(wanted) > ISOLATE_AT_MOST:
                raise
        covered = []
        for repository in wanted:
            try:
                app.scoped_installation_token(spec.installation_id, [repository], LEASE_PERMISSIONS)
            except GitHubAPIError as exc:
                if exc.status != 422:
                    raise
                self._uncovered[(spec.installation_id, repository)] = now + UNCOVERED_FOR
            else:
                covered.append(repository)
        if not covered:
            return None, ()
        return app.scoped_installation_token(
            spec.installation_id, covered, LEASE_PERMISSIONS
        ), tuple(covered)

    def _failed(
        self,
        node: str,
        spec: LeaseSpec,
        grant: Grant | None,
        sentence: ServerText,
        output: str | None,
        *,
        reason: str | None = None,
    ) -> LeaseReport:
        """
        Record that a lease could not be made or handed over.

        Args:
            node: The server.
            spec: The lease.
            grant: What was recorded before, if anything.
            sentence: Why.
            output: The other side's words.
            reason: The server's own sentence, when it said one.

        Returns:
            The lease, failed.
        """
        problem = Problem(sentence, output or reason)
        stored = self.grants.failed(
            node,
            spec.key,
            host=spec.host,
            installation_id=spec.installation_id,
            repositories=spec.repositories,
            error=problem.to_dict(),
        )
        return LeaseReport(
            spec.key,
            spec.installation_id,
            stored.repositories,
            "failed",
            grant.expires_at if grant else None,
            problem,
        )


# -------------------------------------------------------------------------------- helpers


def _due(grant: Grant, wanted: Sequence[str], now: float) -> bool:
    """
    Args:
        grant: What was leased.
        wanted: The repositories to cover now.
        now: Epoch seconds.

    Returns:
        Whether the lease should be made again now.
    """
    if grant.state == "failed":
        return now - grant.updated_at >= RETRY_AFTER or set(wanted) != set(grant.repositories)
    ends = grant.expires_at or 0.0
    return set(grant.repositories) != set(wanted) or ends - now <= RENEW_BEFORE


def _problem_of(grant: Grant) -> Problem | None:
    """
    Args:
        grant: What was leased.

    Returns:
        Why its last try failed, as it was recorded.
    """
    if not grant.error:
        return None
    coded = grant.error.get("coded") or {}
    try:
        sentence = say(str(coded.get("code")), **dict(coded.get("params") or {}))
    except (KeyError, ValueError, TypeError):
        return None
    output = grant.error.get("output")
    return Problem(sentence, output if isinstance(output, str) else None)


def _node_wide(problem: Problem | None) -> str | None:
    """
    Args:
        problem: Why a lease failed.

    Returns:
        The state of the whole server when the reason is about the server and not the lease.
    """
    if problem is None:
        return None
    return {
        "sources.node_unsupported": "unsupported",
        "sources.node_ceiling": "ceiling",
        "sources.node_unreachable": "unreachable",
    }.get(problem.sentence.code)


def _unreachable(node: str, outcome: Any) -> Problem:
    """
    Args:
        node: The server.
        outcome: How it answered the look at its applications.

    Returns:
        The problem.
    """
    return Problem(
        say("sources.node_unreachable", node=node, command=f"noust node test {node}"),
        getattr(outcome, "error_verbatim", None),
    )


def _sentence_of(node: str, failure: Failure) -> ServerText:
    """
    Args:
        node: The server.
        failure: What it answered the lease with.

    Returns:
        The sentence for it.
    """
    if failure.kind == "unsupported":
        return say("sources.node_unsupported", node=node)
    if failure.kind == "forbidden":
        return say("sources.node_ceiling", node=node, command="noust fleet access --level deploy")
    if failure.kind in ("unreachable", "refused"):
        return say("sources.node_unreachable", node=node, command=f"noust node test {node}")
    return say("sources.node_refused", node=node, reason=failure.message)


def _unreachable_repository(repository: str, host: str) -> Problem:
    """
    Args:
        repository: ``owner/repo`` GitHub would not give a token for.
        host: The GitHub host.

    Returns:
        The problem: the App's installation does not include it.
    """
    return Problem(say("sources.not_reachable", repository=repository, url=_site(host)))


def _site(host: str) -> str:
    """
    Args:
        host: The GitHub host.

    Returns:
        Its address, where the App's installations are managed.
    """
    return f"https://{host}/settings/installations"


def start_keeper(keeper: LeaseKeeper | None = None) -> threading.Thread:
    """
    Start looking at the servers every minute, in the background.

    Args:
        keeper: The keeper; a default one when None.

    Returns:
        The thread, a daemon: it ends with the process.
    """
    chosen = keeper or LeaseKeeper()

    def loop() -> None:
        while True:
            time.sleep(LOOK_EVERY)
            try:
                chosen.sweep()
            # The boundary of a background thread: what a look can fail with (the store, the
            # registry of servers, a sealed central) must not end it, and is logged.
            except (NoustError, OSError, sqlite3.Error, RuntimeError):
                logger.exception("The keeper of GitHub leases failed")

    thread = threading.Thread(target=loop, name="noust-fleet-leases", daemon=True)
    thread.start()
    return thread
