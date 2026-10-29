# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of what a central does so that this server deploys from its repositories.

``/api/sources`` is the node's side (:mod:`noust.web.api.source_leases`, :mod:`noust.web.api.source_events`):
the central leases it access to clone with and forwards the events of its repositories. Reading what
it holds is reading applications (``apps.read``). Taking a lease and queueing an update for an
event are what deploying is (``apps.deploy``, which a ``deploy`` ceiling lets through), and both
are, besides, the central's alone, which the handler checks by the fleet token and not by a
permission, since a permission is what a person's role would give.

``/api/fleet/sources`` is the central's: what it leased to each server, the deliveries it forwarded
and what became of them. Reading them is ``fleet.read``; leasing again by hand is ``fleet.manage``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/sources/leases"): Permission.APPS_READ,
    ("PUT", "/api/sources/leases"): Permission.APPS_DEPLOY,
    ("DELETE", "/api/sources/leases"): Permission.APPS_DEPLOY,
    ("POST", "/api/sources/events"): Permission.APPS_DEPLOY,
}
