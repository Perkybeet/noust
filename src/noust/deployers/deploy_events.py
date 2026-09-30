# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What happened to a deployment, told to whoever needs to know.

The deployment recorder is the one place every deployment passes through -
a deploy, an update from the CLI, the console or a webhook, a rollback - so
it is the one place that announces them: when a deployment starts, and how
it ended. Notifications and GitHub's deployment statuses listen here instead
of each guessing from job outcomes, which only ever saw the deployments the
console had started.

Subscribers run in the process that deploys, synchronously and in order. A
subscriber that talks to the network must hand the work to a thread of its
own: a deployment never waits on Slack. A subscriber that raises is logged
and skipped; it cannot fail the deployment or starve the next subscriber.
"""

from __future__ import annotations

import importlib
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class DeployEventKind(str, Enum):
    """How far a deployment got."""

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Failed, and what served before was put back and answers: the
    # application is up on the previous version.
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class DeployEvent:
    """
    One moment in a deployment's life.

    Attributes:
        kind: Started, succeeded, failed or rolled back.
        domain: The application's primary domain.
        deployment_id: The history row, when one was recorded.
        trigger: What started it (``manual``, ``webhook``, ``rollback``...).
        commit: The commit deployed, when known.
        branch: The branch deployed, when known.
        error: The failure as recorded, secrets scrubbed; None unless failed
            or rolled back.
        job_id: The console job running it, when there is one.
        ts: When it happened, UTC.
        operation: What kind of deployment it is: ``deploy``, ``update``,
            ``rollback`` (going back to an earlier release), ``activate``
            (switching to a chosen one) or ``migrate``. Notifications name the
            event for what it is; the ``kind`` above says only how far it got.
        duration_s: How long it took, in seconds; None while it runs.
        commit_message: The subject of the commit deployed, when known.
        release_id: The release built, on the releases layout.
        error_message: The failing step's own one-line message, scrubbed;
            ``error`` is this and the rest in one string.
        error_output: The rest of the failure - the tool's own output or the
            health gate's evidence - scrubbed, apart from the message so a
            notification can show one without the other.
    """

    kind: DeployEventKind
    domain: str
    deployment_id: int | None = None
    trigger: str | None = None
    commit: str | None = None
    branch: str | None = None
    error: str | None = None
    job_id: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    operation: str = "deploy"
    duration_s: float | None = None
    commit_message: str | None = None
    release_id: str | None = None
    error_message: str | None = None
    error_output: str | None = None


Subscriber = Callable[[DeployEvent], None]

# What kind of deployment the code running now is part of. An update is many
# frames above the recorder that will announce it, and every deployer builds
# its recorder in one shared place; setting this once, where the update
# starts, is what lets that recorder say "update" without every deployer
# passing a word down. A context variable, so two updates in two threads never
# see each other's.
_current_operation: ContextVar[str] = ContextVar("noust_deploy_operation", default="deploy")


@contextmanager
def operation(name: str) -> Iterator[None]:
    """
    Say what the deployments recorded inside the block are.

    Args:
        name: ``update`` or ``rollback``; ``deploy`` is what nothing says.
            :attr:`DeployEvent.operation` documents them all.

    Yields:
        Nothing; recorders built inside the block announce ``name``.
    """
    token = _current_operation.set(name)
    try:
        yield
    finally:
        _current_operation.reset(token)


def current_operation() -> str:
    """
    Returns:
        What the deployment being recorded here is: the innermost
        :func:`operation`, or ``deploy``.
    """
    return _current_operation.get()


# Modules whose ``on_deploy_event`` function listens to every deployment in
# every process. Imported on first publication rather than at import time:
# the notifier and the GitHub integration import half of Noust, and a
# deployer importing them back would be a cycle.
DEFAULT_SUBSCRIBERS: tuple[str, ...] = (
    "noust.core.deploy_notifications",
    "noust.integrations.github.statuses",
)

_lock = threading.Lock()
_subscribers: list[Subscriber] = []
_default_handlers: list[Subscriber] = []
_defaults_loaded = False
_suspended = False


def subscribe(subscriber: Subscriber) -> Callable[[], None]:
    """
    Listen to every deployment event this process publishes.

    Args:
        subscriber: Called with each event, in the deploying thread.

    Returns:
        A function that stops the subscription.
    """
    with _lock:
        _subscribers.append(subscriber)

    def unsubscribe() -> None:
        with _lock:
            if subscriber in _subscribers:
                _subscribers.remove(subscriber)

    return unsubscribe


def _load_defaults() -> list[Subscriber]:
    """
    Import the default subscribers, once per process.

    Returns:
        Each default module's ``on_deploy_event``. A module that is not
        there, which a partial install or a test may produce, is skipped.
    """
    global _defaults_loaded
    loaded: list[Subscriber] = []
    for name in DEFAULT_SUBSCRIBERS:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            # Only the module itself missing is tolerated; a module that is
            # there and fails its own imports is a bug and must be seen.
            if exc.name != name:
                raise
            continue
        handler = getattr(module, "on_deploy_event", None)
        if callable(handler):
            loaded.append(handler)
    _defaults_loaded = True
    return loaded


def publish(event: DeployEvent) -> None:
    """
    Tell every subscriber, the defaults first.

    This is an error boundary: a subscriber's failure is logged with its
    traceback and the next subscriber still runs.

    Args:
        event: What happened.
    """
    global _default_handlers
    with _lock:
        if not _defaults_loaded and not _suspended:
            _default_handlers = _load_defaults()
        handlers = ([] if _suspended else list(_default_handlers)) + list(_subscribers)
    for handler in handlers:
        try:
            handler(event)
        except Exception:
            logger.exception(
                "Deployment event subscriber %r failed on %s for %s",
                handler,
                event.kind.value,
                event.domain,
            )


def suspend_defaults(suspended: bool = True) -> None:
    """
    Stop, or resume, calling the default subscribers.

    For tests and for rehearsals, which must not notify anyone about a
    deployment that did not happen. Explicit subscribers still run.

    Args:
        suspended: True to stop calling them, False to resume.
    """
    global _suspended
    with _lock:
        _suspended = suspended


def reset() -> None:
    """Forget every subscriber and reload the defaults on the next event (tests)."""
    global _defaults_loaded, _default_handlers
    with _lock:
        _subscribers.clear()
        _default_handlers = []
        _defaults_loaded = False
