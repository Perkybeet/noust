# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What going back would do, said before it does it.

Each test that says what the plan lists also applies the operation for real against the same
machine and compares: a plan that says one thing while the operation does another is the defect
this is held against.
"""

# The fixtures are imported rather than replicated, so there stays one definition of the machine.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from noust.core.applock import LockHolder
from noust.core.exceptions import NoustError, SchemaChangedError
from noust.deployers import lifecycle
from noust.deployers.plans.rollback import plan_rollback
from tests.support.plans_machine import (  # noqa: F401  (fixtures, found by name)
    DOMAIN,
    Systemd,
    git,
    machine,
    root,
    served,
    store,
    two_releases,
    unit_dirs,
)
from tests.support.release_pipeline import active_id

CANARY = "canary-3c9e7a2d5f1b8046"


def deployment_of(served, release: str):
    """The history row that built a release."""
    return next(row for row in served.store.list_deployments(DOMAIN) if row.release_id == release)


def listing(path: Path) -> list[str]:
    """Every path under a directory, with links told from files, to tell if anything moved."""
    return sorted(
        f"{p.relative_to(path)}{'->' + os.readlink(p) if p.is_symlink() else ''}"
        for p in path.rglob("*")
    )


def test_going_back_to_a_release_says_it_is_a_switch_and_what_serves_now(served) -> None:
    plan = plan_rollback(DOMAIN, deployment_of(served, served.first).id)

    assert plan.effect.code == "plans.rollback.instant"
    assert plan.effect.params["release"] == served.first
    assert plan.effect.params["previous"] == served.second
    assert [(f.change, Path(f.path).name, f.destination) for f in plan.files] == [
        ("link", "current", f"releases/{served.first}")
    ]
    codes = [item.code for item in plan.findings]
    assert "plans.found.database_stays" in codes and "plans.found.serving_now" in codes
    assert plan.blocked is None and plan.needs.confirmations == ()


def test_what_the_plan_lists_is_what_applying_it_runs(served) -> None:
    target = deployment_of(served, served.first)
    plan = plan_rollback(DOMAIN, target.id)
    assert served.runner.changes == []

    lifecycle.rollback_to_deployment(DOMAIN, target.id)

    assert active_id(served.root) == served.first
    assert [command.argv for command in plan.commands if not command.probe] == served.runner.changes
    assert served.runner.changes  # the comparison is not between two empty lists


def test_the_plan_leaves_the_application_and_its_history_as_they_were(served) -> None:
    before = listing(served.root)
    rows = len(served.store.list_deployments(DOMAIN))

    plan_rollback(DOMAIN, deployment_of(served, served.first).id)

    assert listing(served.root) == before
    assert active_id(served.root) == served.second
    assert len(served.store.list_deployments(DOMAIN)) == rows
    assert served.runner.changes == []


def test_the_health_check_is_part_of_the_plan_and_the_plan_does_not_ask_it(served) -> None:
    plan = plan_rollback(DOMAIN, deployment_of(served, served.first).id)

    checks = [command for command in plan.commands if command.check]
    assert [command.text for command in checks] == ["GET http://127.0.0.1:3100/"]
    assert checks[0].probe and checks[0].seconds
    # The order is the operation's: the restart, then the check.
    texts = [command.text for command in plan.commands]
    assert texts.index("systemctl restart rel-example-com.service") < texts.index(
        "GET http://127.0.0.1:3100/"
    )


def test_what_it_cannot_know_is_said(served) -> None:
    plan = plan_rollback(DOMAIN, deployment_of(served, served.first).id)

    assert "plans.unknown.health_older" in [item.code for item in plan.unknown]
    assert plan.unknown[0].params["check"] == "GET http://127.0.0.1:3100/"


def test_going_back_past_a_migration_asks_for_a_confirmation_and_still_shows_what_it_would_do(
    served,
) -> None:
    target = deployment_of(served, served.first)
    newer = deployment_of(served, served.second)
    served.store.record_deployment_hooks(newer.id, hooks=None, schema_changed=True)

    plan = plan_rollback(DOMAIN, target.id)

    assert [(c.kind, c.field) for c in plan.needs.confirmations] == [
        ("schema_changed", "schema_changed_ok")
    ]
    assert plan.needs.confirmations[0].sentence.params["deployments"] == str(newer.id)
    assert any(not command.probe for command in plan.commands)
    # Applying it without that confirmation is refused, as the plan said.
    with pytest.raises(SchemaChangedError):
        lifecycle.rollback_to_deployment(DOMAIN, target.id)


def test_a_deployment_that_cannot_be_gone_back_to_is_a_plan_with_nothing_to_run(served) -> None:
    live = deployment_of(served, served.second)

    plan = plan_rollback(DOMAIN, live.id)

    assert plan.blocked is not None and plan.blocked.sentence.code == "plans.blocked.refused"
    assert "already live" in plan.blocked.sentence.params["reason"]
    assert plan.commands == () and plan.files == ()


def test_a_deployment_of_another_application_is_not_found(served) -> None:
    with pytest.raises(NoustError, match="not found"):
        plan_rollback(DOMAIN, 9999)


def test_what_is_already_running_on_the_application_is_said_as_what_would_stop_it(
    served, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "noust.core.applock.holder_of",
        lambda domain: LockHolder("update", 4242, "2026-11-13T10:00:00Z"),
    )

    plan = plan_rollback(DOMAIN, deployment_of(served, served.first).id)

    assert plan.blocked is not None and plan.blocked.sentence.code == "plans.blocked.busy"
    assert plan.blocked.sentence.params["operation"] == "update"


def test_a_value_the_application_holds_is_not_in_the_plan_wherever_it_could_have_got_in(
    served,
) -> None:
    app = served.store.get_app(DOMAIN)
    app.env_vars = {"DB_PASSWORD": CANARY, "SESSION_SECRET": f"{CANARY}-2"}
    served.store.update_app(app)
    shared = served.root / "shared" / ".env"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(f"DB_PASSWORD={CANARY}\n")

    plan = plan_rollback(DOMAIN, deployment_of(served, served.first).id)

    assert CANARY not in json.dumps(plan.to_dict())


def test_the_plan_is_the_one_shape_the_api_sends(served) -> None:
    body = plan_rollback(DOMAIN, deployment_of(served, served.first).id).to_dict()

    assert set(body) == {
        "operation",
        "subject",
        "effect",
        "commands",
        "files",
        "steps",
        "findings",
        "unknown",
        "needs",
        "blocked",
        "asked_remote",
        "omitted",
    }
    assert body["operation"] == "app.rollback" and body["subject"] == DOMAIN
    assert set(body["effect"]) == {"code", "params", "text", "hint"}
