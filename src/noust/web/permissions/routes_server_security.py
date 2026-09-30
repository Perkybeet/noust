# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/server/security`` (:mod:`noust.web.api.server.security`).

Reading the checks, sshd's configuration, the keys, the firewall and the bans
is ``server.read``. Changing how the server is reached - sshd, keys, the
firewall, and applying a check's fix, which is one of those - is
``server.host_access``, which a central holds only where the node allowed it
(``noust.fleet.policy.permits``): a central that could add itself an SSH key
or open the firewall would outlive its own revocation. fail2ban installs and
unbans are ``server.manage``. Accepting a finding as a risk is a security
decision, ``security.manage``: the security officer documents the exception,
not the administrator who would otherwise fix it.
"""

from __future__ import annotations

from noust.web.permissions import Permission

_PREFIX = "/api/server/security"

ROUTES: dict[tuple[str, str], str] = {
    ("GET", _PREFIX): Permission.SERVER_READ,
    ("GET", f"{_PREFIX}/checks"): Permission.SERVER_READ,
    ("POST", f"{_PREFIX}/checks/refresh"): Permission.SERVER_READ,
    ("POST", f"{_PREFIX}/checks/{{check_id}}/fix"): Permission.SERVER_HOST_ACCESS,
    ("GET", f"{_PREFIX}/ssh"): Permission.SERVER_READ,
    ("GET", f"{_PREFIX}/ssh/fixes/{{fix}}"): Permission.SERVER_READ,
    ("POST", f"{_PREFIX}/ssh/fixes/{{fix}}"): Permission.SERVER_HOST_ACCESS,
    ("GET", f"{_PREFIX}/ssh/keys"): Permission.SERVER_READ,
    ("POST", f"{_PREFIX}/ssh/keys"): Permission.SERVER_HOST_ACCESS,
    ("POST", f"{_PREFIX}/ssh/keys/remove"): Permission.SERVER_HOST_ACCESS,
    ("GET", f"{_PREFIX}/firewall"): Permission.SERVER_READ,
    ("POST", f"{_PREFIX}/firewall/rules"): Permission.SERVER_HOST_ACCESS,
    ("DELETE", f"{_PREFIX}/firewall/rules/{{rule_id}}"): Permission.SERVER_HOST_ACCESS,
    ("POST", f"{_PREFIX}/firewall/enable"): Permission.SERVER_HOST_ACCESS,
    ("POST", f"{_PREFIX}/firewall/disable"): Permission.SERVER_HOST_ACCESS,
    ("GET", f"{_PREFIX}/fail2ban"): Permission.SERVER_READ,
    ("POST", f"{_PREFIX}/fail2ban/install"): Permission.SERVER_MANAGE,
    ("POST", f"{_PREFIX}/fail2ban/unban"): Permission.SERVER_MANAGE,
    ("GET", f"{_PREFIX}/changes"): Permission.SERVER_READ,
    ("POST", f"{_PREFIX}/changes/{{change_id}}/confirm"): Permission.SERVER_HOST_ACCESS,
    ("POST", f"{_PREFIX}/changes/{{change_id}}/revert"): Permission.SERVER_HOST_ACCESS,
    ("GET", f"{_PREFIX}/risks"): Permission.SERVER_READ,
    ("PUT", f"{_PREFIX}/risks/{{check_id}}"): Permission.SECURITY_MANAGE,
    ("DELETE", f"{_PREFIX}/risks/{{check_id}}"): Permission.SECURITY_MANAGE,
}
