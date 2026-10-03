# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which database engines, in which versions, Noust can install on this server.

An engine manager (PostgreSQL, MySQL/MariaDB, Redis/Valkey, MongoDB) is one
thing; what apt installs for it is another. This module is the one answer to
the second question (rule 3): the flavours an operator chooses between (MySQL
and MariaDB, Redis and Valkey are separate choices), the versions each one is
offered in on this distribution, and the packages and repository a choice
becomes (:class:`InstallPlan`).

Three sources of packages, and nothing else:

- **The distribution's own repositories**, for the version the release ships.
  Asking for exactly that version uses them, without adding a repository.
- **The engine's upstream repository**, for every other supported version:
  PostgreSQL's (``apt.postgresql.org``, PGDG), MariaDB's (``deb.mariadb.org``)
  and MongoDB's (``repo.mongodb.org``, the only source of ``mongodb-org``).
- Nothing for a release an upstream repository does not publish for: the
  refusal comes before apt is touched, naming the releases it does publish for.

The tables are what each repository published when they were written
(October 2026), checked against the repositories themselves; a release added
upstream later is refused until it is added here, which is the safe direction.

Every repository's signing key is pinned here by fingerprint. The key is
downloaded, its fingerprint compared with the pinned one, and only then
written where apt trusts it: a key served by a compromised mirror or a
hijacked DNS answer is refused instead of trusted (:func:`install_repository`).
Only Debian and Ubuntu are covered; installing on an RPM system stays
unsupported (3.3 spec, 9.6), and is refused by the managers' ``install``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from noust.core.exceptions import DatabaseEngineError, DatabaseExistsError, ValidationError
from noust.core.fs import FileSystem
from noust.core.runner import CommandRunner
from noust.managers.server.host import HostPaths, OsRelease, read_os_release

#: Where the server's system files are. A test points it at a temporary root.
HOST = HostPaths()

#: Deadline for downloading a signing key.
KEY_DOWNLOAD_TIMEOUT = 300

#: Deadline for gpg reading or converting a key.
GPG_TIMEOUT = 60

#: Where a source of packages comes from.
Source = Literal["distribution", "upstream"]


class EngineInstalledError(DatabaseExistsError):
    """
    The engine, or a flavour that cannot live beside the one asked for, is installed.

    A subclass of :class:`DatabaseExistsError` so the API answers 409, the
    conflict it is.
    """


@dataclass(frozen=True)
class Flavour:
    """
    One choice of what to install.

    Attributes:
        name: The flavour's name, which is also what ``noust db install``
            takes (``mariadb``, ``valkey``).
        engine: The manager that runs it (``mysql`` for MariaDB, ``redis``
            for Valkey).
        display_name: How it is named to a person.
    """

    name: str
    engine: str
    display_name: str


#: Every flavour, in the order the console offers them.
FLAVOURS: dict[str, Flavour] = {
    flavour.name: flavour
    for flavour in (
        Flavour("postgresql", "postgresql", "PostgreSQL"),
        Flavour("mysql", "mysql", "MySQL"),
        Flavour("mariadb", "mysql", "MariaDB"),
        Flavour("redis", "redis", "Redis"),
        Flavour("valkey", "redis", "Valkey"),
        Flavour("mongodb", "mongodb", "MongoDB"),
    )
}

#: Names an operator may type for a flavour that is not its engine's own name.
FLAVOUR_ALIASES: dict[str, str] = {
    "mariadb": "mariadb",
    "maria": "mariadb",
    "valkey": "valkey",
    "valkey-server": "valkey",
}

#: A distribution release, as ``/etc/os-release`` names it: ``ID`` and
#: ``VERSION_CODENAME``.
Release = tuple[str, str]

#: How each release is named to a person, in error messages.
RELEASE_NAMES: dict[Release, str] = {
    ("debian", "bookworm"): "Debian 12 (bookworm)",
    ("debian", "trixie"): "Debian 13 (trixie)",
    ("ubuntu", "jammy"): "Ubuntu 22.04 (jammy)",
    ("ubuntu", "noble"): "Ubuntu 24.04 (noble)",
    ("ubuntu", "resolute"): "Ubuntu 26.04 (resolute)",
}

#: The version of each flavour a release ships in its own repositories, None
#: where it ships none (Debian packages MariaDB, not MySQL). MongoDB is in no
#: distribution. Read from the archives (Debian's madison, Launchpad).
DISTRIBUTION_VERSIONS: dict[Release, dict[str, str | None]] = {
    ("debian", "bookworm"): {
        "postgresql": "15",
        "mariadb": "10.11",
        "mysql": None,
        "redis": "7.0",
        # Only in bookworm-backports, which is not enabled by default.
        "valkey": None,
    },
    ("debian", "trixie"): {
        "postgresql": "17",
        "mariadb": "11.8",
        "mysql": None,
        "redis": "8.0",
        "valkey": "8.1",
    },
    ("ubuntu", "jammy"): {
        "postgresql": "14",
        "mariadb": "10.6",
        "mysql": "8.0",
        "redis": "6.0",
        "valkey": None,
    },
    ("ubuntu", "noble"): {
        "postgresql": "16",
        "mariadb": "10.11",
        "mysql": "8.0",
        "redis": "7.0",
        "valkey": "7.2",
    },
    ("ubuntu", "resolute"): {
        "postgresql": "18",
        "mariadb": "11.8",
        "mysql": "8.4",
        "redis": "8.0",
        "valkey": "9.0",
    },
}

