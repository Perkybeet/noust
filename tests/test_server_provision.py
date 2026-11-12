# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A new server gets the software the old one ran on: web server and Docker (checklist H-28, H-30).

``noust restore-server --install`` used to name what a new machine lacked and stop. What is pinned
here is what installing is allowed to be: the package manager is the one that is there (never a
guess), the command is an argv with a deadline, nothing is installed that was not asked for, a
distribution that does not carry Docker is told so rather than given a repository nobody chose, and
a failure says what the package manager said.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import DependencyError
from noust.core.package_family import PackageFamily
from noust.core.runner import FakeRunner
from noust.managers.server.host import OsRelease
from noust.managers.server_provision import ServerProvisioner

UBUNTU_2404 = OsRelease(id="ubuntu", id_like=("debian",), version_id="24.04", codename="noble")
DEBIAN_12 = OsRelease(id="debian", version_id="12", codename="bookworm")
DEBIAN_13 = OsRelease(id="debian", version_id="13", codename="trixie")
DEBIAN_SID = OsRelease(id="debian", version_id="", codename="sid")
MINT = OsRelease(id="linuxmint", id_like=("ubuntu", "debian"), version_id="22")
FEDORA_44 = OsRelease(id="fedora", version_id="44")
ROCKY_9 = OsRelease(id="rocky", id_like=("rhel", "centos", "fedora"), version_id="9.5")
OPENSUSE = OsRelease(id="opensuse-tumbleweed", id_like=("suse", "opensuse"))
UNKNOWN = OsRelease()


def provisioner(
    runner: FakeRunner, family: PackageFamily, os_release: OsRelease = UNKNOWN
) -> ServerProvisioner:
    return ServerProvisioner(runner=runner, family=family, os_release=os_release)


