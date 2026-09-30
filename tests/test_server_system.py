"""
Tests for the system tab's managers: the clock, the host name, the operating
system and its end of life, the journal of any unit, and the process list.

The outputs of timedatectl, hostnamectl and journalctl are the ones this
Ubuntu 24.04 machine printed (``tests/fixtures/server/system`` and
``journal``, with the host name and machine id replaced). The tests that matter
most are the ones about what is *not* let through: a time zone that is a path,
a journal unit that is an option, a host name that is a command.
"""

from __future__ import annotations

import types
from datetime import date
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.managers.server import eol
from noust.managers.server.clock import (
    ClockManager,
    parse_chrony_offset,
    parse_show,
    parse_status,
)
from noust.managers.server.errors import ServerError
from noust.managers.server.host import OsRelease, read_os_release
from noust.managers.server.identity import (
    IdentityManager,
    parse_hostnamectl_status,
    rewrite_hosts,
    valid_hostname,
)
from noust.managers.server.journal import (
    MAX_MESSAGE,
    JournalReader,
    decode_message,
    parse_boots,
    parse_entries,
)
from noust.managers.server.processes import (
    ProcessRow,
    group_by_unit,
    list_processes,
    unit_of_cgroup,
)
from tests.server_support import fixture, make_host, platform_for


@pytest.fixture
def host(tmp_path: Path):
    return make_host(tmp_path / "root")


