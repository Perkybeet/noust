# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The software Noust installs, uses and may remove, written down once (item 118).

Six things installed packages and none removed them, so what the server carried was in nobody's
list: the Software page, the "this is missing" notice, the installers and the guards each had their
own idea of what nginx or cloudflared is. This registry is the one definition (rule 3). A
**component** says:

- what it is for (``purpose``, the code of a sentence of :mod:`noust.core.server_text`, which the
  console translates);
- its packages on each family, and the programs whose presence says it is installed;
- the marks Noust leaves that tell its own install from one that was there before (a
  ``noust-*.list`` file, the key beside it);
- how serious removing it is (:class:`Severity`): never, only while nothing depends on it, or
  freely;
- how it is installed (:class:`Installer`): through the package manager, or through the installer
  that already knows its repository, its key or its configuration;
- who depends on it, which is a question the code already answers (``component_dependents``).

What it does not hold is behaviour. :class:`~noust.managers.server.software.SoftwareManager` reads
it to list, install and remove; the console's "this is missing" notice and the guards read the
manager. A package name is never spelled anywhere else for these.

Never removable: Noust and what it is built on, systemd, sshd, the firewall, git, curl and gnupg
(Noust fetches and verifies the repositories and installers it uses with the last two). A removal
of any package that would take one of them along is refused as well, by the manager.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from noust.core import dependencies
from noust.managers.database import flavours
from noust.managers.server import component_dependents as who
from noust.managers.server.host import PackageFamily

__all__ = [
    "COMPONENTS",
    "Component",
    "Installer",
    "Severity",
    "component",
    "component_of_engine",
    "ids",
    "other_components",
    "owner_of",
]


class Severity(str, Enum):
    """How serious it is to remove a component."""

    #: Never, whatever depends on it or not.
    NEVER = "never"
    #: Only while nothing depends on it: the guard asks :attr:`Component.dependents`.
    CRITICAL_WHILE = "critical_while"
    #: When the operator wants; what depends on it, if anything, is only said.
    REMOVABLE = "removable"


class Installer(str, Enum):
    """The code that installs a component."""

    #: The package manager, through :meth:`~noust.managers.server.pkg.base.PackageBackend.install`.
    PACKAGE = "package"
    #: A database engine, through the database service (its flavours, repositories and versions).
    DATABASE_ENGINE = "database_engine"
    #: cloudflared, from Cloudflare's repository with its pinned key.
    CLOUDFLARED = "cloudflared"
    #: ModSecurity and the Core Rule Set, for nginx.
    WAF = "waf"
    #: fail2ban, with the packages and the EPEL step each family needs.
    FAIL2BAN = "fail2ban"
    #: Knot DNS, for Noust's own name server.
    KNOT = "knot"
    #: Docker and its Compose plugin.
    DOCKER = "docker"


Packages = Mapping[PackageFamily, tuple[str, ...]]


def _by_family(
    apt: tuple[str, ...] = (), dnf: tuple[str, ...] = (), zypper: tuple[str, ...] = ()
) -> Packages:
    """
    Args:
        apt: Package names on Debian and Ubuntu.
        dnf: Package names on Fedora, RHEL and its rebuilds.
        zypper: Package names on openSUSE.

    Returns:
        The names by family.
    """
    return {PackageFamily.APT: apt, PackageFamily.DNF: dnf, PackageFamily.ZYPPER: zypper}


def _same(*names: str) -> Packages:
    """
    Args:
        names: Package names every family calls the same.

    Returns:
        The names by family.
    """
    return _by_family(names, names, names)


def _of_dependency(dependency: dependencies.Dependency) -> Packages:
    """
    Take the package names of a dependency the installation check already declares.

    Args:
        dependency: One of ``core/dependencies.py``: ``install_dnf`` and ``install_zypper`` are
            empty where the package is called what apt calls it.

    Returns:
        The names by family.
    """
    apt = tuple((dependency.install_apt or "").split())
    return _by_family(
        apt,
        tuple((dependency.install_dnf or dependency.install_apt or "").split()),
        tuple((dependency.install_zypper or dependency.install_apt or "").split()),
    )