#: PostgreSQL majors with upstream support, the ones PGDG builds.
PGDG_VERSIONS: tuple[str, ...] = ("14", "15", "16", "17", "18")

#: Releases PGDG publishes ``<codename>-pgdg`` for.
PGDG_RELEASES: frozenset[Release] = frozenset(
    {
        ("debian", "bookworm"),
        ("debian", "trixie"),
        ("ubuntu", "jammy"),
        ("ubuntu", "noble"),
        ("ubuntu", "resolute"),
    }
)

#: MariaDB's long-term releases and the releases ``deb.mariadb.org`` builds
#: each one for.
MARIADB_RELEASES: dict[str, frozenset[Release]] = {
    "10.11": frozenset({("debian", "bookworm"), ("ubuntu", "jammy"), ("ubuntu", "noble")}),
    "11.4": frozenset({("debian", "bookworm"), ("ubuntu", "jammy"), ("ubuntu", "noble")}),
    "11.8": frozenset(
        {
            ("debian", "bookworm"),
            ("debian", "trixie"),
            ("ubuntu", "jammy"),
            ("ubuntu", "noble"),
            ("ubuntu", "resolute"),
        }
    ),
}

#: MongoDB's series, and per release the path, suite and component of its
#: repository line. Only releases whose repository holds ``mongodb-org-server``
#: are listed: MongoDB publishes the tools alone for some (trixie, resolute).
MONGODB_RELEASES: dict[str, dict[Release, tuple[str, str, str]]] = {
    "7.0": {
        ("ubuntu", "jammy"): ("ubuntu", "jammy", "multiverse"),
        ("debian", "bookworm"): ("debian", "bookworm", "main"),
    },
    "8.0": {
        ("ubuntu", "jammy"): ("ubuntu", "jammy", "multiverse"),
        ("ubuntu", "noble"): ("ubuntu", "noble", "multiverse"),
        ("debian", "bookworm"): ("debian", "bookworm", "main"),
    },
}

#: The MongoDB series installed when none is asked for: supported until 2029.
MONGODB_DEFAULT_SERIES = "8.0"


@dataclass(frozen=True)
class SigningKey:
    """
    A repository's signing key, pinned.

    Attributes:
        url: Where the armoured key is published.
        fingerprint: The primary key's full fingerprint, upper case.
    """

    url: str
    fingerprint: str


#: The PostgreSQL Debian Repository key (ACCC4CF8).
PGDG_KEY = SigningKey(
    url="https://www.postgresql.org/media/keys/ACCC4CF8.asc",
    fingerprint="B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8",
)

#: The MariaDB Signing Key, which signs every ``deb.mariadb.org`` release.
MARIADB_KEY = SigningKey(
    url="https://mariadb.org/mariadb_release_signing_key.pgp",
    fingerprint="177F4010FE56CA3336300305F1656F24C74CD1D8",
)

#: MongoDB signs each series with a key of its own.
MONGODB_KEYS: dict[str, SigningKey] = {
    "7.0": SigningKey(
        url="https://www.mongodb.org/static/pgp/server-7.0.asc",
        fingerprint="E58830201F7DD82CD808AA84160D26BB1785BA38",
    ),
    "8.0": SigningKey(
        url="https://www.mongodb.org/static/pgp/server-8.0.asc",
        fingerprint="4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05",
    ),
}


@dataclass(frozen=True)
class Repository:
    """
    An upstream apt repository Noust adds.

    Attributes:
        name: What it is called in messages (``apt.postgresql.org``).
        key: Its pinned signing key.
        keyring: Where the verified key is written, as apt reads it.
        sources: The apt source file Noust owns for it.
        url: The repository's base URL; a source line already naming it,
            anywhere in apt's sources, means it is configured already.
        suite: The suite and components that follow the URL on the line.
        architectures: The ``arch=`` option, when the repository needs one.
        family: The URL prefix every release and version of this vendor's
            repository shares (``https://repo.mongodb.org/apt/``); a source
            naming it for another suite or version is a conflict, not a
            match. The URL itself when empty.
    """

    name: str
    key: SigningKey
    keyring: Path
    sources: Path
    url: str
    suite: str
    architectures: str = ""
    family: str = ""

    def line(self) -> str:
        """
        Render the apt source line.

        Returns:
            One ``deb`` line, signed by this repository's keyring alone.
        """
        options = f"signed-by={self.keyring}"
        if self.architectures:
            options = f"arch={self.architectures} {options}"
        return f"deb [{options}] {self.url} {self.suite}\n"


@dataclass(frozen=True)
class InstallPlan:
    """
    What installing one flavour in one version becomes.

    Attributes:
        flavour: The flavour.
        engine: The manager that runs it.
        version: The version that installs, when it is known; None for
            "whatever the distribution's default is".
        source: ``distribution`` or ``upstream``.
        package_sets: The package sets to try, in order. One for a choice;
            the install of 3.2 and before, kept for a request that names
            none, tries MariaDB then MySQL, and Redis then Valkey.
        repository: The repository to add first, for ``upstream``.
    """

    flavour: str
    engine: str
    version: str | None
    source: Source
    package_sets: tuple[tuple[str, ...], ...]
    repository: Repository | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the plan as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "flavour": self.flavour,
            "engine": self.engine,
            "version": self.version,
            "source": self.source,
            "packages": [list(packages) for packages in self.package_sets],
            "repository": self.repository.name if self.repository else None,
        }