class TestClock:
    def _manager(self, runner: FakeRunner, host, **kwargs) -> ClockManager:
        return ClockManager(
            runner=runner,
            fs=RecordingFileSystem(),
            host=host,
            platform=platform_for("apt"),
            **kwargs,
        )

    def test_the_real_timedatectl_show_is_read(self, host) -> None:
        runner = FakeRunner().only_knows("timedatectl")
        runner.script(["timedatectl", "show"], stdout=fixture("system", "timedatectl-show.txt"))

        status = self._manager(runner, host).status()

        assert status.timezone == "Atlantic/Canary"
        assert status.ntp_supported is True
        assert status.ntp_enabled is False
        assert status.synchronized is False
        assert status.local_rtc is False
        assert status.offset_seconds is None

    def test_the_real_status_output_is_the_fallback_for_a_systemd_without_show(self, host) -> None:
        runner = FakeRunner().only_knows("timedatectl")
        runner.script(["timedatectl", "show"], stderr="Unknown operation show", exit_code=1)
        runner.script(["timedatectl", "status"], stdout=fixture("system", "timedatectl-status.txt"))

        status = self._manager(runner, host).status()

        assert status.timezone == "Atlantic/Canary"
        assert status.synchronized is False
        assert status.ntp_enabled is False
        assert status.local_rtc is False
        assert status.local_time == "Tue 2026-09-29 22:33:24 WEST"

    def test_a_clock_that_cannot_be_read_says_why_with_the_tools_words(self, host) -> None:
        runner = FakeRunner().only_knows("timedatectl")
        runner.script(["timedatectl"], stderr="Failed to connect to bus", exit_code=1)

        with pytest.raises(ServerError) as raised:
            self._manager(runner, host).status()

        assert "Failed to connect to bus" in (raised.value.output or "")

    def test_chrony_says_how_far_off_the_clock_is(self, host) -> None:
        runner = FakeRunner()
        runner.script(["timedatectl", "show"], stdout=fixture("system", "timedatectl-show.txt"))
        runner.script(
            ["chronyc", "-c", "tracking"],
            stdout="A29FC801,162.159.200.1,3,1759180000.123456789,-0.000012345,0.000003216,"
            "0.000030721,5.171,0.023,0.313,0.031503677,0.001528731,1040.9,Normal\n",
        )

        assert self._manager(runner, host).status().offset_seconds == pytest.approx(-0.000012345)

    def test_properties_parse_both_ways(self) -> None:
        assert parse_show("Timezone=UTC\nNTP=yes\n") == {"Timezone": "UTC", "NTP": "yes"}
        assert (
            parse_status("Time zone: Europe/Madrid (CET, +0100)\n")["Timezone"] == "Europe/Madrid"
        )
        assert parse_chrony_offset("garbage") is None

    def test_a_valid_zone_is_set_and_the_timers_it_moves_are_named(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("zoneinfo.available_timezones", lambda: {"Europe/Madrid", "UTC"})
        runner = FakeRunner()
        runner.script(["timedatectl", "show"], stdout=fixture("system", "timedatectl-show.txt"))
        runner.script(
            ["systemctl", "list-timers"],
            stdout=(
                "Wed 2026-09-30 03:00:00 UTC 5h left n/a n/a noust-backup-shop.timer noust-backup-shop.service\n"
                "Wed 2026-09-30 04:00:00 UTC 6h left n/a n/a noust-cron-report.timer noust-cron-report.service\n"
            ),
        )

        change = self._manager(runner, host).set_timezone("Europe/Madrid")

        assert ("timedatectl", "set-timezone", "Europe/Madrid") in runner.calls
        assert change.previous == "Atlantic/Canary"
        assert change.moved_timers == ["noust-backup-shop.timer", "noust-cron-report.timer"]

    @pytest.mark.parametrize(
        "zone",
        ["Europe/Madrid; reboot", "../etc/passwd", "/etc/passwd", "", "Nowhere/City", "a b", "-x"],
    )
    def test_a_zone_that_is_a_path_an_option_or_unknown_never_reaches_timedatectl(
        self, host, zone: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("zoneinfo.available_timezones", lambda: {"Europe/Madrid", "UTC"})
        runner = FakeRunner()

        with pytest.raises(ValidationError, match="Unknown time zone"):
            self._manager(runner, host).set_timezone(zone)

        assert not runner.ran("timedatectl", "set-timezone")

    def test_a_zone_the_python_database_lacks_is_found_on_disk(
        self, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("zoneinfo.available_timezones", lambda: set())
        zone = host.at("/usr/share/zoneinfo/Europe/Madrid")
        zone.parent.mkdir(parents=True)
        zone.write_text("")

        assert self._manager(FakeRunner(), host).valid_timezone("Europe/Madrid") is True

    def test_ntp_is_switched_with_timedatectl_and_its_output_returned(self, host) -> None:
        runner = FakeRunner()

        self._manager(runner, host).set_ntp(True)

        assert ("timedatectl", "set-ntp", "true") in runner.calls

    def test_without_a_time_daemon_it_says_to_install_one_and_installs_only_when_told(
        self, host
    ) -> None:
        runner = FakeRunner()
        runner.script(
            ["timedatectl", "set-ntp"], stderr="Failed to set ntp: NTP not supported", exit_code=1
        )

        with pytest.raises(ServerError, match="no time daemon") as raised:
            self._manager(runner, host).set_ntp(True)
        assert "NTP not supported" in (raised.value.output or "")
        assert not runner.ran("apt-get")

    def test_allowed_to_install_it_installs_chrony_with_the_package_managers_environment(
        self, host
    ) -> None:
        from tests.server_support import SequencedRunner

        runner = SequencedRunner()
        runner.sequence(
            ["timedatectl", "set-ntp"],
            [{"stderr": "NTP not supported", "exit_code": 1}, {"stdout": ""}],
        )

        message = self._manager(runner, host).set_ntp(True, install=True)

        assert ("apt-get", "install", "-y", "chrony") in runner.calls
        install_env = runner.envs[runner.calls.index(("apt-get", "install", "-y", "chrony"))]
        assert install_env is not None and install_env["DEBIAN_FRONTEND"] == "noninteractive"
        assert "Installed chrony" in message

    def test_a_failed_installation_carries_the_package_managers_output(self, host) -> None:
        runner = FakeRunner()
        runner.script(["timedatectl", "set-ntp"], stderr="NTP not supported", exit_code=1)
        runner.script(
            ["apt-get", "install"], stdout="E: Unable to locate package chrony", exit_code=100
        )

        with pytest.raises(ServerError, match="Could not install chrony") as raised:
            self._manager(runner, host).set_ntp(True, install=True)

        assert "Unable to locate package" in (raised.value.output or "")


class TestHostname:
    def _manager(self, runner: FakeRunner, host, family: str = "apt") -> IdentityManager:
        return IdentityManager(
            runner=runner, fs=RecordingFileSystem(), host=host, platform=platform_for(family)
        )

    @pytest.mark.parametrize("good", ["vps-1", "web.example.com", "a", "x" * 63, "a-b.c-d"])
    def test_valid_names(self, good: str) -> None:
        assert valid_hostname(good) is True

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "-web",
            "web-",
            "Web",
            "web_1",
            "web server",
            "a" * 64,
            "a..b",
            "web;reboot",
            "$(id)",
            ".",
            "x" * 254,
        ],
    )
    def test_a_name_that_is_not_a_dns_label_is_refused(self, bad: str) -> None:
        assert valid_hostname(bad) is False

    def test_the_real_hostnamectl_json_gives_the_names(self, host) -> None:
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))

        info = self._manager(runner, host).hostname()

        assert info.hostname == "vps-1"
        assert info.static == "vps-1"
        assert info.pretty is None
        assert info.machine_id == "0123456789abcdef0123456789abcdef"
        assert info.chassis == "container"

    def test_hostnamectl_status_is_the_fallback_before_systemd_250(self, host) -> None:
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stderr="unrecognized option", exit_code=1)
        runner.script(
            ["hostnamectl", "status"],
            stdout="   Static hostname: web-1\n         Machine ID: abc\n           Boot ID: def\n"
            "           Chassis: vm\n",
        )

        info = self._manager(runner, host).hostname()

        assert info.hostname == "web-1"
        assert info.chassis == "vm"
        assert parse_hostnamectl_status("Static hostname: x\n")["Hostname"] == "x"

    def test_renaming_updates_hostnamectl_and_the_hosts_line_debian_resolves_itself_by(
        self, host
    ) -> None:
        host.etc_hosts.write_text("127.0.0.1 localhost\n127.0.1.1 vps-1\n::1 ip6-localhost\n")
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))

        steps = self._manager(runner, host).set_hostname("web-2.example.com")

        assert ("hostnamectl", "set-hostname", "web-2.example.com") in runner.calls
        assert host.etc_hosts.read_text() == (
            "127.0.0.1 localhost\n127.0.1.1 web-2.example.com web-2\n::1 ip6-localhost\n"
        )
        assert host.etc_hosts.with_name("hosts.noust-bak").read_text().count("vps-1") == 1
        assert any("127.0.1.1" in step for step in steps)

    def test_a_system_that_is_not_debian_leaves_hosts_alone(self, host) -> None:
        host.etc_hosts.write_text("127.0.1.1 vps-1\n")
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))

        self._manager(runner, host, "dnf").set_hostname("web-2")

        assert host.etc_hosts.read_text() == "127.0.1.1 vps-1\n"

    def test_a_name_that_is_a_command_never_reaches_hostnamectl(self, host) -> None:
        runner = FakeRunner()

        with pytest.raises(ValidationError, match="Not a valid host name"):
            self._manager(runner, host).set_hostname("web;reboot")

        assert not runner.ran("hostnamectl", "set-hostname")

    def test_hostnamectl_refusing_is_an_error_with_its_output(self, host) -> None:
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))
        runner.script(["hostnamectl", "set-hostname"], stderr="Access denied", exit_code=1)

        with pytest.raises(ServerError) as raised:
            self._manager(runner, host).set_hostname("web-2")

        assert "Access denied" in (raised.value.output or "")

    def _cloud(self, host, text: str) -> None:
        (host.root / "etc/cloud").mkdir(parents=True, exist_ok=True)
        (host.root / "etc/cloud/cloud.cfg").write_text(text)
        host.cloud_cfg_d.mkdir(parents=True, exist_ok=True)

    def test_cloud_init_that_renames_the_host_on_every_boot_is_warned_about(self, host) -> None:
        self._cloud(host, "users:\n  - default\n")
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))

        steps = self._manager(runner, host).set_hostname("web-2")

        assert any(step.startswith("Warning: cloud-init") for step in steps)
        assert not (host.cloud_cfg_d / "99-noust-hostname.cfg").exists()

    def test_asked_to_it_tells_cloud_init_to_keep_the_name(self, host) -> None:
        self._cloud(host, "users:\n  - default\n")
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))

        self._manager(runner, host).set_hostname("web-2", keep_against_cloud_init=True)

        assert "preserve_hostname: true" in (host.cloud_cfg_d / "99-noust-hostname.cfg").read_text()

    def test_cloud_init_already_told_to_preserve_is_not_warned_about(self, host) -> None:
        self._cloud(host, "preserve_hostname: true\n")
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))

        steps = self._manager(runner, host).set_hostname("web-2")

        assert not any("cloud-init" in step for step in steps)

    def test_only_the_127_0_1_1_line_is_rewritten(self) -> None:
        text = "127.0.0.1 localhost\n10.0.0.5 db\n"

        assert rewrite_hosts(text, "old", "new") == (text, False)
        assert rewrite_hosts("127.0.1.1 old # my name\n", "old", "new") == ("127.0.1.1 new\n", True)


