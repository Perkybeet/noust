# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/compliance`` (:mod:`noust.web.api.compliance`).

Which frameworks are on, and what forces what, is read by anybody who can read the
settings: it says how the server protects itself, the way the security profile always
did. Changing it is a security setting (``security.manage``): turning a framework on
or a control stricter needs nothing more, and loosening needs sudo mode
(``ensure_elevated`` inside the handler, because only the request says whether it
loosens). The check lists accounts and exposure like ``/api/ens/check``:
``compliance.read``.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/compliance"): Permission.SETTINGS_READ,
    ("POST", "/api/compliance/plan"): Permission.SECURITY_MANAGE,
    ("PUT", "/api/compliance"): Permission.SECURITY_MANAGE,
    ("GET", "/api/compliance/check"): Permission.COMPLIANCE_READ,
}
