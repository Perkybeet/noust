# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cancelling the update of a monorepo that builds releases (checklist B-05).

The build of a monorepo on the release layout runs in a release of its own while the units serve
from ``current``, so stopping it is as safe as for any other release: the half-built directory is
removed, ``current`` stays where it was and no unit is restarted.
"""

# The monorepo harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import app_lock, holder_of
from noust.core.cancellation import operation_window, request_cancel
from noust.core.exceptions import DeploymentCancelled
from noust.core.runner import CommandCancelled, FakeRunner
from noust.core.store import NoustStore
from tests.test_monorepo_releases import (  # noqa: F401  (pytest resolves fixtures by name)
    DOMAIN,
    FIRST,
    Probe,
    Units,
    as_root,
    deployer,
    no_waiting,
    probe,
    released_monorepo,
    staging,
    store,
)

WHO = "token:ci"


class Stopped(FakeRunner):
    """A runner whose chosen command is the one an operator stops while it runs."""

    def __init__(self, words: tuple[str, ...]) -> None:
        super().__init__()
        self.words = words

    def _arm(self, argv: Any) -> None:
        if all(word in tuple(argv) for word in self.words):
            request_cancel(DOMAIN, by=WHO)
            raise CommandCancelled(f"Cancelled: {' '.join(argv)}")

    def run(self, argv: Any, **kwargs: Any) -> Any:
        self._arm(argv)
        return super().run(argv, **kwargs)

    def stream(self, argv: Any, **kwargs: Any) -> Any:
        self._arm(argv)
        return super().stream(argv, **kwargs)


@pytest.mark.parametrize(("words"), [("pnpm", "install"), ("pnpm", "build")])
def test_a_stopped_build_removes_the_release_and_leaves_what_serves(
    tmp_path: Path,
    store: NoustStore,
    probe: Probe,
    staging: list,
    words: tuple[str, ...],
) -> None:
    root, app = released_monorepo(tmp_path, store)
    units = Units()

    with app_lock(DOMAIN, "update"), operation_window(DOMAIN, "update"):
        with pytest.raises(DeploymentCancelled, match="cancelled by token:ci"):
            deployer(root, Stopped(words), units).update()

    assert os.readlink(root / "current") == f"releases/{FIRST}"
    assert [path.name for path in (root / "releases").iterdir()] == [FIRST]
    assert units.calls == []
    row = store.list_deployments(DOMAIN)[0]
    assert row.cancelled is True and row.cancelled_by == WHO
    assert holder_of(DOMAIN) is None
    assert app.id is not None
    live = [r.id for r in store.list_releases(app.id) if r.status in ("active", "built")]
    assert live == [FIRST]
