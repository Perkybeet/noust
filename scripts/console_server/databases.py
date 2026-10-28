# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Databases of the 2.2 console: what the Databases pages and the release screens are seeded with."""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from .backup_destinations import seed_backup_destinations
from .code_hosts import seed_github_app
from .demo_database import _DEMO_DATABASE, _PG_DUMP_BYTES
from .env_marks import seed_env_marks
from .machine import Unit
from .previews import seed_previews
from .sandbox import Sandbox
from .zero_downtime import seed_zero_downtime

# ---------------------------------------------------------------------------
# Console 2.2: blue/green, pull request previews, the GitHub App, backup
# destinations and why an environment variable is hidden. Each screen gets the
# states it draws; every network call they make is answered here, offline.
# ---------------------------------------------------------------------------


def seed_databases(sandbox: Sandbox) -> None:
    """
    Give example_production what its page shows (3.1): the application that uses it, the
    accounts Noust keeps passwords for, a backup policy with its timer, dumps of several ages
    (checked, one test-restored, one sent offsite, one that failed its check), and a month of
    size and connection readings for its charts.

    Everything goes through the managers the API uses, over the modelled runner, so the page
    reads exactly what a real server would have recorded.

    Args:
        sandbox: The sandbox.
    """
    import math

    from noust.core.exceptions import NoustError
    from noust.core.secrets import SecretStore
    from noust.core.store import get_store
    from noust.deployers.helpers.databases import _password_secret
    from noust.managers.database.backup_records import BackupRecords, DumpRecord
    from noust.managers.database.backups import DatabaseBackups
    from noust.managers.database.base import BaseDatabaseManager
    from noust.managers.database.records import DatabaseLink, DatabaseRecords
    from noust.managers.database.service import DatabaseService
    from noust.web.metrics_collector import get_metrics_store
    from tests.support.panel_factory import DESTINATION_SFTP as DESTINATION_SFTP_NAME

    store = get_store(sandbox.store_file)
    app = store.get_app("example.com")
    records = DatabaseRecords(store)
    secrets = SecretStore()
    if app is not None and app.id is not None:
        records.save_link(
            DatabaseLink(
                app_id=app.id,
                engine="postgresql",
                db_name=_DEMO_DATABASE,
                username=_DEMO_DATABASE,
                env_var="DATABASE_URL",
            )
        )
    shop = store.get_app("shop.example.net")
    if shop is not None and shop.id is not None:
        records.save_link(
            DatabaseLink(
                app_id=shop.id,
                engine="mysql",
                db_name="shop_wp",
                username="shop_wp",
                env_var="DATABASE_URL",
                extra_vars=True,
            )
        )
        secrets.write(_password_secret("mysql", "shop_wp"), "Wp-demo-7Qx2")
    secrets.write(_password_secret("postgresql", _DEMO_DATABASE), "Pr0d-demo-k8Vw")
    records.save_account(
        "postgresql",
        _DEMO_DATABASE,
        db_name=_DEMO_DATABASE,
        profile="read_write",
        password_changed=True,
    )
    records.save_account(
        "postgresql",
        "analytics_reader",
        db_name=_DEMO_DATABASE,
        profile="read_only",
        password_changed=True,
    )
    secrets.write(_password_secret("postgresql", "analytics_reader"), "An4lytics-demo")

    service = DatabaseService()
    backups = DatabaseBackups(service)
    destinations = [{"name": DESTINATION_SFTP_NAME, "retention_count": 30, "retention_days": 90}]
    try:
        backups.set_policy(
            "postgresql",
            _DEMO_DATABASE,
            schedule="daily",
            retention_count=7,
            retention_days=30,
            destinations=destinations,
            verify_restore=True,
        )
    except NoustError as exc:
        # A destination the sandbox did not seed: the policy stays local.
        print(f"seed_databases: {exc}", file=sys.stderr)
        backups.set_policy(
            "postgresql",
            _DEMO_DATABASE,
            schedule="daily",
            retention_count=7,
            retention_days=30,
            verify_restore=True,
        )

    directory = BaseDatabaseManager.BACKUP_DIR
    directory.mkdir(parents=True, exist_ok=True)
    dump_records = BackupRecords(store)
    now = datetime.now()
    taken: list[tuple[str, str]] = []
    for hours, origin, content in (
        (7, "scheduled", _PG_DUMP_BYTES),
        (31, "scheduled", _PG_DUMP_BYTES),
        (55, "scheduled", _PG_DUMP_BYTES),
        (80, "manual", _PG_DUMP_BYTES),
        (130, "safety", _PG_DUMP_BYTES),
        (200, "manual", "not a dump: the disk filled up while it was written"),
    ):
        moment = now - timedelta(hours=hours)
        name = f"postgresql-{_DEMO_DATABASE}-{moment.strftime('%Y%m%d_%H%M%S')}.dump"
        path = directory / name
        path.write_text(content)
        os.utime(path, (moment.timestamp(), moment.timestamp()))
        dump_records.save_dump(
            DumpRecord(
                engine="postgresql",
                file_name=name,
                db_name=_DEMO_DATABASE,
                origin=origin,
                size=path.stat().st_size,
            )
        )
        taken.append((name, origin))
    for index, (name, _origin) in enumerate(taken):
        backups.verify("postgresql", name, restore=index == 0)
    newest = dump_records.dump("postgresql", taken[0][0])
    if newest is not None:
        newest.remote_copies = [
            {
                "destination": DESTINATION_SFTP_NAME,
                "pushed_at": (now - timedelta(hours=6, minutes=58)).astimezone().isoformat(),
                "verified_by": "sha256",
                "folder": f"databases/postgresql/{_DEMO_DATABASE}",
            }
        ]
        dump_records.save_dump(newest)
    dump_records.record_run("postgresql", _DEMO_DATABASE, ok=True, error=None, dump=taken[0][0])

    metrics = get_metrics_store()
    stamp_now = int(time.time())
    stamps = [
        *range(stamp_now - 30 * 86_400, stamp_now - 86_400, 1_800),
        *range(stamp_now - 86_400, stamp_now, 120),
    ]
    base = f"db.postgresql.{_DEMO_DATABASE}"
    for stamp in stamps:
        age_days = (stamp_now - stamp) / 86_400
        daily = math.sin((stamp % 86_400) / 86_400 * 2 * math.pi - math.pi / 2)
        metrics.record_many(
            [
                (
                    f"{base}.size",
                    48_218_931 - age_days * 310_000 + math.sin(stamp / 7_000) * 40_000,
                ),
                (f"{base}.connections", max(1.0, 5 + 3 * daily + math.sin(stamp / 900) * 1.5)),
                (f"{base}.cache_hit", 99.3 + 0.4 * math.sin(stamp / 5_000)),
                (f"{base}.tps", max(0.5, 18 + 12 * daily + math.sin(stamp / 600) * 4)),
            ],
            ts=stamp,
        )
    metrics.consolidate(now=stamp_now)
    seed_detected_use(store)
    model_database_containers()


