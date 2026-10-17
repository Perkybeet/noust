# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Give an application's disk limit to the kernel, as a project quota (checklist B-63).

The limit :mod:`noust.managers.app_disk` holds is Noust's: it refuses to build a release over it
and cannot stop the application from writing. A filesystem project quota can. XFS and ext4 keep a
quota per *project*, a number set on a tree of files and inherited by what is created inside it,
and refuse the write that would pass its hard limit with ``EDQUOT``. This sets one for an
application's directory, with the tools the kernel's documentation names (``xfs_quota``;
``chattr`` and ``setquota``), checked on real XFS and ext4 mounted with ``prjquota``
(``tests/integration/run.py``, scenario ``kernel_project_quota_stops_the_writes``).

What it will not do, because it changes a running machine and nothing here should surprise it:

- It never mounts, remounts or tunes a filesystem. The filesystem must already be mounted with
  project quotas (:func:`~noust.managers.app_disk.quota_facts` says ``enabled``); otherwise it
  refuses and says what the operator does, once, themselves.
- It is opt-in per application, behind a limit that is already set, and never for a site Noust
  adopted nor a directory outside Noust's apps directory: those files are the operator's.
- It does not touch ``/etc/projects`` or ``/etc/projid``. The project id is a number of Noust's
  own range (:data:`PROJECT_ID_BASE` plus the application's id), kept in the store, so it cannot
  collide with a name someone wrote there or with the ids Docker hands out from 1.

How the limit holds: a process without ``CAP_SYS_RESOURCE`` is stopped at the limit on both
filesystems, and that is every account an application runs as and the sandbox builds with. XFS
also stops root; ext4 does not, so Noust's own root-run work (a restore, a backup extracted in
place) can pass it. A tree that grows by an account that is not in the project's directories is
not counted: files moved in from outside keep their project, and that is the kernel's rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noust.core import audit
from noust.core.app_disk_store import get_record, set_kernel
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.fs import is_rehearsal
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import App, is_adopted
from noust.managers.app_disk import QuotaFacts, quota_facts

#: Where Noust's project ids start. Project ids are 32 bits; Docker hands its own out from 1 and a
#: person's ``/etc/projects`` is nearly always small, so a high range does not meet either.
PROJECT_ID_BASE = 3_000_000_000

#: Seconds a walk of the tree may take: the project id is set on every file already there.
APPLY_TIMEOUT = 1800

#: Seconds a quick quota command (a limit, a report) may take.
QUICK_TIMEOUT = 60

#: What ``xfs_quota -c`` can be given inside one argument without it being read as more than a path.
_XFS_PATH = re.compile(r"[A-Za-z0-9_./@+=:-]+")

#: Read-only probes this module runs (see :mod:`noust.core.runner`).
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = (
    ("xfs_quota", "-x", "-c", "report -p -N -b -n", "*"),
    ("repquota", "-P", "-n", "-O", "csv", "*"),
)


