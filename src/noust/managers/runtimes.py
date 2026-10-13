# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The language runtimes an application can be pinned to.

Until 3.4 an application ran on whatever Node, Python and PHP the server's
package manager had put there, so one server could not host a project that
needs Node 20 and another that needs Node 22, and a distribution upgrade
changed the runtime of every application at its next restart. A project now
says which it needs (``build: {runtime: node@22}``, or a ``.nvmrc``, an
``engines.node``, a ``.python-version``, a ``composer.json``), and this module
makes it available and answers where it is:

- **Node** is downloaded from nodejs.org as the official tarball, verified
  against the ``SHASUMS256.txt`` of its release, and unpacked under
  :data:`noust.core.paths.RUNTIMES_DIR` ``/node/<version>``.
- **Python** is installed with ``uv python install`` into
  ``<runtimes>/python/<version>`` when ``uv`` is on the server; without it the
  error says how to get it or what to do instead.
- **PHP** is the distribution's package, as the PHP-FPM deployer already finds
  it (:func:`noust.deployers.helpers.php_fpm.fpm_versions`): a version that is
  not installed is installed with the package manager (Debian and Ubuntu; the
  Sury and Ondrej repositories carry the versions their own do not), and one
  that cannot be is explained, never guessed at.

A managed runtime is root's, world-readable, and never written to by a build:
the build runs as ``noust-build`` in a sandbox that sees the runtimes
directory read-only, and the unit runs as the application's account with the
absolute path of the interpreter. Nothing here uses nvm, which lives in a
home directory systemd services cannot read.

The one question the deployers ask is :meth:`RuntimeManager.choose`: given
what the project asks for, which runtime does this deployment use? A request
the project made explicitly (``build.runtime``) is an order and a failure to
meet it fails the deployment; a version a repository file merely mentions
(``.nvmrc``) is met by the server's own runtime when that satisfies it, by a
managed one otherwise, and never breaks a deployment that worked: when the
managed one cannot be installed, the deployment goes on with the server's own
and says so.
"""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError

from noust.core import audit, paths
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import FileSystem, is_rehearsal
from noust.core.package_family import PackageFamily, detect_family
from noust.core.runner import SANDBOX_PATH, CommandResult, CommandRunner
from noust.core.versions import RuntimeRequest, Version, parse_version
from noust.managers.base_manager import BaseManager
from noust.managers.source_manager import SourceManager, _open_url, validate_archive_url

#: Where Node's official builds are published.
NODE_DIST = "https://nodejs.org/dist"

#: The largest ``index.json`` or ``SHASUMS256.txt`` read.
MAX_INDEX_BYTES = 8 * 1024 * 1024

#: Deadlines. A version command answers at once; the installers take minutes.
VERSION_TIMEOUT = 20
INDEX_TIMEOUT = 60
UV_TIMEOUT = 1800
PACKAGE_TIMEOUT = 1800

#: ``platform.machine()`` to the name Node's files carry.
_NODE_PLATFORMS: dict[str, str] = {
    "x86_64": "linux-x64",
    "amd64": "linux-x64",
    "aarch64": "linux-arm64",
    "arm64": "linux-arm64",
    "armv7l": "linux-armv7l",
    "ppc64le": "linux-ppc64le",
    "s390x": "linux-s390x",
}

_FULL_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_MINOR_VERSION = re.compile(r"^\d+\.\d+$")
_CPYTHON_DIR = re.compile(r"^cpython-(\d+\.\d+\.\d+)(?:[+-].*)?$")

#: The newest PHP the Sury and Ondrej builds carry first; a request for a PHP
#: older than this is a request for a package that may be gone.
_PHP_PACKAGE_STEM = re.compile(r"\bphp-")

Fetch = Callable[[str, int], bytes]


@dataclass(frozen=True)
class InstalledRuntime:
    """
    A runtime that can run an application.

    Attributes:
        language: ``node``, ``python`` or ``php``.
        version: The full version (``22.11.0``, ``3.12.13``); for PHP, the
            ``major.minor`` the distribution installs it as (``8.3``).
        home: The directory whose ``bin`` holds the interpreter: under the
            runtimes directory for a managed one, ``/usr`` for the server's.
        managed: Whether Noust installed it into its own directory.
    """

    language: str
    version: str
    home: Path
    managed: bool = True

    @property
    def bin_dir(self) -> Path:
        """The directory holding its programs."""
        return self.home / "bin"

    def program(self, name: str) -> Path | None:
        """
        Find one of its programs.

        Args:
            name: ``node``, ``npm``, ``python3``...

        Returns:
            The absolute path when it has such a program, else None.
        """
        candidate = self.bin_dir / name
        return candidate if candidate.exists() else None

    def path_environment(self, base: str = SANDBOX_PATH) -> dict[str, str]:
        """
        Say the ``PATH`` that puts this runtime first.

        Args:
            base: The rest of the path.

        Returns:
            ``{"PATH": ...}``; empty for the server's own runtime, which is
            already where the base path finds it.
        """
        if not self.managed and self.language != "php":
            return {}
        return {"PATH": f"{self.bin_dir}:{base}"}

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``.

        Returns:
            Its language, version, home and whether Noust installed it.
        """
        return {
            "language": self.language,
            "version": self.version,
            "home": str(self.home),
            "managed": self.managed,
        }


