# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application is told about being delivered: its history, its stop and its pause.

One row per application in ``app_rollout`` (``schema_v14.ROLLOUT_SQL``), kept apart
from ``apps`` on purpose: a deploy writes the whole ``apps`` row it read when it
started, and a setting an operator changed meanwhile would be put back (see
:mod:`noust.core.app_build_store`). No row means the defaults, which are what every
application did before these options existed.

- ``history_keep``: how many deployments of its history are kept, with their logs.
  None follows the server (``deploy.history_keep``).
- ``stop_timeout`` and ``stop_signal``: how long the unit may take to stop after the
  signal, and which signal asks it to, before systemd kills it. None is systemd's own
  (90 seconds, ``SIGTERM``); an application that drains connections or finishes jobs
  needs longer, one that dies on ``SIGTERM`` may want ``SIGINT``.
- the pause: whether what deploys the application by itself (a push, a tag, a
  schedule) is held, since when, by whom and why. Going back pauses it
  (``pause_on_rollback``, on unless switched off) so that the next push does not undo
  the rollback; resuming is the operator's decision. A person's own update, click or
  command, is never held by it.

This is the only module that reads or writes the table (rule 3); every setter
validates, so the CLI, the API and ``noust apply`` cannot disagree on what is
acceptable (rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core import audit, delivery
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore, get_store

#: Deployments of one application's history that can be kept, at least and at most.
MIN_HISTORY_KEEP = 1
MAX_HISTORY_KEEP = 1000

#: Seconds a unit may take to stop (``TimeoutStopSec``): a second to an hour.
MIN_STOP_TIMEOUT = 1
MAX_STOP_TIMEOUT = 3600

#: The signals a unit may be asked to stop with. A signal that does not end a process
#: (``SIGUSR1`` on most runtimes does nothing) is left out on purpose: the unit would
#: wait out the timeout and be killed every time.
STOP_SIGNALS: tuple[str, ...] = ("SIGTERM", "SIGINT", "SIGQUIT", "SIGHUP")

#: Longest reason a pause keeps.
MAX_REASON = 300

#: The columns :func:`_write` may set.
_COLUMNS = frozenset(
    {
        "history_keep",
        "stop_timeout",
        "stop_signal",
        "pause_on_rollback",
        "paused_at",
        "paused_by",
        "paused_reason",
    }
)

#: Who the pause is attributed to when going back paused it.
ROLLBACK_PAUSE_ACTOR = "rollback"


@dataclass(frozen=True)
class Rollout:
    """
    An application's delivery settings.

    Attributes:
        history_keep: Deployments kept; None follows the server.
        stop_timeout: ``TimeoutStopSec`` in seconds; None is systemd's.
        stop_signal: ``KillSignal``; None is ``SIGTERM``.
        pause_on_rollback: Going back pauses what deploys by itself.
        paused_at: When automatic deploys were paused; None while they run.
        paused_by: Who paused them (``rollback`` when going back did).
        paused_reason: Why, in a sentence.
        customised: Whether a row exists, that is, somebody set something.
    """

    history_keep: int | None = None
    stop_timeout: int | None = None
    stop_signal: str | None = None
    pause_on_rollback: bool = True
    paused_at: str | None = None
    paused_by: str | None = None
    paused_reason: str | None = None
    customised: bool = False

    @property
    def paused(self) -> bool:
        """Whether what deploys the application by itself is held."""
        return self.paused_at is not None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the settings for the API and ``--json``.

        Returns:
            Every setting, and the pause as one object (null while it runs).
        """
        return {
            "history_keep": self.history_keep,
            "stop_timeout": self.stop_timeout,
            "stop_signal": self.stop_signal,
            "pause_on_rollback": self.pause_on_rollback,
            "paused": (
                {"at": self.paused_at, "by": self.paused_by, "reason": self.paused_reason}
                if self.paused
                else None
            ),
            "customised": self.customised,
        }


def _now() -> str:
    """
    Returns:
        The current time in UTC, ISO 8601 with its offset.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get(domain: str, *, store: NoustStore | None = None) -> Rollout:
    """
    Read an application's delivery settings.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The settings; the defaults for an application with no row or no application.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT r.history_keep, r.stop_timeout, r.stop_signal, r.pause_on_rollback, "
            "r.paused_at, r.paused_by, r.paused_reason FROM app_rollout r "
            "JOIN apps a ON a.id = r.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return Rollout()
    return Rollout(
        history_keep=row[0],
        stop_timeout=row[1],
        stop_signal=row[2],
        pause_on_rollback=bool(row[3]),
        paused_at=row[4],
        paused_by=row[5],
        paused_reason=row[6],
        customised=True,
    )


def history_keep_for(domain: str, *, store: NoustStore | None = None) -> int:
    """
    Say how many deployments of an application's history are kept.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its own setting, else the server's ``deploy.history_keep``.
    """
    own = get(domain, store=store).history_keep
    return own if own is not None else delivery.current().history_keep


def check_history_keep(value: object, *, field: str = "history_keep") -> int | None:
    """
    Args:
        value: What a caller gave; None goes back to the server's number.
        field: The name to blame in a refusal.

    Returns:
        The number of deployments to keep, or None.

    Raises:
        ValidationError: Not a whole number in range.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            "The number of deployments to keep must be a whole number",
            details=f"Give {MIN_HISTORY_KEEP} to {MAX_HISTORY_KEEP}, or 'default' for the server's.",
            field=field,
        )
    if not MIN_HISTORY_KEEP <= value <= MAX_HISTORY_KEEP:
        raise ValidationError(
            f"An application keeps {MIN_HISTORY_KEEP} to {MAX_HISTORY_KEEP} deployments, "
            f"not {value}",
            details="Older deployments and their build logs are deleted past the number. "
            "A rollback needs the one it goes back to to be in the history.",
            field=field,
        )
    return value


