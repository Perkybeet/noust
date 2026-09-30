# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audit interface of Noust 3.0, kept for its callers.

About fifty call sites record through
``get_audit_logger().record(action=..., result=..., client_ip=..., actor=...)``
and read through ``.read(...)``. They keep working unchanged: this class is
now a thin face over :class:`~noust.core.audit.log.AuditLog`, so what they
write is chained, flood-protected and shipped like everything else, and
``noust.web.auth`` re-exports it under its old name.

New code calls :func:`noust.core.audit.record` with an
:class:`~noust.core.audit.actor.Actor` instead: it says who acted in a form an
auditor can filter by role and channel, not only by label.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from noust.core.audit.actor import Actor
from noust.core.audit.log import AuditLog

#: 3.0's rotation size, still the size at which the current file is closed.
AUDIT_MAX_BYTES = 5 * 1024 * 1024
AUDIT_BACKUPS = 3


class AuditLogger:
    """
    Append to and read the audit log with the 3.0 call signatures.

    Attributes:
        path: The current log file.
        enabled: Whether anything is written or read.
        log: The log underneath.
    """

    def __init__(
        self,
        path: Path,
        enabled: bool = True,
        max_bytes: int = AUDIT_MAX_BYTES,
        backups: int = AUDIT_BACKUPS,
    ) -> None:
        """
        Open the audit log, creating it 0600 if needed.

        Args:
            path: Log file path.
            enabled: When false, records are dropped.
            max_bytes: Size at which the current file is closed. Closed files
                are no longer deleted by count: they are kept for the
                retention period (``audit.retention_days``).
            backups: Ignored since 3.1, which keeps files by age, not count;
                accepted so existing callers keep working.

        Raises:
            SecurityError: When the log file cannot be created.
        """
        self.path = Path(path)
        self.enabled = enabled
        self.max_bytes = max(1024, max_bytes)
        self.backups = backups
        self.log = AuditLog(self.path, enabled=enabled, rotate_bytes=self.max_bytes, create=True)

    def record(
        self,
        action: str,
        result: str,
        client_ip: str,
        actor: str = "anonymous",
        resource: str | None = None,
        detail: str | None = None,
    ) -> None:
        """
        Append one audit entry.

        Args:
            action: What was attempted, for example ``auth.login``.
            result: Outcome, for example ``success`` or ``denied``.
            client_ip: Address the request came from.
            actor: Who acted, as :func:`noust.web.auth.actor_label` names
                them, or ``anonymous`` before any credential was verified.
            resource: Target of the action, such as an API path.
            detail: Extra context. Must never contain a credential.
        """
        self.log.append(
            action,
            actor=Actor.from_label(actor, source=client_ip),
            label=actor,
            target=resource,
            outcome=result,
            detail=detail,
            strict=False,
        )

    def read(
        self,
        *,
        limit: int = 50,
        before: str | None = None,
        action: str | None = None,
        result: str | None = None,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Read audit entries, newest first, with keyset pagination.

        Args:
            limit: Maximum number of entries to return.
            before: Only entries strictly older than this ``ts`` value.
            action: Only entries with this exact action, when given.
            result: Only entries with this exact result, when given.
            actor: Only entries with this exact actor, when given.

        Returns:
            Up to ``limit`` matching entries, newest first. Empty when
            auditing is disabled.
        """
        if not self.enabled:
            return []
        return self.log.read(limit=limit, before=before, action=action, result=result, actor=actor)
