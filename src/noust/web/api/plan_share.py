# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One credential does not hold the whole server's share of plans (item 171).

A plan looks at the machine for up to a minute, and the server makes :data:`noust.core.plans.engine.MAX_CONCURRENT_PLANS`
of them at once. The built-in assistant asks for them as the person does, so one credential that asks for many at the same time
(a loop of a script, an assistant in several conversations) would leave nobody else a plan: each credential holds at most
:data:`MAX_PER_CREDENTIAL` at once, and is told so in the words the engine already uses for a server that is full.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends

from noust.core.exceptions import ShareHeldError
from noust.core.plans.engine import MAX_CONCURRENT_PLANS
from noust.core.server_text import say
from noust.web.api.auth import get_current_session
from noust.web.auth import actor_label

__all__ = ["MAX_PER_CREDENTIAL", "hold_a_share"]

#: The most plans one credential has being made at once: half of what the server makes.
MAX_PER_CREDENTIAL = max(1, MAX_CONCURRENT_PLANS // 2)

_held: dict[str, int] = {}
_lock = threading.Lock()


def hold_a_share(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> Iterator[None]:
    """
    Hold one of the plans of a credential for as long as its request lasts.

    Args:
        session: The authenticated payload.

    Yields:
        Nothing: the share is held until the request is over.

    Raises:
        ShareHeldError: The credential already has as many plans being made as it may (429).
    """
    who = actor_label(session)
    with _lock:
        if _held.get(who, 0) >= MAX_PER_CREDENTIAL:
            raise ShareHeldError(
                say("plans.credential_busy", limit=MAX_PER_CREDENTIAL), limit=MAX_PER_CREDENTIAL
            )
        _held[who] = _held.get(who, 0) + 1
    try:
        yield
    finally:
        with _lock:
            _held[who] -= 1
            if _held[who] <= 0:
                del _held[who]
