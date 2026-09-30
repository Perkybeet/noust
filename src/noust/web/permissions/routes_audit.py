# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/audit`` (:mod:`noust.web.api.audit`).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/audit"): Permission.AUDIT_READ,
    ("GET", "/api/audit/verify"): Permission.AUDIT_READ,
    ("GET", "/api/audit/status"): Permission.AUDIT_READ,
    ("GET", "/api/audit/events"): Permission.AUDIT_READ,
    ("GET", "/api/audit/export"): Permission.AUDIT_READ,
    ("GET", "/api/audit/reviews"): Permission.AUDIT_READ,
    # Attesting a review adds an event and changes nothing else; the
    # auditor, whose role is exactly this, holds audit.read only.
    ("POST", "/api/audit/reviews"): Permission.AUDIT_READ,
}
