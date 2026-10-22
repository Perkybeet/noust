# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the terminal and the one-off commands (:mod:`noust.web.api.terminal`).

A shell as an application reads its secrets and changes what runs, so it needs
its own permission, held by administrators like revealing secrets is; the
server's shell is root, and needs another. Everything that reads what the
terminal left (the recordings) is the audit's.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/apps/{domain}/terminal"): Permission.APPS_TERMINAL,
    ("POST", "/api/apps/{domain}/exec"): Permission.APPS_TERMINAL,
    ("POST", "/api/server/terminal"): Permission.SERVER_TERMINAL,
    ("GET", "/api/terminal/status"): Permission.APPS_READ,
    ("GET", "/api/terminal/sessions"): Permission.APPS_TERMINAL,
    ("DELETE", "/api/terminal/sessions/{session_id}"): Permission.APPS_TERMINAL,
    ("GET", "/api/terminal/recordings"): Permission.AUDIT_READ,
    ("GET", "/api/terminal/recordings/{recording_id}"): Permission.AUDIT_READ,
}
