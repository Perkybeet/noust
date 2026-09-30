# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The integrity chain of the audit log (ENS op.exp.8.r4).

Every event carries ``prev``, the MAC of the event before it, and ``mac``,
an HMAC-SHA256 over its own canonical JSON under a key that exists only for
this purpose (``audit-key``, 0600, beside the log). Changing, removing or
reordering any line breaks the chain at that line, and :func:`verify` names
the first one that breaks.

**What this does not protect against, said plainly:** root on this machine
can read the key and write a new, internally consistent chain. The chain
makes tampering *evident to someone who holds an earlier copy*: a SIEM that
received the events as they were written (see :mod:`noust.core.audit.sinks`)
and the ``audit.checkpoint`` events it stored will not match a rewritten
history. That is why the log is shipped off the machine, and why a local
``noust audit verify`` that passes is necessary but not sufficient evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: ``prev`` of the first event of a chain.
GENESIS = "0" * 64

#: What a passing verification does and does not prove, said wherever one is
#: reported (``noust audit verify``, ``GET /api/audit/verify``).
LIMITATION = (
    "A consistent chain proves no line was changed, removed or reordered by anyone "
    "without the audit key. Root on this machine holds that key and could write a new, "
    "consistent chain: compare the head with the copy shipped off the machine "
    "(audit.checkpoint events at your SIEM) to show it is also the original."
)

#: Name of the key file, beside the log.
KEY_FILE_NAME = "audit-key"

KEY_MODE = 0o600


def canonical(entry: dict[str, Any]) -> bytes:
    """
    The exact bytes an event's MAC covers.

    Args:
        entry: The event, without its ``mac``.

    Returns:
        Its JSON with sorted keys, no whitespace and ASCII escapes, so the
        same event always serialises to the same bytes, whatever wrote it.
    """
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def compute_mac(key: bytes, entry: dict[str, Any]) -> str:
    """
    MAC one event.

    Args:
        key: The chain key.
        entry: The event, with its ``prev`` and without its ``mac``.

    Returns:
        The hex HMAC-SHA256.
    """
    body = {name: value for name, value in entry.items() if name != "mac"}
    return hmac.new(key, canonical(body), hashlib.sha256).hexdigest()


def key_path_for(log_path: Path) -> Path:
    """
    Where the key of a log lives.

    Args:
        log_path: The log file.

    Returns:
        ``audit-key`` in the log's directory.
    """
    return log_path.parent / KEY_FILE_NAME


def read_key(path: Path) -> bytes | None:
    """
    Read a chain key.

    Args:
        path: The key file.

    Returns:
        The key, or None when there is no key file yet.

    Raises:
        OSError: The file exists and cannot be read, or holds no key.
    """
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        text = handle.read().decode("ascii", errors="replace").strip()
    try:
        key = bytes.fromhex(text)
    except ValueError as exc:
        raise OSError(f"{path} does not hold an audit key") from exc
    if len(key) < 32:
        raise OSError(f"{path} holds a key shorter than 256 bits")
    return key


