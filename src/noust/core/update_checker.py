# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Update checker for Noust.

Checks for new versions in the background without blocking the user's command.

"A new version is available" is only true once the package manager this
installation upgrades with can install it. A GitHub release exists as soon as
its tag is pushed; the apt and rpm packages arrive from OBS fifteen to thirty
minutes later, PyPI a few minutes later. Announcing the GitHub release sent
operators to an upgrade that did nothing. So a check reads two versions:

- the *installable* one, from the source this installation upgrades from
  (:func:`noust.core.package_index.offer`), which is what an
  update is announced for;
- the *published* one, the latest GitHub release.

and reports one of four states (:class:`VersionCheck.state`):
``update_available`` when the installable version is newer than the one
running, ``on_the_way`` when only the published one is (the package is still
being built: nothing to do yet), ``up_to_date`` otherwise.

A package manager (apt, dnf, yum, zypper) installs what its *local index*
lists, and the operating system refreshes that index on its own schedule,
about once a day. So for those the check also reads the index, and an update
is only ``update_available`` once the index lists it. A release the
repository serves (or, when the repository cannot be read, that GitHub has)
and the index has not seen yet is ``index_behind``: the fix is refreshing the
index (``noust server updates refresh``, which then forgets this check), and
the upgrade is offered after that.

The result is announced on stderr, and only to a person: never under
``--json`` and never when either stream is not a terminal. It was printed to
stdout after every command, which appended a banner to the JSON document of
``noust app list --json | jq`` and broke the parse.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import logging
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

import noust
from noust import __version__
from noust.core import package_index, paths
from noust.core.exceptions import NoustError

logger = logging.getLogger(__name__)

#: The dotted key that turns the check off. An operator on an airgapped or
#: tightly firewalled server has no use for a request that can only time out,
#: repeated on every command.
CONFIG_KEY = "updates.check"

UpdateState = Literal["up_to_date", "update_available", "on_the_way", "index_behind"]

RELEASES_URL = "https://github.com/Perkybeet/noust/releases/tag/v{version}"

#: What refreshes the package index, in the CLI; the console's
#: ``POST /api/server/updates/refresh`` runs the same manager method.
REFRESH_COMMAND = "noust server updates refresh"


def web_installed() -> bool:
    """
    Whether this installation has the console's dependencies.

    An upgrade of such an installation names the ``web`` extra, so that what
    a new release adds to it is installed too.

    Returns:
        True when FastAPI can be imported by this interpreter.
    """
    return importlib.util.find_spec("fastapi") is not None


class UpdateCheckInProgress(Exception):
    """
    Raised by :meth:`UpdateChecker.check` when it declines to fetch.

    Another call already holds the check's lock and is talking to the
    network, and there is no cached result - not even a stale one - this
    call can answer with instead. Concurrent callers otherwise each opened
    their own connection to the same repository a slow or trickling server
    was already holding open for someone else, piling every one of them up
    behind it; a caller that catches this - :func:`noust.web.api.system.check_version`
    does - answers "checking" rather than waiting.
    """


def _location() -> str:
    """
    Where the running Noust is installed: the installation's fingerprint.

    A different installation method puts the package somewhere else (a pipx
    venv, ``/usr/lib/python3/dist-packages``, ``/usr/local/lib/...``), so a
    cached answer is only reused by the installation that produced it.

    Returns:
        The resolved directory of the ``noust`` package.
    """
    return str(Path(noust.__file__).resolve().parent)


