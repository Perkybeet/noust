# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The image pull of before 3.2.1 is taken out of a stack's unit by the pass that brings it up to date.

Until 3.2.1 the unit pulled images before every start, and 3.2.1 took the line out of the template ("their
unit never pulls at start": a new image of a database downloaded at a restart recreated it with nobody
deciding). A unit nobody has deployed since still has it, and the upgrade to 4.3 left it. The pass takes
that one line out, only from a unit that is Noust's, says so in its summary and in the audit trail, and
changes nothing else.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.audit import catalog
from noust.core.store import App, NoustStore
from noust.deployers import compose_upgrade
from tests.support.compose_gate_rig import (  # noqa: F401
    UNIT_BEFORE_321,
    Machine,
    lines,
    machine,
    make_stack,
    store,
    unit_of,
    units,
)

# ruff: noqa: F811

SHOP = "shop.example.com"
PULL = "ExecStartPre=/usr/bin/docker compose{project} pull --ignore-pull-failures"


def bring(store: NoustStore, machine: Machine) -> list[compose_upgrade.Outcome]:
    return compose_upgrade.bring_up_to_date(store=store, runner=machine)


def pulls(text: str) -> list[str]:
    return [line for line in text.splitlines() if "pull --ignore-pull-failures" in line]


def stack(
    tmp_path: Path,
    store: NoustStore,
    units: Path,
    machine: Machine,
    *,
    project: str = "",
    unit: str = UNIT_BEFORE_321,
) -> App:
    """A stack whose unit is as 3.2.0 wrote it, in a tree Compose can resolve."""
    app = make_stack(tmp_path, store, units, machine, SHOP)
    body = unit.format(path=app.app_path, project=project)
    (units / "shop-example-com.service").write_text(
        body.replace('Environment="COMPOSE_FILE=docker/compose.yml"\n', "")
    )
    return app


