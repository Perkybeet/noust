# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cancelling a deployment while it is being built.

A deployment on the release layout builds in a directory of its own and moves
nothing that serves until it activates, which is what makes stopping it safe:
the build is killed (its transient systemd unit is stopped by the runner), the
half-built release is removed and the deployment's row says it was cancelled.
Nothing that served before was touched.

The rules, and why they are these:

- **There is a point of no return.** Up to the moment a release starts to be
  activated, a cancellation is honoured: at once inside the commands of the
  fetch, the install and the build, and at the next checkpoint between steps.
  From :func:`commit` on it is refused (``too_late``): activation switches the
  site and the unit behind the health gate and puts back what served before when
  it fails, and killing it halfway would be the one way to leave neither.
- **It is addressed to an operation, not to an application.** The request
  names the pid and start time the operation recorded in the application's lock
  (:class:`noust.core.applock.LockHolder`), so one that outlives its operation
  can never cancel the next deployment, and the process that asks does not have
  to be the one that deploys: ``noust deploy cancel`` in a terminal stops a
  deployment the console started, and the reverse.
- **The operation answers.** Whoever asked learns what came of it
  (:mod:`noust.core.delivery_store` ``outcome``): cancelled, too late, already
  finished, or not something that can be interrupted (a build in place runs in
  the live tree).
- **A stack, a container and a monorepo on releases can be stopped too.** The build of a
  Compose stack or of a container is a ``docker`` command whose client is killed, and the
  daemon stops the build with it; what serves (the containers) is replaced only once the
  build is done. What the update changed on the way - the tree it pulled in place, the compose
  file of a container - is put back exactly as after a build that failed. A monorepo on releases
  builds in a release of its own like any other.
- **Only the build is interruptible.** :func:`interruptible` puts a region
  under the runner's cancel scope, in which a command that has not started never
  starts and one that runs is killed. The undo of a failed deployment runs
  outside every region, because the same scope would refuse to start the
  commands that put the machine back.