def distribution(host: HostPaths | None = None) -> OsRelease:
    """
    Read which distribution and release this server runs.

    Args:
        host: Where the system files are; :data:`HOST` by default.

    Returns:
        What ``/etc/os-release`` says.
    """
    return read_os_release(host or HOST)


def release_of(os_release: OsRelease) -> Release:
    """
    Reduce an os-release to the pair the tables are keyed by.

    Args:
        os_release: The distribution's identity.

    Returns:
        ``(ID, VERSION_CODENAME)``, lower case.
    """
    return (os_release.id.lower(), os_release.codename.lower())


def _names(releases: frozenset[Release] | Mapping[Release, Any]) -> str:
    """
    Name releases in a sentence, in a stable order.

    Args:
        releases: The releases.

    Returns:
        ``Debian 12 (bookworm), Ubuntu 24.04 (noble)``.
    """
    return ", ".join(RELEASE_NAMES.get(release, " ".join(release)) for release in sorted(releases))


def resolve_flavour(
    typed: str, engine: str, *, flavour: str | None = None, version: str | None = None
) -> str | None:
    """
    Decide which flavour an install asks for.

    Args:
        typed: The engine name as the operator typed it (``mariadb``, ``pg``).
        engine: The canonical engine it resolved to.
        flavour: A flavour named explicitly, as the API's body does.
        version: A version asked for.

    Returns:
        The flavour, or None for the install of 3.2 and before: no flavour
        named, typed as the engine's own name and no version.

    Raises:
        ValidationError: When the flavour is unknown or belongs to another
            engine.
    """
    if flavour:
        chosen = FLAVOURS.get(flavour.strip().lower())
        if chosen is None or chosen.engine != engine:
            offered = [name for name, spec in FLAVOURS.items() if spec.engine == engine]
            raise ValidationError(
                f"{flavour!r} is not something Noust installs for {engine}",
                details=f"Choose one of: {', '.join(offered)}.",
                field="flavour",
            )
        return chosen.name
    alias = FLAVOUR_ALIASES.get(typed.strip().lower())
    if alias is not None:
        return alias
    return engine if version else None


def _refuse(message: str, details: str) -> ValidationError:
    """
    Build the refusal of a version this server cannot have.

    Args:
        message: What is refused.
        details: Where it can be had instead.

    Returns:
        The error, about the ``version`` field.
    """
    return ValidationError(message, details=details, field="version")


def plan_install(flavour: str, version: str | None, os_release: OsRelease) -> InstallPlan:
    """
    Turn a choice into the packages and repository that install it.

    Args:
        flavour: The flavour (a key of :data:`FLAVOURS`).
        version: The version asked for; None for the distribution's own (or,
            for MongoDB, the default series).
        os_release: The distribution this server runs.

    Returns:
        The plan.

    Raises:
        ValidationError: When the flavour is unknown, or the version is not
            one Noust offers or not one published for this release. Nothing
            has been touched.
    """
    spec = FLAVOURS.get(flavour)
    if spec is None:
        raise ValidationError(
            f"Noust does not install {flavour!r}",
            details=f"Choose one of: {', '.join(FLAVOURS)}.",
            field="flavour",
        )
    release = release_of(os_release)
    shipped = DISTRIBUTION_VERSIONS.get(release)
    distro_version = shipped.get(flavour) if shipped else None
    asked = version.strip() if version else None
    builder = _PLANNERS[flavour]
    return builder(spec, asked, release, os_release, shipped is not None, distro_version)


def _distribution_plan(
    spec: Flavour, version: str | None, packages: tuple[str, ...]
) -> InstallPlan:
    """
    Plan an install from the distribution's own repositories.

    Args:
        spec: The flavour.
        version: The version the release ships, when known.
        packages: The packages.

    Returns:
        The plan.
    """
    return InstallPlan(
        flavour=spec.name,
        engine=spec.engine,
        version=version,
        source="distribution",
        package_sets=(packages,),
    )


