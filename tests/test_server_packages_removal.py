# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Removing packages through the package manager backend, and asking what a removal would take (4.2).

Removal was spelled by hand in the database engines' code and nowhere else. It is one method of the
backend now, like installing is, with its simulation declared as a read-only probe so a ``--dry-run``
can say what would go. A removal is never a purge: the configuration of what Noust removes stays,
because the operator may install it again, and deleting an engine's data is its own explicit action.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.core.fs import RecordingFileSystem
from noust.core.runner import DryRunRunner, FakeRunner
from noust.managers.server.errors import ServerError, UnsupportedHostError
from noust.managers.server.pkg import backend_for
from noust.managers.server.pkg.base import PackageBackend
from noust.managers.server.pkg.dnf import parse_removal_transaction
from noust.managers.server.probes import is_declared_probe
from tests.support.server_support import fixture, make_host, platform_for

FAMILIES = ["apt", "dnf", "zypper"]


@pytest.fixture
def host(tmp_path: Path):
    return make_host(tmp_path)


def backend_of(family: str, runner: FakeRunner, host) -> PackageBackend:
    return backend_for(platform_for(family), runner=runner, fs=RecordingFileSystem(), host=host)


class TestTheArgv:
    def test_apt_removes_and_asks_nothing(self, runner: FakeRunner, host) -> None:
        argv = backend_of("apt", runner, host).remove_argv(["nginx", "libnginx-mod-stream"])

        assert argv == ["apt-get", "remove", "-y", "nginx", "libnginx-mod-stream"]

    def test_dnf_removes_and_asks_nothing(self, runner: FakeRunner, host) -> None:
        argv = backend_of("dnf", runner, host).remove_argv(["nginx"])

        assert argv == ["dnf", "-y", "remove", "nginx"]

    def test_zypper_removes_and_asks_nothing(self, runner: FakeRunner, host) -> None:
        argv = backend_of("zypper", runner, host).remove_argv(["nginx"])

        assert argv == ["zypper", "--non-interactive", "remove", "nginx"]

    @pytest.mark.parametrize("family", FAMILIES)
    def test_a_removal_is_never_a_purge_and_never_takes_what_nobody_named(
        self, family: str, runner: FakeRunner, host
    ) -> None:
        backend = backend_of(family, runner, host)

        for argv in (backend.remove_argv(["x", "y"]), backend.simulate_remove_argv(["x", "y"])):
            words = " ".join(argv).lower()
            assert "purge" not in words
            assert "autoremove" not in words
            assert "clean-deps" not in words and "--clean-deps" not in words
            assert "--allowerasing" not in words

    @pytest.mark.parametrize("family", FAMILIES)
    @pytest.mark.parametrize(
        "name", ["", "-y", "--purge", "a b", "a;b", "$(id)", "../x", "x=1", "x*", "a\nb"]
    )
    def test_a_name_that_could_be_an_option_or_a_pattern_is_refused(
        self, family: str, name: str, runner: FakeRunner, host
    ) -> None:
        backend = backend_of(family, runner, host)

        with pytest.raises(ValidationError, match="is not a package name"):
            backend.remove_argv([name])
        with pytest.raises(ValidationError):
            backend.simulate_remove_argv([name])

    @pytest.mark.parametrize("family", FAMILIES)
    def test_removing_nothing_is_not_a_command(self, family: str, runner: FakeRunner, host) -> None:
        with pytest.raises(ValidationError, match="No package was named"):
            backend_of(family, runner, host).remove_argv([])

    def test_a_host_whose_packages_noust_does_not_manage_refuses(self, runner: FakeRunner) -> None:
        backend = backend_for(platform_for("none"), runner=runner)

        with pytest.raises(UnsupportedHostError):
            backend.remove_argv(["nginx"])
        with pytest.raises(UnsupportedHostError):
            backend.simulate_remove_argv(["nginx"])


