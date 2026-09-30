# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The background jobs the databases API queues.

Each is a thin wrapper: it binds a capturing logger to the job's log so every
step the service reports reaches the console, calls the one service method,
and returns a JSON summary. A password never reaches a job's result or its
log: the result of a rotation says the new password is stored, and the
console shows it through the sudo-mode reveal endpoint.
"""

from __future__ import annotations

from typing import Any

from noust.core.audit import Actor
from noust.deployers.recorder import CapturingLogger
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.service import DatabaseService
from noust.web.jobs import JobContext


def _service(job_context: JobContext | None, step: str, actor: str | None) -> DatabaseService:
    """
    Build a service whose logger writes into the job's log.

    Args:
        job_context: The running job, when there is one.
        step: The first step's name.
        actor: The label of who queued the job, for the audit trail.

    Returns:
        The service.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.update(step, 10)
        logger.attach_sink(job_context.log)
    return DatabaseService(actor=Actor.from_label(actor) if actor else None, logger=logger)


def _done(job_context: JobContext | None) -> None:
    """
    Mark a job's progress complete.

    Args:
        job_context: The running job, when there is one.
    """
    if job_context is not None:
        job_context.update("Done", 100)


def dump_job(
    engine: str,
    database: str,
    compress: bool = True,
    dump_format: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Dump a database.

    Args:
        engine: The engine.
        database: The database.
        compress: gzip the dump.
        dump_format: PostgreSQL's dump format.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The dump, as the backups listing describes it.
    """
    # Through the backups class, not the service alone: the dump is hashed and
    # checked, and a dump that does not verify fails the job with the check's words.
    view = DatabaseBackups(_service(job_context, f"Dumping {database}", actor)).dump(
        engine, database, compress=compress, dump_format=dump_format
    )
    _done(job_context)
    return view.to_dict()


def restore_job(
    engine: str,
    database: str,
    backup_name: str,
    drop_existing: bool = False,
    safety_backup: bool = True,
    new_name: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Restore a dump, with the safety copy the service always takes first.

    Args:
        engine: The engine.
        database: The database the dump is of.
        backup_name: The dump's file name.
        drop_existing: Drop and recreate the target first.
        safety_backup: Take the safety copy when nothing is dropped.
        new_name: Restore into a new database of this name instead.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The target, the dump and the safety copy's file name.
    """
    outcome = DatabaseBackups(
        _service(job_context, f"Restoring {new_name or database}", actor)
    ).restore(
        engine,
        database,
        backup_name,
        drop_existing=drop_existing,
        safety_backup=safety_backup,
        new_name=new_name,
    )
    _done(job_context)
    return {
        "engine": engine,
        "database": outcome.database,
        "source": outcome.source.name,
        "safety_copy": outcome.safety_copy.name if outcome.safety_copy else None,
        "replaced": outcome.replaced,
    }


def drop_job(
    engine: str,
    name: str,
    force: bool = False,
    keep_backup: bool = True,
    unlink: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Drop a database after its last dump.

    Args:
        engine: The engine.
        name: The database.
        force: Disconnect sessions first.
        keep_backup: Dump it first.
        unlink: Remove its variables from the applications that use it.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done.
    """
    outcome = _service(job_context, f"Dropping {name}", actor).drop(
        engine, name, force=force, keep_backup=keep_backup, unlink=unlink
    )
    _done(job_context)
    return outcome.to_dict()


def provision_job(
    domain: str,
    engine: str,
    name: str | None = None,
    env_var: str | None = None,
    extra_vars: bool = False,
    restart: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Create a database for an application and link it.

    Args:
        domain: The application.
        engine: The engine.
        name: The database; derived from the application when None.
        env_var: The variable.
        extra_vars: Also write the ``DB_*`` variables.
        restart: Restart the application behind its gate.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done.
    """
    outcome = _service(job_context, f"Creating a database for {domain}", actor).provision_for_app(
        domain, engine, name=name, env_var=env_var, extra_vars=extra_vars, restart=restart
    )
    _done(job_context)
    return outcome.to_dict()


def link_job(
    domain: str,
    engine: str,
    database: str,
    username: str | None = None,
    env_var: str | None = None,
    extra_vars: bool = False,
    restart: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Link an existing database to an application.

    Args:
        domain: The application.
        engine: The engine.
        database: The database.
        username: The account to sign in as.
        env_var: The variable.
        extra_vars: Also write the ``DB_*`` variables.
        restart: Restart the application behind its gate.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done.
    """
    outcome = _service(job_context, f"Linking {database} to {domain}", actor).link(
        domain,
        engine,
        database,
        username=username,
        env_var=env_var,
        extra_vars=extra_vars,
        restart=restart,
    )
    _done(job_context)
    return outcome.to_dict()


def unlink_job(
    domain: str,
    engine: str,
    database: str,
    drop: bool = False,
    restart: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Unlink a database from an application, and drop it if asked.

    Args:
        domain: The application.
        engine: The engine.
        database: The database.
        drop: Drop it too, after its last dump.
        restart: Restart the application behind its gate.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done.
    """
    dropped = _service(job_context, f"Unlinking {database} from {domain}", actor).unlink(
        domain, engine, database, drop=drop, restart=restart
    )
    _done(job_context)
    return {
        "domain": domain,
        "engine": engine,
        "database": database,
        "dropped": dropped.to_dict() if dropped else None,
    }


def rotate_job(
    engine: str,
    username: str,
    host: str = "localhost",
    propagate: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
    first_password: bool = False,
) -> dict[str, Any]:
    """
    Rotate an account's password and give it to the applications that use it.

    Args:
        engine: The engine.
        username: The account.
        host: Its host restriction.
        propagate: Rewrite and restart the applications that use it.
        actor: Who queued it, as the audit trail names them.
        first_password: Confirms a Redis instance's first password.
        job_context: Injected by the job manager.

    Returns:
        What was done, without the password: it is in the secret store, and
        the console shows it through the sudo-mode reveal endpoint.
    """
    outcome = _service(job_context, f"Rotating the password of {username}", actor).rotate_password(
        engine, username, host=host, propagate=propagate, first_password=first_password
    )
    _done(job_context)
    return {**outcome.to_dict(), "password_stored": True}