class TestEndOfLife:
    TODAY = date(2026, 9, 29)

    def _status(self, os_id: str, version: str, **extra) -> eol.EolStatus:
        return eol.status_of(OsRelease(id=os_id, version_id=version, **extra), today=self.TODAY)

    def test_a_supported_version_says_how_long_is_left(self) -> None:
        status = self._status("ubuntu", "24.04")

        assert status.status == "ok"
        assert status.end_date == "2029-05-31"
        assert status.days_left == (date(2029, 5, 31) - self.TODAY).days
        assert status.source == "table"

    def test_debian_11_and_leap_15_6_are_already_out_of_support_today(self) -> None:
        assert self._status("debian", "11").status == "expired"
        assert self._status("opensuse-leap", "15.6").status == "expired"
        assert self._status("debian", "11").days_left is not None
        assert self._status("debian", "11").days_left < 0  # type: ignore[operator]

    def test_within_six_months_is_a_warning(self) -> None:
        status = self._status("fedora", "43")

        assert status.status == "warn"
        assert status.days_left == (date(2026, 12, 9) - self.TODAY).days

    def test_a_minor_version_matches_its_major_row(self) -> None:
        assert self._status("rocky", "9.5").end_date == "2032-05-31"
        assert self._status("almalinux", "8.10").end_date == "2029-05-31"

    def test_a_prefix_is_a_whole_component_not_a_string_prefix(self) -> None:
        # Amazon Linux 2 must not answer for 2023, nor Ubuntu 2 for 24.04.
        assert self._status("amzn", "2023").end_date == "2029-06-30"
        assert self._status("amzn", "2").end_date == "2026-06-30"
        assert self._status("ubuntu", "24.04.1").end_date == "2029-05-31"

    def test_the_distribution_saying_its_own_end_is_believed_over_the_table(self) -> None:
        status = self._status("fedora", "42", support_end="2027-01-01")

        assert status.source == "os-release"
        assert status.end_date == "2027-01-01"

    def test_a_rolling_release_has_no_end(self) -> None:
        assert self._status("opensuse-tumbleweed", "20260929").status == "rolling"

    def test_a_distribution_the_table_does_not_know_is_unknown_not_fine(self) -> None:
        status = self._status("nixos", "25.05")

        assert status.status == "unknown"
        assert status.end_date is None

    def test_a_garbled_end_date_is_unknown(self) -> None:
        assert self._status("fedora", "42", support_end="soon").status == "unknown"

    def test_the_lookup_takes_the_longest_matching_row(self) -> None:
        assert eol.lookup("opensuse-leap", "15.6") == "2026-04-30"
        assert eol.lookup("opensuse-leap", "15") is None