def check_stop_timeout(value: object, *, field: str = "stop_timeout") -> int | None:
    """
    Args:
        value: Seconds a caller gave; None goes back to systemd's.
        field: The name to blame in a refusal.

    Returns:
        The timeout, or None.

    Raises:
        ValidationError: Not a whole number of seconds in range.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            "The stop timeout must be a whole number of seconds",
            details=f"Give {MIN_STOP_TIMEOUT} to {MAX_STOP_TIMEOUT}.",
            field=field,
        )
    if not MIN_STOP_TIMEOUT <= value <= MAX_STOP_TIMEOUT:
        raise ValidationError(
            f"A unit may take {MIN_STOP_TIMEOUT} to {MAX_STOP_TIMEOUT} seconds to stop, "
            f"not {value}",
            details="After that long systemd kills what is left of it.",
            field=field,
        )
    return value


def check_stop_signal(value: object, *, field: str = "stop_signal") -> str | None:
    """
    Args:
        value: The signal a caller gave; None goes back to ``SIGTERM``.
        field: The name to blame in a refusal.

    Returns:
        The signal, upper case, or None.

    Raises:
        ValidationError: A signal a unit is not stopped with.
    """
    if value is None:
        return None
    name = str(value).strip().upper()
    if not name.startswith("SIG"):
        name = f"SIG{name}"
    if name not in STOP_SIGNALS:
        raise ValidationError(
            f"Not a signal to stop with: {value!r}",
            details="Use one of: " + ", ".join(STOP_SIGNALS) + ".",
            field=field,
        )
    return name


def _write(
    domain: str,
    changes: dict[str, Any],
    *,
    actor: str | None,
    store: NoustStore | None,
) -> Rollout:
    """
    Merge columns into an application's row, making it when there is none.

    Args:
        domain: The application's domain.
        changes: Column to value; only the columns of the table.
        actor: Who changed it.
        store: The store; the process-wide one when None.

    Returns:
        The settings now in force.

    Raises:
        NoustError: The application is unknown.
    """
    unknown = set(changes) - _COLUMNS
    if unknown:
        # Column names are code, never input; this is the guard on the one f-string.
        raise ValueError(f"not a column of app_rollout: {sorted(unknown)}")
    target = store or get_store()
    columns = ", ".join(changes)
    marks = ", ".join("?" for _ in changes)
    updates = ", ".join(f"{name} = excluded.{name}" for name in changes)
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        cursor.execute(
            f"INSERT INTO app_rollout (app_id, {columns}, updated_by, updated_at) "  # noqa: S608 - names checked above, values bound
            f"VALUES (?, {marks}, ?, ?) ON CONFLICT(app_id) DO UPDATE SET {updates}, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (row[0], *changes.values(), actor, _now()),
        )
    return get(domain, store=target)


def set_history_keep(
    domain: str, keep: object, *, actor: str | None = None, store: NoustStore | None = None
) -> Rollout:
    """
    Set how many deployments of an application's history are kept.

    The history is pruned to the number at the application's next deployment.

    Args:
        domain: The application's domain.
        keep: A number, or None for the server's.
        actor: Who changed it.
        store: The store; the process-wide one when None.

    Returns:
        The settings now in force.

    Raises:
        ValidationError: The number is not acceptable.
        NoustError: The application is unknown.
    """
    value = check_history_keep(keep)
    settings = _write(domain, {"history_keep": value}, actor=actor, store=store)
    audit.record(
        "apps.rollout",
        target=f"app:{domain}",
        details={"history_keep": value if value is not None else "default"},
    )
    return settings


def set_stop_policy(
    domain: str,
    *,
    timeout: object,
    signal: object,
    actor: str | None = None,
    store: NoustStore | None = None,
) -> Rollout:
    """
    Set how an application's unit is asked to stop.

    Only the row is written here; :func:`noust.deployers.stop_policy.apply_stop_policy`
    is what puts it in the unit.

    Args:
        domain: The application's domain.
        timeout: Seconds, or None for systemd's.
        signal: A signal name, or None for ``SIGTERM``.
        actor: Who changed it.
        store: The store; the process-wide one when None.

    Returns:
        The settings now in force.

    Raises:
        ValidationError: The timeout or the signal is not acceptable.
        NoustError: The application is unknown.
    """
    seconds = check_stop_timeout(timeout)
    name = check_stop_signal(signal)
    settings = _write(
        domain, {"stop_timeout": seconds, "stop_signal": name}, actor=actor, store=store
    )
    audit.record(
        "apps.rollout",
        target=f"app:{domain}",
        details={"stop_timeout": seconds, "stop_signal": name},
    )
    return settings


def set_pause_on_rollback(
    domain: str, enabled: bool, *, actor: str | None = None, store: NoustStore | None = None
) -> Rollout:
    """
    Choose whether going back pauses what deploys the application by itself.

    Args:
        domain: The application's domain.
        enabled: True to pause after a rollback (the default).
        actor: Who changed it.
        store: The store; the process-wide one when None.

    Returns:
        The settings now in force.

    Raises:
        ValidationError: ``enabled`` is not true or false.
        NoustError: The application is unknown.
    """
    if not isinstance(enabled, bool):
        raise ValidationError(
            "pause_on_rollback must be true or false",
            details="Give it as on or off.",
            field="pause_on_rollback",
        )
    settings = _write(domain, {"pause_on_rollback": int(enabled)}, actor=actor, store=store)
    audit.record("apps.rollout", target=f"app:{domain}", details={"pause_on_rollback": enabled})
    return settings


def pause(
    domain: str,
    *,
    reason: str | None = None,
    by: str | None = None,
    store: NoustStore | None = None,
) -> Rollout:
    """
    Hold what deploys an application by itself: its pushes, its tags and its schedules.

    A person's own update is not held. Pausing a paused application keeps when it
    was paused and says the new reason.

    Args:
        domain: The application's domain.
        reason: Why, in a sentence; none is allowed.
        by: Who paused it (``rollback`` when going back did).
        store: The store; the process-wide one when None.

    Returns:
        The settings now in force.

    Raises:
        ValidationError: The reason is too long.
        NoustError: The application is unknown.
    """
    text = (reason or "").strip() or None
    if text is not None and len(text) > MAX_REASON:
        raise ValidationError(
            f"The reason is {len(text)} characters long; {MAX_REASON} is the most",
            field="reason",
        )
    current = get(domain, store=store)
    settings = _write(
        domain,
        {
            "paused_at": current.paused_at or _now(),
            "paused_by": by,
            "paused_reason": text,
        },
        actor=by,
        store=store,
    )
    audit.record(
        "deploys.pause",
        target=f"app:{domain}",
        details={"by": by, "reason": text},
    )
    return settings


def resume(domain: str, *, by: str | None = None, store: NoustStore | None = None) -> bool:
    """
    Let an application's pushes, tags and schedules deploy it again.

    Args:
        domain: The application's domain.
        by: Who resumed it.
        store: The store; the process-wide one when None.

    Returns:
        True when it was paused.

    Raises:
        NoustError: The application is unknown.
    """
    if not get(domain, store=store).paused:
        target = store or get_store()
        if target.get_app(domain) is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        return False
    _write(
        domain,
        {"paused_at": None, "paused_by": None, "paused_reason": None},
        actor=by,
        store=store,
    )
    audit.record("deploys.resume", target=f"app:{domain}", details={"by": by})
    return True


def forget(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Go back to the defaults, pause included.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a row.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_rollout WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        return cursor.rowcount > 0


__all__ = [
    "MAX_HISTORY_KEEP",
    "MAX_STOP_TIMEOUT",
    "MIN_HISTORY_KEEP",
    "MIN_STOP_TIMEOUT",
    "ROLLBACK_PAUSE_ACTOR",
    "STOP_SIGNALS",
    "Rollout",
    "check_history_keep",
    "check_stop_signal",
    "check_stop_timeout",
    "forget",
    "get",
    "history_keep_for",
    "pause",
    "resume",
    "set_history_keep",
    "set_pause_on_rollback",
    "set_stop_policy",
]
