# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Railpack: building an image from source code without a Dockerfile.

Noust does not reimplement Nixpacks or Railpack (decision D1 of the 4.0
design). A container application can be built by Railpack when its binary is
on the server, and ``noust setup railpack`` puts it there: one release of the
project, pinned in this module, downloaded over HTTPS, checked against the
SHA-256 published with that release, unpacked member by member and installed
atomically. Nothing is fetched from "latest", and nothing that fails its
checksum is installed: the binary runs as root on this server, and builds
untrusted repositories through the Docker daemon.

Bumping :data:`VERSION` is a change to this file and nothing else: the
checksums come from the ``checksums.txt`` of that release, and the BuildKit
frontend image a build uses is named after the same version.
"""

from __future__ import annotations

import hashlib
import platform
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.fs import SECRET_DIR_MODE, FileSystem, get_fs, is_rehearsal
from noust.core.logger import Logger
from noust.core.runner import CommandRunner, get_runner

#: The release Noust installs and builds with.
VERSION = "0.40.1"

#: Where a release is published.
RELEASE_URL = "https://github.com/railwayapp/railpack/releases/download/v{version}/{name}"

#: The BuildKit frontend a build runs the plan through, tagged like the release.
FRONTEND_IMAGE = "ghcr.io/railwayapp/railpack-frontend"

#: Where the binary is installed: on the PATH of root and of the units.
INSTALL_PATH = Path("/usr/local/bin/railpack")

#: The binary's name inside the archive, and the only file taken from it.
BINARY_NAME = "railpack"

#: Seconds each step may take.
DOWNLOAD_TIMEOUT = 300
EXTRACT_TIMEOUT = 120
VERSION_TIMEOUT = 30

#: The largest the binary may be: the release is about 24 MB.
MAX_BINARY_BYTES = 200 * 1024 * 1024

_VERSION = re.compile(r"\d+\.\d+\.\d+")


@dataclass(frozen=True)
class Asset:
    """
    One file of the pinned release.

    Attributes:
        name: The archive's file name.
        sha256: The digest the release publishes for it (``checksums.txt``).
    """

    name: str
    sha256: str


#: The Linux builds of :data:`VERSION`, by what ``uname -m`` says. musl, so
#: the binary has no libc to disagree with on any distribution.
ASSETS: dict[str, Asset] = {
    "x86_64": Asset(
        "railpack-v0.40.1-x86_64-unknown-linux-musl.tar.gz",
        "2842de93e68713af9037e0bc0a398d7da78f3b96aa4804303a638db2bc69bd30",
    ),
    "aarch64": Asset(
        "railpack-v0.40.1-arm64-unknown-linux-musl.tar.gz",
        "c24a064b586b8f4f8c2fab44dd5ef19253e4c6cc4e1df793b3ae19cd87f7a5d4",
    ),
}

#: Other spellings of the same machines.
_MACHINES = {"amd64": "x86_64", "arm64": "aarch64"}


@dataclass(frozen=True)
class InstallOutcome:
    """
    What ``noust setup railpack`` did.

    Attributes:
        version: The version now installed.
        path: Where the binary is.
        changed: False when that version was already there.
    """

    version: str
    path: Path
    changed: bool


def asset_for(machine: str | None = None) -> Asset:
    """
    Choose the archive for this machine.

    Args:
        machine: What ``uname -m`` says; this machine's when None.

    Returns:
        The asset.

    Raises:
        DeploymentError: Railpack does not publish a build for it.
    """
    name = (machine or platform.machine()).lower()
    asset = ASSETS.get(_MACHINES.get(name, name))
    if asset is None:
        raise DeploymentError(
            f"Railpack has no build for {name or 'this machine'}",
            details="It publishes Linux builds for x86_64 and aarch64. Build the image from a "
            "Dockerfile instead, or deploy it from a registry.",
        )
    return asset


def frontend_reference(version: str) -> str:
    """
    Name the BuildKit frontend image of a Railpack version.

    Args:
        version: ``0.40.1``.

    Returns:
        ``ghcr.io/railwayapp/railpack-frontend:v0.40.1``.

    Raises:
        ValidationError: The version is not three numbers.
    """
    if _VERSION.fullmatch(version) is None:
        raise ValidationError(f"{version!r} is not a Railpack version", field="version")
    return f"{FRONTEND_IMAGE}:v{version}"


def installed_version(runner: CommandRunner | None = None) -> str | None:
    """
    Ask the installed binary which version it is.

    Args:
        runner: The runner; the process-wide one when None.

    Returns:
        The version, or None when Railpack is not on the PATH or does not say.
    """
    run = runner or get_runner()
    if not run.exists(BINARY_NAME):
        return None
    result = run.run([BINARY_NAME, "--version"], timeout=VERSION_TIMEOUT)
    if not result.success:
        return None
    found = _VERSION.search(result.stdout or result.stderr)
    return found.group(0) if found else None


def _digest(path: Path) -> str:
    """
    Hash a file without reading it whole.

    Args:
        path: The file.

    Returns:
        Its SHA-256, in hex.
    """
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def check_archive(path: Path) -> None:
    """
    Refuse an archive that is not a Railpack release, before anything is unpacked.

    The digest says the bytes are the ones published; this says what is in
    them is a regular ``railpack`` file and nothing that could be written
    elsewhere, so unpacking it with tar cannot do more than make that file.

    Args:
        path: The downloaded archive.

    Raises:
        DeploymentError: It is not a readable gzip tar, has no ``railpack``
            member, that member is not a regular file or is too large.
    """
    try:
        with tarfile.open(path, "r:gz") as archive:
            member = next((m for m in archive.getmembers() if m.name == BINARY_NAME), None)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise DeploymentError(
            "The Railpack archive cannot be read",
            details=f"{exc}. Nothing was installed; run 'noust setup railpack' again.",
        ) from exc
    if member is None or not member.isreg() or member.size > MAX_BINARY_BYTES:
        raise DeploymentError(
            "The Railpack archive does not hold the railpack binary as a regular file",
            details="Nothing was installed. This is not the release Noust pins.",
        )


class RailpackInstaller:
    """Installs the pinned Railpack release, checked, through the seams."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        logger: Logger | None = None,
        work_dir: Path,
        install_path: Path = INSTALL_PATH,
    ) -> None:
        """
        Args:
            runner: Runs curl, tar and the binary; the process-wide one when None.
            fs: Every file change goes through it; the process-wide one when None.
            logger: Where progress is said.
            work_dir: Where the archive is downloaded and unpacked; private
                (0700), and removed when the install is over.
            install_path: Where the binary goes.
        """
        self._runner = runner
        self._fs = fs
        self.logger = logger or Logger()
        self.work_dir = work_dir
        self.install_path = install_path

    @property
    def runner(self) -> CommandRunner:
        """The runner everything is executed through."""
        return self._runner if self._runner is not None else get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem every change goes through."""
        return self._fs if self._fs is not None else get_fs()

    def install(self, *, force: bool = False, machine: str | None = None) -> InstallOutcome:
        """
        Download, verify and install the pinned release.

        Args:
            force: Install it again even when this version is already there.
            machine: What ``uname -m`` says; this machine's when None.

        Returns:
            What was done.

        Raises:
            DeploymentError: No build for this machine, curl is missing, the
                download fails, its checksum does not match the pinned one
                (nothing is installed), the archive is not what the release
                holds, or the installed binary does not report the pinned version.
        """
        asset = asset_for(machine)
        current = installed_version(self.runner)
        if current == VERSION and not force:
            self.logger.substep(f"Railpack {VERSION} is already installed")
            return InstallOutcome(VERSION, self.install_path, changed=False)
        if not self.runner.exists("curl") or not self.runner.exists("tar"):
            raise DeploymentError(
                "curl and tar are needed to install Railpack",
                details="Install them with your package manager, then run 'noust setup railpack'.",
            )

        self.fs.make_dir(self.work_dir, mode=SECRET_DIR_MODE, parents=True)
        try:
            archive = self._download(asset)
            self._unpack(archive)
            self._install(self.work_dir / BINARY_NAME)
        finally:
            self.fs.remove_tree(self.work_dir)

        if not is_rehearsal():
            reported = installed_version(self.runner)
            if reported != VERSION:
                raise DeploymentError(
                    f"The installed railpack reports {reported or 'no version'}, "
                    f"not the {VERSION} that was installed",
                    details=f"Another railpack earlier on the PATH may shadow {self.install_path}.",
                )
        self.logger.success(f"Railpack {VERSION} installed at {self.install_path}")
        return InstallOutcome(VERSION, self.install_path, changed=True)

    def _download(self, asset: Asset) -> Path:
        """
        Fetch the archive and check it against the pinned digest.

        Args:
            asset: The archive to fetch.

        Returns:
            Where it is.

        Raises:
            DeploymentError: The download failed or the digest is not the pinned one.
        """
        archive = self.work_dir / asset.name
        url = RELEASE_URL.format(version=VERSION, name=asset.name)
        self.logger.substep(f"Downloading {url}")
        result = self.runner.run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--proto",
                "=https",
                "--tlsv1.2",
                "--output",
                str(archive),
                url,
            ],
            timeout=DOWNLOAD_TIMEOUT,
        )
        if not result.success:
            raise DeploymentError(
                "Railpack could not be downloaded",
                details=(result.stderr or result.stdout).strip()
                or "Check that this server can reach github.com.",
            )
        if is_rehearsal():
            self.logger.substep(f"Would check {asset.name} against SHA-256 {asset.sha256}")
            return archive
        found = _digest(archive)
        if found != asset.sha256:
            self.fs.remove(archive)
            raise DeploymentError(
                "The Railpack download does not match its published checksum",
                details=f"Expected SHA-256 {asset.sha256}, got {found}. Nothing was installed.",
            )
        self.logger.substep("Checksum verified")
        return archive

    def _unpack(self, archive: Path) -> None:
        """
        Take the binary out of the archive.

        Args:
            archive: The verified archive.

        Raises:
            DeploymentError: The archive is not what the release holds, or tar failed.
        """
        if not is_rehearsal():
            check_archive(archive)
        result = self.runner.run(
            ["tar", "--extract", "--gzip", "--no-same-owner", "--file", str(archive),
             "--directory", str(self.work_dir), BINARY_NAME],
            timeout=EXTRACT_TIMEOUT,
        )  # fmt: skip
        if not result.success:
            raise DeploymentError(
                "The Railpack archive could not be unpacked", details=result.stderr.strip()
            )

    def _install(self, binary: Path) -> None:
        """
        Put the binary in place, so a reader never meets half of it.

        Args:
            binary: The unpacked binary.
        """
        staged = self.install_path.with_name(f".{self.install_path.name}.new")
        self.fs.make_dir(self.install_path.parent)
        self.fs.move(binary, staged)
        self.fs.chmod(staged, 0o755)
        self.fs.rename(staged, self.install_path)
