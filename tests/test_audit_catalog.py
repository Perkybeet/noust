# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audit catalog is closed: every event name the code records is declared.

ENS op.exp.8.r3.1 asks which security events are audited, and the catalog is
the answer only if nothing records around it. The scan below reads the source
for every literal event name handed to a recording call - the new
``record(...)``, 3.0's ``.record(action=...)`` and the per-router ``_audit``
helpers - and fails on one the catalog does not declare.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from noust.core.audit import record
from noust.core.audit.bridge import LEGACY_EVENTS
from noust.core.audit.catalog import (
    CATEGORIES,
    CRITICAL,
    EVENTS,
    INFO,
    MAX_NAME_LENGTH,
    NOTICE,
    WARNING,
    markdown_table,
    severity_of,
    spec_for,
)

SOURCE = Path(__file__).resolve().parent.parent / "src" / "noust"

#: What an event name looks like: dotted lowercase words.
EVENT_NAME = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")

#: Calls whose literal arguments are event names.
RECORDING_CALLS = frozenset(
    {"record", "record_audit", "audit_record", "_audit", "audit", "_record", "record_event"}
)


def literal_event_names() -> dict[str, list[str]]:
    """
    Find every literal event name passed to a recording call in the source.

    Returns:
        Event name to where it appears, as ``path:line``.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        in_audit_package = "core/audit" in path.as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else getattr(function, "id", None)
            )
            if name == "append" and in_audit_package:
                pass
            elif name not in RECORDING_CALLS:
                continue
            candidates = [*node.args[:3]]
            # 3.0's AuditLogger.record names the event ``action=``; a helper
            # of another shape may use that keyword for a detail of its own.
            if name == "record":
                candidates += [kw.value for kw in node.keywords if kw.arg in ("action", "event")]
            for value in candidates:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if EVENT_NAME.match(value.value):
                        where = f"{path.relative_to(SOURCE.parent.parent)}:{node.lineno}"
                        found.setdefault(value.value, []).append(where)
    return found


def test_every_event_the_code_records_is_in_the_catalog() -> None:
    """An undeclared literal name is a programming error, caught here rather than in production."""
    unknown = {name: places for name, places in literal_event_names().items() if name not in EVENTS}
    assert not unknown, (
        "These events are recorded but missing from noust/core/audit/catalog.py; declare "
        "them there with a category, a severity and a sentence:\n"
        + "\n".join(f"  {name}: {', '.join(places)}" for name, places in sorted(unknown.items()))
    )


def test_the_scan_finds_the_events_the_code_is_known_to_record() -> None:
    """A scan that finds nothing would pass forever; it must see real call sites."""
    names = literal_event_names()
    for expected in ("auth.login", "apps.env.reveal", "fleet.authorize", "audit.purge"):
        assert expected in names, f"the scan no longer sees {expected}"


def test_names_built_at_runtime_are_declared() -> None:
    """The middleware builds ``api.<method>`` and ``<scope>.request`` from the request."""
    for name in ("api.post", "api.put", "api.patch", "api.delete"):
        assert name in EVENTS
    for scope in ("http", "websocket"):
        assert f"{scope}.request" in EVENTS


def test_every_event_the_old_logger_bridge_maps_to_is_declared() -> None:
    for verb, name in LEGACY_EVENTS.items():
        assert name in EVENTS, f"noust.audit line {verb!r} maps to undeclared {name!r}"


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_every_name_is_a_valid_rfc5424_msgid(name: str) -> None:
    """The event name is the MSGID of the shipped message: printable ASCII, 32 at most."""
    assert len(name) <= MAX_NAME_LENGTH
    assert all(33 <= ord(char) <= 126 for char in name)
    assert EVENT_NAME.match(name)


@pytest.mark.parametrize("name", sorted(EVENTS))
def test_every_entry_is_complete(name: str) -> None:
    spec = EVENTS[name]
    assert spec.category in CATEGORIES
    assert spec.severity in (CRITICAL, WARNING, NOTICE, INFO)
    assert spec.description.strip().endswith(".")


def test_sensitive_reads_are_their_own_category() -> None:
    for name, spec in EVENTS.items():
        assert spec.sensitive_read == (spec.category == "read"), name
    assert spec_for("apps.env.reveal").sensitive_read
    assert spec_for("apps.export.secrets").sensitive_read


def test_a_failure_is_shipped_at_warning_severity_at_least() -> None:
    login = spec_for("auth.login")
    assert severity_of(login, "ok") == INFO
    assert severity_of(login, "failure") == WARNING
    assert severity_of(login, "denied") == WARNING
    assert severity_of(spec_for("api.post"), "error:500") == WARNING
    assert severity_of(spec_for("auth.lockout"), "ok") == CRITICAL


def test_the_catalog_renders_as_the_ens_event_table() -> None:
    table = markdown_table()
    assert table.startswith("| Event | Category |")
    assert "| `auth.break_glass` | access | 2 |" in table
    assert table.count("\n") == len(EVENTS) + 2


def test_an_unknown_event_is_recorded_as_such_not_lost(tmp_path: Path) -> None:
    """A missed declaration must never cost the event itself."""
    from noust.core.audit import get_log

    record("apps.teleport", target="app:shop.example.com", details={"why": "typo"})

    entry = get_log().read(limit=1)[0]
    assert entry["action"] == "audit.unknown_event"
    assert entry["details"]["event"] == "apps.teleport"
    assert entry["details"]["why"] == "typo"
    assert entry["resource"] == "app:shop.example.com"
