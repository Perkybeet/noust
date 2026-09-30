"""
Tests for the updates manager and the transient unit that runs it.

The first thing under test is not the package manager but what surrounds it: an
update that would remove packages is a question before it is an action, one that
overlaps another package manager is refused, and the result of one is written to
disk because the console that started it may not be alive to hear it. The second
is the unit: the update runs in its own cgroup so restarting the console, which
the ``noust`` package's own post-install script does, cannot kill it in the
middle of dpkg.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import DryRunFileSystem, RecordingFileSystem, set_fs
from noust.core.runner import FakeRunner
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    HostBusyError,
    PreflightError,
    ServerError,
    UnsupportedHostError,
)
from noust.managers.server.pkg.base import UpdateScope
from noust.managers.server.updates import (
    ApplyPlan,
    RecordStore,
    UpdateRecord,
    UpdatesManager,
    conffiles_kept,
    impact_of,
)
from noust.managers.server.updates_unit import (
    UNIT_PREFIX,
    UnitState,
    UpdateUnit,
    decode_message,
    parse_journal,
    parse_show,
    start_and_follow,
)
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    SequencedRunner,
    fixture,
    make_host,
    no_package_manager_running,
    platform_for,
)

pytestmark = pytest.mark.usefixtures("no_package_manager_running")


@pytest.fixture
def host(tmp_path: Path):
    return make_host(tmp_path / "root")


@pytest.fixture
def fs() -> RecordingFileSystem:
    return RecordingFileSystem()


@pytest.fixture
def records(tmp_path: Path, fs: RecordingFileSystem) -> RecordStore:
    return RecordStore(tmp_path / "os-updates", fs=fs)


def _manager(runner, host, fs, records, **kwargs) -> UpdatesManager:
    return UpdatesManager(
        platform=kwargs.pop("platform", platform_for("apt")),
        runner=runner,
        fs=fs,
        host=host,
        records=records,
        **kwargs,
    )


def _script_apt(runner: FakeRunner, *, simulation: str | None = None) -> None:
    runner.script(["apt-get", "-s"], stdout=simulation or fixture("apt", "upgrade-simulation.txt"))


class TestPlanning:
    def test_the_security_plan_names_exactly_the_security_packages(self, host, fs, records) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        manager = _manager(runner, host, fs, records)

        plan = manager.plan(UpdateScope.SECURITY)

        names = {p.name for p in plan.packages}
        assert "openssl" in names
        assert "bash" not in names
        assert (
            plan.argv[-3:-1] == ("--only-upgrade", "--no-remove") or "--only-upgrade" in plan.argv
        )
        assert "bash" not in plan.argv
        assert plan.removals == ()

    def test_kernel_and_the_software_it_disturbs_are_named_in_the_impact(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        manager = _manager(runner, host, fs, records)

        plan = manager.plan(UpdateScope.ALL)

        assert "kernel" in plan.impact
        assert "libc" not in plan.impact
        assert plan.restarts_console is False

    def test_an_update_of_noust_itself_says_the_console_will_restart(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        _script_apt(
            runner,
            simulation="Inst noust [3.0.0] (3.1.0 Noust:stable/stable [all])\n"
            "Inst docker-ce [1] (2 Docker:noble/stable [amd64])\n",
        )
        manager = _manager(runner, host, fs, records)

        plan = manager.plan(UpdateScope.ALL)

        assert plan.restarts_console is True
        assert plan.impact == ("docker", "noust")

    def test_a_full_upgrade_reports_what_it_would_remove(self, host, fs, records) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        runner.script(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"],
            stdout=fixture("apt", "full-upgrade-removals.txt"),
        )
        manager = _manager(runner, host, fs, records)

        plan = manager.plan(UpdateScope.ALL, full=True)

        assert plan.removals == ("libfoo1t64", "python3-oldthing")
        assert plan.argv[-1] == "full-upgrade"

    def test_a_half_configured_database_is_a_reason_not_to_start(self, host, fs, records) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        runner.script(["dpkg", "--audit"], stdout="The following packages are only half configured")
        manager = _manager(runner, host, fs, records)

        with pytest.raises(PreflightError, match="half configured") as raised:
            manager.plan(UpdateScope.ALL)

        assert raised.value.blockers

    def test_an_unsupported_system_says_why_and_lists_nothing(self, host, fs, records) -> None:
        manager = _manager(FakeRunner(), host, fs, records, platform=platform_for("none"))

        with pytest.raises(UnsupportedHostError, match="cannot be managed"):
            manager.plan(UpdateScope.ALL)

    def test_impact_matches_by_glob(self) -> None:
        assert impact_of(["postgresql-16", "libc6", "nginx-common"]) == [
            "nginx",
            "postgresql",
            "libc",
        ]


class TestPreflight:
    def test_another_package_manager_stops_it(
        self, host, fs, records, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.managers.server.pkg.base.running_processes", lambda names: ["unattended-upgr"]
        )
        manager = _manager(FakeRunner(), host, fs, records)

        with pytest.raises(HostBusyError, match="unattended-upgr") as raised:
            manager.preflight()

        assert raised.value.holders == ["unattended-upgr"]

    def test_a_running_deploy_or_backup_stops_it(self, host, fs, records) -> None:
        manager = _manager(
            FakeRunner(), host, fs, records, blockers=lambda: ["deploy shop.example.com"]
        )

        with pytest.raises(PreflightError) as raised:
            manager.preflight()

        assert raised.value.blockers == ["deploy shop.example.com is running"]

    def test_a_nearly_full_disk_stops_it(
        self, host, fs, records, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        usage = shutil._ntuple_diskusage(
            total=10 * 1024**3, used=10 * 1024**3 - 5 * 1024**2, free=5 * 1024**2
        )
        monkeypatch.setattr(shutil, "disk_usage", lambda path: usage)
        manager = _manager(FakeRunner(), host, fs, records)

        with pytest.raises(PreflightError) as raised:
            manager.preflight()

        assert "5 MiB" in raised.value.blockers[0]

    def test_a_clear_machine_passes(self, host, fs, records) -> None:
        _manager(FakeRunner(), host, fs, records).preflight()


class TestApplying:
    def _ready(self, host, fs, records, *, exit_code: int = 0, output: str = "done\n"):
        runner = FakeRunner()
        _script_apt(runner)
        runner.script(["apt-get", "-y"], stdout=output, exit_code=exit_code)
        return runner, _manager(runner, host, fs, records)

    def test_it_runs_the_command_of_the_plan_in_an_environment_with_no_questions(
        self, host, fs, records
    ) -> None:
        runner, manager = self._ready(host, fs, records)
        lines: list[str] = []

        manager.apply(UpdateScope.ALL, on_line=lines.append, update_id="0a1b2c3d")

        index = next(i for i, call in enumerate(runner.calls) if call[:2] == ("apt-get", "-y"))
        assert runner.calls[index][-2:] == ("--with-new-pkgs", "upgrade")
        env = runner.envs[index]
        assert env is not None
        assert env["DEBIAN_FRONTEND"] == "noninteractive"
        assert env["NEEDRESTART_MODE"] == "l"
        assert lines == ["done"]

    def test_the_run_is_written_down_when_it_starts_and_again_when_it_ends(
        self, host, fs, records
    ) -> None:
        runner, manager = self._ready(host, fs, records)

        record = manager.apply(
            UpdateScope.SECURITY, on_line=lambda line: None, update_id="0a1b2c3d", actor="yago"
        )

        stored = records.read("0a1b2c3d")
        assert stored is not None
        assert stored.status == "completed"
        assert stored.exit_code == 0
        assert stored.actor == "yago"
        assert "openssl" in stored.packages
        assert record.finished_at is not None
        written = [path.name for kind, path in fs.changes if kind == "write"]
        assert written.count("0a1b2c3d.json") == 2

    def test_it_records_whether_a_reboot_is_now_due_and_which_services_are_stale(
        self, host, fs, records
    ) -> None:
        runner, manager = self._ready(host, fs, records)
        runner.script(["needrestart", "-b"], stdout=fixture("apt", "needrestart-batch.txt"))

        record = manager.apply(UpdateScope.ALL, on_line=lambda line: None, update_id="0a1b2c3d")

        assert record.reboot_required is True
        assert record.stale_services == ["cron.service", "nginx.service", "php8.3-fpm.service"]

    def test_the_configuration_files_it_left_alone_are_listed(self, host, fs, records) -> None:
        runner, manager = self._ready(
            host,
            fs,
            records,
            output="Configuration file '/etc/ssh/sshd_config'\n ==> Keeping old config file as default.\n",
        )

        record = manager.apply(UpdateScope.ALL, on_line=lambda line: None, update_id="0a1b2c3d")

        assert record.conffiles_kept == ["/etc/ssh/sshd_config"]

    def test_a_failure_is_recorded_and_carries_the_tools_own_words(self, host, fs, records) -> None:
        runner, manager = self._ready(
            host,
            fs,
            records,
            exit_code=100,
            output="E: Sub-process /usr/bin/dpkg returned an error code (1)\n",
        )

        with pytest.raises(ServerError) as raised:
            manager.apply(UpdateScope.ALL, on_line=lambda line: None, update_id="0a1b2c3d")

        assert "dpkg returned an error code" in (raised.value.output or "")
        assert "repair" in raised.value.details
        stored = records.read("0a1b2c3d")
        assert stored is not None
        assert stored.status == "failed"
        assert stored.exit_code == 100

    def test_removals_are_a_question_and_nothing_is_touched_until_answered(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        runner.script(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"],
            stdout=fixture("apt", "full-upgrade-removals.txt"),
        )
        manager = _manager(runner, host, fs, records)

        with pytest.raises(ConfirmationRequiredError) as raised:
            manager.apply(UpdateScope.ALL, full=True, on_line=lambda line: None)

        assert raised.value.required == {"removals": ["libfoo1t64", "python3-oldthing"]}
        assert not [c for c in runner.calls if c[:2] == ("apt-get", "-y")]
        assert records.recent() == []

    def test_once_allowed_a_full_upgrade_runs(self, host, fs, records) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        runner.script(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"],
            stdout=fixture("apt", "full-upgrade-removals.txt"),
        )
        manager = _manager(runner, host, fs, records)

        manager.apply(UpdateScope.ALL, full=True, allow_removals=True, on_line=lambda line: None)

        assert next(c for c in runner.calls if c[:2] == ("apt-get", "-y"))[-1] == "full-upgrade"

    def test_security_with_nothing_marked_says_so_instead_of_running_apt_with_no_packages(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        _script_apt(runner, simulation=fixture("apt", "upgrade-simulation-clean.txt"))
        manager = _manager(runner, host, fs, records)

        with pytest.raises(ServerError, match="no security updates"):
            manager.apply(UpdateScope.SECURITY, on_line=lambda line: None)

    def test_a_rehearsal_writes_no_record(self, host, tmp_path: Path) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        runner.script(["apt-get", "-y"], stdout="done\n")
        dry = DryRunFileSystem()
        set_fs(dry)
        manager = UpdatesManager(
            platform=platform_for("apt"),
            runner=runner,
            host=host,
            records=RecordStore(tmp_path / "os-updates"),
        )

        manager.apply(UpdateScope.ALL, on_line=lambda line: None, update_id="0a1b2c3d")

        assert not (tmp_path / "os-updates").exists()
        assert any("0a1b2c3d.json" in change for change in dry.skipped)


class TestRefreshAndRepair:
    def test_the_refresh_streams_apt_updates_output_and_fails_with_it(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        runner.script(
            ["apt-get", "update"],
            stdout="Err:3 http://mirror.example.com noble InRelease\n  Could not resolve host",
            exit_code=100,
        )
        manager = _manager(runner, host, fs, records)
        lines: list[str] = []

        with pytest.raises(ServerError) as raised:
            manager.refresh(lines.append)

        assert lines[0].startswith("Err:3")
        assert "Could not resolve host" in (raised.value.output or "")

    def test_repair_runs_dpkg_then_apt_and_reports_what_ran(self, host, fs, records) -> None:
        runner = FakeRunner()
        manager = _manager(runner, host, fs, records)

        ran = manager.repair(lambda line: None)

        assert ran == ["dpkg --configure -a", "apt-get -f install -y"]
        assert runner.calls[0] == ("dpkg", "--configure", "-a")

    def test_there_is_nothing_to_repair_on_dnf(self, host, fs, records) -> None:
        manager = _manager(FakeRunner(), host, fs, records, platform=platform_for("dnf"))

        with pytest.raises(UnsupportedHostError):
            manager.repair(lambda line: None)


class TestRecords:
    def test_an_identifier_that_is_not_ours_cannot_name_a_file(self, records: RecordStore) -> None:
        with pytest.raises(ValidationError):
            records.path("../../etc/passwd")

    def test_only_the_newest_fifty_are_kept(self, records: RecordStore, tmp_path: Path) -> None:
        for number in range(53):
            records.write(UpdateRecord(id=f"{number:08x}", scope="all"))

        assert len(list((tmp_path / "os-updates").glob("*.json"))) == 50

    def test_running_lists_only_the_unfinished(self, records: RecordStore) -> None:
        records.write(UpdateRecord(id="00000001", scope="all", status="completed"))
        records.write(UpdateRecord(id="00000002", scope="all", status="running"))

        assert [r.id for r in records.running()] == ["00000002"]

    def test_a_record_a_newer_noust_wrote_is_still_readable(
        self, records: RecordStore, tmp_path: Path
    ) -> None:
        records.write(UpdateRecord(id="00000001", scope="all"))
        path = tmp_path / "os-updates" / "00000001.json"
        data = json.loads(path.read_text())
        data["a_field_from_the_future"] = 1
        path.write_text(json.dumps(data))

        assert records.read("00000001") is not None

    def test_records_are_private_to_root(
        self, records: RecordStore, fs: RecordingFileSystem, tmp_path: Path
    ) -> None:
        records.write(UpdateRecord(id="00000001", scope="all"))

        mode = (tmp_path / "os-updates" / "00000001.json").stat().st_mode & 0o777
        assert mode == 0o600

    def test_conffile_notices_are_read_for_dpkg_and_rpm(self) -> None:
        text = (
            "Configuration file '/etc/a.conf'\nConfiguration file '/etc/a.conf'\n"
            "warning: /etc/b.conf created as /etc/b.conf.rpmnew\n"
        )

        assert conffiles_kept(text) == ["/etc/a.conf", "/etc/b.conf"]


class TestTheTransientUnit:
    def _unit(self, runner, host, fs, records) -> UpdateUnit:
        return UpdateUnit(records=records, runner=runner, fs=fs, host=host, sleep=lambda s: None)

    def test_the_update_is_started_in_its_own_unit_that_is_forgotten_when_it_ends(
        self, host, fs, records, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner()
        monkeypatch.setattr(
            "noust.managers.server.updates_unit.noust_command", lambda: ["/usr/bin/noust"]
        )
        unit = self._unit(runner, host, fs, records)

        name = unit.start("0a1b2c3d", scope="security", job_id="abcd1234", actor="yago")

        assert name == f"{UNIT_PREFIX}0a1b2c3d"
        argv = runner.calls[0]
        assert argv[0] == "systemd-run"
        assert "--unit=noust-os-update-0a1b2c3d" in argv
        assert "--collect" in argv
        assert "--property=StandardOutput=journal" in argv
        assert "--property=RuntimeMaxSec=3720" in argv
        tail = argv[argv.index("--") + 1 :]
        assert tail[:5] == ("/usr/bin/noust", "server", "updates", "run", "--id")
        assert "--scope" in tail and "security" in tail
        assert tail[tail.index("--job-id") + 1] == "abcd1234"
        assert "--full" not in tail
        assert "--allow-removals" not in tail

    def test_the_data_directory_the_console_uses_is_passed_on(
        self, host, fs, records, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner()
        monkeypatch.setenv("NOUST_DATA_DIR", "/data/noust")
        unit = self._unit(runner, host, fs, records)

        unit.start("0a1b2c3d", scope="all")

        assert "--setenv=NOUST_DATA_DIR=/data/noust" in runner.calls[0]

    def test_full_and_allowed_removals_are_passed_to_the_unit(self, host, fs, records) -> None:
        runner = FakeRunner()
        unit = self._unit(runner, host, fs, records)

        unit.start("0a1b2c3d", scope="all", full=True, allow_removals=True)

        assert "--full" in runner.calls[0]
        assert "--allow-removals" in runner.calls[0]

    def test_a_refusal_by_systemd_run_is_an_error_with_its_output(self, host, fs, records) -> None:
        runner = FakeRunner()
        runner.script(["systemd-run"], stderr="Failed to connect to bus", exit_code=1)

        with pytest.raises(ServerError) as raised:
            self._unit(runner, host, fs, records).start("0a1b2c3d", scope="all")

        assert "Failed to connect to bus" in (raised.value.output or "")

    def test_available_needs_systemd_running_and_systemd_run_present(
        self, host, fs, records
    ) -> None:
        assert self._unit(FakeRunner(), host, fs, records).available() is True
        assert (
            self._unit(FakeRunner().only_knows("apt-get"), host, fs, records).available() is False
        )
        host.systemd_marker.rmdir()
        assert self._unit(FakeRunner(), host, fs, records).available() is False

    def test_following_delivers_each_line_once_and_returns_the_record(
        self, host, fs, records
    ) -> None:
        runner = SequencedRunner()
        first = '{"__CURSOR":"c1","MESSAGE":"Reading package lists..."}\n'
        second = '{"__CURSOR":"c2","MESSAGE":"Setting up openssl"}\n'
        runner.sequence(
            ["journalctl"],
            [{"stdout": first}, {"stdout": second}, {"stdout": ""}, {"stdout": ""}],
        )
        active = "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nExecMainStatus=0\n"
        gone = "LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\nExecMainStatus=0\n"
        runner.sequence(["systemctl", "show"], [{"stdout": active}, {"stdout": gone}])
        records.write(UpdateRecord(id="0a1b2c3d", scope="all", status="completed", exit_code=0))
        lines: list[str] = []

        record = self._unit(runner, host, fs, records).follow("0a1b2c3d", lines.append)

        assert lines == ["Reading package lists...", "Setting up openssl"]
        assert record.status == "completed"
        # The second look asks for what came after the first line, not everything again.
        assert any(
            call[-1] == "--after-cursor=c1" for call in runner.calls if call[0] == "journalctl"
        )

    def test_a_unit_that_ends_without_a_result_is_an_error_with_what_it_said(
        self, host, fs, records
    ) -> None:
        runner = SequencedRunner()
        runner.sequence(
            ["journalctl"], [{"stdout": '{"__CURSOR":"c1","MESSAGE":"Killed"}\n'}, {"stdout": ""}]
        )
        runner.script(["systemctl", "show"], stdout="LoadState=not-found\nActiveState=inactive\n")

        with pytest.raises(ServerError, match="without recording a result") as raised:
            self._unit(runner, host, fs, records).follow("0a1b2c3d", lambda line: None)

        assert "Killed" in (raised.value.output or "")

    def test_a_unit_that_never_ends_is_left_running_and_the_command_to_follow_it_is_given(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "show"], stdout="LoadState=loaded\nActiveState=active\n")

        with pytest.raises(ServerError, match="Gave up") as raised:
            self._unit(runner, host, fs, records).follow(
                "0a1b2c3d", lambda line: None, poll_seconds=0, max_seconds=0
            )

        assert "journalctl -fu noust-os-update-0a1b2c3d" in raised.value.details

    def test_journal_messages_that_are_not_utf8_arrive_as_lists_of_bytes(self) -> None:
        assert decode_message([72, 105, 255]) == "Hi�"
        assert decode_message("plain") == "plain"
        assert decode_message(None) is None
        assert parse_journal('not json\n{"__CURSOR":"c","MESSAGE":[79,75]}\n') == [("c", "OK")]

    def test_show_reads_a_forgotten_unit_as_not_loaded(self) -> None:
        state = parse_show("LoadState=not-found\nActiveState=inactive\nExecMainStatus=0\n")

        assert state.loaded is False
        assert state.active is False
        assert parse_show("LoadState=loaded\nActiveState=activating\n").active is True


class TestReconciling:
    def test_a_unit_still_running_is_found_again(self, host, fs, records) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "show"], stdout="LoadState=loaded\nActiveState=active\n")
        records.write(
            UpdateRecord(
                id="0a1b2c3d", scope="all", status="running", unit=f"{UNIT_PREFIX}0a1b2c3d"
            )
        )

        outcome = UpdateUnit(records=records, runner=runner, fs=fs, host=host).reconcile()

        assert [r.id for r in outcome.reattach] == ["0a1b2c3d"]
        assert outcome.lost == ()

    def test_a_unit_that_is_gone_without_a_result_is_marked_failed_with_its_last_words(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "show"], stdout="LoadState=not-found\nActiveState=inactive\n")
        runner.script(["journalctl"], stdout="Setting up openssl\nKilled\n")
        records.write(
            UpdateRecord(
                id="0a1b2c3d", scope="all", status="running", unit=f"{UNIT_PREFIX}0a1b2c3d"
            )
        )

        outcome = UpdateUnit(records=records, runner=runner, fs=fs, host=host).reconcile()

        assert outcome.reattach == ()
        assert [r.id for r in outcome.lost] == ["0a1b2c3d"]
        stored = records.read("0a1b2c3d")
        assert stored is not None
        assert stored.status == "failed"
        assert stored.tail == ["Setting up openssl", "Killed"]

    def test_a_finished_record_is_left_alone(self, host, fs, records) -> None:
        records.write(UpdateRecord(id="0a1b2c3d", scope="all", status="completed"))

        outcome = UpdateUnit(records=records, runner=FakeRunner(), fs=fs, host=host).reconcile()

        assert outcome.reattach == () and outcome.lost == ()

    def test_a_run_started_in_a_terminal_is_not_declared_lost_while_it_may_still_go(
        self, host, fs, records
    ) -> None:
        from datetime import datetime, timezone

        records.write(
            UpdateRecord(
                id="0a1b2c3d",
                scope="all",
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(),
            )
        )

        outcome = UpdateUnit(records=records, runner=FakeRunner(), fs=fs, host=host).reconcile()

        assert outcome.lost == ()


class TestAnUnansweredProbe:
    """
    A ``systemctl show`` that failed or timed out says nothing about the unit.

    It parsed as "inactive", which marked a running update failed and made a
    follower stop following an update that was still going.
    """

    def test_reconciling_does_not_declare_a_run_lost_when_systemd_did_not_answer(
        self, host, fs, records
    ) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "show"], exit_code=1, stderr="Failed to connect to bus")
        records.write(
            UpdateRecord(
                id="0a1b2c3d", scope="all", status="running", unit=f"{UNIT_PREFIX}0a1b2c3d"
            )
        )

        outcome = UpdateUnit(records=records, runner=runner, fs=fs, host=host).reconcile()

        assert outcome.lost == ()
        stored = records.read("0a1b2c3d")
        assert stored is not None and stored.status == "running"

    def test_following_keeps_waiting_when_systemd_did_not_answer(self, host, fs, records) -> None:
        runner = FakeRunner()
        runner.script(["systemctl", "show"], exit_code=1, stderr="Connection timed out")

        with pytest.raises(ServerError, match="Gave up"):
            UpdateUnit(records=records, runner=runner, fs=fs, host=host).follow(
                "0a1b2c3d", lambda line: None, poll_seconds=0, max_seconds=0
            )

    def test_a_result_recorded_while_reconciling_is_not_overwritten(
        self, host, fs, records, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records.write(
            UpdateRecord(
                id="0a1b2c3d", scope="all", status="running", unit=f"{UNIT_PREFIX}0a1b2c3d"
            )
        )
        unit = UpdateUnit(records=records, runner=FakeRunner(), fs=fs, host=host)

        def finishes_meanwhile(update_id: str) -> UnitState:
            records.write(UpdateRecord(id=update_id, scope="all", status="completed", exit_code=0))
            return UnitState(loaded=False, active=False)

        monkeypatch.setattr(unit, "state", finishes_meanwhile)

        outcome = unit.reconcile()

        assert outcome.lost == ()
        stored = records.read("0a1b2c3d")
        assert stored is not None and stored.status == "completed"


class TestStartAndFollow:
    def test_where_systemd_is_there_the_update_runs_in_its_unit(self, host, fs, records) -> None:
        runner = SequencedRunner()
        runner.sequence(
            ["journalctl"], [{"stdout": '{"__CURSOR":"c1","MESSAGE":"ok"}\n'}, {"stdout": ""}]
        )
        runner.script(["systemctl", "show"], stdout="LoadState=not-found\nActiveState=inactive\n")
        records.write(UpdateRecord(id="0a1b2c3d", scope="all", status="completed"))
        manager = _manager(runner, host, fs, records)
        unit = UpdateUnit(records=records, runner=runner, fs=fs, host=host, sleep=lambda s: None)
        lines: list[str] = []

        result = start_and_follow(
            manager,
            unit,
            "0a1b2c3d",
            lines.append,
            scope="all",
            full=False,
            allow_removals=False,
            job_id=None,
            actor=None,
        )

        assert result.status == "completed"
        assert runner.calls[0][0] == "systemd-run"
        assert not [c for c in runner.calls if c[:2] == ("apt-get", "-y")]

    def test_without_systemd_the_update_runs_in_this_process(self, host, fs, records) -> None:
        runner = FakeRunner()
        _script_apt(runner)
        host.systemd_marker.rmdir()
        manager = _manager(runner, host, fs, records)
        unit = UpdateUnit(records=records, runner=runner, fs=fs, host=host)

        result = start_and_follow(
            manager,
            unit,
            "0a1b2c3d",
            lambda line: None,
            scope="all",
            full=False,
            allow_removals=False,
            job_id=None,
            actor=None,
        )

        assert result.status == "completed"
        assert not runner.ran("systemd-run")

    def test_a_rehearsal_starts_nothing_and_follows_nothing(self, host, fs, records) -> None:
        runner = FakeRunner()
        set_fs(DryRunFileSystem())
        manager = _manager(runner, host, fs, records)
        unit = UpdateUnit(records=records, runner=runner, fs=fs, host=host)

        result = start_and_follow(
            manager,
            unit,
            "0a1b2c3d",
            lambda line: None,
            scope="all",
            full=False,
            allow_removals=False,
            job_id=None,
            actor=None,
        )

        assert result.status == "dry-run"
        assert not runner.ran("journalctl")


def test_an_apply_plan_is_frozen_data() -> None:
    plan = ApplyPlan(UpdateScope.ALL, False, (), (), (), False, ())

    with pytest.raises(AttributeError):
        plan.full = True  # type: ignore[misc]
