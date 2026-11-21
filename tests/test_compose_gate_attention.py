# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who brings a server's stacks under the gate, and what the operator is told about them.

The monitor does it at its first scan after an upgrade and again while a stack is waiting, ``noust setup
compose-gate`` does it by hand (and takes the gate out again for going back to a version that does not
know it), and ``noust health`` says what is waiting and why, what the gate held back, what was taken over
from before it and which applications run as one project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ServiceError
from noust.core.store import NoustStore
from noust.deployers import compose_gate, compose_upgrade
from tests.support.compose_gate_rig import (  # noqa: F401
    Machine,
    lines,
    machine,
    make_stack,
    store,
    unit_of,
    units,
)

# ruff: noqa: F811

OUTSIDE = "outside.example.com"


class TestWhoRunsIt:
    def test_the_console_takes_the_stacks_over_a_little_after_it_starts(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.web.compose_gate_worker import ComposeGateWorker

        make_stack(tmp_path, store, units, machine, OUTSIDE)
        worker = ComposeGateWorker(
            lambda: compose_upgrade.bring_up_to_date(store=store, runner=machine),
            first_after=0.0,
            every=3600.0,
        )

        worker.tick()

        assert lines(unit_of(units, OUTSIDE), "ExecStartPre")

    def test_the_worker_runs_in_a_thread_of_its_own_and_stops_with_the_console(self) -> None:
        import threading

        from noust.web.compose_gate_worker import ComposeGateWorker

        ran = threading.Event()
        worker = ComposeGateWorker(lambda: ran.set() or [], first_after=0.0, every=3600.0)

        worker.start()
        assert ran.wait(5)
        worker.stop()

        assert worker._thread is not None and not worker._thread.is_alive()

    def test_a_pass_that_fails_is_a_line_of_the_log_and_the_next_one_goes_on(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import sqlite3

        from noust.web.compose_gate_worker import ComposeGateWorker

        def broken() -> list[compose_upgrade.Outcome]:
            raise sqlite3.OperationalError("database is locked")

        worker = ComposeGateWorker(broken, first_after=0.0, every=3600.0)

        worker.tick()

        assert "database is locked" in caplog.text

    def test_the_console_starts_it_and_the_monitor_does_not_touch_units(self) -> None:
        # The monitor runs with the system read-only and promises to modify no file.
        from pathlib import Path as P

        import noust

        root = P(noust.__file__).parent
        assert "compose_gate_worker" in (root / "web" / "server.py").read_text()
        assert "compose_upgrade" not in (root / "monitor" / "process_monitor.py").read_text()

    def test_setup_compose_gate_takes_the_reason_of_a_package_upgrade(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.cli.app import main

        make_stack(tmp_path, store, units, machine, OUTSIDE)

        assert main(["setup", "compose-gate", "--reason", "package upgrade"]) == 0
        assert lines(unit_of(units, OUTSIDE), "ExecStartPre")

    def test_setup_compose_gate_does_the_same_by_hand(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from noust.cli.app import main

        make_stack(tmp_path, store, units, machine, OUTSIDE)

        code = main(["setup", "compose-gate"])

        assert code == 0
        assert f"{OUTSIDE}: record written, unit written with the check" in capsys.readouterr().out
        assert lines(unit_of(units, OUTSIDE), "ExecStartPre")

    def test_setup_compose_gate_says_what_it_left_and_exits_one(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from noust.cli.app import main

        make_stack(tmp_path, store, units, machine, OUTSIDE)
        machine.fail_config = set(machine.stacks)

        code = main(["setup", "compose-gate", "--app", OUTSIDE])

        assert code == 1
        assert "left as it was" in capsys.readouterr().out

    def test_setup_compose_gate_remove_takes_the_check_out(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.cli.app import main

        make_stack(tmp_path, store, units, machine, OUTSIDE)
        main(["setup", "compose-gate"])

        code = main(["setup", "compose-gate", "--remove"])

        assert code == 0
        assert not lines(unit_of(units, OUTSIDE), "ExecStartPre")


class TestWhatHealthSays:
    def test_a_stack_waiting_for_the_step_is_listed_with_why_and_how_to_try_again(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        machine.fail_config = set(machine.stacks)
        compose_upgrade.bring_up_to_date(store=store)

        said = compose_upgrade.attention(store=store)

        assert len(said) == 1
        assert OUTSIDE in said[0] and "has not brought" in said[0]
        assert f"noust setup compose-gate --app {OUTSIDE}" in said[0]

    def test_what_was_taken_over_is_listed_to_be_looked_at(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE, env="DATA=/srv/shared\n")
        compose_upgrade.bring_up_to_date(store=store)

        said = compose_upgrade.attention(store=store)

        assert len(said) == 1 and "taken over as accepted" in said[0]
        assert f"noust app sandbox compose-exception {OUTSIDE}" in said[0]

    def test_a_stack_that_asks_for_nothing_of_the_server_is_not_listed(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "stopped.example.com")
        compose_upgrade.bring_up_to_date(store=store)

        assert compose_upgrade.attention(store=store) == []

    def test_a_held_back_start_is_listed_with_the_ways_out(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        app = make_stack(tmp_path, store, units, machine, "stopped.example.com")
        compose_upgrade.bring_up_to_date(store=store)
        (Path(app.app_path) / ".env").write_text("DATA=/\n")
        compose_gate.judge_app(app, store=store)

        said = compose_upgrade.attention(store=store)

        assert len(said) == 1 and "last start was held back" in said[0]
        assert "noust update stopped.example.com" in said[0]

    def test_two_applications_of_one_project_are_listed_together(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "shop.example.com", compose_dir="docker")
        make_stack(tmp_path, store, units, machine, "evil.example.org", compose_dir="docker")
        compose_upgrade.bring_up_to_date(store=store)

        said = compose_upgrade.attention(store=store)

        assert any("evil.example.org and shop.example.com run as one" in line for line in said)

    def test_the_health_report_carries_them(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.managers import health

        make_stack(tmp_path, store, units, machine, OUTSIDE)
        machine.fail_config = set(machine.stacks)
        compose_upgrade.bring_up_to_date(store=store)
        warnings: list[str] = []

        check = health._check_builds(warnings)

        assert check.status == "warning"
        assert any("has not brought this stack" in line for line in warnings)


MARK = "# Generated by Noust\n"


def made_theirs(units: Path, domain: str) -> None:
    """The operator deletes the first line of the unit's mark: from then on it is theirs."""
    path = units / f"{domain.replace('.', '-')}.service"
    path.write_text(path.read_text().replace(MARK, "", 1))


class TestAUnitTheOperatorMadeTheirs:
    """Noust never rewrites a unit that does not carry its mark, so it puts no gate in one."""

    def test_the_stack_is_taken_over_and_its_unit_is_left_exactly_as_it_is(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        made_theirs(units, OUTSIDE)
        before = unit_of(units, OUTSIDE)

        outcomes = compose_upgrade.bring_up_to_date(store=store, runner=machine)

        assert [(o.state, o.recorded, o.unit) for o in outcomes] == [("own", True, False)]
        assert unit_of(units, OUTSIDE) == before
        assert not any(call[:2] == ("systemctl", "daemon-reload") for call in machine.calls)

    def test_it_is_not_a_stack_left_waiting_and_is_not_tried_again_as_one(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.core import compose_acceptance

        make_stack(tmp_path, store, units, machine, OUTSIDE)
        made_theirs(units, OUTSIDE)

        compose_upgrade.bring_up_to_date(store=store, runner=machine)

        assert compose_acceptance.note_of(OUTSIDE, store=store) is None
        again = compose_upgrade.bring_up_to_date(store=store, runner=machine)
        assert [o.state for o in again] == ["own"]

    def test_noust_health_says_the_stack_starts_without_the_check_and_why(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        made_theirs(units, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)

        said = [
            line
            for line in compose_upgrade.attention(store=store, runner=machine)
            if "starts without the check" in line
        ]

        assert len(said) == 1
        assert said[0].startswith(f"{OUTSIDE}: this stack starts without the check of its start")
        assert "its unit is yours" in said[0]
        assert "does not carry" in said[0]

    def test_a_unit_with_its_mark_is_not_named(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, "stopped.example.com")
        compose_upgrade.bring_up_to_date(store=store, runner=machine)

        assert not [
            line
            for line in compose_upgrade.attention(store=store, runner=machine)
            if "starts without the check" in line
        ]

    def test_what_noust_starts_itself_is_still_judged(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.deployers import compose_gate

        app = make_stack(tmp_path, store, units, machine, OUTSIDE)
        made_theirs(units, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)
        (Path(app.app_path) / ".env").write_text("DATA=/\n")

        assert not compose_gate.judge_app(app, store=store).allowed

    def test_taking_the_gate_out_takes_it_out_of_a_unit_without_the_mark_too(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # The lane before this one put the line in a unit whose first line was gone: it is Noust's line.
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)
        made_theirs(units, OUTSIDE)
        machine.calls.clear()

        changed = compose_upgrade.remove_gates(store=store, runner=machine)

        assert changed == [OUTSIDE]
        text = unit_of(units, OUTSIDE)
        assert not lines(text, "ExecStartPre") and len(lines(text, "ExecReload")) == 1
        assert "Generated by Noust" not in text and 'Environment="MY_OWN=kept"' in text
        assert any(call[:2] == ("systemctl", "daemon-reload") for call in machine.calls)

    def test_taking_it_out_keeps_the_mode_of_the_file(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)
        made_theirs(units, OUTSIDE)
        path = units / f"{OUTSIDE.replace('.', '-')}.service"
        path.chmod(0o640)

        compose_upgrade.remove_gates(store=store, runner=machine)

        assert path.stat().st_mode & 0o777 == 0o640


def as_the_first_builds_wrote(units: Path, domain: str) -> None:
    """Put back the gate lines as the first builds of 4.3 wrote them: the command alone, twice."""
    path = units / f"{domain.replace('.', '-')}.service"
    project = domain.replace(".", "-")
    command = f"/usr/bin/noust app compose gate {domain} --project {project}"
    path.write_text(
        "\n".join(
            f"{line.split('=', 1)[0]}={command}" if " app compose gate " in line else line
            for line in path.read_text().split("\n")
        )
    )


class TestTheFirstFormOfTheLine:
    def test_the_next_pass_gives_it_the_form_that_tolerates_noust_being_removed(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)
        as_the_first_builds_wrote(units, OUTSIDE)
        assert "[ ! -x" not in unit_of(units, OUTSIDE)
        machine.calls.clear()

        outcomes = compose_upgrade.bring_up_to_date(store=store, runner=machine)

        assert [(o.state, o.unit) for o in outcomes] == [("done", True)]
        text = unit_of(units, OUTSIDE)
        assert all("[ ! -x" in line for line in text.split("\n") if " app compose gate " in line)
        assert text.count(" app compose gate ") == 2
        assert any(call[:2] == ("systemctl", "daemon-reload") for call in machine.calls)
        assert [o.state for o in compose_upgrade.bring_up_to_date(store=store, runner=machine)] == [
            "unchanged"
        ]

    def test_taking_the_gate_out_takes_the_first_form_out_of_a_unit_with_its_mark(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)
        as_the_first_builds_wrote(units, OUTSIDE)

        assert compose_upgrade.remove_gates(store=store, runner=machine) == [OUTSIDE]

        text = unit_of(units, OUTSIDE)
        assert " app compose gate " not in text and "Generated by Noust" in text


class TestWhatTakingTheGateOutWillNotTouch:
    def test_a_unit_that_is_a_link_is_left_as_it_is(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)
        path = units / f"{OUTSIDE.replace('.', '-')}.service"
        real = tmp_path / "elsewhere.service"
        real.write_text(path.read_text())
        path.unlink()
        path.symlink_to(real)
        before = real.read_text()

        try:
            compose_upgrade.remove_gates(store=store, runner=machine)
        except (OSError, ServiceError):
            pass

        assert real.read_text() == before

    def test_only_the_lines_of_the_gate_go(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, OUTSIDE)
        compose_upgrade.bring_up_to_date(store=store, runner=machine)
        made_theirs(units, OUTSIDE)
        path = units / f"{OUTSIDE.replace('.', '-')}.service"
        path.write_text(path.read_text() + "ExecStartPre=/usr/bin/true\n# my own note\n")
        expected = "\n".join(
            line for line in path.read_text().split("\n") if " app compose gate " not in line
        )

        compose_upgrade.remove_gates(store=store, runner=machine)

        assert path.read_text() == expected