def _plan_postgresql(
    spec: Flavour,
    version: str | None,
    release: Release,
    os_release: OsRelease,
    known: bool,
    distro_version: str | None,
) -> InstallPlan:
    """
    Plan PostgreSQL: the distribution's, or a PGDG major.

    Args:
        spec: The flavour.
        version: The major asked for.
        release: The release.
        os_release: Its identity, for messages.
        known: Whether the release is in :data:`DISTRIBUTION_VERSIONS`.
        distro_version: The major the release ships.

    Returns:
        The plan.

    Raises:
        ValidationError: For a major PGDG does not build or a release it
            does not publish for.
    """
    if version is None:
        # The meta packages: exactly what 'noust db install postgresql' always did.
        return _distribution_plan(spec, distro_version, ("postgresql", "postgresql-contrib"))
    if version not in PGDG_VERSIONS:
        raise _refuse(
            f"PostgreSQL {version} is not a version Noust installs",
            f"Choose one of the supported majors: {', '.join(PGDG_VERSIONS)}.",
        )
    if version == distro_version:
        return _distribution_plan(spec, version, (f"postgresql-{version}",))
    if release not in PGDG_RELEASES:
        raise _refuse(
            f"PostgreSQL's repository publishes no packages for {os_release.pretty_name}",
            f"apt.postgresql.org publishes for {_names(PGDG_RELEASES)}. "
            + (
                f"This release ships PostgreSQL {distro_version}: install that one."
                if distro_version
                else "Install the distribution's PostgreSQL without --version."
            ),
        )
    codename = release[1]
    repository = Repository(
        name="apt.postgresql.org",
        key=PGDG_KEY,
        keyring=Path("/usr/share/keyrings/noust-pgdg.gpg"),
        sources=Path("/etc/apt/sources.list.d/noust-pgdg.list"),
        url="https://apt.postgresql.org/pub/repos/apt",
        suite=f"{codename}-pgdg main",
    )
    # PostgreSQL 10 and later ship the contrib modules in the server package.
    return InstallPlan(
        flavour=spec.name,
        engine=spec.engine,
        version=version,
        source="upstream",
        package_sets=((f"postgresql-{version}",),),
        repository=repository,
    )


def _plan_mariadb(
    spec: Flavour,
    version: str | None,
    release: Release,
    os_release: OsRelease,
    known: bool,
    distro_version: str | None,
) -> InstallPlan:
    """
    Plan MariaDB: the distribution's, or one of MariaDB's long-term releases.

    Args:
        spec: The flavour.
        version: The release asked for.
        release: The release of the distribution.
        os_release: Its identity, for messages.
        known: Whether the release is in :data:`DISTRIBUTION_VERSIONS`.
        distro_version: The MariaDB the distribution ships.

    Returns:
        The plan.

    Raises:
        ValidationError: For a release that is not long-term, or one MariaDB
            does not build for this distribution.
    """
    if version is None or version == distro_version:
        return _distribution_plan(spec, distro_version, ("mariadb-server",))
    if version not in MARIADB_RELEASES:
        raise _refuse(
            f"MariaDB {version} is not a version Noust installs",
            f"Choose one of MariaDB's long-term releases: {', '.join(MARIADB_RELEASES)}"
            + (f", or {distro_version}, the distribution's." if distro_version else "."),
        )
    builds = MARIADB_RELEASES[version]
    if release not in builds:
        raise _refuse(
            f"MariaDB {version} publishes no packages for {os_release.pretty_name}",
            f"deb.mariadb.org builds MariaDB {version} for {_names(builds)}."
            + (
                f" This release ships MariaDB {distro_version}: install that one."
                if distro_version
                else ""
            ),
        )
    distro, codename = release
    repository = Repository(
        name="deb.mariadb.org",
        key=MARIADB_KEY,
        keyring=Path("/usr/share/keyrings/noust-mariadb.gpg"),
        sources=Path("/etc/apt/sources.list.d/noust-mariadb.list"),
        url=f"https://deb.mariadb.org/{version}/{distro}",
        suite=f"{codename} main",
        family="https://deb.mariadb.org/",
    )
    return InstallPlan(
        flavour=spec.name,
        engine=spec.engine,
        version=version,
        source="upstream",
        package_sets=(("mariadb-server",),),
        repository=repository,
    )


def _plan_distribution_only(
    spec: Flavour,
    version: str | None,
    release: Release,
    os_release: OsRelease,
    known: bool,
    distro_version: str | None,
) -> InstallPlan:
    """
    Plan a flavour only the distribution provides: MySQL, Redis, Valkey.

    Args:
        spec: The flavour.
        version: The version asked for; only the distribution's is offered.
        release: The release.
        os_release: Its identity, for messages.
        known: Whether the release is in :data:`DISTRIBUTION_VERSIONS`.
        distro_version: The version the release ships, None for none.

    Returns:
        The plan.

    Raises:
        ValidationError: When the release ships no such package, or the
            version asked for is not the one it ships.
    """
    packages = _DISTRIBUTION_PACKAGES[spec.name]
    if known and distro_version is None:
        raise _refuse(
            f"{os_release.pretty_name} does not ship {spec.display_name}",
            _NOT_SHIPPED[spec.name],
        )
    if version is not None and version != distro_version:
        offered = (
            f"This release ships {spec.display_name} {distro_version}: install that one, "
            "or leave the version out."
            if distro_version
            else f"Noust does not know which {spec.display_name} this release ships: leave "
            "the version out to install the distribution's."
        )
        raise _refuse(
            f"{spec.display_name} {version} is not available on {os_release.pretty_name}",
            offered,
        )
    return _distribution_plan(spec, distro_version, packages)