@dataclass(frozen=True)
class VersionCheck:
    """
    What one update check found.

    Attributes:
        current: The version running when the check was made.
        location: :func:`_location` when the check was made.
        method: How Noust was installed (see
            :meth:`UpdateChecker._detect_installation_method`).
        installable: The newest version the installation's own source
            offers, None when it could not be read.
        published: The latest GitHub release, None when it could not be read.
        checked_at: When the check was made, as a Unix timestamp.
        noted: Whether the "on the way" note has been shown for this check,
            so it is shown at most once per cache period.
        indexed: For a package manager, the newest version this server's
            package index lists; None otherwise, or when it did not answer.
        repository: The newest version the repository itself serves, read
            from it; None when it could not be read.
    """

    current: str
    location: str
    method: str
    installable: str | None
    published: str | None
    checked_at: float
    noted: bool = False
    indexed: str | None = None
    repository: str | None = None

    @property
    def state(self) -> UpdateState:
        """
        Whether there is anything to install.

        Returns:
            ``update_available`` when what the upgrade installs is newer than
            the running version - for a package manager, what its local index
            lists; ``index_behind`` when a package manager's repository serves
            a newer one its index has not seen, or the repository could not be
            read and GitHub has one; ``on_the_way`` when only the published
            release is newer (including when an installable version could not
            be read, so an unreadable source never claims an upgrade that may
            not exist); ``up_to_date`` otherwise.
        """
        newer = package_index.is_newer
        if self.method in package_index.INDEXED_METHODS:
            if newer(self.indexed, self.current):
                return "update_available"
            if newer(self.repository, self.current):
                return "index_behind"
            if newer(self.published, self.current):
                # A repository that was read and has not got it is still
                # building the package; one that could not be read may well
                # have it, and only a refresh of the index can tell.
                return "on_the_way" if self.repository is not None else "index_behind"
            return "up_to_date"
        if newer(self.installable, self.current):
            return "update_available"
        if newer(self.published, self.current):
            return "on_the_way"
        return "up_to_date"

    @property
    def announced_version(self) -> str | None:
        """The version the state is about: the one to install, to refresh for, or on the way."""
        state = self.state
        if state == "update_available":
            if self.method in package_index.INDEXED_METHODS:
                return self.indexed
            return self.installable
        if state == "index_behind":
            if package_index.is_newer(self.repository, self.current):
                return self.repository
            return self.published
        if state == "on_the_way":
            return self.published
        return None

    @property
    def update_command(self) -> str:
        """The command that upgrades this installation."""
        return UpdateChecker._get_update_command(self.method)

    @property
    def refresh_command(self) -> str | None:
        """The command that refreshes the package index, while it is behind."""
        return REFRESH_COMMAND if self.state == "index_behind" else None

    @property
    def release_url(self) -> str | None:
        """The release notes of :attr:`announced_version`, when there is one."""
        version = self.announced_version
        return RELEASES_URL.format(version=version) if version else None

    def to_cache(self) -> dict[str, Any]:
        """The JSON-serialisable form written to the cache file."""
        return asdict(self)

    @classmethod
    def from_cache(cls, data: dict[str, Any]) -> VersionCheck | None:
        """
        Rebuild a check from the cache file.

        Args:
            data: The cache file's object.

        Returns:
            The check, or None when the file is from an older release or
            carries values of the wrong type.
        """
        checked_at = data.get("checked_at")
        if not isinstance(checked_at, (int, float)) or isinstance(checked_at, bool):
            return None
        texts = {key: data.get(key) for key in ("current", "location", "method")}
        optional = {
            key: data.get(key) for key in ("installable", "published", "indexed", "repository")
        }
        if not all(isinstance(value, str) for value in texts.values()):
            return None
        if not all(value is None or isinstance(value, str) for value in optional.values()):
            return None
        return cls(
            current=str(texts["current"]),
            location=str(texts["location"]),
            method=str(texts["method"]),
            installable=optional["installable"],
            published=optional["published"],
            checked_at=float(checked_at),
            noted=bool(data.get("noted", False)),
            indexed=optional["indexed"],
            repository=optional["repository"],
        )


