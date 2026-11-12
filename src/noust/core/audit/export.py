# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one export of the audit log: which events, and the two files they are written as (item 164).

``noust audit export``, ``GET /api/audit/export`` and the console's Audit page all end here, so a
period and a filter select the same events whoever asks and the two formats are written one way:

- NDJSON keeps every field and the chain, byte for byte, for an auditor or a SIEM that verifies it;
- CSV is for a spreadsheet: the columns people read, with what each event recorded in a last
  column.

CSV is where the two guards sit. What an event recorded is text from the log, which already had
its secrets taken out when it was written; it is masked once more by shape on the way out, because
a file that leaves the machine is the worse place to find one. And a cell a spreadsheet would run
as a formula (it starts with ``=``, ``+``, ``-`` or ``@``) is written as text, since the actor, the
target and the detail are things a person or a repository chose.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.secret_detection import mask_secret_shapes

if TYPE_CHECKING:
    from noust.core.audit.log import AuditLog

__all__ = [
    "CSV_COLUMNS",
    "FORMATS",
    "MAX_EXPORT",
    "ExportFilter",
    "detail_of",
    "render",
    "render_csv",
    "render_ndjson",
    "select_events",
    "text_matches",
]

#: The two files an export can be.
FORMATS = ("ndjson", "csv")

#: The columns of the CSV, in the order a spreadsheet shows them. ``detail`` is last so the
#: columns a script of before it existed read keep their place.
CSV_COLUMNS = (
    "seq",
    "ts",
    "actor",
    "action",
    "resource",
    "result",
    "ip",
    "corr",
    "mac",
    "detail",
)

#: The most events one export holds: the log is read into memory, and a million lines is a
#: period to export in parts, not one file.
MAX_EXPORT = 1_000_000

#: What starts a cell a spreadsheet reads as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class ExportFilter:
    """
    Which events an export keeps: the filters the Audit page has, and a period.

    Attributes:
        action: Only this exact event.
        result: Only this exact outcome.
        actor: Only this exact actor label.
        category: Only this catalog category.
        categories: Only events of one of these categories, as the console's views read the log.
        correlation_id: Only the events of one request, command or job.
        target: Only events on this exact target.
        q: Words to find in an event's action, actor, target, detail, address or correlation id,
            in any case; blank keeps everything.
        since: Lower bound on ``ts``, inclusive.
        until: Upper bound on ``ts``, inclusive.
    """

    action: str | None = None
    result: str | None = None
    actor: str | None = None
    category: str | None = None
    categories: tuple[str, ...] | None = None
    correlation_id: str | None = None
    target: str | None = None
    q: str | None = None
    since: str | None = None
    until: str | None = None

    def described(self) -> dict[str, Any]:
        """
        Say the filter that is set, for the audit event that records the export.

        Returns:
            Each field that is not empty, by name.
        """
        fields: dict[str, Any] = {
            "action": self.action,
            "result": self.result,
            "actor": self.actor,
            "category": self.category,
            "categories": list(self.categories) if self.categories else None,
            "correlation_id": self.correlation_id,
            "target": self.target,
            "q": (self.q or "").strip() or None,
        }
        return {name: value for name, value in fields.items() if value}


def detail_of(raw: dict[str, Any]) -> str | None:
    """
    Say what an event recorded in one line.

    Args:
        raw: An event as the log stores it.

    Returns:
        Its ``detail`` when it has one, else its ``details`` as ``key=value`` pairs; None when it
        recorded nothing.
    """
    if raw.get("detail"):
        return str(raw["detail"])
    details = raw.get("details")
    if isinstance(details, dict) and details:
        return ", ".join(
            f"{key}={value if isinstance(value, str) else json.dumps(value, sort_keys=True)}"
            for key, value in details.items()
        )
    return None


def text_matches(entry: dict[str, Any], q: str | None) -> bool:
    """
    Whether an event holds some words, as the Audit page's search box reads it.

    Args:
        entry: An event as the log stores it.
        q: The words; blank matches everything.

    Returns:
        True when the action, the actor, the structured actor's name, the target, the detail,
        the address or the correlation id contains them, in any case.
    """
    needle = (q or "").strip().lower()
    if not needle:
        return True
    who = entry.get("who")
    named = who.get("name") if isinstance(who, dict) else None
    values = (
        entry.get("action"),
        entry.get("actor"),
        named,
        entry.get("resource"),
        detail_of(entry),
        entry.get("ip"),
        entry.get("corr"),
    )
    return any(needle in str(value).lower() for value in values if value)


def select_events(log: AuditLog, selection: ExportFilter) -> list[dict[str, Any]]:
    """
    Read the events an export keeps, oldest first.

    Args:
        log: The audit log.
        selection: The filter and the period.

    Returns:
        Up to :data:`MAX_EXPORT` events.
    """
    categories: Collection[str] | None = selection.categories
    newest_first = log.read(
        limit=MAX_EXPORT,
        action=selection.action,
        result=selection.result,
        actor=selection.actor,
        category=selection.category,
        categories=categories,
        correlation_id=selection.correlation_id,
        target=selection.target,
        since=selection.since,
    )
    kept = [
        entry
        for entry in newest_first
        if (selection.until is None or str(entry.get("ts", "")) <= selection.until)
        and text_matches(entry, selection.q)
    ]
    kept.reverse()
    return kept


def render_ndjson(entries: list[dict[str, Any]]) -> str:
    """
    Write events as one JSON object per line, every field and the chain included.

    Args:
        entries: Events as the log stores them.

    Returns:
        The text; empty for no events.
    """
    return "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries)


def _cell(value: Any) -> Any:
    """
    Make one value safe to open in a spreadsheet.

    Args:
        value: A field of an event.

    Returns:
        The value, as text with a leading apostrophe when it would be read as a formula.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def render_csv(entries: list[dict[str, Any]]) -> str:
    """
    Write events as a CSV for a spreadsheet.

    Args:
        entries: Events as the log stores them.

    Returns:
        The header and one row per event; what an event recorded is masked by shape and no cell
        is a formula.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for entry in entries:
        row = [_cell(entry.get(name, "")) for name in CSV_COLUMNS[:-1]]
        row.append(_cell(mask_secret_shapes(detail_of(entry) or "")))
        writer.writerow(row)
    return buffer.getvalue()


def render(entries: list[dict[str, Any]], fmt: str) -> str:
    """
    Write events in one of the two formats.

    Args:
        entries: Events as the log stores them.
        fmt: One of :data:`FORMATS`.

    Returns:
        The file's text.

    Raises:
        ValueError: ``fmt`` is neither.
    """
    if fmt == "ndjson":
        return render_ndjson(entries)
    if fmt == "csv":
        return render_csv(entries)
    raise ValueError(f"an export is one of {', '.join(FORMATS)}, not {fmt!r}")
