# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the deploy keys (:mod:`noust.web.api.deploy_keys`).

Reading a key shows its public half, which is not a secret. Making, assigning,
rotating and removing one is managing the application; importing one and
removing one also ask for sudo mode in the handler, because a private key
travels in or a credential goes away. Forgetting a pinned host key is accepting
that a host's identity changed, which is a change to how this server trusts the
network, so it is the host-access permission; pinning one by a fingerprint decides
whom this server trusts before it has met them, and is the same.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/apps/{domain}/deploy-key"): Permission.APPS_READ,
    ("POST", "/api/apps/{domain}/deploy-key"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/deploy-key/import"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/deploy-key"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/deploy-key/rotate"): Permission.APPS_MANAGE,
    ("DELETE", "/api/apps/{domain}/deploy-key/rotate"): Permission.APPS_MANAGE,
    ("POST", "/api/apps/{domain}/deploy-key/test"): Permission.APPS_MANAGE,
    ("GET", "/api/deploy-keys"): Permission.APPS_READ,
    ("GET", "/api/deploy-keys/hosts"): Permission.APPS_READ,
    ("POST", "/api/deploy-keys/hosts"): Permission.SERVER_HOST_ACCESS,
    ("DELETE", "/api/deploy-keys/hosts/{host}"): Permission.SERVER_HOST_ACCESS,
}
