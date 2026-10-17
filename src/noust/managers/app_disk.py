# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application takes of the disk, and the limit Noust holds it to (checklist B-63).

The server's storage analysis (:mod:`noust.managers.server.storage`) says what takes space on the
machine and has no row per application. This says it per application: the active release, the
releases beside it that a prune would remove, ``shared/`` (the uploads and the ``.env``), the
repository cache and, apart, the backups. It measures with the one ``du`` that analysis uses
(:meth:`~noust.managers.server.storage.StorageManager.measure`) and, like it, never in a request:
a job takes the measurement, the row keeps it and says how old it is.

The limit is **Noust's, not the kernel's**. A deploy that would build a new release is refused
while the application is over it (:func:`deploy_refusal`, read by the pre-flight checks); going
back to an earlier release never is, because an outage is not a reason to wait for room. Kernel
project quotas (XFS, ext4) would stop the writes themselves, but they need the filesystem mounted
with ``prjquota`` and a project id on the tree, which no process of Noust's should change on a
running machine: :func:`quota_facts` says whether the filesystem holds the application would
accept one, and ``docs/runtime.md`` says how an operator turns it on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from noust.core import audit
from noust.core.app_disk_store import DiskRecord, get_record, set_limit, set_usage
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, get_store
from noust.core.utils import domain_to_app_name, format_bytes

#: The smallest limit: below it a single release does not fit and every deploy would be refused.
MIN_LIMIT_MB = 64

#: The largest: a petabyte, which is a typo and not a limit.
MAX_LIMIT_MB = 1024 * 1024 * 1024

#: From this share of the limit the application is "near" it.
NEAR_PERCENT = 90.0

#: Seconds ``findmnt`` may take.
FINDMNT_TIMEOUT = 15

#: Read-only probes this module runs (see :mod:`noust.core.runner`).
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    ("findmnt", "-no", "TARGET,FSTYPE,OPTIONS", "-T", "*"),
)

#: The mount options that mean project quotas are on, by filesystem.
_PROJECT_QUOTA_OPTIONS = {"xfs": ("prjquota", "pquota", "prjquota-enforce"), "ext4": ("prjquota",)}

State = Literal["ok", "near", "over"]
ProjectQuota = Literal["enabled", "available", "unsupported", "unknown"]


@dataclass(frozen=True)
class DiskPart:
    """
    One place an application takes disk.

    Attributes:
        name: ``tree`` (an application built in place), ``current`` (the active release),
            ``releases`` (the others), ``shared`` or ``repo``.
        bytes: What it takes, on its filesystem alone.
        path: Where it is.
        reclaimable_bytes: What a prune of the releases the application keeps would give back;
            None where it does not apply.
    """

    name: str
    bytes: int
    path: str | None = None
    reclaimable_bytes: int | None = None


@dataclass
class DiskUsage:
    """
    What an application takes of the disk.

    Attributes:
        parts: Each place that takes some, largest first.
        backups_bytes: The archives of its backups, which live elsewhere and do not count
            against the limit.
        measured_at: When it was measured, as an ISO timestamp.
        errors: Paths that could not be measured in time; the total is then at least this much.
    """

    parts: list[DiskPart] = field(default_factory=list)
    backups_bytes: int = 0
    measured_at: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """What the application's own directory takes: the figure the limit is held against."""
        return sum(part.bytes for part in self.parts)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The measurement as JSON-ready values.
        """
        return {
            "parts": [
                {
                    "name": part.name,
                    "bytes": part.bytes,
                    "path": part.path,
                    "reclaimable_bytes": part.reclaimable_bytes,
                }
                for part in self.parts
            ],
            "total_bytes": self.total_bytes,
            "backups_bytes": self.backups_bytes,
            "measured_at": self.measured_at,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class DiskVerdict:
    """
    How an application stands against its limit.

    Attributes:
        limit_bytes: The limit.
        used_bytes: What it took when last measured.
        percent: The share of the limit, one decimal.
        state: ``ok``, ``near`` (from :data:`NEAR_PERCENT`) or ``over``.
    """

    limit_bytes: int
    used_bytes: int
    percent: float
    state: State

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The verdict as JSON-ready values.
        """
        return {
            "limit_bytes": self.limit_bytes,
            "used_bytes": self.used_bytes,
            "percent": self.percent,
            "state": self.state,
        }


@dataclass(frozen=True)
class QuotaFacts:
    """
    Whether the filesystem an application lives on could hold a kernel project quota.

    Attributes:
        mount: The mount point.
        fstype: The filesystem type.
        options: Its mount options.
        project_quota: ``enabled`` (mounted with project quotas), ``available`` (xfs or ext4, not
            mounted with them), ``unsupported`` (anything else) or ``unknown`` (``findmnt`` did
            not say).
    """

    mount: str | None
    fstype: str | None
    options: tuple[str, ...]
    project_quota: ProjectQuota

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The facts as JSON-ready values.
        """
        return {
            "mount": self.mount,
            "fstype": self.fstype,
            "options": list(self.options),
            "project_quota": self.project_quota,
        }


@dataclass(frozen=True)
class DiskStatus:
    """
    Everything the API and the console show of an application's disk.

    Attributes:
        domain: The application.
        soft_mb: The limit, or None.
        usage: The last measurement, or None before the first.
        verdict: How it stands against the limit, when there is both.
        filesystem: Whether a kernel quota could be held there.
    """

    domain: str
    soft_mb: int | None
    usage: dict[str, Any] | None
    verdict: DiskVerdict | None
    filesystem: QuotaFacts

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The status as JSON-ready values.
        """
        return {
            "domain": self.domain,
            "soft_mb": self.soft_mb,
            "usage": self.usage,
            "verdict": self.verdict.to_dict() if self.verdict is not None else None,
            "filesystem": self.filesystem.to_dict(),
        }


