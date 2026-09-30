"""
Tests for ``noust server``.

The command decides nothing: every subcommand is a manager call
(:mod:`noust.managers.server`, tested where it lives) and this file pins the
translation: the arguments that reach the manager, what has to be confirmed
before anything is touched, what needs root, ``--json`` on either side of the
command name, and a refusal becoming exit code 1 with the manager's own words.
The machine is the one made of captured tool outputs, as for the API.
"""

from __future__ import annotations

import json
import types
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner, Result

from noust.cli.app import cli as root_cli
from noust.cli.app import main
from noust.cli.commands import server as server_cli
from noust.core.fs import DryRunFileSystem, RecordingFileSystem
from noust.core.runner import DryRunRunner
from noust.core.store import NoustStore
from noust.managers.server.context import ServerContext
from noust.managers.server.facts import FactCache
from noust.managers.server.updates import UpdateRecord
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    fixture,
    make_machine,
    no_package_manager_running,
    platform_for,
)

pytestmark = pytest.mark.usefixtures("no_package_manager_running")


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "cli.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store):
    """The server made of fixtures, root, and the CLI pointed at it."""
    made = make_machine(tmp_path, monkeypatch, store)
    made.ctx.power._apps_check = lambda runner: []
    monkeypatch.setattr(server_cli, "build_context", lambda: made.ctx)
    monkeypatch.setattr(server_cli, "check_root", lambda: True)
    monkeypatch.setattr(server_cli, "RecordStore", lambda: made.ctx.records)
    return made


def run(args: list[str], *, input: str | None = None) -> Result:
    return CliRunner().invoke(root_cli, args, input=input)


def said(result: Result) -> str:
    """
    Everything a person would have read: the output and, for a refusal, the error.

    ``main()`` turns a Noust error into printed lines and an exit code; invoking
    the group directly leaves it as the exception, whose text is the same.
    """
    error = result.exception
    extra = str(error) if error is not None and not isinstance(error, SystemExit) else ""
    return f"{result.output}\n{extra}"


class TestStatus:
    def test_json_is_the_summary_on_either_side_of_the_command_name(self, machine) -> None:
        after = run(["server", "status", "--json"])
        before = run(["--json", "server", "status"])

        assert after.exit_code == 0, after.output
        body = json.loads(after.output)
        assert body["hostname"] == "vps-1"
        assert body["updates"]["pending"] == 9
        assert json.loads(before.output)["updates"]["security"] == 7

    def test_the_human_report_says_what_needs_attention(self, machine) -> None:
        result = run(["server", "status"])

        assert result.exit_code == 0
        assert "9 pending, 7 security" in result.output
        assert "required" in result.output
        assert "/data" in result.output and "92.0% used" in result.output
        assert "degraded: nginx.service, backup.service" in result.output


