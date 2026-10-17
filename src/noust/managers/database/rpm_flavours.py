# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What installing a database engine becomes on a system that is not Debian's.

:mod:`noust.managers.database.flavours` is the one answer to "which engine, in which versions,
from which packages" (rule 3), and until 3.4 it knew apt alone. This module is the same answer
for the two RPM families, written beside it because the tables are another shape: a release is
a major of the RHEL family or a Leap, not a codename, and a version comes from the distribution
as a *module stream* or a package named after it more often than from a repository Noust adds.

**What is covered is what was run.** Each table below was read from the real repositories and the
installs were run in containers of those releases (``tests/integration/engines_run.py``); a
release, an engine or a version that is not in them is refused before anything is touched, with
the way that does work (an instance, which needs no package manager), which is the safe
direction. Today:

- **The RHEL family, major 9** (Rocky Linux, AlmaLinux, Red Hat Enterprise Linux, CentOS Stream
  and Oracle Linux, which are rebuilds of the same sources and share their repositories):
  PostgreSQL, MariaDB, MySQL, Redis and Valkey from AppStream, with the module streams the
  release offers; PostgreSQL's other majors from PGDG and MongoDB from its own repository, each
  signed by a key pinned here by fingerprint.
- **openSUSE Leap 16.0 and 15.6**: PostgreSQL and MariaDB from the distribution's own
  repositories. Redis and Valkey run as templated units that need a configuration file written
  first, and MySQL and MongoDB are in no Leap repository, so those are instances.

Fedora, the next RHEL major and Tumbleweed are not in the tables yet.