@dataclass(frozen=True)
class RuntimeChoice:
    """
    The runtime a deployment uses, and why.

    Attributes:
        request: What the project asked for.
        runtime: The runtime to use; None means the server's own, as
            before 3.4 (nothing is put on ``PATH``).
        reason: One sentence for the deployment's log.
        warning: Set when the deployment goes on with something other than
            what was asked for.
    """

    request: RuntimeRequest
    runtime: InstalledRuntime | None
    reason: str
    warning: str | None = None


def _http_get(url: str, limit: int = MAX_INDEX_BYTES) -> bytes:
    """
    Read a small document over HTTPS, within a size.

    Args:
        url: An https URL.
        limit: The most bytes accepted.

    Returns:
        The body.

    Raises:
        DeploymentError: The request failed or the body is too large.
    """
    validate_archive_url(url)
    # The socket timeout bounds one read, not the transfer: a server that
    # answers a byte at a time never trips it, so the whole is bounded too.
    deadline = time.monotonic() + 2 * INDEX_TIMEOUT
    chunks: list[bytes] = []
    total = 0
    try:
        with _open_url(url, timeout=INDEX_TIMEOUT) as response:
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{url} was not complete within {2 * INDEX_TIMEOUT}s")
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise DeploymentError(
                        f"{url} is larger than {limit} bytes", details="Refusing to read it."
                    )
    except (URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise DeploymentError(
            f"Could not read {url}",
            details=f"{reason}. Check the server's connection to nodejs.org and try again.",
        ) from exc
    return b"".join(chunks)


class RuntimeManager(BaseManager):
    """Installs, finds and removes the runtimes applications are pinned to."""

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        *,
        root: Path | None = None,
        fetch: Fetch | None = None,
        source_manager: SourceManager | None = None,
        machine: str | None = None,
        php_root: Path = Path("/"),
    ) -> None:
        """
        Args:
            verbose: Enable verbose logging.
            runner: The runner every command goes through.
            fs: The filesystem every change goes through.
            root: The directory runtimes are installed under; the
                :data:`~noust.core.paths.RUNTIMES_DIR` by default.
            fetch: Reads a small document by URL and size limit; the HTTPS
                reader by default. Tests replace it.
            source_manager: Downloads and unpacks the Node tarball; built on
                first use by default.
            machine: The CPU architecture, as ``platform.machine()`` says it;
                this machine's by default.
            php_root: The filesystem root PHP-FPM is looked for under.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        self.root = root if root is not None else paths.RUNTIMES_DIR
        self._fetch: Fetch = fetch if fetch is not None else _http_get
        self._source_manager = source_manager
        self._machine = machine if machine is not None else platform.machine()
        self.php_root = php_root
        self._host: dict[str, InstalledRuntime | None] = {}

    # BaseManager --------------------------------------------------------

    def is_installed(self) -> bool:
        """Runtimes are Noust's own; there is nothing to be installed first."""
        return True

    def get_version(self) -> str | None:
        """A set of runtimes has no version of its own."""
        return None

    @property
    def source_manager(self) -> SourceManager:
        """The manager that downloads, verifies and unpacks archives."""
        if self._source_manager is None:
            self._source_manager = SourceManager(verbose=self.verbose, fs=self._fs)
        return self._source_manager

    # What is installed ----------------------------------------------------

    def list_installed(self) -> list[InstalledRuntime]:
        """
        List the runtimes Noust installed, and the PHP versions the server has.

        Returns:
            Node and Python under the runtimes directory, PHP from the
            installed FPMs, each language newest first.
        """
        found: list[InstalledRuntime] = []
        for version in self._versions_under("node"):
            home = self.root / "node" / version
            if (home / "bin" / "node").exists():
                found.append(InstalledRuntime("node", version, home))
        for version in self._versions_under("python"):
            interpreter = self._python_home(self.root / "python" / version)
            if interpreter is not None:
                found.append(InstalledRuntime("python", version, interpreter))
        for version in self._php_versions():
            found.append(self._php_runtime(version))
        return sorted(
            found, key=lambda item: (item.language, tuple(-n for n in _key(item.version)))
        )

    def _versions_under(self, language: str) -> list[str]:
        """
        Name the version directories of a language, newest first.

        Args:
            language: ``node`` or ``python``.

        Returns:
            Directory names that are full versions; staging directories and
            anything else somebody put there are not runtimes.
        """
        directory = self.root / language
        if not directory.is_dir():
            return []
        names = [
            entry.name
            for entry in directory.iterdir()
            if _FULL_VERSION.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink()
        ]
        return sorted(names, key=_key, reverse=True)

    @staticmethod
    def _python_home(version_dir: Path) -> Path | None:
        """
        Find the interpreter's home inside a uv installation directory.

        Args:
            version_dir: ``<runtimes>/python/<version>``.

        Returns:
            The ``cpython-*`` directory holding ``bin/python3``, or None.
        """
        if not version_dir.is_dir():
            return None
        for entry in sorted(version_dir.iterdir()):
            if (
                _CPYTHON_DIR.fullmatch(entry.name)
                and entry.is_dir()
                and not entry.is_symlink()
                and (entry / "bin" / "python3").exists()
            ):
                return entry
        return None

    def _php_versions(self) -> list[str]:
        """
        Name the PHP versions whose FPM is installed.

        Returns:
            ``major.minor`` strings, newest first.
        """
        from noust.deployers.helpers.php_fpm import fpm_versions

        return fpm_versions(self.php_root)

    def _php_runtime(self, version: str) -> InstalledRuntime:
        """
        Describe an installed PHP version.

        Args:
            version: ``major.minor``.

        Returns:
            The runtime, whose ``bin`` holds a ``php`` that is that version's
            command line (a link Noust keeps), so Composer asks the version
            the application runs on.
        """
        return InstalledRuntime("php", version, self.root / "php" / version, managed=False)

    def host(self, language: str) -> InstalledRuntime | None:
        """
        Find the runtime the server itself has.

        Args:
            language: ``node``, ``python`` or ``php``.

        Returns:
            Its version as the program says it (and PHP's newest FPM), or None
            when the server has none.
        """
        if language not in self._host:
            self._host[language] = self._read_host(language)
        return self._host[language]

    def _read_host(self, language: str) -> InstalledRuntime | None:
        """
        Ask the server's own program for its version.

        Args:
            language: ``node``, ``python`` or ``php``.

        Returns:
            The runtime, or None.
        """
        if language == "php":
            versions = self._php_versions()
            return self._php_runtime(versions[0]) if versions else None
        argv, pattern = {
            "node": (["node", "--version"], r"v?(\d+\.\d+\.\d+)"),
            "python": (["python3", "--version"], r"Python (\d+\.\d+\.\d+)"),
        }[language]
        if not self.runner.exists(argv[0]):
            return None
        result = self.runner.run(argv, timeout=VERSION_TIMEOUT)
        match = re.search(pattern, result.output) if result.success else None
        if match is None:
            return None
        return InstalledRuntime(language, match.group(1), Path("/usr"), managed=False)

    def find(self, request: RuntimeRequest) -> InstalledRuntime | None:
        """
        Find the newest installed runtime that satisfies a request.

        Args:
            request: What the project asks for.

        Returns:
            A runtime Noust installed (or, for PHP, the distribution's), or
            None.

        Raises:
            ValidationError: The request's range is not in its language's
                dialect.
        """
        candidates = [item for item in self.list_installed() if item.language == request.language]
        matching = [
            item
            for item in candidates
            if request.allows(_version(item.version))
            and (request.exact is None or _version(item.version) == request.exact)
        ]
        if not matching:
            return None
        return max(matching, key=lambda item: _key(item.version))

    # Choosing -------------------------------------------------------------

    def choose(self, request: RuntimeRequest, *, install: bool = True) -> RuntimeChoice:
        """
        Decide which runtime a deployment uses for what its project asked.

        Args:
            request: What the project asks for.
            install: Whether a runtime that is missing may be installed.

        Returns:
            The runtime and the reason. An explicit request is always met by a
            managed (or, for PHP, a package-installed) runtime; a version
            file's is met by the server's own runtime when that satisfies it.

        Raises:
            DeploymentError: An explicit request could not be met.
            ValidationError: The request's range cannot be read; for a version
                file's request it is reported as a warning instead.
        """
        if request.explicit:
            runtime = self.ensure(request) if install else self.find(request)
            if runtime is None:
                raise DeploymentError(
                    f"{request.text} is not installed",
                    details=f"Install it: noust runtime install {request.text}",
                )
            return RuntimeChoice(request, runtime, f"Runtime {request.text}: {_describe(runtime)}")

        try:
            return self._choose_detected(request, install=install)
        except ValidationError as exc:
            return self._host_fallback(
                request, f"{request.source} could not be read: {exc.message}"
            )

    def _choose_detected(self, request: RuntimeRequest, *, install: bool) -> RuntimeChoice:
        """
        Meet a version a repository file mentions.

        Args:
            request: The request, not explicit.
            install: Whether a runtime that is missing may be installed.

        Returns:
            The choice; the server's own runtime when it satisfies, a managed
            one otherwise, and the server's own with a warning when the
            managed one cannot be had.
        """
        host = self.host(request.language)
        if request.language == "php":
            # PHP is always one FPM or another; the newest that satisfies.
            runtime = self.find(request)
            if runtime is not None:
                self._link_php_cli(runtime)
                return RuntimeChoice(
                    request,
                    runtime,
                    f"{request.source} asks for PHP {request.spec}: PHP-FPM {runtime.version} "
                    "satisfies it",
                )
            return self._host_fallback(
                request, f"{request.source} asks for PHP {request.spec}, which no PHP-FPM here is"
            )
        if host is not None and request.allows(_version(host.version)):
            return RuntimeChoice(
                request,
                None,
                f"{request.source} asks for {request.language} {request.spec}: the server's "
                f"{host.version} satisfies it",
            )
        managed = self.find(request)
        if managed is None and install:
            try:
                managed = self.ensure(request)
            except NoustError as exc:
                return self._host_fallback(
                    request,
                    f"{request.source} asks for {request.language} {request.spec} and it could "
                    f"not be installed: {exc.message}",
                )
        if managed is None:
            return self._host_fallback(
                request,
                f"{request.source} asks for {request.language} {request.spec}, which is not "
                "installed",
            )
        return RuntimeChoice(
            request,
            managed,
            f"{request.source} asks for {request.language} {request.spec}: "
            f"using {_describe(managed)}",
        )

    def _host_fallback(self, request: RuntimeRequest, why: str) -> RuntimeChoice:
        """
        Go on with the server's own runtime, saying so.

        Args:
            request: The request that was not met.
            why: What happened.

        Returns:
            A choice with a warning, which never fails the deployment.
        """
        host = self.host(request.language)
        have = f"{request.language} {host.version}" if host is not None else "none"
        return RuntimeChoice(
            request,
            host if request.language == "php" else None,
            f"Using the server's own {request.language} ({have})",
            warning=f"{why}; using the server's own ({have}). Pin it with "
            f"noust app build set --runtime {request.language}@<version>.",
        )

    # Installing -----------------------------------------------------------

    def ensure(self, request: RuntimeRequest) -> InstalledRuntime:
        """
        Make a runtime that satisfies a request available, installing it if needed.

        Args:
            request: What the project asks for.

        Returns:
            The runtime.

        Raises:
            DeploymentError: It cannot be installed, with how to do it by hand.
            ValidationError: The request's range is not in its dialect.
        """
        found = self.find(request)
        if found is not None:
            if found.language == "php":
                self._link_php_cli(found)
            return found
        return self.install(request)

    def install(self, request: RuntimeRequest) -> InstalledRuntime:
        """
        Install the runtime a request names, unless it is already there.

        Args:
            request: What to install.

        Returns:
            The runtime.

        Raises:
            DeploymentError: It cannot be installed, with how to do it by hand.
            ValidationError: The request's range is not in its dialect.
        """
        installers = {
            "node": self._install_node,
            "python": self._install_python,
            "php": self._install_php,
        }
        runtime = installers[request.language](request)
        audit.record(
            "runtimes.install",
            target=f"runtime:{request.language}@{runtime.version}",
            details={"request": request.text, "managed": runtime.managed},
        )
        return runtime

    def _stage(self, language: str) -> Path:
        """
        Make a private directory to install into, beside the final one.

        Args:
            language: ``node`` or ``python``.

        Returns:
            The directory. The final one appears by a rename, so a runtime is
            either complete or not there.
        """
        parent = self.root / language
        self.fs.make_dir(parent, mode=0o755, parents=True)
        staging = parent / f".staging-{secrets.token_hex(6)}"
        self.fs.make_dir(staging, mode=0o755)
        return staging

    def _place(self, staging: Path, final: Path) -> None:
        """
        Put a finished installation where it is looked for.

        Args:
            staging: The directory it was installed in.
            final: Where it stays.

        Raises:
            DeploymentError: The directory could not be moved into place.
        """
        try:
            self.fs.rename(staging, final)
        except OSError as exc:
            # Another deployment installed the same version a moment ago:
            # what is there is as good as this.
            if final.is_dir():
                self.fs.remove_tree(staging)
                return
            self.fs.remove_tree(staging)
            raise DeploymentError(
                f"Could not put the runtime in place at {final}", details=str(exc)
            ) from exc

    def _tidy(self, staging: Path, *, keep: Path) -> None:
        """
        Remove what an installer left beside the interpreter.

        uv leaves a lock file nobody but root should be able to write to
        (it is created world-writable), a scratch directory, and a link from
        the minor version's name to the staging directory this installation is
        about to leave. Nothing there is the runtime.

        Args:
            staging: The directory the installer worked in.
            keep: The interpreter's own directory, which stays.
        """
        for entry in staging.iterdir():
            if entry == keep:
                continue
            if entry.is_dir() and not entry.is_symlink():
                self.fs.remove_tree(entry)
            else:
                self.fs.remove(entry)

    def _discard(self, staging: Path) -> None:
        """
        Remove what a failed installation left.

        Args:
            staging: The staging directory.
        """
        if staging.exists():
            self.fs.remove_tree(staging)

    # Node ------------------------------------------------------------------

    def _node_platform(self) -> str:
        """
        Name the platform Node's builds are published for.

        Returns:
            ``linux-x64``, ``linux-arm64``...

        Raises:
            DeploymentError: Alpine (musl), or a CPU without an official build.
        """
        if Path("/etc/alpine-release").exists():
            raise DeploymentError(
                "Node's official builds need glibc, which this system does not have",
                details="Install Node with the distribution's package manager: apk add nodejs npm",
            )
        name = _NODE_PLATFORMS.get(self._machine.lower())
        if name is None:
            raise DeploymentError(
                f"Node publishes no official build for {self._machine}",
                details="Install Node with the distribution's package manager.",
            )
        return name

    def _node_version(self, request: RuntimeRequest, platform_name: str) -> str:
        """
        Pick the Node release a request means.

        A full version is that release. Anything else is looked up in
        ``index.json``: a major or a minor takes the newest release of it, and a
        range (a repository's ``engines.node``) the newest long-term release
        it allows, or the newest it allows when none of them is long-term.

        Args:
            request: The request.
            platform_name: The platform whose build must exist.

        Returns:
            The version, ``22.11.0``.

        Raises:
            DeploymentError: No published release satisfies it.
            ValidationError: The range cannot be read.
        """
        wanted = request.version_range()
        exact = request.exact
        if exact is not None:
            return str(exact)
        try:
            index = json.loads(self._fetch(f"{NODE_DIST}/index.json", MAX_INDEX_BYTES))
        except ValueError as exc:
            raise DeploymentError("nodejs.org's release index is not valid JSON") from exc
        releases: list[tuple[Version, bool]] = []
        for entry in index if isinstance(index, list) else []:
            if not isinstance(entry, dict) or platform_name not in (entry.get("files") or []):
                continue
            version = parse_version(str(entry.get("version", "")))
            if version is not None:
                releases.append((version, bool(entry.get("lts"))))
        if wanted is None:
            pool = [item for item in releases if item[1]] if request.alias == "lts" else releases
        else:
            pool = [item for item in releases if wanted.allows(item[0])]
            is_range = re.search(r"[<>|^~ ]", request.spec.strip()) is not None
            lts = [item for item in pool if item[1]]
            if is_range and lts:
                pool = lts
        if not pool:
            raise DeploymentError(
                f"nodejs.org publishes no release of {request.text} for {platform_name}",
                details="Check the version with: https://nodejs.org/dist/index.json",
            )
        return str(max(pool)[0])

    def _install_node(self, request: RuntimeRequest) -> InstalledRuntime:
        """
        Download, verify and unpack a Node release.

        Args:
            request: The request.

        Returns:
            The installed runtime.

        Raises:
            DeploymentError: Nothing satisfies it, the download failed, or its
                SHA-256 is not the one nodejs.org publishes for it.
        """
        platform_name = self._node_platform()
        version = self._node_version(request, platform_name)
        final = self.root / "node" / version
        if (final / "bin" / "node").exists():
            return InstalledRuntime("node", version, final)
        runtime = InstalledRuntime("node", version, final)
        if is_rehearsal():
            self.logger.substep(f"Would install Node {version} into {final}")
            return runtime

        base = f"{NODE_DIST}/v{version}"
        archive = f"node-v{version}-{platform_name}.tar.xz"
        try:
            sums = self._fetch(f"{base}/SHASUMS256.txt", MAX_INDEX_BYTES).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeploymentError("nodejs.org's SHASUMS256.txt is not text") from exc
        digest = _checksum_for(sums, archive)
        if digest is None:
            raise DeploymentError(
                f"nodejs.org's SHASUMS256.txt for v{version} does not list {archive}",
                details="That release has no build for this machine.",
            )
        self.logger.substep(f"Installing Node {version} ({archive}, sha256 {digest[:12]}...)")
        staging = self._stage("node")
        try:
            # download_archive hashes the file against the digest before it
            # unpacks a single member, and unpacks with the checks every
            # archive of Noust gets (no links out, no devices, no setuid).
            self.source_manager.download_archive(f"{base}/{archive}#sha256={digest}", staging)
            self._prove(staging / "bin" / "node", f"v{version}")
        except NoustError:
            self._discard(staging)
            raise
        self._place(staging, final)
        return runtime

    def _prove(self, program: Path, expected: str) -> None:
        """
        Run a freshly unpacked interpreter, so a broken installation never stays.

        Args:
            program: The interpreter.
            expected: What its version command must print.

        Raises:
            DeploymentError: It does not run, or is not the version it should be.
        """
        if not program.exists():
            raise DeploymentError(
                f"The archive holds no {program.name}",
                details=f"Expected {program}. Nothing was installed.",
            )
        result = self.runner.run([str(program), "--version"], timeout=VERSION_TIMEOUT)
        if not result.success or expected not in result.output:
            raise DeploymentError(
                f"{program.name} does not run, or is not {expected}",
                details=_said(result) or "It printed nothing. Nothing was installed.",
            )

    # Python ----------------------------------------------------------------

    def _install_python(self, request: RuntimeRequest) -> InstalledRuntime:
        """
        Install a CPython with uv.

        Args:
            request: The request.

        Returns:
            The installed runtime.

        Raises:
            DeploymentError: uv is not installed, or could not install it.
        """
        if not self.runner.exists("uv"):
            raise DeploymentError(
                f"{request.text} needs uv, which is not installed",
                details="Install uv (https://docs.astral.sh/uv/: pipx install uv, or "
                "curl -LsSf https://astral.sh/uv/install.sh | sh as root, or your distribution's "
                f"package), then run: noust runtime install {request.text}. Or install "
                f"python{request.spec} with the system's package manager and deploy again.",
            )
        # An alias names no version: the newest Python 3 uv has.
        wanted = "3" if request.alias is not None else request.spec
        if is_rehearsal():
            self.logger.substep(f"Would install Python {wanted} with uv into {self.root}/python")
            return InstalledRuntime("python", wanted, self.root / "python" / wanted)
        staging = self._stage("python")
        self.logger.substep(f"Installing Python {wanted} with uv")
        result = self.runner.run(
            [
                "uv",
                "python",
                "install",
                wanted,
                "--install-dir",
                str(staging),
                "--no-bin",
                "--no-config",
            ],
            cwd=staging,
            env={
                "UV_PYTHON_INSTALL_DIR": str(staging),
                "UV_PYTHON_BIN_DIR": str(staging / ".bin"),
                "UV_NO_PROGRESS": "1",
            },
            timeout=UV_TIMEOUT,
        )
        if not result.success:
            self._discard(staging)
            raise DeploymentError(
                f"uv could not install Python {wanted}",
                details=_said(result) or "uv printed nothing.",
            )
        home = next(
            (
                entry
                for entry in sorted(staging.iterdir())
                if _CPYTHON_DIR.fullmatch(entry.name) and (entry / "bin" / "python3").exists()
            ),
            None,
        )
        match = _CPYTHON_DIR.fullmatch(home.name) if home is not None else None
        if home is None or match is None:
            self._discard(staging)
            raise DeploymentError(
                f"uv installed nothing under {staging}", details=_said(result) or "No output."
            )
        version = match.group(1)
        final = self.root / "python" / version
        if self._python_home(final) is not None:
            self._discard(staging)
        else:
            try:
                self._prove(home / "bin" / "python3", version)
            except NoustError:
                self._discard(staging)
                raise
            self._tidy(staging, keep=home)
            self._place(staging, final)
        installed = self._python_home(final)
        return InstalledRuntime("python", version, installed or final)

    # PHP -------------------------------------------------------------------

    def _install_php(self, request: RuntimeRequest) -> InstalledRuntime:
        """
        Install a PHP version with the distribution's package manager.

        Args:
            request: The request, whose spec names a ``major.minor``.

        Returns:
            The installed runtime.

        Raises:
            DeploymentError: The spec does not name a minor, the package
                manager is not apt, or the package is not in its repositories.
        """
        spec = request.spec
        if not _MINOR_VERSION.fullmatch(spec):
            installed = ", ".join(self._php_versions()) or "none"
            raise DeploymentError(
                f"{request.text} does not name an installed PHP, and only a major and a minor "
                f"(php@8.3) can be installed (installed: {installed})",
                details="Pin the minor version: noust runtime install php@8.3",
            )
        from noust.core.dependencies import PHP_FPM_DEPENDENCY

        family, _program = detect_family(self.runner)
        if family is not PackageFamily.APT:
            raise DeploymentError(
                f"PHP {spec} is not installed, and Noust installs a PHP version only with apt",
                details=_php_by_hand(spec, family),
            )
        packages = _PHP_PACKAGE_STEM.sub(
            f"php{spec}-", PHP_FPM_DEPENDENCY.install_apt or ""
        ).split()
        if is_rehearsal():
            self.logger.substep(f"Would install: apt-get install -y {' '.join(packages)}")
            return self._php_runtime(spec)
        self.logger.substep(f"Installing PHP {spec}: apt-get install -y {' '.join(packages)}")
        result = self.runner.run(
            ["apt-get", "install", "-y", *packages],
            env={"DEBIAN_FRONTEND": "noninteractive"},
            timeout=PACKAGE_TIMEOUT,
        )
        if not result.success:
            raise DeploymentError(
                f"apt could not install PHP {spec}",
                details=f"{_said(result)}\n\n{_php_by_hand(spec, family)}".strip(),
            )
        if spec not in self._php_versions():
            raise DeploymentError(
                f"apt installed packages, but PHP-FPM {spec} is not there",
                details=_php_by_hand(spec, family),
            )
        runtime = self._php_runtime(spec)
        self._link_php_cli(runtime)
        return runtime

    def _link_php_cli(self, runtime: InstalledRuntime) -> None:
        """
        Keep ``php`` in the runtime's ``bin`` pointing at that version's command line.

        Composer and ``php artisan`` are run through ``PATH``; without this a
        server with two PHP versions asks the wrong one whether the
        application's dependencies fit.

        Args:
            runtime: A PHP runtime.
        """
        target = Path("/usr/bin") / f"php{runtime.version}"
        if not (self.php_root / "usr" / "bin" / f"php{runtime.version}").exists():
            return
        link = runtime.bin_dir / "php"
        if link.is_symlink() and os.readlink(link) == str(target):
            return
        self.fs.make_dir(runtime.bin_dir, mode=0o755, parents=True)
        self.fs.symlink(target, link)

    # Removing ----------------------------------------------------------------

    def remove(self, language: str, version: str) -> None:
        """
        Remove an installed runtime, unless an application runs on it.

        The guard is here, not in the commands that call it (rule 4): an
        application whose unit names the runtime's interpreter would fail to
        start at its next restart.

        Args:
            language: ``node`` or ``python``.
            version: The full version.

        Raises:
            DeploymentError: Nothing like that is installed, PHP (which is the
                distribution's), or an application's unit runs on it.
            ValidationError: Not a version, or not a language.
        """
        from noust.core import app_build_store

        if language == "php":
            raise DeploymentError(
                "PHP is the distribution's package, not something Noust installed",
                details="Remove it with the package manager: apt remove php"
                f"{version}-fpm php{version}-cli",
            )
        if language not in ("node", "python"):
            raise ValidationError(
                f"{language!r} is not a runtime Noust installs",
                details="They are node and python (PHP is the distribution's).",
                field="language",
            )
        if not _FULL_VERSION.fullmatch(version):
            raise ValidationError(
                f"{version!r} is not a full version",
                details="Write it as 22.11.0.",
                field="version",
            )
        target = self.root / language / version
        if not target.is_dir() or target.is_symlink():
            raise DeploymentError(
                f"{language} {version} is not installed",
                details="See what is: noust runtime list",
            )
        users = app_build_store.runtime_users(language, version)
        if users:
            raise DeploymentError(
                f"{language} {version} is what {', '.join(users)} runs on",
                details="Pin them to another version first (noust app build set DOMAIN "
                f"--runtime {language}@<version>) and update them, then remove this one.",
            )
        self.fs.remove_tree(target)
        audit.record(
            "runtimes.remove", target=f"runtime:{language}@{version}", details={"home": str(target)}
        )


