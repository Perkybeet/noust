# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The clock the session, token and elevation logic read.

``_now`` is a module attribute and is looked up when it is called: a test moves time by
replacing it in each module that imports it (see ``tests/web_auth_clock.py``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone

#: Indirection over time.time(), so a test can advance a fake clock to expire
#: an elevation window without perturbing session expiry, which is computed
#: from the real wall clock elsewhere in this module.
_now: Callable[[], float] = time.time


def utcnow() -> datetime:
    """
    Return the current time as an aware UTC datetime.

    Returns:
        The current UTC time.
    """
    return datetime.now(timezone.utc)
