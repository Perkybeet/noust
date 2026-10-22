# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The write-ahead log archive of a PostgreSQL cluster, and the base backups taken from it.

Everything here is a pure function over names, texts and dates, so the rules a recovery
depends on are pinned by tests without an engine: which file is a segment, which segments a
set of base backups still needs, which base backups a retention window keeps, which base to
recover from for an instant, and the exact command the engine runs to archive a segment.

The log is only useful whole: a recovery replays every segment from the one a base backup
started in to the instant asked for, so the rules that delete anything err toward keeping.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any

from noust.core.exceptions import ValidationError

__all__ = [
    "BackupLabel",
    "BaseBackup",
    "Fork",
    "archive_command",
    "base_id_at",
    "bases_to_drop",
    "choose_timeline",
    "format_recovery_time",
    "missing_segments",
    "parse_backup_label",
    "parse_base_id",
    "parse_history",
    "parse_recovery_time",
    "prunable_wal",
    "restore_command",
    "select_base",
    "wal_position",
]

#: One segment of the log: eight hex digits of timeline, then the two halves of the position.
_SEGMENT = re.compile(r"([0-9A-F]{8})([0-9A-F]{8})([0-9A-F]{8})")
#: The file PostgreSQL writes beside the segments when a base backup ends.
_BACKUP_FILE = re.compile(r"([0-9A-F]{24})\.[0-9A-F]{8}\.backup")
#: The file that records where a timeline forked from its parent.
_HISTORY_FILE = re.compile(r"[0-9A-F]{8}\.history")
#: A base backup's identifier: the UTC instant it started.
_BASE_ID = re.compile(r"base-(\d{8}T\d{6}Z)")

#: Where an archive may be: nothing a shell or PostgreSQL's configuration parser could read as
#: anything but a path.
_SAFE_DIRECTORY = re.compile(r"/[A-Za-z0-9_./-]+")

#: How the engine writes an instant in ``recovery_target_time``.
RECOVERY_TIME_FORMAT = "%Y-%m-%d %H:%M:%S+00"


@dataclass(frozen=True)
class BaseBackup:
    """
    A base backup: a copy of the cluster's files that the archived log can be replayed over.

    Attributes:
        id: ``base-<UTC start>``; also the stem of its files.
        started_at: When the backup began.
        finished_at: When it ended: the earliest instant a recovery can stop at.
        start_wal_file: The first segment recovery needs.
        timeline: The timeline the cluster was on.
        size: Bytes of the archive.
        sha256: Its digest, checked before every recovery.
        server_version: The engine's version when it was taken.
        system_id: The cluster's identifier; a base is only ever replayed over its own log.
    """

    id: str
    started_at: datetime
    finished_at: datetime
    start_wal_file: str
    timeline: int
    size: int
    sha256: str
    server_version: str
    system_id: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The base as plain data, JSON-ready (the sidecar beside its archive).
        """
        return {
            "format": 1,
            "id": self.id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds"),
            "start_wal_file": self.start_wal_file,
            "timeline": self.timeline,
            "size": self.size,
            "sha256": self.sha256,
            "server_version": self.server_version,
            "system_id": self.system_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaseBackup:
        """
        Args:
            data: A sidecar, as :meth:`to_dict` writes it.

        Returns:
            The base.

        Raises:
            ValueError: When a field is missing or malformed.
        """
        try:
            return cls(
                id=str(data["id"]),
                started_at=datetime.fromisoformat(str(data["started_at"])),
                finished_at=datetime.fromisoformat(str(data["finished_at"])),
                start_wal_file=str(data["start_wal_file"]),
                timeline=int(data["timeline"]),
                size=int(data["size"]),
                sha256=str(data["sha256"]),
                server_version=str(data.get("server_version", "")),
                system_id=str(data["system_id"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"not a base backup record: {exc!r}") from exc


@dataclass(frozen=True)
class BackupLabel:
    """
    What the ``.backup`` file PostgreSQL archives says about a finished base backup.

    Attributes:
        label: The label the backup was started with.
        start_wal_file: The first segment recovery needs.
        timeline: The timeline it started on.
        started_at: When it began (UTC).
        stopped_at: When it ended (UTC).
    """

    label: str
    start_wal_file: str
    timeline: int
    started_at: datetime
    stopped_at: datetime


def _utc(text: str) -> datetime:
    """
    Read a time as PostgreSQL prints it in a backup label.

    Args:
        text: ``2026-10-17 12:34:56 UTC``.

    Returns:
        The instant, aware.

    Raises:
        ValueError: When it is not one.
    """
    stamp = text.strip().removesuffix("UTC").strip()
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def parse_backup_label(text: str) -> BackupLabel:
    """
    Read the ``.backup`` file PostgreSQL archives when a base backup ends.

    Args:
        text: Its contents.

    Returns:
        The label, the first segment needed and the instants.

    Raises:
        ValueError: When a field recovery needs is missing.
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields.setdefault(key.strip(), value.strip())
    located = re.search(r"\(file ([0-9A-F]{24})\)", fields.get("START WAL LOCATION", ""))
    if located is None:
        raise ValueError("no START WAL LOCATION with its file")
    try:
        return BackupLabel(
            label=fields["LABEL"],
            start_wal_file=located.group(1),
            timeline=int(fields.get("START TIMELINE") or int(located.group(1)[:8], 16)),
            started_at=_utc(fields["START TIME"]),
            stopped_at=_utc(fields["STOP TIME"]),
        )
    except KeyError as exc:
        raise ValueError(f"no {exc.args[0]}") from exc


