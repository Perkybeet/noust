# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Disks: which filesystems are real, what takes their space, and what can be freed.

Three rules shape this module, each from a way a disk page goes wrong:

- **A mount list is not the output of ``df``.** On a machine with Docker the
  same device is mounted a dozen times as bind mounts, and the pseudo
  filesystems (overlay, tmpfs, squashfs) are not places a person can run out of
  space. What is shown is one row per real device, with its inodes: a disk with
  ten percent free and no inodes left fails exactly like a full one.
- **Nothing measures a tree in a request.** ``du`` over a release tree with a
  ``node_modules`` in it takes a minute. Measuring is a job (``analyze``) with a
  deadline per path, the answer is kept, and the page says how old it is.
- **Cleaning is a closed list of actions, never a path.** No endpoint accepts a
  file name to delete. ``docker system prune`` is not among them (it takes
  volumes with it, and volumes are the databases), and ``docker image prune -a``
  is not either: it would delete the ``wasm-previous`` image of every Compose
  application, which is its way back.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noust.core.exceptions import DependencyError, ValidationError
from noust.core.fs import DryRunFileSystem, FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    ServerError,
    UnsupportedHostError,
)
from noust.managers.server.host import HostPaths, Platform, detect_platform
from noust.managers.server.pkg import backend_for

#: Filesystem types that are not somewhere a person runs out of space.
IGNORED_FILESYSTEMS = frozenset(
    {
        "overlay",
        "squashfs",
        "tmpfs",
        "devtmpfs",
        "ramfs",
        "proc",
        "sysfs",
        "cgroup",
        "cgroup2",
        "nsfs",
        "iso9660",
        "efivarfs",
        "fuse.snapfuse",
        "fuse.lxcfs",
        "fuse.portal",
        "fuse.gvfsd-fuse",
    }
)

#: A mount below one of these is Docker's, the snap store's or the kernel's.
IGNORED_PREFIXES = ("/var/lib/docker", "/run", "/snap", "/proc", "/sys", "/dev")

#: A filesystem this full, or with this few inodes left, is something to act on.
DISK_WARN_PERCENT = 85.0
DISK_CRITICAL_PERCENT = 95.0
INODE_WARN_PERCENT = 90.0

#: The size the journal is vacuumed to when the operator does not choose.
DEFAULT_JOURNAL_MB = 200

#: Deadline for measuring one path with ``du``.
DU_TIMEOUT = 120

#: Deadline for the quick probes (``journalctl --disk-usage``, ``docker system df``).
PROBE_TIMEOUT = 60

#: Deadline of a cleanup command. Pruning a large Docker cache takes minutes.
CLEANUP_TIMEOUT = 900

#: The tag Docker Compose applications keep their way back under. Nothing
#: unused-image related may remove it: no container runs it after an update,
#: which is exactly what makes it look unused.
PREVIOUS_TAG = "wasm-previous"

#: Every action a cleanup can be. The closed list is the guard: an endpoint
#: passes one of these keys and nothing else reaches a command.
CLEANUP_ACTIONS = (
    "journal",
    "pkg-cache",
    "docker-build-cache",
    "docker-dangling-images",
    "docker-image",
    "releases",
)

#: Actions that take something the operator may want back, and so need a yes.
NEEDS_CONFIRMATION = frozenset({"docker-build-cache", "docker-dangling-images", "docker-image"})

_JOURNAL_SIZE = re.compile(r"take up ([\d.]+)\s*([KMGTP]?)", re.IGNORECASE)
_DOCKER_SIZE = re.compile(r"^\s*([\d.]+)\s*([kKMGTP]?B)")
_IMAGE_ID = re.compile(r"^(sha256:)?[0-9a-f]{6,64}$")

_UNITS_1024 = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5}
_UNITS_1000 = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4, "PB": 1000**5}


