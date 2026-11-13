# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the plans of operations (:mod:`noust.web.api.plans`, item 183).

A plan is read with the permission to read what the operation would change, because it changes
nothing: it is what a person looks at before deciding to apply it, and what the built-in assistant
shows. Applying the operation keeps the permission it has.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/apps/{domain}/update/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/deployments/{deployment_id}/rollback/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/restart/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/stop/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/start/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/limits/plan"): Permission.APPS_READ,
    ("POST", "/api/certs/{domain}/renew/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/domains/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/domains/{name}/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/delete/plan"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/migrate/plan"): Permission.APPS_READ,
    ("POST", "/api/docker/cleanup/plan"): Permission.DOCKER_READ,
}
