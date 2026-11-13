# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``--plan`` on the commands that have a plan (item 183).

The command prints what the console's "See what it will do" shows for the same operation, from the same
function, and applies nothing. ``--dry-run`` stays what it is.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import webapp
from noust.cli.plan_output import render
from noust.core.plans.shape import (
    PlanBlock,
    PlanCommand,
    PlanConfirmation,
    PlanDiffLine,
    PlanFile,
    PlanNeeds,
    PlanOut,
)
from noust.core.server_text import say


def a_plan() -> PlanOut:
    """An update that has a freeze to confirm, a diff, a check and what it cannot know."""
    return PlanOut(
        operation="app.update",
        subject="shop.example.com",
        effect=say("plans.update.release", domain="shop.example.com", branch="main"),
        commands=(
            PlanCommand(("systemctl", "is-active", "shop"), probe=True),
            PlanCommand(("npm", "run", "build"), cwd="/srv/shop", runs_as="sandbox:noust-build"),
            PlanCommand(("GET", "http://127.0.0.1:3000/"), probe=True, check=True, seconds=30.0),
        ),
        files=(
            PlanFile(
                "/srv/shop/shop.service",
                "modify",
                shown="diff",
                lines=(
                    PlanDiffLine("del", "MemoryMax=256M"),
                    PlanDiffLine("add", "MemoryMax=512M"),
                ),
            ),
        ),
        findings=(say("plans.found.database_stays"),),
        unknown=(say("plans.unknown.build"),),
        needs=PlanNeeds(
            elevation=True,
            confirmations=(
                PlanConfirmation(
                    "frozen",
                    say("plans.confirm.frozen_open", domain="shop.example.com", reason="Friday"),
                    "frozen_ok",
                ),
            ),
        ),
    )


def test_a_plan_is_said_in_order_with_what_is_not_known_and_what_it_will_ask() -> None:
    text = render(a_plan())

    assert text.startswith("Builds shop.example.com from main")
    assert text.index("systemctl is-active shop  (only looks)") < text.index("npm run build")
    assert "GET http://127.0.0.1:3000/  (a check, up to 30 s)" in text
    assert "modify /srv/shop/shop.service" in text
    assert "    - MemoryMax=256M" in text and "    + MemoryMax=512M" in text
    assert "What it does not know:" in text and "Which install and build commands run" in text
    assert "Applying it will ask for:" in text and "Sudo mode" in text and "frozen" in text


def test_what_would_stop_it_is_said_first() -> None:
    plan = PlanOut(
        operation="app.update",
        subject="x",
        effect=say("plans.update.exact", what="commit abc1234", domain="x"),
        blocked=PlanBlock(say("plans.blocked.nothing_new", branch="main", live="abc1234")),
    )

    assert "Refused now: main has nothing new since abc1234" in render(plan)


def test_update_with_plan_prints_the_plan_and_does_not_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: dict[str, object] = {}

    def plan_update(domain: str, **kwargs: object) -> PlanOut:
        asked.update(domain=domain, **kwargs)
        return a_plan()

    monkeypatch.setattr("noust.deployers.plans.update.plan_update", plan_update)
    monkeypatch.setattr(
        webapp, "_update_app", lambda *args, **kwargs: pytest.fail("the update was applied")
    )

    result = CliRunner().invoke(
        root_cli, ["update", "shop.example.com", "--plan", "--commit", "abc1234", "--no-cache"]
    )

    assert result.exit_code == 0, result.output
    assert "Commands, in this order:" in result.output
    assert asked == {
        "domain": "shop.example.com",
        "force": False,
        "no_cache": True,
        "commit": "abc1234",
        "tag": None,
    }


def test_a_plan_does_not_cover_a_new_source_a_branch_or_a_build_only_update() -> None:
    for extra in (["-s", "https://git.example.com/x.git"], ["-b", "next"], ["--build-only"]):
        result = CliRunner().invoke(root_cli, ["update", "shop.example.com", "--plan", *extra])

        assert result.exit_code == 2 and "--plan covers" in result.output


def test_delete_with_plan_asks_for_what_the_command_would_remove_and_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: dict[str, object] = {}

    def plan_delete(domain: str, **kwargs: object) -> PlanOut:
        asked.update(domain=domain, **kwargs)
        return a_plan()

    monkeypatch.setattr("noust.deployers.plans.delete.plan_delete", plan_delete)
    monkeypatch.setattr(
        webapp, "_delete_app", lambda *args, **kwargs: pytest.fail("the application was deleted")
    )

    result = CliRunner().invoke(
        root_cli, ["delete", "shop.example.com", "--plan", "--keep-files", "--remove-volumes"]
    )

    assert result.exit_code == 0, result.output
    # The command's own defaults: it removes files and the certificate unless told to keep them.
    assert asked == {
        "domain": "shop.example.com",
        "remove_files": False,
        "remove_ssl": True,
        "remove_volumes": True,
        "remove_databases": False,
        "remove_adopted_directory": None,
    }