A repository Noust adds is a ``.repo`` file it owns (``noust-<alias>.repo``) whose key is the
file it verified: the key is downloaded, its fingerprint compared with the pinned one, and only
then written where the package manager trusts it, exactly as for apt
(:func:`noust.managers.database.flavours.install_repository`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import DatabaseEngineError, ValidationError
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner
from noust.managers.database import flavours
from noust.managers.database.flavours import (
    MARIADB_KEY,
    MONGODB_DEFAULT_SERIES,
    MONGODB_KEYS,
    PGDG_VERSIONS,
    Flavour,
    InstallPlan,
    SigningKey,
    export_pinned_key,
)
from noust.managers.server.host import HostPaths, OsRelease

#: What an uninstall reads before it removes anything: the names of the installed packages. A
#: rehearsal runs these, because "what would be removed" is fiction without them. Written in the
#: language of :data:`noust.core.runner.READ_ONLY_PROBES`.
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (("rpm", "-qa", "--qf", "*"),)

#: The package managers of the two families.
DNF = "dnf"
ZYPPER = "zypper"

#: The distributions whose ``ID`` is the RHEL family's (they share AppStream's content).
_RHEL_IDS = frozenset({"rhel", "rocky", "almalinux", "centos", "ol"})

#: The PostgreSQL RPM Repository key (PGDG, created 2023), which signs the repository of every
#: major for the RHEL family.
PGDG_RPM_KEY = SigningKey(
    url="https://download.postgresql.org/pub/repos/yum/keys/PGDG-RPM-GPG-KEY-RHEL",
    fingerprint="D4BF08AE67A0B4C7A1DBCCD240BCA2B408B40D20",
)

#: Where the package managers keep a repository's definition and a key they trust.
_DNF_REPO_DIR = Path("/etc/yum.repos.d")
_ZYPPER_REPO_DIR = Path("/etc/zypp/repos.d")
_KEY_DIR = Path("/etc/pki/rpm-gpg")

#: MariaDB's long-term release the RHEL family gets from MariaDB itself: 10.11 and 11.8 are
#: module streams of AppStream, and 11.4 is in neither.
MARIADB_UPSTREAM = ("11.4",)

_ALIAS = re.compile(r"[a-z][a-z0-9-]{1,40}")


@dataclass(frozen=True)
class RpmRelease:
    """
    A release the RPM tables describe.

    Attributes:
        manager: ``dnf`` or ``zypper``.
        key: The table's key: ``el9``, ``leap-16.0``, ``leap-15.6``.
        name: How to name it to a person.
        repo_dir: The directory its package manager reads repository definitions from.
    """

    manager: str
    key: str
    name: str
    repo_dir: Path


@dataclass(frozen=True)
class RpmRepository:
    """
    A repository of RPM packages Noust adds, trusting only its pinned key.

    Attributes:
        name: What it is called in messages (``download.postgresql.org``).
        alias: The repository's id in its definition and the stem of its files.
        key: Its pinned signing key.
        baseurl: Where the packages are.
        manager: ``dnf`` or ``zypper``.
        directory: Where the definition is written.
        module_hotfixes: Whether dnf must show this repository's packages even where an
            AppStream module of the same name hides them. dnf compares the names without
            regard to case, so with AppStream's ``mariadb`` module in the way MariaDB's own
            ``MariaDB-server`` is "filtered out by modular filtering" and cannot be found at
            all; MariaDB's documented repository file sets this.
    """

    name: str
    alias: str
    key: SigningKey
    baseurl: str
    manager: str
    directory: Path
    module_hotfixes: bool = False

    def __post_init__(self) -> None:
        """
        Raises:
            ValueError: The alias is not one that can name a file and an INI section.
        """
        if _ALIAS.fullmatch(self.alias) is None:
            raise ValueError(f"{self.alias!r} cannot be a repository alias")

    @property
    def key_file(self) -> Path:
        """Where the verified key is written, as the package manager reads it."""
        return _KEY_DIR / f"RPM-GPG-KEY-noust-{self.alias}"

    @property
    def sources(self) -> Path:
        """The repository definition Noust owns."""
        return self.directory / f"noust-{self.alias}.repo"

    def render(self) -> str:
        """
        Write the repository definition.

        Returns:
            The ``.repo`` file: signed packages, checked against the verified key alone.
        """
        lines = [
            "# Generated by Noust. It trusts only the key it verified against a pinned fingerprint.",
            f"[noust-{self.alias}]",
            f"name={self.name} (added by Noust)",
            f"baseurl={self.baseurl}",
            "enabled=1",
            "gpgcheck=1",
            f"gpgkey=file://{self.key_file}",
        ]
        if self.manager == ZYPPER:
            lines += ["type=rpm-md", "autorefresh=1"]
        else:
            lines += ["repo_gpgcheck=0", "skip_if_unavailable=False"]
            if self.module_hotfixes:
                lines.append("module_hotfixes=1")
        return "\n".join(lines) + "\n"

    def to_name(self) -> str:
        """
        Returns:
            The repository's name, for a plan's description.
        """
        return self.name


def rpm_manager(os_release: OsRelease) -> str | None:
    """
    Say which RPM package manager a distribution uses.

    Args:
        os_release: The distribution's identity.

    Returns:
        ``dnf`` for the RHEL family and Fedora, ``zypper`` for SUSE, None for anything else
        (Debian's family is apt, which :mod:`~noust.managers.database.flavours` plans).
    """
    identity = {os_release.id.lower(), *(word.lower() for word in os_release.id_like)}
    if identity & {"debian", "ubuntu"}:
        return None
    if identity & {"rhel", "centos", "fedora"} or os_release.id.lower() in _RHEL_IDS:
        return DNF
    if os_release.id.lower().startswith(("opensuse", "sles", "suse")) or "suse" in identity:
        return ZYPPER
    return None


def rpm_release(os_release: OsRelease) -> RpmRelease | None:
    """
    Find the table a release is described by.

    Args:
        os_release: The distribution's identity.

    Returns:
        The release, or None when it is not one the tables cover.
    """
    manager = rpm_manager(os_release)
    version = os_release.version_id
    if manager == DNF and os_release.id.lower() in _RHEL_IDS and version.split(".")[0] == "9":
        return RpmRelease(DNF, "el9", f"{os_release.pretty_name}", _DNF_REPO_DIR)
    if manager == ZYPPER and os_release.id.lower() == "opensuse-leap" and version in LEAP:
        return RpmRelease(ZYPPER, f"leap-{version}", os_release.pretty_name, _ZYPPER_REPO_DIR)
    return None


#: The PostgreSQL majors and the MariaDB each Leap ships, read from its repositories
#: (``zypper info``, October 2026). Leap 15.6 is past its end of life but its repositories are
#: still served.
LEAP: dict[str, dict[str, tuple[str, ...]]] = {
    "16.0": {"postgresql": ("14", "15", "16", "17", "18"), "mariadb": ("11.8",)},
    "15.6": {"postgresql": ("14", "15", "16", "17", "18"), "mariadb": ("10.11",)},
}


@dataclass(frozen=True)
class AppStream:
    """
    What AppStream of the RHEL family, major 9, offers of one flavour.

    Attributes:
        default: The version of the plain package.
        module: The dnf module whose streams give the other versions; None when it has none.
        streams: Each version a stream gives, with the stream's name.
    """

    default: str
    module: str | None
    streams: dict[str, str]


#: What AppStream offers per flavour (``dnf module list``, Rocky Linux 9.8, October 2026).
EL9: dict[str, AppStream] = {
    "postgresql": AppStream("13", "postgresql", {"15": "15", "16": "16", "18": "18"}),
    "mariadb": AppStream("10.5", "mariadb", {"10.11": "10.11", "11.8": "11.8"}),
    "mysql": AppStream("8.0", "mysql", {"8.4": "8.4"}),
    "redis": AppStream("6.2", "redis", {"7.2": "7"}),
    "valkey": AppStream("8.0", None, {}),
}

#: The packages and unit of each flavour on the RHEL family.
EL_PACKAGES: dict[str, tuple[tuple[str, ...], str]] = {
    "postgresql": (("postgresql-server", "postgresql-contrib"), "postgresql"),
    "mariadb": (("mariadb-server",), "mariadb"),
    "mysql": (("mysql-server",), "mysqld"),
    "redis": (("redis",), "redis"),
    "valkey": (("valkey",), "valkey"),
}

#: What EL9 and Leap offer of each flavour, for the refusal of one they do not.
_INSTANCE_ADVICE = (
    "Run it as an instance, a container that needs no package manager: "
    "noust db instance create NAME --engine {flavour}."
)


def _unsupported(
    spec: Flavour, release: RpmRelease | None, os_release: OsRelease
) -> ValidationError:
    """
    Build the refusal of a release the RPM tables do not describe.

    Args:
        spec: The flavour.
        release: The release, when it is one of the tables'.
        os_release: The distribution's identity.

    Returns:
        The error, about the ``flavour`` field.
    """
    return ValidationError(
        f"Noust does not install {spec.display_name} on {os_release.pretty_name} yet",
        details=(
            "It installs database engines with dnf on the RHEL family, major 9 (Rocky Linux, "
            "AlmaLinux, Red Hat Enterprise Linux, CentOS Stream, Oracle Linux), and with zypper "
            "on openSUSE Leap 16.0 and 15.6. "
            + _INSTANCE_ADVICE.format(flavour=spec.name)
            + " Or install it with the distribution's package manager, enable and start its "
            f"service, then run 'noust db status {spec.engine}': Noust manages it from there."
        ),
        field="flavour",
    )


def _not_offered(
    spec: Flavour, release: RpmRelease, version: str | None, offered: str
) -> ValidationError:
    """
    Build the refusal of a version a release cannot be given.

    Args:
        spec: The flavour.
        release: The release.
        version: The version asked for.
        offered: What it can be given, as a sentence.

    Returns:
        The error, about the ``version`` field.
    """
    what = f"{spec.display_name} {version}" if version else spec.display_name
    return ValidationError(
        f"{what} is not available on {release.name}",
        details=f"{offered} {_INSTANCE_ADVICE.format(flavour=spec.name)}",
        field="version",
    )


def _pgdg(version: str, release: RpmRelease) -> RpmRepository:
    """
    Describe PGDG's repository of one PostgreSQL major.

    Args:
        version: The major.
        release: A RHEL-family release.

    Returns:
        The repository.
    """
    return RpmRepository(
        name="download.postgresql.org",
        alias=f"pgdg{version}",
        key=PGDG_RPM_KEY,
        baseurl=f"https://download.postgresql.org/pub/repos/yum/{version}/redhat/rhel-9-$basearch",
        manager=DNF,
        directory=release.repo_dir,
    )


def _plan_el(spec: Flavour, version: str | None, release: RpmRelease) -> InstallPlan:
    """
    Plan an engine on the RHEL family, major 9.

    Args:
        spec: The flavour.
        version: The version asked for; None for the plain package.
        release: The release.

    Returns:
        The plan.

    Raises:
        ValidationError: For a flavour or version the tables do not cover.
    """
    if spec.name == "mongodb":
        return _plan_mongodb_el(spec, version, release)
    table = EL9.get(spec.name)
    if table is None:
        raise _unsupported(spec, release, OsRelease(pretty_name=release.name))
    packages, unit = EL_PACKAGES[spec.name]
    default, module, streams = table.default, table.module, table.streams
    initialize: tuple[str, ...] = (
        ("postgresql-setup", "--initdb") if spec.name == "postgresql" else ()
    )
    if version is None or version == default:
        return InstallPlan(
            flavour=spec.name,
            engine=spec.engine,
            version=default,
            source="distribution",
            package_sets=(packages,),
            family=DNF,
            # Reset, not enabled: a stream an earlier install enabled stays enabled after its
            # packages are removed, and the plain package would then be that stream's.
            module=(module, None) if module is not None else None,
            unit=unit,
            initialize=initialize,
        )
    if version in streams and module is not None:
        return InstallPlan(
            flavour=spec.name,
            engine=spec.engine,
            version=version,
            source="distribution",
            package_sets=(packages,),
            family=DNF,
            module=(module, streams[version]),
            unit=unit,
            initialize=initialize,
        )
    if spec.name == "postgresql" and version in PGDG_VERSIONS:
        return InstallPlan(
            flavour=spec.name,
            engine=spec.engine,
            version=version,
            source="upstream",
            package_sets=((f"postgresql{version}-server", f"postgresql{version}-contrib"),),
            family=DNF,
            rpm_repository=_pgdg(version, release),
            # PGDG's packages and AppStream's module would both claim the name 'postgresql'.
            disabled_modules=("postgresql",),
            unit=f"postgresql-{version}",
            initialize=(f"/usr/pgsql-{version}/bin/postgresql-{version}-setup", "initdb"),
        )
    if spec.name == "mariadb" and version in MARIADB_UPSTREAM:
        return InstallPlan(
            flavour=spec.name,
            engine=spec.engine,
            version=version,
            source="upstream",
            package_sets=(("MariaDB-server", "MariaDB-client"),),
            family=DNF,
            rpm_repository=RpmRepository(
                name="rpm.mariadb.org",
                alias=f"mariadb{version.replace('.', '')}",
                key=MARIADB_KEY,
                baseurl=f"https://rpm.mariadb.org/{version}/rhel/9/$basearch",
                manager=DNF,
                directory=release.repo_dir,
                module_hotfixes=True,
            ),
            disabled_modules=("mariadb",),
            unit="mariadb",
        )
    offered = [default, *streams]
    if spec.name == "postgresql":
        offered = sorted({*offered, *PGDG_VERSIONS}, key=int)
    if spec.name == "mariadb":
        offered = [*offered, *MARIADB_UPSTREAM]
    raise _not_offered(
        spec,
        release,
        version,
        f"{release.name} can be given {spec.display_name} {', '.join(offered)}.",
    )


def _plan_mongodb_el(spec: Flavour, version: str | None, release: RpmRelease) -> InstallPlan:
    """
    Plan MongoDB from its own repository, the only one with ``mongodb-org``.

    Args:
        spec: The flavour.
        version: The series asked for; the default series when None.
        release: The release.

    Returns:
        The plan.

    Raises:
        ValidationError: For a series Noust does not install.
    """
    series = version or MONGODB_DEFAULT_SERIES
    if series not in MONGODB_KEYS:
        raise _not_offered(
            spec, release, series, f"Noust installs MongoDB {', '.join(MONGODB_KEYS)}."
        )
    return InstallPlan(
        flavour=spec.name,
        engine=spec.engine,
        version=series,
        source="upstream",
        package_sets=(("mongodb-org",),),
        family=DNF,
        rpm_repository=RpmRepository(
            name="repo.mongodb.org",
            alias=f"mongodb{series.replace('.', '')}",
            key=MONGODB_KEYS[series],
            baseurl=f"https://repo.mongodb.org/yum/redhat/9/mongodb-org/{series}/$basearch/",
            manager=DNF,
            directory=release.repo_dir,
        ),
        unit="mongod",
    )


def _plan_leap(spec: Flavour, version: str | None, release: RpmRelease) -> InstallPlan:
    """
    Plan an engine on openSUSE Leap.

    Args:
        spec: The flavour.
        version: The version asked for; None for the distribution's own.
        release: The release.

    Returns:
        The plan.

    Raises:
        ValidationError: For a flavour the tables do not cover on Leap, or a version it does
            not ship.
    """
    shipped = LEAP[release.key.removeprefix("leap-")]
    versions = shipped.get(spec.name)
    if versions is None:
        raise _unsupported(spec, release, OsRelease(pretty_name=release.name))
    if spec.name == "postgresql":
        if version is None:
            # The newest major the release ships, named. The meta packages ask for any
            # "postgresql-server-implementation", and zypper then picks the major it likes:
            # Leap 15.6's meta package says 18 and installs 16.
            version = versions[-1]
        if version not in versions:
            raise _not_offered(
                spec,
                release,
                version,
                f"{release.name} ships PostgreSQL {', '.join(versions)}.",
            )
        return InstallPlan(
            flavour=spec.name,
            engine=spec.engine,
            version=version,
            source="distribution",
            package_sets=((f"postgresql{version}-server", f"postgresql{version}-contrib"),),
            family=ZYPPER,
            unit="postgresql",
        )
    only = versions[0]
    if version is not None and version != only:
        raise _not_offered(
            spec,
            release,
            version,
            f"{release.name} ships {spec.display_name} {only}: install that one, or leave "
            "the version out.",
        )
    return InstallPlan(
        flavour=spec.name,
        engine=spec.engine,
        version=only,
        source="distribution",
        package_sets=(("mariadb", "mariadb-client"),),
        family=ZYPPER,
        unit="mariadb",
    )


def plan_rpm_install(
    spec: Flavour, version: str | None, os_release: OsRelease, manager: str
) -> InstallPlan:
    """
    Turn a choice into what dnf or zypper installs for it.

    Args:
        spec: The flavour.
        version: The version asked for; None for the distribution's own.
        os_release: The distribution.
        manager: ``dnf`` or ``zypper``, from :func:`rpm_manager`.

    Returns:
        The plan.

    Raises:
        ValidationError: When the release is not one the tables cover, or the flavour or
            version is not one it can be given. Nothing has been touched.
    """
    release = rpm_release(os_release)
    if release is None or release.manager != manager:
        raise _unsupported(spec, release, os_release)
    asked = version.strip() if version else None
    if release.manager == DNF:
        return _plan_el(spec, asked, release)
    return _plan_leap(spec, asked, release)


def offered_rpm_versions(flavour: str, os_release: OsRelease) -> tuple[str, ...]:
    """
    List the versions a release can be given of a flavour.

    Args:
        flavour: The flavour.
        os_release: The distribution.

    Returns:
        The versions Noust accepts here, oldest first; empty when the flavour is not covered.
    """
    release = rpm_release(os_release)
    if release is None:
        return ()
    if release.manager == ZYPPER:
        shipped = LEAP[release.key.removeprefix("leap-")].get(flavour)
        return tuple(shipped) if shipped else ()
    if flavour == "mongodb":
        return tuple(sorted(MONGODB_KEYS, key=flavours.version_key))
    table = EL9.get(flavour)
    if table is None:
        return ()
    found = {table.default, *table.streams}
    if flavour == "postgresql":
        found |= set(PGDG_VERSIONS)
    if flavour == "mariadb":
        found |= set(MARIADB_UPSTREAM)
    return tuple(sorted(found, key=flavours.version_key))


def default_version(flavour: str, os_release: OsRelease) -> str | None:
    """
    Name the version that installs when none is asked for.

    Args:
        flavour: The flavour.
        os_release: The distribution.

    Returns:
        The version, or None when the release does not cover the flavour.
    """
    release = rpm_release(os_release)
    if release is None:
        return None
    if release.manager == ZYPPER:
        shipped = LEAP[release.key.removeprefix("leap-")].get(flavour)
        return shipped[-1] if shipped else None
    if flavour == "mongodb":
        return MONGODB_DEFAULT_SERIES
    table = EL9.get(flavour)
    return table.default if table else None


def install_rpm_repository(
    repository: RpmRepository,
    *,
    runner: CommandRunner,
    fs: FileSystem,
    host: HostPaths | None = None,
    log: Callable[[str], None] | None = None,
) -> list[Path]:
    """
    Add a repository of RPM packages, trusting only its pinned key.

    The key is verified and written by :func:`~noust.managers.database.flavours.export_pinned_key`
    (armoured, which is what a ``gpgkey`` reads); only then is the definition written, naming
    that file and no other, and, for zypper, the key imported into the RPM database it checks
    signatures against.

    Args:
        repository: The repository.
        runner: Where curl, gpg and rpm run.
        fs: Where the definition is written.
        host: Where the system files are; :data:`~noust.managers.database.flavours.HOST` by
            default. The definition still names the key by its real path.
        log: Where progress is reported.

    Returns:
        The files this call created that did not exist before, so a caller whose next step
        fails can take them back out (:func:`withdraw_rpm_repository`); empty under a
        rehearsal.

    Raises:
        DatabaseEngineError: When the key cannot be fetched or is not the pinned one, or the
            definition cannot be written.
    """
    say = log or (lambda _message: None)
    paths = host or flavours.HOST
    sources = paths.at(str(repository.sources))
    key_file = paths.at(str(repository.key_file))
    say(f"Adding the {repository.name} repository")
    created = [path for path in (sources, key_file) if not path.exists()]
    fs.make_dir(key_file.parent, mode=0o755)
    try:
        written = export_pinned_key(
            repository.name,
            repository.key,
            key_file,
            runner=runner,
            armor=True,
            manager=repository.manager,
        )
    except DatabaseEngineError:
        withdraw_rpm_repository(created, fs=fs)
        raise
    if not written:
        say(f"Would verify the key of {repository.name} ({repository.key.fingerprint})")
        return []
    if repository.manager == ZYPPER:
        imported = runner.run(["rpm", "--import", str(key_file)], timeout=60)
        if not imported.success:
            withdraw_rpm_repository(created, fs=fs)
            raise DatabaseEngineError(
                f"Failed to trust the signing key of {repository.name}",
                details="rpm could not import the verified key. Nothing was added to zypper.",
                output=imported.stderr.strip() or None,
            )
    try:
        fs.write_text(sources, repository.render(), mode=0o644)
    except OSError as exc:
        withdraw_rpm_repository(created, fs=fs)
        raise DatabaseEngineError(
            f"Failed to add the {repository.name} repository",
            details=f"{exc}. Write {sources} by hand with:\n{repository.render()}",
        ) from exc
    return created


def withdraw_rpm_repository(created: list[Path], *, fs: FileSystem) -> list[Path]:
    """
    Take back out the files :func:`install_rpm_repository` created.

    Called when the package manager cannot read the repository right after: a definition it
    cannot refresh breaks every later run on the server, not only this install.

    Args:
        created: What :func:`install_rpm_repository` returned.
        fs: Where the files are removed.

    Returns:
        The files that were there and are now gone.
    """
    return flavours.withdraw_repository(created, fs=fs)


#: The stem every repository of a flavour is named by (``noust-<stem><version>.repo``).
REPOSITORY_STEMS: dict[str, str] = {
    "postgresql": "pgdg",
    "mariadb": "mariadb",
    "mongodb": "mongodb",
}


def repository_files(flavour: str, *, host: HostPaths | None = None) -> list[Path]:
    """
    Find the repository definitions Noust added for a flavour.

    Args:
        flavour: The flavour.
        host: Where the system files are; the real machine by default.

    Returns:
        The ``.repo`` files that are Noust's own (they carry its marker) and belong to the
        flavour, in both package managers' directories.
    """
    stem = REPOSITORY_STEMS.get(flavour)
    if stem is None:
        return []
    paths = host or flavours.HOST
    found: list[Path] = []
    for directory in (_DNF_REPO_DIR, _ZYPPER_REPO_DIR):
        folder = paths.at(str(directory))
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob(f"noust-{stem}*.repo")):
            try:
                if "Generated by Noust" in path.read_text(encoding="utf-8", errors="replace"):
                    found.append(path)
            except OSError:
                continue
    return found


