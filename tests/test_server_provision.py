# Copyright (c) 2024-2026 Yago Lopez Prado
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
from noust.managers.server_provision import ServerProvisioner


def provisioner(runner: FakeRunner, family: PackageFamily) -> ServerProvisioner:
    return ServerProvisioner(runner=runner, family=family)


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
    def test_debian_gets_the_engine_and_the_compose_plugin(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.29")

        step = provisioner(runner, PackageFamily.APT).ensure_docker()

        assert step.state == "installed"
        assert ("apt-get", "install", "-y", "docker.io") in runner.calls
        assert ("apt-get", "install", "-y", "docker-compose-v2") in runner.calls
        assert ("systemctl", "enable", "--now", "docker") in runner.calls

    def test_a_compose_package_that_does_not_exist_falls_to_the_next_name(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["apt-get", "install", "-y", "docker-compose-v2"], exit_code=100)
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.29")

        step = provisioner(runner, PackageFamily.APT).ensure_docker()

        assert step.state == "installed"
        assert ("apt-get", "install", "-y", "docker-compose-plugin") in runner.calls

    def test_no_compose_at_all_fails_and_says_what_is_missing(self) -> None:
        runner = FakeRunner().only_knows("systemctl", "apt-get")
        runner.script(["apt-get", "install", "-y", "docker-compose-v2"], exit_code=100)
        runner.script(["apt-get", "install", "-y", "docker-compose-plugin"], exit_code=100)
        runner.script(["docker", "compose", "version"], stderr="unknown command", exit_code=1)

        with pytest.raises(DependencyError) as caught:
            provisioner(runner, PackageFamily.APT).ensure_docker()

        assert "Compose" in str(caught.value)

    def test_a_family_that_does_not_carry_docker_is_told_how_instead_of_given_a_repository(
        self,
    ) -> None:
        runner = FakeRunner().only_knows("dnf")

        with pytest.raises(DependencyError) as caught:
            provisioner(runner, PackageFamily.DNF).ensure_docker()

        assert "docs.docker.com/engine/install" in str(caught.value)
        assert not any(call[:2] == ("dnf", "install") for call in runner.calls)

    def test_docker_that_is_there_with_compose_is_left_alone(self) -> None:
        runner = FakeRunner().only_knows("docker")
        runner.script(["docker", "compose", "version"], stdout="Docker Compose version v2.29")

        step = provisioner(runner, PackageFamily.APT).ensure_docker()

        assert step.state == "present"
