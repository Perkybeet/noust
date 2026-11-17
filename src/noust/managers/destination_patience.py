# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How patient a call to a backup destination is, decided here and nowhere else (item 201).

A destination is spoken to in two ways, and whoever calls says which:

* **Asking** is testing a destination, the monitor's probe and listing it for a person who is
  waiting at the console. It takes one attempt and short times, so that a destination that is down
  says so in seconds. This is what every call to rclone was, until a night of bad network between a
  server and its destination turned nine scheduled sends into nine failures: each had one attempt.
* **Working** is sending a copy, fetching one, deleting by retention, and the listings and checks
  inside those. It is patient: three attempts, a pause that grows between them and times that put
  up with a network that loses packets, and a ceiling for the whole operation, so that a run is never
  held all night by one send.

The numbers, each with its reason, are :data:`ASK` and :data:`WORK`. rclone's own retry is left at one
attempt in both: the loop is Noust's (it logs every attempt and honours the ceiling), and a second loop
inside rclone would multiply the attempts of the first.

A public operation of a destination manager says what it is (:func:`working`, :func:`asking`) and
every rclone call under it follows, through a :class:`~contextvars.ContextVar`: the helpers below an
operation never change their signature, and a job running on its own thread does not borrow the
mode of another.

What waiting does not mend (a refused credential, a missing bucket, a full quota) is told apart
(:func:`is_permanent`): retrying it only delays the news.
"""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from noust.core.logger import Logger
from noust.core.runner import CommandResult, CommandRunner
from noust.core.server_text import say

__all__ = [
    "ASK",
    "WORK",
    "Patience",
    "asking",
    "current",
    "env_for",
    "is_permanent",
    "run",
    "using",
    "working",
]

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Patience:
    """
    How hard to try one call to a destination.

    Attributes:
        name: ``ask`` or ``work``.
        attempts: How many times a call is made before it is given up.
        waits: Seconds paused after the first failure, after the second... the last one repeats.
        connect_timeout: rclone's ``--contimeout``: how long to wait for the connection.
        idle_timeout: rclone's ``--timeout``: how long a transfer may move no data; None leaves
            rclone's own (five minutes).
        low_level_retries: rclone's ``--low-level-retries``, the retries of one request inside a
            transfer; None leaves rclone's own.
        cap_seconds: The most one operation may last altogether, attempts and pauses included;
            0 for no ceiling (one attempt has none to keep).
    """

    name: str
    attempts: int
    waits: tuple[float, ...]
    connect_timeout: str
    idle_timeout: str | None
    low_level_retries: int | None
    cap_seconds: float


#: Asking. One attempt and a short connection time: rclone's defaults retry an unreachable server for
#: about a minute and a half, and a destination that is down should say so in seconds.
ASK = Patience(
    name="ask",
    attempts=1,
    waits=(),
    connect_timeout="15s",
    idle_timeout=None,
    low_level_retries=None,
    cap_seconds=0.0,
)

#: Working. Three attempts: the first is the ordinary one, the second rides out a lost packet or a
#: router that restarts, the third a provider's blip, and a fourth would only make a run late (the next
#: scheduled run and the deferred retries of ``noust.managers.backup_sending`` are what come after).
#: The pauses grow, half a minute and then two, because a network that lost the first attempt is
#: more likely to be back for one that waits longer. A connection gets a minute (a lossy path may need
#: several SYNs) and a transfer two minutes without data before it is called stuck, a third of
#: rclone's own, which is what let the real night hold each failed send for minutes. The ceiling is
#: two hours: longer than any one transfer should take, shorter than the night.
WORK = Patience(
    name="work",
    attempts=3,
    waits=(30.0, 120.0),
    connect_timeout="60s",
    idle_timeout="2m",
    low_level_retries=5,
    cap_seconds=2 * 3600.0,
)

#: The shortest attempt worth starting when the ceiling is nearly spent.
_MIN_ATTEMPT_SECONDS = 30.0

#: What a person is told when waiting did not help: rclone's own words are kept untouched above it.
_current: ContextVar[Patience] = ContextVar("noust_destination_patience", default=ASK)

#: The clock and the pause, as functions of this module so that a test can move both and never wait.
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep

#: What waiting does not mend. Every text is compared lower-cased. A credential that was refused, a
#: bucket that is not there, a quota that is full, a host key that changed: trying again in half a
#: minute only delays the news. The rest of what rclone says (a timeout, a refused connection, a
#: reset, a 5xx) is a network and may be better in an hour.
_PERMANENT_TEXT = (
    "permission denied",
    "access denied",
    "accessdenied",
    "authentication failed",
    "unable to authenticate",
    "auth failed",
    "unauthorized",
    "forbidden",
    "invalid credentials",
    "invalidaccesskeyid",
    "signaturedoesnotmatch",
    "invalid access key",
    "nosuchbucket",
    "no such bucket",
    "bucket does not exist",
    "quota",
    "insufficient storage",
    "no space left",
    "disk full",
    "read-only file system",
    "operation not permitted",
    "knownhosts",
    "known_hosts",
    "host key",
    "key has expired",
    "login incorrect",
    "bad password",
    "didn't find section in config file",
    "unknown backend",
)

#: HTTP statuses that are a refusal and not a network. Whole numbers only: ``4030`` is not a 403.
_PERMANENT_STATUS = re.compile(r"(?<![0-9])40[13](?![0-9])")

#: rclone's exit codes for a directory or a file that is not there: an answer, not a failure.
_NOT_THERE = (3, 4)

#: The words that say it is not there, for the programs that do not use the exit code.
_NOT_THERE_TEXT = ("directory not found", "object not found", "file not found")


def current() -> Patience:
    """
    Returns:
        The patience of the operation this thread is in: :data:`ASK` outside any.
    """
    return _current.get()


@contextmanager
def using(mode: Patience) -> Iterator[None]:
    """
    Say how patient everything inside the block is.

    Args:
        mode: :data:`ASK` or :data:`WORK`.

    Yields:
        Nothing; the previous patience is back when the block ends, however it ends.
    """
    token = _current.set(mode)
    try:
        yield
    finally:
        _current.reset(token)


def working(function: _F) -> _F:
    """
    Declare that a public operation works: sends, fetches or deletes copies.

    Args:
        function: A method of a destination manager.

    Returns:
        The method, whose every call to rclone is patient.
    """

    @functools.wraps(function)
    def patient(*args: Any, **kwargs: Any) -> Any:
        with using(WORK):
            return function(*args, **kwargs)

    return patient  # type: ignore[return-value]


def asking(function: _F) -> _F:
    """
    Declare that a public operation asks: one attempt and short times, whoever called it.

    Args:
        function: A method of a destination manager.

    Returns:
        The method, whose every call to rclone is a quick one.
    """

    @functools.wraps(function)
    def quick(*args: Any, **kwargs: Any) -> Any:
        with using(ASK):
            return function(*args, **kwargs)

    return quick  # type: ignore[return-value]


def env_for(mode: Patience) -> dict[str, str]:
    """
    Say how rclone is to behave, as the environment it reads its flags from.

    Args:
        mode: The patience.

    Returns:
        ``RCLONE_*`` variables: one retry of rclone's own (the loop is :func:`run`'s) and the
        times of the mode.
    """
    env = {"RCLONE_RETRIES": "1", "RCLONE_CONTIMEOUT": mode.connect_timeout}
    if mode.idle_timeout is not None:
        env["RCLONE_TIMEOUT"] = mode.idle_timeout
    if mode.low_level_retries is not None:
        env["RCLONE_LOW_LEVEL_RETRIES"] = str(mode.low_level_retries)
    return env


def is_permanent(text: str) -> bool:
    """
    Say whether waiting would mend what a destination said.

    Args:
        text: What rclone (or Noust, wrapping it) said.

    Returns:
        True for a refused credential, a missing bucket, a full quota and the like; False for a
        network that failed, and for no words at all.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in _PERMANENT_TEXT):
        return True
    return _PERMANENT_STATUS.search(lowered) is not None


