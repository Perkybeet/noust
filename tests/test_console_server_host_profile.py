# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The machine the console server models: a VPS, or the container the Noust central runs in on a NAS.

The console is read and tested on a sandbox whose machine is an Ubuntu VPS with everything on it, so
the faults that only a container has (no systemd to read the clock from, no journal to search, no
sshd to ask, nobody to be root) never showed: the owner's hub is that container. ``--host-profile
container`` models it as the image ships it (``packaging/container/Dockerfile``: Python, tini, ssh,
openssl and git, run as an account that is not root): the programs it lacks answer 127 as the real
runner reports a missing binary, the host tree has no systemd, and Noust is not root. What is held
here is that, and that what the session says of such a machine is read from the model by the code
that reads a real one (``noust.central.machine``), never set by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.central import features, machine
from noust.central.machine import read_machine_facts
from noust.managers.server.host import HostPaths
from scripts.console_server import host_profile
from scripts.console_server.arguments import parse_args
from scripts.console_server.runner import make_runner
from scripts.console_server.server_host import ServerHost


def modelled_runner(tmp_path: Path, profile: host_profile.HostProfile):
    return make_runner({}, {}, {}, [], tmp_path / "units", programs=profile.programs)


def modelled_host(tmp_path: Path, profile: host_profile.HostProfile) -> ServerHost:
    server = ServerHost(tmp_path / "host", "nas", {}, profile=profile)
    server.build()
    return server


