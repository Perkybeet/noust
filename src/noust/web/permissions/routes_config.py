# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of ``/api/config`` (:mod:`noust.web.api.config`).

The console's own access (``web``) and the whole configuration, which carries
it, are security settings. ``PATCH /api/config`` addresses one key in its
body; :mod:`noust.web.permissions.enforce` asks ``security.manage`` for a key
under a security section and ``settings.manage`` for any other.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/config"): Permission.SETTINGS_READ,
    ("PUT", "/api/config"): Permission.SECURITY_MANAGE,
    ("PATCH", "/api/config"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/config/apps-directory"): Permission.SETTINGS_READ,
    ("PUT", "/api/config/apps-directory"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/config/webserver"): Permission.SETTINGS_READ,
    ("PUT", "/api/config/webserver"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/config/backup"): Permission.SETTINGS_READ,
    ("PUT", "/api/config/backup"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/config/ssl"): Permission.SETTINGS_READ,
    ("PUT", "/api/config/ssl"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/config/web"): Permission.SETTINGS_READ,
    ("PUT", "/api/config/web"): Permission.SECURITY_MANAGE,
    ("GET", "/api/config/smtp"): Permission.SETTINGS_READ,
    ("PUT", "/api/config/smtp"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/config/reload"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/config/defaults"): Permission.SETTINGS_READ,
    ("GET", "/api/config/notifications/telegram"): Permission.SETTINGS_READ,
    ("PUT", "/api/config/notifications/telegram"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/config/notifications/{channel}/test"): Permission.APPS_OPERATE,
    ("POST", "/api/config/notifications/telegram/chats"): Permission.SETTINGS_MANAGE,
}