class TestIdentity:
    def test_it_reads_the_os_the_hostname_the_eol_and_the_uptime(self, host) -> None:
        host.os_release.write_text(
            'PRETTY_NAME="Ubuntu 24.04.5 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n'
            "VERSION_CODENAME=noble\nID=ubuntu\nID_LIKE=debian\n"
        )
        host.proc_uptime.write_text("3600.5 100.0\n")
        runner = FakeRunner()
        runner.script(["hostnamectl", "--json=short"], stdout=fixture("system", "hostnamectl.json"))
        manager = IdentityManager(
            runner=runner, host=host, platform=platform_for("apt"), fs=RecordingFileSystem()
        )

        identity = manager.identity(today=date(2026, 9, 29))

        assert identity.os_name == "Ubuntu 24.04.5 LTS"
        assert identity.os_id == "ubuntu"
        assert identity.codename == "noble"
        assert identity.hostname.hostname == "vps-1"
        assert identity.eol.status == "ok"
        assert identity.uptime_seconds == 3600.5
        assert identity.booted_at is not None
        assert identity.cpu_count >= 1
        assert len(identity.load) == 3
        assert identity.to_dict()["hostname"]["static"] == "vps-1"

    def test_os_release_parses_quotes_and_falls_back_to_the_usr_copy(self, host) -> None:
        host.usr_os_release.parent.mkdir(parents=True, exist_ok=True)
        host.usr_os_release.write_text(
            'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.5"\n'
        )

        release = read_os_release(host)

        assert release.id == "rocky"
        assert release.id_like == ("rhel", "centos", "fedora")
        assert release.pretty_name == "Linux"

    def test_no_os_release_at_all_is_generic_linux(self, tmp_path: Path) -> None:
        assert read_os_release(make_host(tmp_path / "empty")).id == "linux"


