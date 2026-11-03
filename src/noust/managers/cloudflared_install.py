# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Installing ``cloudflared``, the connector a Cloudflare Tunnel runs through, from Cloudflare's own repository.

A tunnel needs the connector on the machine, and it was the one program Noust did not install for the
operator: the page said where Cloudflare keeps its packages and left the steps to a person. Noust does
them the way it does for the engines of the databases: the repository is added with the signing key
**pinned** (the key is downloaded into a private directory, read with a throwaway gpg home and refused
unless it is exactly one public primary key with Cloudflare's fingerprint; only then is the source line
or the repository file written, naming that key alone), the package manager's lists are refreshed (a
repository it cannot read is taken back out, since one broken source breaks every later run), and the
package is installed through the platform's own backend. The code that adds a repository is the
engines', not a second copy (rule 3): :func:`~noust.managers.database.flavours.install_repository` for
apt, :func:`~noust.managers.database.rpm_flavours.install_rpm_repository` for dnf and zypper.

A repository the operator already configured (Cloudflare's own instructions put it in
``cloudflared.list``) is used as it is. On a machine whose packages Noust does not manage (an image-based
system), the error says how to do it by hand.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core import audit
from noust.core.exceptions import DatabaseEngineError, DependencyError, PrerequisiteError
from noust.core.fs import FileSystem, get_fs, is_rehearsal
from noust.core.package_family import PackageFamily
from noust.core.runner import CommandRunner, get_runner
from noust.managers.database import flavours
from noust.managers.database.flavours import (
    Repository,
    SigningKey,
    install_repository,
    withdraw_repository,
)
from noust.managers.database.rpm_flavours import (
    DNF,
    ZYPPER,
    RpmRepository,
    install_rpm_repository,
    withdraw_rpm_repository,
)
from noust.managers.server.host import HostPaths, Platform, detect_platform
from noust.managers.server.pkg import backend_for
from noust.managers.server.software_record import record_install

#: Where Cloudflare keeps its packages, for the messages.
PACKAGE_REPOSITORY = "https://pkg.cloudflare.com/"

#: Cloudflare's package signing key ("CloudFlare Software Packaging 2025"). The file served at
#: ``cloudflare-main.gpg`` and the ASCII one the RPM repository names carry this same primary key.
CLOUDFLARE_KEY = SigningKey(
    url="https://pkg.cloudflare.com/cloudflare-main.gpg",
    fingerprint="CC94B39C77AE7342A68B89628A682D308D4E5E73",
)

#: The package, and the program it installs.
PACKAGE = "cloudflared"

#: Seconds the package manager may take to refresh or to install.
_TIMEOUT = 1800
_VERSION_TIMEOUT = 20


