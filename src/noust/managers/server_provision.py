# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The software a server needs under Noust: the web server and Docker.

``noust restore-server`` brings the applications of an old server back on a new machine, and an
application that needs nginx or Docker cannot start on a machine that has neither. This module
installs them, with the package manager that is there (apt, dnf or zypper, found by the program
that exists, never guessed) and nothing else.

Two rules:

* **Nothing is installed that was not asked for**, and nothing from a repository nobody chose.
  A distribution whose own repositories do not carry Docker (the RPM families, apart from
  Fedora and openSUSE) is told how, in a sentence, instead of being given a third-party
  repository.
* **A failure says what the package manager said.** Every command is an argv with a deadline.

The database engines are installed by their own manager (``noust db install``); this is the rest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from noust.core.exceptions import DependencyError
from noust.core.logger import Logger
from noust.core.package_family import PackageFamily, detect_family
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.host import OsRelease, read_os_release

__all__ = ["DockerPlan", "ProvisionStep", "ServerProvisioner", "docker_plan"]

#: Seconds a package manager refreshing its lists gets.
REFRESH_TIMEOUT = 300

#: Seconds an install gets.
INSTALL_TIMEOUT = 900

#: Seconds systemctl gets.
SYSTEMCTL_TIMEOUT = 120

#: What a package manager must not stop to ask.
_ENV = {"DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}


@dataclass(frozen=True)
class _WebServer:
    """
    A web server as the distributions package it.

    Attributes:
        programs: Any of these on the machine says it is installed.
        packages: The package each family calls it.
        units: The systemd unit each family calls it.
    """

    programs: tuple[str, ...]
    packages: Mapping[PackageFamily, str]
    units: Mapping[PackageFamily, str]


#: The web servers, by the name the configuration gives them.
_WEBSERVERS: dict[str, _WebServer] = {
    "nginx": _WebServer(
        ("nginx",),
        {
            PackageFamily.APT: "nginx",
            PackageFamily.DNF: "nginx",
            PackageFamily.ZYPPER: "nginx",
        },
        {
            PackageFamily.APT: "nginx",
            PackageFamily.DNF: "nginx",
            PackageFamily.ZYPPER: "nginx",
        },
    ),
    "apache": _WebServer(
        ("apache2", "httpd"),
        {
            PackageFamily.APT: "apache2",
            PackageFamily.DNF: "httpd",
            PackageFamily.ZYPPER: "apache2",
        },
        {
            PackageFamily.APT: "apache2",
            PackageFamily.DNF: "httpd",
            PackageFamily.ZYPPER: "apache2",
        },
    ),
}

#: Where Docker says how to install its engine on every distribution, and on Debian, whose own
#: repositories cannot give Compose v2 before Debian 13.
DOCKER_INSTALL_DOCS = "https://docs.docker.com/engine/install/"
DOCKER_INSTALL_DOCS_DEBIAN = "https://docs.docker.com/engine/install/debian/"


@dataclass(frozen=True)
class DockerPlan:
    """
    What installs Docker with its Compose plugin on one distribution, from its own repositories.

    Attributes:
        engine: The packages that bring the engine and its client.
        compose: The packages that bring ``docker compose`` (Compose v2), tried in order; the
            first that installs and answers ``docker compose version`` is kept. Empty when the
            distribution's repositories have none: Debian 12 and 11 package Compose v1, a
            program of its own that is not the plugin.
    """

    engine: tuple[str, ...]
    compose: tuple[str, ...]


#: The Debian release that first packages Compose v2 (``docker-compose`` 2.26.1 in trixie; in
#: bookworm the package of that name is Compose 1.29.2).
_DEBIAN_WITH_COMPOSE_V2 = 13

#: ``docker-compose-v2`` is Ubuntu's name for the plugin: Debian has no package of that name, and
#: ``docker-compose-plugin`` is the one of Docker's own repository, which Noust does not add.
_UBUNTU = DockerPlan(("docker.io",), ("docker-compose-v2",))
_DEBIAN = DockerPlan(("docker.io",), ("docker-compose",))
_DEBIAN_OLD = DockerPlan(("docker.io",), ())
#: Fedora's engine is Moby (``moby-engine`` brings the ``docker`` client with it) and its Compose
#: is the Go one, ``docker-compose``, from Fedora 43.
_FEDORA = DockerPlan(("moby-engine",), ("docker-compose",))
_ZYPPER = DockerPlan(("docker",), ("docker-compose",))
#: A distribution of the apt family that is not known by name: each name, verified by asking.
_APT_UNKNOWN = DockerPlan(("docker.io",), ("docker-compose-v2", "docker-compose"))


def docker_plan(family: PackageFamily, os_release: OsRelease) -> DockerPlan | None:
    """
    Choose what installs Docker on a distribution.

    The package names are the distribution's, not a family's: ``docker-compose-v2`` exists on
    Ubuntu and not on Debian, ``docker-compose`` is Compose v2 on Debian 13 and Compose v1 before
    it, and the RPM family is Fedora and a dozen that do not package Docker at all.

    Args:
        family: The package manager.
        os_release: The distribution's identity.

    Returns:
        The plan, or None when the distribution's own repositories do not carry Docker.
    """
    if family is PackageFamily.ZYPPER:
        return _ZYPPER
    if family is PackageFamily.DNF:
        return _FEDORA if os_release.id == "fedora" else None
    if family is not PackageFamily.APT:
        return None
    if os_release.id == "ubuntu" or "ubuntu" in os_release.id_like:
        return _UBUNTU
    if os_release.id == "debian" or "debian" in os_release.id_like:
        release = _debian_release(os_release)
        if release is None:
            return _APT_UNKNOWN
        return _DEBIAN if release >= _DEBIAN_WITH_COMPOSE_V2 else _DEBIAN_OLD
    return _APT_UNKNOWN


def _debian_release(os_release: OsRelease) -> int | None:
    """
    Args:
        os_release: A Debian or a distribution made from it.

    Returns:
        The Debian release it carries: its major version, or a number past every released one
        for testing and unstable (which have no ``VERSION_ID``), or None when it cannot be told.
    """
    major = os_release.version_id.split(".", 1)[0]
    if major.isdigit():
        return int(major)
    if os_release.codename in {"forky", "sid"}:
        return 99
    return None


@dataclass(frozen=True)
class ProvisionStep:
    """
    What was found or done about one requirement.

    Attributes:
        name: The requirement (``nginx``, ``docker``).
        state: ``present`` (already there), ``installed`` (installed now) or ``missing``
            (only looked at: it is not there).
        detail: A sentence for the operator.
        command: The command that installs it by hand.
    """

    name: str
    state: Literal["present", "installed", "missing"]
    detail: str = ""
    command: str = ""


class ServerProvisioner:
    """
    Check for, and install, the web server and Docker.

    Args:
        runner: Every process goes through it.
        logger: Where progress is reported.
        family: The package manager's family; found by the programs that exist when None.
        os_release: The distribution's identity, which decides the names of Docker's packages;
            read from the machine when None.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        logger: Logger | None = None,
        family: PackageFamily | None = None,
        os_release: OsRelease | None = None,
    ) -> None:
        self._runner = runner
        self.logger = logger or Logger(verbose=False)
        self._family = family
        self._os_release = os_release

    @property
    def runner(self) -> CommandRunner:
        """The runner every process goes through."""
        return self._runner or get_runner()

    @property
    def family(self) -> PackageFamily:
        """This machine's package manager."""
        if self._family is None:
            self._family = detect_family(self.runner)[0]
        return self._family

    @property
    def os_release(self) -> OsRelease:
        """This machine's distribution."""
        if self._os_release is None:
            self._os_release = read_os_release()
        return self._os_release

    # ------------------------------------------------------------------ helpers

    def _install_argv(self, packages: tuple[str, ...]) -> list[str]:
        """
        Build the command that installs packages without asking.

        Args:
            packages: Package names, from this module's tables.

        Returns:
            The argv.

        Raises:
            DependencyError: There is no package manager Noust can drive.
        """
        family = self.family
        if family is PackageFamily.APT:
            return ["apt-get", "install", "-y", *packages]
        if family is PackageFamily.DNF:
            return ["dnf", "install", "-y", *packages]
        if family is PackageFamily.ZYPPER:
            return ["zypper", "--non-interactive", "install", *packages]
        raise DependencyError(
            "This server has no package manager Noust can drive",
            details="Noust installs with apt, dnf or zypper. Install the software with this "
            "machine's own tool, then run the command again.",
        )

    def _install(self, packages: tuple[str, ...]) -> None:
        """
        Install packages, refreshing the lists first where the manager needs it.

        Args:
            packages: Package names.

        Raises:
            DependencyError: The package manager refused; its words are in the error.
        """
        argv = self._install_argv(packages)
        if self.family is PackageFamily.APT:
            # A fresh machine has no lists at all; a failed refresh is not fatal, the install
            # says what is wrong if it is.
            self.runner.run(["apt-get", "update"], env=_ENV, timeout=REFRESH_TIMEOUT)
        result = self.runner.run(argv, env=_ENV, timeout=INSTALL_TIMEOUT)
        if not result.success:
            raise DependencyError(
                f"Could not install {' '.join(packages)}",
                details=(result.stderr or result.stdout).strip()
                or f"{argv[0]} exited with code {result.exit_code}.",
            )

    def _enable(self, unit: str) -> None:
        """
        Enable a unit and start it.

        Args:
            unit: The systemd unit.

        Raises:
            DependencyError: systemd refused.
        """
        result = self.runner.run(["systemctl", "enable", "--now", unit], timeout=SYSTEMCTL_TIMEOUT)
        if not result.success:
            raise DependencyError(
                f"Installed, but {unit} would not start",
                details=(result.stderr or result.stdout).strip() or "See: systemctl status " + unit,
            )

    # --------------------------------------------------------------- web server

    def _webserver(self, name: str) -> _WebServer:
        """
        Look a web server up in the table.

        Args:
            name: ``nginx`` or ``apache``.

        Returns:
            Its entry.

        Raises:
            DependencyError: Not one Noust installs.
        """
        entry = _WEBSERVERS.get(name)
        if entry is None:
            raise DependencyError(
                f"{name!r} is not a web server Noust installs",
                details=f"Use one of: {', '.join(sorted(_WEBSERVERS))}.",
            )
        return entry

    def _webserver_present(self, entry: _WebServer) -> bool:
        """
        Args:
            entry: A web server's table entry.

        Returns:
            Whether one of its programs is installed.
        """
        return any(self.runner.exists(program) for program in entry.programs)

    def check_webserver(self, name: str) -> ProvisionStep:
        """
        Say whether a web server is installed, and how to install it. Changes nothing.

        Args:
            name: ``nginx`` or ``apache``.

        Returns:
            ``present`` or ``missing``, with the command that installs it.

        Raises:
            DependencyError: Not a web server Noust installs.
        """
        entry = self._webserver(name)
        if self._webserver_present(entry):
            return ProvisionStep(name, "present", f"{name} is installed")
        package = entry.packages.get(self.family)
        command = " ".join(self._install_argv((package,))) if package else ""
        return ProvisionStep(name, "missing", f"{name} is not installed", command)

    def ensure_webserver(self, name: str) -> ProvisionStep:
        """
        Install a web server unless it is there, and start it.

        Args:
            name: ``nginx`` or ``apache``.

        Returns:
            ``present`` or ``installed``.

        Raises:
            DependencyError: Not a web server Noust installs, no package manager, or the
                installation failed.
        """
        entry = self._webserver(name)
        if self._webserver_present(entry):
            return ProvisionStep(name, "present", f"{name} is installed")
        package = entry.packages.get(self.family)
        if package is None:
            self._install_argv(())  # no manager: the error says so
            raise DependencyError(f"{name} cannot be installed on this server")
        self.logger.info(f"Installing {name}")
        self._install((package,))
        self._enable(entry.units[self.family])
        return ProvisionStep(name, "installed", f"{name} was installed and started")

    # ------------------------------------------------------------------- Docker

    def _compose_ready(self) -> bool:
        """
        Returns:
            Whether ``docker compose`` answers.
        """
        result = self.runner.run(["docker", "compose", "version"], timeout=SYSTEMCTL_TIMEOUT)
        return result.success

    def _docker_ready(self) -> bool:
        """
        Returns:
            Whether the ``docker`` client is there and has its Compose plugin.
        """
        return self.runner.exists("docker") and self._compose_ready()

    def check_docker(self) -> ProvisionStep:
        """
        Say whether Docker with Compose is installed, and how to install it. Changes nothing.

        Returns:
            ``present`` or ``missing``.
        """
        if self._docker_ready():
            return ProvisionStep("docker", "present", "Docker and its Compose plugin are installed")
        plan = docker_plan(self.family, self.os_release)
        if plan is None:
            command = f"See {DOCKER_INSTALL_DOCS}"
        elif not plan.compose:
            command = f"See {DOCKER_INSTALL_DOCS_DEBIAN}"
        else:
            command = " ".join(self._install_argv((*plan.engine, plan.compose[0])))
        return ProvisionStep(
            "docker", "missing", "Docker or its Compose plugin is missing", command
        )

    def ensure_docker(self) -> ProvisionStep:
        """
        Install Docker and its Compose plugin unless they are there, and start the daemon.

        Returns:
            ``present`` or ``installed``.

        Raises:
            DependencyError: This distribution does not carry Docker, or its Compose v2, in its
                own repositories (the message says where Docker explains how), there is no
                package manager, or the installation failed.
        """
        if self._docker_ready():
            return ProvisionStep("docker", "present", "Docker and its Compose plugin are installed")
        plan = docker_plan(self.family, self.os_release)
        if plan is None:
            if self.family is PackageFamily.NONE:
                self._install_argv(())
            raise DependencyError(
                "Docker is not in this distribution's own repositories",
                details=f"Add Docker's repository and install docker-ce with its Compose plugin "
                f"as {DOCKER_INSTALL_DOCS} explains, then run the command again. Noust does not "
                "add a third-party repository by itself.",
            )
        if not plan.compose:
            # Before installing anything: an engine without the plugin is a machine that cannot
            # deploy, and it would have to be undone by hand.
            release = self.os_release
            name = release.pretty_name if release.pretty_name != "Linux" else release.id
            raise DependencyError(
                f"{name} does not package Docker Compose v2",
                details="Its repositories carry Docker's engine (docker.io) and Compose v1, a "
                "program of its own that has no 'docker compose', and Noust needs the plugin. "
                f"Install Docker from Docker's own repository as {DOCKER_INSTALL_DOCS_DEBIAN} "
                "explains, then run the command again. Noust does not add a third-party "
                "repository by itself.",
            )
        self.logger.info("Installing Docker")
        self._install(plan.engine)
        self._enable("docker")
        for candidate in plan.compose:
            try:
                self._install((candidate,))
            except DependencyError:
                continue
            if self._compose_ready():
                return ProvisionStep("docker", "installed", "Docker and Compose were installed")
        raise DependencyError(
            "Docker was installed but its Compose plugin could not be",
            details=f"Tried: {', '.join(plan.compose)}. Install the Compose v2 plugin as "
            f"{DOCKER_INSTALL_DOCS} explains, then run the command again.",
        )