class TestJournalCommand:
    def _reader(self) -> JournalReader:
        return JournalReader(runner=FakeRunner())

    def test_the_unit_is_glued_to_its_option_so_it_can_never_be_one(self) -> None:
        argv = self._reader().command(unit="nginx.service", lines=50)

        assert "--unit=nginx.service" in argv
        assert "--lines=51" in argv
        assert "--no-pager" in argv

    @pytest.mark.parametrize("unit", ["--all", "-x", "a b", "$(id)", "a;b", "", "x" * 300, "../x"])
    def test_a_unit_that_is_not_a_name_is_refused(self, unit: str) -> None:
        with pytest.raises(ValidationError, match="Not a unit name"):
            self._reader().command(unit=unit)

    def test_priority_by_name_or_number(self) -> None:
        assert "--priority=3" in self._reader().command(priority="err")
        assert "--priority=4" in self._reader().command(priority=4)
        assert "--priority=7" in self._reader().command(priority="7")

    @pytest.mark.parametrize("bad", ["loud", 8, "-1", "3;4"])
    def test_a_priority_that_is_not_a_level_is_refused(self, bad) -> None:
        with pytest.raises(ValidationError, match="Not a priority"):
            self._reader().command(priority=bad)

    @pytest.mark.parametrize(
        "good", ["2026-09-29", "2026-09-29 10:30", "2026-09-29T10:30:15", "-30min", "-2h", "-7d"]
    )
    def test_times_journalctl_documents_are_accepted(self, good: str) -> None:
        assert f"--since={good}" in self._reader().command(since=good)

    @pytest.mark.parametrize(
        "bad", ["yesterday", "--boot", "10:30", "-5x", "2026-09-29; reboot", "$(date)"]
    )
    def test_any_other_time_is_refused(self, bad: str) -> None:
        with pytest.raises(ValidationError, match="not a time"):
            self._reader().command(since=bad)

    @pytest.mark.parametrize("lines", [0, -1, 1001, "50", True])
    def test_the_line_count_is_a_number_in_range(self, lines) -> None:
        with pytest.raises(ValidationError):
            self._reader().command(lines=lines)

    def test_boot_kernel_and_cursor(self) -> None:
        argv = self._reader().command(boot=-1, kernel=True, after_cursor="s=abc;i=1;b=2")

        assert "--boot=-1" in argv
        assert "--dmesg" in argv
        assert "--after-cursor=s=abc;i=1;b=2" in argv

    @pytest.mark.parametrize("cursor", ["; rm", "a b", "--all"])
    def test_a_cursor_that_is_not_one_is_refused(self, cursor: str) -> None:
        with pytest.raises(ValidationError, match="cursor"):
            self._reader().command(after_cursor=cursor)

    def test_a_boot_in_the_future_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            self._reader().command(boot=1)


