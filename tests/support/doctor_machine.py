# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a test that describes a server to the doctor says about the store.

The doctor reads a machine through :class:`~noust.managers.setup_doctor.DoctorEnvironment`, and a
test describes the machine's size, its ports and its programs instead of reading the laptop it runs
on. The store is one more fact of the machine: the suite keeps every test's own store in its own
directory (``isolated_store_paths``), which is never the state directory of a server, so a report
collected without describing it says that the store is outside it (item 194) and counts a warning
no test about something else asked for. Whoever builds an environment for a server gives it this.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from noust.managers.store_location import StoreLocation

__all__ = ["store_in_its_place"]


def store_in_its_place(directory: Path) -> Callable[[], StoreLocation]:
    """
    Describe a server whose store is where a server keeps it: in the state directory.

    Args:
        directory: A directory of the test's own; the state directory is made of a name below it.

    Returns:
        What ``DoctorEnvironment.store_location`` is given.
    """
    state = directory / "state"
    return lambda: StoreLocation(state / "noust.db", state)
