# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/overview`` (:mod:`noust.web.api.overview`).

The Overview is the server's own look at itself (applications, deployments,
certificates, backups, disk, updates, what needs attention), so it is read with
the permission that reads the server. It names no secret and no process command
line.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/overview"): Permission.SERVER_READ,
    # What needs this person: the Overview's list for whoever reads the server, and the rest of it
    # (requests to decide, a newer Noust, recipes behind) for whoever holds what each asks for. The
    # handler narrows it, as it narrows the list of approvals, so everyone signed in may ask.
    ("GET", "/api/attention"): Permission.SELF,
}
