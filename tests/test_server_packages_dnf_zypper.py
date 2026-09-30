"""
Tests for the dnf and zypper backends.

dnf 4 and dnf 5 print different tables and the parser reads both; zypper keeps
"security" on patches, and Tumbleweed has no patches at all. The dnf 5 and
zypper outputs are written from the tools' documented formats (they are not on
the machine the fixtures were captured on), so the harness has to confirm them
against a real Fedora and a real openSUSE before the parsers are trusted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.managers.server.errors import ServerError, UnsupportedHostError
from noust.managers.server.pkg import UpdateScope, backend_for
from noust.managers.server.pkg.dnf import (
    DnfBackend,
    parse_advisories,
    parse_check_update,
    set_ini_value,
)
from noust.managers.server.pkg.zypper import (
    ZypperBackend,
    parse_removed,
    parse_updates_xml,
    read_shell_var,
    set_shell_var,
)
from tests.server_support import fixture, make_host, platform_for


@pytest.fixture
def host(tmp_path: Path):
    return make_host(tmp_path)


@pytest.fixture
def fs() -> RecordingFileSystem:
    return RecordingFileSystem()


class TestDnfListing:
    def test_dnf4_rows_are_read_and_a_wrapped_line_is_joined(self) -> None:
        rows = parse_check_update(fixture("dnf4", "check-update.txt"))

        names = [row[0] for row in rows]
        assert names == [
            "bash",
            "kernel",
            "kernel-core",
            "kernel-modules",
            "NetworkManager-libnm",
            "openssl-libs",
            "tzdata",
        ]
        wrapped = next(row for row in rows if row[0] == "NetworkManager-libnm")
        assert wrapped == ("NetworkManager-libnm", "x86_64", "1:1.48.10-2.el9_5", "baseos")

    def test_the_obsoleting_section_is_not_an_update(self) -> None:
        rows = parse_check_update(fixture("dnf4", "check-update.txt"))

        assert "grub2-tools" not in [row[0] for row in rows]

    def test_the_dnf5_table_is_read_too(self) -> None:
        rows = parse_check_update(fixture("dnf5", "check-upgrade.txt"))

        assert [row[0] for row in rows] == [
            "bash",
            "kernel",
            "kernel-core",
            "openssl-libs",
            "tzdata",
        ]
        assert rows[3] == ("openssl-libs", "x86_64", "1:3.2.4-2.fc42", "updates")

    def test_advisories_give_each_package_its_identifier_and_severity(self) -> None:
        found = parse_advisories(fixture("dnf4", "updateinfo-security.txt"))

        assert found["kernel"] == ("RHSA-2025:1234", "Important")
        assert found["openssl-libs"] == ("RHSA-2025:2222", "Moderate")

    def test_security_is_membership_of_the_security_listing(self, runner: FakeRunner, host) -> None:
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)
        runner.script(
            ["dnf", "-q", "-y", "check-update"],
            stdout=fixture("dnf4", "check-update.txt"),
            exit_code=100,
        )
        runner.script(
            ["dnf", "-q", "-y", "check-update", "--security"],
            stdout=fixture("dnf4", "check-update-security.txt"),
            exit_code=100,
        )
        runner.script(
            ["dnf", "-q", "updateinfo"], stdout=fixture("dnf4", "updateinfo-security.txt")
        )

        pending = backend.list_updates()

        by_name = {p.name: p for p in pending.packages}
        assert by_name["openssl-libs"].security is True
        assert by_name["openssl-libs"].advisory == "RHSA-2025:2222"
        assert by_name["bash"].security is False
        assert by_name["kernel-core"].kernel is True
        assert by_name["kernel"].kernel is True
        assert by_name["kernel-modules"].kernel is False
        assert pending.pending == 7
        assert pending.security == 4

    def test_no_updates_is_exit_zero_and_an_empty_list(self, runner: FakeRunner, host) -> None:
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)
        runner.script(["dnf", "-q", "-y", "check-update"], stdout="", exit_code=0)

        pending = backend.list_updates()

        assert pending.pending == 0
        assert not runner.ran("dnf", "-q", "updateinfo")

    def test_exit_one_is_a_failure_and_carries_dnfs_words(self, runner: FakeRunner, host) -> None:
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)
        runner.script(
            ["dnf", "-q", "-y", "check-update"],
            stderr="This system is not registered with an entitlement server.",
            exit_code=1,
        )

        with pytest.raises(ServerError) as raised:
            backend.list_updates()

        assert "not registered" in (raised.value.output or "")

    def test_dnf_is_asked_in_english(self, runner: FakeRunner, host) -> None:
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)
        runner.script(["dnf", "-q", "-y", "check-update"], stdout="", exit_code=0)

        backend.list_updates()

        assert runner.envs[0]["LC_ALL"] == "C"  # type: ignore[index]


class TestDnfApplying:
    def test_security_only_is_dnfs_own_switch(self, runner: FakeRunner, host) -> None:
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)

        assert backend.upgrade_argv(UpdateScope.SECURITY, [], full=False) == [
            "dnf",
            "-y",
            "upgrade",
            "--refresh",
            "--security",
        ]
        assert backend.upgrade_argv(UpdateScope.ALL, [], full=False) == [
            "dnf",
            "-y",
            "upgrade",
            "--refresh",
        ]

    def test_dnf_never_offers_a_removal_list(self, runner: FakeRunner, host) -> None:
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)

        assert backend.removals(UpdateScope.ALL, full=True) == []

    def test_only_the_downloaded_packages_are_cleaned(self, runner: FakeRunner, host) -> None:
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)

        assert backend.clean_cache_argv() == ["dnf", "clean", "packages"]


class TestDnfReboot:
    def _backend(self, runner: FakeRunner, host) -> DnfBackend:
        return DnfBackend(platform_for("dnf"), runner=runner, host=host)

    def test_exit_one_means_a_reboot_is_due_and_the_bullets_are_the_packages(
        self, runner: FakeRunner, host
    ) -> None:
        runner.script(
            ["dnf", "needs-restarting", "-r"],
            stdout=fixture("dnf4", "needs-restarting-r-needed.txt"),
            exit_code=1,
        )
        runner.script(
            ["dnf", "needs-restarting", "-s"], stdout=fixture("dnf4", "needs-restarting-s.txt")
        )

        probe = self._backend(runner, host).restart_probe()

        assert probe.reboot.required is True
        assert probe.reboot.packages == ("kernel", "glibc", "systemd")
        assert probe.services == ("crond.service", "nginx.service", "sshd.service")

    def test_exit_zero_means_none_is_due(self, runner: FakeRunner, host) -> None:
        runner.script(
            ["dnf", "needs-restarting", "-r"],
            stdout=fixture("dnf4", "needs-restarting-r-clean.txt"),
            exit_code=0,
        )

        assert self._backend(runner, host).restart_probe().reboot.required is False

    def test_a_missing_plugin_is_not_read_as_a_reboot_needed(
        self, runner: FakeRunner, host, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # dnf4 without the plugin also exits 1: the message is what tells them apart.
        runner.script(
            ["dnf", "needs-restarting", "-r"],
            stdout=fixture("dnf4", "no-such-command.txt"),
            exit_code=1,
        )
        runner.script(
            ["rpm", "-q", "kernel-core"],
            stdout="5.14.0-427.13.1.el9_4.x86_64\n5.14.0-503.11.1.el9_5.x86_64\n",
        )
        monkeypatch.setattr(
            "noust.managers.server.pkg.dnf.running_kernel", lambda: "5.14.0-503.11.1.el9_5.x86_64"
        )

        probe = self._backend(runner, host).restart_probe()

        assert probe.available is False
        assert probe.reboot.required is False


class TestDnfAutomatic:
    def test_the_state_is_read_from_the_timer_and_the_config(
        self, runner: FakeRunner, host
    ) -> None:
        host.dnf_automatic_conf.write_text(
            "[commands]\nupgrade_type = security\napply_updates = yes\nreboot = never\n"
        )
        runner.script(["rpm", "-q", "dnf-automatic"], stdout="dnf-automatic-4.14.0-17.el9.noarch")
        runner.script(
            ["systemctl", "is-enabled", "dnf-automatic-install.timer"], stdout="enabled\n"
        )
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)

        status = backend.auto_status()

        assert status.mechanism == "dnf-automatic"
        assert status.enabled is True
        assert status.security_only is True
        assert status.reboots is False

    def test_enabling_sets_security_in_one_line_and_enables_the_install_timer(
        self, runner: FakeRunner, host, fs: RecordingFileSystem
    ) -> None:
        host.dnf_automatic_conf.write_text(
            "# Noust must keep this comment\n[commands]\nupgrade_type = default\n"
            "apply_updates = no\n[emitters]\nemit_via = stdio\n"
        )
        runner.script(["rpm", "-q", "dnf-automatic"], stdout="dnf-automatic-4.14.0")
        runner.script(
            ["systemctl", "is-enabled", "dnf-automatic-install.timer"], stdout="enabled\n"
        )
        backend = DnfBackend(platform_for("dnf"), runner=runner, fs=fs, host=host)

        # The state read afterwards is the file this call wrote.
        backend.set_auto(True, True, lambda line: None)

        text = host.dnf_automatic_conf.read_text()
        assert "upgrade_type = security" in text
        assert "apply_updates = yes" in text
        assert "# Noust must keep this comment" in text
        assert "emit_via = stdio" in text
        assert ("systemctl", "enable", "--now", "dnf-automatic-install.timer") in runner.calls
        assert (host.dnf_automatic_conf.with_name("automatic.conf.noust-bak")).exists()

    def test_disabling_stops_only_the_timers_that_are_enabled(
        self, runner: FakeRunner, host
    ) -> None:
        runner.script(["rpm", "-q", "dnf-automatic"], stdout="dnf-automatic-4.14.0")
        answers = {"dnf-automatic-install.timer": "enabled\n"}

        original = runner.run

        def run(argv, **kwargs):
            if argv[:2] == ["systemctl", "is-enabled"]:
                runner.script(
                    argv,
                    stdout=answers.get(argv[2], "disabled\n"),
                    exit_code=0 if argv[2] in answers else 1,
                )
            return original(argv, **kwargs)

        runner.run = run  # type: ignore[method-assign]
        backend = DnfBackend(platform_for("dnf"), runner=runner, host=host)
        # After the disable the install timer reads as disabled.
        original_disable = runner.run

        def run2(argv, **kwargs):
            if argv[:2] == ["systemctl", "disable"]:
                answers.clear()
            return original_disable(argv, **kwargs)

        runner.run = run2  # type: ignore[method-assign]

        backend.set_auto(False, True, lambda line: None)

        assert ("systemctl", "disable", "--now", "dnf-automatic-install.timer") in runner.calls

    def test_ini_edit_replaces_in_place_and_adds_a_missing_key_or_section(self) -> None:
        text = "[commands]\n# comment\nupgrade_type = default\n"

        assert set_ini_value(text, "commands", "upgrade_type", "security") == (
            "[commands]\n# comment\nupgrade_type = security\n"
        )
        assert "apply_updates = yes" in set_ini_value(text, "commands", "apply_updates", "yes")
        assert set_ini_value("", "commands", "upgrade_type", "security") == (
            "[commands]\nupgrade_type = security\n"
        )
        assert "[emitters]\nemit_via = stdio" in set_ini_value(
            text, "emitters", "emit_via", "stdio"
        )


class TestZypperListing:
    def test_a_security_patch_is_security_and_a_package_is_not(self) -> None:
        packages = parse_updates_xml(fixture("zypper", "list-updates-package.xml"))
        patches = parse_updates_xml(fixture("zypper", "list-updates-patch.xml"))

        assert [p.name for p in packages] == ["openssl-3", "kernel-default", "bash"]
        assert packages[0].installed == "3.1.4-150600.5.18.1"
        assert all(not p.security for p in packages)
        assert [p.security for p in patches] == [True, True, False]
        assert patches[0].kind == "patch"
        assert patches[0].severity == "important"

    def test_leap_asks_for_packages_and_for_patches_and_counts_packages_once(
        self, runner: FakeRunner, host
    ) -> None:
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)
        runner.script(
            [
                "zypper",
                "--non-interactive",
                "--xmlout",
                "--no-refresh",
                "list-updates",
                "-t",
                "package",
            ],
            stdout=fixture("zypper", "list-updates-package.xml"),
        )
        runner.script(
            [
                "zypper",
                "--non-interactive",
                "--xmlout",
                "--no-refresh",
                "list-updates",
                "-t",
                "patch",
            ],
            stdout=fixture("zypper", "list-updates-patch.xml"),
        )

        pending = backend.list_updates()

        assert pending.pending == 3
        assert pending.security == 2
        assert pending.security_scope is True
        assert runner.envs[0]["ZYPP_LOCK_TIMEOUT"] == "120"  # type: ignore[index]

    def test_tumbleweed_asks_for_packages_only_and_has_no_security_subset(
        self, runner: FakeRunner, host
    ) -> None:
        backend = ZypperBackend(platform_for("zypper", rolling=True), runner=runner, host=host)
        runner.script(
            [
                "zypper",
                "--non-interactive",
                "--xmlout",
                "--no-refresh",
                "list-updates",
                "-t",
                "package",
            ],
            stdout=fixture("zypper", "list-updates-package-tumbleweed.xml"),
        )

        pending = backend.list_updates()

        assert pending.security_scope is False
        assert len(runner.calls) == 1
        assert any("rolling" in note for note in pending.notes)

    def test_a_document_type_declaration_is_refused_before_parsing(self) -> None:
        bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><stream>&a;</stream>'

        with pytest.raises(ServerError, match="document type"):
            parse_updates_xml(bomb)

    def test_output_that_is_not_xml_is_an_error_with_the_output(self) -> None:
        with pytest.raises(ServerError) as raised:
            parse_updates_xml("Root privileges are required")

        assert "Root privileges" in (raised.value.output or "")


class TestZypperApplying:
    def test_security_on_leap_is_patch_by_category(self, runner: FakeRunner, host) -> None:
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)

        assert backend.upgrade_argv(UpdateScope.SECURITY, [], full=False) == [
            "zypper",
            "--non-interactive",
            "patch",
            "--category",
            "security",
        ]
        assert backend.upgrade_argv(UpdateScope.ALL, [], full=False) == [
            "zypper",
            "--non-interactive",
            "update",
        ]

    def test_tumbleweed_updates_with_dup_and_refuses_security(
        self, runner: FakeRunner, host
    ) -> None:
        backend = ZypperBackend(platform_for("zypper", rolling=True), runner=runner, host=host)

        assert backend.upgrade_argv(UpdateScope.ALL, [], full=False) == [
            "zypper",
            "--non-interactive",
            "dup",
        ]
        with pytest.raises(UnsupportedHostError, match="no security-only"):
            backend.upgrade_argv(UpdateScope.SECURITY, [], full=False)

    def test_a_distribution_upgrade_is_never_offered_on_leap(
        self, runner: FakeRunner, host
    ) -> None:
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)

        with pytest.raises(UnsupportedHostError, match="not offered"):
            backend.upgrade_argv(UpdateScope.ALL, [], full=True)

    def test_dup_removals_come_from_a_dry_run(self, runner: FakeRunner, host) -> None:
        backend = ZypperBackend(platform_for("zypper", rolling=True), runner=runner, host=host)
        runner.script(
            ["zypper", "--non-interactive", "dist-upgrade", "--dry-run"],
            stdout=fixture("zypper", "dup-dry-run.txt"),
        )

        assert backend.removals(UpdateScope.ALL, full=True) == ["libfoo1", "python311-oldthing"]
        assert parse_removed("nothing here") == []

    def test_leap_never_simulates_a_removal(self, runner: FakeRunner, host) -> None:
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)

        assert backend.removals(UpdateScope.ALL, full=False) == []
        assert runner.calls == []


class TestZypperReboot:
    def test_exit_102_is_a_reboot_and_ps_lists_the_services(self, runner: FakeRunner, host) -> None:
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)
        runner.script(["zypper", "needs-rebooting"], stdout="Reboot is suggested.", exit_code=102)
        runner.script(["zypper", "ps", "-sss"], stdout=fixture("zypper", "ps-sss.txt"))

        probe = backend.restart_probe()

        assert probe.reboot.required is True
        assert probe.services == ("cron.service", "nginx.service", "sshd.service")

    def test_the_flag_file_is_enough_when_zypper_cannot_say(self, runner: FakeRunner, host) -> None:
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)
        (host.root / "run").mkdir(exist_ok=True)
        host.zypper_reboot_needed.write_text("")
        runner.script(["zypper", "needs-rebooting"], exit_code=5)

        probe = backend.restart_probe()

        assert probe.reboot.required is True
        assert probe.reboot.since is not None

    def test_nothing_is_due_on_a_clean_machine(self, runner: FakeRunner, host) -> None:
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)

        assert backend.restart_probe().reboot.required is False


class TestZypperAutomatic:
    def test_os_update_is_read_from_its_config_and_timer(self, runner: FakeRunner, host) -> None:
        host.os_update_conf.parent.mkdir(parents=True, exist_ok=True)
        host.os_update_conf.write_text(fixture("zypper", "os-update.conf"))
        runner.script(["systemctl", "is-enabled", "os-update.timer"], stdout="enabled\n")
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)

        status = backend.auto_status()

        assert status.mechanism == "os-update"
        assert status.enabled is True
        assert status.security_only is True
        assert status.reboots is False

    def test_a_transactional_system_reports_and_does_not_change(
        self, runner: FakeRunner, host
    ) -> None:
        backend = backend_for(platform_for("zypper", transactional=True), runner=runner, host=host)

        status = backend.auto_status()

        assert status.supported is False
        assert "transactional" in status.detail
        with pytest.raises(UnsupportedHostError):
            backend.set_auto(True, True, lambda line: None)

    def test_a_leap_without_os_update_has_no_mechanism_to_switch(
        self, runner: FakeRunner, host
    ) -> None:
        runner.only_knows("zypper")
        backend = ZypperBackend(platform_for("zypper"), runner=runner, host=host)

        status = backend.auto_status()

        assert status.supported is False
        assert "os-update" in status.detail

    def test_enabling_writes_the_two_variables_and_enables_the_timer(
        self, runner: FakeRunner, host, fs: RecordingFileSystem
    ) -> None:
        host.os_update_conf.parent.mkdir(parents=True, exist_ok=True)
        host.os_update_conf.write_text("# mine\nUPDATE_CMD=up\n")
        runner.script(["systemctl", "is-enabled", "os-update.timer"], stdout="enabled\n")
        backend = ZypperBackend(platform_for("zypper"), runner=runner, fs=fs, host=host)

        backend.set_auto(True, True, lambda line: None)

        text = host.os_update_conf.read_text()
        assert "UPDATE_CMD=security" in text
        assert "REBOOT_CMD=none" in text
        assert "# mine" in text
        assert ("systemctl", "enable", "--now", "os-update.timer") in runner.calls

    def test_shell_variables_are_read_and_set(self) -> None:
        assert read_shell_var("# UPDATE_CMD=old\nUPDATE_CMD='dup'\n", "UPDATE_CMD") == "dup"
        assert set_shell_var("A=1\n", "B", "2") == "A=1\nB=2\n"
        assert set_shell_var("A=1\nB=0\n", "B", "2") == "A=1\nB=2\n"


class TestBackendChoice:
    @pytest.mark.parametrize(
        ("family", "expected"),
        [("apt", "apt"), ("dnf", "dnf"), ("zypper", "zypper"), ("none", "none")],
    )
    def test_each_family_gets_its_backend(self, family: str, expected: str) -> None:
        assert backend_for(platform_for(family)).name == expected

    def test_a_transactional_system_gets_the_backend_that_only_reports(self) -> None:
        assert backend_for(platform_for("dnf", transactional=True)).name == "none"

    def test_the_unsupported_backend_says_why_on_every_action(self) -> None:
        backend = backend_for(platform_for("none"))

        with pytest.raises(UnsupportedHostError, match="cannot be managed"):
            backend.refresh_argv()
        assert backend.list_updates().notes
