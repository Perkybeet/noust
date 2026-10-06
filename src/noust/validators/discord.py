# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The shape of a Discord id, checked wherever one is saved or used.

One rule for the configuration chokepoint (``noust config set``, the API's
settings endpoints) and for the renderer that turns an id into a mention: an
id is never put into a message unless it is, from its first character to its
last, a snowflake.
"""

from __future__ import annotations

import re

#: A Discord id (a "snowflake"): a 64-bit integer, 17 to 20 digits today.
_SNOWFLAKE = re.compile(r"[0-9]{17,20}")


def validate_discord_id(value: str, what: str = "a Discord id") -> str:
    """
    Check that a value is, in full, a Discord role or user id.

    Args:
        value: The id exactly as it will be used; whoever reads it from what a
            person typed strips it first.
        what: What the id is, for the message (``a Discord role id``).

    Returns:
        The id, unchanged.

    Raises:
        ValueError: When it is anything else: a mention (``<@&123...>``), a
            name, ``@everyone``, or a number of another length. The message
            says how to find a real one.
    """
    text = str(value)
    if not _SNOWFLAKE.fullmatch(text):
        raise ValueError(
            f"{text!r} is not {what}: expected the 17 to 20 digits Discord shows for it. "
            "Turn on Developer Mode in Discord (User Settings, Advanced), then right-click "
            "the role or the person and choose Copy ID."
        )
    return text
