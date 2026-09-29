# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Record fleet actions taken at a terminal in the same audit log the console writes.

Authorizing a central hands it a credential to this server; adding or
removing a node changes what a central can reach. Each is recorded next to
the console's own entries, so one log answers "who gave access to whom".
"""

from __future__ import annotations

import getpass
import logging

from noust.core.exceptions import SecurityError

logger = logging.getLogger(__name__)


def audit(
    action: str, result: str, *, resource: str | None = None, detail: str | None = None
) -> None:
    """
    Append one entry to the console's audit log, as the operator at this terminal.

    Never pass a credential in ``detail``. A server without the console's
    dependencies has no audit log; the entry goes to the process log instead.

    Args:
        action: Such as ``fleet.authorize``.
        result: ``success`` or ``failure``.
        resource: What was acted on, such as ``central:nas``.
        detail: One sentence of context.
    """
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "unknown"
    try:
        from noust.web.auth import AuditLogger, SecurityConfig
    except ImportError:
        logger.warning(
            "Audit (no console installed): %s %s %s %s", action, result, resource, detail
        )
        return
    config = SecurityConfig()
    try:
        auditor = AuditLogger(
            config.audit_log,
            enabled=config.audit_enabled,
            max_bytes=config.audit_max_bytes,
            backups=config.audit_backups,
        )
    except SecurityError as exc:
        logger.warning(
            "Cannot open the audit log (%s): %s %s %s", exc.message, action, result, detail
        )
        return
    auditor.record(
        action=action,
        result=result,
        client_ip="local",
        actor=f"cli:{user}",
        resource=resource,
        detail=detail,
    )