class TestJournalReading:
    def test_the_real_entries_are_read_with_time_priority_unit_and_cursor(self) -> None:
        entries = parse_entries(fixture("journal", "entries.json"))

        assert len(entries) == 3
        first = entries[0]
        assert first.cursor.startswith("s=")
        assert first.timestamp.startswith("2026-09-29T")
        assert first.priority == 6
        assert first.unit
        assert first.message

    def test_a_message_that_is_not_utf8_arrives_as_bytes_and_is_decoded(self) -> None:
        assert decode_message([104, 105, 255]) == "hi�"
        assert decode_message(None) == ""

    def test_a_runaway_line_is_cut(self) -> None:
        assert len(decode_message("x" * 10_000)) == MAX_MESSAGE

    def test_a_line_that_is_not_json_is_skipped(self) -> None:
        assert (
            parse_entries('half a line\n{"__CURSOR":"c","MESSAGE":"ok","PRIORITY":"3"}\n')[
                0
            ].priority
            == 3
        )

    def test_reading_asks_for_one_more_line_to_know_whether_there_is_more(self) -> None:
        runner = FakeRunner()
        lines = "\n".join(
            f'{{"__CURSOR":"c{n}","MESSAGE":"line {n}","__REALTIME_TIMESTAMP":"1790716408434957"}}'
            for n in range(4)
        )
        runner.script(["journalctl"], stdout=lines)

        page = JournalReader(runner=runner).read(unit="nginx", lines=3)

        assert page.truncated is True
        assert [e.message for e in page.entries] == ["line 1", "line 2", "line 3"]
        assert page.next_cursor == "c3"

    def test_a_text_filter_keeps_matching_messages_ignoring_case(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"],
            stdout='{"__CURSOR":"a","MESSAGE":"Connection refused"}\n{"__CURSOR":"b","MESSAGE":"all fine"}\n',
        )

        page = JournalReader(runner=runner).read(text="REFUSED", lines=10)

        assert [e.message for e in page.entries] == ["Connection refused"]
        assert page.truncated is False

    def test_a_failure_carries_journalctls_own_words(self) -> None:
        runner = FakeRunner()
        runner.script(
            ["journalctl"], stderr="Failed to open journal: Permission denied", exit_code=1
        )

        with pytest.raises(ServerError) as raised:
            JournalReader(runner=runner).read(unit="nginx")

        assert "Permission denied" in (raised.value.output or "")

    def test_following_streams_the_journals_own_lines_with_the_same_checked_filters(self) -> None:
        runner = FakeRunner()
        runner.script(["journalctl"], stdout="2026-09-29T22:13:28+0100 vps-1 nginx[1]: started")
        seen: list[str] = []

        JournalReader(runner=runner).follow(seen.append, unit="nginx", lines=20, priority="err")

        argv = runner.calls[0]
        assert argv[0] == "journalctl"
        assert "--unit=nginx" in argv and "--priority=3" in argv
        assert "--output=short-iso" in argv and "--output=json" not in argv
        assert "--lines=20" in argv and argv[-1] == "--follow"
        assert seen == ["2026-09-29T22:13:28+0100 vps-1 nginx[1]: started"]

    def test_following_a_unit_that_is_an_option_never_runs_journalctl(self) -> None:
        runner = FakeRunner()

        with pytest.raises(ValidationError):
            JournalReader(runner=runner).follow(lambda line: None, unit="--all")

        assert runner.calls == []

    def test_the_boots_are_read_from_the_real_list(self) -> None:
        boots = parse_boots(
            "IDX BOOT ID                          FIRST ENTRY                  LAST ENTRY\n"
            "-12 abeb408dc0f148b19e82c79a9be3603b Thu 2026-09-17 06:49:52 WEST Thu 2026-09-17 07:06:02 WEST\n"
            "  0 fedcba9876543210fedcba9876543210 Tue 2026-09-29 06:12:00 WEST Tue 2026-09-29 22:13:35 WEST\n"
        )

        assert [b.index for b in boots] == [-12, 0]
        assert boots[0].first == "Thu 2026-09-17 06:49:52 WEST"


