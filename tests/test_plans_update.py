# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What updating an application would do, said before it does it.

The machine is the one of ``test_plans_rollback``: real managers over a runner that can be asked what
it was given, a repository that is the one thing faked. The update is applied for real beside the plan
to compare what each ran.
"""

# The fixtures are imported rather than replicated, so there stays one definition of the machine.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from noust.core import deploy_freeze
from noust.core.exceptions import DeploysFrozenError, NoustError
from noust.deployers import lifecycle
from noust.deployers.plans.update import plan_update
from tests.support.plans_machine import (  # noqa: F401  (fixtures, found by name)
    DOMAIN,
    git,
    machine,
    node_tree,
    root,
    served,
    store,
    two_releases,
    unit_dirs,
)
from tests.support.release_pipeline import GOOD_SERVER, active_id, write_tree

CANARY = "canary-8e1b4d7a2c5f9306"


def same_release(argv: tuple[str, ...]) -> tuple[str, ...]:
    """A command with the time in the name of a release taken out, to compare two runs of it."""
    return tuple(re.sub(r"\d{8}-\d{6}", "<time>", part) for part in argv)


def listing(path: Path) -> list[str]:
    """Every path under a directory, with links told from files, to tell if anything moved."""
    return sorted(
        f"{p.relative_to(path)}{'->' + os.readlink(p) if p.is_symlink() else ''}"
        for p in path.rglob("*")
    )


def publish(served, tmp_path: Path, *, lockfile: bool = True) -> str:
    """Make a new commit the head of the branch; without a lockfile unless asked."""
    tree = node_tree(tmp_path / "next", server=GOOD_SERVER + "// next\n")
    if not lockfile:
        (tree / "package-lock.json").unlink()
    return served.git.publish(tree)


def live_commit(served) -> str:
    """The full commit the serving release was built from."""
    short = served.second.split("-")[2]
    return next(commit for commit in served.git.commits if commit.startswith(short))


def test_an_update_with_something_new_says_what_comes_in_and_how_it_switches(
    served, tmp_path: Path
) -> None:
    new = publish(served, tmp_path)

    plan = plan_update(DOMAIN)

    assert plan.effect.code == "plans.update.release"
    found = {item.code: item.params for item in plan.findings}
    assert found["plans.found.upstream_new"]["remote"] == new[:7]
    assert found["plans.found.serving_now"]["release"] == served.second
    assert plan.blocked is None and plan.needs.confirmations == ()
    assert [f.change for f in plan.files if Path(f.path).name == "current"] == ["link"]


def test_what_the_plan_runs_is_what_applying_it_runs_up_to_what_only_the_repository_decides(
    served, tmp_path: Path
) -> None:
    publish(served, tmp_path, lockfile=False)
    plan = plan_update(DOMAIN)
    assert served.runner.changes == []

    lifecycle.update_app(DOMAIN)

    commands = [command.argv for command in plan.commands if not command.probe]
    # A release is named after the second it is made in, and the plan forecasts it: the two runs
    # may be a second apart.
    assert [same_release(argv) for argv in commands] == [
        same_release(argv) for argv in served.runner.changes
    ]
    assert commands[0] == ("npm", "install")


def test_the_repository_decides_which_install_runs_and_the_plan_says_so(
    served, tmp_path: Path
) -> None:
    """A lockfile in the commit lets the update reuse the dependencies; a plan has not fetched it."""
    publish(served, tmp_path, lockfile=True)

    plan = plan_update(DOMAIN)
    lifecycle.update_app(DOMAIN)

    assert plan.commands[0].argv == ("npm", "install")
    # What the update really did: it took the dependencies of the release that serves, which the
    # unchanged lockfile allows, and installed nothing.
    assert served.runner.changes[0][:2] == ("cp", "-a")
    assert ("npm", "ci") not in served.runner.changes
    assert "plans.unknown.build" in [item.code for item in plan.unknown]


def test_the_phases_the_update_announces_are_the_steps_of_the_plan(served, tmp_path: Path) -> None:
    publish(served, tmp_path)

    plan = plan_update(DOMAIN)

    titles = [step.title for step in plan.steps]
    assert titles[0] == "Installing dependencies" and titles[-1].startswith("Activating release")
    assert sorted(i for step in plan.steps for i in step.commands) == list(
        range(len(plan.commands))
    )


def test_the_health_check_comes_after_the_restart_and_is_not_asked(served, tmp_path: Path) -> None:
    publish(served, tmp_path)

    plan = plan_update(DOMAIN)

    texts = [command.text for command in plan.commands]
    assert texts.index("systemctl restart rel-example-com.service") < texts.index(
        "GET http://127.0.0.1:3100/"
    )
    assert served.machine.probes == []


def test_it_says_what_it_does_not_know(served, tmp_path: Path) -> None:
    publish(served, tmp_path)

    plan = plan_update(DOMAIN)

    codes = [item.code for item in plan.unknown]
    assert {
        "plans.unknown.build",
        "plans.unknown.new_code",
        "plans.unknown.health",
        "plans.unknown.readback",
    } <= set(codes)


def test_the_plan_leaves_the_application_and_its_history_as_they_were(
    served, tmp_path: Path
) -> None:
    publish(served, tmp_path)
    before = listing(served.root)
    everything = sorted(str(p) for p in tmp_path.rglob("*"))
    rows = len(served.store.list_deployments(DOMAIN))

    plan_update(DOMAIN)

    assert listing(served.root) == before
    assert sorted(str(p) for p in tmp_path.rglob("*")) == everything
    assert active_id(served.root) == served.second
    assert len(served.store.list_deployments(DOMAIN)) == rows
    assert served.runner.changes == []


def test_nothing_new_is_what_the_update_would_be_refused_with_unless_forced(
    served, monkeypatch: pytest.MonkeyPatch
) -> None:
    served.git.head = live_commit(served)

    plan = plan_update(DOMAIN)

    assert plan.blocked is not None and plan.blocked.sentence.code == "plans.blocked.nothing_new"
    assert plan_update(DOMAIN, force=True).blocked is None
    assert plan_update(DOMAIN, no_cache=True).blocked is None


def test_a_freeze_is_a_confirmation_the_plan_still_shows_what_would_run(
    served, tmp_path: Path
) -> None:
    publish(served, tmp_path)
    deploy_freeze.create("Black Friday", domain=DOMAIN, until_hours=2)

    plan = plan_update(DOMAIN)

    assert [(c.kind, c.field) for c in plan.needs.confirmations] == [("frozen", "frozen_ok")]
    assert plan.needs.confirmations[0].sentence.params["reason"] == "Black Friday"
    assert any(not command.probe for command in plan.commands)
    with pytest.raises(DeploysFrozenError):
        lifecycle.update_app(DOMAIN)


def test_an_exact_commit_asks_nothing_of_the_repository_and_going_back_asks_to_confirm_the_schema(
    served, tmp_path: Path
) -> None:
    first_commit = next(c for c in served.git.commits if c.startswith(served.first.split("-")[2]))
    newer = next(r for r in served.store.list_deployments(DOMAIN) if r.release_id == served.second)
    served.store.record_deployment_hooks(newer.id, hooks=None, schema_changed=True)

    plan = plan_update(DOMAIN, commit=first_commit)

    assert plan.effect.code == "plans.update.exact"
    assert [c.kind for c in plan.needs.confirmations] == ["schema_changed"]
    assert "plans.unknown.new_code" not in [item.code for item in plan.unknown]


def test_an_application_that_is_not_deployed_is_an_error_not_an_empty_plan(served) -> None:
    with pytest.raises(NoustError, match="not found"):
        plan_update("nobody.example.com")


def test_a_value_the_application_holds_never_reaches_the_plan(served, tmp_path: Path) -> None:
    publish(served, tmp_path)
    app = served.store.get_app(DOMAIN)
    app.env_vars = {"DB_PASSWORD": CANARY, "SESSION_SECRET": f"{CANARY}-2", "MODE": "production"}
    served.store.update_app(app)
    write_tree(served.root / "shared", {".env": f"DB_PASSWORD={CANARY}\nMODE=production\n"})

    plan = plan_update(DOMAIN)

    assert CANARY not in json.dumps(plan.to_dict())


def test_what_is_running_on_the_application_now_is_said(
    served, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.applock import LockHolder

    publish(served, tmp_path)
    monkeypatch.setattr(
        "noust.core.applock.holder_of",
        lambda domain: LockHolder("migration", 1, "2026-11-13T10:00:00Z"),
    )

    plan = plan_update(DOMAIN)

    assert plan.blocked is not None and plan.blocked.sentence.params["operation"] == "migration"