@pytest.fixture(autouse=True)
def root_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A profile says whether Noust is root by replacing one function; put it back."""
    monkeypatch.setattr(machine, "is_root", machine.is_root)


class TestTheProfiles:
    def test_a_vps_is_the_default(self) -> None:
        assert parse_args([]).host_profile == "server"
        assert host_profile.PROFILES["server"].programs is None

    def test_the_container_is_asked_for_by_name(self) -> None:
        args = parse_args(["--central-role", "hub", "--host-profile", "container"])

        assert args.host_profile == "container"
        assert host_profile.PROFILES[args.host_profile] is host_profile.CONTAINER

    def test_the_container_has_what_the_image_ships_and_nothing_more(self) -> None:
        programs = set(host_profile.CONTAINER.programs or ())

        assert {"git", "openssl", "ssh", "ssh-keygen", "tini", "python3"} <= programs
        for absent in (
            "systemctl",
            "systemd-detect-virt",
            "journalctl",
            "nginx",
            "apache2",
            "certbot",
            "docker",
            "sshd",
            "ufw",
            "fail2ban-client",
            "timedatectl",
            "ss",
        ):
            assert absent not in programs, absent

    def test_a_container_is_not_root(self) -> None:
        assert host_profile.CONTAINER.root is False
        assert host_profile.SERVER.root is True


class TestAProgramTheContainerLacks:
    def test_answers_127_as_the_real_runner_reports_a_missing_binary(self, tmp_path: Path) -> None:
        runner = modelled_runner(tmp_path, host_profile.CONTAINER)

        result = runner.run(["journalctl", "--no-pager"], timeout=5)

        assert result.exit_code == 127
        assert "Command not found: journalctl" in result.stderr
        assert runner.exists("journalctl") is False
        assert runner.exists("nginx") is False
        assert runner.exists("git") is True

    def test_a_program_it_has_is_answered_by_the_model_as_ever(self, tmp_path: Path) -> None:
        runner = modelled_runner(tmp_path, host_profile.CONTAINER)

        result = runner.run(["git", "--version"], timeout=5)

        assert result.exit_code == 0

    def test_the_vps_keeps_every_program_it_had(self, tmp_path: Path) -> None:
        runner = modelled_runner(tmp_path, host_profile.SERVER)

        assert runner.exists("nginx") and runner.exists("journalctl")
        assert runner.run(["systemctl", "--version"], timeout=5).exit_code != 127


class TestTheHostTree:
    def test_a_vps_was_booted_with_systemd_and_keeps_a_journal(self, tmp_path: Path) -> None:
        server = modelled_host(tmp_path, host_profile.SERVER)

        assert (server.root / "run/systemd/system").is_dir()
        assert (server.root / "var/log/journal").is_dir()

    def test_a_container_was_not(self, tmp_path: Path) -> None:
        server = modelled_host(tmp_path, host_profile.CONTAINER)

        assert not (server.root / "run/systemd").exists()
        assert not (server.root / "var/log/journal").exists()
        # What the Server area reads of any machine is still there.
        assert (server.root / "etc/os-release").is_file()


class TestWhatTheSessionSaysOfEachMachine:
    """Read by the code that reads a real machine, from the model."""

    def facts(self, tmp_path: Path, profile: host_profile.HostProfile) -> machine.MachineFacts:
        runner = modelled_runner(tmp_path, profile)
        server = modelled_host(tmp_path, profile)
        host_profile.use(profile)
        machine.reset_machine_facts()
        # Imported by name: the suite replaces the module's own, so no test asks the machine it runs on.
        return read_machine_facts(runner, host=HostPaths(root=server.root))

    def test_the_container_of_a_nas_has_none_of_it(self, tmp_path: Path) -> None:
        facts = self.facts(tmp_path, host_profile.CONTAINER)

        assert (facts.systemd, facts.web_server, facts.docker, facts.root, facts.journal) == (
            False,
            False,
            False,
            False,
            False,
        )
        assert facts.docker_problem is not None
        assert facts.docker_problem.code == "docker.engine.missing"

    def test_the_vps_has_all_of_it_but_docker_which_the_sandbox_leaves_to_install(
        self, tmp_path: Path
    ) -> None:
        facts = self.facts(tmp_path, host_profile.SERVER)

        assert (facts.systemd, facts.web_server, facts.root, facts.journal) == (
            True,
            True,
            True,
            True,
        )
        assert facts.docker is False

    def test_so_a_hub_in_the_container_offers_the_owner_none_of_what_failed_for_him(
        self, tmp_path: Path
    ) -> None:
        facts = self.facts(tmp_path, host_profile.CONTAINER)

        published = features.published(role="hub", facts=facts)

        for key in ("system_clock", "system_logs", "root_shell", "server_security", "power"):
            assert published[key]["available"] is False, key
        assert published["system_logs"]["reason"]["code"] == "features.needs_journal"


class TestWhatTheModelAnswersAsEachMachine:
    """
    The pages of the machine (processes, disks, updates) are read from the machine Noust runs on, and
    in the central's container that is the container: not the VPS's nginx, postgres and sshd, not a
    kernel waiting for a reboot, not eight packages to update (item 12).
    """

    def test_the_vps_runs_what_a_server_runs_and_the_container_runs_its_own_few(self) -> None:
        from scripts.console_server.host_reads import modelled_processes

        vps = {process.name for process in modelled_processes({}, host_profile.SERVER)}
        container = modelled_processes({}, host_profile.CONTAINER)

        assert {"systemd", "nginx", "postgres", "sshd"} <= vps
        names = {process.name for process in container}
        assert names == {"tini", "python3", "ssh"}
        assert [process.pid for process in container][:1] == [1]
        # The account that is not root runs what the image runs, and nothing of the machine's.
        assert {process.user for process in container} == {"noust"}
        assert all(process.unit is None for process in container)

    def test_the_applications_of_a_container_host_are_in_docker_and_not_among_its_processes(
        self,
    ) -> None:
        from scripts.console_server.host_reads import modelled_processes
        from scripts.console_server.machine import Unit

        units = {"shop-example-com": Unit(active="active", pid=2101)}

        names = {process.name for process in modelled_processes(units, host_profile.CONTAINER)}

        assert "node" not in names

    def test_the_container_has_no_kernel_waiting_for_a_reboot(self, tmp_path: Path) -> None:
        vps = modelled_host(tmp_path / "vps", host_profile.SERVER)
        container = modelled_host(tmp_path / "container", host_profile.CONTAINER)

        assert (vps.root / "var/run/reboot-required").is_file()
        assert not (container.root / "var/run/reboot-required").exists()
        assert not (container.root / "var/run/reboot-required.pkgs").exists()

    def test_its_package_manager_finds_nothing_to_update_in_the_image(self, tmp_path: Path) -> None:
        vps = modelled_host(tmp_path / "vps", host_profile.SERVER)
        container = modelled_host(tmp_path / "container", host_profile.CONTAINER)

        asked = ("apt-get", "-s", "upgrade")
        assert "8 upgraded" in vps.answer(asked).stdout
        nothing = container.answer(asked).stdout
        assert "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded." in nothing
        assert "Inst " not in nothing

    def test_its_disk_is_the_images_and_the_volume_it_was_given(self) -> None:
        container = host_profile.CONTAINER.disks

        assert container is not None
        assert [row[0] for row in container] == ["/", "/data"]
        assert host_profile.SERVER.disks is None

    def test_what_the_session_says_of_it_is_read_from_this_and_offers_none_of_it(
        self, tmp_path: Path
    ) -> None:
        facts = TestWhatTheSessionSaysOfEachMachine().facts(tmp_path, host_profile.CONTAINER)

        published = features.published(role="hub", facts=facts, deploy_here="containers")

        for key in ("system_processes", "system_files", "system_updates", "zero_downtime"):
            assert published[key]["available"] is False, key
        for key in ("system_processes", "system_files", "system_updates"):
            assert published[key]["reason"]["code"] == "features.needs_host", key
        assert published["deploy_here"]["available"] is True

    def test_the_vps_offers_its_processes_its_files_and_its_updates(self, tmp_path: Path) -> None:
        facts = TestWhatTheSessionSaysOfEachMachine().facts(tmp_path, host_profile.SERVER)

        published = features.published(role="server", facts=facts)

        for key in ("system_processes", "system_files", "system_updates"):
            assert published[key]["available"] is True, key
        assert published["deploy_here"]["available"] is False
