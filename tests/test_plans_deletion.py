# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What deleting an application would delete and what it would leave.
"""

# The fixtures are imported rather than replicated, so there stays one definition of the machine.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.deployers import lifecycle
from noust.deployers.plans.delete import plan_delete
from tests import support  # noqa: F401
from tests.support import app_deletion
from tests.support.app_deletion import apps_directory, machine, store  # noqa: F401  (fixtures)

CANARY = "canary-6b2f9d4e1a7c3085"


def listing(path: Path) -> list[str]:
    """Every path under a directory, with links told from files."""
    return sorted(
        f"{p.relative_to(path)}{'->' + os.readlink(p) if p.is_symlink() else ''}"
        for p in path.rglob("*")
    )


def test_deleting_says_what_goes_and_what_stays_and_deletes_nothing(
    tmp_path: Path, store, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "get_store", lambda: store)
    app_deletion.compose_app(store, tmp_path / "apps" / "shop-example-com")
    root = tmp_path / "apps" / "shop-example-com"
    before = listing(tmp_path)
    app = store.get_app(app_deletion.DOMAIN)
    app.env_vars = {"DB_PASSWORD": CANARY}
    store.update_app(app)
    before_rows = [a.domain for a in store.list_apps()]

    plan = plan_delete(app_deletion.DOMAIN)

    assert listing(tmp_path) == before and root.is_dir()
    assert [a.domain for a in store.list_apps()] == before_rows
    codes = [item.code for item in plan.findings]
    assert {
        "plans.found.delete_files_kept",
        "plans.found.delete_certificate_kept",
        "plans.found.delete_volumes_kept",
    } <= set(codes)
    assert plan.effect.code == "plans.delete.app"
    assert any(c.argv[:2] == ("docker", "compose") and "down" in c.argv for c in plan.commands)
    assert CANARY not in json.dumps(plan.to_dict())


def test_asking_to_remove_more_removes_it_from_what_stays(
    tmp_path: Path, store, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "get_store", lambda: store)
    app_deletion.compose_app(store, tmp_path / "apps" / "shop-example-com")

    plan = plan_delete(app_deletion.DOMAIN, remove_files=True, remove_ssl=True, remove_volumes=True)

    assert not [item for item in plan.findings if item.code.startswith("plans.found.delete_")]
    assert any(f.change == "delete" for f in plan.files)


def test_deleting_what_is_not_deployed_is_an_error(
    tmp_path: Path, store, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.exceptions import NoustError

    with pytest.raises(NoustError, match="not found"):
        plan_delete("nobody.example.com")