@dataclass(frozen=True)
class CloudflaredInstall:
    """
    What installing the connector did.

    Attributes:
        path: Where ``cloudflared`` is; None under a rehearsal that found it nowhere.
        installed_now: Whether this call installed it, False when it was already there.
        version: What the program says its version is, when it could be asked.
        rehearsal: Nothing was changed: this is what would have been done.
    """

    path: str | None
    installed_now: bool
    version: str | None = None
    rehearsal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result for the API and the command.
        """
        return {
            "path": self.path,
            "installed_now": self.installed_now,
            "version": self.version,
            "rehearsal": self.rehearsal,
        }


def repository_for(platform: Platform) -> Repository | RpmRepository | None:
    """
    Name Cloudflare's repository in the form this platform's package manager reads.

    Args:
        platform: What the machine is.

    Returns:
        An apt source, an RPM repository for dnf or zypper, or None when Noust installs no
        package here.
    """
    if platform.family is PackageFamily.APT:
        return Repository(
            name="pkg.cloudflare.com",
            key=CLOUDFLARE_KEY,
            keyring=Path("/usr/share/keyrings/noust-cloudflared.gpg"),
            sources=Path("/etc/apt/sources.list.d/noust-cloudflared.list"),
            url="https://pkg.cloudflare.com/cloudflared",
            suite="any main",
            family="https://pkg.cloudflare.com/cloudflared",
        )
    if platform.family in (PackageFamily.DNF, PackageFamily.ZYPPER):
        dnf = platform.family is PackageFamily.DNF
        return RpmRepository(
            name="pkg.cloudflare.com",
            alias="cloudflared",
            key=CLOUDFLARE_KEY,
            baseurl="https://pkg.cloudflare.com/cloudflared/rpm",
            manager=DNF if dnf else ZYPPER,
            directory=Path("/etc/yum.repos.d" if dnf else "/etc/zypp/repos.d"),
        )
    return None


class CloudflaredInstaller:
    """
    Install the connector, from Cloudflare's own repository, with its pinned key.

    Args:
        runner: Where curl, gpg and the package manager run; the process-wide one by default.
        fs: Where the repository's files are written; the process-wide one by default.
        host: Where the system files are; the real machine's by default.
        platform: What the machine is; detected by default.
        which: Finds a program on the path; ``shutil.which`` by default.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        platform: Platform | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._runner = runner
        self._fs = fs
        self._host = host
        self._platform = platform
        self._which = which

    @property
    def runner(self) -> CommandRunner:
        """The runner in force now (``--dry-run`` swaps it process-wide)."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force now."""
        return self._fs or get_fs()

    def install(self) -> CloudflaredInstall:
        """
        Put ``cloudflared`` on this machine, unless it is already there.

        Returns:
            Where it is, whether this call installed it and which version it is.

        Raises:
            PrerequisiteError: Noust installs no packages on this machine; the error says how to
                do it by hand.
            DependencyError: The key is not Cloudflare's, the package manager cannot read the
                repository or cannot install the package, or the install left no connector on
                the path. The error carries the tool's own words.
        """
        found = self._which(PACKAGE)
        if found:
            return CloudflaredInstall(found, False, self._version(found))
        platform = self._platform or detect_platform(self.runner)
        repository = None if platform.transactional else repository_for(platform)
        if repository is None:
            raise PrerequisiteError(
                "Noust cannot install cloudflared on this machine",
                details=(
                    "It installs packages through apt, dnf or zypper on a machine whose packages "
                    f"change in place. Install it from Cloudflare's own package repository, {PACKAGE_REPOSITORY}, "
                    "then create the tunnel again."
                ),
            )
        added = self._add(repository)
        self._refresh(platform, repository, added)
        self._install(platform)
        if is_rehearsal():
            return CloudflaredInstall(None, False, rehearsal=True)
        path = self._which(PACKAGE)
        if not path:
            raise DependencyError(
                "cloudflared was installed but not found on the path",
                details="Check 'which cloudflared' and that its package put it in /usr/bin.",
            )
        audit.record(
            "tunnels.install_connector",
            target="package:cloudflared",
            details={"path": path, "family": platform.family.value},
        )
        record_install(
            "cloudflared", via="cloudflared", packages=[PACKAGE], host=self._host or flavours.HOST
        )
        return CloudflaredInstall(path, True, self._version(path))

    def _add(self, repository: Repository | RpmRepository) -> list[Path]:
        """
        Add the repository, verifying its key first.

        Args:
            repository: Cloudflare's, in this family's form.

        Returns:
            The files this call created, so a later failure can take them back out.

        Raises:
            DependencyError: The key could not be fetched or is not the pinned one.
        """
        host = self._host or flavours.HOST
        try:
            if isinstance(repository, Repository):
                return install_repository(repository, runner=self.runner, fs=self.fs, host=host)
            return install_rpm_repository(repository, runner=self.runner, fs=self.fs, host=host)
        except DatabaseEngineError as exc:
            raise DependencyError(exc.message, details=exc.details, output=exc.output) from exc

    def _refresh(
        self, platform: Platform, repository: Repository | RpmRepository, added: list[Path]
    ) -> None:
        """
        Refresh the package manager's lists, taking the repository back out if it cannot read it.

        Args:
            platform: What the machine is.
            repository: The repository that was added.
            added: What adding it created.

        Raises:
            DependencyError: The refresh failed; the repository is gone again.
        """
        backend = backend_for(platform, runner=self.runner, fs=self.fs)
        result = self.runner.run(
            backend.refresh_argv(), env=backend.env(), timeout=backend.refresh_timeout()
        )
        if result.success:
            return
        # One source the package manager cannot read breaks every later run on the server, not
        # only this install: what was just added goes back out.
        if isinstance(repository, Repository):
            removed = withdraw_repository(added, fs=self.fs)
        else:
            removed = withdraw_rpm_repository(added, fs=self.fs)
        note = (
            f" Noust removed what it had just added ({', '.join(map(str, removed))}), so the "
            "package manager is as it was."
            if removed
            else ""
        )
        raise DependencyError(
            "The package manager could not read Cloudflare's repository",
            details=((result.stderr or result.stdout).strip() or "No output.") + note,
        )

    def _install(self, platform: Platform) -> None:
        """
        Install the package.

        Args:
            platform: What the machine is.

        Raises:
            DependencyError: The package manager refused, with its own words.
        """
        backend = backend_for(platform, runner=self.runner, fs=self.fs)
        result = backend.install([PACKAGE], timeout=_TIMEOUT)
        if not result.success:
            raise DependencyError(
                "cloudflared could not be installed",
                details=(result.stderr or result.stdout).strip()[-2000:] or "No output.",
            )

    def _version(self, path: str) -> str | None:
        """
        Ask the program which version it is.

        Args:
            path: Where it is.

        Returns:
            The first line it prints, or None when it could not be asked.
        """
        result = self.runner.run([path, "--version"], timeout=_VERSION_TIMEOUT)
        line = result.stdout.strip().splitlines()[0] if result.success and result.stdout else ""
        return line or None