def _plan_mongodb(
    spec: Flavour,
    version: str | None,
    release: Release,
    os_release: OsRelease,
    known: bool,
    distro_version: str | None,
) -> InstallPlan:
    """
    Plan MongoDB from its own repository, the only one with ``mongodb-org``.

    Args:
        spec: The flavour.
        version: The series asked for; :data:`MONGODB_DEFAULT_SERIES` when None.
        release: The release.
        os_release: Its identity, for messages.
        known: Unused; MongoDB is in no distribution.
        distro_version: Unused, for the same reason.

    Returns:
        The plan.

    Raises:
        ValidationError: For a series Noust does not install, or one
            MongoDB does not publish for this release.
    """
    series = version or MONGODB_DEFAULT_SERIES
    if series not in MONGODB_RELEASES:
        raise _refuse(
            f"MongoDB {series} is not a version Noust installs",
            f"Choose one of: {', '.join(MONGODB_RELEASES)}.",
        )
    published = MONGODB_RELEASES[series]
    line = published.get(release)
    if line is None:
        raise _refuse(
            f"MongoDB {series} publishes no packages for {os_release.pretty_name}",
            f"Noust installs MongoDB {series} on {_names(published)}. Elsewhere, install it by "
            "hand following mongodb.com/docs/manual/installation, then manage it with "
            "'noust db status mongodb'.",
        )
    path, suite, component = line
    repository = Repository(
        name="repo.mongodb.org",
        key=MONGODB_KEYS[series],
        keyring=Path(f"/usr/share/keyrings/mongodb-server-{series}.gpg"),
        sources=Path(f"/etc/apt/sources.list.d/mongodb-org-{series}.list"),
        url=f"https://repo.mongodb.org/apt/{path}",
        suite=f"{suite}/mongodb-org/{series} {component}",
        architectures="amd64,arm64",
        family="https://repo.mongodb.org/apt/",
    )
    return InstallPlan(
        flavour=spec.name,
        engine=spec.engine,
        version=series,
        source="upstream",
        package_sets=(("mongodb-org",),),
        repository=repository,
    )


#: The package of each flavour that only the distribution provides.
_DISTRIBUTION_PACKAGES: dict[str, tuple[str, ...]] = {
    "mysql": ("mysql-server",),
    "redis": ("redis-server",),
    "valkey": ("valkey-server",),
}

#: What to do instead, for a flavour a release does not ship.
_NOT_SHIPPED: dict[str, str] = {
    "mysql": (
        "Debian packages MariaDB instead: run 'noust db install mariadb'. To run MySQL "
        "itself, add Oracle's MySQL APT repository by hand and install mysql-server; "
        "Noust manages it from there."
    ),
    "redis": "Install Valkey instead: run 'noust db install valkey'.",
    "valkey": (
        "Valkey is packaged from Debian 13 and Ubuntu 24.04 on. Install Redis instead: "
        "run 'noust db install redis'."
    ),
}

#: The planner of each flavour.
_PLANNERS: dict[str, Callable[..., InstallPlan]] = {
    "postgresql": _plan_postgresql,
    "mysql": _plan_distribution_only,
    "mariadb": _plan_mariadb,
    "redis": _plan_distribution_only,
    "valkey": _plan_distribution_only,
    "mongodb": _plan_mongodb,
}


# ------------------------------------------------------------------ catalog


@dataclass(frozen=True)
class VersionChoice:
    """
    One version of a flavour this server can be given.

    Attributes:
        version: The version (a PostgreSQL major, a MariaDB release).
        source: ``distribution`` or ``upstream``.
        default: Whether it is what installs when no version is asked for.
    """

    version: str
    source: Source
    default: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {"version": self.version, "source": self.source, "default": self.default}


@dataclass(frozen=True)
class FlavourChoice:
    """
    A flavour as the install dialog offers it on this server.

    Attributes:
        flavour: The flavour.
        engine: The manager that runs it.
        display_name: How it is named to a person.
        installed: Whether this flavour is the one installed.
        installable: Whether it can be installed now.
        blocked: Why not, as a stable code: ``installed`` (it is),
            ``conflict`` (its other flavour is), ``not_available`` (nothing
            publishes it for this release), ``no_apt`` (not a Debian or
            Ubuntu system). None when it can.
        reason: The same, as one English sentence with what to do.
        versions: The versions offered, oldest first.
    """

    flavour: str
    engine: str
    display_name: str
    installed: bool
    installable: bool
    blocked: str | None = None
    reason: str | None = None
    versions: tuple[VersionChoice, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "flavour": self.flavour,
            "engine": self.engine,
            "display_name": self.display_name,
            "installed": self.installed,
            "installable": self.installable,
            "blocked": self.blocked,
            "reason": self.reason,
            "versions": [choice.to_dict() for choice in self.versions],
        }


def _version_key(version: str) -> tuple[int, ...]:
    """
    Order versions numerically.

    Args:
        version: ``10.11`` or ``8``.

    Returns:
        The numbers.
    """
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def offered_versions(flavour: str, os_release: OsRelease) -> tuple[VersionChoice, ...]:
    """
    List the versions of a flavour this release can be given.

    Args:
        flavour: The flavour.
        os_release: The distribution.

    Returns:
        Every version :func:`plan_install` accepts here, oldest first, the
        default marked. Empty when none is.
    """
    release = release_of(os_release)
    shipped = DISTRIBUTION_VERSIONS.get(release, {})
    distro_version = shipped.get(flavour)
    candidates: set[str] = set()
    if flavour == "postgresql":
        candidates = set(PGDG_VERSIONS)
    elif flavour == "mariadb":
        candidates = set(MARIADB_RELEASES)
    elif flavour == "mongodb":
        candidates = set(MONGODB_RELEASES)
    if distro_version:
        candidates.add(distro_version)
    choices: list[VersionChoice] = []
    default: str | None = MONGODB_DEFAULT_SERIES if flavour == "mongodb" else distro_version
    for version in sorted(candidates, key=_version_key):
        try:
            plan = plan_install(flavour, version, os_release)
        except ValidationError:
            continue
        choices.append(VersionChoice(version, plan.source, default=version == default))
    return tuple(choices)


