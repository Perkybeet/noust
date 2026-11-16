# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of the built-in assistant's routes (:mod:`noust.web.api.assistant`).

What the assistant is, and where its data goes, is the server's: reading how it is set up and what it
cost this month is ``settings.read``, and choosing the provider, probing it, turning it on or off and
revoking what it holds is ``settings.manage`` (the first and the last in sudo mode). Talking to it,
reading and deleting one's own conversations, and seeing what it made it do for oneself and the totals of
one's conversation is ``assistant.use``, which an administrator and an operator hold and no team role does.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/assistant/settings"): Permission.SETTINGS_READ,
    ("PUT", "/api/assistant/settings"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/assistant/probe"): Permission.SETTINGS_MANAGE,
    ("POST", "/api/assistant/off"): Permission.SETTINGS_MANAGE,
    ("GET", "/api/assistant/presets"): Permission.SETTINGS_READ,
    # What it costs is the server's, like the settings that limit it: whoever reads them reads it. What the caller
    # made it do and one conversation's totals are the assistant's, and the handler asks for the permission to use
    # it for those (the route is mapped to the least any of them takes).
    ("GET", "/api/assistant/usage"): Permission.SETTINGS_READ,
    ("GET", "/api/assistant/conversations"): Permission.ASSISTANT_USE,
    ("GET", "/api/assistant/conversations/{conversation_id}"): Permission.ASSISTANT_USE,
    ("DELETE", "/api/assistant/conversations/{conversation_id}"): Permission.ASSISTANT_USE,
    ("POST", "/api/assistant/conversations"): Permission.ASSISTANT_USE,
    ("POST", "/api/assistant/conversations/{conversation_id}/turns"): Permission.ASSISTANT_USE,
    ("GET", "/api/assistant/conversations/{conversation_id}/turns"): Permission.ASSISTANT_USE,
    (
        "POST",
        "/api/assistant/conversations/{conversation_id}/turns/{turn_id}/cancel",
    ): Permission.ASSISTANT_USE,
    ("GET", "/api/assistant/conversations/{conversation_id}/proposals"): Permission.ASSISTANT_USE,
    (
        "GET",
        "/api/assistant/conversations/{conversation_id}/proposals/{proposal_id}",
    ): Permission.ASSISTANT_USE,
    (
        "POST",
        "/api/assistant/conversations/{conversation_id}/proposals/{proposal_id}/outcome",
    ): Permission.ASSISTANT_USE,
    (
        "POST",
        "/api/assistant/conversations/{conversation_id}/proposals/{proposal_id}/reject",
    ): Permission.ASSISTANT_USE,
}
