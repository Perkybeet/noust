# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Route the ``noust.audit`` logger into the audit trail (finding H2).

Several endpoints (databases, cron, backup schedules and destinations, the
GitHub integration, the application importer) write their audit lines as
``logging.getLogger("noust.audit").info("drop_database engine=%s ...")``.
Nothing in Noust ever configured a handler for that logger, and its level
fell back to the root's ``WARNING``, so every one of those lines was
discarded before it was formatted: a dropped database left no trace at all.

:class:`AuditLogHandler` turns each line into a catalog event: the first word
names it (:data:`LEGACY_EVENTS`), ``session=`` is the actor, the other
``key=value`` pairs are details, and a ``statement=`` is replaced by its
digest. A word missing from the table is still recorded, as
``audit.legacy`` with the whole line, so no line is lost while those call
sites move to :func:`noust.core.audit.record`.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Any

from noust.core.audit.actor import Actor

#: The logger the old call sites write to.
LOGGER_NAME = "noust.audit"

#: First word of an old line to its catalog event.
LEGACY_EVENTS: dict[str, str] = {
    "drop_database": "db.drop",
    "create_user": "db.user.create",
    "grant": "db.grant",
    "revoke": "db.revoke",
    "drop_user": "db.user.drop",
    "restore": "db.restore",
    "query": "db.query",
    "create_cron_job": "cron.create",
    "update_cron_job": "cron.update",
    "delete_cron_job": "cron.delete",
    "run_cron_job": "cron.run",
    "enable_cron_job": "cron.enable",
    "disable_cron_job": "cron.disable",
    "create_backup_schedule": "backup.schedule.create",
    "update_backup_schedule": "backup.schedule.update",
    "delete_backup_schedule": "backup.schedule.delete",
    "create_backup_destination": "backup.destination.create",
    "update_backup_destination": "backup.destination.update",
    "delete_backup_destination": "backup.destination.delete",
    "show_backup_destination_key": "backup.destination.key",
    "github_manifest_start": "integration.github.manifest",
    "github_app_create": "integration.github.create",
    "github_app_created": "integration.github.create",
    "github_installation_add": "integration.github.install",
    "github_installations_sync": "integration.github.sync",
    "github_app_remove": "integration.github.remove",
}

#: Detail keys that name the target of the event, in order of preference.
TARGET_KEYS = ("domain", "database", "name", "user", "installation_id", "app")

_PAIR = re.compile(r"(\w+)=('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|\S*)")


def parse_line(message: str) -> tuple[str, dict[str, Any]]:
    """
    Split an old audit line into its verb and its ``key=value`` pairs.

    Args:
        message: The formatted line, such as
            ``drop_database engine=postgresql database=shop session=master``.

    Returns:
        The first word and the pairs; a quoted value (``%r``) is unquoted.
    """
    verb, _, rest = message.strip().partition(" ")
    pairs: dict[str, Any] = {}
    for match in _PAIR.finditer(rest):
        key, value = match.group(1), match.group(2)
        if value[:1] in ("'", '"'):
            try:
                value = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                value = value[1:-1]
        pairs[key] = value
    return verb, pairs


class AuditLogHandler(logging.Handler):
    """Record each ``noust.audit`` log line as an audit event."""

    def emit(self, record: logging.LogRecord) -> None:
        """
        Turn one line into an event.

        Args:
            record: The log record.
        """
        from noust.core.audit import get_log
        from noust.core.audit.sanitize import statement_digest

        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            self.handleError(record)
            return
        verb, pairs = parse_line(message)
        label = str(pairs.pop("session", "") or "") or None
        actor = Actor.from_label(label) if label else None
        statement = pairs.pop("statement", None)
        if isinstance(statement, str):
            pairs["statement"] = statement_digest(statement)
        event = LEGACY_EVENTS.get(verb)
        if event == "db.query" and str(pairs.get("mode", "")).lower() in ("read", "readonly"):
            event = "db.query.read"
        target = next((str(pairs[key]) for key in TARGET_KEYS if pairs.get(key)), None)
        if event is None:
            event = "audit.legacy"
            pairs = {"line": message}
        get_log().append(event, actor=actor, label=label, target=target, details=pairs)


_handler: AuditLogHandler | None = None


def install_bridge() -> None:
    """
    Attach the handler to ``noust.audit`` and let INFO lines through.

    The logger keeps propagating, so anything else listening (a test's
    ``caplog``, an operator's own logging configuration) still sees the lines.
    Idempotent.
    """
    global _handler
    target = logging.getLogger(LOGGER_NAME)
    if _handler is None:
        _handler = AuditLogHandler(level=logging.INFO)
    if _handler not in target.handlers:
        target.addHandler(_handler)
    if target.level == logging.NOTSET or target.level > logging.INFO:
        target.setLevel(logging.INFO)
