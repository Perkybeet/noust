# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's and the monitor's units are given more files than the system's default (issue 19).

A unit without ``LimitNOFILE=`` gets the system's soft limit of 1024 files, which is what a central's
console ran out of. Both units Noust writes for itself now carry the same number
(:data:`noust.core.descriptors.SERVICE_LIMIT`), a unit written before is rewritten by what already
rewrites them (the package's upgrade runs ``noust web refresh-unit`` and ``noust monitor install``),
and ``noust setup doctor`` names the ones that are still as they were, with the command that writes
each again.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core import descriptors, paths
from noust.core.runner import FakeRunner
from noust.managers import setup_doctor
from noust.managers.service_manager import ServiceManager
from noust.monitor.process_monitor import ProcessMonitor

OLD_WEB = "[Unit]\nDescription=Noust console\n[Service]\nType=simple\nExecStart=/usr/bin/noust web start\n"
OLD_MONITOR = (
    "[Unit]\nDescription=Noust process and resource monitor\n"
    "[Service]\nType=simple\nExecStart=/usr/bin/noust monitor run\nLimitNOFILE=1024\n"
)


def web_unit(runner: FakeRunner) -> str:
    return ServiceManager(runner=runner)._render_unit(
        "noust-web", {"exec_start": "/usr/bin/noust web start --under-systemd"}
    )


def monitor_unit(runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    binary = tmp_path / "bin" / "noust"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary.parent))
    return ProcessMonitor(verbose=False)._unit_content()


class TestTheUnitsThatAreWrittenNow:
    def test_the_console_is_given_the_limit(self, runner: FakeRunner) -> None:
        text = web_unit(runner)

        assert f"LimitNOFILE={descriptors.SERVICE_LIMIT}" in text.splitlines()
        assert descriptors.SERVICE_LIMIT == 65536
        assert not descriptors.has_old_limit(text)

    def test_the_monitor_is_given_the_same_one(
        self, runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = monitor_unit(runner, tmp_path, monkeypatch)

        assert f"LimitNOFILE={descriptors.SERVICE_LIMIT}" in text.splitlines()
        assert not descriptors.has_old_limit(text)

    def test_the_limit_is_in_the_service_section_where_systemd_reads_it(
        self, runner: FakeRunner
    ) -> None:
        text = web_unit(runner)

        service = text.split("[Service]", 1)[1].split("[Install]", 1)[0]
        assert "LimitNOFILE=" in service

    def test_nothing_else_of_the_console_unit_moved(self, runner: FakeRunner) -> None:
        text = web_unit(runner)

        for kept in ("NoNewPrivileges=true", "Restart=on-failure", "SyslogIdentifier=noust-web"):
            assert kept in text


@pytest.fixture
def systemd_dir(tmp_path: Path) -> Path:
    path = tmp_path / "system"
    path.mkdir()
    return path


class TestWhatTheDoctorSays:
    def test_a_unit_written_before_is_named_with_the_command_that_writes_it_again(
        self, systemd_dir: Path
    ) -> None:
        (systemd_dir / f"{paths.WEB_UNIT}.service").write_text(OLD_WEB)
        (systemd_dir / f"{paths.MONITOR_UNIT}.service").write_text(OLD_MONITOR)

        section = setup_doctor.limits_section(systemd_dir)

        assert section is not None
        assert (section.title, section.key) == ("Noust's own units", "limits")
        found = {i.coded.params["unit"]: i for i in section.items if i.coded}
        assert set(found) == {paths.WEB_UNIT, paths.MONITOR_UNIT}
        assert found[paths.WEB_UNIT].fix == "noust web refresh-unit"
        assert found[paths.MONITOR_UNIT].fix == "noust monitor install"
        assert found[paths.WEB_UNIT].status == "warn"
        # A unit with no limit of its own has the system's default; one with a low one says which.
        web, monitor = found[paths.WEB_UNIT].coded, found[paths.MONITOR_UNIT].coded
        assert web is not None and monitor is not None
        assert (web.code, dict(web.params)) == (
            "doctor.limits.default",
            {"unit": paths.WEB_UNIT, "new": str(descriptors.SERVICE_LIMIT)},
        )
        assert (monitor.code, dict(monitor.params)) == (
            "doctor.limits.old",
            {"unit": paths.MONITOR_UNIT, "limit": "1024", "new": str(descriptors.SERVICE_LIMIT)},
        )

    def test_a_unit_that_has_the_limit_is_not_named(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        (systemd_dir / f"{paths.WEB_UNIT}.service").write_text(web_unit(runner))

        assert setup_doctor.limits_section(systemd_dir) is None

    def test_a_unit_that_is_not_there_is_not_a_finding(self, systemd_dir: Path) -> None:
        # A console run as a daemon, a monitor never installed: nothing to rewrite.
        assert setup_doctor.limits_section(systemd_dir) is None

    def test_a_unit_an_operator_gave_more_is_left_alone(self, systemd_dir: Path) -> None:
        (systemd_dir / f"{paths.WEB_UNIT}.service").write_text(OLD_WEB + "LimitNOFILE=1048576\n")

        assert setup_doctor.limits_section(systemd_dir) is None

    @pytest.mark.parametrize("command", ["noust web refresh-unit", "noust monitor install"])
    def test_the_commands_it_gives_are_ones_the_tool_has(self, command: str) -> None:
        result = CliRunner().invoke(cli, [*shlex.split(command)[1:], "--help"])

        assert result.exit_code == 0, result.output

    def test_the_section_is_part_of_the_report(
        self,
        runner: FakeRunner,
        systemd_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.deployers import compose_relay
        from noust.managers.server.requirements import DiskFree, Resources
        from noust.validators import ssh as ssh_module

        (systemd_dir / f"{paths.WEB_UNIT}.service").write_text(OLD_WEB)
        monkeypatch.setattr(setup_doctor, "SYSTEMD_DIR", systemd_dir)
        monkeypatch.setattr(compose_relay, "leftover_relays", lambda: [])
        monkeypatch.setattr(ssh_module, "ssh_key_exists", lambda: (True, "/root/.ssh/id_ed25519"))
        env = setup_doctor.DoctorEnvironment(
            command_exists=lambda program: True,
            run=lambda argv: type("R", (), {"stdout": "x 1.0\n"})(),
            include_ssh=False,
            apps_dir=tmp_path,
            log_dir=tmp_path,
            config_path=tmp_path / "config.yaml",
            resources=lambda: Resources(4096, 0, 2, (DiskFree(str(tmp_path), 51200, 102400),)),
            listeners=lambda: [(80, ("nginx",)), (443, ("nginx",))],
            app_types=lambda: {"python"},
        )

        report = setup_doctor.collect(env)

        assert "limits" in [section.key for section in report.sections]