@dataclass(frozen=True)
class KernelState:
    """
    Whether an application's limit is also the kernel's.

    Attributes:
        applied: True when the limit was given to the kernel and has not been taken off.
        project: The project id it lives under.
        mount: The mount point it lives on.
        hard_mb: The hard limit the kernel holds, in megabytes, as it reports it.
        used_bytes: What the kernel counts against it, as it reports it; None when it could not
            be asked.
    """

    applied: bool = False
    project: int | None = None
    mount: str | None = None
    hard_mb: int | None = None
    used_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The state as JSON-ready values.
        """
        return {
            "applied": self.applied,
            "project": self.project,
            "mount": self.mount,
            "hard_mb": self.hard_mb,
            "used_bytes": self.used_bytes,
        }


def project_id_for(app_id: int) -> int:
    """
    Name the project an application's quota lives under.

    Args:
        app_id: The application's id in the store.

    Returns:
        :data:`PROJECT_ID_BASE` plus the id.
    """
    return PROJECT_ID_BASE + app_id


def commands_for(
    fstype: str, mount: str, path: Path, project: int, hard_mb: int | None
) -> dict[str, list[list[str]]]:
    """
    Build the commands that give a tree a project and a limit, and take them away.

    Argv only, never a shell. The walk that sets the project id comes first, then the limit; taking
    it off is the limit first, so the tree is never over a limit that stays after its owner left.

    Args:
        fstype: ``xfs`` or ``ext4``.
        mount: The mount point the quota lives on.
        path: The application's directory.
        project: The project id.
        hard_mb: The hard limit in megabytes; None for the plans that need none.

    Returns:
        ``apply`` (the walk and the limit), ``limit`` (the limit alone), ``remove`` (take it all
        off) and ``report`` (what the kernel counts).

    Raises:
        ValidationError: The filesystem is not one this knows, or ``xfs_quota`` cannot be given
            the path.
    """
    limit_mb = hard_mb if hard_mb is not None else 0
    if fstype == "xfs":
        if _XFS_PATH.fullmatch(str(path)) is None:
            raise ValidationError(
                f"xfs_quota cannot be given {path}",
                details="It takes a command as one argument, so a path with a space or a quote "
                "in it cannot be named. Keep the apps directory to letters, digits and . _ - /.",
            )

        def x(command: str) -> list[str]:
            return ["xfs_quota", "-x", "-c", command, mount]

        return {
            "apply": [
                x(f"project -s -p {path} {project}"),
                x(f"limit -p bhard={limit_mb}m {project}"),
            ],
            "limit": [x(f"limit -p bhard={limit_mb}m {project}")],
            "remove": [
                x(f"limit -p bsoft=0 bhard=0 {project}"),
                x(f"project -C -p {path} {project}"),
            ],
            "report": [x("report -p -N -b -n")],
        }
    if fstype == "ext4":
        here = ["find", str(path), "-xdev"]
        set_id = [*here, "!", "-type", "l", "-exec", "chattr", "-p", str(project), "{}", "+"]
        inherit = [*here, "-type", "d", "-exec", "chattr", "+P", "{}", "+"]
        clear_id = [*here, "!", "-type", "l", "-exec", "chattr", "-p", "0", "{}", "+"]
        uninherit = [*here, "-type", "d", "-exec", "chattr", "-P", "{}", "+"]
        limit = ["setquota", "-P", str(project), "0", str(limit_mb * 1024), "0", "0", mount]
        return {
            "apply": [set_id, inherit, limit],
            "limit": [limit],
            "remove": [
                ["setquota", "-P", str(project), "0", "0", "0", "0", mount],
                uninherit,
                clear_id,
            ],
            "report": [["repquota", "-P", "-n", "-O", "csv", mount]],
        }
    raise ValidationError(
        f"Project quotas are not set up on {fstype} filesystems",
        details="XFS and ext4 mounted with prjquota are the two Noust knows.",
    )


def parse_report(fstype: str, output: str, project: int) -> tuple[int | None, int | None]:
    """
    Read what the kernel counts and allows for a project.

    Args:
        fstype: ``xfs`` or ``ext4``.
        output: What the report command printed.
        project: The project id.

    Returns:
        ``(used_kib, hard_kib)``; None for what the report does not carry (a project the kernel
        has no entry for).
    """
    key = f"#{project}"
    for line in output.splitlines():
        if fstype == "xfs":
            fields = line.split()
            if fields and fields[0] == key and len(fields) >= 4:
                return int(fields[1]), int(fields[3])
        else:
            fields = line.split(",")
            if fields and fields[0] == key and len(fields) >= 6:
                return int(fields[3]), int(fields[5])
    return None, None


def _unsuitable(app: App) -> str | None:
    """
    Say why an application's tree is not one to put a project quota on.

    Args:
        app: The application's row.

    Returns:
        The reason, or None when it can have one.
    """
    from noust.deployers.helpers.layout import app_root
    from noust.deployers.lifecycle import is_in_apps_directory

    if is_adopted(app.app_type):
        return "it is a site Noust adopted: its files are yours, and a project id would be written on them"
    if app.id is None:
        return "it is not registered"
    if not is_in_apps_directory(app_root(app)):
        return "its directory is outside Noust's apps directory, so Noust did not make it"
    return None


def read(
    app: App, facts: QuotaFacts | None = None, *, runner: CommandRunner | None = None
) -> KernelState:
    """
    Say whether an application's limit is also the kernel's, and what the kernel counts.

    Asks the kernel only when the store says the quota was applied.

    Args:
        app: The application's row.
        facts: What :func:`~noust.managers.app_disk.quota_facts` said, when already asked.
        runner: The runner the report goes through.

    Returns:
        The state.
    """
    record = get_record(app.domain)
    if record is None or record.kernel_project is None or record.kernel_mount is None:
        return KernelState()
    run = runner or get_runner()
    from noust.deployers.helpers.layout import app_root

    facts = facts or quota_facts(app_root(app), runner=run)
    used = hard = None
    if facts.fstype in ("xfs", "ext4") and facts.mount:
        argv = commands_for(facts.fstype, facts.mount, app_root(app), record.kernel_project, None)[
            "report"
        ][0]
        result = run.run(argv, timeout=QUICK_TIMEOUT)
        if result.success:
            used, hard = parse_report(facts.fstype, result.stdout, record.kernel_project)
    return KernelState(
        applied=True,
        project=record.kernel_project,
        mount=record.kernel_mount,
        hard_mb=hard // 1024 if hard else None,
        used_bytes=used * 1024 if used is not None else None,
    )


def _run(run: CommandRunner, plan: list[list[str]], what: str) -> None:
    """
    Run a plan, stopping at the first command that fails with that tool's own words.

    Args:
        run: The runner.
        plan: The commands, in order.
        what: What the plan does, for the error.

    Raises:
        DeploymentError: A command failed.
    """
    missing = [argv[0] for argv in plan if not run.exists(argv[0])]
    if missing:
        raise DeploymentError(
            f"{what}: {missing[0]} is not installed",
            details="It comes with the quota tools of the distribution (xfsprogs for XFS, quota "
            "and e2fsprogs for ext4).",
        )
    for argv in plan:
        result = run.run(argv, timeout=APPLY_TIMEOUT)
        if not result.success:
            raise DeploymentError(
                f"{what}: {argv[0]} failed",
                details=(result.stderr or result.stdout).strip() or f"exit {result.exit_code}",
                output=(result.stderr or result.stdout).strip() or None,
            )


@dataclass(frozen=True)
class Ground:
    """
    What a kernel quota would be set on.

    Attributes:
        fstype: ``xfs`` or ``ext4``.
        mount: The mount point.
        soft_mb: The limit the application has, which the kernel is given.
    """

    fstype: str
    mount: str
    soft_mb: int


def check_possible(app: App, *, runner: CommandRunner | None = None) -> Ground:
    """
    Refuse a kernel quota the application or its filesystem cannot have.

    Args:
        app: The application's row.
        runner: The runner ``findmnt`` goes through.

    Returns:
        The filesystem the tree is on and the limit it would be given.

    Raises:
        ValidationError: No limit is set, the tree is not one to put a quota on, or the
            filesystem is not mounted with project quotas.
    """
    from noust.deployers.helpers.layout import app_root

    run = runner or get_runner()
    record = get_record(app.domain)
    if record is None or record.soft_mb is None:
        raise ValidationError(
            f"{app.domain} has no disk limit to give the kernel",
            details=f"Set one first: noust app disk {app.domain} --limit <MB>",
            field="soft_mb",
        )
    reason = _unsuitable(app)
    if reason is not None:
        raise ValidationError(f"{app.domain} cannot have a kernel quota: {reason}", field="domain")
    facts = quota_facts(app_root(app), runner=run)
    if facts.project_quota != "enabled" or facts.fstype is None or facts.mount is None:
        raise ValidationError(
            f"The filesystem {app.domain} is on is not mounted with project quotas "
            f"({facts.fstype or 'unknown'}: {facts.project_quota})",
            details="Noust does not mount or tune filesystems. Mount it with prjquota yourself "
            "(docs/runtime.md, Disk), then apply the quota.",
            field="domain",
        )
    return Ground(fstype=facts.fstype, mount=facts.mount, soft_mb=record.soft_mb)


def apply(app: App, *, runner: CommandRunner | None = None, actor: str = "system") -> KernelState:
    """
    Give an application's disk limit to the kernel.

    Idempotent: applying again walks the tree once more, so what was written meanwhile in a
    directory the first walk had passed gets the project too.

    Args:
        app: The application's row.
        runner: The runner every command goes through.
        actor: Who asked, for the audit trail.

    Returns:
        What the kernel holds now.

    Raises:
        ValidationError: No limit is set, the tree is not one to put a quota on, or the filesystem
            is not mounted with project quotas.
        DeploymentError: A command failed, or the kernel does not report the limit afterwards.
    """
    from noust.deployers.helpers.layout import app_root

    run = runner or get_runner()
    ground = check_possible(app, runner=run)
    path = app_root(app)
    project = project_id_for(app.id or 0)  # _unsuitable refused an application with no id
    plans = commands_for(ground.fstype, ground.mount, path, project, ground.soft_mb)
    _run(run, plans["apply"], f"Giving {app.domain}'s limit to the kernel")
    if is_rehearsal():
        return KernelState(
            applied=True, project=project, mount=ground.mount, hard_mb=ground.soft_mb
        )
    set_kernel(app.domain, project, ground.mount)
    state = read(app, runner=run)
    if state.hard_mb != ground.soft_mb:
        raise DeploymentError(
            f"The kernel does not report the limit of {app.domain} after it was set",
            details=f"It reports {state.hard_mb} MB on {ground.mount} for project {project}; "
            f"{ground.soft_mb} MB was set. Look at: {' '.join(plans['report'][0])}",
        )
    audit.record(
        "apps.disk_kernel_quota",
        target=f"app:{app.domain}",
        details={
            "applied": True,
            "project": project,
            "mount": ground.mount,
            "fstype": ground.fstype,
            "hard_mb": ground.soft_mb,
            "by": actor,
        },
    )
    return state


def refresh_limit(app: App, *, runner: CommandRunner | None = None) -> KernelState:
    """
    Make the kernel hold the limit the application has now, when it holds one.

    Args:
        app: The application's row.
        runner: The runner the limit goes through.

    Returns:
        What the kernel holds now.
    """
    from noust.deployers.helpers.layout import app_root

    run = runner or get_runner()
    record = get_record(app.domain)
    if record is None or record.kernel_project is None or record.soft_mb is None:
        return KernelState()
    facts = quota_facts(app_root(app), runner=run)
    if facts.fstype in ("xfs", "ext4") and facts.mount:
        plans = commands_for(
            facts.fstype, facts.mount, app_root(app), record.kernel_project, record.soft_mb
        )
        _run(run, plans["limit"], f"Changing the kernel's limit of {app.domain}")
    return read(app, facts, runner=run)


def remove(app: App, *, runner: CommandRunner | None = None, actor: str = "system") -> KernelState:
    """
    Take an application's quota off the kernel and the project off its files.

    Args:
        app: The application's row.
        runner: The runner every command goes through.
        actor: Who asked, for the audit trail.

    Returns:
        The state afterwards: not applied.

    Raises:
        DeploymentError: A command failed.
    """
    from noust.deployers.helpers.layout import app_root

    run = runner or get_runner()
    record = get_record(app.domain)
    if record is None or record.kernel_project is None:
        return KernelState()
    path = app_root(app)
    facts = quota_facts(path, runner=run)
    if facts.fstype in ("xfs", "ext4") and facts.mount:
        plans = commands_for(facts.fstype, facts.mount, path, record.kernel_project, None)
        _run(run, plans["remove"], f"Taking {app.domain}'s limit off the kernel")
    if not is_rehearsal():
        set_kernel(app.domain, None, None)
        audit.record(
            "apps.disk_kernel_quota",
            target=f"app:{app.domain}",
            details={"applied": False, "project": record.kernel_project, "by": actor},
        )
    return KernelState()