class TestWhatThePassDoes:
    @pytest.mark.parametrize("project", ["", " -p shop-example-com"])
    def test_it_takes_the_line_out_with_or_without_the_project(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine, project: str
    ) -> None:
        stack(tmp_path, store, units, machine, project=project)
        assert pulls(unit_of(units, SHOP))

        [outcome] = bring(store, machine)

        assert outcome.state == "done" and outcome.unit and outcome.pull_removed
        assert pulls(unit_of(units, SHOP)) == []

    def test_everything_else_of_the_unit_stays_as_it_was(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # With its project already in every command, which the pass otherwise adds to the three it knows.
        stack(tmp_path, store, units, machine, project=" -p shop-example-com")
        before = unit_of(units, SHOP).splitlines()

        bring(store, machine)

        after = unit_of(units, SHOP).splitlines()
        gone = [line for line in before if line not in after]
        new = [line for line in after if line not in before]
        assert [line for line in gone if "pull --ignore-pull-failures" not in line] == []
        assert len(new) == 2 and all("app compose gate" in line for line in new)
        assert 'Environment="MY_OWN=kept"' in after and "TimeoutStartSec=300" in after

    def test_nothing_is_started_stopped_or_restarted_and_systemd_is_told(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        stack(tmp_path, store, units, machine)

        bring(store, machine)

        calls = {call[:2] for call in machine.calls}
        assert not {("systemctl", "start"), ("systemctl", "restart"), ("systemctl", "stop")} & calls
        assert ("systemctl", "daemon-reload") in calls

    def test_a_second_pass_changes_nothing(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        stack(tmp_path, store, units, machine)
        bring(store, machine)
        written = unit_of(units, SHOP)
        reloaded = [call for call in machine.calls if call[:2] == ("systemctl", "daemon-reload")]

        [again] = bring(store, machine)

        assert again.state == "unchanged" and not again.pull_removed
        assert unit_of(units, SHOP) == written
        assert [
            call for call in machine.calls if call[:2] == ("systemctl", "daemon-reload")
        ] == reloaded

    def test_a_unit_that_4_3_already_put_the_check_in_loses_the_line_too(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        stack(tmp_path, store, units, machine)
        bring(store, machine)
        # What 4.3 wrote on a unit that still pulled: the pull and the check, both.
        text = unit_of(units, SHOP).replace(
            "ExecStartPre=/bin/sh",
            "ExecStartPre=/usr/bin/docker compose pull --ignore-pull-failures\n"
            "ExecStartPre=/bin/sh",
            1,
        )
        (units / "shop-example-com.service").write_text(text)
        assert pulls(text)

        [outcome] = bring(store, machine)

        assert (outcome.state, outcome.unit, outcome.pull_removed) == ("done", True, True)
        assert pulls(unit_of(units, SHOP)) == []
        assert len(lines(unit_of(units, SHOP), "ExecStartPre")) == 1

    @pytest.mark.parametrize(
        "line",
        [
            "ExecStartPre=/usr/bin/docker compose pull",
            "ExecStartPre=/usr/bin/docker compose pull --ignore-pull-failures --quiet",
            "ExecStartPre=/usr/bin/docker compose pull web --ignore-pull-failures",
            "ExecStartPre=/usr/local/bin/docker compose pull --ignore-pull-failures",
            "ExecStartPre=/bin/true",
        ],
    )
    def test_only_that_exact_line_goes(
        self, line: str, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # Something of the operator's that looks like it is not Noust's to take.
        unit = UNIT_BEFORE_321.replace(PULL, line)
        stack(tmp_path, store, units, machine, unit=unit)

        [outcome] = bring(store, machine)

        assert outcome.state == "done" and not outcome.pull_removed
        assert line in unit_of(units, SHOP).splitlines()

    def test_a_unit_that_is_the_operators_keeps_its_pull(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        stack(tmp_path, store, units, machine)
        body = unit_of(units, SHOP).replace("# Generated by Noust\n", "")
        (units / "shop-example-com.service").write_text(body)

        [outcome] = bring(store, machine)

        assert outcome.state == "own" and not outcome.pull_removed
        assert unit_of(units, SHOP) == body

    def test_going_back_does_not_bring_it_back(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        stack(tmp_path, store, units, machine)
        bring(store, machine)

        compose_upgrade.remove_gates(store=store, runner=machine)

        assert pulls(unit_of(units, SHOP)) == []
        assert lines(unit_of(units, SHOP), "ExecStartPre") == []

    def test_a_stack_that_cannot_be_taken_yet_is_left_exactly_as_it_was(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        stack(tmp_path, store, units, machine)
        before = unit_of(units, SHOP)
        machine.fail_config = set(machine.stacks)

        [outcome] = bring(store, machine)

        assert outcome.state == "pending"
        assert unit_of(units, SHOP) == before


class TestWhatItSays:
    def test_it_is_in_the_audit_trail_once_with_the_unit_and_the_line(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []

        def record(event: str, **kwargs: Any) -> None:
            recorded.append((event, kwargs))

        monkeypatch.setattr(audit, "record", record)
        stack(tmp_path, store, units, machine, project=" -p shop-example-com")

        bring(store, machine)
        bring(store, machine)

        assert [event for event, _ in recorded] == ["compose.pull_removed"]
        kwargs = recorded[0][1]
        assert kwargs["target"] == f"app:{SHOP}"
        assert kwargs["details"]["unit"] == "shop-example-com.service"
        assert kwargs["details"]["line"] == (
            "ExecStartPre=/usr/bin/docker compose -p shop-example-com pull --ignore-pull-failures"
        )

    def test_the_event_is_in_the_catalog(self) -> None:
        spec = catalog.EVENTS["compose.pull_removed"]

        assert spec.category == "system" and "pull" in spec.description

    def test_the_summary_of_the_command_says_it(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from noust.cli.app import main

        stack(tmp_path, store, units, machine)

        code = main(["setup", "compose-gate"])

        out = capsys.readouterr().out
        assert code == 0
        assert (
            f"{SHOP}: record written, unit written with the check, and the line that pulled "
            "images at every start (before 3.2.1) taken out"
        ) in out

    def test_the_console_says_it_in_its_log(self, caplog: pytest.LogCaptureFixture) -> None:
        from noust.web.compose_gate_worker import ComposeGateWorker

        worker = ComposeGateWorker(
            lambda: [compose_upgrade.Outcome(SHOP, "done", unit=True, pull_removed=True)],
            first_after=0.0,
            every=3600.0,
        )

        with caplog.at_level(logging.INFO):
            worker.tick()

        assert "image pull" in caplog.text