@dataclass(frozen=True)
class Component:
    """
    One piece of software of the server.

    Attributes:
        id: Lower-case words and ``_``; the ledger's key and the API's.
        purpose: The code of the sentence that says what Noust uses it for.
        severity: How serious removing it is.
        packages: What an install brings, by family; empty for a family Noust installs nothing on
            (the component is then listed, and cannot be installed there).
        related: Other packages that belong to it and go with it when they are installed (the
            package that carries nginx's binary, the Docker engine of another vendor). Never a
            pattern: a package manager is only handed names.
        binaries: Programs whose presence on the path says it is installed.
        marks: Absolute paths, globs allowed, that Noust writes and nobody else does: an install
            that finds one was Noust's. They are also what a removal takes back out.
        installer: What installs it.
        install_families: The families the installer works on, when it is not every one.
        engine: The database engine manager it is, for :attr:`Installer.DATABASE_ENGINE`.
        dependents: Who depends on it, asked at the time (the stores and managers that already
            answer it); None when nothing does.
        never_reason: The code of the sentence that says why it is never removed.
    """

    id: str
    purpose: str
    severity: Severity
    packages: Packages = field(default_factory=dict)
    related: Packages = field(default_factory=dict)
    binaries: tuple[str, ...] = ()
    marks: tuple[str, ...] = ()
    installer: Installer = Installer.PACKAGE
    install_families: frozenset[PackageFamily] | None = None
    engine: str | None = None
    dependents: Callable[[], list[who.Dependent]] | None = None
    never_reason: str | None = None

    def packages_on(self, family: PackageFamily) -> tuple[str, ...]:
        """
        Args:
            family: A package manager.

        Returns:
            What installing it brings there.
        """
        return tuple(self.packages.get(family, ()))

    def candidates_on(self, family: PackageFamily) -> tuple[str, ...]:
        """
        Args:
            family: A package manager.

        Returns:
            Every package of it that may be installed there: what an install brings and the
            related ones, which is what a removal looks for.
        """
        return (*self.packages_on(family), *self.related.get(family, ()))

    def installable_on(self, family: PackageFamily) -> bool:
        """
        Args:
            family: A package manager.

        Returns:
            Whether Noust installs it on that family: the installer's families, or, for the
            package manager's own install, a package to name.
        """
        if family is PackageFamily.NONE:
            return False
        if self.install_families is not None:
            return family in self.install_families
        if self.installer is Installer.PACKAGE:
            return bool(self.packages_on(family))
        return True


_APT_REPOSITORIES = "/etc/apt/sources.list.d"
_KEYRINGS = "/usr/share/keyrings"
_DNF_REPOSITORIES = "/etc/yum.repos.d"
_ZYPPER_REPOSITORIES = "/etc/zypp/repos.d"
_RPM_KEYS = "/etc/pki/rpm-gpg"


def _engine_marks(*stems: str) -> tuple[str, ...]:
    """
    Name the files Noust writes for an engine's upstream repositories, on every family.

    Args:
        stems: ``pgdg``, ``mariadb``, ``mysql``, ``mongodb``: what follows ``noust-`` in the name
            of a repository file.

    Returns:
        The apt source and keyring (written from the one table of the flavours) and the glob of the
        rpm repositories and their keys.
    """
    marks: list[str] = []
    for stem in stems:
        marks.append(f"{_DNF_REPOSITORIES}/noust-{stem}*.repo")
        marks.append(f"{_ZYPPER_REPOSITORIES}/noust-{stem}*.repo")
        marks.append(f"{_RPM_KEYS}/RPM-GPG-KEY-noust-{stem}*")
    return tuple(marks)


def _apt_marks_of(*flavour_names: str) -> tuple[str, ...]:
    """
    Args:
        flavour_names: Flavours of the database engines that have an apt repository of Noust's.

    Returns:
        The source and keyring files of each, from :data:`flavours.APT_REPOSITORY_FILES`.
    """
    return tuple(
        str(path) for name in flavour_names for path in flavours.APT_REPOSITORY_FILES[name]
    )


def _php_fpm_versions() -> tuple[str, ...]:
    """
    Returns:
        The names ``apt`` gives each PHP minor's FPM, for the minors Noust installs
        (``php8.3-fpm``): an exact name each, because a package manager is never given a pattern.
    """
    return tuple(
        f"php{major}.{minor}-fpm"
        for major, minors in ((7, (4,)), (8, range(0, 6)))
        for minor in minors
    )


