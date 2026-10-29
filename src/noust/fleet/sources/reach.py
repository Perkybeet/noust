# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether GitHub can deliver to this central, when its servers depend on it.

A server a central manages has no address GitHub could call, so the App's webhook is the central's:
if GitHub cannot reach the central, a push to a repository one of its servers deploys deploys
nothing and nobody is told. The App can be created, installed and used for access without a public
address (the manifest flow goes through the operator's browser), so nothing fails at the moment the
operator sets it up; this is the sentence that says it then, while there is time to fix it.
"""

from __future__ import annotations

import sqlite3

from noust.central import is_hub
from noust.core.exceptions import NoustError
from noust.core.server_text import ServerText, say
from noust.fleet.nodes import NodeManager
from noust.integrations.github.service import github_hooks_url

__all__ = ["manages_servers", "webhook_warnings"]


def manages_servers() -> bool:
    """
    Returns:
        Whether this central has servers whose repositories depend on its webhook: it manages
        one, or it is a hub, whose whole purpose is to.
    """
    if is_hub():
        return True
    try:
        return bool(NodeManager().list())
    except (NoustError, sqlite3.Error):
        return False


def webhook_warnings() -> list[ServerText]:
    """
    Say what keeps GitHub from delivering to this central, when its servers depend on it.

    Returns:
        The sentences; none when this central manages no servers, or GitHub has an address to
        deliver to.
    """
    if github_hooks_url() is not None or not manages_servers():
        return []
    return [
        say(
            "sources.webhook_unreachable",
            command="noust web expose-hooks hooks.example.com",
            config_command="noust config set web.hooks_url https://hooks.example.com/hooks",
        )
    ]