def validate_limit(value: int | None) -> int | None:
    """
    Check a limit.

    Args:
        value: Mebibytes, or None for none.

    Returns:
        It, unchanged.

    Raises:
        ValidationError: It is below :data:`MIN_LIMIT_MB` or above :data:`MAX_LIMIT_MB`.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            "The disk limit is a whole number of megabytes",
            details="Give it in MB, such as 2048, or remove it.",
            field="soft_mb",
        )
    if not MIN_LIMIT_MB <= value <= MAX_LIMIT_MB:
        raise ValidationError(
            f"The disk limit must be between {MIN_LIMIT_MB} MB and {MAX_LIMIT_MB} MB",
            details="Below the floor a single release does not fit and every deploy would be "
            "refused. To have no limit, remove it.",
            field="soft_mb",
        )
    return value


def verdict_of(used_bytes: int, soft_mb: int | None) -> DiskVerdict | None:
    """
    Say how a size stands against a limit.

    Args:
        used_bytes: What an application took.
        soft_mb: Its limit.

    Returns:
        The verdict, or None for an application with no limit.
    """
    if soft_mb is None:
        return None
    limit = soft_mb * 1024 * 1024
    percent = round(used_bytes * 100 / limit, 1)
    state: State = "over" if used_bytes >= limit else "near" if percent >= NEAR_PERCENT else "ok"
    return DiskVerdict(limit_bytes=limit, used_bytes=used_bytes, percent=percent, state=state)


def quota_facts(path: Path, *, runner: CommandRunner | None = None) -> QuotaFacts:
    """
    Say whether the filesystem holding a path could carry a kernel project quota.

    Args:
        path: Where the application is.
        runner: The runner ``findmnt`` goes through.

    Returns:
        The facts; ``unknown`` when ``findmnt`` could not be asked.
    """
    result = (runner or get_runner()).run(
        ["findmnt", "-no", "TARGET,FSTYPE,OPTIONS", "-T", str(path)], timeout=FINDMNT_TIMEOUT
    )
    fields = result.stdout.strip().splitlines()[0].split(None, 2) if result.stdout.strip() else []
    if not result.success or len(fields) < 2:
        return QuotaFacts(mount=None, fstype=None, options=(), project_quota="unknown")
    mount, fstype = fields[0], fields[1]
    options = tuple(fields[2].split(",")) if len(fields) > 2 else ()
    wanted = _PROJECT_QUOTA_OPTIONS.get(fstype)
    if wanted is None:
        return QuotaFacts(mount, fstype, options, "unsupported")
    return QuotaFacts(
        mount, fstype, options, "enabled" if any(o in options for o in wanted) else "available"
    )


def measure(app: App, *, runner: CommandRunner | None = None) -> DiskUsage:
    """
    Measure what an application takes of the disk.

    Each tree has its own deadline, so one slow tree costs its own answer and not the rest. Run
    it in a job: a release tree with a ``node_modules`` in it takes a minute.

    Args:
        app: The application's row.
        runner: The runner ``du`` goes through.

    Returns:
        The measurement. A place that does not exist is not there; one that could not be measured
        in time is named in ``errors``.
    """
    from noust.core.fs import DryRunFileSystem
    from noust.deployers.helpers.layout import app_root
    from noust.deployers.releases import REPO_CACHE_DIR, SHARED_DIR, ReleaseManager
    from noust.managers.backup_manager import BackupManager
    from noust.managers.server.storage import StorageManager

    run = runner or get_runner()
    storage = StorageManager(runner=run)
    root = app_root(app)
    usage = DiskUsage(measured_at=datetime.now(timezone.utc).isoformat())

    def take(name: str, path: Path, reclaimable: int | None = None) -> int:
        if not path.exists():
            return 0
        size = storage.measure(path)
        if size is None:
            usage.errors.append(f"{path}: could not be measured in time")
            return 0
        usage.parts.append(DiskPart(name, size, str(path), reclaimable))
        return size

    if app.layout == "releases":
        manager = ReleaseManager(root, fs=DryRunFileSystem(), runner=run)
        releases = manager.list()
        active = next((release for release in releases if release.active), None)
        if active is not None:
            take("current", active.path)
        # What a prune of the releases the application keeps would remove, worked out on a
        # filesystem that changes nothing: the one implementation of "which releases go".
        doomed = {release.id for release in manager.prune(app.keep_releases)}
        others = [release for release in releases if not release.active]
        sizes = {release.id: (storage.measure(release.path) or 0) for release in others}
        if others:
            usage.parts.append(
                DiskPart(
                    "releases",
                    sum(sizes.values()),
                    str(root / "releases"),
                    sum(size for key, size in sizes.items() if key in doomed),
                )
            )
        take("shared", root / SHARED_DIR)
        take("repo", root / REPO_CACHE_DIR)
    else:
        take("tree", root)

    usage.parts.sort(key=lambda part: part.bytes, reverse=True)
    by_app = BackupManager(runner=run).get_storage_usage().get("by_app", {})
    usage.backups_bytes = int(by_app.get(domain_to_app_name(app.domain), {}).get("size_bytes", 0))
    return usage


def measure_and_record(app: App, *, runner: CommandRunner | None = None) -> DiskUsage:
    """
    Measure an application and keep the measurement.

    Args:
        app: The application's row.
        runner: The runner ``du`` goes through.

    Returns:
        The measurement.
    """
    usage = measure(app, runner=runner)
    set_usage(app.domain, usage.to_dict(), measured_at=usage.measured_at)
    return usage


def measure_domain(domain: str, *, runner: CommandRunner | None = None) -> DiskUsage:
    """
    Measure an application by its domain, as the job that does it does.

    Args:
        domain: The application's domain.
        runner: The runner ``du`` goes through.

    Returns:
        The measurement, kept.

    Raises:
        NoustError: The application is gone.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="It was deleted while this was queued."
        )
    return measure_and_record(app, runner=runner)