def _said(result: CommandResult) -> str:
    """
    Gather what a failed command said, on whichever stream.

    Args:
        result: The command's outcome.

    Returns:
        Its standard error and its standard output, stripped.
    """
    parts = [part.strip() for part in (result.stderr, result.stdout) if part and part.strip()]
    return "\n".join(parts)


def _checksum_for(sums: str, filename: str) -> str | None:
    """
    Find a file's SHA-256 in a ``SHASUMS256.txt``.

    Args:
        sums: The file's text: ``<hex>  <name>`` per line.
        filename: The archive's name.

    Returns:
        The digest, lowercase, or None when the file is not listed.
    """
    for line in sums.splitlines():
        parts = line.split()
        if (
            len(parts) == 2
            and parts[1].lstrip("*") == filename
            and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0])
        ):
            return parts[0].lower()
    return None


def _php_by_hand(version: str, family: PackageFamily) -> str:
    """
    Say how to get a PHP version this machine's package manager does not carry.

    Args:
        version: ``major.minor``.
        family: The machine's package manager.

    Returns:
        Instructions, for ``details``.
    """
    if family is PackageFamily.APT:
        return (
            f"If the distribution does not carry php{version}, add the repository that does "
            "(Ubuntu: add-apt-repository ppa:ondrej/php; Debian: https://packages.sury.org/php/) "
            f"and run: apt-get install php{version}-fpm php{version}-cli"
        )
    if family is PackageFamily.DNF:
        return (
            "Fedora and RHEL carry one PHP at a time. Enable the version from Remi's "
            f"repository (https://rpms.remirepo.net/): dnf module reset php; dnf module enable "
            f"php:remi-{version}; dnf install php-fpm php-cli"
        )
    if family is PackageFamily.ZYPPER:
        return f"openSUSE: zypper install php{version.split('.')[0]}-fpm (one PHP per major)"
    return f"Install PHP {version} with your system's package manager."


def _describe(runtime: InstalledRuntime) -> str:
    """
    Say a runtime in a log line.

    Args:
        runtime: The runtime.

    Returns:
        ``node 22.11.0 (/opt/noust/runtimes/node/22.11.0)``.
    """
    return f"{runtime.language} {runtime.version} ({runtime.home})"


def _version(text: str) -> Version:
    """
    Read a version a runtime was installed as.

    Args:
        text: ``22.11.0`` or ``8.3``.

    Returns:
        The version.

    Raises:
        DeploymentError: Not a version, which only a corrupt directory name is.
    """
    parsed = parse_version(text)
    if parsed is None:
        raise DeploymentError(f"{text!r} is not a version of an installed runtime")
    return parsed


def _key(text: str) -> tuple[int, ...]:
    """
    Order version texts numerically.

    Args:
        text: ``22.11.0`` or ``8.3``.

    Returns:
        The numbers, for sorting.
    """
    parsed = parse_version(text)
    return (parsed.major, parsed.minor, parsed.patch) if parsed is not None else (0, 0, 0)


__all__ = [
    "InstalledRuntime",
    "RuntimeChoice",
    "RuntimeManager",
]
