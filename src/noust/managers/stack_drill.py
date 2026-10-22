# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Loading the dump of a Compose stack's database into a throwaway container.

A database that lives in a container cannot be tried on the side the way a host's can (a
temporary database beside the real one): the stack's own container is the real one, and a
restore replaces what it holds. So the dump is tried where nothing is at stake. A container of
**the same image the service ran** is started on no network, with a password made for the
occasion; the dump is fed to it by the very command a real restore runs
(:func:`~noust.managers.stack_databases.client_command`), and what it holds is counted. The
container is then removed, by its exact name and with its anonymous volume, whatever happened.

What makes it safe on a machine that runs other people's containers:

* the name is ``noust-drill-<random>`` and the label ``noust.drill=1``, so what the test made can
  be told from everything else and is removed by name, never by a prune or a filter;
* no network, no published port, a memory and a process limit: a dump cannot reach anything;
* the image is the one the dump's manifest records, checked to be the official image of the
  engine, and refused when it could be read as an option;
* the throwaway password reaches Docker through the environment of the ``docker`` process and is
  in no argument.

What it does not prove is that the application works on the restored data; that is the drill
(:mod:`noust.managers.restore_drill`) starting the application.
"""

from __future__ import annotations

import re
import secrets
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from noust.core.runner import CommandRunner
from noust.managers.database.backup_records import bound_evidence
from noust.managers.stack_databases import (
    COMPOSE_TIMEOUT,
    RESTORE_TIMEOUT,
    StackDatabase,
    client_command,
    image_engine,
    inflated,
)

__all__ = ["CONTAINER_LABEL", "READY_ATTEMPTS", "StackRestoreTest", "restore_test_stack_dump"]

#: The label every throwaway container carries.
CONTAINER_LABEL = "noust.drill"

#: How long the engine gets to finish its first start: attempts, and seconds between them.
READY_ATTEMPTS = 90
READY_INTERVAL = 2.0

#: Seconds ``docker run`` gets: the image may have to be pulled.
RUN_TIMEOUT = 900

#: Memory and processes the container may use.
MEMORY_LIMIT = "1g"
PIDS_LIMIT = "1024"

#: What an image reference may look like. Anything else could be read by ``docker`` as an option.
_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}")

#: What each official image prints when its first start, the one that creates the database,
#: is over and the server is about to restart. Loading before it is loading into a server that
#: is going away.
_FIRST_START_OVER = {
    "postgres": "init process complete",
    "mysql": "init process done",
    "mariadb": "init process done",
    "mongo": "init process complete",
}


@dataclass(frozen=True)
class StackRestoreTest:
    """
    What loading a stack's dump into a throwaway container found.

    Attributes:
        ok: The dump loaded (or the test could not be tried, see ``skipped``).
        detail: What was found, or the engine's own words when it failed.
        seconds: How long it took.
        skipped: It was not tried, and ``detail`` says why.
    """

    ok: bool
    detail: str
    seconds: float = 0.0
    skipped: bool = False


def _environment(database: StackDatabase) -> tuple[dict[str, str], list[str]]:
    """
    Make the variables the image starts its database from.

    Args:
        database: The database the container stands in for.

    Returns:
        The secrets (passed through the environment) and the plain ``NAME=value`` settings
        (passed as arguments: a user and a database name are not secrets).
    """
    secret: dict[str, str] = {}
    plain: list[str] = []
    if database.engine == "postgres":
        secret["POSTGRES_PASSWORD"] = secrets.token_urlsafe(24)
        plain += [f"POSTGRES_USER={database.user}", f"POSTGRES_DB={database.database}"]
    elif database.engine == "mongo":
        if database.user:
            secret["MONGO_INITDB_ROOT_PASSWORD"] = secrets.token_urlsafe(24)
            plain.append(f"MONGO_INITDB_ROOT_USERNAME={database.user}")
        if database.database:
            plain.append(f"MONGO_INITDB_DATABASE={database.database}")
    else:
        prefix = "MARIADB_" if database.engine == "mariadb" else "MYSQL_"
        secret[f"{prefix}ROOT_PASSWORD"] = secrets.token_urlsafe(24)
        if database.user != "root":
            secret[f"{prefix}PASSWORD"] = secrets.token_urlsafe(24)
            plain.append(f"{prefix}USER={database.user}")
        if database.database:
            plain.append(f"{prefix}DATABASE={database.database}")
    return secret, plain


def _refusal(database: StackDatabase) -> str | None:
    """
    Say why a dump cannot be tried on this image, when it cannot.

    Args:
        database: The database, with the image its dump was taken from.

    Returns:
        The reason, or None when the image can be run.
    """
    if not database.image:
        return (
            f"{database.label}: the backup does not record the image the service ran, so a "
            "throwaway container of the same version cannot be started; the next backup records it"
        )
    if not _IMAGE.fullmatch(database.image):
        return f"{database.label}: the image name recorded in the backup is not one Noust runs"
    if image_engine(database.image) != database.engine:
        return (
            f"{database.label}: {database.image} is not the official {database.engine} image, "
            "whose variables a throwaway container is started from"
        )
    return None


def restore_test_stack_dump(
    runner: CommandRunner,
    database: StackDatabase,
    dump: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
    token: str | None = None,
    attempts: int = READY_ATTEMPTS,
) -> StackRestoreTest:
    """
    Load a stack's database dump into a throwaway container and count what it holds.

    Args:
        runner: Every process goes through it.
        database: The database the dump is of, with the image it ran.
        dump: The dump file, gzipped or not.
        sleep: Pauses while the engine starts; replaceable for tests.
        token: The random part of the container's name; generated when None.
        attempts: How many times the engine is asked whether it has finished starting.

    Returns:
        What happened. A dump the engine refuses is a result, not an exception.
    """
    reason = _refusal(database)
    if reason is not None:
        return StackRestoreTest(True, reason, skipped=True)
    started = time.monotonic()
    name = f"noust-drill-{token or secrets.token_hex(4)}"
    secret, plain = _environment(database)
    argv = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--label",
        f"{CONTAINER_LABEL}=1",
        "--network",
        "none",
        "--memory",
        MEMORY_LIMIT,
        "--pids-limit",
        PIDS_LIMIT,
        *(part for variable in secret for part in ("-e", variable)),
        *(part for setting in plain for part in ("-e", setting)),
        database.image,
    ]
    try:
        run = runner.run(argv, env=secret, timeout=RUN_TIMEOUT)
        if not run.success:
            return StackRestoreTest(
                False,
                f"The throwaway container of {database.image} did not start: "
                f"{run.stderr.strip() or f'exit code {run.exit_code}'}",
                time.monotonic() - started,
            )
        problem = _wait_until_ready(runner, name, database, sleep, attempts)
        if problem is not None:
            return StackRestoreTest(False, problem, time.monotonic() - started)
        with tempfile.TemporaryDirectory(prefix="noust-drill-stack-") as workspace:
            loaded = runner.run(
                ["docker", "exec", "-i", name, *client_command(database, "restore")],
                stdin_path=inflated(dump, Path(workspace)),
                timeout=RESTORE_TIMEOUT,
            )
        if not loaded.success:
            said = (loaded.stderr or loaded.stdout).strip() or f"exit code {loaded.exit_code}"
            return StackRestoreTest(
                False,
                f"{database.label} did not load into a clean {database.image}: "
                f"{bound_evidence(said)}",
                time.monotonic() - started,
            )
        counted = runner.run(
            ["docker", "exec", name, *client_command(database, "tables")],
            timeout=COMPOSE_TIMEOUT,
        )
        found = _count(counted.stdout) if counted.success else None
        seconds = time.monotonic() - started
        held = f"{found} tables" if found is not None else "loaded"
        return StackRestoreTest(
            True,
            f"{database.label} loaded into a clean {database.image} in {seconds:.1f} s "
            f"({held}); the container was removed afterwards.",
            seconds,
        )
    finally:
        # By exact name, and with its anonymous volume: the data of a test is nobody's.
        runner.run(["docker", "rm", "-f", "-v", name], timeout=COMPOSE_TIMEOUT)


def _count(output: str) -> int | None:
    """
    Read the number a count query printed.

    Args:
        output: The client's standard output.

    Returns:
        The last whole number on its own line, or None.
    """
    for line in reversed(output.strip().splitlines()):
        if line.strip().isdigit():
            return int(line.strip())
    return None


def _wait_until_ready(
    runner: CommandRunner,
    name: str,
    database: StackDatabase,
    sleep: Callable[[float], None],
    attempts: int,
) -> str | None:
    """
    Wait for the engine to finish the first start of its image and accept connections.

    Args:
        runner: Runs the checks.
        name: The container.
        database: The database whose engine is awaited.
        sleep: Pauses between attempts.
        attempts: How many times to ask.

    Returns:
        None when it is ready, else what to tell the operator (the engine's log included).
    """
    marker = _FIRST_START_OVER[database.engine]
    last = ""
    for attempt in range(attempts):
        logs = runner.run(["docker", "logs", name], timeout=COMPOSE_TIMEOUT)
        last = ((logs.stdout or "") + (logs.stderr or "")).strip()
        if marker in last:
            ping = runner.run(
                ["docker", "exec", name, *client_command(database, "ping", tcp=True)],
                timeout=COMPOSE_TIMEOUT,
            )
            if ping.success:
                return None
        if attempt + 1 < attempts:
            sleep(READY_INTERVAL)
    tail = "\n".join(last.splitlines()[-12:])
    return (
        f"The {database.engine} of the throwaway container never finished starting "
        f"({attempts} attempts)." + (f"\n{tail}" if tail else "")
    )