class TestProcesses:
    def _psutil(self, monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
        class Gone(Exception):
            pass

        def process(info: dict) -> types.SimpleNamespace:
            return types.SimpleNamespace(info=info)

        def iterator(fields):
            for row in rows:
                if row.get("denied"):
                    raise Gone()
                yield process({k: v for k, v in row.items() if k in fields or k == "pid"})

        fake = types.SimpleNamespace(
            process_iter=iterator, NoSuchProcess=Gone, AccessDenied=Gone, ZombieProcess=Gone
        )
        monkeypatch.setattr("noust.managers.server.processes._psutil", lambda: fake)

    def _row(self, pid: int, name: str, cpu: float, mem: float, rss_mb: int, **extra) -> dict:
        return {
            "pid": pid,
            "name": name,
            "cpu_percent": cpu,
            "memory_percent": mem,
            "memory_info": types.SimpleNamespace(rss=rss_mb * 1024**2),
            "status": "sleeping",
            "username": "www-data",
            "cmdline": ["/usr/sbin/nginx", "-g", "daemon off;", "--secret=hunter2"],
            **extra,
        }

    @pytest.mark.parametrize(
        ("text", "unit"),
        [
            ("0::/system.slice/nginx.service\n", "nginx.service"),
            ("0::/user.slice/user-1000.slice/session-2.scope\n", "session-2.scope"),
            ("0::/system.slice/docker.service/abc123\n", "docker.service"),
            (
                "12:memory:/system.slice/cron.service\n0::/system.slice/cron.service\n",
                "cron.service",
            ),
            ("0::/\n", None),
            ("", None),
        ],
    )
    def test_a_process_belongs_to_the_unit_its_cgroup_names(
        self, text: str, unit: str | None
    ) -> None:
        assert unit_of_cgroup(text) == unit

    def test_sorted_by_cpu_limited_and_counted_in_all(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._psutil(
            monkeypatch,
            [
                self._row(1, "a", 1.0, 1.0, 10),
                self._row(2, "b", 50.0, 2.0, 20),
                self._row(3, "c", 9.0, 3.0, 30),
            ],
        )

        rows, total = list_processes(limit=2, proc_root=tmp_path)

        assert [r.name for r in rows] == ["b", "c"]
        assert total == 3

    @pytest.mark.parametrize(
        ("key", "first"), [("memory", "c"), ("pid", "a"), ("name", "a"), ("nonsense", "b")]
    )
    def test_each_sort_key_and_an_unknown_one_falling_back_to_cpu(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str, first: str
    ) -> None:
        self._psutil(
            monkeypatch,
            [
                self._row(1, "a", 1.0, 1.0, 10),
                self._row(2, "b", 50.0, 2.0, 20),
                self._row(3, "c", 9.0, 3.0, 30),
            ],
        )

        rows, _ = list_processes(sort_by=key, proc_root=tmp_path)

        assert rows[0].name == first

    def test_a_command_line_is_only_included_when_the_caller_may_see_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._psutil(monkeypatch, [self._row(1, "nginx", 1.0, 1.0, 10)])

        hidden, _ = list_processes(proc_root=tmp_path)
        shown, _ = list_processes(proc_root=tmp_path, show_commands=True)

        assert hidden[0].command is None
        assert shown[0].command is not None and "--secret" in shown[0].command

    def test_a_process_that_vanished_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # psutil raises while iterating; the listing goes on with what it has.
        self._psutil(monkeypatch, [self._row(1, "a", 1.0, 1.0, 10)])

        rows, total = list_processes(proc_root=tmp_path)

        assert total == 1 and rows[0].name == "a"

    def test_processes_of_a_unit_are_added_up_and_the_unitless_are_left_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for pid, unit in ((1, "nginx.service"), (2, "nginx.service"), (3, "cron.service")):
            (tmp_path / str(pid)).mkdir()
            (tmp_path / str(pid) / "cgroup").write_text(f"0::/system.slice/{unit}\n")
        self._psutil(
            monkeypatch,
            [
                self._row(1, "nginx", 1.0, 1.0, 10),
                self._row(2, "nginx", 2.0, 1.5, 30),
                self._row(3, "cron", 0.0, 0.1, 2),
                self._row(4, "bash", 0.0, 0.1, 5),
            ],
        )

        rows, _ = list_processes(proc_root=tmp_path)
        grouped = group_by_unit(rows)

        assert [(u.unit, u.processes, u.memory_mb) for u in grouped] == [
            ("nginx.service", 2, 40.0),
            ("cron.service", 1, 2.0),
        ]
        assert isinstance(rows[0], ProcessRow)
