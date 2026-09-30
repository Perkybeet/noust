# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/ens`` (:mod:`noust.web.api.ens`).

The compliance check and its report list accounts, tokens and how the server
is exposed: ``compliance.read``, which ``admin``, ``security`` and ``auditor``
hold (ENS review §4.2.2). The profile's baseline is plain documentation any
signed-in principal may read. The inventory is read like the applications it
describes, and changed by whoever may configure them. The access review lists
the accounts (``accounts.read``) and is attested by the security officer
(``accounts.manage``).
"""

from __future__ import annotations

from noust.web.permissions import Permission

_PREFIX = "/api/ens"

ROUTES: dict[tuple[str, str], str] = {
    ("GET", f"{_PREFIX}/check"): Permission.COMPLIANCE_READ,
    ("GET", f"{_PREFIX}/report"): Permission.COMPLIANCE_READ,
    ("GET", f"{_PREFIX}/incident"): Permission.COMPLIANCE_READ,
    ("GET", f"{_PREFIX}/profile"): Permission.SETTINGS_READ,
    ("GET", f"{_PREFIX}/inventory"): Permission.APPS_READ,
    ("PUT", f"{_PREFIX}/inventory/{{domain}}"): Permission.APPS_MANAGE,
    # The review lists accounts (accounts.read: security, auditor); attesting
    # it is the security officer's decision (accounts.manage).
    ("GET", f"{_PREFIX}/access-review"): Permission.ACCOUNTS_READ,
    ("POST", f"{_PREFIX}/access-review"): Permission.ACCOUNTS_MANAGE,
}