#: Every component, in the order the page shows them: what the machine is made of, what serves
#: sites, what runs applications, the engines, the network and the rest.
COMPONENTS: tuple[Component, ...] = (
    Component(
        "noust",
        "software.noust.purpose",
        Severity.NEVER,
        packages=_same("noust"),
        binaries=("noust",),
        never_reason="software.noust.never",
    ),
    Component(
        "systemd",
        "software.systemd.purpose",
        Severity.NEVER,
        packages=_same("systemd"),
        binaries=("systemctl",),
        never_reason="software.systemd.never",
    ),
    Component(
        "sshd",
        "software.sshd.purpose",
        Severity.NEVER,
        packages=_by_family(("openssh-server",), ("openssh-server",), ("openssh",)),
        binaries=("sshd",),
        never_reason="software.sshd.never",
    ),
    Component(
        "firewall",
        "software.firewall.purpose",
        Severity.NEVER,
        packages=_by_family(("ufw",), ("firewalld",), ("firewalld",)),
        related=_same("nftables", "iptables"),
        binaries=("ufw", "firewall-cmd", "nft"),
        never_reason="software.firewall.never",
    ),
    Component(
        "git",
        "software.git.purpose",
        Severity.NEVER,
        packages=_of_dependency(dependencies.SYSTEM_DEPENDENCIES[0]),
        binaries=("git",),
        never_reason="software.git.never",
    ),
    Component(
        "curl",
        "software.curl.purpose",
        Severity.NEVER,
        packages=_of_dependency(dependencies.SYSTEM_DEPENDENCIES[1]),
        binaries=("curl",),
        never_reason="software.curl.never",
    ),
    Component(
        "gnupg",
        "software.gnupg.purpose",
        Severity.NEVER,
        packages=_by_family(("gnupg",), ("gnupg2",), ("gpg2",)),
        binaries=("gpg",),
        never_reason="software.gnupg.never",
    ),
    Component(
        "nginx",
        "software.nginx.purpose",
        Severity.CRITICAL_WHILE,
        packages=_same("nginx"),
        # The packages that carry the binary: removing the metapackage alone leaves nginx there.
        related=_by_family(
            ("nginx-core", "nginx-full", "nginx-light", "nginx-extras"), ("nginx-core",), ()
        ),
        binaries=("nginx",),
        dependents=who.nginx_users,
    ),
    Component(
        "apache",
        "software.apache.purpose",
        Severity.CRITICAL_WHILE,
        packages=_by_family(("apache2",), ("httpd",), ("apache2",)),
        related=_by_family(("apache2-bin",), ("httpd-core",), ()),
        binaries=("apache2", "httpd"),
        dependents=who.apache_users,
    ),
    Component(
        "certbot",
        "software.certbot.purpose",
        Severity.CRITICAL_WHILE,
        packages=_same("certbot"),
        related=_same("python3-certbot-nginx", "python3-certbot-apache"),
        binaries=("certbot",),
        dependents=who.certbot_users,
    ),
    Component(
        "nodejs",
        "software.nodejs.purpose",
        Severity.CRITICAL_WHILE,
        packages=_same("nodejs"),
        related=_by_family(("npm",), (), ()),
        binaries=("node",),
        dependents=who.node_users,
    ),
    Component(
        "php",
        "software.php.purpose",
        Severity.CRITICAL_WHILE,
        packages=_of_dependency(dependencies.PHP_FPM_DEPENDENCY),
        related=_by_family(_php_fpm_versions(), (), ()),
        dependents=who.php_users,
    ),
    Component(
        "docker",
        "software.docker.purpose",
        Severity.CRITICAL_WHILE,
        packages=_by_family(("docker.io", "docker-compose-v2"), (), ("docker", "docker-compose")),
        # What Docker's own repository installs, which an operator may have used instead.
        related=_by_family(
            ("docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"),
            ("docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"),
            (),
        ),
        binaries=("docker",),
        installer=Installer.DOCKER,
        install_families=frozenset({PackageFamily.APT, PackageFamily.ZYPPER}),
        dependents=who.docker_users,
    ),
    Component(
        "postgresql",
        "software.postgresql.purpose",
        Severity.CRITICAL_WHILE,
        binaries=("psql",),
        marks=(*_apt_marks_of("postgresql"), *_engine_marks("pgdg")),
        installer=Installer.DATABASE_ENGINE,
        engine="postgresql",
        dependents=who.postgresql_users,
    ),
    Component(
        "mysql",
        "software.mysql.purpose",
        Severity.CRITICAL_WHILE,
        binaries=("mysql",),
        marks=(*_apt_marks_of("mariadb", "mysql"), *_engine_marks("mariadb", "mysql")),
        installer=Installer.DATABASE_ENGINE,
        engine="mysql",
        dependents=who.mysql_users,
    ),
    Component(
        "redis",
        "software.redis.purpose",
        Severity.CRITICAL_WHILE,
        binaries=("redis-cli", "valkey-cli"),
        installer=Installer.DATABASE_ENGINE,
        engine="redis",
        dependents=who.redis_users,
    ),
    Component(
        "mongodb",
        "software.mongodb.purpose",
        Severity.CRITICAL_WHILE,
        binaries=("mongosh",),
        marks=_engine_marks("mongodb"),
        installer=Installer.DATABASE_ENGINE,
        engine="mongodb",
        dependents=who.mongodb_users,
    ),
    Component(
        "cloudflared",
        "software.cloudflared.purpose",
        Severity.CRITICAL_WHILE,
        packages=_same("cloudflared"),
        binaries=("cloudflared",),
        marks=(
            f"{_APT_REPOSITORIES}/noust-cloudflared.list",
            f"{_KEYRINGS}/noust-cloudflared.gpg",
            f"{_DNF_REPOSITORIES}/noust-cloudflared.repo",
            f"{_ZYPPER_REPOSITORIES}/noust-cloudflared.repo",
            f"{_RPM_KEYS}/RPM-GPG-KEY-noust-cloudflared",
        ),
        installer=Installer.CLOUDFLARED,
        dependents=who.tunnel_users,
    ),
    Component(
        "nginx_stream_module",
        "software.nginx_stream_module.purpose",
        Severity.CRITICAL_WHILE,
        # openSUSE ships nginx with the stream module built in: there is no package to install.
        packages=_by_family(("libnginx-mod-stream",), ("nginx-mod-stream",), ()),
        dependents=who.stream_users,
    ),
    Component(
        "waf",
        "software.waf.purpose",
        Severity.CRITICAL_WHILE,
        packages=_by_family(("libnginx-mod-http-modsecurity", "modsecurity-crs"), (), ()),
        installer=Installer.WAF,
        install_families=frozenset({PackageFamily.APT}),
        dependents=who.waf_users,
    ),
    Component(
        "knot",
        "software.knot.purpose",
        Severity.CRITICAL_WHILE,
        packages=_same("knot"),
        related=_by_family(("knot-dnsutils",), ("knot-utils",), ("knot-utils",)),
        binaries=("knotd",),
        installer=Installer.KNOT,
        dependents=who.dns_zone_users,
    ),
    Component(
        "fail2ban",
        "software.fail2ban.purpose",
        Severity.REMOVABLE,
        packages=_by_family(("fail2ban", "python3-systemd"), ("fail2ban",), ("fail2ban",)),
        binaries=("fail2ban-client",),
        installer=Installer.FAIL2BAN,
    ),
    Component(
        "chrony",
        "software.chrony.purpose",
        Severity.REMOVABLE,
        packages=_same("chrony"),
        binaries=("chronyc",),
    ),
    Component(
        "auto_updates",
        "software.auto_updates.purpose",
        Severity.REMOVABLE,
        packages=_by_family(("unattended-upgrades",), ("dnf-automatic",), ()),
    ),
    Component(
        "rclone",
        "software.rclone.purpose",
        Severity.CRITICAL_WHILE,
        packages=_of_dependency(dependencies.RCLONE_DEPENDENCY),
        binaries=("rclone",),
        dependents=who.backup_destination_users,
    ),
)

