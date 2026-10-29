# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the name of a fleet token says of its central.

A server gives each central its own token, named ``fleet-<central>`` (and ``fleet-<central>.<n>``
when it is issued again). The name is how the server tells which central is calling
(:mod:`noust.fleet.central_calls`) and how it names the one that acted for a person in its audit
trail (:mod:`noust.core.audit.actor`); it lives here, below both, so there is one definition.
"""

from __future__ import annotations

import re

__all__ = ["central_of_token"]

#: A central's fleet token: ``fleet-<central>`` or ``fleet-<central>.<n>``. The one definition of
#: what a token's name says of its central.
_FLEET_TOKEN = re.compile(r"fleet-([a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)(?:\.[0-9]+)?")


def central_of_token(token_name: str | None) -> str | None:
    """
    Args:
        token_name: The name of the token a request presented.

    Returns:
        The central it belongs to (``fleet-nas.2`` is ``nas``); None when it is not a fleet token.
    """
    if not token_name:
        return None
    match = _FLEET_TOKEN.fullmatch(token_name)
    return match.group(1) if match else None