#: The application whose ``.env`` names ``example_staging`` without Noust having linked it:
#: the database's row offers to record the use (``detected_apps``).
DETECTED_APP = "docs.example.org"

#: The database that application's ``DATABASE_URL`` points at, on the host's PostgreSQL.
DETECTED_DATABASE = "example_staging"


def seed_detected_use(store: Any) -> None:
    """
    Make one application's ``.env`` name a database it has no link to (3.3).

    The use is found the way a real one is: the ``.env`` the layout says (``env_file_for``)
    is read for connection strings, which here points at the host's PostgreSQL on its port.

    Args:
        store: The seeded store.
    """
    from noust.core.appdir.layout import env_file_for

    app = store.get_app(DETECTED_APP)
    if app is None:
        return
    env_file = env_file_for(app)
    url = f"postgres://staging_app:Stg-demo-9Fq2@127.0.0.1:5432/{DETECTED_DATABASE}"
    text = env_file.read_text(encoding="utf-8") if env_file.is_file() else "NODE_ENV=production\n"
    if re.search(r"^DATABASE_URL=", text, re.MULTILINE):
        text = re.sub(r"^DATABASE_URL=.*$", f"DATABASE_URL={url}", text, flags=re.MULTILINE)
    else:
        text = f"{text.rstrip(chr(10))}\nDATABASE_URL={url}\n"
    env_file.write_text(text, encoding="utf-8")
    env_file.chmod(0o600)


class _DockerInstalled:
    """
    The runner database discovery asks, for which Docker is installed.

    The modelled machine has no Docker for every other page (the adoption wizard and the
    dependency checks say so, and their suites rely on it), yet it holds a database
    container; discovery gates itself on ``exists("docker")``, so only it is told otherwise.
    Everything else is the runner's own.
    """

    def __init__(self, runner: Any) -> None:
        """
        Args:
            runner: The machine's runner.
        """
        self._runner = runner

    def exists(self, program: str) -> bool:
        """
        Args:
            program: An executable name.

        Returns:
            True for ``docker``, otherwise what the runner says.
        """
        return program == "docker" or bool(self._runner.exists(program))

    def run(self, argv: Sequence[str], **kwargs: Any) -> Any:
        """
        Args:
            argv: The command.
            **kwargs: The runner's options.

        Returns:
            The runner's answer, which for Docker is :class:`_DatabaseContainers`'.
        """
        return self._runner.run(argv, **kwargs)


def model_database_containers() -> None:
    """Show the Databases area the machine's database container (``engines``, ``databases``)."""
    import noust.managers.database.service as service_module
    from noust.core.runner import get_runner

    discover = service_module.discover

    def discover_with_docker(runner: Any = None) -> Any:
        return discover(_DockerInstalled(runner or get_runner()))

    service_module.discover = discover_with_docker


def seed_release_22(
    sandbox: Sandbox,
    store: Any,
    units: dict[str, Unit],
    ports: dict[str, int],
    domains: dict[str, str],
) -> None:
    """
    Seed what the 2.2 screens need beyond the machine seeded before them.

    Args:
        sandbox: The sandbox.
        store: The seeded store.
        units: The modelled units, mutated in place.
        ports: Each unit's port, mutated in place.
        domains: Each unit's domain, mutated in place.
    """
    seed_zero_downtime(sandbox, store, units, ports, domains)
    seed_previews(sandbox, store, units, ports, domains)
    seed_github_app(sandbox, store)
    seed_backup_destinations(sandbox, store)
    seed_env_marks(sandbox, store)
