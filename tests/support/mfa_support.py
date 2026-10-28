# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Put the console where an account without a second factor is held at the enrolment.

Until 4.0 every account was held; now only a console somebody beyond its own machine can reach
holds one (``noust.core.console_exposure_store.console_published``) or a framework that asks for
it. A test about what the enrolment does for an account that is held says so with this, instead of
leaning on a default that is no longer there.
"""

from __future__ import annotations

from noust.core.console_exposure_store import (
    STATE_ACTIVE,
    ConsoleExposureStore,
    ExposureState,
)


def require_second_factor() -> None:
    """Record that the console is published, in the store the test uses."""
    ConsoleExposureStore().save(
        ExposureState(domain="console.example.com", port=8080, state=STATE_ACTIVE)
    )