def catalog(
    os_release: OsRelease,
    installed: Mapping[str, str | None],
    *,
    apt: bool,
) -> list[FlavourChoice]:
    """
    Describe what the install dialog can offer on this server.

    Args:
        os_release: The distribution.
        installed: Per engine, the flavour installed, or None.
        apt: Whether apt is present; without it nothing is installable.

    Returns:
        One entry per flavour, in :data:`FLAVOURS` order.
    """
    choices: list[FlavourChoice] = []
    for spec in FLAVOURS.values():
        present = installed.get(spec.engine)
        versions = offered_versions(spec.name, os_release) if apt else ()
        blocked: str | None = None
        reason: str | None = None
        if present == spec.name:
            blocked, reason = "installed", f"{spec.display_name} is installed."
        elif present is not None:
            other = FLAVOURS[present].display_name if present in FLAVOURS else present
            blocked, reason = "conflict", conflict_reason(other, spec.display_name)
        elif not apt:
            blocked = "no_apt"
            reason = (
                f"Noust installs {spec.display_name} with apt, which this system does not "
                "have. Install it with the distribution's package manager: Noust manages it "
                "from there."
            )
        elif not _installable_here(spec.name, os_release):
            blocked = "not_available"
            reason = _unavailable_reason(spec.name, os_release)
        choices.append(
            FlavourChoice(
                flavour=spec.name,
                engine=spec.engine,
                display_name=spec.display_name,
                installed=present == spec.name,
                installable=blocked is None,
                blocked=blocked,
                reason=reason,
                versions=versions,
            )
        )
    return choices


def _installable_here(flavour: str, os_release: OsRelease) -> bool:
    """
    Tell whether a flavour can be installed at all on this release.

    Args:
        flavour: The flavour.
        os_release: The distribution.

    Returns:
        True when :func:`plan_install` accepts it without a version.
    """
    try:
        plan_install(flavour, None, os_release)
    except ValidationError:
        return False
    return True


def _unavailable_reason(flavour: str, os_release: OsRelease) -> str:
    """
    Say why a flavour cannot be installed on this release.

    Args:
        flavour: The flavour.
        os_release: The distribution.

    Returns:
        The refusal's sentence and what to do.
    """
    try:
        plan_install(flavour, None, os_release)
    except ValidationError as exc:
        return f"{exc.message}. {exc.details}".strip()
    return ""


def conflict_reason(installed: str, requested: str) -> str:
    """
    Say why one flavour of an engine cannot be installed beside the other.

    Args:
        installed: The installed flavour's name for a person.
        requested: The requested one's.

    Returns:
        The sentence, with what to do.
    """
    shared = (
        "port 3306, their packages and /var/lib/mysql"
        if {installed, requested} <= {"MySQL", "MariaDB"}
        else "port 6379 and their packages"
    )
    return (
        f"{installed} is installed, and {requested} cannot run beside it: they share "
        f"{shared}. Remove {installed} first if you mean to replace it."
    )


# --------------------------------------------------------------- repository


@dataclass(frozen=True)
class SourceEntry:
    """
    One apt source entry, from a one-line ``.list`` file or a deb822 stanza.

    Attributes:
        path: The file it is in.
        text: How it reads there, for messages.
        uri: The repository URL.
        suite: The suite (``noble-pgdg``, ``jammy/mongodb-org/8.0``).
        components: The components after the suite.
    """

    path: Path
    text: str
    uri: str
    suite: str
    components: tuple[str, ...]


def _normalise_url(url: str) -> str:
    """
    Reduce a repository URL to what identifies it.

    Args:
        url: ``https://repo.mongodb.org/apt/ubuntu/``.

    Returns:
        ``repo.mongodb.org/apt/ubuntu``: no scheme, no trailing slash, lower case.
    """
    bare = url.strip().split("://", 1)[-1]
    return bare.rstrip("/").lower()


def _one_line_entries(path: Path, text: str) -> list[SourceEntry]:
    """
    Read the ``deb`` lines of a one-line-style source file.

    Args:
        path: The file.
        text: Its contents.

    Returns:
        One entry per ``deb`` or ``deb-src`` line that names a URL and a suite.
    """
    entries: list[SourceEntry] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        kind, _, rest = stripped.partition(" ")
        if kind not in ("deb", "deb-src"):
            continue
        rest = rest.strip()
        if rest.startswith("["):
            # The options may hold spaces (arch=amd64 signed-by=...).
            _, _, rest = rest.partition("]")
        words = rest.split()
        if len(words) >= 2:
            entries.append(SourceEntry(path, stripped, words[0], words[1], tuple(words[2:])))
    return entries