class TestUpdates:
    def test_the_listing_puts_security_first_and_reports_kept_back_and_the_reboot(
        self, machine
    ) -> None:
        result = run(["server", "updates", "list"])

        assert result.exit_code == 0, result.output
        assert result.output.index("openssl") < result.output.index("tzdata")
        assert "Kept back by the resolver: ubuntu-drivers-common" in result.output
        assert "A reboot is required" in result.output
        assert "cron.service" in result.output

    def test_the_json_listing_carries_the_counts_and_the_stale_services(self, machine) -> None:
        body = json.loads(run(["server", "updates", "list", "--json"]).output)

        assert body["pending"] == 9 and body["security"] == 7
        assert body["reboot_required"] is True
        assert body["stale_services"] == ["cron.service", "nginx.service", "php8.3-fpm.service"]

    def test_refresh_streams_the_package_managers_words(self, machine) -> None:
        machine.runner.script(
            ["apt-get", "update"], stdout="Hit:1 http://archive.ubuntu.com noble InRelease"
        )

        result = run(["server", "updates", "refresh"])

        assert result.exit_code == 0, result.output
        assert "Hit:1 http://archive.ubuntu.com noble InRelease" in result.output
        assert "9 pending, 7 security" in result.output

    def test_what_changes_the_machine_needs_root(
        self, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(server_cli, "check_root", lambda: False)

        result = run(["server", "updates", "refresh"])

        assert result.exit_code == 1
        assert not machine.runner.ran("apt-get", "update")

    def _followed(self, machine, monkeypatch: pytest.MonkeyPatch, **record: Any) -> list[dict]:
        calls: list[dict] = []

        def fake(manager, unit, update_id, on_line, **kwargs):
            calls.append({"update_id": update_id, **kwargs})
            on_line("Setting up openssl")
            return UpdateRecord(
                id=update_id,
                scope=kwargs["scope"],
                status=record.get("status", "completed"),
                **{k: v for k, v in record.items() if k != "status"},
            )

        monkeypatch.setattr(server_cli, "start_and_follow", fake)
        return calls

    def test_apply_shows_what_it_touches_and_runs_it_in_the_unit(
        self, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._followed(machine, monkeypatch, reboot_required=True, packages=["openssl"])

        result = run(["server", "updates", "apply", "--security-only", "--yes"])

        assert result.exit_code == 0, result.output
        assert "It touches: kernel" in result.output
        assert "Setting up openssl" in result.output
        assert "A reboot is required" in result.output
        assert calls[0]["scope"] == "security"
        assert calls[0]["full"] is False and calls[0]["allow_removals"] is False
        assert calls[0]["actor"].startswith("cli:")

    def test_noust_being_updated_warns_that_the_console_restarts(
        self, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine.runner.script(
            ["apt-get", "-s"], stdout="Inst noust [3.0.0] (3.1.0 Noust:stable/stable [all])\n"
        )
        self._followed(machine, monkeypatch)

        result = run(["server", "updates", "apply", "--yes"])

        assert "the console restarts" in result.output

    def test_without_a_terminal_and_without_yes_it_asks_for_yes_and_touches_nothing(
        self, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._followed(machine, monkeypatch)

        result = run(["server", "updates", "apply"])

        assert result.exit_code == 2
        assert "Add --yes" in result.output
        assert calls == []

    def test_a_removal_list_is_a_refusal_until_it_is_accepted(
        self, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine.runner.script(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"],
            stdout=fixture("apt", "full-upgrade-removals.txt"),
        )
        calls = self._followed(machine, monkeypatch)

        refused = run(["server", "updates", "apply", "--full", "--yes"])
        accepted = run(["server", "updates", "apply", "--full", "--allow-removals", "--yes"])

        assert refused.exit_code == 1
        assert "libfoo1t64" in said(refused) and "python3-oldthing" in said(refused)
        assert accepted.exit_code == 0
        assert len(calls) == 1 and calls[0]["full"] is True and calls[0]["allow_removals"] is True

    def test_a_failed_run_is_exit_code_1_with_the_reason(
        self, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._followed(
            machine, monkeypatch, status="failed", error="The package manager exited with code 100"
        )

        result = run(["server", "updates", "apply", "--yes"])

        assert result.exit_code == 1
        assert "exited with code 100" in said(result)

    def test_detach_starts_the_unit_and_says_how_to_follow_it(self, machine) -> None:
        result = run(["server", "updates", "apply", "--yes", "--detach"])

        assert result.exit_code == 0, result.output
        assert any(call[0] == "systemd-run" for call in machine.runner.calls)
        assert "journalctl -fu noust-os-update-" in result.output

    def test_detach_needs_systemd(self, machine) -> None:
        machine.host.systemd_marker.rmdir()

        result = run(["server", "updates", "apply", "--yes", "--detach"])

        assert result.exit_code == 1
        assert "needs systemd" in said(result)

    def test_the_command_the_unit_runs_applies_and_writes_the_record(self, machine) -> None:
        machine.runner.script(["apt-get", "-y"], stdout="Setting up openssl")

        result = run(
            [
                "server",
                "updates",
                "run",
                "--id",
                "0a1b2c3d",
                "--scope",
                "security",
                "--unit",
                "noust-os-update-0a1b2c3d",
                "--job-id",
                "job1",
                "--actor",
                "yago",
            ]
        )

        assert result.exit_code == 0, result.output
        assert "Setting up openssl" in result.output
        record = machine.ctx.records.read("0a1b2c3d")
        assert record is not None
        assert record.status == "completed"
        assert record.unit == "noust-os-update-0a1b2c3d"
        assert record.job_id == "job1"
        assert record.actor == "yago"

    def test_a_failing_run_exits_non_zero_so_the_unit_is_seen_to_fail(self, machine) -> None:
        machine.runner.script(["apt-get", "-y"], stdout="E: dpkg returned an error", exit_code=100)

        result = run(["server", "updates", "run", "--id", "0a1b2c3d", "--scope", "all"])

        assert result.exit_code == 1
        assert machine.ctx.records.read("0a1b2c3d").status == "failed"  # type: ignore[union-attr]

    def test_the_command_the_unit_runs_is_not_advertised(self, machine) -> None:
        assert "run " not in run(["server", "updates", "--help"]).output.replace("Run ", "")

    def test_history_lists_the_runs_and_shows_one(self, machine) -> None:
        machine.ctx.records.write(
            UpdateRecord(
                id="0a1b2c3d",
                scope="all",
                status="completed",
                started_at="2026-09-29T20:00:00+00:00",
                packages=["openssl"],
                tail=["Setting up openssl"],
                actor="cli:yago",
            )
        )

        listed = run(["server", "updates", "history"])
        one = run(["server", "updates", "history", "0a1b2c3d"])
        missing = run(["server", "updates", "history", "ffffffff"])

        assert "0a1b2c3d" in listed.output
        assert "Setting up openssl" in one.output
        assert missing.exit_code == 1

    def test_the_automatic_updates_are_shown_and_switched(self, machine) -> None:
        shown = run(["server", "updates", "auto", "status"])
        enabled = run(["server", "updates", "auto", "enable"])

        assert "unattended-upgrades" in shown.output
        assert enabled.exit_code == 0, enabled.output
        assert "APT::Periodic::Unattended-Upgrade" in enabled.output

    def test_a_json_option_on_a_command_that_has_none_is_refused(self, machine) -> None:
        assert run(["server", "updates", "refresh", "--json"]).exit_code == 2


class TestPower:
    def test_a_reboot_shows_the_checks_asks_and_schedules_it(self, machine) -> None:
        result = run(["server", "reboot", "--in", "5m", "--yes"])

        assert result.exit_code == 0, result.output
        assert "console" in result.output and "fstab" in result.output
        assert any(call[:3] == ("shutdown", "-r", "+5") for call in machine.runner.calls)

    def test_without_a_time_it_is_one_minute_and_now_is_only_for_the_command_line(
        self, machine
    ) -> None:
        run(["server", "reboot", "--yes"])
        run(["server", "reboot", "--now", "--yes"])

        pluses = [call[2] for call in machine.runner.calls if call[0] == "shutdown"]
        assert pluses == ["+1", "+0"]

    def test_a_reboot_needs_yes_when_nobody_can_answer(self, machine) -> None:
        result = run(["server", "reboot", "--in", "5"])

        assert result.exit_code == 2
        assert not machine.runner.ran("shutdown")

    def test_asking_two_ways_to_say_when_is_a_usage_error(self, machine) -> None:
        result = run(["server", "reboot", "--in", "5", "--now", "--yes"])

        assert result.exit_code == 2
        assert "not --in and --now" in result.output

    def test_a_warning_stops_it_until_forced(self, machine) -> None:
        machine.ctx.power._apps_check = lambda runner: ["shop-example-com"]

        refused = run(["server", "reboot", "--yes"])
        forced = run(["server", "reboot", "--yes", "--force"])

        assert refused.exit_code == 1
        assert "shop-example-com" in said(refused)
        assert forced.exit_code == 0

    def test_at_a_clock_time_is_the_next_one(self) -> None:
        minutes = server_cli.parse_delay(None, "04:00", False)

        assert 1 <= minutes <= 24 * 60 + 1

    def test_at_a_moment_in_the_past_is_refused(self) -> None:
        past = datetime(2020, 1, 1, 0, 0).astimezone().isoformat()

        with pytest.raises(Exception, match="already passed"):
            server_cli.parse_delay(None, past, False)

    @pytest.mark.parametrize(("text", "minutes"), [("5", 5), ("5m", 5), ("2h", 120), ("90min", 90)])
    def test_delays(self, text: str, minutes: int) -> None:
        assert server_cli.parse_delay(text, None, False) == minutes

    @pytest.mark.parametrize("bad", ["soon", "-5", "5x", ""])
    def test_a_delay_that_is_not_one_is_a_usage_error(self, bad: str) -> None:
        with pytest.raises(click.UsageError):
            server_cli.parse_delay(bad, None, False)

    def test_cancel_and_status(self, machine) -> None:
        run(["server", "reboot", "--yes"])
        machine.host.shutdown_scheduled.write_text("MODE=reboot\n")

        status = run(["server", "reboot", "--status"])
        cancelled = run(["server", "reboot", "--cancel"])

        assert "reboot scheduled for" in status.output
        assert "Cancelled" in cancelled.output
        assert ("shutdown", "-c") in machine.runner.calls

    def test_a_shutdown_powers_off_and_says_where_it_is_started_again(self, machine) -> None:
        result = run(["server", "shutdown", "--yes"])

        assert result.exit_code == 0, result.output
        assert any(call[:2] == ("shutdown", "-P") for call in machine.runner.calls)

    def test_a_rehearsal_schedules_nothing(self, store, tmp_path: Path) -> None:
        from noust.core.runner import FakeRunner

        fake = FakeRunner()
        dry = DryRunRunner(fake)
        machine = make_machine(tmp_path, pytest.MonkeyPatch(), store)
        ctx = ServerContext(
            runner=dry,
            fs=DryRunFileSystem(),
            host=machine.host,
            platform=platform_for("apt"),
            cache=FactCache(background=False),
            store=store,
        )
        ctx.power._apps_check = lambda runner: []

        ctx.power.schedule("reboot", minutes=5, actor="yago", force=True)

        assert ("shutdown", "-r", "+5", "Reboot requested from Noust by yago") in dry.skipped
        assert not fake.ran("shutdown")


class TestStorage:
    def test_json_has_the_mounts_and_the_candidates(self, machine) -> None:
        machine.runner.script(
            ["journalctl", "--disk-usage"], stdout=fixture("journal", "disk-usage.txt")
        )

        body = json.loads(run(["server", "storage", "--json"]).output)

        assert [m["mount_point"] for m in body["mounts"]] == ["/data", "/"]
        assert body["candidates"][0]["id"] == "journal"

    def test_the_human_table_says_how_to_clean_each_thing(self, machine) -> None:
        machine.runner.script(
            ["journalctl", "--disk-usage"], stdout=fixture("journal", "disk-usage.txt")
        )

        result = run(["server", "storage"])

        assert "noust server cleanup journal" in result.output
        assert "--analyze" in result.output

    def test_the_plan_of_a_cleanup_changes_nothing(self, machine) -> None:
        result = run(["server", "cleanup", "journal", "--plan", "--size-mb", "100"])

        assert result.exit_code == 0
        assert "journalctl --vacuum-size=100M" in result.output
        assert not machine.runner.ran("journalctl", "--rotate")

    def test_a_docker_cleanup_says_what_it_takes_and_needs_yes(self, machine) -> None:
        refused = run(["server", "cleanup", "docker-build-cache"])
        done = run(["server", "cleanup", "docker-build-cache", "--yes"])

        assert refused.exit_code == 2
        assert done.exit_code == 0, done.output
        assert (
            "docker",
            "builder",
            "prune",
            "-f",
            "--filter",
            "until=168h",
        ) in machine.runner.calls

    def test_the_journal_is_vacuumed_without_asking(self, machine) -> None:
        result = run(["server", "cleanup", "journal", "--days", "14"])

        assert result.exit_code == 0, result.output
        assert ("journalctl", "--vacuum-time=14d") in machine.runner.calls

    def test_an_action_that_is_not_on_the_list_is_a_usage_error(self, machine) -> None:
        result = run(["server", "cleanup", "docker-system-prune"])

        assert result.exit_code == 2
        assert not machine.runner.ran("docker")


class TestSwap:
    @pytest.fixture(autouse=True)
    def roomy(self, monkeypatch: pytest.MonkeyPatch):
        import shutil

        usage = shutil._ntuple_diskusage(total=100 * 1024**3, used=30 * 1024**3, free=70 * 1024**3)
        monkeypatch.setattr(shutil, "disk_usage", lambda path: usage)

    def test_status_recommends_swap_on_a_small_machine(self, machine) -> None:
        machine.runner.script(["swapon"], stdout="")

        result = run(["server", "swap"])

        assert result.exit_code == 0, result.output
        assert "noust server swap create --size 2048M" in result.output

    def test_creating_it_parses_the_size_and_asks_for_yes(self, machine) -> None:
        machine.runner.script(["findmnt", "-no", "FSTYPE"], stdout="ext4\n")
        (machine.host.root / "etc").mkdir(exist_ok=True)
        machine.host.fstab.write_text("UUID=abc / ext4 defaults 0 1\n")

        refused = run(["server", "swap", "create", "--size", "2G"])
        done = run(["server", "swap", "create", "--size", "2G", "--yes"])

        assert refused.exit_code == 2
        assert done.exit_code == 0, done.output
        assert ("fallocate", "-l", str(2 * 1024**3), "/swapfile") in machine.runner.calls

    @pytest.mark.parametrize(
        ("text", "size"),
        [("2G", 2 * 1024**3), ("512M", 512 * 1024**2), ("1024", 1024**3), ("1GiB", 1024**3)],
    )
    def test_sizes(self, text: str, size: int) -> None:
        assert server_cli.parse_size(text) == size

    @pytest.mark.parametrize("bad", ["big", "-2G", "2.5G", ""])
    def test_a_size_that_is_not_one_is_a_usage_error(self, bad: str) -> None:
        with pytest.raises(click.BadParameter):
            server_cli.parse_size(bad)

    def test_swappiness(self, machine) -> None:
        result = run(["server", "swap", "swappiness", "20"])

        assert result.exit_code == 0
        assert ("sysctl", "-w", "vm.swappiness=20") in machine.runner.calls

    def test_swappiness_out_of_range_is_a_usage_error(self, machine) -> None:
        assert run(["server", "swap", "swappiness", "500"]).exit_code == 2


class TestSystem:
    def test_the_clock(self, machine) -> None:
        result = run(["server", "time"])

        assert "Atlantic/Canary" in result.output

    def test_the_zone_is_checked_before_timedatectl_sees_it(
        self, machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("zoneinfo.available_timezones", lambda: {"Europe/Madrid"})

        ok = run(["server", "time", "timezone", "Europe/Madrid"])
        bad = run(["server", "time", "timezone", "Europe/Madrid; reboot"])

        assert ok.exit_code == 0, ok.output
        assert "Atlantic/Canary -> Europe/Madrid" in ok.output
        assert bad.exit_code == 1
        assert ("timedatectl", "set-timezone", "Europe/Madrid; reboot") not in machine.runner.calls

    def test_ntp(self, machine) -> None:
        assert run(["server", "time", "ntp", "on"]).exit_code == 0
        assert ("timedatectl", "set-ntp", "true") in machine.runner.calls

    def test_the_hostname_is_shown_and_changed(self, machine) -> None:
        shown = run(["server", "hostname"])
        changed = run(["server", "hostname", "web-2"])
        bad = run(["server", "hostname", "web;reboot"])

        assert "vps-1" in shown.output
        assert changed.exit_code == 0
        assert ("hostnamectl", "set-hostname", "web-2") in machine.runner.calls
        assert bad.exit_code == 1

    def test_logs_pass_their_filters_checked(self, machine) -> None:
        machine.runner.script(
            ["journalctl"],
            stdout='{"__CURSOR":"c1","MESSAGE":"Connection refused","__REALTIME_TIMESTAMP":"1790716408434957","_SYSTEMD_UNIT":"nginx.service"}',
        )

        result = run(["server", "logs", "nginx", "-n", "5", "-p", "err", "--since", "-2h"])

        assert result.exit_code == 0, result.output
        assert "nginx.service: Connection refused" in result.output
        argv = next(c for c in machine.runner.calls if c[0] == "journalctl")
        assert "--unit=nginx" in argv and "--priority=3" in argv and "--since=-2h" in argv

    def test_follow_prints_the_journals_lines_and_refuses_json(self, machine) -> None:
        machine.runner.script(
            ["journalctl"], stdout="2026-09-29T22:13:28+0100 vps-1 nginx[1]: started"
        )

        followed = run(["server", "logs", "nginx", "-f"])
        as_json = run(["server", "logs", "nginx", "-f", "--json"])

        assert "nginx[1]: started" in followed.output
        assert followed.exit_code == 0
        assert as_json.exit_code == 2

    def test_a_unit_that_is_an_option_is_refused_with_journalctl_never_running(
        self, machine
    ) -> None:
        result = run(["server", "logs", "--", "--all"])

        assert result.exit_code == 1
        assert not machine.runner.ran("journalctl")

    def test_processes_by_unit(
        self, machine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        rows = [
            types.SimpleNamespace(
                info={
                    "pid": 1,
                    "name": "nginx",
                    "cpu_percent": 3.0,
                    "memory_percent": 1.0,
                    "memory_info": types.SimpleNamespace(rss=10 * 1024**2),
                    "status": "sleeping",
                    "username": "www-data",
                }
            )
        ]
        fake = types.SimpleNamespace(
            process_iter=lambda fields: iter(rows),
            NoSuchProcess=KeyError,
            AccessDenied=KeyError,
            ZombieProcess=KeyError,
        )
        monkeypatch.setattr("noust.managers.server.processes._psutil", lambda: fake)

        result = run(["server", "processes", "--json"])

        assert json.loads(result.output)["processes"][0]["name"] == "nginx"


class TestAttachingMoreCommands:
    def _fake_module(self, monkeypatch: pytest.MonkeyPatch, **attributes: Any) -> None:
        import importlib.machinery
        import sys

        module = types.ModuleType("noust.cli.commands.server_security")
        module.__spec__ = importlib.machinery.ModuleSpec("noust.cli.commands.server_security", None)
        for name, value in attributes.items():
            setattr(module, name, value)
        monkeypatch.setitem(sys.modules, "noust.cli.commands.server_security", module)

    def test_a_register_function_is_called_with_the_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[click.Group] = []
        self._fake_module(monkeypatch, register=seen.append)

        server_cli._attach_security_commands()

        assert seen == [server_cli.cli]

    def test_a_list_of_commands_is_added(self, monkeypatch: pytest.MonkeyPatch) -> None:
        @click.command("firewall-test-only")
        def extra() -> None:
            """Extra."""

        self._fake_module(monkeypatch, commands=[extra])
        try:
            server_cli._attach_security_commands()

            assert "firewall-test-only" in server_cli.cli.commands
        finally:
            server_cli.cli.commands.pop("firewall-test-only", None)


def test_the_error_boundary_prints_a_managers_refusal_and_exits_1(machine, capsys) -> None:
    code = main(["server", "hostname", "web;reboot"])

    assert code == 1
    assert "Not a valid host name" in capsys.readouterr().out