def key_file_of(repository_file: Path) -> Path:
    """
    Name the key file that belongs to a repository definition Noust wrote.

    Args:
        repository_file: ``.../noust-<alias>.repo``.

    Returns:
        ``/etc/pki/rpm-gpg/RPM-GPG-KEY-noust-<alias>``.
    """
    return _KEY_DIR / f"RPM-GPG-KEY-{repository_file.stem}"


def default_rpm_plan(engine: str, os_release: OsRelease) -> InstallPlan:
    """
    Decide what installing an engine with no flavour and no version asked for means.

    The install of 3.2 and before tried MariaDB then MySQL, and Redis then Valkey: the first
    of an engine's flavours the release can be given wins, as on apt.

    Args:
        engine: The engine (``postgresql``, ``mysql``, ``redis``, ``mongodb``).
        os_release: The distribution.

    Returns:
        The plan of the first flavour that has one.

    Raises:
        ValidationError: When none does; the first flavour's refusal, which says why.
    """
    refusal: ValidationError | None = None
    for name in _DEFAULT_FLAVOURS.get(engine, (engine,)):
        try:
            return flavours.plan_install(name, None, os_release)
        except ValidationError as exc:
            refusal = refusal or exc
    if refusal is not None:
        raise refusal
    raise ValidationError(f"Noust installs no {engine} on {os_release.pretty_name}", field="engine")