def create_key(path: Path) -> bytes:
    """
    Create a chain key, 0600 from the moment it exists.

    Args:
        path: The key file, which must not exist.

    Returns:
        The new key.

    Raises:
        FileExistsError: Another process created it first; read theirs.
        OSError: It cannot be written.
    """
    key = secrets.token_bytes(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, KEY_MODE)
    try:
        os.write(descriptor, key.hex().encode("ascii") + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return key


@dataclass(frozen=True)
class BrokenLink:
    """
    The first place a chain does not hold.

    Attributes:
        file: The file the line is in.
        line: Its line number in that file, from 1.
        seq: The sequence number the line claims, when it has one.
        reason: What is wrong, in one sentence.
    """

    file: str
    line: int
    seq: int | None
    reason: str


@dataclass
class VerifyResult:
    """
    What :func:`verify` found.

    Attributes:
        checked: Chained events whose MAC was checked.
        legacy: Lines written before the chain existed, which cannot be checked.
        first_seq: Sequence number of the oldest chained event.
        last_seq: Sequence number of the newest chained event.
        last_mac: MAC of the newest chained event: the value a receiver's
            latest ``audit.checkpoint`` should hold.
        broken: The first broken link, or None when the chain holds.
        notes: Things worth knowing that are not breaks.
    """

    checked: int = 0
    legacy: int = 0
    first_seq: int | None = None
    last_seq: int | None = None
    last_mac: str | None = None
    broken: BrokenLink | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no link is broken."""
        return self.broken is None

    def to_dict(self) -> dict[str, Any]:
        """
        The result as JSON-ready data.

        Returns:
            Every field, the broken link flattened.
        """
        return {
            "ok": self.ok,
            "checked": self.checked,
            "legacy": self.legacy,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "last_mac": self.last_mac,
            "broken": None
            if self.broken is None
            else {
                "file": self.broken.file,
                "line": self.broken.line,
                "seq": self.broken.seq,
                "reason": self.broken.reason,
            },
            "notes": list(self.notes),
        }


def verify(lines: Iterable[tuple[str, int, str]], key: bytes | None) -> VerifyResult:
    """
    Walk a log oldest first and find the first line that breaks the chain.

    A chain may start at :data:`GENESIS`, or after a retention purge: the
    oldest surviving event's ``prev`` is then the MAC an ``audit.purge``
    event later in the log recorded for the last event it deleted. A start
    that neither explains is a log whose head was cut off.

    Args:
        lines: ``(file, line number, text)`` for every line, oldest first.
        key: The chain key, or None when it is missing (every chained line is
            then reported as uncheckable).

    Returns:
        The result; :attr:`VerifyResult.broken` is the first problem found.
    """
    result = VerifyResult()
    previous_mac: str | None = None
    previous_seq: int | None = None
    anchor: tuple[str, int, int | None, str] | None = None
    purged_macs: set[str] = set()

    for file_name, number, text in lines:
        if not text.strip():
            continue
        try:
            entry = json.loads(text)
        except json.JSONDecodeError:
            result.broken = BrokenLink(file_name, number, None, "the line is not valid JSON")
            return result
        if not isinstance(entry, dict):
            result.broken = BrokenLink(file_name, number, None, "the line is not an event")
            return result
        if "mac" not in entry:
            if previous_mac is not None:
                result.broken = BrokenLink(
                    file_name, number, None, "an unchained line follows chained events"
                )
                return result
            result.legacy += 1
            continue

        seq = entry.get("seq")
        seq = seq if isinstance(seq, int) else None
        if key is None:
            result.broken = BrokenLink(
                file_name, number, seq, "the audit key is missing, so no MAC can be checked"
            )
            return result
        if not hmac.compare_digest(compute_mac(key, entry), str(entry.get("mac"))):
            result.broken = BrokenLink(
                file_name, number, seq, "the MAC does not match: the event was changed"
            )
            return result

        prev = str(entry.get("prev", ""))
        if previous_mac is None:
            anchor = (file_name, number, seq, prev)
            result.first_seq = seq
        else:
            if prev == GENESIS and seq == 1:
                result.notes.append(
                    f"A new chain starts at {file_name}:{number}; the events before it "
                    "belong to a previous chain."
                )
            elif prev != previous_mac:
                result.broken = BrokenLink(
                    file_name, number, seq, "the previous event is not the one this one follows"
                )
                return result
            elif seq is not None and previous_seq is not None and seq != previous_seq + 1:
                result.broken = BrokenLink(
                    file_name,
                    number,
                    seq,
                    f"events {previous_seq + 1} to {seq - 1} are missing",
                )
                return result

        if entry.get("action") == "audit.purge":
            details = entry.get("details") or {}
            if isinstance(details, dict) and details.get("last_mac"):
                purged_macs.add(str(details["last_mac"]))
        previous_mac = str(entry["mac"])
        previous_seq = seq
        result.checked += 1

    result.last_seq = previous_seq
    result.last_mac = previous_mac
    if anchor is not None and anchor[3] != GENESIS and anchor[3] not in purged_macs:
        result.broken = BrokenLink(
            anchor[0],
            anchor[1],
            anchor[2],
            "the oldest event follows one that is gone, and no retention purge accounts for it",
        )
    if result.legacy:
        result.notes.append(
            f"{result.legacy} line(s) predate the chain (Noust 3.0 or earlier) and cannot be "
            "checked."
        )
    return result
