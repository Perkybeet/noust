# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of Noust DNS (:mod:`noust.web.api.authdns`).

The server's part is the machine's: installing the name server, making it a primary or a
secondary, removing it and reading the key its transfers are signed with change what the host
runs and what port 53 answers, so they are host access (and a central may reach them only on a
server that allows it). The zones and their records, DNSSEC and the secondaries are managed with
the server; reading any of it, the delegation steps and the check included, is a read.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/dns/noust/server"): Permission.SERVER_READ,
    ("POST", "/api/dns/noust/server/install"): Permission.SERVER_HOST_ACCESS,
    ("POST", "/api/dns/noust/server"): Permission.SERVER_HOST_ACCESS,
    ("DELETE", "/api/dns/noust/server"): Permission.SERVER_HOST_ACCESS,
    ("GET", "/api/dns/noust/server/key"): Permission.SERVER_HOST_ACCESS,
    ("POST", "/api/dns/noust/server/secondaries"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/dns/noust/server/secondaries/{address}"): Permission.SERVER_MANAGE,
    ("GET", "/api/dns/noust/zones"): Permission.SERVER_READ,
    ("POST", "/api/dns/noust/zones"): Permission.SERVER_MANAGE,
    ("GET", "/api/dns/noust/zones/{zone}"): Permission.SERVER_READ,
    ("PATCH", "/api/dns/noust/zones/{zone}"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/dns/noust/zones/{zone}"): Permission.SERVER_MANAGE,
    ("POST", "/api/dns/noust/zones/{zone}/records"): Permission.SERVER_MANAGE,
    ("PATCH", "/api/dns/noust/zones/{zone}/records/{record_id}"): Permission.SERVER_MANAGE,
    ("DELETE", "/api/dns/noust/zones/{zone}/records/{record_id}"): Permission.SERVER_MANAGE,
    ("GET", "/api/dns/noust/zones/{zone}/dnssec"): Permission.SERVER_READ,
    ("PUT", "/api/dns/noust/zones/{zone}/dnssec"): Permission.SERVER_MANAGE,
    ("POST", "/api/dns/noust/zones/{zone}/dnssec/ds-published"): Permission.SERVER_MANAGE,
    ("GET", "/api/dns/noust/zones/{zone}/delegation"): Permission.SERVER_READ,
    ("GET", "/api/dns/noust/zones/{zone}/delegation/check"): Permission.SERVER_READ,
}