class TestTheSimulationIsAProbe:
    @pytest.mark.parametrize("family", FAMILIES)
    def test_what_would_go_is_declared_read_only(
        self, family: str, runner: FakeRunner, host
    ) -> None:
        argv = backend_of(family, runner, host).simulate_remove_argv(["nginx", "certbot"])

        assert is_declared_probe(argv)

    @pytest.mark.parametrize("family", FAMILIES)
    def test_the_removal_itself_is_not(self, family: str, runner: FakeRunner, host) -> None:
        argv = backend_of(family, runner, host).remove_argv(["nginx"])

        assert not is_declared_probe(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            ["apt-get", "-s", "remove", "nginx"],  # no lock-free option: not the declared shape
            ["apt-get", "remove", "-y", "nginx"],
            ["apt-get", "purge", "-y", "nginx"],
            ["dnf", "remove", "nginx"],
            ["dnf", "-y", "remove", "nginx"],
            ["zypper", "--non-interactive", "remove", "nginx"],
            ["zypper", "remove", "--dry-run", "nginx"],
        ],
    )
    def test_nothing_that_removes_is_declared(self, argv: list[str]) -> None:
        assert not is_declared_probe(argv)

    def test_a_rehearsal_asks_the_package_manager_and_removes_nothing(
        self, runner: FakeRunner, host
    ) -> None:
        runner.script(["apt-get", "-s"], stdout=fixture("apt", "remove-simulation.txt"))
        rehearsal = DryRunRunner(runner)
        backend = backend_for(platform_for("apt"), runner=rehearsal, host=host)

        names = backend.simulate_remove(["nginx"])
        backend.remove(["nginx"], timeout=60)

        assert names[0] == "libnginx-mod-http-geoip2"
        assert [call[:3] for call in runner.calls] == [("apt-get", "-s", "-o")]
        assert rehearsal.skipped == [("apt-get", "remove", "-y", "nginx")]


class TestWhatWouldGo:
    def test_apt_names_the_packages_on_its_remv_lines(self, runner: FakeRunner, host) -> None:
        runner.script(["apt-get", "-s"], stdout=fixture("apt", "remove-simulation.txt"))

        names = backend_of("apt", runner, host).simulate_remove(["nginx"])

        assert names == [
            "libnginx-mod-http-geoip2",
            "libnginx-mod-stream",
            "nginx",
            "nginx-common",
        ]
        assert runner.calls[0] == (
            "apt-get",
            "-s",
            "-o",
            "Debug::NoLocking=1",
            "remove",
            "nginx",
        )

    def test_dnf_names_what_it_would_remove_in_every_section_and_joins_a_wrapped_row(
        self, runner: FakeRunner, host
    ) -> None:
        # `--assumeno` aborts the transaction on purpose: exit code 1 is the answer, not a failure.
        runner.script(
            ["dnf", "--assumeno"], stdout=fixture("dnf4", "remove-assumeno.txt"), exit_code=1
        )

        names = backend_of("dnf", runner, host).simulate_remove(["nginx"])

        assert names == [
            "nginx",
            "nginx-mod-stream",
            "nginx-mod-http-image-filter-with-a-very-long-name",
            "nginx-filesystem",
        ]
        assert runner.calls[0] == ("dnf", "--assumeno", "remove", "nginx")

    def test_dnf5_prints_its_own_table_and_reads_the_same(self) -> None:
        names = parse_removal_transaction(fixture("dnf5", "remove-assumeno.txt"))

        assert names == ["nginx", "nginx-mod-stream", "nginx-filesystem"]

    def test_dnf_with_nothing_installed_to_remove_says_nothing_would_go(
        self, runner: FakeRunner, host
    ) -> None:
        runner.script(
            ["dnf", "--assumeno"],
            stderr="No match for argument: nginx\nError: No packages marked for removal.",
            exit_code=1,
        )

        assert backend_of("dnf", runner, host).simulate_remove(["nginx"]) == []

    def test_dnf_that_failed_for_another_reason_carries_its_words(
        self, runner: FakeRunner, host
    ) -> None:
        runner.script(
            ["dnf", "--assumeno"], stderr="Error: Failed to download metadata", exit_code=1
        )

        with pytest.raises(ServerError) as raised:
            backend_of("dnf", runner, host).simulate_remove(["nginx"])

        assert "Failed to download metadata" in (raised.value.output or "")

    def test_zypper_reads_the_block_of_what_is_going_to_be_removed(
        self, runner: FakeRunner, host
    ) -> None:
        runner.script(["zypper"], stdout=fixture("zypper", "remove-dry-run.txt"))

        names = backend_of("zypper", runner, host).simulate_remove(["nginx"])

        assert names == ["nginx", "nginx-module-stream", "python3-certbot-nginx"]
        assert runner.calls[0] == (
            "zypper",
            "--non-interactive",
            "remove",
            "--dry-run",
            "nginx",
        )

    def test_zypper_that_does_not_find_the_package_says_nothing_would_go(
        self, runner: FakeRunner, host
    ) -> None:
        runner.script(["zypper"], stdout="package 'nginx' not found.", exit_code=104)

        assert backend_of("zypper", runner, host).simulate_remove(["nginx"]) == []

    def test_apt_that_failed_carries_its_words(self, runner: FakeRunner, host) -> None:
        runner.script(
            ["apt-get", "-s"], stderr="E: Could not get lock /var/lib/dpkg/lock", exit_code=100
        )

        with pytest.raises(ServerError) as raised:
            backend_of("apt", runner, host).simulate_remove(["nginx"])

        assert "Could not get lock" in (raised.value.output or "")


