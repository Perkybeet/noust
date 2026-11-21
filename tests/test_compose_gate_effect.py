# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Noust says of a stack is what systemd will do when it starts it.

A unit that has the line that runs the gate in its file can still start without it: an addition of the
unit (``<unit>.service.d/*.conf``) that assigns ``ExecStartPre=`` to nothing forgets every command the file
gave for it, and the same goes for ``ExecReload=``. Noust asks systemd what it runs after it writes a unit,
at every pass of the console and whenever ``noust health`` looks, says so when the check is not part of it,
never touches the addition, and takes the warning back as soon as the operator mends the file. Where there
is no systemd it asks nothing, and where it cannot ask it says that and never that the check is in place.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import runner as runner_module
from noust.core.config import Config
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import NoustStore
from noust.deployers import compose_upgrade
from noust.managers import unit_execution
from noust.managers.service_manager import ServiceManager
from tests.support.compose_gate_rig import (  # noqa: F401
    Machine,
    add_drop_in,
    lines,
    machine,
    make_stack,
    store,
    unit_of,
    units,
)

# ruff: noqa: F811

SHOP = "shop.example.com"

#: What an operator wrote before 3.2.1 so that starting the unit did not download images.
NO_PULL = "[Service]\nExecStartPre=\n"
NO_RELOAD = "[Service]\nExecReload=\n"


def bring(store: NoustStore, machine: Machine) -> list[compose_upgrade.Outcome]:
    return compose_upgrade.bring_up_to_date(store=store, runner=machine)


def says(store: NoustStore, machine: Machine) -> list[str]:
    return [
        line
        for line in compose_upgrade.attention(store=store, runner=machine)
        if "starts without the check" in line or "could not tell whether the check" in line
    ]


def shows(machine: Machine) -> list[tuple[str, ...]]:
    return [call for call in machine.calls if call[:2] == ("systemctl", "show")]


def reloads(machine: Machine) -> int:
    return sum(1 for call in machine.calls if call[:2] == ("systemctl", "daemon-reload"))


class TestAnAdditionThatEmptiesWhatRunsBeforeTheStart:
    def test_the_line_is_written_and_the_pass_says_systemd_will_not_run_it(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        path = add_drop_in(units, SHOP, "10-no-pull.conf", NO_PULL)

        outcomes = bring(store, machine)

        assert [(o.state, o.unit, o.recorded) for o in outcomes] == [("overridden", True, True)]
        assert str(path) in outcomes[0].detail
        assert lines(unit_of(units, SHOP), "ExecStartPre")
        assert path.read_text() == NO_PULL

    def test_it_is_not_a_stack_left_waiting_and_a_second_pass_writes_nothing(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.core import compose_acceptance

        make_stack(tmp_path, store, units, machine, SHOP)
        add_drop_in(units, SHOP, "10-no-pull.conf", NO_PULL)
        bring(store, machine)
        written = unit_of(units, SHOP)
        reloaded = reloads(machine)

        again = bring(store, machine)

        assert [o.state for o in again] == ["overridden"]
        assert compose_acceptance.note_of(SHOP, store=store) is None
        assert unit_of(units, SHOP) == written
        assert reloads(machine) == reloaded

    def test_health_says_which_addition_and_what_to_do(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        path = add_drop_in(units, SHOP, "10-no-pull.conf", NO_PULL)
        bring(store, machine)

        said = says(store, machine)

        assert len(said) == 1
        assert said[0].startswith(f"{SHOP}: this stack starts without the check of its start")
        assert f"an addition to its unit ({path})" in said[0]
        assert "(ExecStartPre=)" in said[0]
        assert "systemctl daemon-reload" in said[0]
        assert f"noust setup compose-gate --app {SHOP}" in said[0]

    def test_what_noust_starts_itself_is_still_judged(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.deployers import compose_gate

        app = make_stack(tmp_path, store, units, machine, SHOP)
        add_drop_in(units, SHOP, "10-no-pull.conf", NO_PULL)
        bring(store, machine)
        (Path(app.app_path) / ".env").write_text("DATA=/\n")

        assert not compose_gate.judge_app(app, store=store).allowed

    def test_the_operator_mends_the_addition_and_the_next_pass_sees_the_check_in_place(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        path = add_drop_in(units, SHOP, "10-no-pull.conf", NO_PULL)
        bring(store, machine)
        assert says(store, machine)

        path.write_text("[Service]\n")
        mended = bring(store, machine)

        assert [o.state for o in mended] == ["unchanged"]
        assert not says(store, machine)

    def test_an_addition_that_puts_another_command_in_its_place_replaces_the_check_too(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        add_drop_in(
            units, SHOP, "10-mine.conf", "[Service]\nExecStartPre=\nExecStartPre=/usr/bin/true\n"
        )

        assert [o.state for o in bring(store, machine)] == ["overridden"]

    def test_an_addition_that_adds_a_command_and_forgets_none_leaves_the_check_in_place(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        add_drop_in(units, SHOP, "10-mine.conf", "[Service]\nExecStartPre=/usr/bin/true\n")

        assert [o.state for o in bring(store, machine)] == ["done"]
        assert not says(store, machine)

    def test_an_addition_that_comes_before_the_one_that_empties_it_is_judged_as_systemd_does(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # Additions are read by the order of their names: the empty assignment of 20- forgets 10-'s.
        make_stack(tmp_path, store, units, machine, SHOP)
        add_drop_in(units, SHOP, "10-mine.conf", "[Service]\nExecStartPre=/usr/bin/true\n")
        add_drop_in(units, SHOP, "20-no-pull.conf", NO_PULL)

        assert [o.state for o in bring(store, machine)] == ["overridden"]

    def test_the_sentence_names_every_addition_systemd_read(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        first = add_drop_in(units, SHOP, "10-mine.conf", "[Service]\nEnvironment=A=b\n")
        second = add_drop_in(units, SHOP, "20-no-pull.conf", NO_PULL)
        bring(store, machine)

        said = says(store, machine)

        assert str(first) in said[0] and str(second) in said[0]


class TestAnAdditionThatEmptiesTheReload:
    def test_an_empty_reload_is_said_with_its_directive(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        add_drop_in(units, SHOP, "10-no-reload.conf", NO_RELOAD)

        outcomes = bring(store, machine)

        assert [o.state for o in outcomes] == ["overridden"]
        said = says(store, machine)
        assert "(ExecReload=)" in said[0]

    def test_both_emptied_are_both_named(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        add_drop_in(units, SHOP, "10-both.conf", "[Service]\nExecStartPre=\nExecReload=\n")
        bring(store, machine)

        said = says(store, machine)

        assert "ExecStartPre=" in said[0] and "ExecReload=" in said[0]

    def test_a_reload_whose_check_comes_after_the_rebuild_is_not_in_place(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        bring(store, machine)
        unit = unit_of(units, SHOP)
        gate_line = lines(unit, "ExecReload")[0]
        rebuild = lines(unit, "ExecReload")[1]
        add_drop_in(
            units,
            SHOP,
            "10-reorder.conf",
            f"[Service]\nExecReload=\n{rebuild}\n{gate_line}\n",
        )

        assert [o.state for o in bring(store, machine)] == ["overridden"]


class TestWhenSystemdCannotBeAsked:
    def test_a_systemctl_that_fails_is_never_a_check_in_place(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        machine.systemd_fails = True

        outcomes = bring(store, machine)

        assert [(o.state, o.unit) for o in outcomes] == [("unverified", True)]
        assert "Failed to connect to bus" in outcomes[0].detail

    def test_health_says_it_could_not_tell_and_how_to_look(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        bring(store, machine)
        machine.systemd_fails = True

        said = says(store, machine)

        assert len(said) == 1
        assert said[0].startswith(
            f"{SHOP}: Noust could not tell whether the check of its start runs"
        )
        assert "Failed to connect to bus" in said[0]
        assert "systemctl show shop-example-com.service -p ExecStartPre" in said[0]

    def test_it_leaves_no_note_and_the_next_pass_asks_again(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        from noust.core import compose_acceptance

        make_stack(tmp_path, store, units, machine, SHOP)
        machine.systemd_fails = True
        bring(store, machine)
        assert compose_acceptance.note_of(SHOP, store=store) is None

        machine.systemd_fails = False

        assert [o.state for o in bring(store, machine)] == ["unchanged"]
        assert not says(store, machine)

    def test_an_answer_with_nothing_in_it_is_not_a_yes(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        # What systemd says of a unit it has not loaded.
        make_stack(tmp_path, store, units, machine, SHOP)
        machine.systemd_silent = True

        assert [o.state for o in bring(store, machine)] == ["unverified"]

    def test_a_unit_systemd_has_not_reloaded_is_said_with_the_way_to_reload_it(
        self, tmp_path: Path, store: NoustStore, units: Path, machine: Machine
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)
        machine.loaded["shop-example-com.service"] = "[Service]\nExecStart=/usr/bin/true\n"

        outcomes = bring(store, machine)

        assert [o.state for o in outcomes] == ["unverified"]
        assert "systemctl daemon-reload" in outcomes[0].detail

    def test_setup_exits_one_for_what_it_could_not_tell_and_says_it(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from noust.cli.app import main

        make_stack(tmp_path, store, units, machine, SHOP)
        machine.systemd_fails = True

        code = main(["setup", "compose-gate"])

        out = capsys.readouterr().out
        assert code == 1
        assert f"{SHOP}: could not tell whether systemd runs the check" in out


class TestWhatSetupSaysOfAnOverriddenStack:
    def test_it_names_the_addition_counts_the_stack_and_does_not_fail(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from noust.cli.app import main

        make_stack(tmp_path, store, units, machine, SHOP)
        path = add_drop_in(units, SHOP, "10-no-pull.conf", NO_PULL)

        code = main(["setup", "compose-gate"])

        out = capsys.readouterr().out
        assert code == 0
        assert f"{SHOP}: unit written with the check, but an addition of the unit ({path})" in out
        assert "1 stack(s) start without the check" in out


class TestWithoutSystemd:
    @pytest.fixture
    def container_host(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
        Config.reset_instance()
        yield
        # The singleton keeps what it read: the next test reads the environment it has.
        monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
        monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
        Config.reset_instance()

    def test_where_docker_keeps_the_stack_alive_nothing_is_asked_of_systemd(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        container_host: None,
    ) -> None:
        make_stack(tmp_path, store, units, machine, SHOP)

        outcomes = bring(store, machine)

        assert not shows(machine)
        assert not [o for o in outcomes if o.state in ("overridden", "unverified")]
        assert not says(store, machine)
        assert not shows(machine)


class TestTheQuestion:
    ARGV = [
        "systemctl",
        "show",
        "shop-example-com.service",
        "-p",
        "ExecStartPre",
        "-p",
        "ExecReload",
        "-p",
        "ExecStart",
        "-p",
        "DropInPaths",
    ]

    def test_it_is_a_declared_read_only_probe_so_a_dry_run_asks_it_for_real(self) -> None:
        assert runner_module.is_read_only(self.ARGV)

    def test_it_asks_exactly_that_with_a_deadline(self, units: Path) -> None:
        class Spy(FakeRunner):
            seen: tuple[tuple[str, ...], dict[str, Any]] = ((), {})

            def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
                self.seen = (tuple(str(word) for word in argv), kwargs)
                return CommandResult(
                    tuple(self.seen[0]),
                    0,
                    "ExecStart={ path=/usr/bin/true ; argv[]=/usr/bin/true ; ignore_errors=no }\n"
                    "DropInPaths=\n",
                    "",
                )

        spy = Spy()
        (units / "shop-example-com.service").write_text("# Generated by Noust\n[Service]\n")
        services = ServiceManager(runner=spy)

        seen = unit_execution.read_execution(services, "shop-example-com")

        assert seen.problem is None
        assert spy.seen[0] == tuple(self.ARGV)
        assert 0 < spy.seen[1]["timeout"] <= 60

    def test_a_systemctl_that_does_not_answer_in_time_is_a_problem_not_an_empty_unit(
        self, units: Path
    ) -> None:
        class Slow(FakeRunner):
            def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
                return CommandResult(tuple(str(word) for word in argv), -9, "", "", timed_out=True)

        (units / "shop-example-com.service").write_text("# Generated by Noust\n[Service]\n")

        seen = unit_execution.read_execution(ServiceManager(runner=Slow()), "shop-example-com")

        assert seen.problem is not None and "did not answer" in seen.problem
        assert not seen.pre and not seen.start

    def test_a_machine_without_systemctl_is_a_problem_too(self, units: Path) -> None:
        class Bare(FakeRunner):
            def exists(self, program: str) -> bool:
                return False

        (units / "shop-example-com.service").write_text("# Generated by Noust\n[Service]\n")

        seen = unit_execution.read_execution(ServiceManager(runner=Bare()), "shop-example-com")

        assert seen.problem is not None and "systemctl" in seen.problem

    def test_a_name_that_is_not_a_unit_is_refused_before_anything_is_asked(self) -> None:
        from noust.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            unit_execution.read_execution(ServiceManager(runner=FakeRunner()), "a b; reboot")

    def test_the_commands_are_read_as_what_systemd_prints(self, units: Path) -> None:
        class Prints(FakeRunner):
            def run(self, argv: Any, **kwargs: Any) -> CommandResult:  # type: ignore[override]
                return CommandResult(
                    tuple(str(word) for word in argv),
                    0,
                    "ExecStartPre={ path=/bin/sh ; argv[]=/bin/sh -c [ ! -x /usr/bin/noust ] || exec /usr/bin/noust app compose gate shop.example.com ; ignore_errors=no ; start_time=[n/a] ; pid=0 }\n"
                    "ExecStart={ path=/usr/bin/docker ; argv[]=/usr/bin/docker compose up -d --remove-orphans ; ignore_errors=no ; pid=0 }\n"
                    "DropInPaths=/etc/systemd/system/a.service.d/10-no-pull.conf /run/systemd/system/a.service.d/20.conf\n",
                    "",
                )

        services = ServiceManager(runner=Prints())
        (units / "shop-example-com.service").write_text("# Generated by Noust\n[Service]\n")

        seen = unit_execution.read_execution(services, "shop-example-com")

        assert seen.problem is None
        assert seen.pre == (
            "/bin/sh -c [ ! -x /usr/bin/noust ] || exec /usr/bin/noust app compose gate shop.example.com",
        )
        assert seen.start == ("/usr/bin/docker compose up -d --remove-orphans",)
        assert seen.drop_ins == (
            "/etc/systemd/system/a.service.d/10-no-pull.conf",
            "/run/systemd/system/a.service.d/20.conf",
        )


#: A unit as it was written before 3.2.1, which nobody has deployed since: it pulls before it starts.
UNIT_BEFORE_321 = """# Systemd service for Docker Compose: shop-example-com
# Generated by Noust

[Unit]
Description=Docker Compose app: shop.example.com
Documentation=https://github.com/Perkybeet/noust
After=network.target docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=root
WorkingDirectory={path}

# Environment
Environment="MY_OWN=kept"
Environment="COMPOSE_FILE=docker/compose.yml"
Environment="COMPOSE_FILE=docker/compose.yml"

# Commands
ExecStartPre=/usr/bin/docker compose{project} pull --ignore-pull-failures
ExecStart=/usr/bin/docker compose{project} up -d --remove-orphans
ExecStop=/usr/bin/docker compose{project} down
ExecReload=/usr/bin/docker compose{project} up -d --remove-orphans --build

# Timeouts
TimeoutStartSec=300

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=shop-example-com

[Install]
WantedBy=multi-user.target
"""


class TestAUnitWrittenBefore321:
    @pytest.mark.parametrize("project", ["", " -p shop-example-com"])
    def test_the_check_goes_between_the_pull_and_the_start(self, project: str) -> None:
        text = UNIT_BEFORE_321.format(path="/x", project=project)

        new = compose_upgrade.with_gate(
            text, domain=SHOP, executable="/usr/bin/noust", project="shop-example-com"
        )

        pre = lines(new, "ExecStartPre")
        assert len(pre) == 2
        assert (
            pre[0] == f"ExecStartPre=/usr/bin/docker compose{project} pull --ignore-pull-failures"
        )
        assert "app compose gate shop.example.com --project shop-example-com" in pre[1]
        assert new.index(pre[1]) < new.index("ExecStart=")

    @pytest.mark.parametrize("project", ["", " -p shop-example-com"])
    def test_the_pull_is_not_given_a_project_it_did_not_have(self, project: str) -> None:
        text = UNIT_BEFORE_321.format(path="/x", project=project)

        new = compose_upgrade.with_gate(
            text, domain=SHOP, executable="/usr/bin/noust", project="shop-example-com"
        )

        assert lines(new, "ExecStartPre")[0].endswith("pull --ignore-pull-failures")
        assert ("-p shop-example-com pull" in new) == bool(project)

    @pytest.mark.parametrize("project", ["", " -p shop-example-com"])
    def test_a_second_pass_changes_nothing_and_taking_the_check_out_leaves_the_pull(
        self, project: str
    ) -> None:
        text = UNIT_BEFORE_321.format(path="/x", project=project)
        once = compose_upgrade.with_gate(
            text, domain=SHOP, executable="/usr/bin/noust", project="shop-example-com"
        )

        twice = compose_upgrade.with_gate(
            once, domain=SHOP, executable="/usr/bin/noust", project="shop-example-com"
        )

        plain = compose_upgrade.without_gate(once)
        assert twice == once
        assert lines(plain, "ExecStartPre") == lines(text, "ExecStartPre")
        assert " app compose gate " not in plain

    def test_the_variable_written_twice_stays_written_twice(self) -> None:
        text = UNIT_BEFORE_321.format(path="/x", project="")

        new = compose_upgrade.with_gate(
            text, domain=SHOP, executable="/usr/bin/noust", project="shop-example-com"
        )

        assert lines(new, "Environment") == lines(text, "Environment")
        assert len(lines(new, "Environment")) == 3

    @pytest.mark.parametrize("project", ["", " -p shop-example-com"])
    def test_a_pass_over_the_server_brings_it_under_the_check_and_systemd_runs_both(
        self,
        tmp_path: Path,
        store: NoustStore,
        units: Path,
        machine: Machine,
        project: str,
    ) -> None:
        app = make_stack(tmp_path, store, units, machine, SHOP)
        # The stack of this test keeps its file where Compose looks first: the variable the real unit has
        # twice is covered above, on the text.
        body = UNIT_BEFORE_321.format(path=app.app_path, project=project)
        (units / "shop-example-com.service").write_text(
            body.replace('Environment="COMPOSE_FILE=docker/compose.yml"\n', "")
        )

        outcomes = bring(store, machine)

        assert [(o.state, o.unit) for o in outcomes] == [("done", True)]
        assert shows(machine)
        assert not says(store, machine)
        pulls = [
            line
            for line in lines(unit_of(units, SHOP), "ExecStartPre")
            if "pull --ignore-pull-failures" in line
        ]
        assert len(pulls) == 1