Everything here is a no-op outside :func:`operation_window`, so a deployer that
is used on its own (a test, a rehearsal) behaves exactly as it did.
"""

from __future__ import annotations

import contextvars
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from noust.core import applock, delivery_store
from noust.core.applock import LockHolder
from noust.core.audit import Actor
from noust.core.audit import record as record_audit
from noust.core.exceptions import CancelRefused, DeploymentCancelled, NoustError
from noust.core.fs import is_rehearsal
from noust.core.runner import CommandCancelled, cancellable
from noust.core.store import AppLayout, AppType, NoustStore, get_store

logger = logging.getLogger(__name__)

#: The operations a request can be addressed to: the ones that build a release.
CANCELLABLE_OPERATIONS = frozenset({"deploy", "update"})

#: How often a running operation looks for a request another process made.
POLL_INTERVAL = 0.5

#: Types that run as Docker containers: their images are built by the daemon, and the
#: containers that serve are replaced only once the build is done, so a build can be stopped
#: while the application is not on the release layout.
_DOCKER_TYPES = frozenset({AppType.DOCKER_COMPOSE.value, AppType.CONTAINER.value})


class CancelToken:
    """
    The cancellation state of one operation on one application.

    Attributes:
        domain: The application.
        operation: ``deploy`` or ``update``.
        holder: What the operation recorded in the application's lock.
        supported: Whether this application's build can be interrupted.
        unsupported_reason: Why not, when it cannot.
        event: Set when a cancellation is honoured; the runner's cancel scope
            watches it.
        committed: The point of no return passed.
        tripped: A cancellation was honoured (the build stopped).
        requested_by: Who asked, once somebody did.
        phase: What the operation is doing, for what is shown to the operator.
    """

    def __init__(
        self,
        domain: str,
        operation: str,
        holder: LockHolder,
        *,
        supported: bool,
        unsupported_reason: str = "",
    ) -> None:
        """
        Args:
            domain: The application.
            operation: ``deploy`` or ``update``.
            holder: What the operation recorded in the lock.
            supported: Whether the build can be interrupted.
            unsupported_reason: Why it cannot, for the refusal.
        """
        self.domain = domain
        self.operation = operation
        self.holder = holder
        self.supported = supported
        self.unsupported_reason = unsupported_reason
        self.event = threading.Event()
        self.committed = False
        self.tripped = False
        self.requested_by: str | None = None
        self.phase = "starting"
        self._lock = threading.Lock()

    @property
    def requested(self) -> bool:
        """Whether a cancellation was asked for and is waiting for a safe moment."""
        return self.event.is_set()

    def request(self, by: str | None) -> bool:
        """
        Ask for the cancellation, if it can still be honoured.

        Args:
            by: Who asks.

        Returns:
            True when it was accepted; False after the point of no return or
            for a build that cannot be interrupted.
        """
        with self._lock:
            if self.committed or not self.supported:
                return False
            if not self.event.is_set():
                self.requested_by = by
                self.event.set()
            return True

    def commit(self, phase: str) -> None:
        """
        Pass the point of no return: from here a request is refused.

        Also the last checkpoint, in the same step: a request that arrives
        between a checkpoint and this call would otherwise be accepted and
        then never honoured.

        Args:
            phase: What starts, as the operator reads it.

        Raises:
            DeploymentCancelled: A cancellation was asked for before this.
        """
        with self._lock:
            if self.event.is_set() and not self.committed:
                self.tripped = True
                raise self.cancelled()
            self.committed = True
            self.phase = phase

    def checkpoint(self) -> None:
        """
        Stop here if a cancellation was asked for.

        Raises:
            DeploymentCancelled: One was, and the point of no return has not
                passed.
        """
        with self._lock:
            if not self.event.is_set() or self.committed:
                return
            self.tripped = True
        raise self.cancelled()

    def cancelled(self) -> DeploymentCancelled:
        """
        Build the error that ends a cancelled operation.

        Returns:
            The error, naming who cancelled it and what was left alone.
        """
        who = f" by {self.requested_by}" if self.requested_by else ""
        return DeploymentCancelled(
            f"The {self.operation} of {self.domain} was cancelled{who}",
            "Nothing that was serving was touched; the release that was being built was removed.",
            cancelled_by=self.requested_by,
        )


_current: contextvars.ContextVar[CancelToken | None] = contextvars.ContextVar(
    "noust_cancel_token", default=None
)

_active_lock = threading.Lock()
_active: dict[str, CancelToken] = {}


def current() -> CancelToken | None:
    """
    Returns:
        The token of the operation this thread is running, if it is in one.
    """
    return _current.get()


def token_of(domain: str) -> CancelToken | None:
    """
    Find the token of the operation running on an application in this process.

    Args:
        domain: The application.

    Returns:
        The token, or None when nothing that can be cancelled runs on it here.
    """
    with _active_lock:
        return _active.get(domain.strip().lower())


def supports_cancel(
    domain: str, store: NoustStore | None = None, app_type: str | None = None
) -> tuple[bool, str]:
    """
    Tell whether an application's build can be interrupted safely.

    A new application builds where nothing serves yet, an application on
    releases builds beside what serves, and a Compose stack or a container
    builds images the containers that serve do not use until the build is
    done. One built in place is rebuilt in the tree that serves, so stopping it
    halfway would leave the application broken.

    Args:
        domain: The application.
        store: The store; defaults to the process-wide one.
        app_type: The type a new application is being deployed as, when it is
            known before its row exists. Any type can be stopped while it is
            new, so it changes no answer; the argument stays for the callers
            that have it.

    Returns:
        Whether it can, and why not when it cannot.
    """
    app = (store or get_store()).get_app(domain)
    if app is None or app.app_type in _DOCKER_TYPES:
        return True, ""
    if app.layout != AppLayout.RELEASES.value:
        return False, (
            "this application builds in place, in the tree that serves: stopping it halfway "
            "would leave the live application broken"
        )
    return True, ""


def restrict(reason: str) -> None:
    """
    Say that what runs turned out not to be interruptible.

    For a deployer that is chosen after the window opened (the automatic one
    detects the type from the source it fetched) and turns out to be one whose
    build cannot be stopped, a request must not be accepted for it.

    Args:
        reason: Why, as the operator reads it.
    """
    token = _current.get()
    if token is None:
        return
    with token._lock:
        if not token.tripped:
            token.supported = False
            token.unsupported_reason = reason


@contextmanager
def operation_window(
    domain: str,
    operation: str,
    *,
    store: NoustStore | None = None,
) -> Iterator[CancelToken | None]:
    """
    Make the operation holding an application's lock cancellable.

    Entered once, by the outermost of the operations that build (an update
    that hands over to a deployer finds the window already open). The caller
    holds :func:`noust.core.applock.app_lock`: the window is addressed by the
    record that lock wrote.

    A request that was made before the window opened (the operation was
    cancelled the moment it started) is honoured at once: the window raises
    :class:`DeploymentCancelled` before anything ran.

    Args:
        domain: The application.
        operation: ``deploy`` or ``update``.
        store: The store; defaults to the process-wide one.

    Yields:
        The token, or None when there is nothing to address (a rehearsal, or
        the lock is not held by this thread).

    Raises:
        DeploymentCancelled: The operation was cancelled before it began.
    """
    key = domain.strip().lower()
    existing = _current.get()
    if existing is not None and existing.domain == key:
        yield existing
        return
    holder = applock.held_record(domain)
    if holder is None or is_rehearsal():
        yield None
        return

    target = store or get_store()
    supported, why = supports_cancel(key, target)
    token = CancelToken(key, operation, holder, supported=supported, unsupported_reason=why)
    stop = threading.Event()
    watcher = threading.Thread(
        target=_watch, args=(token, target, stop), name=f"noust-cancel-{key}", daemon=True
    )
    with _active_lock:
        _active[key] = token
    marker = _current.set(token)
    try:
        _adopt_pending(token, target)
        watcher.start()
        # A request made before this point has set the event: stop before any work.
        token.checkpoint()
        yield token
    finally:
        stop.set()
        _current.reset(marker)
        with _active_lock:
            if _active.get(key) is token:
                del _active[key]
        if watcher.is_alive():
            watcher.join(timeout=2 * POLL_INTERVAL)
        _decide(token, target)


def _adopt_pending(token: CancelToken, store: NoustStore) -> None:
    """
    Pick up what was recorded for this application before the window opened.

    Args:
        token: The operation's token.
        store: The store.
    """
    try:
        delivery_store.drop_stale(
            token.domain, token.holder.pid, token.holder.started_at, store=store
        )
        pending = delivery_store.request_for(
            token.domain, token.holder.pid, token.holder.started_at, store=store
        )
    except (NoustError, OSError, sqlite3.Error) as exc:
        logger.warning("Could not read a cancel request for %s: %s", token.domain, exc)
        return
    if pending is not None and pending.outcome is None:
        _accept(token, pending, store)


def _accept(token: CancelToken, request: delivery_store.CancelRequest, store: NoustStore) -> None:
    """
    Honour a request another process made, or answer it at once if it cannot be.

    A request that can no longer be honoured (the release is being activated)
    or never could be (an in-place build) is answered the moment it is seen, so
    whoever asked does not wait for the operation to end to be told.

    Args:
        token: The operation's token.
        request: The request, not yet answered.
        store: The store.
    """
    if token.request(request.requested_by):
        return
    outcome = delivery_store.TOO_LATE if token.committed else delivery_store.UNSUPPORTED
    try:
        delivery_store.decide(
            token.domain, token.holder.pid, token.holder.started_at, outcome, store=store
        )
    except (NoustError, OSError, sqlite3.Error) as exc:
        logger.warning("Could not answer a cancel request for %s: %s", token.domain, exc)


def _watch(token: CancelToken, store: NoustStore, stop: threading.Event) -> None:
    """
    Look for a request another process made, until the operation ends.

    Args:
        token: The operation's token.
        store: The store.
        stop: Set when the operation ends.
    """
    while not stop.wait(POLL_INTERVAL):
        if token.requested:
            continue
        try:
            request = delivery_store.request_for(
                token.domain, token.holder.pid, token.holder.started_at, store=store
            )
        except (NoustError, OSError, sqlite3.Error) as exc:
            # A store that cannot be read costs the cancel, never the deployment.
            logger.debug("Could not look for a cancel request for %s: %s", token.domain, exc)
            continue
        if request is not None and request.outcome is None:
            _accept(token, request, store)


def _decide(token: CancelToken, store: NoustStore) -> None:
    """
    Tell whoever asked what became of the request.

    Args:
        token: The operation's token, as it ended.
        store: The store.
    """
    if token.tripped:
        outcome = delivery_store.CANCELLED
    elif token.committed:
        outcome = delivery_store.TOO_LATE
    elif not token.supported:
        outcome = delivery_store.UNSUPPORTED
    else:
        outcome = delivery_store.FINISHED
    try:
        delivery_store.decide(
            token.domain, token.holder.pid, token.holder.started_at, outcome, store=store
        )
    except (NoustError, OSError, sqlite3.Error) as exc:
        logger.warning(
            "Could not record how the cancel request for %s ended: %s", token.domain, exc
        )


# ---------------------------------------------------------------------------
# What a deployer calls
# ---------------------------------------------------------------------------


def checkpoint() -> None:
    """
    Stop here if the running operation was cancelled.

    A no-op outside :func:`operation_window`, after :func:`commit`, and for a
    build that cannot be interrupted.

    Raises:
        DeploymentCancelled: It was.
    """
    token = _current.get()
    if token is not None:
        token.checkpoint()


def commit(phase: str) -> None:
    """
    Pass the point of no return: the release is about to be activated.

    Args:
        phase: What starts, for the operator.

    Raises:
        DeploymentCancelled: A cancellation was asked for before this point.
    """
    token = _current.get()
    if token is not None:
        token.commit(phase)


@contextmanager
def interruptible(phase: str) -> Iterator[None]:
    """
    Run a region whose commands a cancellation kills.

    Inside it a command that has not started never starts, and one that runs
    is killed with the processes it started; its transient unit is stopped by
    the runner. The :class:`~noust.core.runner.CommandCancelled` that raises
    is turned into :class:`DeploymentCancelled` on the way out. Outside an
    operation window, and for a build that cannot be interrupted, the region
    is transparent.

    Args:
        phase: What the region does, for the operator.

    Yields:
        Nothing.

    Raises:
        DeploymentCancelled: The operation was cancelled before or inside it.
    """
    token = _current.get()
    if token is None or not token.supported or token.committed:
        yield
        return
    token.checkpoint()
    previous, token.phase = token.phase, phase
    try:
        with cancellable(token.event):
            yield
    except CommandCancelled as exc:
        with token._lock:
            token.tripped = True
        raise token.cancelled() from exc
    finally:
        if not token.committed:
            token.phase = previous


def is_cancellation(error: BaseException | str | None) -> bool:
    """
    Tell whether a failure is an operator's cancellation.

    Looks through ``raise ... from`` chains, because a deployer that wraps what
    its build raised must not turn a cancellation into a failure, and asks the
    running operation too: if its build was stopped by a cancellation, the
    failure that follows is the cancellation's.

    Args:
        error: What a deployment ended with.

    Returns:
        True for a cancellation.
    """
    if error is None or isinstance(error, str):
        return False
    seen: set[int] = set()
    cause: BaseException | None = error
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, DeploymentCancelled | CommandCancelled):
            return True
        seen.add(id(cause))
        cause = cause.__cause__
    token = _current.get()
    return token is not None and token.tripped


def cancelled_by(error: BaseException | None) -> str | None:
    """
    Name who cancelled, as far as a failure or the running operation says.

    Args:
        error: What a deployment ended with.

    Returns:
        Who asked, or None when it is not known.
    """
    cause: BaseException | None = error
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        if isinstance(cause, DeploymentCancelled) and cause.cancelled_by:
            return cause.cancelled_by
        seen.add(id(cause))
        cause = cause.__cause__
    token = _current.get()
    return token.requested_by if token is not None else None


# ---------------------------------------------------------------------------
# What the CLI and the API call
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CancelResult:
    """
    What asking to cancel did.

    Attributes:
        domain: The application.
        operation: What runs on it (``update``, ``deploy``).
        pid: The process running it.
        started_at: When it took the application's lock.
        immediate: The operation runs in this process and was told at once.
        phase: What it was doing when it was told, when this process knows.
    """

    domain: str
    operation: str
    pid: int
    started_at: str
    immediate: bool = False
    phase: str | None = None


def request_cancel(
    domain: str,
    *,
    by: str | None,
    store: NoustStore | None = None,
    app_type: str | None = None,
) -> CancelResult:
    """
    Ask the operation running on an application to stop, if it can.

    Recorded in the store, addressed to the operation that holds the
    application's lock; an operation of this process is told at once, one of
    another process finds the request within :data:`POLL_INTERVAL`. The
    deployment's own row, the release and the lock are cleaned up by that
    operation as it stops (rule 3: there is one way a deployment ends), not here.

    Args:
        domain: The application's domain.
        by: Who asks, as the audit trail names them.
        store: The store; defaults to the process-wide one.
        app_type: The type a new application is being deployed as, when the
            caller knows it before the application has a row.

    Returns:
        What the request is addressed to.

    Raises:
        CancelRefused: Nothing is running, what runs cannot be cancelled (a
            rollback, a deletion, an in-place build), or its release is
            already being activated.
    """
    target = store or get_store()
    key = domain.strip().lower()
    holder = applock.holder_of(key)
    if holder is None:
        raise CancelRefused(
            f"Nothing is running on {key}",
            f"There is no deployment to cancel. 'noust deploy queue {key}' shows what is waiting.",
            reason="idle",
        )
    if holder.operation not in CANCELLABLE_OPERATIONS:
        raise CancelRefused(
            f"The {holder.operation} running on {key} cannot be cancelled",
            "Only a deploy or an update that is building can be stopped: the others change "
            "what serves, and stopping them halfway would leave it half changed. Wait for it "
            "to finish.",
            reason="unsupported",
        )

    token = token_of(key)
    local = token is not None and (token.holder.pid, token.holder.started_at) == (
        holder.pid,
        holder.started_at,
    )
    supported, why = (
        (token.supported, token.unsupported_reason)
        if local and token is not None
        else supports_cancel(key, target, app_type)
    )
    if not supported:
        raise CancelRefused(
            f"The {holder.operation} of {key} cannot be cancelled",
            f"{why[:1].upper()}{why[1:]}.",
            reason="unsupported",
        )
    if local and token is not None and token.committed:
        raise _too_late(key, token.phase)

    delivery_store.put_request(
        key,
        holder_pid=holder.pid,
        holder_started_at=holder.started_at,
        requested_by=by,
        store=target,
    )
    phase: str | None = None
    if local and token is not None:
        if not token.request(by):
            # Passed the point of no return between the look and the request.
            raise _too_late(key, token.phase)
        phase = token.phase
    record_audit(
        "apps.deploy.cancel",
        actor=Actor.from_label(by) if by else None,
        target=f"app:{key}",
        details={"operation": holder.operation, "pid": holder.pid, "phase": phase},
    )
    return CancelResult(
        domain=key,
        operation=holder.operation,
        pid=holder.pid,
        started_at=holder.started_at,
        immediate=local,
        phase=phase,
    )


def _too_late(domain: str, phase: str) -> CancelRefused:
    """
    Build the refusal for a release that is already being activated.

    Args:
        domain: The application.
        phase: What the operation is doing.

    Returns:
        The refusal.
    """
    return CancelRefused(
        f"The release of {domain} is already being activated",
        f"It is {phase} behind the health gate: it finishes, or puts back what served before "
        "by itself. Roll back to the previous release once it is done if it is not what you want.",
        reason="too_late",
    )


def wait_for_outcome(
    domain: str,
    result: CancelResult,
    *,
    timeout: float,
    store: NoustStore | None = None,
    sleep: float = 0.25,
) -> str | None:
    """
    Wait for the operation a request was addressed to to answer it.

    Args:
        domain: The application.
        result: What :func:`request_cancel` returned.
        timeout: How long to wait, in seconds.
        store: The store; defaults to the process-wide one.
        sleep: Seconds between looks.

    Returns:
        One of :data:`noust.core.delivery_store.OUTCOMES`, or None when the
        operation had not answered in time and is still running.
    """
    target = store or get_store()
    deadline = time.monotonic() + timeout
    while True:
        answer = delivery_store.request_for(domain, result.pid, result.started_at, store=target)
        if answer is not None and answer.outcome is not None:
            return answer.outcome
        holder = applock.holder_of(domain)
        gone = holder is None or (holder.pid, holder.started_at) != (result.pid, result.started_at)
        if gone:
            # It ended without looking at the request, or answered and was
            # cleaned up in the meantime.
            answer = delivery_store.request_for(domain, result.pid, result.started_at, store=target)
            return answer.outcome if answer and answer.outcome else delivery_store.FINISHED
        if time.monotonic() >= deadline:
            return None
        time.sleep(sleep)
