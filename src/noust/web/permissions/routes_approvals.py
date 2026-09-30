# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/approvals`` (:mod:`noust.web.api.approvals`).

Everyone signed in may list what they may see and ask how approvals work
here: ``self``, narrowed by the handlers to one's own requests unless one
reads the audit trail or decides. Who may decide is not a permission any role
holds by itself but the policy's call - ``security``, and ``admin`` for
infrastructure when ``approval.approvers`` says so, never the requester -
judged by :class:`noust.core.accounts.approvals.ApprovalManager` on every
decision, in sudo mode.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/approvals"): Permission.SELF,
    ("GET", "/api/approvals/policy"): Permission.SELF,
    ("GET", "/api/approvals/{approval_id}"): Permission.SELF,
    ("POST", "/api/approvals/{approval_id}/approve"): Permission.SELF,
    ("POST", "/api/approvals/{approval_id}/reject"): Permission.SELF,
}
