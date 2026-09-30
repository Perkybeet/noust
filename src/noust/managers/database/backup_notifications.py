# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a database backup or restore tells the operator, with the database as subject.

The words are the notification catalog's own (:mod:`noust.core.messages`):
these functions take the composers that already speak of backups and restores
(:mod:`noust.core.notifications.composers`) and point them at a database
instead of an application, so the title, the state, the excerpt of what the
tool said and the console link are exactly what an application's would be.
What differs is the subject (``postgresql/shop``), the page the link opens
(the database's), the code (``database.backup.failed``...) and the one
sentence under the title, which the catalog words for a database: the
application's says "the application's files".

Two callers announce, and each announces once: a job the console queued is
announced by the job manager's own subscriber
(:func:`database_job_notification`, from ``web/server.py``), and a run of the
timer, which has no job manager, delivers itself (:func:`deliver`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from noust.core.config import Config
from noust.core.messages import message
from noust.core.notifications.composers import (
    compose_backup_completed,
    compose_backup_failed,
    compose_backup_upload_failed,
    compose_restore,
)
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.notifier import Notifier


def database_subject(engine: str, database: str) -> str:
    """
    Args:
        engine: Canonical engine name.
        database: The database (a slot number for Redis).

    Returns:
        What a notification about it is about: ``postgresql/shop``.
    """
    return f"{engine}/{database}"


def _about(
    notification: Notification,
    engine: str,
    database: str,
    ctx: NotificationContext,
    *,
    code: str,
) -> Notification:
    """
    Turn an application notification into one about a database.

    Args:
        notification: What a composer built.
        engine: Canonical engine name.
        database: The database.
        ctx: The context, for the link and the language.
        code: The database's code (``database.backup.failed``), whose title
            and sentence the catalog words for a database.

    Returns:
        The notification with the database as its subject, code, title and
        sentence, the database's page as its link and no application domain.
    """
    link = ctx.link(f"/databases/{engine}/{database}")
    return replace(
        notification,
        code=code,
        title=message(f"title.{code}", ctx.locale),
        subject=database_subject(engine, database),
        summary=message(f"summary.{code}", ctx.locale),
        domain=None,
        links=(link,) if link else (),
    )


def compose_database_backup_failed(
    engine: str, database: str, error: str | None, ctx: NotificationContext
) -> Notification:
    """
    Compose the notification for a dump that was not taken or did not verify.

    Args:
        engine: Canonical engine name.
        database: The database.
        error: The failure's text, ``message\\n  Details: ...``, verbatim.
        ctx: The context.

    Returns:
        The notification, under ``backup_failed``.
    """
    return _about(
        compose_backup_failed(None, error, ctx),
        engine,
        database,
        ctx,
        code="database.backup.failed",
    )


def compose_database_backup_upload_failed(
    engine: str, database: str, destination: str, error: str | None, ctx: NotificationContext
) -> Notification:
    """
    Compose the notification for a dump kept locally that a destination refused.

    Args:
        engine: Canonical engine name.
        database: The database.
        destination: The destination's name.
        error: The failure's text; ``Details`` is rclone's own words.
        ctx: The context.

    Returns:
        The notification, a warning under ``backup_failed``.
    """
    subject = database_subject(engine, database)
    return _about(
        compose_backup_upload_failed(subject, destination, error, ctx),
        engine,
        database,
        ctx,
        code="database.backup.upload_failed",
    )


def compose_database_backup_completed(
    engine: str,
    database: str,
    dump: str,
    ctx: NotificationContext,
    *,
    size_bytes: int | None = None,
    destinations: tuple[str, ...] = (),
) -> Notification:
    """
    Compose the optional heartbeat for a policy run that went everywhere.

    Args:
        engine: Canonical engine name.
        database: The database.
        dump: The dump's file name.
        ctx: The context.
        size_bytes: Its size.
        destinations: The destinations it was sent to.

    Returns:
        The notification, under ``backup_success`` (off by default).
    """
    subject = database_subject(engine, database)
    return _about(
        compose_backup_completed(
            subject, dump, ctx, size_bytes=size_bytes, destinations=destinations
        ),
        engine,
        database,
        ctx,
        code="database.backup.completed",
    )


def compose_database_restore(
    ok: bool,
    engine: str,
    database: str,
    source: str | None,
    error: str | None,
    ctx: NotificationContext,
) -> Notification:
    """
    Compose the notification for a finished database restore.

    Args:
        ok: Whether it completed.
        engine: Canonical engine name.
        database: The database restored into.
        source: The dump it was loaded from.
        error: The failure's text, verbatim, when it failed.
        ctx: The context.

    Returns:
        The notification, under ``restore_success`` or ``restore_failed``.
    """
    return _about(
        compose_restore(ok=ok, domain=None, backup_id=source, error=error, ctx=ctx),
        engine,
        database,
        ctx,
        code="database.restore.completed" if ok else "database.restore.failed",
    )


def _status(value: Any) -> str:
    """
    Args:
        value: A job's status or type, an enum or a string.

    Returns:
        Its plain string.
    """
    return str(getattr(value, "value", value))


def database_job_notification(job: Any, ctx: NotificationContext) -> Notification | None:
    """
    Translate a finished database job into the notification it deserves.

    Args:
        job: A job whose metadata says it is about a database
            (``kind == "database"``, ``engine``, ``database``).
        ctx: The context.

    Returns:
        A failed dump or policy run as ``backup_failed``, a finished restore
        as ``restore_success`` or ``restore_failed``, and None for anything
        else (a verification, a push, a job that is still going).
    """
    status = _status(job.status)
    job_type = _status(job.type)
    engine = str(job.metadata.get("engine") or "")
    database = str(job.metadata.get("database") or "")
    if not engine or not database:
        return None
    if job_type == "backup" and status == "failed":
        return compose_database_backup_failed(engine, database, job.error, ctx)
    if job_type == "restore" and status in ("completed", "failed"):
        source = job.metadata.get("backup_id")
        return compose_database_restore(
            status == "completed", engine, database, str(source) if source else None, job.error, ctx
        )
    return None


def deliver(build: Callable[[NotificationContext], Notification]) -> None:
    """
    Compose a notification over the configuration on disk and send it now.

    For a process that has no job manager to announce for it: the timer's
    ``noust db backup-run``. The notifier isolates a failing channel and never
    raises, so an announcement can never turn a finished backup into a failed one.

    Args:
        build: Builds the notification from the context.
    """
    config = Config()
    Notifier(config).notify(build(NotificationContext.from_config(config)))
