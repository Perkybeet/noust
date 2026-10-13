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
from noust.core.exceptions import DatabaseEngineError
from noust.deployers.recorder import CapturingLogger
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.instance_migration import InstanceMigrations
from noust.managers.database.instance_tls import InstanceTls
from noust.managers.database.instance_tuning import InstanceTuning
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.moves import DatabaseMover
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
    version: str | None = None,
    install: bool = False,
    instance: str | None = None,
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
        version: The version of the engine the database is for; None for the
            installed one.
        install: Install the engine, in that version, first when it is missing.
        instance: The instance Noust runs to make the database on.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done.
    """
    outcome = _service(job_context, f"Creating a database for {domain}", actor).provision_for_app(
        domain,
        engine,
        name=name,
        env_var=env_var,
        extra_vars=extra_vars,
        restart=restart,
        version=version,
        install=install,
        instance=instance,
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
    tls_mode: str | None = None,
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
        tls_mode: How the application treats the engine's certificate, or None.
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
        tls_mode=tls_mode,
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


def instance_job(
    action: str,
    name: str,
    engine: str | None = None,
    version: str | None = None,
    memory_mb: int | None = None,
    port: int | None = None,
    backup: bool = True,
    confirm: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Make, start, upgrade or remove a database instance Noust runs.

    Each of these pulls an image, starts a container or waits for an engine to
    answer, which is minutes of work that must not sit on a request.

    Args:
        action: ``create``, ``start``, ``upgrade`` or ``remove``.
        name: The instance's name.
        engine: What to run, for ``create``.
        version: The version, for ``create``.
        memory_mb: The memory limit, for ``create``.
        port: The port on 127.0.0.1, for ``create``.
        backup: Dump every database first, for ``upgrade``.
        confirm: The operator accepted that the data goes, for ``remove``.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done: the instance, what an upgrade changed, or what a
        removal took away. No password is in it: the superuser's is in a file
        only root reads.

    Raises:
        DatabaseEngineError: ``action`` is not one of the four, or a creation
            names no engine.
    """
    managed = ManagedInstances(_service(job_context, f"{action.capitalize()} {name}", actor))
    if action == "create":
        if engine is None:
            raise DatabaseEngineError("An instance needs an engine to run")
        result: dict[str, Any] = {
            "instance": managed.create(name, engine, version=version, memory=memory_mb, port=port)
        }
    elif action == "start":
        result = {"instance": managed.start(name)}
    elif action == "upgrade":
        result = {"upgrade": managed.upgrade(name, backup=backup).to_dict()}
    elif action == "remove":
        result = {"removed": managed.remove(name, confirm=confirm).to_dict()}
    else:
        raise DatabaseEngineError(
            f"Unsupported instance action: {action}",
            details="Use create, start, upgrade or remove.",
        )
    _done(job_context)
    return result


def move_job(
    source: str,
    database: str,
    target: str,
    replace: bool = False,
    drop_source: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Move a database to another engine.

    Args:
        source: The engine it is on.
        database: Its name.
        target: The engine it goes to.
        replace: Replace a database the target already holds, after a safety copy.
        drop_source: Drop it from the source once moved, after a last dump.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done. No password is in it: the accounts that were given new
        ones are named, and the console reveals one through sudo mode.
    """
    outcome = DatabaseMover(_service(job_context, f"Moving {database}", actor)).move(
        source, database, target, replace=replace, drop_source=drop_source
    )
    _done(job_context)
    return outcome.to_dict()


def migrate_job(
    source: str,
    to_version: str,
    name: str | None = None,
    memory_mb: int | None = None,
    port: int | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Move an engine's databases to a new instance of a newer major version.

    Args:
        source: The instance or the host's engine.
        to_version: The version to go to.
        name: What to call the new instance.
        memory_mb: Its memory limit.
        port: Its port on 127.0.0.1.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done: the move's number, what each database's move did and
        what was kept for the way back.
    """
    managed = ManagedInstances(_service(job_context, f"Moving {source} to {to_version}", actor))
    outcome = InstanceMigrations(managed).run(
        source, to_version, name=name, memory=memory_mb, port=port
    )
    _done(job_context)
    return outcome.to_dict()


def rollback_job(
    migration: int,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Point the applications back at the engine a move left.

    Args:
        migration: The move's number.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What was done: the databases pointed back, the dumps taken first.
    """
    managed = ManagedInstances(_service(job_context, f"Rolling back move {migration}", actor))
    outcome = InstanceMigrations(managed).rollback(migration)
    _done(job_context)
    return outcome.to_dict()


def tls_job(
    name: str,
    mode: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Change how an instance serves TLS, or sign its certificate again.

    Either restarts the engine and waits for it to answer, which is work that
    must not sit on a request.

    Args:
        name: The instance.
        mode: ``off``, ``optional`` or ``required``; None signs the certificate
            it has again.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The instance's TLS afterwards, as ``InstanceTls.status`` says.
    """
    what = f"Serving TLS as {mode}" if mode else "Renewing the certificate of"
    tls = InstanceTls(ManagedInstances(_service(job_context, f"{what} {name}", actor)))
    status = tls.renew(name) if mode is None else tls.set_mode(name, mode)
    _done(job_context)
    return status


def tuning_job(
    name: str,
    memory_mb: int | None = None,
    cpus: float | None = None,
    swap_mb: int | None = None,
    interval_s: int | None = None,
    timeout_s: int | None = None,
    retries: int | None = None,
    clear: list[str] | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Change what an instance may use, or how it is probed.

    The engine restarts on the new numbers and the job waits for it to answer, which
    is work that must not sit on a request.

    Args:
        name: The instance.
        memory_mb: The new memory limit, or None to leave it.
        cpus: The new CPU limit, or None to leave it.
        swap_mb: The swap it may use besides its memory, or None to leave it.
        interval_s: Seconds between health probes, or None to leave it.
        timeout_s: Seconds a probe may take, or None to leave it.
        retries: Failed probes in a row before it is unhealthy, or None to leave it.
        clear: Settings to put back to their default.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The instance's settings afterwards, as ``InstanceTuning.status`` says.
    """
    tuning = InstanceTuning(ManagedInstances(_service(job_context, f"Changing {name}", actor)))
    status = tuning.apply(
        name,
        memory=memory_mb,
        cpus=cpus,
        swap=swap_mb,
        interval=interval_s,
        timeout=timeout_s,
        retries=retries,
        clear=clear or (),
    )
    _done(job_context)
    return status