_BY_ID = {item.id: item for item in COMPONENTS}


def ids() -> tuple[str, ...]:
    """
    Returns:
        Every component's id, in the order the page shows them.
    """
    return tuple(item.id for item in COMPONENTS)


def component(component_id: str) -> Component | None:
    """
    Args:
        component_id: What a request named.

    Returns:
        The component, or None when the registry has none of that id. The registry is the
        allow-list: an id that is not here is never looked up anywhere else.
    """
    return _BY_ID.get(component_id)


def component_of_engine(engine: str) -> Component | None:
    """
    Args:
        engine: A database engine's manager name (``postgresql``, ``mysql``, ``redis``).

    Returns:
        The component that engine is, or None for one that is only ever an instance in a
        container.
    """
    for item in COMPONENTS:
        if item.engine == engine:
            return item
    return None


def other_components(component_id: str) -> tuple[Component, ...]:
    """
    Args:
        component_id: A component's id.

    Returns:
        Every other component.
    """
    return tuple(item for item in COMPONENTS if item.id != component_id)


def owner_of(package: str, family: PackageFamily) -> Component | None:
    """
    Find which component a package belongs to.

    Args:
        package: A package name.
        family: The package manager it is a name of.

    Returns:
        The component that lists it among its packages or its related ones, or None.
    """
    for item in COMPONENTS:
        if package in item.candidates_on(family):
            return item
    return None
