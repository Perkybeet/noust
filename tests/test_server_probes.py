"""
Tests for the read-only probes the server managers declare.

A rehearsal (``--dry-run``) lets a command run when it only looks, and what
"only looks" means has to be exact: ``apt-get -s upgrade`` looks and ``apt-get
upgrade`` does not, and a program name cannot tell them apart. The declaration is
:data:`~noust.managers.server.probes.READ_ONLY_PROBES`; the test that matters here
runs every read path of the managers against a recording runner and fails on any
command that is not declared, so a new probe or a renamed flag cannot slip out of
the list, and on the opposite side that nothing which changes the machine is in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.managers.server.pkg import UpdateScope, backend_for
from noust.managers.server.probes import READ_ONLY_PROBES, is_declared_probe, matches
from noust.managers.server.summary import build_summary
from tests.server_support import (  # noqa: F401 - the fixture registers itself
    fixture,
    make_machine,
    no_package_manager_running,
    platform_for,
)

pytestmark = pytest.mark.usefixtures("no_package_manager_running")


class TestMatching:
    def test_literals_must_be_equal_and_the_length_must_match(self) -> None:
        assert matches(("dpkg", "--audit"), ("dpkg", "--audit"))
        assert not matches(("dpkg", "--audit", "extra"), ("dpkg", "--audit"))
        assert not matches(("dpkg",), ("dpkg", "--audit"))
        assert not matches(("dpkg", "--configure"), ("dpkg", "--audit"))

    def test_an_ellipsis_lets_anything_follow(self) -> None:
        assert matches(("journalctl", "--unit=nginx", "--lines=10"), ("journalctl", ...))
        assert matches(("journalctl",), ("journalctl", ...))
        assert not matches(("journalctl2",), ("journalctl", ...))

    def test_a_star_matches_text_in_its_position_only(self) -> None:
        probe = ("findmnt", "-no", "FSTYPE", "-T", "*")

        assert matches(("findmnt", "-no", "FSTYPE", "-T", "/"), probe)
        assert not matches(("findmnt", "-no", "FSTYPE", "-T"), probe)


class TestWhatIsNotDeclared:
    @pytest.mark.parametrize(
        "argv",
        [
            ["apt-get", "upgrade"],
            ["apt-get", "-y", "upgrade"],
            ["apt-get", "-s", "install", "openssl"],
            ["apt-get", "update"],
            ["apt-get", "clean"],
            ["dnf", "-y", "upgrade"],
            ["dnf", "clean", "packages"],
            ["zypper", "--non-interactive", "update"],
            ["zypper", "--non-interactive", "dup"],
            ["docker", "system", "prune", "-f"],
            ["docker", "system", "df"],
            ["docker", "image", "prune", "-a"],
            ["docker", "builder", "prune", "-f"],
            ["systemctl", "restart", "nginx"],
            ["systemctl", "enable", "--now", "apt-daily.timer"],
            ["timedatectl", "set-timezone", "UTC"],
            ["timedatectl", "set-ntp", "true"],
            ["hostnamectl", "set-hostname", "x"],
            ["swapon", "/swapfile"],
            ["swapoff", "/swapfile"],
            ["shutdown", "-r", "+1"],
            ["shutdown", "-c"],
            ["systemd-run", "--unit=x", "--", "true"],
            ["du", "-sx", "-B1", "--", "/"],
            ["fallocate", "-l", "1G", "/swapfile"],
            ["mkswap", "/swapfile"],
            ["sysctl", "-w", "vm.swappiness=10"],
            ["needrestart", "-r", "a"],
            ["findmnt"],
            ["iptables", "-I", "DOCKER-USER", "-p", "tcp", "--dport", "3307", "-j", "DROP"],
            ["iptables", "-F", "DOCKER-USER"],
            ["iptables", "-S"],
            ["nft", "flush", "chain", "ip", "filter", "DOCKER-USER"],
            ["ip", "route", "del", "default"],
        ],
    )
    def test_a_command_that_changes_something_is_never_a_probe(self, argv: list[str]) -> None:
        assert not is_declared_probe(argv)

    def test_a_declared_probe_that_names_an_acting_verb_is_a_simulation(self) -> None:
        acting = {"install", "remove", "upgrade", "full-upgrade", "dist-upgrade", "restart", "stop"}
        simulation = {"-s", "--dry-run"}

        for probe in READ_ONLY_PROBES:
            words = {str(part) for part in probe}
            if acting & words:
                assert simulation & words, probe


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "probes.db", fs=RecordingFileSystem())
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def undeclared(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [call for call in calls if not is_declared_probe(call)]


class TestEveryReadPathIsDeclared:
    def test_the_summary_and_everything_it_reads(self, tmp_path: Path, monkeypatch, store) -> None:
        machine = make_machine(tmp_path, monkeypatch, store)

        build_summary(machine.ctx, wait=True)

        assert machine.runner.calls
        assert undeclared(machine.runner.calls) == []

    def test_the_updates_page_and_the_plans(self, tmp_path: Path, monkeypatch, store) -> None:
        machine = make_machine(tmp_path, monkeypatch, store)
        machine.runner.script(
            ["apt-get", "-s", "-o", "Debug::NoLocking=1", "full-upgrade"],
            stdout=fixture("apt", "full-upgrade-removals.txt"),
        )
        updates = machine.ctx.updates

        updates.pending()
        updates.restart_probe()
        updates.backend.auto_status()
        updates.plan(UpdateScope.SECURITY)
        updates.plan(UpdateScope.ALL, full=True)

        assert undeclared(machine.runner.calls) == []

    def test_the_storage_page_the_scan_and_the_plans(
        self, tmp_path: Path, monkeypatch, store
    ) -> None:
        machine = make_machine(tmp_path, monkeypatch, store)
        for relative in ("/var/cache/apt/archives", "/var/crash", "/var/lib/postgresql"):
            machine.host.at(relative).mkdir(parents=True)
        machine.runner.script(["ionice"], stdout="1024\tx\n")
        machine.runner.script(
            ["docker", "system", "df"], stdout=fixture("docker", "system-df.json")
        )
        machine.runner.script(["docker", "image", "ls"], stdout=fixture("docker", "image-ls.json"))
        storage = machine.ctx.storage

        storage.usage()
        storage.docker_unused_images()
        storage.plan_cleanup("journal")
        storage.plan_cleanup("pkg-cache")
        storage.analyze()

        assert machine.runner.ran("ionice")
        assert undeclared(machine.runner.calls) == []

    def test_swap_the_clock_the_names_the_power_checks_and_the_journal(
        self, tmp_path: Path, monkeypatch, store
    ) -> None:
        machine = make_machine(tmp_path, monkeypatch, store)
        ctx = machine.ctx

        ctx.swap.status()
        ctx.clock.status()
        ctx.clock.affected_timers()
        ctx.identity.identity()
        ctx.power.status()
        ctx.power.checks()
        ctx.journal.read(unit="nginx", lines=5)
        ctx.journal.boots()

        assert undeclared(machine.runner.calls) == []

    def test_dnf_and_zypper_read_paths(self, tmp_path: Path, monkeypatch, store) -> None:
        machine = make_machine(tmp_path, monkeypatch, store)
        for family, extra in (("dnf", {}), ("zypper", {}), ("zypper", {"rolling": True})):
            machine.runner.calls.clear()
            backend = backend_for(
                platform_for(family, **extra), runner=machine.runner, host=machine.host
            )
            machine.runner.script(
                ["zypper", "--non-interactive", "--xmlout"],
                stdout=fixture("zypper", "list-updates-package.xml"),
            )
            machine.runner.script(["dnf", "-q", "-y", "check-update"], stdout="", exit_code=0)

            backend.list_updates()
            backend.restart_probe()
            backend.auto_status()
            backend.removals(UpdateScope.ALL, full=True)

            assert undeclared(machine.runner.calls) == [], family