class UpdateChecker:
    """Check for Noust updates where this installation gets them from."""

    CACHE_FILE = paths.user_cache_dir() / "version_check.json"
    CHECK_INTERVAL = 300  # 5 minutes
    DPKG_STATUS = Path("/var/lib/dpkg/status")

    # Background check state
    _check_thread: threading.Thread | None = None
    _pending: VersionCheck | None = None

    #: Held by whichever call is actually talking to the network in
    #: :meth:`check`, so a burst of concurrent callers - several console tabs
    #: hitting ``GET /api/system/version`` at once - never opens more than one
    #: connection to the same repository between them.
    _check_lock: threading.Lock = threading.Lock()

    #: Bumped by :meth:`forget`. A check that was already reading when the
    #: index was refreshed carries the old index's answer, and must not write
    #: it back over the forgetting.
    _generation: int = 0

    @classmethod
    def enabled(cls) -> bool:
        """
        Report whether the operator has left the update check on.

        Reads the configuration rather than caching the answer: ``noust
        config set updates.check false`` must take effect on the very next
        command, not the next restart of a long-lived process.

        Returns:
            True unless ``updates.check`` is set to false. A configuration
            that cannot be read must not turn a cosmetic check into a
            command that fails to start, so this defaults to enabled.
        """
        try:
            from noust.core.config import Config

            return bool(Config().get(CONFIG_KEY, True))
        except OSError as exc:
            # Config._load_config already contains its own OSError/YAMLError
            # handling for a bad file; this is the belt for the one thing
            # left outside it - Path.exists() propagating a permission or
            # I/O error from an unreachable config directory (an NFS mount
            # gone away, for instance) instead of returning False.
            logger.debug(
                "Could not read %s: %s; treating the update check as enabled", CONFIG_KEY, exc
            )
            return True

    @staticmethod
    def should_announce(argv: Sequence[str]) -> bool:
        """
        Report whether this invocation may carry the update banner at all.

        Args:
            argv: The command line, without the program name.

        Returns:
            False under ``--json``, whose output is a document and nothing
            else, and when stdout or stderr is not a terminal: output being
            piped or captured is read by a program, and an ANSI-coloured
            banner is noise in a log file. True otherwise.
        """
        if "--json" in argv:
            return False
        return all(
            bool(getattr(stream, "isatty", lambda: False)()) for stream in (sys.stdout, sys.stderr)
        )

    # -- the check ----------------------------------------------------------

    @classmethod
    def check(cls) -> VersionCheck:
        """
        Report what can be installed, from the cache while it is fresh.

        The one implementation behind the CLI banner and ``GET
        /api/system/version``. Single-flight: when the cache is stale and
        another call is already fetching, this one does not open a second
        connection to the same repository beside it - see
        :attr:`_check_lock` and :class:`UpdateCheckInProgress`.

        Returns:
            The cached check when it is younger than :attr:`CHECK_INTERVAL`
            and was made by this same installation and version; a new one,
            written to the cache, otherwise. A new check is written even when
            nothing could be read, so a server without a route out does not
            retry on every command.

        Raises:
            UpdateCheckInProgress: Another call is fetching right now, and
                there is no cached result - not even a stale one - to answer
                with instead.
        """
        cached = cls._cached_check()
        if cached is not None:
            return cached

        if not cls._check_lock.acquire(blocking=False):
            # Answering from the disk cache - stale or not - beats every
            # concurrent caller opening its own connection to the same slow
            # or trickling repository the one call already in flight is
            # reading from.
            existing = VersionCheck.from_cache(cls._read_cache() or {})
            if existing is not None and cls._same_installation(existing):
                return existing
            raise UpdateCheckInProgress("An update check is already in progress")

        try:
            # The call that held the lock before this one may have just
            # finished and written a fresh cache while this one waited.
            cached = cls._cached_check()
            if cached is not None:
                return cached

            previous = cls._read_cache()
            stale = VersionCheck.from_cache(previous) if previous else None
            # Detection runs processes; the method only changes with the
            # installation, which the location already fingerprints.
            if stale is not None and cls._same_installation(stale):
                method = stale.method
            else:
                method = cls._detect_installation_method()

            published: list[str | None] = [None]

            def read_published() -> None:
                published[0] = cls._fetch_published_version()

            generation = cls._generation
            # The two sources are independent; reading them side by side keeps
            # the check inside the moment a short command gives it.
            github = threading.Thread(target=read_published, daemon=True)
            github.start()
            offer = cls._fetch_offer(method)
            github.join(timeout=package_index.TIMEOUT * 3)

            result = VersionCheck(
                current=__version__,
                location=_location(),
                method=method,
                installable=offer.installable,
                published=published[0],
                checked_at=time.time(),
                indexed=offer.index,
                repository=offer.repository,
            )
            if generation == cls._generation:
                cls._write_cache(result.to_cache())
            return result
        finally:
            cls._check_lock.release()

    @classmethod
    def _fetch_published_version(cls) -> str | None:
        """
        Read the latest GitHub release.

        Returns:
            Its version, or None when it cannot be read.
        """
        return package_index.github_latest()

    @classmethod
    def _fetch_offer(cls, method: str) -> package_index.Offer:
        """
        Read what the installation's own source offers, and its local index.

        Args:
            method: The installation method.

        Returns:
            The repository's version and the index's, each None when unread.
        """
        from noust.core.runner import get_runner

        return package_index.offer(method, get_runner())

    @classmethod
    def forget(cls) -> None:
        """
        Drop the cached check, so the next one reads every source again.

        Called when the package index is refreshed: the cached answer may say
        the index has not seen a release it now lists.
        """
        cls._generation += 1
        cls._pending = None
        from noust.core.fs import get_fs

        try:
            get_fs().remove(cls.CACHE_FILE, missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove %s: %s", cls.CACHE_FILE, exc)

    # -- the CLI banner -----------------------------------------------------

    @classmethod
    def start_background_check(cls) -> None:
        """
        Start the update check in a background thread.

        Call this at the beginning of command execution. A fresh cache is
        answered synchronously; otherwise the check runs in parallel while
        the command executes.
        """
        cls._pending = None
        if not cls.enabled():
            return

        try:
            cached = cls._cached_check()
            if cached is not None:
                cls._pending = cached
                return

            cls._check_thread = threading.Thread(target=cls._background_check, daemon=True)
            cls._check_thread.start()
        except RuntimeError as exc:
            # "can't start new thread": the check is cosmetic, the command is not.
            logger.debug("Could not start the update check: %s", exc)

    @classmethod
    def _background_check(cls) -> None:
        """Perform the actual update check (runs in background thread)."""
        generation = cls._generation
        try:
            check = cls.check()
            if generation == cls._generation:
                cls._pending = check
        except Exception as exc:
            # The top of a daemon thread: nothing above it would catch this,
            # and an uncaught exception there is printed over the command's
            # own output. It is the one error boundary here, and it logs.
            logger.debug("Background update check failed: %s", exc, exc_info=True)

    @classmethod
    def show_update_if_available(cls, timeout: float = 0.1) -> None:
        """
        Announce what the check found, if it is worth a line.

        Call this at the end of command execution. ``update_available`` is
        announced every time; ``on_the_way`` once per cache period, since
        there is nothing to do about it; ``up_to_date`` never.

        Args:
            timeout: Max seconds to wait for background check to complete.
        """
        if cls._check_thread and cls._check_thread.is_alive():
            cls._check_thread.join(timeout=timeout)

        check, cls._pending = cls._pending, None
        if check is None:
            return

        try:
            if check.state == "update_available":
                cls._show_update_message(check)
            elif check.state == "index_behind":
                cls._show_index_behind_message(check)
            elif check.state == "on_the_way" and not check.noted:
                cls._show_on_the_way_message(check)
                cls._write_cache(replace(check, noted=True).to_cache())
        except (OSError, UnicodeError, NoustError) as exc:
            # A reader that went away (`noust ... | head`), a terminal that
            # cannot encode the banner: neither may change the command's exit.
            logger.debug("Could not announce %s: %s", check.announced_version, exc)

    @classmethod
    def check_for_updates(cls) -> None:
        """
        Legacy method: Check for updates synchronously.

        Deprecated: Use start_background_check() and show_update_if_available() instead.
        """
        cls.start_background_check()
        if cls._check_thread:
            cls._check_thread.join(timeout=package_index.TIMEOUT * 4)
        cls.show_update_if_available(timeout=0)

    @classmethod
    def _show_update_message(cls, check: VersionCheck) -> None:
        """
        Display the update notification, on stderr.

        Args:
            check: A check whose state is ``update_available``.

        Raises:
            OSError: When stderr cannot be written.
            UnicodeError: When the terminal cannot encode the banner.
        """
        sys.stderr.write(
            f"\n\033[33m⚠  New version available: {check.announced_version} "
            f"(current: {check.current})\033[0m\n"
            f"\033[33m   Update with: {check.update_command}\033[0m\n"
            f"\033[33m   Release notes: {check.release_url}\033[0m\n\n"
        )
        sys.stderr.flush()

    @classmethod
    def _show_index_behind_message(cls, check: VersionCheck) -> None:
        """
        Say that a release exists that this server's package index has not seen.

        Args:
            check: A check whose state is ``index_behind``.

        Raises:
            OSError: When stderr cannot be written.
            UnicodeError: When the terminal cannot encode the note.
        """
        sys.stderr.write(
            f"\n\033[33mNoust {check.announced_version} is published; this server's package "
            "index has not seen it yet.\033[0m\n"
            f"\033[33m   Refresh it with: {check.refresh_command}\033[0m\n"
            f"\033[33m   Release notes: {check.release_url}\033[0m\n\n"
        )
        sys.stderr.flush()

    @classmethod
    def _show_on_the_way_message(cls, check: VersionCheck) -> None:
        """
        Say, once, that a release is published but not yet installable here.

        Args:
            check: A check whose state is ``on_the_way``.

        Raises:
            OSError: When stderr cannot be written.
            UnicodeError: When the terminal cannot encode the note.
        """
        sys.stderr.write(
            f"\nNoust {check.published} is published; the package for this system is not "
            "available yet (usually 15-30 minutes). Nothing to do now.\n\n"
        )
        sys.stderr.flush()

    # -- the cache ----------------------------------------------------------

    @classmethod
    def _same_installation(cls, check: VersionCheck) -> bool:
        """Report whether a check was made by the Noust that is running now."""
        return check.current == __version__ and check.location == _location()

    @classmethod
    def _cached_check(cls) -> VersionCheck | None:
        """
        Read the cached check if it still answers for this installation.

        Returns:
            The check, or None when there is none, it is older than
            :attr:`CHECK_INTERVAL`, or it was made by another version or
            installation method (an upgrade, a move from pip to apt).
        """
        data = cls._read_cache()
        check = VersionCheck.from_cache(data) if data else None
        if check is None or not cls._same_installation(check):
            return None
        if time.time() - check.checked_at >= cls.CHECK_INTERVAL:
            return None
        return check

    @classmethod
    def _is_cache_valid(cls) -> bool:
        """
        Check if the cache still answers for this installation.

        Returns:
            True if the cache is valid and should be used.
        """
        return cls._cached_check() is not None

    @classmethod
    def _read_cache(cls) -> dict | None:
        """
        Read cached version check data.

        Returns:
            Cached data dict or None if failed.
        """
        try:
            if not cls.CACHE_FILE.exists():
                return None
            data = json.loads(cls.CACHE_FILE.read_text())
        except (OSError, ValueError) as exc:
            logger.debug("Could not read %s: %s", cls.CACHE_FILE, exc)
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def _write_cache(cls, data: dict) -> None:
        """
        Write version check data to cache.

        Args:
            data: Dictionary to cache.
        """
        try:
            cls.CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            cls.CACHE_FILE.write_text(json.dumps(data, indent=2))
        except OSError as exc:
            # Permission errors, disk full, etc.
            logger.debug("Could not write %s: %s", cls.CACHE_FILE, exc)

    @classmethod
    def _is_newer_version(cls, remote: str, local: str) -> bool:
        """
        Compare semantic versions.

        Args:
            remote: Remote version string (e.g., "0.13.11").
            local: Local version string (e.g., "0.13.11").

        Returns:
            True if remote is newer than local.
        """
        return package_index.is_newer(remote, local)

    # -- installation method ------------------------------------------------

    @classmethod
    def _detect_installation_method(cls) -> str:
        """
        Detect how the running Noust was installed.

        Returns:
            Installation method: 'pipx', 'apt', 'dnf', 'yum', 'zypper',
            'source', 'pip', or 'unknown'.
        """
        from noust.core.runner import get_runner

        runner = get_runner()

        # Every probe is short: a package manager that does not answer in two
        # seconds is not going to, and this runs beside the command the user
        # actually asked for.
        probe = 2

        # The interpreter running this is the pipx venv's own: a pipx venv
        # merely existing on the machine says nothing about which Noust runs.
        prefix = Path(sys.prefix)
        if "pipx" in prefix.parts and prefix.name in package_index.PYPI_DISTRIBUTIONS:
            return "pipx"

        location = _location()
        # A distribution package lives under /usr/lib; pip, even run as root
        # on the same machine, installs under /usr/local or a venv. Without
        # this, a pip-installed Noust on a machine that once had the package
        # was offered the package manager's upgrade, which changes nothing.
        from_system = location.startswith(("/usr/lib/", "/usr/lib64/", "/usr/share/"))
        if from_system:
            if cls.DPKG_STATUS.exists():
                # The package is noust from 3.0, wasm before: either one
                # installed means apt is how this machine upgrades.
                for package in package_index.DEB_PACKAGES:
                    status = runner.run(
                        ["dpkg-query", "-W", "-f=${Status}", package], timeout=probe
                    )
                    if status.success and "install ok installed" in status.stdout:
                        return "apt"
            if runner.exists("rpm") and any(
                runner.run(["rpm", "-q", package], timeout=probe)
                for package in package_index.RPM_PACKAGES
            ):
                for manager in ("zypper", "dnf", "yum"):
                    if runner.exists(manager):
                        return manager

        if (Path(location).parent.parent / ".git").exists():
            return "source"
        distribution = None
        for name in package_index.PYPI_DISTRIBUTIONS:
            try:
                distribution = importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                continue
            break
        if distribution is None:
            return "unknown"
        direct_url = distribution.read_text("direct_url.json")
        if direct_url:
            try:
                editable = json.loads(direct_url).get("dir_info", {}).get("editable", False)
            except (ValueError, AttributeError):
                editable = False
            if editable:
                return "source"
        return "pip"

    @classmethod
    def _get_update_command(cls, method: str) -> str:
        """
        Get the appropriate update command for the installation method.

        Args:
            method: Installation method from _detect_installation_method.

        Returns:
            Update command string.
        """
        # Quoted: an unquoted [web] is a glob to zsh.
        spec = "'noust[web]'" if web_installed() else "noust"
        commands = {
            # Install, not only-upgrade: a machine that still has the
            # package under WASM's name (wasm, wasm-cli) gets noust this way.
            "pip": f"pip install --upgrade {spec}",
            "pipx": f"pipx install --force {spec}",
            "apt": "sudo apt update && sudo apt install noust",
            "dnf": "sudo dnf install --refresh noust",
            "yum": "sudo yum install noust",
            "zypper": "sudo zypper refresh && sudo zypper install noust",
            "source": "cd <noust-repo> && git pull && pip install -e .",
            "unknown": "pip install --upgrade noust  # or use your system package manager",
        }
        return commands.get(method, commands["unknown"])