class TestTheWebServer:
    @pytest.mark.parametrize(
        ("family", "install", "unit"),
        [
            (PackageFamily.APT, ("apt-get", "install", "-y", "nginx"), "nginx"),
            (PackageFamily.DNF, ("dnf", "install", "-y", "nginx"), "nginx"),
            (PackageFamily.ZYPPER, ("zypper", "--non-interactive", "install", "nginx"), "nginx"),
        ],
    )
    def test_nginx_is_installed_with_the_package_manager_that_is_there(
        self, family: PackageFamily, install: tuple[str, ...], unit: str
    ) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get", "dnf", "zypper")

        step = provisioner(runner, family).ensure_webserver("nginx")

        assert step.state == "installed"
        assert install in runner.calls
        assert ("systemctl", "enable", "--now", unit) in runner.calls

    def test_apache_has_the_name_each_family_gives_it(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "dnf")

        provisioner(runner, PackageFamily.DNF).ensure_webserver("apache")

        assert ("dnf", "install", "-y", "httpd") in runner.calls
        assert ("systemctl", "enable", "--now", "httpd") in runner.calls

    def test_one_that_is_there_is_left_alone(self) -> None:
        runner = FakeRunner().only_knows("nginx", "systemctl")

        step = provisioner(runner, PackageFamily.APT).ensure_webserver("nginx")

        assert step.state == "present"
        assert not any(call[0] == "apt-get" for call in runner.calls)

    def test_the_package_lists_are_refreshed_first_on_apt(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")

        provisioner(runner, PackageFamily.APT).ensure_webserver("nginx")

        assert runner.calls.index(("apt-get", "update")) < runner.calls.index(
            ("apt-get", "install", "-y", "nginx")
        )

    def test_a_failure_carries_what_the_package_manager_said(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(
            ["apt-get", "install"], stderr="E: Unable to locate package nginx", exit_code=100
        )

        with pytest.raises(DependencyError) as caught:
            provisioner(runner, PackageFamily.APT).ensure_webserver("nginx")

        assert "Unable to locate package nginx" in str(caught.value)

    def test_without_a_package_manager_it_says_so_instead_of_guessing(self) -> None:
        runner = FakeRunner().only_knows()

        with pytest.raises(DependencyError) as caught:
            provisioner(runner, PackageFamily.NONE).ensure_webserver("nginx")

        assert "apt, dnf or zypper" in str(caught.value)

    def test_a_name_that_is_not_a_web_server_is_refused(self) -> None:
        with pytest.raises(DependencyError):
            provisioner(FakeRunner(), PackageFamily.APT).ensure_webserver("caddy; rm -rf /")

    def test_what_it_would_do_can_be_asked_without_doing_it(self) -> None:
        runner = FakeRunner().only_knows("apt-get")

        step = provisioner(runner, PackageFamily.APT).check_webserver("nginx")

        assert step.state == "missing"
        assert step.command == "apt-get install -y nginx"
        assert not any(call[0] == "apt-get" for call in runner.calls)


class TestDocker:
    """
    Docker is installed with what each distribution packages, not with what one of them does.

    Debian has no ``docker-compose-v2`` and no ``docker-compose-plugin`` (that one is Docker's own
    repository's), Debian 13 packages Compose v2 as ``docker-compose``, Debian 12 and 11 only
    Compose v1 (which has no ``docker compose``), and Fedora's engine is ``moby-engine``. What each
    one carries was read from packages.debian.org and mdapi.fedoraproject.org on 2026-11-12.
    """

    def installs(self, runner: FakeRunner, *packages: str) -> bool:
        return any(
            call[:3] == ("apt-get", "install", "-y") and call[3:] == packages
            for call in runner.calls
        )

    def test_ubuntu_gets_the_engine_and_the_compose_plugin_of_its_own_name(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.40.3")

        step = provisioner(runner, PackageFamily.APT, UBUNTU_2404).ensure_docker()

        assert step.state == "installed"
        assert self.installs(runner, "docker.io")
        assert self.installs(runner, "docker-compose-v2")
        assert ("systemctl", "enable", "--now", "docker") in runner.calls

    def test_a_derivative_of_ubuntu_is_given_ubuntus_packages(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.40.3")

        provisioner(runner, PackageFamily.APT, MINT).ensure_docker()

        assert self.installs(runner, "docker-compose-v2")
        assert not self.installs(runner, "docker-compose")

    @pytest.mark.parametrize("release", [DEBIAN_13, DEBIAN_SID])
    def test_debian_13_and_after_package_compose_v2_as_docker_compose(
        self, release: OsRelease
    ) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.26.1")

        step = provisioner(runner, PackageFamily.APT, release).ensure_docker()

        assert step.state == "installed"
        assert self.installs(runner, "docker.io")
        assert self.installs(runner, "docker-compose")
        # Neither name Debian does not have is even tried.
        assert not self.installs(runner, "docker-compose-v2")
        assert not self.installs(runner, "docker-compose-plugin")

    @pytest.mark.parametrize(
        "release",
        [DEBIAN_12, OsRelease(id="debian", version_id="11", codename="bullseye")],
    )
    def test_debian_before_13_has_no_compose_v2_and_nothing_is_installed_to_find_that_out(
        self, release: OsRelease
    ) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")

        with pytest.raises(DependencyError) as caught:
            provisioner(runner, PackageFamily.APT, release).ensure_docker()

        said = str(caught.value)
        assert "Compose v1" in said
        assert "docs.docker.com/engine/install/debian" in said
        assert not any(call[0] == "apt-get" and "install" in call for call in runner.calls)

    def test_what_a_machine_without_docker_is_told_to_run_is_the_command_that_works_there(
        self,
    ) -> None:
        runner = FakeRunner().only_knows("apt-get")

        ubuntu = provisioner(runner, PackageFamily.APT, UBUNTU_2404).check_docker()
        debian = provisioner(runner, PackageFamily.APT, DEBIAN_13).check_docker()
        old = provisioner(runner, PackageFamily.APT, DEBIAN_12).check_docker()

        assert ubuntu.command == "apt-get install -y docker.io docker-compose-v2"
        assert debian.command == "apt-get install -y docker.io docker-compose"
        assert "docs.docker.com/engine/install/debian" in old.command
        assert not any(call[0] == "apt-get" for call in runner.calls)

    def test_a_distribution_that_is_not_known_tries_each_name_and_keeps_the_one_that_answers(
        self,
    ) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["apt-get", "install", "-y", "docker-compose-v2"], exit_code=100)
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.26.1")

        step = provisioner(runner, PackageFamily.APT, UNKNOWN).ensure_docker()

        assert step.state == "installed"
        assert self.installs(runner, "docker-compose")

    def test_no_compose_at_all_fails_and_says_what_is_missing(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["apt-get", "install", "-y", "docker-compose-v2"], exit_code=100)
        runner.script(["apt-get", "install", "-y", "docker-compose"], exit_code=100)
        runner.script(["docker", "compose", "version"], stderr="unknown command", exit_code=1)

        with pytest.raises(DependencyError) as caught:
            provisioner(runner, PackageFamily.APT, UNKNOWN).ensure_docker()

        assert "Compose" in str(caught.value)
        assert "docker-compose-v2, docker-compose" in str(caught.value)

    def test_fedora_gets_moby_engine_and_docker_compose(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "dnf")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v5.7.1")

        step = provisioner(runner, PackageFamily.DNF, FEDORA_44).ensure_docker()

        assert step.state == "installed"
        assert ("dnf", "install", "-y", "moby-engine") in runner.calls
        assert ("dnf", "install", "-y", "docker-compose") in runner.calls
        assert ("systemctl", "enable", "--now", "docker") in runner.calls

    def test_fedora_is_told_the_command_that_works_there(self) -> None:
        runner = FakeRunner().only_knows("dnf")

        step = provisioner(runner, PackageFamily.DNF, FEDORA_44).check_docker()

        assert step.command == "dnf install -y moby-engine docker-compose"

    @pytest.mark.parametrize("release", [ROCKY_9, UNKNOWN])
    def test_a_family_that_does_not_carry_docker_is_told_how_instead_of_given_a_repository(
        self, release: OsRelease
    ) -> None:
        # Rocky and its kind are dnf machines whose ID_LIKE says fedora: the engine is not in
        # their repositories, so the package of Fedora is not tried there.
        runner = FakeRunner().only_knows("dnf")

        with pytest.raises(DependencyError) as caught:
            provisioner(runner, PackageFamily.DNF, release).ensure_docker()

        assert "docs.docker.com/engine/install" in str(caught.value)
        assert not any(call[:2] == ("dnf", "install") for call in runner.calls)

    def test_opensuse_keeps_its_own_names(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "zypper")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.40.3")

        provisioner(runner, PackageFamily.ZYPPER, OPENSUSE).ensure_docker()

        assert ("zypper", "--non-interactive", "install", "docker") in runner.calls
        assert ("zypper", "--non-interactive", "install", "docker-compose") in runner.calls

    def test_docker_that_is_there_with_compose_is_left_alone(self) -> None:
        runner = FakeRunner().only_knows("docker")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.29")

        step = provisioner(runner, PackageFamily.APT, DEBIAN_12).ensure_docker()

        assert step.state == "present"
