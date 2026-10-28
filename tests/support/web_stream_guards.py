# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_stream_guards.py`` that other tests build on."""

from __future__ import annotations


class ConnectedRequest:
    """A request whose client never goes away; see tests/test_web_events.py."""

    async def is_disconnected(self) -> bool:
        """
        Returns:
            Always False.
        """
        return False
