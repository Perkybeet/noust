# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audit log on disk: one chained JSON line per event.

The file is the same one Noust 3.0 wrote (``web-audit.log``, 0600, in the
console's state directory), and a 3.0 line is still a valid line: readers see
the same ``ts``, ``action``, ``result``, ``actor``, ``ip``, ``resource`` and
``detail`` fields, so nothing that read the log before breaks. What 3.1 adds
to each line: ``v``, ``seq``, ``id``, the catalog's ``cat`` and ``sev``, the
structured actor in ``who``, ``details``, ``corr`` (the correlation id),
``host``, ``pid``, and the chain, ``prev`` and ``mac``.

Writes are serialised across processes with ``flock`` on ``audit.lock``, so
the console and every CLI command extend one chain. The current file is
closed every day and whenever it passes its size (``<name>.<UTC stamp>``);
closed files are deleted only once they are older than the retention period
*and* every configured destination has received them, and the deletion is
itself an event (``audit.purge``) that anchors the chain's new start.

A rehearsal (``--dry-run``) writes nothing here, the audit log included:
what would have been recorded goes to the process log.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust import __version__
from noust.core.audit import status
from noust.core.audit.actor import Actor
from noust.core.audit.catalog import CRITICAL, INFO, EventSpec, is_known, severity_of, spec_for
from noust.core.audit.chain import (
    GENESIS,
    VerifyResult,
    compute_mac,
    create_key,
    key_path_for,
    read_key,
    verify,
)
from noust.core.audit.context import current_actor, current_correlation_id
from noust.core.audit.flood import FloodGuard, FloodSummary
from noust.core.audit.sanitize import clean_details, clean_text
from noust.core.audit.settings import AuditSettings, load_settings
from noust.core.exceptions import SecurityError
from noust.core.fs import get_fs, is_rehearsal

logger = logging.getLogger(__name__)

LOG_MODE = 0o600
DIR_MODE = 0o700

#: The lock every writer takes, beside the log.
LOCK_FILE_NAME = "audit.lock"

#: Bytes read from the end of a file to find its last event.
TAIL_BYTES = 256 * 1024

#: Block size when reading a file backwards.
READ_BLOCK = 64 * 1024

_STAMP = re.compile(r"^(?P<stamp>\d{8}T\d{6}Z)(?:-(?P<index>\d+))?$")

_HOSTNAME = socket.gethostname()

#: Set while this thread is writing an event, so nothing the write itself
#: causes (a file the filesystem seam reports) is recorded in turn.
_writing = threading.local()


def writing_now() -> bool:
    """
    Whether this thread is inside an audit write.

    Returns:
        True while :meth:`AuditLog.append` is writing.
    """
    return bool(getattr(_writing, "active", False))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class PurgeReport:
    """
    What a retention purge deleted.

    Attributes:
        files: Names of the deleted files, oldest first.
        events: Chained events they held.
        last_seq: Sequence number of the newest deleted event.
        last_mac: Its MAC, which the oldest surviving event follows.
    """

    files: tuple[str, ...]
    events: int
    last_seq: int | None
    last_mac: str | None


class AuditLog:
    """
    Append to, read, verify and prune one chained audit log.

    Thread-safe and safe across processes. Every write failure is caught,
    logged and kept in :mod:`noust.core.audit.status`: recording an event
    never raises into the action being recorded.
    """

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        settings: AuditSettings | None = None,
        rotate_bytes: int | None = None,
        create: bool = False,
    ) -> None:
        """
        Args:
            path: The current log file.
            enabled: When false, nothing is written or read.
            settings: The audit settings; read from the configuration on first
                use when None.
            rotate_bytes: Size at which the current file is closed, instead of
                the setting's.
            create: Create the directory and the file now, and fail if that is
                impossible, instead of on the first write.

        Raises:
            SecurityError: ``create`` was asked for and the log cannot be
                created.
        """
        self.path = Path(path)
        self.enabled = enabled
        self.key_path = key_path_for(self.path)
        self.lock_path = self.path.parent / LOCK_FILE_NAME
        self._settings = settings
        self._rotate_bytes = rotate_bytes
        self._lock = threading.RLock()
        self._key: bytes | None = None
        self._head_cache: tuple[int, int, int, str] | None = None
        self._first_day: tuple[int, str] | None = None
        self._flood: FloodGuard | None = None
        self._inline: list[Any] | None = None
        #: Set by the worker when the log is over ``max_total_mb``.
        self.over_limit = False
        if create and enabled and not is_rehearsal():
            try:
                self._ensure_file(tighten=True)
            except OSError as exc:
                raise SecurityError(
                    f"Cannot open the audit log {self.path}",
                    details=(
                        "A console that runs systemd as root must be auditable. Run as root, or "
                        "set NOUST_WEB_STATE_DIR to a directory the current user owns."
                    ),
                ) from exc

    # -- configuration ------------------------------------------------------

    @property
    def settings(self) -> AuditSettings:
        """The audit settings in force, read on first use."""
        if self._settings is None:
            self._settings = load_settings()
        return self._settings

    def reload_settings(self, settings: AuditSettings | None = None) -> None:
        """
        Use new settings from now on.

        Args:
            settings: The settings; read from the configuration when None.
        """
        with self._lock:
            fresh = settings or load_settings()
            old = self._settings
            if self._flood is not None and (
                old is None
                or (old.flood_window_seconds, old.flood_burst)
                != (fresh.flood_window_seconds, fresh.flood_burst)
            ):
                # What the old windows counted is written, not dropped.
                self._write_summaries(self._flood.flush(time.monotonic(), force=True))
                self._flood = None
            self._settings = fresh
            if self._inline:
                for sink in self._inline:
                    sink.close()
            self._inline = None

    @property
    def rotate_bytes(self) -> int:
        """Size at which the current file is closed."""
        return self._rotate_bytes or self.settings.rotate_bytes

    def _guard(self) -> FloodGuard:
        if self._flood is None:
            settings = self.settings
            self._flood = FloodGuard(settings.flood_window_seconds, settings.flood_burst)
        return self._flood

    def _inline_sinks(self) -> list[Any]:
        if self._inline is None:
            from noust.core.audit.sinks import inline_sinks

            self._inline = list(inline_sinks(self.settings))
        return self._inline

    # -- recording ----------------------------------------------------------

    def append(
        self,
        event: str,
        *,
        actor: Actor | None = None,
        target: str | None = None,
        outcome: str = "ok",
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        label: str | None = None,
        detail: str | None = None,
        strict: bool = True,
    ) -> dict[str, Any] | None:
        """
        Record one event.

        Args:
            event: A catalog event name.
            actor: Who acted; the context's actor, else ``system``, when None.
            target: What it was done to.
            outcome: How it ended: ``ok``, ``failure``, ``denied``...
            details: Structured context; secrets are replaced before writing.
            correlation_id: Links events of one request, command or job; the
                context's when None.
            label: The actor label to show instead of the actor's own (lines
                written through the 3.0 interface keep theirs verbatim).
            detail: A one-line description, as the 3.0 interface wrote it.
            strict: Record a name missing from the catalog as
                ``audit.unknown_event`` (the default), or as itself, flagged
                ``uncatalogued`` (the 3.0 interface, whose callers predate
                the catalog).

        Returns:
            The event as written, or None when nothing was written: auditing
            is off, this is a rehearsal, the flood guard counted it, or the
            write failed (see :mod:`noust.core.audit.status`).
        """
        if not self.enabled:
            return None
        actor = actor or current_actor() or Actor.system()
        entry, spec = self._entry(
            event, actor, target, outcome, details, correlation_id, label, detail, strict
        )
        if is_rehearsal():
            logger.info("Audit entry not written during a rehearsal: %s", json.dumps(entry))
            return None

        with self._lock:
            summaries: list[FloodSummary] = []
            if actor.kind == "anonymous" and spec.severity > CRITICAL:
                admitted, summaries = self._guard().admit(
                    entry["action"],
                    actor.source,
                    target,
                    time.monotonic(),
                    tight=self.over_limit and spec.severity >= INFO,
                )
                self._write_summaries(summaries)
                if not admitted:
                    return None
            return self._commit(entry)

    def flush_flood(self, *, force: bool = False) -> int:
        """
        Write the summaries of flood windows that have closed.

        Args:
            force: Close every window now (the process is stopping).

        Returns:
            How many summaries were written.
        """
        if not self.enabled or self._flood is None:
            return 0
        with self._lock:
            summaries = self._flood.flush(time.monotonic(), force=force)
            self._write_summaries(summaries)
            return len(summaries)

    def _write_summaries(self, summaries: list[FloodSummary]) -> None:
        for flood in summaries:
            entry, _ = self._entry(
                "audit.coalesced",
                Actor.anonymous(),
                flood.target,
                "ok",
                {
                    "event": flood.event,
                    "count": flood.count,
                    "sources": list(flood.sources),
                    "distinct_sources": flood.distinct_sources,
                    "seconds": round(flood.last - flood.first, 3),
                    "window_seconds": flood.window_seconds,
                },
                None,
                None,
                None,
                True,
            )
            self._commit(entry)

    def _entry(
        self,
        event: str,
        actor: Actor,
        target: str | None,
        outcome: str,
        details: dict[str, Any] | None,
        correlation_id: str | None,
        label: str | None,
        detail: str | None,
        strict: bool,
    ) -> tuple[dict[str, Any], EventSpec]:
        cleaned = clean_details(details)
        if is_known(event):
            spec = spec_for(event)
            category = spec.category
        elif strict:
            logger.error("Audit event %r is not in the catalog; recorded as unknown", event)
            cleaned = {"event": clean_text(event, 64), **cleaned}
            event = "audit.unknown_event"
            spec = spec_for(event)
            category = spec.category
        else:
            spec = EventSpec("uncatalogued", INFO, "")
            category = "uncatalogued"
        entry: dict[str, Any] = {
            "v": 2,
            "ts": _utcnow().isoformat(),
            "action": event,
            "result": outcome,
            "cat": category,
            "sev": severity_of(spec, outcome),
            "actor": label if label is not None else actor.label,
            "who": actor.to_dict(),
            "host": _HOSTNAME,
            "pid": os.getpid(),
            "ver": __version__,
        }
        if actor.source:
            entry["ip"] = actor.source
        if target is not None:
            entry["resource"] = clean_text(str(target), 512)
        if detail is not None:
            entry["detail"] = clean_text(detail)
        if cleaned:
            entry["details"] = cleaned
        correlation = correlation_id or current_correlation_id()
        if correlation:
            entry["corr"] = correlation
        if spec.sensitive_read:
            entry["sensitive"] = True
        return entry, spec

    def _commit(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        """
        Chain and write one event, reporting a failure instead of raising it.

        Args:
            entry: The event without ``seq``, ``id``, ``prev`` and ``mac``.

        Returns:
            The event as written, or None when the write failed.
        """
        _writing.active = True
        try:
            lines = self._write(entry)
        except OSError as exc:
            message = f"Cannot write the audit log {self.path}: {exc}"
            if status.mark_failure(message):
                logger.error("%s. Audit events are being lost until this is fixed.", message)
            else:
                logger.debug(message)
            return None
        finally:
            _writing.active = False
        recovered = status.mark_success()
        for written in lines:
            self._deliver_inline(written)
        if recovered:
            self.append("audit.recovered", actor=Actor.system("audit"))
        return lines[-1]

    def _deliver_inline(self, entry: dict[str, Any]) -> None:
        for sink in self._inline_sinks():
            try:
                sink.send(entry)
            except OSError as exc:
                if status.mark_sink(sink.sink_id, str(exc)):
                    logger.warning("Cannot send audit events to %s: %s", sink.sink_id, exc)
                continue
            status.mark_sink(sink.sink_id, None)

    def _ensure_file(self, *, tighten: bool = False) -> None:
        """
        Create the log's directory and file when missing, 0700 and 0600.

        Args:
            tighten: Also set 0600 on a file that already exists, as the
                console does at start: a log copied in with looser modes is
                corrected rather than left readable.
        """
        if self.path.exists():
            if tighten:
                os.chmod(self.path, LOG_MODE)
            return
        directory = self.path.parent
        if not directory.is_dir():
            fs = get_fs()
            fs.make_dir(directory, mode=DIR_MODE)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, LOG_MODE)
        os.close(descriptor)
        os.chmod(self.path, LOG_MODE)

    def _load_key(self) -> tuple[bytes, bool]:
        if self._key is not None:
            return self._key, False
        key = read_key(self.key_path)
        created = False
        if key is None:
            try:
                key = create_key(self.key_path)
                created = True
            except FileExistsError:
                key = read_key(self.key_path)
                if key is None:
                    raise OSError(f"{self.key_path} vanished while it was being created") from None
        self._key = key
        return key, created

    def _write(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Chain and append one event under the cross-process lock.

        Args:
            entry: The event to write.

        Returns:
            What was written, oldest first: the event, preceded by
            ``audit.chain_start`` when the log had no chain yet or by
            ``audit.key_created`` when the key had to be created for an
            existing chain.

        Raises:
            OSError: The log, its key or its lock cannot be written.
        """
        self._ensure_file()
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, LOG_MODE)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            key, created = self._load_key()
            self._maybe_rotate()
            head = self._head()
            written: list[dict[str, Any]] = []
            prelude: tuple[str, str] | None = None
            if head is None:
                head = (0, GENESIS)
                prelude = ("audit.chain_start", "no earlier chained event in this log")
            elif created:
                prelude = (
                    "audit.key_created",
                    "no key file was found; earlier events cannot be verified",
                )
            if prelude is not None:
                system = self._system_entry(prelude[0], {"reason": prelude[1]})
                head = self._append_line(system, key, head)
                written.append(system)
            event = dict(entry)
            self._append_line(event, key, head)
            written.append(event)
            return written
        finally:
            os.close(descriptor)

    def _system_entry(self, event: str, details: dict[str, Any]) -> dict[str, Any]:
        # The log itself is the target: every line names one, as 3.0's did.
        entry, _ = self._entry(
            event,
            Actor.system("audit"),
            f"audit:{self.path.name}",
            "ok",
            details,
            None,
            None,
            None,
            True,
        )
        return entry

    def _append_line(
        self, entry: dict[str, Any], key: bytes, head: tuple[int, str]
    ) -> tuple[int, str]:
        # Stamped under the lock, so the order of the lines is the order of
        # their timestamps, which is what keyset pagination on ``ts`` needs.
        entry["ts"] = _utcnow().isoformat()
        entry["seq"] = head[0] + 1
        entry["id"] = uuid.uuid4().hex
        entry["prev"] = head[1]
        entry["mac"] = compute_mac(key, entry)
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, LOG_MODE)
        try:
            size = os.fstat(descriptor).st_size
            if size and not self._ends_with_newline(size):
                # A line cut short by a crash stays what it is (verify reports
                # it); the next event must not be glued onto it.
                os.write(descriptor, b"\n")
            os.write(descriptor, line.encode("ascii"))
            stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        self._head_cache = (stat.st_ino, stat.st_size, entry["seq"], entry["mac"])
        return entry["seq"], entry["mac"]

    def _ends_with_newline(self, size: int) -> bool:
        with open(self.path, "rb") as handle:
            handle.seek(size - 1)
            return handle.read(1) == b"\n"

    # -- head, rotation and files -------------------------------------------

    def _head(self) -> tuple[int, str] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            stat = None
        if stat is not None and self._head_cache is not None:
            inode, size, seq, mac = self._head_cache
            if (stat.st_ino, stat.st_size) == (inode, size):
                return seq, mac
        for path in self.files_newest_first():
            last = last_chained(path)
            if last is not None:
                return int(last["seq"]), str(last["mac"])
        return None

    def _maybe_rotate(self) -> None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return
        if stat.st_size == 0:
            return
        today = _utcnow().strftime("%Y-%m-%d")
        if stat.st_size < self.rotate_bytes and self._first_day_of(stat.st_ino) >= today:
            return
        stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        destination = self.path.with_name(f"{self.path.name}.{stamp}")
        suffix = 0
        while destination.exists():
            suffix += 1
            destination = self.path.with_name(f"{self.path.name}.{stamp}-{suffix}")
        os.rename(self.path, destination)
        self._ensure_file()

    def _first_day_of(self, inode: int) -> str:
        if self._first_day is not None and self._first_day[0] == inode:
            return self._first_day[1]
        day = "9999-12-31"
        with open(self.path, "rb") as handle:
            first = handle.readline(TAIL_BYTES)
        try:
            moment = _parse_ts(json.loads(first).get("ts"))
        except (json.JSONDecodeError, AttributeError):
            moment = None
        if moment is not None:
            day = moment.astimezone(timezone.utc).strftime("%Y-%m-%d")
        self._first_day = (inode, day)
        return day

    def closed_files(self) -> list[Path]:
        """
        The files the log was rotated into, oldest first.

        Returns:
            3.0's numbered backups (``.3``, ``.2``, ``.1``), then the dated
            files in order. The current file is not included.
        """
        numbered: list[tuple[int, Path]] = []
        dated: list[tuple[tuple[str, int], Path]] = []
        prefix = f"{self.path.name}."
        if not self.path.parent.is_dir():
            return []
        for candidate in self.path.parent.iterdir():
            if not candidate.name.startswith(prefix) or not candidate.is_file():
                continue
            suffix = candidate.name[len(prefix) :]
            if suffix.isdigit():
                numbered.append((int(suffix), candidate))
            elif match := _STAMP.match(suffix):
                # Several files closed in the same second are numbered; "-10"
                # comes after "-9", which a plain string sort gets wrong.
                dated.append(((match["stamp"], int(match["index"] or 0)), candidate))
        return [path for _, path in sorted(numbered, reverse=True)] + [
            path for _, path in sorted(dated)
        ]

    def files_oldest_first(self) -> list[Path]:
        """
        Every file of the log, oldest first.

        Returns:
            The closed files, then the current one when it exists.
        """
        files = self.closed_files()
        if self.path.exists():
            files.append(self.path)
        return files

    def files_newest_first(self) -> list[Path]:
        """
        Every file of the log, newest first.

        Returns:
            The reverse of :meth:`files_oldest_first`.
        """
        return list(reversed(self.files_oldest_first()))

    def total_bytes(self) -> int:
        """
        The size of the whole log.

        Returns:
            Bytes across every file.
        """
        total = 0
        for path in self.files_oldest_first():
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def head(self) -> tuple[int, str] | None:
        """
        The newest chained event.

        Returns:
            Its sequence number and MAC, or None when nothing is chained yet.
        """
        with self._lock:
            return self._head()

    # -- reading ------------------------------------------------------------

    def iter_newest_first(self) -> Iterator[dict[str, Any]]:
        """
        Every event, newest first, across files.

        Yields:
            Each event as a dict; malformed lines are skipped.
        """
        if not self.enabled:
            return
        for path in self.files_newest_first():
            for text in reverse_lines(path):
                entry = _parse(text)
                if entry is not None:
                    yield entry

    def iter_lines(self) -> Iterator[tuple[str, int, str]]:
        """
        Every line, oldest first, with where it is.

        Yields:
            ``(file name, line number, text)``.
        """
        for path in self.files_oldest_first():
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for number, text in enumerate(handle, start=1):
                        yield path.name, number, text.rstrip("\n")
            except FileNotFoundError:
                continue

    def iter_since(self, seq: int) -> Iterator[dict[str, Any]]:
        """
        Chained events after a sequence number, oldest first.

        Whole files whose last event is not newer are skipped without being
        read, so shipping from a recent cursor costs the recent files only.

        Args:
            seq: The last sequence number already handled.

        Yields:
            Each later chained event.
        """
        files = self.files_oldest_first()
        start = 0
        for index in range(len(files) - 1, -1, -1):
            last = last_chained(files[index])
            if last is not None and int(last["seq"]) <= seq:
                start = index + 1
                break
        for path in files[start:]:
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for text in handle:
                        entry = _parse(text)
                        if entry is None or not isinstance(entry.get("seq"), int):
                            continue
                        if entry["seq"] > seq:
                            yield entry
            except FileNotFoundError:
                continue

    def read(
        self,
        *,
        limit: int = 50,
        before: str | None = None,
        action: str | None = None,
        result: str | None = None,
        actor: str | None = None,
        category: str | None = None,
        correlation_id: str | None = None,
        target: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Read events newest first, with keyset pagination on ``ts``.

        Args:
            limit: Most events returned.
            before: Only events strictly older than this ``ts`` (the last one
                of the previous page).
            action: Only this exact event.
            result: Only this exact outcome.
            actor: Only this exact actor label.
            category: Only this catalog category.
            correlation_id: Only events of this request, command or job.
            target: Only events on this exact target.
            since: Only events at or after this ``ts``; reading stops there.

        Returns:
            Up to ``limit`` events.
        """
        matched: list[dict[str, Any]] = []
        for entry in self.iter_newest_first():
            timestamp = str(entry.get("ts", ""))
            if since is not None and timestamp < since:
                break
            if before is not None and not timestamp < before:
                continue
            if action is not None and entry.get("action") != action:
                continue
            if result is not None and entry.get("result") != result:
                continue
            if actor is not None and entry.get("actor") != actor:
                continue
            if category is not None and entry.get("cat") != category:
                continue
            if correlation_id is not None and entry.get("corr") != correlation_id:
                continue
            if target is not None and entry.get("resource") != target:
                continue
            matched.append(entry)
            if len(matched) >= limit:
                break
        return matched

    def find(self, key: str) -> dict[str, Any] | None:
        """
        Find one event by sequence number or id.

        Args:
            key: A ``seq`` (digits) or an ``id`` (or a prefix of one).

        Returns:
            The newest matching event, or None.
        """
        wanted_seq = int(key) if key.isdigit() else None
        for entry in self.iter_newest_first():
            if wanted_seq is not None and entry.get("seq") == wanted_seq:
                return entry
            if wanted_seq is None and str(entry.get("id", "")).startswith(key):
                return entry
        return None

    def verify(self) -> VerifyResult:
        """
        Check the whole chain.

        Returns:
            What :func:`noust.core.audit.chain.verify` found.

        Raises:
            OSError: The key exists but cannot be read.
        """
        key = self._key or read_key(self.key_path)
        return verify(self.iter_lines(), key)

    # -- retention ----------------------------------------------------------

    def purge(
        self, *, retention_days: int, shipped_seq: int | None, now: datetime | None = None
    ) -> PurgeReport | None:
        """
        Delete closed files past the retention period that were shipped.

        Files go oldest first and the purge stops at the first one that must
        stay, so what remains is always one contiguous stretch of the chain.
        The deletion goes through the filesystem seam, so a rehearsal
        deletes nothing, and is recorded as ``audit.purge`` with the MAC the
        oldest surviving event follows.

        Args:
            retention_days: Age after which a file may go.
            shipped_seq: The oldest cursor among the shipping destinations: a
                file holding a later event stays. None when nothing ships.
            now: The current time; for tests.

        Returns:
            What was deleted, or None when nothing was.
        """
        if not self.enabled or is_rehearsal():
            return None
        cutoff = (now or _utcnow()) - timedelta(days=retention_days)
        doomed: list[Path] = []
        events = 0
        last_seq: int | None = None
        last_mac: str | None = None
        with self._lock:
            for path in self.closed_files():
                newest = last_entry(path)
                moment = _parse_ts(newest.get("ts")) if newest else None
                if newest is not None and (moment is None or moment >= cutoff):
                    break
                chained = last_chained(path)
                if chained is not None and shipped_seq is not None and chained["seq"] > shipped_seq:
                    break
                doomed.append(path)
                if chained is not None:
                    last_seq, last_mac = int(chained["seq"]), str(chained["mac"])
                    events += sum(1 for _ in _chained_lines(path))
            if not doomed:
                return None
            report = PurgeReport(tuple(path.name for path in doomed), events, last_seq, last_mac)
            # The anchor first: deleted first, a crash or a failed append left
            # a gap at the head of the chain that nothing in the log explained.
            # An anchor whose files are still there is only early.
            self.append(
                "audit.purge",
                actor=Actor.system("retention"),
                details={
                    "files": list(report.files),
                    "events": report.events,
                    "last_seq": report.last_seq,
                    "last_mac": report.last_mac,
                    "retention_days": retention_days,
                },
            )
            fs = get_fs()
            for path in doomed:
                fs.remove(path)
        return report


# -- file helpers -----------------------------------------------------------


def _parse(text: str) -> dict[str, Any] | None:
    if not text.strip():
        return None
    try:
        entry = json.loads(text)
    except json.JSONDecodeError:
        return None
    return entry if isinstance(entry, dict) else None


def reverse_lines(path: Path) -> Iterator[str]:
    """
    Read a file's lines from the last to the first, a block at a time.

    Args:
        path: The file.

    Yields:
        Each line without its newline, newest first.
    """
    try:
        handle = open(path, "rb")
    except FileNotFoundError:
        return
    with handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        remainder = b""
        while position > 0:
            size = min(READ_BLOCK, position)
            position -= size
            handle.seek(position)
            block = handle.read(size) + remainder
            lines = block.split(b"\n")
            remainder = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line.decode("utf-8", errors="replace")
        if remainder:
            yield remainder.decode("utf-8", errors="replace")


def last_entry(path: Path) -> dict[str, Any] | None:
    """
    The newest parseable event of a file.

    Args:
        path: The file.

    Returns:
        The event, or None when the file holds none.
    """
    for text in reverse_lines(path):
        entry = _parse(text)
        if entry is not None:
            return entry
    return None


def last_chained(path: Path) -> dict[str, Any] | None:
    """
    The newest chained event of a file.

    Args:
        path: The file.

    Returns:
        The event with its ``seq`` and ``mac``, or None when the file holds
        no chained event.
    """
    for text in reverse_lines(path):
        entry = _parse(text)
        if entry is not None and isinstance(entry.get("seq"), int) and entry.get("mac"):
            return entry
    return None


def _chained_lines(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for text in handle:
                entry = _parse(text)
                if entry is not None and entry.get("mac"):
                    yield entry
    except FileNotFoundError:
        return