class TestRemoving:
    @pytest.mark.parametrize("family", FAMILIES)
    def test_it_runs_the_argv_in_the_package_managers_environment(
        self, family: str, runner: FakeRunner, host
    ) -> None:
        backend = backend_of(family, runner, host)

        result = backend.remove(["nginx"], timeout=90)

        assert result.success
        assert runner.calls[0] == tuple(backend.remove_argv(["nginx"]))
        env = runner.envs[0]
        assert env is not None and env["LC_ALL"] == "C"

    def test_a_failure_is_returned_for_the_caller_to_read(self, runner: FakeRunner, host) -> None:
        runner.script(
            ["apt-get", "remove"], stderr="E: Sub-process /usr/bin/dpkg returned", exit_code=100
        )

        result = backend_of("apt", runner, host).remove(["nginx"], timeout=90)

        assert not result.success
        assert "dpkg returned" in result.stderr


class TestWhichVersionIsInstalled:
    def test_apt_reads_the_version_beside_each_installed_package(
        self, runner: FakeRunner, host
    ) -> None:
        backend = backend_of("apt", runner, host)
        runner.script(
            ["dpkg-query"],
            stdout="nginx ii  1.24.0-2ubuntu7.1\ncertbot rc  2.9.0-1\nfail2ban un  \n",
        )

        found = backend.installed_versions(["nginx", "certbot", "fail2ban", "git"])

        assert found == {"nginx": "1.24.0-2ubuntu7.1"}
        assert runner.calls[0][:2] == ("dpkg-query", "-W")

    def test_rpm_reads_name_and_version_release(self, runner: FakeRunner, host) -> None:
        backend = backend_of("dnf", runner, host)
        runner.script(
            ["rpm", "-q"],
            stdout="nginx 1.20.1-14.el9_2.1\npackage git is not installed\n",
            exit_code=1,
        )

        found = backend.installed_versions(["nginx", "git"])

        assert found == {"nginx": "1.20.1-14.el9_2.1"}

    def test_both_are_declared_probes(self, runner: FakeRunner, host) -> None:
        for family in ("apt", "dnf", "zypper"):
            argv = backend_of(family, runner, host).versions_argv(["nginx"])

            assert is_declared_probe(argv), argv
