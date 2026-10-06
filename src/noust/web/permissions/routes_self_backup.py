# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/self-backup`` (:mod:`noust.web.api.self_backup`).

A bundle of Noust's own state holds every secret of the server, encrypted under
a passphrase. Seeing which there are is ``backups.read``; taking, proving and
queueing a run is ``backups.run``; sending one away or deleting one is
``backups.manage``. The schedule is a root timer that carries those secrets to
a destination and keeps the passphrase where the timer can read it, so setting
or removing it is ``root_equivalent``, which asks sudo mode and, when approvals
are on, a second person. A bundle is never downloaded and never restored from
the API: see the module.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/self-backup"): Permission.BACKUPS_READ,
    ("POST", "/api/self-backup"): Permission.BACKUPS_RUN,
    ("PUT", "/api/self-backup/schedule"): Permission.ROOT_EQUIVALENT,
    ("DELETE", "/api/self-backup/schedule"): Permission.ROOT_EQUIVALENT,
    ("POST", "/api/self-backup/run"): Permission.BACKUPS_RUN,
    ("POST", "/api/self-backup/{name}/verify"): Permission.BACKUPS_RUN,
    ("POST", "/api/self-backup/{name}/push"): Permission.BACKUPS_MANAGE,
    ("DELETE", "/api/self-backup/{name}"): Permission.BACKUPS_MANAGE,
}
