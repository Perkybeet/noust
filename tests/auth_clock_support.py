# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving the console's clock in a test.

Sessions, tokens and the sudo-mode window read the time from ``_now``, which each of the modules
that need it imports from ``noust.web.auth.clock``. A name imported into a module is looked up in
that module, so replacing it takes one assignment per module that reads it: this is the one place
that knows which. ``tests/test_web_auth_package.py`` fails when a module starts to read the clock
and is not listed.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from noust.web.auth import payload, sessions, tokens

#: The modules of ``noust.web.auth`` that read ``_now``.
CLOCK_READERS = (payload, sessions, tokens)


def set_clock(monkeypatch: pytest.MonkeyPatch, now: Callable[[], float]) -> None:
    """
    Make every part of the console's authentication read the time from a function.

    Args:
        monkeypatch: The test's patcher, so the clock is put back when it ends.
        now: Called whenever the code asks what time it is; seconds since the epoch.
    """
    for module in CLOCK_READERS:
        monkeypatch.setattr(module, "_now", now)