#: The flavours of an engine, in the order an install that names none tries them.
_DEFAULT_FLAVOURS: dict[str, tuple[str, ...]] = {
    "postgresql": ("postgresql",),
    "mysql": ("mariadb", "mysql"),
    "redis": ("redis", "valkey"),
    "mongodb": ("mongodb",),
}

#: The packages that make up each flavour on the RPM families, by name: what ``uninstall``
#: removes, and nothing a pattern could widen. ``(\d+)`` is a PostgreSQL major.
INSTALLED_PACKAGES: dict[str, tuple[str, ...]] = {
    "postgresql": (
        r"postgresql",
        r"postgresql-server",
        r"postgresql-contrib",
        r"postgresql-private-libs",
        r"postgresql\d+",
        r"postgresql\d+-server",
        r"postgresql\d+-contrib",
        r"postgresql\d+-libs",
    ),
    "mariadb": (
        r"mariadb",
        r"mariadb-server",
        r"mariadb-client",
        r"MariaDB-server",
        r"MariaDB-client",
        r"MariaDB-common",
    ),
    "mysql": (r"mysql", r"mysql-server", r"mysql-common"),
    "redis": (r"redis",),
    "valkey": (r"valkey",),
    "mongodb": (r"mongodb-org", r"mongodb-org-server", r"mongodb-org-database"),
}


def installed_packages(flavour: str, listing: str) -> list[str]:
    """
    Pick, out of ``rpm -qa``'s package names, the ones that are this flavour's.

    Args:
        flavour: The flavour.
        listing: ``rpm -qa --qf '%{NAME}\\n'``'s output.

    Returns:
        The names that match one of :data:`INSTALLED_PACKAGES` exactly, sorted, each once.
    """
    patterns = [re.compile(pattern) for pattern in INSTALLED_PACKAGES.get(flavour, ())]
    names = {line.strip() for line in listing.splitlines() if line.strip()}
    return sorted(
        name for name in names if any(pattern.fullmatch(name) is not None for pattern in patterns)
    )
