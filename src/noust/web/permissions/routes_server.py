# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/server`` (:mod:`noust.web.api.server`), without its
security checks, which state their own map.

The split follows what each thing is:

- **Reading** the machine is ``server.read``: updates, disks, swap, the clock,
  the process list. The list of processes leaves command lines out for anyone
  who cannot read secrets, by the handler itself.
- **Changing** it is ``server.manage``: updates, a reboot, cleaning, swap, the
  clock, the host name. Applying operating system updates is deliberately not
  ``server.host_access``: a central at the ``admin`` ceiling updates the systems
  of its nodes (with sudo mode, and never with a reboot it did not ask for),
  and only what changes how the host is reached needs that permission.
- **Shutting down** is ``server.host_access``: a powered-off server cannot be
  started from Noust, only from the provider's panel, so a central does not do
  it unless the node said it may.
- **The journal of any unit** is ``secrets.reveal``: it carries addresses, user
  names and, now and then, somebody else's secret.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/server/summary"): Permission.SERVER_READ,
    ("GET", "/api/server/capabilities"): Permission.SERVER_READ,
    # Updates
    ("GET", "/api/server/updates"): Permission.SERVER_READ,
    ("GET", "/api/server/updates/plan"): Permission.SERVER_READ,
    ("GET", "/api/server/updates/runs"): Permission.SERVER_READ,
    ("GET", "/api/server/updates/runs/{update_id}"): Permission.SERVER_READ,
    ("GET", "/api/server/updates/auto"): Permission.SERVER_READ,
    ("POST", "/api/server/updates/refresh"): Permission.SERVER_MANAGE,
    ("POST", "/api/server/updates/apply"): Permission.SERVER_MANAGE,
    ("POST", "/api/server/updates/repair"): Permission.SERVER_MANAGE,
    ("GET", "/api/server/updates/restarts"): Permission.SERVER_READ,
    ("POST", "/api/server/updates/restarts"): Permission.SERVER_MANAGE,
    ("PUT", "/api/server/updates/auto"): Permission.SERVER_MANAGE,
    # Power
    ("GET", "/api/server/power"): Permission.SERVER_READ,
    ("POST", "/api/server/power/reboot"): Permission.SERVER_MANAGE,
    ("POST", "/api/server/power/shutdown"): Permission.SERVER_HOST_ACCESS,
    ("DELETE", "/api/server/power/scheduled"): Permission.SERVER_MANAGE,
    # Storage
    ("GET", "/api/server/storage"): Permission.SERVER_READ,
    ("GET", "/api/server/storage/analyze/latest"): Permission.SERVER_READ,
    ("GET", "/api/server/storage/docker/images"): Permission.SERVER_READ,
    ("GET", "/api/server/storage/cleanup/plan"): Permission.SERVER_READ,
    # It only measures; nothing on the server changes (item 59), like checks/refresh.
    ("POST", "/api/server/storage/analyze"): Permission.SERVER_READ,
    ("POST", "/api/server/storage/cleanup"): Permission.SERVER_MANAGE,
    # Swap
    ("GET", "/api/server/swap"): Permission.SERVER_READ,
    ("POST", "/api/server/swap"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/server/swap"): Permission.SERVER_MANAGE,
    ("PUT", "/api/server/swap/swappiness"): Permission.SERVER_MANAGE,
    # The system
    ("GET", "/api/server/time"): Permission.SERVER_READ,
    ("GET", "/api/server/clock/timezones"): Permission.SERVER_READ,
    ("PUT", "/api/server/time"): Permission.SERVER_MANAGE,
    ("GET", "/api/server/identity"): Permission.SERVER_READ,
    ("PUT", "/api/server/identity/hostname"): Permission.SERVER_MANAGE,
    ("GET", "/api/server/processes"): Permission.SERVER_READ,
    # The journal
    ("GET", "/api/server/logs"): Permission.SECRETS_REVEAL,
    ("GET", "/api/server/logs/units"): Permission.SERVER_READ,
    ("GET", "/api/server/logs/boots"): Permission.SERVER_READ,
}