def _retryable(result: CommandResult) -> bool:
    """
    Args:
        result: A failed call.

    Returns:
        Whether another attempt could go differently: a deadline always could; a thing that is not
        there or a refusal never will.
    """
    if result.timed_out:
        return True
    if result.exit_code in _NOT_THERE:
        return False
    text = f"{result.stderr}\n{result.stdout}"
    if any(words in text.lower() for words in _NOT_THERE_TEXT):
        return False
    return not is_permanent(text)


def _span(seconds: float) -> str:
    """
    Args:
        seconds: A duration.

    Returns:
        It as a person reads it: ``30s``, ``2m30s``.
    """
    whole = round(seconds)
    minutes, rest = divmod(whole, 60)
    if not minutes:
        return f"{rest}s"
    return f"{minutes}m{rest:02d}s" if rest else f"{minutes}m"


def run(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: int,
    secrets: Sequence[str],
    input: str | None = None,
) -> CommandResult:
    """
    Run one call to a destination as patiently as the operation it is part of says.

    Every attempt is said (the journal of a timer's run shows each one), the pause grows, and the
    ceiling of the operation shrinks the deadline of the attempts that are left and stops the ones that
    would run past it. What fails for a reason waiting does not mend is not tried again.

    Args:
        runner: The one way a process is run.
        argv: The command, ``rclone`` first.
        env: Its environment (:func:`env_for` has been merged in by the caller).
        timeout: The deadline of one attempt, in seconds.
        secrets: Values to keep out of what is recorded.
        input: What to send on its standard input, sent again with every attempt.

    Returns:
        The last attempt's result. After several attempts that all failed, its ``stderr`` ends with
        a line of Noust's own that says how many there were and over how long.
    """
    mode = current()
    verb = " ".join(str(part) for part in argv[:2])
    started = _monotonic()
    attempt = 1
    while True:
        left = mode.cap_seconds - (_monotonic() - started) if mode.cap_seconds else float(timeout)
        deadline = int(timeout if attempt == 1 else max(1, min(float(timeout), left)))
        result = runner.run(list(argv), env=env, timeout=deadline, secrets=secrets, input=input)
        if result.success:
            return result
        if attempt >= mode.attempts or not _retryable(result):
            return _with_the_story(result, attempt, _monotonic() - started)
        pause = mode.waits[min(attempt - 1, len(mode.waits) - 1)] if mode.waits else 0.0
        spent = _monotonic() - started
        if mode.cap_seconds and spent + pause + _MIN_ATTEMPT_SECONDS > mode.cap_seconds:
            return _with_the_story(result, attempt, spent)
        Logger(verbose=False).warning(
            f"{verb}: attempt {attempt} of {mode.attempts} failed "
            f"({_last_line(result)}); trying again in {_span(pause)}"
        )
        _sleep(pause)
        attempt += 1


def _last_line(result: CommandResult) -> str:
    """
    Args:
        result: A failed call.

    Returns:
        The last line it said, bounded, for the log.
    """
    lines = [
        line.strip() for line in f"{result.stderr}\n{result.stdout}".splitlines() if line.strip()
    ]
    return (lines[-1] if lines else f"exit status {result.exit_code}")[:200]


def _with_the_story(result: CommandResult, attempts: int, seconds: float) -> CommandResult:
    """
    Add to a failure, once it is final, how many times it was tried.

    Args:
        result: The last attempt's result.
        attempts: How many attempts there were.
        seconds: How long they took altogether.

    Returns:
        The result, unchanged when there was one attempt; otherwise with a closing line of Noust's
        own after what the tool said, which stays verbatim.
    """
    if attempts < 2:
        return result
    note = say("backup_destinations.gave_up_after", count=str(attempts), span=_span(seconds))
    return replace(result, stderr=f"{result.stderr.rstrip()}\n{note.text}".lstrip("\n"))
