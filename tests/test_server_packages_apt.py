"""
Tests for the apt backend: the listing, the security subset, the reboot flag and
unattended-upgrades.

The outputs are the ones apt, dpkg-query, apt-config and needrestart print
(``tests/fixtures/server/apt``); the assertions are on the exact argv, because a
package manager called with the wrong flag is a job that hangs on a question.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.managers.server import host as host_module
from noust.managers.server.errors import ServerError
from noust.managers.server.pkg.apt import (
    AptBackend,
    parse_config_dump,
    parse_needrestart,
    parse_removals,
    parse_upgrade_simulation,
)
from noust.managers.server.pkg.base import UpdateScope
from tests.server_support import fixture, make_host, platform_for


@pytest.fixture
def host(tmp_path: Path):
    return make_host(tmp_path)


@pytest.fixture
def fs() -> RecordingFileSystem:
    return RecordingFileSystem()


@pytest.fixture
def backend(runner: FakeRunner, host, fs) -> AptBackend:
    return AptBackend(platform_for("apt"), runner=runner, fs=fs, host=host)


class TestParsingTheSimulation:
    def test_the_real_line_of_a_package_with_two_origins_is_a_security_update(self) -> None:
        # Captured from `apt-get -s install --reinstall openssl` on Ubuntu 24.04.
        line = (
            "Inst openssl [3.0.13-0ubuntu3.15] (3.0.13-0ubuntu3.15 "
            "Ubuntu:24.04/noble-updates, Ubuntu:24.04/noble-security [amd64])"
        )

        packages, _ = parse_upgrade_simulation(line)

        assert len(packages) == 1
        assert packages[0].name == "openssl"
        assert packages[0].installed == "3.0.13-0ubuntu3.15"
        assert packages[0].security is True

    def test_a_package_from_the_updates_suite_alone_is_not_security(self) -> None:
        packages, _ = parse_upgrade_simulation(fixture("apt", "upgrade-simulation.txt"))

        by_name = {p.name: p for p in packages}
        assert by_name["bash"].security is False
        assert by_name["tzdata"].security is False
        assert by_name["libssl3t64"].security is True

    def test_a_package_new_to_the_machine_has_no_installed_version(self) -> None:
        packages, _ = parse_upgrade_simulation(fixture("apt", "upgrade-simulation.txt"))

        image = next(p for p in packages if p.name == "linux-image-6.8.0-100-generic")
        assert image.installed is None
        assert image.candidate == "6.8.0-100.100"
        assert image.kernel is True
        assert image.security is True

    def test_only_the_kernel_image_counts_as_a_kernel_update(self) -> None:
        packages, _ = parse_upgrade_simulation(fixture("apt", "upgrade-simulation.txt"))

        assert {p.name for p in packages if p.kernel} == {"linux-image-6.8.0-100-generic"}

    def test_the_kept_back_block_is_read_and_ends_at_the_next_line(self) -> None:
        _, kept_back = parse_upgrade_simulation(fixture("apt", "upgrade-simulation.txt"))

        assert kept_back == ["ubuntu-drivers-common"]

    def test_an_up_to_date_machine_lists_nothing(self) -> None:
        packages, kept_back = parse_upgrade_simulation(
            fixture("apt", "upgrade-simulation-clean.txt")
        )

        assert packages == []
        assert kept_back == []

    def test_a_suite_that_only_contains_security_in_its_name_is_not_security(self) -> None:
        line = "Inst foo [1] (2 Ubuntu:24.04/noble-security-backports [amd64])"

        packages, _ = parse_upgrade_simulation(line)

        assert packages[0].security is False

    def test_esm_and_debian_security_suites_count(self) -> None:
        text = "\n".join(
            [
                "Inst a [1] (2 UbuntuESM:20.04/focal-infra-security [amd64])",
                "Inst b [1] (2 Debian-Security:12/stable-security [amd64])",
            ]
        )

        packages, _ = parse_upgrade_simulation(text)

        assert [p.security for p in packages] == [True, True]

    def test_removals_come_from_the_remv_lines(self) -> None:
        assert parse_removals(fixture("apt", "full-upgrade-removals.txt")) == [
            "libfoo1t64",
            "python3-oldthing",
        ]


class TestListing:
    def test_it_simulates_with_the_environment_that_keeps_apt_quiet_and_in_english(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        runner.script(
            ["apt-get", "-s"], stdout=fixture("apt", "upgrade-simulation.txt"), exit_code=0
        )

        pending = backend.list_updates()

        assert runner.calls[0] == (
            "apt-get",
            "-s",
            "-o",
            "Debug::NoLocking=1",
            "--with-new-pkgs",
            "upgrade",
        )
        env = runner.envs[0]
        assert env is not None
        assert env["LC_ALL"] == "C"
        assert env["DEBIAN_FRONTEND"] == "noninteractive"
        assert env["NEEDRESTART_MODE"] == "l"
        assert env["APT_LISTCHANGES_FRONTEND"] == "none"
        assert pending.pending == 9
        assert pending.security == 7

    def test_holds_and_a_half_configured_database_are_reported(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        runner.script(["apt-get", "-s"], stdout=fixture("apt", "upgrade-simulation-clean.txt"))
        runner.script(["apt-mark", "showhold"], stdout="nginx\nlinux-image-generic\n")
        runner.script(
            ["dpkg", "--audit"], stdout="The following packages are only half configured:\n foo\n"
        )

        pending = backend.list_updates()

        assert pending.holds == ["nginx", "linux-image-generic"]
        assert pending.broken is True

    def test_the_age_of_the_lists_is_the_age_of_apts_own_stamp(
        self, backend: AptBackend, runner: FakeRunner, host
    ) -> None:
        runner.script(["apt-get", "-s"], stdout=fixture("apt", "upgrade-simulation-clean.txt"))
        stamp = host.apt_periodic / "update-success-stamp"
        stamp.write_text("")
        two_days_ago = time.time() - 2 * 86400
        os.utime(stamp, (two_days_ago, two_days_ago))

        pending = backend.list_updates()

        assert pending.lists_age_seconds is not None
        assert 2 * 86400 <= pending.lists_age_seconds < 2 * 86400 + 60

    def test_a_simulation_that_fails_carries_apts_own_words(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        runner.script(
            ["apt-get", "-s"],
            stderr="E: Unable to locate package foo",
            exit_code=100,
        )

        with pytest.raises(ServerError) as raised:
            backend.list_updates()

        assert "Unable to locate package foo" in (raised.value.output or "")


class TestApplying:
    def test_all_updates_never_remove_a_package_and_answer_dpkgs_questions(
        self, backend: AptBackend
    ) -> None:
        argv = backend.upgrade_argv(UpdateScope.ALL, [], full=False)

        assert argv == [
            "apt-get",
            "-y",
            "-q",
            "-o",
            "DPkg::Lock::Timeout=300",
            "-o",
            "Dpkg::Options::=--force-confdef",
            "-o",
            "Dpkg::Options::=--force-confold",
            "-o",
            "Dpkg::Use-Pty=0",
            "--with-new-pkgs",
            "upgrade",
        ]

    def test_a_full_upgrade_is_the_only_one_allowed_to_remove(self, backend: AptBackend) -> None:
        argv = backend.upgrade_argv(UpdateScope.ALL, [], full=True)

        assert argv[-1] == "full-upgrade"
        assert "--with-new-pkgs" not in argv

    def test_security_only_names_the_packages_and_refuses_to_remove(
        self, backend: AptBackend
    ) -> None:
        argv = backend.upgrade_argv(UpdateScope.SECURITY, ["openssl", "libssl3t64"], full=False)

        assert argv[-5:] == ["install", "--only-upgrade", "--no-remove", "openssl", "libssl3t64"]

    def test_security_only_with_nothing_to_install_says_so(self, backend: AptBackend) -> None:
        with pytest.raises(ServerError, match="no security updates"):
            backend.upgrade_argv(UpdateScope.SECURITY, [], full=False)

    def test_only_a_full_upgrade_is_simulated_for_removals(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        runner.script(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"],
            stdout=fixture("apt", "full-upgrade-removals.txt"),
        )

        assert backend.removals(UpdateScope.ALL, full=False) == []
        assert runner.calls == []
        assert backend.removals(UpdateScope.ALL, full=True) == ["libfoo1t64", "python3-oldthing"]

    def test_the_refresh_fails_when_a_mirror_fails(self, backend: AptBackend) -> None:
        argv = backend.refresh_argv()

        # Without --error-on=any apt exits 0 when a mirror is down, the lists
        # stay old and nobody is told.
        assert "--error-on=any" in argv
        assert argv[:2] == ["apt-get", "update"]

    def test_a_half_applied_update_is_repaired_with_dpkg_then_apt(
        self, backend: AptBackend
    ) -> None:
        assert backend.repair_commands() == [
            ["dpkg", "--configure", "-a"],
            ["apt-get", "-f", "install", "-y"],
        ]


class TestRebootAndServices:
    def test_the_flag_and_the_packages_behind_it_are_reported_without_repeats(
        self, backend: AptBackend, runner: FakeRunner, host
    ) -> None:
        host.reboot_required.write_text("*** System restart required ***\n")
        host.reboot_required_pkgs.write_text(fixture("apt", "reboot-required.pkgs"))
        runner.only_knows("apt-get")

        probe = backend.restart_probe()

        assert probe.reboot.required is True
        assert probe.reboot.packages == ("linux-image-6.8.0-100-generic", "linux-base")
        assert probe.reboot.since is not None
        assert probe.available is False

    def test_needrestart_reports_the_kernel_and_the_services(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        runner.script(["needrestart", "-b"], stdout=fixture("apt", "needrestart-batch.txt"))

        probe = backend.restart_probe()

        assert runner.calls[-1] == ("needrestart", "-b")
        assert probe.reboot.required is True
        assert any(
            "6.8.0-45-generic" in r and "6.8.0-100-generic" in r for r in probe.reboot.reasons
        )
        assert probe.services == ("cron.service", "nginx.service", "php8.3-fpm.service")
        assert probe.available is True

    def test_a_current_kernel_needs_no_reboot_but_lists_nothing_either(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        runner.script(["needrestart", "-b"], stdout=fixture("apt", "needrestart-batch-current.txt"))

        probe = backend.restart_probe()

        assert probe.reboot.required is False
        assert probe.services == ()

    def test_without_needrestart_the_kernel_on_disk_is_compared_with_the_running_one(
        self, backend: AptBackend, runner: FakeRunner, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner.only_knows("apt-get")
        (host.boot / "vmlinuz-6.8.0-45-generic").write_text("")
        (host.boot / "vmlinuz-6.8.0-100-generic").write_text("")
        monkeypatch.setattr(
            "noust.managers.server.pkg.apt.running_kernel", lambda: "6.8.0-45-generic"
        )

        probe = backend.restart_probe()

        # 100 is newer than 45: a plain string sort gets this wrong.
        assert probe.reboot.required is True
        assert "6.8.0-100-generic" in probe.reboot.reasons[0]
        assert probe.reboot.source == "kernel"

    def test_the_newest_kernel_being_the_running_one_is_no_reason(
        self, backend: AptBackend, runner: FakeRunner, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner.only_knows("apt-get")
        (host.boot / "vmlinuz-6.8.0-100-generic").write_text("")
        monkeypatch.setattr(
            "noust.managers.server.pkg.apt.running_kernel", lambda: "6.8.0-100-generic"
        )

        assert backend.restart_probe().reboot.required is False

    def test_needrestart_batch_is_keyed_without_its_prefix(self) -> None:
        fields = parse_needrestart(fixture("apt", "needrestart-batch.txt"))

        assert fields["KSTA"] == ["3"]
        assert fields["SVC"] == ["cron.service", "nginx.service", "php8.3-fpm.service"]


def _script_auto(
    runner: FakeRunner, dump: str, *, installed: bool = True, timers: bool = True
) -> None:
    runner.script(["dpkg-query"], stdout="ii " if installed else "")
    runner.script(["apt-config", "dump"], stdout=dump)
    runner.script(
        ["systemctl", "is-enabled"], stdout="enabled\nenabled\n", exit_code=0 if timers else 1
    )


class TestAutomaticUpdates:
    def test_the_real_apt_config_dump_reads_as_security_only(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        _script_auto(runner, fixture("apt", "config-dump-unattended.txt"))

        status = backend.auto_status()

        assert status.mechanism == "unattended-upgrades"
        assert status.installed is True
        assert status.enabled is True
        assert status.security_only is True
        assert status.reboots is False

    def test_the_updates_suite_and_automatic_reboot_are_read(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        _script_auto(runner, fixture("apt", "config-dump-unattended-all.txt"))

        status = backend.auto_status()

        assert status.security_only is False
        assert status.reboots is True

    def test_configured_but_with_disabled_timers_is_not_enabled(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        _script_auto(runner, fixture("apt", "config-dump-unattended.txt"), timers=False)

        status = backend.auto_status()

        assert status.enabled is False
        assert "timers" in status.detail

    def test_a_zero_turns_it_off(self, backend: AptBackend, runner: FakeRunner) -> None:
        _script_auto(runner, fixture("apt", "config-dump-off.txt"))

        assert backend.auto_status().enabled is False

    def test_enabling_installs_the_package_writes_the_files_and_enables_the_timers(
        self, backend: AptBackend, runner: FakeRunner, host, fs: RecordingFileSystem
    ) -> None:
        # Not installed at first; the state read after the change is the enabled one.
        answers = iter(["", "ii ", "ii "])
        runner.script(["apt-config", "dump"], stdout=fixture("apt", "config-dump-unattended.txt"))
        runner.script(["systemctl", "is-enabled"], stdout="enabled\nenabled\n")
        original = runner.run

        def run(argv, **kwargs):
            if argv[0] == "dpkg-query":
                runner.script(["dpkg-query"], stdout=next(answers))
            return original(argv, **kwargs)

        runner.run = run  # type: ignore[method-assign]
        lines: list[str] = []

        steps = backend.set_auto(True, True, lines.append)

        assert ("apt-get", "install", "-y", "unattended-upgrades") in runner.calls
        assert (
            "systemctl",
            "enable",
            "--now",
            "apt-daily.timer",
            "apt-daily-upgrade.timer",
        ) in runner.calls
        written = (host.apt_conf_d / "20auto-upgrades").read_text()
        assert 'APT::Periodic::Unattended-Upgrade "1";' in written
        assert "Generated by Noust" in written
        assert any("Installed unattended-upgrades" in step for step in steps)
        assert ("write", host.apt_conf_d / "20auto-upgrades") in fs.changes

    def test_all_updates_add_the_updates_suite_in_a_file_of_ours(
        self, backend: AptBackend, runner: FakeRunner, host
    ) -> None:
        _script_auto(runner, fixture("apt", "config-dump-unattended-all.txt"))

        backend.set_auto(True, False, lambda line: None)

        extra = (host.apt_conf_d / "52noust-unattended-upgrades").read_text()
        assert "-updates" in extra

    def test_going_back_to_security_only_removes_that_file(
        self, backend: AptBackend, runner: FakeRunner, host
    ) -> None:
        extra = host.apt_conf_d / "52noust-unattended-upgrades"
        extra.write_text("x")
        _script_auto(runner, fixture("apt", "config-dump-unattended.txt"))

        backend.set_auto(True, True, lambda line: None)

        assert not extra.exists()

    def test_an_operators_file_is_kept_as_a_backup_before_it_is_replaced(
        self, backend: AptBackend, runner: FakeRunner, host
    ) -> None:
        mine = host.apt_conf_d / "20auto-upgrades"
        mine.write_text('APT::Periodic::Unattended-Upgrade "1";\n// operator note\n')
        _script_auto(runner, fixture("apt", "config-dump-unattended.txt"))

        backend.set_auto(True, True, lambda line: None)

        assert "operator note" in (host.apt_conf_d / "20auto-upgrades.noust-bak").read_text()

    def test_a_setting_another_file_overrides_is_reported_instead_of_pretended(
        self, backend: AptBackend, runner: FakeRunner
    ) -> None:
        # We write "1", but apt still reads "0": a later file wins.
        _script_auto(runner, fixture("apt", "config-dump-off.txt"))

        with pytest.raises(ServerError, match="not in effect"):
            backend.set_auto(True, True, lambda line: None)

    def test_the_config_dump_keeps_every_value_of_a_list_option(self) -> None:
        values = parse_config_dump(fixture("apt", "config-dump-unattended.txt"))

        assert len(values["Unattended-Upgrade::Allowed-Origins::"]) == 4


def test_natural_kernel_order_puts_100_after_45() -> None:
    key = host_module.natural_key

    assert key("6.8.0-100-generic") > key("6.8.0-45-generic")
