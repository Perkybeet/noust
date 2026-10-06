# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What zero downtime by default must never touch.

An application that already exists keeps how it updates, whatever the server's
default is: a migration moves it onto releases without converting it, and a
static or PHP site, which has no process to start twice, never needs the mode
because activating a release is one atomic rename of ``current``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core import delivery
from noust.core.store import NoustStore
from noust.deployers import base as base_module
from noust.deployers.migrate import migrate, plan_migration
from tests.test_migrate import (
    DOMAIN,
    machine,
    root,
    store,
)

#: pytest resolves these by name; naming them here is what keeps the linter from
#: reading the tests' parameters as redefinitions of unused imports.
FIXTURES = (machine, root, store)


def test_a_migration_moves_an_application_onto_releases_without_converting_it(
    root: Path, store: NoustStore, machine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Moving to releases is a decision of its own; the mode is another, taken explicitly."""
    monkeypatch.setattr(
        delivery, "current", lambda **_k: delivery.DeliverySettings(zero_downtime=True)
    )

    def converted(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("a migration must not turn the mode on")

    monkeypatch.setattr(base_module, "adopt_for_new_app", converted)

    result = migrate(DOMAIN, plan_migration(DOMAIN), trigger="panel")

    assert os.readlink(root / "current") == f"releases/{result.release_id}"
    app = store.get_app(DOMAIN)
    assert app is not None
    assert (app.layout, app.zero_downtime, app.active_color) == ("releases", False, None)
    # And it is exactly what health then suggests.
    from noust.deployers.bluegreen import could_update_without_downtime

    assert could_update_without_downtime(app, store=store) is True