def base_id_at(started: datetime) -> str:
    """
    Name a base backup by the instant it starts.

    Args:
        started: When it starts.

    Returns:
        ``base-20261017T123456Z``.
    """
    return "base-" + started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_base_id(name: str) -> datetime | None:
    """
    Read the instant a base backup's name or file name carries.

    Args:
        name: ``base-20261017T123456Z`` or a file starting with it.

    Returns:
        The instant, or None when the name is not a base backup's.
    """
    matched = _BASE_ID.match(name)
    if matched is None:
        return None
    return datetime.strptime(matched.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Fork:
    """
    Where a timeline split from its parent, as its ``.history`` file records.

    Attributes:
        timeline: The timeline the file is named after.
        parent: The timeline it continued from.
        at: The instant of the commit the parent's history stopped before (or after): the
            moment the two diverge. For a timeline that began at the end of the log, with no
            target, the file's own time.
    """

    timeline: int
    parent: int
    at: datetime


#: The instant a history line's reason names: ``before 2026-10-17 13:08:26.594565+00``.
_FORK_REASON = re.compile(
    r"(?:before|after) (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)([+-]\d{2})(?::?(\d{2}))?"
)


def parse_history(name: str, text: str, archived_at: datetime) -> Fork | None:
    """
    Read a timeline history file.

    Args:
        name: Its file name, ``00000002.history``.
        text: Its contents: one line per ancestor, the last being the parent's fork.
        archived_at: When the file was archived, the fork's time when the line names none (a
            recovery to the end of the log).

    Returns:
        The fork, or None when the file is not one.
    """
    if _HISTORY_FILE.fullmatch(name) is None:
        return None
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return None
    fields = lines[-1].split("\t", 2)
    if len(fields) < 2 or not fields[0].isdigit():
        return None
    when = archived_at
    reason = _FORK_REASON.search(fields[2]) if len(fields) > 2 else None
    if reason is not None:
        stamp = reason.group(1)
        fraction = ".%f" if "." in stamp else ""
        parsed = datetime.strptime(stamp, f"%Y-%m-%d %H:%M:%S{fraction}")
        offset = timedelta(hours=int(reason.group(2)), minutes=int(reason.group(3) or 0))
        if reason.group(2).startswith("-"):
            offset = -abs(offset)
        when = (parsed - offset).replace(tzinfo=timezone.utc)
    return Fork(timeline=int(name[:8], 16), parent=int(fields[0]), at=when)


def choose_timeline(start: int, target: datetime, forks: Sequence[Fork]) -> int:
    """
    Pick the timeline a recovery to an instant should follow.

    ``latest``, the engine's default, always takes the newest branch, and that is wrong for an
    instant that falls before a branch began: the recovery to it has already stopped by then,
    unless nothing after the instant was ever written on that branch, and then the engine
    reaches the end of the log without ever passing the instant and refuses ("recovery ended
    before configured recovery target was reached"). So a branch is followed only when the
    instant is at or after the moment it split off.

    Args:
        start: The timeline of the base backup.
        target: The instant to recover to.
        forks: Every timeline history the archive holds.

    Returns:
        The timeline to name in ``recovery_target_timeline``.
    """
    current = start
    while True:
        branches = sorted(
            (fork for fork in forks if fork.parent == current and fork.at <= target),
            key=lambda fork: (fork.at, fork.timeline),
        )
        if not branches:
            return current
        current = branches[-1].timeline


def wal_position(name: str) -> tuple[int, int, int] | None:
    """
    Order a segment: its timeline, then where in the log it is.

    Args:
        name: A file name of the archive.

    Returns:
        ``(timeline, log, segment)``, or None when the name is not a segment (a ``.backup``
        file is its segment's position).
    """
    matched = _SEGMENT.fullmatch(name)
    if matched is None:
        backup = _BACKUP_FILE.fullmatch(name)
        if backup is None:
            return None
        matched = _SEGMENT.fullmatch(backup.group(1))
        if matched is None:
            return None
    timeline, log, segment = (int(part, 16) for part in matched.groups())
    return timeline, log, segment


def prunable_wal(names: Iterable[str], bases: Sequence[BaseBackup]) -> list[str]:
    """
    Say which files of the archive no base backup can use any more.

    A segment is kept when some base backup starts at or before it on its own timeline, or
    on an earlier one (a later timeline continues from there). Timeline history files, files
    that are not segments and the archive of a cluster with no base backup are always kept:
    the log has no use but under a base, and deleting from it by guess is the one mistake
    that cannot be undone.

    Args:
        names: The files of the archive.
        bases: The base backups kept.

    Returns:
        The names that can go, sorted.
    """
    anchors = [wal_position(base.start_wal_file) for base in bases]
    needed = [anchor for anchor in anchors if anchor is not None]
    if not needed:
        return []
    doomed: list[str] = []
    for name in names:
        if _HISTORY_FILE.fullmatch(name):
            continue
        position = wal_position(name)
        if position is None:
            continue
        timeline, log, segment = position
        if not any(
            timeline > anchor[0] or (timeline == anchor[0] and (log, segment) >= anchor[1:])
            for anchor in needed
        ):
            doomed.append(name)
    return sorted(doomed)


def bases_to_drop(
    bases: Sequence[BaseBackup],
    *,
    now: datetime,
    count: int | None,
    days: int | None,
) -> list[BaseBackup]:
    """
    Pick the base backups retention lets go of.

    "Keep ``days`` days" means a recovery to any instant of the last ``days`` days must be
    possible, so the base backup that came before the window opens is kept too: it is the one
    the first instants of the window are recovered from. Both limits given, the one that keeps
    more wins, and the newest base backup always stays.

    Args:
        bases: Every base backup, in any order.
        now: The current instant.
        count: Base backups to keep, newest first; None for no limit by number.
        days: How far back a recovery must reach; None for no limit by age.

    Returns:
        The ones to delete, oldest first.
    """
    ordered = sorted(bases, key=lambda base: (base.finished_at, base.id), reverse=True)
    if not ordered or (not count and not days):
        return []
    keep: set[str] = {ordered[0].id}
    if count:
        keep.update(base.id for base in ordered[:count])
    if days:
        cutoff = now - timedelta(days=days)
        inside = [base for base in ordered if base.finished_at >= cutoff]
        keep.update(base.id for base in inside)
        before = [base for base in ordered if base.finished_at < cutoff]
        if before:
            keep.add(before[0].id)
    return [base for base in reversed(ordered) if base.id not in keep]


def select_base(bases: Sequence[BaseBackup], target: datetime | None) -> BaseBackup:
    """
    Pick the base backup to recover from.

    Args:
        bases: Every base backup.
        target: The instant to recover to; None for the end of the log, which the newest base
            backup reaches.

    Returns:
        The newest base backup that finished at or before the instant.

    Raises:
        ValidationError: When there is none, naming the earliest instant that can be reached.
    """
    ordered = sorted(bases, key=lambda base: (base.finished_at, base.id))
    if not ordered:
        raise ValidationError(
            "There is no base backup to recover from",
            details="Take one with: noust db pitr base",
            field="time",
        )
    if target is None:
        return ordered[-1]
    usable = [base for base in ordered if base.finished_at <= target]
    if not usable:
        first = ordered[0].finished_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        raise ValidationError(
            "No base backup is old enough for that instant",
            details=(
                f"The oldest one finished at {first}, and a recovery cannot stop before the "
                "backup it starts from ends. Ask for a later instant."
            ),
            field="time",
        )
    return usable[-1]


def missing_segments(names: Iterable[str], base: BaseBackup, last: str | None) -> list[str]:
    """
    Find the gaps in the log between a base backup and the last segment archived.

    Only the base's own timeline is checked: a later one continues from a fork point the
    history file records, and the engine itself says what it lacks there.

    Args:
        names: The files of the archive.
        base: The base backup recovery starts from.
        last: The last segment archived on the base's timeline; None checks only the first.

    Returns:
        The segment names that should be there and are not, in order. Empty when the log is
        whole. A cluster with 16 MB segments has 256 per log; others are read from the names.
    """
    present = {name for name in names if _SEGMENT.fullmatch(name)}
    start = wal_position(base.start_wal_file)
    if start is None:
        return [base.start_wal_file]
    end = wal_position(last) if last else start
    if end is None or end[0] != start[0]:
        end = start
    per_log = _segments_per_log(present, start[0])
    missing: list[str] = []
    log, segment = start[1], start[2]
    while (log, segment) <= (end[1], end[2]):
        name = f"{start[0]:08X}{log:08X}{segment:08X}"
        if name not in present:
            missing.append(name)
        segment += 1
        if segment >= per_log:
            log, segment = log + 1, 0
        if len(missing) > 64:
            break
    return missing


def _segments_per_log(names: set[str], timeline: int) -> int:
    """
    Work out how many segments one log file holds, from the highest segment number seen.

    Args:
        names: Segment names.
        timeline: The timeline to look at.

    Returns:
        256 for the default 16 MB segments; larger when the cluster was made with smaller
        ones (the highest number seen is below the log's capacity, so it can only grow).
    """
    highest = 0
    for name in names:
        position = wal_position(name)
        if position is not None and position[0] == timeline:
            highest = max(highest, position[2])
    return max(256, highest + 1)


def parse_recovery_time(text: str) -> datetime:
    """
    Read the instant an operator asks to recover to.

    No time zone means UTC, and the plan prints the instant back as UTC, because a recovery
    that stops an hour from where the operator thought is worse than one that asked.

    Args:
        text: ``2026-10-17 12:34:56``, ``2026-10-17T12:34:56Z``, ``2026-10-17 14:34:56+02:00``.

    Returns:
        The instant, aware, in UTC.

    Raises:
        ValidationError: When it is not an instant.
    """
    candidate = text.strip().replace(" UTC", "Z").replace("Z", "+00:00")
    candidate = re.sub(r"^(\d{4}-\d{2}-\d{2}) ", r"\1T", candidate)
    try:
        instant = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError(
            f"{text!r} is not a date and time",
            details="Write it as 2026-10-17 12:34:56 (UTC), or with its offset: "
            "2026-10-17T14:34:56+02:00.",
            field="time",
        ) from exc
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def format_recovery_time(instant: datetime) -> str:
    """
    Write an instant the way ``recovery_target_time`` reads it.

    Args:
        instant: The instant, aware.

    Returns:
        ``2026-10-17 12:34:56+00``.
    """
    return instant.astimezone(timezone.utc).strftime(RECOVERY_TIME_FORMAT)


def _checked_directory(directory: PurePath | str) -> str:
    """
    Make sure a directory can be written into a configuration file and a shell word as it is.

    Args:
        directory: An absolute path.

    Returns:
        The path as text.

    Raises:
        ValidationError: When it holds anything but letters, digits and ``_./-``.
    """
    text = str(directory)
    if _SAFE_DIRECTORY.fullmatch(text) is None or ".." in text.split("/"):
        raise ValidationError(
            f"{text!r} cannot be the directory of the archive",
            details="It must be an absolute path made of letters, digits and _ . / -",
            field="archive",
        )
    return text


def archive_command(directory: PurePath | str) -> str:
    """
    Build the command the engine runs to archive each segment.

    The PostgreSQL documentation's rule (never overwrite an archived file) made safe against a
    repeat: a segment archived twice with the same bytes (a crash between the copy and the
    engine noting it) succeeds, one that differs fails loudly, and the file only appears under
    its real name once its bytes are on disk (copy, flush, rename), so the log never holds a
    half-written segment. coreutils alone, and nothing of Noust's: the archive keeps working
    when Noust does not run.

    Args:
        directory: Where the archive is.

    Returns:
        The text of ``archive_command``, without single quotes so it can sit inside the
        statement that sets it.

    Raises:
        ValidationError: When the directory is not a plain absolute path.
    """
    where = _checked_directory(directory)
    target = f'"{where}/%f"'
    partial = f'"{where}/%f.noust-partial"'
    return (
        f'test -f {target} && cmp -s "%p" {target} || '
        f'{{ test ! -f {target} && cp "%p" {partial} && sync {partial} && mv {partial} {target}; }}'
    )


def restore_command(directory: PurePath | str) -> str:
    """
    Build the command the engine runs to fetch a segment during a recovery.

    Args:
        directory: Where the archive is.

    Returns:
        The text of ``restore_command``.

    Raises:
        ValidationError: When the directory is not a plain absolute path.
    """
    where = _checked_directory(directory)
    return f'cp "{where}/%f" "%p"'