def status(app: App, *, runner: CommandRunner | None = None) -> DiskStatus:
    """
    Show what is recorded of an application's disk. Measures nothing.

    Args:
        app: The application's row.
        runner: The runner ``findmnt`` goes through.

    Returns:
        The limit, the last measurement, how it stands and what the filesystem could hold.
    """
    from noust.deployers.helpers.layout import app_root

    record: DiskRecord | None = get_record(app.domain)
    usage = record.usage if record is not None else None
    used = int(usage["total_bytes"]) if usage and "total_bytes" in usage else None
    soft = record.soft_mb if record is not None else None
    return DiskStatus(
        domain=app.domain,
        soft_mb=soft,
        usage=usage,
        verdict=verdict_of(used, soft) if used is not None else None,
        filesystem=quota_facts(app_root(app), runner=runner),
    )


def set_disk_limit(app: App, soft_mb: int | None, *, actor: str) -> DiskRecord | None:
    """
    Set or remove the limit an application is held to.

    Args:
        app: The application's row.
        soft_mb: Mebibytes, or None to remove it.
        actor: Who asked, for the audit trail.

    Returns:
        What is recorded now; None when nothing is.

    Raises:
        ValidationError: The limit is out of range.
    """
    validate_limit(soft_mb)
    previous = get_record(app.domain)
    set_limit(app.domain, soft_mb, changed_by=actor)
    audit.record(
        "apps.disk_limit",
        target=f"app:{app.domain}",
        details={
            "soft_mb": soft_mb,
            "previous_soft_mb": previous.soft_mb if previous is not None else None,
            "by": actor,
        },
    )
    return get_record(app.domain)


def deploy_refusal(app: App | None, *, runner: CommandRunner | None = None) -> list[str]:
    """
    Say whether a deploy that would build a new release must wait.

    The pre-flight check of the limit. An application with none, or one that is not yet
    registered, is never held. The measurement is taken now and kept, and it is not evidence when
    a tree could not be measured in time and the part that was measured is under the limit.

    Args:
        app: The application's row, or None for a first deploy.
        runner: The runner ``du`` goes through.

    Returns:
        The problems found, empty when the deploy may go on.
    """
    if app is None or app.id is None:
        return []
    record = get_record(app.domain, store=get_store())
    if record is None or record.soft_mb is None:
        return []
    usage = measure_and_record(app, runner=runner)
    verdict = verdict_of(usage.total_bytes, record.soft_mb)
    if verdict is None or verdict.state != "over":
        return []
    return [
        f"{app.domain} is over its disk limit: {format_bytes(verdict.used_bytes)} of "
        f"{format_bytes(verdict.limit_bytes)}. Free space (releases it no longer needs, uploads, "
        f"caches) or raise the limit with: noust app disk {app.domain} --limit <MB>. Going back to "
        "an earlier release is not held up by it."
    ]


def require_room(app: App | None, *, runner: CommandRunner | None = None) -> None:
    """
    Refuse an update that would build a new release while the application is over its limit.

    The chokepoint of the limit: every update - the command, the console's job, the webhook, the
    scheduled one - reaches :func:`~noust.deployers.lifecycle.update_app`, which asks here before
    anything is fetched. Going back to an earlier release never asks.

    Args:
        app: The application's row.
        runner: The runner ``du`` goes through.

    Raises:
        DeploymentError: The application is over its limit, with the figures and what to do.
    """
    problems = deploy_refusal(app, runner=runner)
    if problems:
        raise DeploymentError(
            "Pre-flight checks failed",
            details="The following issues were found:\n"
            + "\n".join(f"  - {problem}" for problem in problems),
        )