def _deb822_entries(path: Path, text: str) -> list[SourceEntry]:
    """
    Read the stanzas of a deb822 ``.sources`` file.

    Args:
        path: The file.
        text: Its contents.

    Returns:
        One entry per URI and suite of every enabled stanza.
    """
    entries: list[SourceEntry] = []
    for stanza in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in stanza.splitlines():
            if line.lstrip().startswith("#") or ":" not in line or line[:1].isspace():
                continue
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
        if fields.get("enabled", "yes").lower() == "no":
            continue
        components = tuple(fields.get("components", "").split())
        summary = "; ".join(f"{key}: {value}" for key, value in fields.items())
        for uri in fields.get("uris", "").split():
            for suite in fields.get("suites", "").split():
                entries.append(SourceEntry(path, summary, uri, suite, components))
    return entries


def _source_entries(host: HostPaths, skip: Path) -> list[SourceEntry]:
    """
    Read every apt source entry on the server.

    Args:
        host: Where the system files are.
        skip: A file to leave out (Noust's own for the repository at hand).

    Returns:
        Every entry, in the order apt reads the files.
    """
    candidates = [host.at("/etc/apt/sources.list")]
    directory = host.at("/etc/apt/sources.list.d")
    if directory.is_dir():
        candidates += sorted(
            path for path in directory.iterdir() if path.suffix in (".list", ".sources")
        )
    entries: list[SourceEntry] = []
    for path in candidates:
        if path == skip:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        reader = _deb822_entries if path.suffix == ".sources" else _one_line_entries
        entries += reader(path, text)
    return entries


def _configured_elsewhere(repository: Repository, host: HostPaths) -> Path | None:
    """
    Find an apt source that already provides a repository, other than Noust's.

    Two source entries for one URL with different ``signed-by`` keyrings
    stop apt altogether ("Conflicting values set for option Signed-By"), so
    an operator's own entry for PGDG is used as it is, not doubled. It must
    name the same URL, suite and components, though: a leftover
    ``mongodb-org-7.0.list`` shares MongoDB's URL with an 8.0 plan, and
    taking it as "configured" would install 7.0 where 8.0 was asked for.

    Args:
        repository: The repository.
        host: Where the system files are.

    Returns:
        The file that provides it, or None when none does.

    Raises:
        DatabaseEngineError: When a source names the same vendor's repository
            for another suite or version: apt would mix both, and which one
            installs would be apt's choice, not the operator's.
    """
    url = _normalise_url(repository.url)
    family = _normalise_url(repository.family or repository.url)
    suite, *components = repository.suite.split()
    found: Path | None = None
    for entry in _source_entries(host, host.at(str(repository.sources))):
        uri = _normalise_url(entry.uri)
        if uri != family and not uri.startswith(family + "/"):
            continue
        if uri == url and entry.suite == suite and set(components) <= set(entry.components):
            found = found or entry.path
            continue
        raise DatabaseEngineError(
            f"An apt source already names {repository.name} for another release or version",
            details=(
                f"{entry.path} has: {entry.text}\n"
                f"Noust would add: {repository.line().strip()}\n"
                "With both, apt picks which one installs, not you. Install the version that "
                f"source provides, or remove {entry.path} (and the keyring its signed-by "
                "names), run 'apt-get update', and install again. Nothing was changed."
            ),
        )
    return found


def primary_fingerprints(colons: str) -> list[str]:
    """
    Read the primary keys' fingerprints from ``gpg --with-colons`` output.

    A secret primary key (``sec``) counts as a primary key too, so a file
    carrying one beside the pinned public key is never "exactly one key".

    Args:
        colons: The output.

    Returns:
        One fingerprint per ``pub`` or ``sec`` record, upper case, in order.
    """
    found: list[str] = []
    expecting = False
    for line in colons.splitlines():
        fields = line.split(":")
        if fields[0] in ("pub", "sec"):
            expecting = True
        elif fields[0] == "fpr" and expecting and len(fields) > 9:
            found.append(fields[9].upper())
            expecting = False
    return found


def holds_secret_keys(colons: str) -> bool:
    """
    Tell whether ``gpg --with-colons`` output lists any secret key material.

    Args:
        colons: The output.

    Returns:
        True when a ``sec`` or ``ssb`` record is present.
    """
    return any(line.split(":", 1)[0] in ("sec", "ssb") for line in colons.splitlines())


def _refuse_key(repository: Repository, found: str, output: str | None) -> DatabaseEngineError:
    """
    Build the refusal of a served key that is not exactly the pinned one.

    Args:
        repository: The repository.
        found: What was found instead, for a person.
        output: gpg's own words.

    Returns:
        The error.
    """
    return DatabaseEngineError(
        f"The signing key served for {repository.name} is not the one Noust trusts",
        details=(
            f"Expected exactly one public key with fingerprint {repository.key.fingerprint}, "
            f"got {found}. Nothing was added to apt. "
            "Check that the server reaches the real repository (DNS, proxy)."
        ),
        output=output,
    )


