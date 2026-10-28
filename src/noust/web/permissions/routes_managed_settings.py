# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/managed-settings`` (:mod:`noust.web.api.managed_settings`).

What a central manages on a server is read like any other setting (``settings.read``). Recording
that the central applied one, letting one go and asking for one to be put back are changes to how
the server's settings are managed (``settings.manage``); the first two are, besides, the central's
alone, which the handler checks by the signature of the push and not by a permission, since the
permission is what a person's role would give.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/managed-settings"): Permission.SETTINGS_READ,
    ("PUT", "/api/managed-settings/{resource}/{name}"): Permission.SETTINGS_MANAGE,
    ("DELETE", "/api/managed-settings/{resource}/{name}"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/managed-settings/{resource}/{name}/restore"): Permission.SETTINGS_MANAGE,
}
