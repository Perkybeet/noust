# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the store cannot hold on its own: the console's service, the backups and the cron jobs a
machine is seeded with."""

from __future__ import annotations

import json
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .machine import Unit
from .sandbox import Sandbox


def seed_console_service(sandbox: Sandbox, units: dict[str, Unit]) -> None:
    """
    Install the console as the service ``noust web enable`` writes, running.

    Publishing the console on a public name needs it to be a service: the trusted proxy is
    written into its unit's ``ExecStart`` and the site forwards to the port that command
    line names. The sandbox's own console is a plain process, so this is the machine it
    stands for, not the process in front of the browser.

    Args:
        sandbox: The sandbox whose unit directory receives the unit file.
        units: The unit model, which learns the console runs.
    """
    from noust.managers.service_manager import UNIT_MARKER

    (sandbox.systemd_dir / "noust-web.service").write_text(
        f"# {UNIT_MARKER}\n"
        "[Unit]\nDescription=Noust console\nAfter=network.target\n\n"
        "[Service]\nType=simple\n"
        "ExecStart=/usr/bin/noust web start --host 127.0.0.1 --port 8080\n"
        "Restart=always\n\n[Install]\nWantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    units["noust-web"] = Unit(
        active="active",
        enabled=True,
        pid=1300,
        since=datetime.now() - timedelta(days=6),
        managed=False,
    )


def seed_overview_failed_worker(
    sandbox: Sandbox, store: Any, units: dict[str, Unit], *, domain: str = "example.com"
) -> None:
    """
    Seed a Noust unit that belongs to no application and has failed.

    A worker created with ``noust service create`` (a queue consumer, a mailer)
    fails without any application's state saying so. The overview names the
    failed units beyond those an application already accounts for, and this
    is the one that makes that count non-zero on the seeded machine.

    Args:
        sandbox: The sandbox whose unit directory receives the unit file.
        store: The seeded store, which tracks the service.
        units: The unit model the runner answers from; gains the worker.
        domain: The application whose tree the worker's script lives beside; one of the
            default machine's own by default, one of ``--showcase``'s otherwise.
    """
    from noust.core.store import Service
    from noust.managers.service_manager import UNIT_MARKER

    name = "queue-worker"
    command = f"/usr/bin/node /var/www/apps/{domain}/current/worker.js"
    store.create_service(
        Service(
            name=name,
            unit_file=str(sandbox.systemd_dir / f"{name}.service"),
            working_directory=f"/var/www/apps/{domain}/current",
            command=command,
            status="failed",
        )
    )
    units[name] = Unit(
        active="failed",
        since=datetime.now() - timedelta(minutes=42),
        restarts=5,
    )
    (sandbox.systemd_dir / f"{name}.service").write_text(
        f"# {UNIT_MARKER}\n"
        "[Unit]\n"
        f"Description=Queue worker for {domain}\n\n"
        "[Service]\n"
        f"WorkingDirectory=/var/www/apps/{domain}/current\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n",
        encoding="utf-8",
    )


def seed_backups(sandbox: Sandbox, domains: list[str], *, root: Path | None = None) -> None:
    """
    Write backups the way :class:`~noust.managers.backup_manager.BackupManager` lists them.

    A metadata file beside a real, tiny archive: the manager only lists a
    backup whose archive is present.

    Args:
        sandbox: The sandbox.
        domains: Domains to give backups to, two each.
        root: Where the ``<app>/`` directories go; the backup directory by default.
    """
    from noust.core.utils import domain_to_app_name
    from noust.managers.backup_manager import BackupMetadata

    now = datetime.now()
    for index, domain in enumerate(domains):
        app_name = domain_to_app_name(domain)
        directory = (root or sandbox.backup_dir) / app_name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for age_days, tags in ((index + 1, ["scheduled"]), (index + 8, ["pre-deploy"])):
            created = now - timedelta(days=age_days, hours=index)
            backup_id = f"{app_name}_{created.strftime('%Y%m%d_%H%M%S')}"
            archive = directory / f"{backup_id}.tar.gz"
            payload = directory / f"{backup_id}.README"
            payload.write_text(f"Seeded backup of {domain}\n", encoding="utf-8")
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(payload, arcname="app/README")
            payload.unlink()
            metadata = BackupMetadata(
                id=backup_id,
                domain=domain,
                app_name=app_name,
                created_at=created.isoformat(),
                size_bytes=archive.stat().st_size,
                app_type="nextjs",
                version="2.0.0",
                description="Nightly backup" if "scheduled" in tags else "Before deploy",
                includes_env=True,
                includes_node_modules=False,
                git_commit="9f2c41a",
                git_branch="main",
                tags=tags,
            )
            (directory / f"{backup_id}.json").write_text(
                json.dumps(metadata.to_dict(), indent=2), encoding="utf-8"
            )


def seed_misplaced_backups(sandbox: Sandbox) -> None:
    """
    Leave backups where an empty ``backup.directory`` once sent them (``--misplaced-backups``).

    Production looks for them in ``/root`` and ``/`` (``MISPLACED_BACKUP_ROOTS``), which
    :func:`redirect_system_paths` empties on a developer's machine; this points it at a
    stand-in for ``/root`` inside the sandbox instead, holding two backups of an application
    the backup directory already knows - only known applications are looked for.

    Args:
        sandbox: The sandbox, its backups already seeded.
    """
    from noust.managers.backup_manager import BackupManager

    known = sorted(sandbox.backup_dir.glob("*/*.json"))[0]
    domain = str(json.loads(known.read_text(encoding="utf-8"))["domain"])
    home = sandbox.root / "root"
    seed_backups(sandbox, [domain], root=home)
    BackupManager.MISPLACED_BACKUP_ROOTS = (home,)


def seed_cron(domain: str) -> None:
    """
    Create cron jobs through :class:`~noust.managers.cron_manager.CronManager` itself.

    The units are written into the sandboxed systemd directory; enabling the
    timer is answered by the fake runner.

    Args:
        domain: The application the first job belongs to.
    """
    from datetime import datetime, timedelta, timezone

    from noust.core.cron_once_history import CronOnceHistory, OnceRun
    from noust.managers.cron_manager import CronJob, CronManager

    manager = CronManager()
    # A task that runs once, a few days from now: it lists as a one-time task, and is the one the
    # dialog's "Once" shows when it is edited.
    in_three_days = (datetime.now().astimezone() + timedelta(days=3)).strftime("%Y-%m-%d 03:00")
    for job in (
        CronJob(
            name="sitemap",
            command="/usr/bin/node scripts/sitemap.js",
            schedule="daily",
            user="www-data",
            app_domain=domain,
        ),
        CronJob(
            name="cleanup-tmp",
            command="/usr/bin/find /tmp -name 'upload-*' -mtime +2 -delete",
            schedule="*-*-* 03:30:00",
            user="root",
        ),
        CronJob(
            name="reindex-search",
            command="/usr/bin/node scripts/reindex.js",
            schedule="daily",
            user="www-data",
            app_domain=domain,
            once_at=in_three_days,
        ),
    ):
        manager.create_job(job)

    # What two one-time tasks left when they ran and retired: one that ended well, and one the
    # server ran late (it was off at that moment) that failed, with what it printed.
    history = CronOnceHistory()
    now = datetime.now(timezone.utc)
    for name, command, days_ago, result, exit_code, late, output in (
        (
            "migrate-legacy-orders",
            "/usr/bin/node scripts/migrate-orders.js --all",
            2,
            "success",
            0,
            False,
            "migrated 1204 orders\ndone in 41s\n",
        ),
        (
            "purge-old-exports",
            "/usr/bin/find /var/exports -mtime +90 -delete",
            5,
            "exit-code",
            1,
            True,
            "find: '/var/exports/2026-07': Permission denied\n",
        ),
    ):
        asked = (now - timedelta(days=days_ago)).replace(hour=3, minute=0, second=0, microsecond=0)
        started = asked + timedelta(minutes=18 if late else 0, seconds=2)
        history.record(
            OnceRun(
                name=name,
                command=command,
                app_domain=domain if exit_code == 0 else "",
                run_as="www-data" if exit_code == 0 else "root",
                run_at=asked.astimezone().isoformat(timespec="seconds"),
                started_at=started.isoformat(timespec="seconds"),
                finished_at=(started + timedelta(seconds=41)).isoformat(timespec="seconds"),
                result=result,
                exit_code=exit_code,
                late=late,
                output=output,
            )
        )