def install_repository(
    repository: Repository,
    *,
    runner: CommandRunner,
    fs: FileSystem,
    host: HostPaths | None = None,
    log: Callable[[str], None] | None = None,
) -> list[Path]:
    """
    Add an upstream repository, trusting only its pinned key.

    The key is downloaded into a private temporary directory, read with a
    throwaway gpg home, and refused unless it is exactly one public primary
    key with the pinned fingerprint and no secret material. Even then the
    download is never what apt gets: it is imported into the throwaway home
    and only the pinned key is exported from there, minimal, as the
    keyring. Dearmouring the file would hand apt everything it held, and a
    second key in it would be trusted to sign packages too. Only then is
    the source line written.

    Args:
        repository: The repository.
        runner: Where curl and gpg run.
        fs: Where the source file is written.
        host: Where the system files are; :data:`HOST` by default.
        log: Where progress is reported.

    Returns:
        The files this call created that did not exist before (the source
        and the keyring), so a caller whose next step fails can take them
        back out (:func:`withdraw_repository`); empty when an existing
        source was used, and under a rehearsal.

    Raises:
        DatabaseEngineError: When the key cannot be fetched or is not the
            pinned one, a source names the repository for another version,
            or the source cannot be written.
    """
    paths = host or HOST
    say = log or (lambda _message: None)
    elsewhere = _configured_elsewhere(repository, paths)
    if elsewhere is not None:
        say(f"{repository.name} is already configured in {elsewhere}; using it as it is")
        return []
    say(f"Adding the {repository.name} repository")
    keyring = paths.at(str(repository.keyring))
    sources = paths.at(str(repository.sources))
    created = [path for path in (sources, keyring) if not path.exists()]
    pinned = repository.key.fingerprint
    with tempfile.TemporaryDirectory(prefix="noust-apt-key-") as workdir:
        armoured = Path(workdir) / "key.asc"
        gpg = ["gpg", "--homedir", str(Path(workdir) / "gnupg"), "--batch"]
        result = runner.capture_to_file(
            ["curl", "-fsSL", "--proto", "=https", repository.key.url],
            armoured,
            timeout=KEY_DOWNLOAD_TIMEOUT,
        )
        if not result.success:
            raise DatabaseEngineError(
                f"Failed to download the signing key of {repository.name}",
                details=f"Check outbound HTTPS access to {repository.key.url}.",
                output=result.stderr.strip() or None,
            )
        if not armoured.exists():
            # Only a rehearsal reports a download it did not make; there is
            # nothing to check, and every later step is a rehearsal too.
            say(f"Would verify the key of {repository.name} ({pinned})")
            return []
        listed = runner.run(
            [*gpg, "--show-keys", "--with-colons", str(armoured)], timeout=GPG_TIMEOUT
        )
        said = (listed.stderr or listed.stdout).strip() or None
        if not listed.success:
            raise _refuse_key(repository, "a file gpg could not read", said)
        if holds_secret_keys(listed.stdout):
            raise _refuse_key(repository, "a file that holds secret key material", said)
        fingerprints = primary_fingerprints(listed.stdout)
        if fingerprints != [pinned]:
            raise _refuse_key(repository, ", ".join(fingerprints) or "none", said)
        imported = runner.run([*gpg, "--import", str(armoured)], timeout=GPG_TIMEOUT)
        if not imported.success:
            raise DatabaseEngineError(
                f"Failed to read the signing key of {repository.name}",
                details="gpg could not import the downloaded key. Nothing was added to apt.",
                output=imported.stderr.strip() or None,
            )
        exported = runner.run(
            [
                *gpg,
                "--yes",
                "--export",
                "--export-options",
                "export-minimal",
                "-o",
                str(keyring),
                pinned,
            ],
            timeout=GPG_TIMEOUT,
        )
        if not exported.success:
            withdraw_repository([path for path in created if path == keyring], fs=fs)
            raise DatabaseEngineError(
                f"Failed to install the signing key of {repository.name}",
                details=f"Could not write {keyring}. Nothing was added to apt.",
                output=exported.stderr.strip() or None,
            )
    try:
        fs.write_text(sources, repository.line(), mode=0o644)
    except OSError as exc:
        raise DatabaseEngineError(
            f"Failed to add the {repository.name} apt source",
            details=f"{exc}. Write {repository.sources} by hand with: {repository.line().strip()}",
        ) from exc
    return created


def withdraw_repository(created: list[Path], *, fs: FileSystem) -> list[Path]:
    """
    Take back out the files :func:`install_repository` created.

    Called when ``apt-get update`` fails right after: a source apt cannot
    read breaks every later apt run on the server, not only this install.

    Args:
        created: What :func:`install_repository` returned.
        fs: Where the files are removed.

    Returns:
        The files that were there and are now gone.
    """
    removed: list[Path] = []
    for path in created:
        if path.exists() or path.is_symlink():
            fs.remove(path)
            removed.append(path)
    return removed


def refuse_other_flavour(installed: str | None, chosen: str | None) -> None:
    """
    Refuse a flavour while its engine's other flavour is installed.

    MySQL and MariaDB, Redis and Valkey share their port and their packages
    (and the first two ``/var/lib/mysql``): installing one replaces the other.

    Args:
        installed: The flavour installed, or None.
        chosen: The flavour asked for, or None for "whichever installs".

    Raises:
        EngineInstalledError: When both are named and differ.
    """
    if installed is None or chosen is None or chosen == installed:
        return
    have = FLAVOURS[installed].display_name if installed in FLAVOURS else installed
    want = FLAVOURS[chosen].display_name if chosen in FLAVOURS else chosen
    raise EngineInstalledError(
        f"{have} is installed, so {want} cannot be installed",
        details=conflict_reason(have, want),
    )