@dataclass(frozen=True)
class Mount:
    """
    One real filesystem.

    Attributes:
        mount_point: Where it is mounted.
        device: The device.
        fstype: The filesystem type.
        total_bytes: Size.
        used_bytes: Used.
        free_bytes: Free to an ordinary user.
        percent_used: Used, as ``df`` reports it.
        inodes_total: Inodes the filesystem has; 0 where it has no fixed number (btrfs).
        inodes_free: Inodes left.
        inodes_percent: Inodes used, 0 where there is no fixed number.
        readonly: Mounted read-only.
        status: ``ok``, ``warn`` or ``critical``.
    """

    mount_point: str
    device: str
    fstype: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float
    inodes_total: int
    inodes_free: int
    inodes_percent: float
    readonly: bool
    status: str = "ok"

    def to_dict(self) -> dict[str, Any]:
        """
        Render the mount as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class Candidate:
    """
    Something that takes space and might be freed.

    Attributes:
        id: Stable identifier the console translates.
        size_bytes: How much it takes, None when it has not been measured.
        reclaimable_bytes: How much of it a cleanup would free, None when unknown.
        action: The cleanup action that frees it, None where the console only
            reports (databases, coredumps).
        detail: What it is, one sentence of facts (a path, a count).
        measured_at: When the size was measured, ISO 8601 UTC.
    """

    id: str
    size_bytes: int | None = None
    reclaimable_bytes: int | None = None
    action: str | None = None
    detail: str = ""
    measured_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the candidate as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass
class Analysis:
    """
    The result of measuring the known places, kept for a while.

    Attributes:
        measured_at: When the scan finished, ISO 8601 UTC.
        candidates: Every place measured.
        errors: Paths that could not be measured, with the reason, verbatim.
    """

    measured_at: str
    candidates: list[Candidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the analysis as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return {
            "measured_at": self.measured_at,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class CleanupPlan:
    """
    What a cleanup action would do, worked out without doing it.

    Attributes:
        action: The action.
        commands: The commands it would run, as text.
        effect: What it does, in a sentence.
        needs_confirmation: It takes something the operator may want back.
        items: What it would remove, when the action can list it.
    """

    action: str
    commands: tuple[str, ...]
    effect: str
    needs_confirmation: bool
    items: tuple[str, ...] = ()


@dataclass
class CleanupResult:
    """
    What a cleanup did.

    Attributes:
        action: The action.
        dry_run: Nothing was changed; this is what would have been.
        commands: The commands that ran, as text.
        freed_bytes: Space freed, when the tool says or it could be measured.
        removed: What was removed, when the action can name it.
        output: What the tools printed, verbatim.
    """

    action: str
    dry_run: bool
    commands: list[str] = field(default_factory=list)
    freed_bytes: int | None = None
    removed: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)


def parse_journal_size(text: str) -> int | None:
    """
    Read ``journalctl --disk-usage``.

    Args:
        text: ``Archived and active journals take up 753.0M in the file system.``

    Returns:
        The size in bytes, or None when the line is not that.
    """
    match = _JOURNAL_SIZE.search(text)
    if not match:
        return None
    return int(float(match.group(1)) * _UNITS_1024[match.group(2).upper()])


def parse_docker_size(text: str) -> int | None:
    """
    Read a size the way ``docker`` prints it.

    Args:
        text: ``29.98GB (63%)`` or ``310.2MB`` or ``0B``. Docker counts in
            powers of ten.

    Returns:
        The size in bytes, or None when it is not a size.
    """
    match = _DOCKER_SIZE.match(text)
    if not match:
        return None
    return int(float(match.group(1)) * _UNITS_1000[match.group(2).upper()])


def parse_docker_df(text: str) -> dict[str, dict[str, Any]]:
    """
    Read ``docker system df --format json``, one JSON object per line.

    Args:
        text: The output.

    Returns:
        For each ``Type`` (``Images``, ``Containers``, ``Local Volumes``,
        ``Build Cache``): ``size``, ``reclaimable`` in bytes and ``count``.
    """
    found: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or "Type" not in row:
            continue
        found[str(row["Type"])] = {
            "size": parse_docker_size(str(row.get("Size", ""))),
            "reclaimable": parse_docker_size(str(row.get("Reclaimable", ""))),
            "count": int(row["TotalCount"]) if str(row.get("TotalCount", "")).isdigit() else None,
        }
    return found


def parse_docker_images(text: str) -> list[dict[str, str]]:
    """
    Read ``docker image ls --format '{{json .}}'``.

    Args:
        text: The output, one JSON object per line.

    Returns:
        ``id``, ``repository``, ``tag``, ``size`` and ``containers`` for each image.
    """
    images = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("ID"):
            images.append(
                {
                    "id": str(row["ID"]),
                    "repository": str(row.get("Repository", "")),
                    "tag": str(row.get("Tag", "")),
                    "size": str(row.get("Size", "")),
                    "containers": str(row.get("Containers", "N/A")),
                }
            )
    return images


def _status(percent: float, inode_percent: float) -> str:
    """
    Judge a filesystem.

    Args:
        percent: Space used.
        inode_percent: Inodes used.

    Returns:
        ``critical``, ``warn`` or ``ok``.
    """
    if percent >= DISK_CRITICAL_PERCENT:
        return "critical"
    if percent >= DISK_WARN_PERCENT or inode_percent >= INODE_WARN_PERCENT:
        return "warn"
    return "ok"


def _psutil() -> Any:
    """
    Import psutil, turning its absence into an actionable error.

    Returns:
        The module.

    Raises:
        DependencyError: psutil is not installed.
    """
    try:
        import psutil
    except ImportError as exc:
        raise DependencyError(
            "psutil is not installed, so disks cannot be listed",
            details="Install the web extra: pip install 'noust[web]'.",
        ) from exc
    return psutil


class StorageManager:
    """Lists disks, measures what takes their space and cleans it, from a closed list."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
        host: HostPaths | None = None,
        platform: Platform | None = None,
        backups_usage: Callable[[], dict[str, Any]] | None = None,
        release_apps: Callable[[], list[Any]] | None = None,
        log_paths: Callable[[], Iterable[Path]] | None = None,
    ) -> None:
        """
        Args:
            runner: Command runner; the process-wide one when omitted.
            fs: Filesystem seam; the process-wide one when omitted.
            host: Where the system files are.
            platform: What the machine is; detected once when omitted.
            backups_usage: Returns how much the backups take, in the shape of
                ``BackupManager.get_storage_usage``; that one by default.
            release_apps: Returns the applications on the release layout; read
                from the store by default.
            log_paths: Returns the directories of Noust's own logs.
        """
        self._runner = runner
        self._fs = fs
        self.host = host or HostPaths()
        self._platform = platform
        self._backups_usage = backups_usage
        self._release_apps = release_apps
        self._log_paths = log_paths

    @property
    def runner(self) -> CommandRunner:
        """The runner in force right now."""
        return self._runner or get_runner()

    @property
    def fs(self) -> FileSystem:
        """The filesystem in force right now."""
        return self._fs or get_fs()

    @property
    def platform(self) -> Platform:
        """What the machine is, detected on first use."""
        if self._platform is None:
            self._platform = detect_platform(self._runner, self.host)
        return self._platform

    # -- mounts ---------------------------------------------------------------

    def mounts(self) -> list[Mount]:
        """
        List the real filesystems, one row per device.

        Returns:
            Mounts, the fullest first. A device mounted several times (bind
            mounts) appears once, at its shortest mount point.

        Raises:
            DependencyError: psutil is not installed.
        """
        psutil = _psutil()
        chosen: dict[str, Any] = {}
        for partition in psutil.disk_partitions(all=True):
            if partition.fstype in IGNORED_FILESYSTEMS or not partition.device.startswith("/"):
                continue
            if partition.mountpoint.startswith(IGNORED_PREFIXES):
                continue
            known = chosen.get(partition.device)
            if known is None or len(partition.mountpoint) < len(known.mountpoint):
                chosen[partition.device] = partition

        mounts = []
        for partition in chosen.values():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                stat = os.statvfs(partition.mountpoint)
            except OSError:
                continue
            inode_percent = (
                round((stat.f_files - stat.f_ffree) / stat.f_files * 100, 1)
                if stat.f_files
                else 0.0
            )
            mounts.append(
                Mount(
                    mount_point=partition.mountpoint,
                    device=partition.device,
                    fstype=partition.fstype,
                    total_bytes=usage.total,
                    used_bytes=usage.used,
                    free_bytes=usage.free,
                    percent_used=usage.percent,
                    inodes_total=stat.f_files,
                    inodes_free=stat.f_ffree,
                    inodes_percent=inode_percent,
                    readonly="ro" in partition.opts.split(","),
                    status=_status(usage.percent, inode_percent),
                )
            )
        return sorted(mounts, key=lambda mount: mount.percent_used, reverse=True)

    def worst_mount(self, mounts: list[Mount] | None = None) -> Mount | None:
        """
        Find the filesystem in the worst state.

        Args:
            mounts: A listing computed a moment ago; computed when omitted.

        Returns:
            The fullest writable filesystem, or None when there are none. One
            mounted read-only cannot fill up, so it cannot be the answer.
        """
        writable = [m for m in (mounts if mounts is not None else self.mounts()) if not m.readonly]
        return max(writable, key=lambda mount: mount.percent_used, default=None)

    # -- quick candidates -------------------------------------------------------

    def _journal_candidate(self) -> Candidate | None:
        """
        Measure the journal.

        Returns:
            The candidate, or None when there is no journalctl or it did not answer.
        """
        if not self.runner.exists("journalctl"):
            return None
        result = self.runner.run(["journalctl", "--disk-usage"], timeout=PROBE_TIMEOUT)
        size = parse_journal_size(result.stdout) if result.success else None
        if size is None:
            return None
        target = DEFAULT_JOURNAL_MB * 1024**2
        return Candidate(
            id="journal",
            size_bytes=size,
            reclaimable_bytes=max(0, size - target),
            action="journal",
            detail=f"Reduce it to {DEFAULT_JOURNAL_MB} MB",
            measured_at=_now(),
        )

    def docker_df(self) -> dict[str, dict[str, Any]] | None:
        """
        Ask Docker what it holds.

        Returns:
            The table of :func:`parse_docker_df`, or None when Docker is not
            here or its daemon does not answer.
        """
        if not self.runner.exists("docker"):
            return None
        result = self.runner.run(
            ["docker", "system", "df", "--format", "json"], timeout=PROBE_TIMEOUT
        )
        return parse_docker_df(result.stdout) if result.success else None

    def _docker_candidates(self) -> list[Candidate]:
        """
        Measure what Docker holds and could give back.

        Returns:
            The build cache, the unused images and the volumes (volumes are
            reported and never offered: they are the databases).
        """
        table = self.docker_df()
        if not table:
            return []
        now = _now()
        found = []
        cache = table.get("Build Cache")
        if cache:
            found.append(
                Candidate(
                    "docker-build-cache",
                    cache["size"],
                    cache["reclaimable"],
                    "docker-build-cache",
                    "Build cache older than a week",
                    now,
                )
            )
        images = table.get("Images")
        if images:
            found.append(
                Candidate(
                    "docker-images",
                    images["size"],
                    images["reclaimable"],
                    None,
                    "Images no container uses. Review them one by one: the image a "
                    "Compose application keeps to roll back to looks unused too",
                    now,
                )
            )
        volumes = table.get("Local Volumes")
        if volumes:
            found.append(
                Candidate(
                    "docker-volumes",
                    volumes["size"],
                    None,
                    None,
                    "Volumes hold data (databases among it); Noust never removes them",
                    now,
                )
            )
        return found

    def usage(self, analysis: Analysis | None = None) -> dict[str, Any]:
        """
        Everything the storage page shows at once, without measuring any tree.

        Args:
            analysis: The last scan, when there is one; its candidates fill in
                what the quick probes do not measure.

        Returns:
            ``mounts``, ``worst`` (the fullest writable filesystem or None) and
            ``candidates`` (quick ones first, then the scan's).
        """
        mounts = self.mounts()
        candidates: list[Candidate] = []
        journal = self._journal_candidate()
        if journal is not None:
            candidates.append(journal)
        candidates += self._docker_candidates()
        seen = {candidate.id for candidate in candidates}
        if analysis is not None:
            candidates += [c for c in analysis.candidates if c.id not in seen]
        worst = self.worst_mount(mounts)
        return {
            "mounts": mounts,
            "worst": worst,
            "candidates": candidates,
            "analysis_at": analysis.measured_at if analysis else None,
        }

    # -- analysis -------------------------------------------------------------

    def measure(self, path: Path) -> int | None:
        """
        Measure a tree with ``du``, on the machine's spare time.

        Args:
            path: The tree.

        Returns:
            Bytes, or None when it does not exist or could not be measured in
            time. One filesystem only (``-x``): a mount inside is another disk's.
        """
        if not path.exists():
            return None
        result = self.runner.run(
            ["ionice", "-c3", "nice", "-n", "19", "du", "-sx", "-B1", "--", str(path)],
            timeout=DU_TIMEOUT,
        )
        if not result.success:
            return None
        first = result.stdout.split()
        return int(first[0]) if first and first[0].isdigit() else None

    def _measure_into(
        self,
        out: Analysis,
        identifier: str,
        path: Path,
        *,
        action: str | None = None,
        reclaim_all: bool = False,
        detail: str | None = None,
    ) -> None:
        """
        Measure one path and add it to an analysis, or say why not.

        Args:
            out: The analysis being built.
            identifier: The candidate's id.
            path: What to measure.
            action: The cleanup action that frees it, when there is one.
            reclaim_all: Everything measured is what the action frees.
            detail: What to say about it; the path by default.
        """
        if not path.exists():
            return
        size = self.measure(path)
        if size is None:
            out.errors.append(f"{path}: could not be measured in {DU_TIMEOUT}s")
            return
        out.candidates.append(
            Candidate(
                id=identifier,
                size_bytes=size,
                reclaimable_bytes=size if reclaim_all else None,
                action=action,
                detail=detail or str(path),
                measured_at=_now(),
            )
        )

    def analyze(self, on_step: Callable[[str], None] | None = None) -> Analysis:
        """
        Measure the known places that take space.

        Every path has its own deadline, so one slow tree costs its own answer
        and not the scan. It never walks ``/``.

        Args:
            on_step: Told what is being measured, so a job can show progress.

        Returns:
            The analysis.
        """
        report = on_step or (lambda text: None)
        out = Analysis(measured_at=_now())

        report("Package caches")
        for identifier, relative in (
            ("pkg-cache", "/var/cache/apt/archives"),
            ("pkg-cache-dnf", "/var/cache/dnf"),
            ("pkg-cache-libdnf5", "/var/cache/libdnf5"),
            ("pkg-cache-zypp", "/var/cache/zypp"),
        ):
            self._measure_into(
                out, identifier, self.host.at(relative), action="pkg-cache", reclaim_all=True
            )

        report("Releases of applications")
        self._analyze_releases(out)

        report("Backups")
        self._analyze_backups(out)

        report("Noust logs")
        for log_dir in self._log_paths() if self._log_paths else self._default_log_paths():
            self._measure_into(out, "noust-logs", log_dir, detail=str(log_dir))

        report("Crash dumps and temporary files")
        for identifier, relative in (
            ("crash", "/var/crash"),
            ("coredump", "/var/lib/systemd/coredump"),
            ("tmp", "/tmp"),  # noqa: S108 - measured, never touched
            ("var-tmp", "/var/tmp"),  # noqa: S108
        ):
            self._measure_into(out, identifier, self.host.at(relative))

        report("Database data directories")
        for identifier, relative in (
            ("postgresql-data", "/var/lib/postgresql"),
            ("mysql-data", "/var/lib/mysql"),
            ("mongodb-data", "/var/lib/mongodb"),
        ):
            self._measure_into(out, identifier, self.host.at(relative))
        return out

    def _default_log_paths(self) -> list[Path]:
        """
        Locate Noust's own log directories.

        Returns:
            The log directory, and the job and deploy logs beside the store.
        """
        from noust.core import paths
        from noust.core.store import get_store

        base = get_store().db_path.parent
        return [paths.log_dir(), base / "job-logs", base / "deploy-logs"]

    def _apps_on_releases(self) -> list[Any]:
        """
        List the applications on the release layout.

        Returns:
            Their store rows.
        """
        if self._release_apps is not None:
            return self._release_apps()
        from noust.core.store import get_store

        return [app for app in get_store().list_apps() if app.layout == "releases"]

    def _analyze_releases(self, out: Analysis) -> None:
        """
        Measure the releases each application would give back.

        Args:
            out: The analysis being built.
        """
        from noust.deployers.helpers.layout import app_root
        from noust.deployers.releases import ReleaseManager

        total = 0
        reclaimable = 0
        apps = 0
        for app in self._apps_on_releases():
            root = app_root(app)
            # What a prune would remove, worked out on a filesystem that
            # changes nothing: the one implementation of "which releases go".
            manager = ReleaseManager(root, fs=DryRunFileSystem(), runner=self.runner)
            for release in manager.list():
                size = self.measure(release.path) or 0
                total += size
            doomed = manager.prune(app.keep_releases)
            if doomed:
                apps += 1
            for release in doomed:
                reclaimable += self.measure(release.path) or 0
        if total:
            out.candidates.append(
                Candidate(
                    id="releases",
                    size_bytes=total,
                    reclaimable_bytes=reclaimable,
                    action="releases",
                    detail=f"{apps} application(s) keep more releases than they are set to",
                    measured_at=_now(),
                )
            )

    def _analyze_backups(self, out: Analysis) -> None:
        """
        Add the backups' size.

        Args:
            out: The analysis being built.
        """
        if self._backups_usage is not None:
            usage = self._backups_usage()
        else:
            from noust.managers.backup_manager import BackupManager

            usage = BackupManager(runner=self.runner, fs=self._fs).get_storage_usage()
        if usage.get("total_size_bytes"):
            out.candidates.append(
                Candidate(
                    id="backups",
                    size_bytes=int(usage["total_size_bytes"]),
                    detail=f"{usage.get('total_backups', 0)} backups; their retention is a backup setting",
                    measured_at=_now(),
                )
            )

    # -- cleanup --------------------------------------------------------------

    def docker_unused_images(self) -> list[dict[str, str]]:
        """
        List the images no container uses, without the ones that are a way back.

        Returns:
            The images, each with its id, name, tag and size. Images tagged
            ``wasm-previous`` are left out, and so are the ones a container
            (running or not) references.
        """
        if not self.runner.exists("docker"):
            return []
        images = self.runner.run(
            ["docker", "image", "ls", "--format", "{{json .}}"], timeout=PROBE_TIMEOUT
        )
        if not images.success:
            return []
        used = self.runner.run(
            ["docker", "ps", "-a", "--format", "{{.Image}}"], timeout=PROBE_TIMEOUT
        ).stdout.split()
        unused = []
        for image in parse_docker_images(images.stdout):
            if image["tag"] == PREVIOUS_TAG:
                continue
            names = {image["id"], image["repository"], f"{image['repository']}:{image['tag']}"}
            if names & set(used):
                continue
            unused.append(image)
        return unused

    def plan_cleanup(self, action: str, **params: Any) -> CleanupPlan:
        """
        Say what a cleanup would do, without doing it.

        Args:
            action: One of :data:`CLEANUP_ACTIONS`.
            **params: The action's parameters (``size_mb``, ``days``, ``target``).

        Returns:
            The plan.

        Raises:
            ValidationError: The action or a parameter is not valid.
            UnsupportedHostError: This machine has nothing to clean that way.
        """
        if action == "journal":
            return CleanupPlan(
                action,
                tuple(" ".join(argv) for argv in self._journal_commands(**params)),
                "Rotates the journal and deletes archived files beyond the limit. The active "
                "file is not touched.",
                False,
            )
        if action == "pkg-cache":
            argv = self._pkg_cache_argv()
            return CleanupPlan(
                action, (" ".join(argv),), "Deletes the downloaded package files.", False
            )
        if action == "docker-build-cache":
            argv = self._docker_build_cache_argv()
            return CleanupPlan(
                action,
                (" ".join(argv),),
                "Deletes build cache older than a week. The next build is slower for it.",
                True,
            )
        if action == "docker-dangling-images":
            argv = ["docker", "image", "prune", "-f"]
            return CleanupPlan(
                action,
                (" ".join(argv),),
                "Deletes images that have no name and no container. Named images are kept, "
                "and so is every wasm-previous image.",
                True,
            )
        if action == "docker-image":
            image = self._image_target(params.get("target"))
            argv = ["docker", "image", "rm", image]
            return CleanupPlan(
                action,
                (" ".join(argv),),
                f"Deletes the image {image}, without --force: Docker refuses if a container uses it.",
                True,
                items=(image,),
            )
        if action == "releases":
            doomed = self._doomed_releases()
            return CleanupPlan(
                action,
                (),
                "Deletes the releases each application keeps beyond its own limit. The active "
                "release and the one a rollback goes to always stay.",
                False,
                items=tuple(f"{domain}: {release}" for domain, release in doomed),
            )
        raise ValidationError(
            f"Unknown cleanup action: {action!r}", f"Use one of: {', '.join(CLEANUP_ACTIONS)}."
        )

    def cleanup(
        self,
        action: str,
        *,
        confirm: bool = False,
        on_line: Callable[[str], None] | None = None,
        **params: Any,
    ) -> CleanupResult:
        """
        Run one cleanup action.

        Args:
            action: One of :data:`CLEANUP_ACTIONS`.
            confirm: The caller has read what the action takes and accepts it.
            on_line: Receives what the tools print, verbatim.
            **params: The action's parameters.

        Returns:
            What was done.

        Raises:
            ValidationError: The action or a parameter is not valid.
            ConfirmationRequiredError: The action needs a yes and did not get one;
                nothing was changed.
            ServerError: A command failed, carrying its output.
        """
        plan = self.plan_cleanup(action, **params)
        if plan.needs_confirmation and not confirm:
            raise ConfirmationRequiredError(
                f"'{action}' deletes something you may want back",
                plan.effect,
                required={
                    "action": action,
                    "commands": list(plan.commands),
                    "items": list(plan.items),
                },
            )
        emit = on_line or (lambda line: None)
        result = CleanupResult(action=action, dry_run=isinstance(self.fs, DryRunFileSystem))

        if action == "journal":
            before = self._journal_candidate()
            for argv in self._journal_commands(**params):
                self._run(argv, emit, result)
            after = self._journal_candidate()
            if before and after:
                result.freed_bytes = max(0, (before.size_bytes or 0) - (after.size_bytes or 0))
        elif action == "pkg-cache":
            self._run(self._pkg_cache_argv(), emit, result)
        elif action == "docker-build-cache":
            self._run(self._docker_build_cache_argv(), emit, result)
            result.freed_bytes = _reclaimed(result.output)
        elif action == "docker-dangling-images":
            self._run(["docker", "image", "prune", "-f"], emit, result)
            result.freed_bytes = _reclaimed(result.output)
        elif action == "docker-image":
            self._run(["docker", "image", "rm", plan.items[0]], emit, result)
            result.removed = [plan.items[0]]
        elif action == "releases":
            result.removed = self._prune_releases(emit)
        return result

    def _run(self, argv: list[str], emit: Callable[[str], None], result: CleanupResult) -> None:
        """
        Run one command of a cleanup, streaming its output.

        Args:
            argv: The command.
            emit: Receives each output line.
            result: The result being built.

        Raises:
            ServerError: The command failed.
        """
        result.commands.append(" ".join(argv))

        def relay(line: str) -> None:
            result.output.append(line)
            emit(line)

        outcome = self.runner.stream(argv, on_line=relay, timeout=CLEANUP_TIMEOUT)
        if not outcome.success:
            raise ServerError(
                f"'{' '.join(argv[:3])}' failed (exit code {outcome.exit_code})",
                "Nothing else was changed. The tool's own output is below.",
                output="\n".join(result.output[-40:]) or outcome.stderr,
            )

    def _journal_commands(self, **params: Any) -> list[list[str]]:
        """
        Build the commands that shrink the journal.

        Args:
            **params: ``size_mb`` (default 200) or ``days``, not both.

        Returns:
            Rotate, then vacuum.

        Raises:
            ValidationError: A value is not a positive whole number, or both were given.
        """
        size = params.get("size_mb")
        days = params.get("days")
        if size is not None and days is not None:
            raise ValidationError("Give a size or an age, not both", "Use size_mb or days.")
        if days is not None:
            limit = f"--vacuum-time={_positive(days, 'days')}d"
        else:
            limit = f"--vacuum-size={_positive(DEFAULT_JOURNAL_MB if size is None else size, 'size_mb')}M"
        # Rotating first turns the active file into an archive, which is the
        # only kind vacuum will delete.
        return [["journalctl", "--rotate"], ["journalctl", limit]]

    def _pkg_cache_argv(self) -> list[str]:
        """
        Build the command that empties the package cache.

        Returns:
            The command of this machine's package manager.

        Raises:
            UnsupportedHostError: There is no supported package manager here.
        """
        argv = backend_for(
            self.platform, runner=self._runner, fs=self._fs, host=self.host
        ).clean_cache_argv()
        if argv is None:
            raise UnsupportedHostError(
                "There is no package cache to clean on this system",
                self.platform.why_updates_unsupported() or "No package manager was found.",
            )
        return argv

    def _docker_build_cache_argv(self) -> list[str]:
        """
        Build the command that prunes old build cache.

        Returns:
            ``docker builder prune`` with an age, never ``system prune``.

        Raises:
            UnsupportedHostError: Docker is not installed.
        """
        if not self.runner.exists("docker"):
            raise UnsupportedHostError("Docker is not installed on this system")
        return ["docker", "builder", "prune", "-f", "--filter", "until=168h"]

    def _image_target(self, target: Any) -> str:
        """
        Validate the image a caller wants removed.

        Args:
            target: An image id as ``docker image ls`` prints it.

        Returns:
            The id.

        Raises:
            ValidationError: It is not an image id, or the image is a way back
                or one a container uses.
        """
        if not isinstance(target, str) or not _IMAGE_ID.match(target):
            raise ValidationError(
                "Name the image by its id", "Use an id from the list of unused images."
            )
        allowed = {image["id"] for image in self.docker_unused_images()}
        if target.removeprefix("sha256:")[:12] not in {i[:12] for i in allowed}:
            raise ValidationError(
                "That image is not in the list of unused images",
                "It may be in use, or be the wasm-previous image an application rolls back to.",
            )
        return target

    def _doomed_releases(self) -> list[tuple[str, str]]:
        """
        Work out which releases a prune would remove, without removing them.

        Returns:
            ``(domain, release id)`` pairs.
        """
        from noust.deployers.helpers.layout import app_root
        from noust.deployers.releases import ReleaseManager

        doomed = []
        for app in self._apps_on_releases():
            manager = ReleaseManager(app_root(app), fs=DryRunFileSystem(), runner=self.runner)
            doomed += [(app.domain, release.id) for release in manager.prune(app.keep_releases)]
        return doomed

    def _prune_releases(self, emit: Callable[[str], None]) -> list[str]:
        """
        Prune every application to its own retention.

        Through the release manager's own retention function, so the locks, the
        rollback target and the rows in the store are the ones a deploy's prune
        has: this is not a second implementation of pruning.

        Args:
            emit: Receives one line per application.

        Returns:
            ``domain: release`` for each release removed.
        """
        from noust.deployers.lifecycle import set_release_retention

        removed = []
        for app in self._apps_on_releases():
            change = set_release_retention(app.domain, app.keep_releases)
            for release in change.pruned:
                emit(f"{app.domain}: removed release {release}")
                removed.append(f"{app.domain}: {release}")
        return removed


def _positive(value: Any, name: str) -> int:
    """
    Read a positive whole number.

    Args:
        value: What the caller gave. Only an integer is one: ``1.5`` is not
            silently 1, and ``True`` is not silently 1 either.
        name: Its name, for the message.

    Returns:
        The number.

    Raises:
        ValidationError: It is not a positive integer.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{name} must be a positive whole number", f"Got {value!r}.")
    return int(value)


def _reclaimed(lines: list[str]) -> int | None:
    """
    Read ``Total reclaimed space: 6.1GB`` from a Docker prune.

    Args:
        lines: The prune's output.

    Returns:
        Bytes, or None when Docker did not say.
    """
    for line in reversed(lines):
        if "reclaimed space" in line.lower():
            return parse_docker_size(line.rsplit(":", 1)[-1])
    return None


def _now() -> str:
    """
    Read the clock as the analysis writes it.

    Returns:
        ISO 8601 UTC.
    """
    return datetime.now(timezone.utc).isoformat()
