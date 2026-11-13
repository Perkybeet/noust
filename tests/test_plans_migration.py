# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What moving an application onto releases would do, in the common shape (item 183).

The move keeps its own plan and its own answer; the common shape says what its rehearsal says and, as
what a plan cannot know, that the commands of the move are not simulated one by one.
"""

# The fixtures are imported rather than replicated, so there stays one definition of the machine.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from noust.core.exceptions import NoustError
from noust.deployers.plans.migrate import plan_migrate
from tests.support.migrate import DOMAIN, machine, root, store  # noqa: F401  (fixtures)


def listing(path: Path) -> list[str]:
    """Every path under a directory, with links told from files."""
    return sorted(
        f"{p.relative_to(path)}{'->' + os.readlink(p) if p.is_symlink() else ''}"
        for p in path.rglob("*")
    )


def test_the_plan_says_what_moves_and_what_stays_and_moves_nothing(
    machine, root: Path, tmp_path: Path
) -> None:
    before = listing(tmp_path)

    plan = plan_migrate(DOMAIN)

    assert listing(tmp_path) == before and not (root / "releases").exists()
    assert plan.operation == "app.migrate" and plan.effect.code == "plans.migrate.app"
    found = {item.code: item.params for item in plan.findings}
    assert found["plans.found.migrate_persistent"]["count"] >= 1
    assert "plans.found.migrate_untracked" in found
    assert "plans.unknown.migrate_steps" in [item.code for item in plan.unknown]


def test_what_the_application_holds_is_not_in_the_plan(machine, root: Path) -> None:
    (root / ".env").write_text("DB_PASSWORD=canary-4d8a1f6c9e2b7305\n")

    plan = plan_migrate(DOMAIN)

    assert "canary-4d8a1f6c9e2b7305" not in json.dumps(plan.to_dict())


def test_an_application_on_releases_already_is_refused_as_the_operation_refuses_it(
    machine, store
) -> None:
    app = store.get_app(DOMAIN)
    app.layout = "releases"
    store.update_app(app)

    with pytest.raises(NoustError):
        plan_migrate(DOMAIN)
