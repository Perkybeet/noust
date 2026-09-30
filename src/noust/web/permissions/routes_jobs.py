# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/jobs`` (:mod:`noust.web.api.jobs`).

Clearing the job history erases activity records, so it belongs to whoever
manages the audit trail, not to whoever produced it (ENS op.exp.8.r4).
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/jobs"): Permission.APPS_READ,
    ("GET", "/api/jobs/active"): Permission.APPS_READ,
    ("POST", "/api/jobs/update"): Permission.APPS_DEPLOY,
    ("POST", "/api/jobs/rollback"): Permission.APPS_DEPLOY,
    ("POST", "/api/jobs/delete"): Permission.APPS_MANAGE,
    ("POST", "/api/jobs/backup"): Permission.BACKUPS_RUN,
    ("POST", "/api/jobs/cert"): Permission.APPS_MANAGE,
    ("DELETE", "/api/jobs/cleanup"): Permission.AUDIT_MANAGE,
    ("GET", "/api/jobs/{job_id}"): Permission.APPS_READ,
    ("GET", "/api/jobs/{job_id}/log"): Permission.APPS_READ,
    ("POST", "/api/jobs/{job_id}/cancel"): Permission.APPS_OPERATE,
}
