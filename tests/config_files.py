# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Read ``config.yaml`` as the server does: with the secrets of the notification channels opened.

They are encrypted in the file (checklist K-12), so a test that wants to know what was stored reads
it through the same step :class:`~noust.core.config.Config` does, and a test that wants to know that
no secret is in the file reads the text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from noust.core import channel_secrets


def stored_config(path: Path) -> dict[str, Any]:
    """
    Args:
        path: The configuration file.

    Returns:
        What it holds, the channels' secrets in clear.
    """
    loaded = yaml.safe_load(path.read_text()) or {}
    opened, _problems = channel_secrets.open_sealed(loaded, channel_secrets.key_path(path))
    return opened


def planted(path: str, marker: str) -> str:
    """
    Make a value to plant under a secret setting.

    Args:
        path: The setting's dotted path.
        marker: The text a test looks for afterwards.

    Returns:
        The marker inside the shape the setting is refused without: a URL for a channel's
        webhook, ``Name: value`` for its headers. It stays visible wherever the value leaks.
    """
    if path.endswith("webhook_url"):
        return f"https://example.test/{marker}"
    if path.endswith("webhook_headers"):
        return f"X-Marker: {marker}"
    return marker
