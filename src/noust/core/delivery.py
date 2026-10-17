# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How this server delivers: how many builds at once, how many may wait, how long
one may take, how new applications update.

The settings live under ``deploy`` in the configuration, and this is the
one place that reads them, so the job queue, the deployers and ``noust health``
cannot come to different answers about what "the limit" is. A long-running
console reads them again every few seconds rather than once: an operator who
changes the limit with ``noust config set`` expects the next build to obey it,
not the next restart.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from noust.core.config import Config
from noust.core.exceptions import NoustError

logger = logging.getLogger(__name__)

#: How many builds run at the same time unless the server says otherwise.
DEFAULT_MAX_CONCURRENT_BUILDS = 2

#: How many deployments and updates may wait before the next is refused.
DEFAULT_QUEUE_LIMIT = 25

#: How long the install of the dependencies and the build may each take unless the server
#: or the application says otherwise. A build that is still going after this is stopped, and
#: says whether it was the time or the memory.
DEFAULT_INSTALL_TIMEOUT = 1800
DEFAULT_BUILD_TIMEOUT = 2700
#: How many deployments of an application's history are kept unless the application
#: says otherwise: the same number the history has always been rotated to.
DEFAULT_HISTORY_KEEP = 20

#: The longest a deployment may build, in minutes, unless the server says
#: otherwise. A build past an hour is a hung one in nearly every case, and it
#: holds one of the few build slots for as long as it hangs. 0 means no limit.
DEFAULT_MAX_MINUTES = 60

#: The most minutes a limit may be: a day.
MAX_MINUTES = 1440

#: Seconds in a minute of the limit. A name, not a literal, so the tests of the
#: job queue can make a minute last a tenth of a second.
SECONDS_PER_MINUTE = 60.0

#: Who a cancellation says asked for it when the server's own time limit did.
#: A deployment that ends on it failed: nobody decided to stop it.
TIME_LIMIT_ACTOR = "the maximum deployment time"

#: Seconds the old instance keeps running after traffic moved, for a new
#: application. The same number :data:`noust.core.store.DEFAULT_DRAIN_SECONDS` is.
DEFAULT_DRAIN_SECONDS = 10

#: Seconds a reading of the configuration is reused by :func:`current`.
REFRESH_SECONDS = 5.0


@dataclass(frozen=True)
class DeliverySettings:
    """
    What the server's configuration says about delivering.

    Attributes:
        zero_downtime: A new application that can run twice starts in
            zero-downtime mode.
        drain_seconds: Seconds the old instance keeps running after a switch.
        max_concurrent_builds: Builds that run at the same time.
        queue_limit: Deployments and updates that may wait.
        install_timeout: Seconds the install of an application's dependencies may take.
        build_timeout: Seconds its build may take.
        history_keep: Deployments of an application's history that are kept.
        max_minutes: The longest a deployment may build; 0 is no limit.
    """

    zero_downtime: bool = True
    drain_seconds: int = DEFAULT_DRAIN_SECONDS
    max_concurrent_builds: int = DEFAULT_MAX_CONCURRENT_BUILDS
    queue_limit: int = DEFAULT_QUEUE_LIMIT
    install_timeout: int = DEFAULT_INSTALL_TIMEOUT
    build_timeout: int = DEFAULT_BUILD_TIMEOUT
    history_keep: int = DEFAULT_HISTORY_KEEP
    max_minutes: int = DEFAULT_MAX_MINUTES

    @property
    def max_seconds(self) -> float | None:
        """Returns: The longest a deployment may build, in seconds; None for no limit."""
        return self.max_minutes * SECONDS_PER_MINUTE if self.max_minutes else None

    @classmethod
    def read(cls, config: Config) -> DeliverySettings:
        """
        Read the settings from a configuration.

        A value that does not hold (a hand-edited file) is read as the default:
        the key's own validator is what refuses it when it is written, and a
        deployment must not stop for a typo in a setting about queueing.

        Args:
            config: The configuration.

        Returns:
            The settings.
        """
        return cls(
            zero_downtime=_flag(config.get("deploy.zero_downtime", True), True),
            drain_seconds=_whole(config.get("deploy.drain_seconds"), DEFAULT_DRAIN_SECONDS, 0, 300),
            max_concurrent_builds=_whole(
                config.get("deploy.max_concurrent_builds"), DEFAULT_MAX_CONCURRENT_BUILDS, 1, 16
            ),
            queue_limit=_whole(config.get("deploy.queue_limit"), DEFAULT_QUEUE_LIMIT, 1, 1000),
            install_timeout=_whole(
                config.get("deploy.install_timeout"), DEFAULT_INSTALL_TIMEOUT, 60, 86400
            ),
            build_timeout=_whole(
                config.get("deploy.build_timeout"), DEFAULT_BUILD_TIMEOUT, 60, 86400
            ),
            history_keep=_whole(config.get("deploy.history_keep"), DEFAULT_HISTORY_KEEP, 1, 1000),
            max_minutes=_whole(
                config.get("deploy.max_minutes"), DEFAULT_MAX_MINUTES, 0, MAX_MINUTES
            ),
        )


def _flag(value: object, default: bool) -> bool:
    """
    Read a yes/no setting.

    Args:
        value: What the configuration holds.
        default: What a value that is not a yes or a no means.

    Returns:
        The flag.
    """
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    return default


def _whole(value: object, default: int, low: int, high: int) -> int:
    """
    Read a whole number in a range.

    Args:
        value: What the configuration holds.
        default: What a value out of range or not a number means.
        low: Smallest accepted.
        high: Largest accepted.

    Returns:
        The number, or the default.
    """
    if isinstance(value, bool):
        return default
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def minutes_words(count: int) -> str:
    """
    Spell a number of minutes.

    Args:
        count: How many.

    Returns:
        ``1 minute`` or ``15 minutes``.
    """
    return f"{count} minute" if count == 1 else f"{count} minutes"


def stopped_by_time_limit(operation: str, domain: object, minutes: int) -> str:
    """
    Say that a deployment was stopped because it ran past the server's limit.

    One sentence for the job, the deployment's history row and the CLI, so they
    cannot tell the operator three different things about the same build.

    Args:
        operation: ``deploy`` or ``update``.
        domain: The application.
        minutes: The limit that applied.

    Returns:
        The sentence, with how to raise the limit.
    """
    return (
        f"The {operation} of {domain} was stopped: it ran longer than this server's maximum "
        f"deployment time ({minutes_words(minutes)}). Nothing that was serving was touched. "
        "Raise the limit with 'noust config set deploy.max_minutes <minutes>' (0 is no limit)."
    )


_lock = threading.Lock()
_cached: tuple[float, DeliverySettings] | None = None


def current(*, refresh: bool = False) -> DeliverySettings:
    """
    Return the settings as the configuration file says now, read at most every
    :data:`REFRESH_SECONDS`.

    Args:
        refresh: Read the file again even if a reading is recent.

    Returns:
        The settings; the defaults when the file cannot be read.
    """
    global _cached
    now = time.monotonic()
    with _lock:
        if not refresh and _cached is not None and now - _cached[0] < REFRESH_SECONDS:
            return _cached[1]
    try:
        settings = DeliverySettings.read(Config.snapshot())
    except (OSError, NoustError) as exc:
        logger.warning("The delivery settings could not be read, using the defaults: %s", exc)
        settings = DeliverySettings()
    with _lock:
        _cached = (now, settings)
    return settings


def forget() -> None:
    """Drop the reading kept by :func:`current`: for tests, and after a change of setting."""
    global _cached
    with _lock:
        _cached = None
