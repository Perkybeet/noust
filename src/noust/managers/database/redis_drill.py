# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Loading a Redis or Valkey snapshot into a server of its own.

A snapshot replaces every key of the instance it is restored into, so it cannot be tried in a
temporary database beside the real data the way a SQL dump is (:func:`~noust.managers.database.backup_verify.restore_test`).
It is tried instead in a **second server** that nothing else can reach: it listens on no port
(``--port 0``) but on a Unix socket inside a private directory, reads the snapshot as its
data set, answers how many keys it holds, and is stopped. The directory goes with it.

Only a snapshot (an RDB file) is loaded this way. An append-only file is a log the server
replays, which is a different test; it is said rather than guessed.
"""

from __future__ import annotations

import gzip
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from noust.core.runner import CommandRunner, ProcessHandle
from noust.managers.database.backup_verify import RestoreTest

__all__ = ["redis_restore_test"]

#: How long the second server gets to load the snapshot: attempts and seconds between them.
LOAD_ATTEMPTS = 60
LOAD_INTERVAL = 1.0

#: Seconds one client call may take.
CLIENT_TIMEOUT = 15

#: The first bytes of every RDB file.
_RDB_MAGIC = b"REDIS"

#: What ``DBSIZE`` prints when the server has answered, with or without redis-cli's type prefix.
_COUNT = re.compile(r"(?:\(integer\)\s*)?(\d+)")


def _skipped(detail: str) -> RestoreTest:
    """
    Describe a test that was not tried.

    Args:
        detail: Why.

    Returns:
        A passing result flagged as skipped.
    """
    return RestoreTest(True, "", detail, 0.0, skipped=True)


def redis_restore_test(
    runner: CommandRunner,
    dump: Path,
    *,
    server: str,
    client: str,
    sleep: Callable[[float], None] = time.sleep,
) -> RestoreTest:
    """
    Load a snapshot into a second server and count its keys.

    Args:
        runner: Every process goes through it.
        dump: The snapshot, gzipped or not.
        server: The server program (``redis-server`` or ``valkey-server``).
        client: The client program (``redis-cli`` or ``valkey-cli``).
        sleep: Pauses while the server loads; replaceable for tests.

    Returns:
        What happened. A snapshot the server refuses is a result, not an exception.
    """
    if not (runner.exists(server) and runner.exists(client)):
        return _skipped(
            f"{server} is not installed on this server, so a snapshot cannot be loaded into a "
            "second server here; its file is checked with the archive"
        )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="noust-redis-drill-") as scratch:
        directory = Path(scratch)
        target = directory / "dump.rdb"
        try:
            opener = gzip.open if _is_gzip(dump) else open
            with opener(dump, "rb") as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
        except (OSError, EOFError) as exc:
            return RestoreTest(False, "", f"The snapshot cannot be read: {exc}", 0.0)
        with target.open("rb") as handle:
            head = handle.read(len(_RDB_MAGIC))
        if head != _RDB_MAGIC:
            return _skipped(
                "this is an append-only file, which the server replays instead of loading; "
                "only a snapshot is loaded into a second server. Its file is checked with the "
                "archive"
            )
        socket = directory / "redis.sock"
        process = runner.start(
            [
                server,
                "--port",
                "0",
                "--unixsocket",
                str(socket),
                "--unixsocketperm",
                "700",
                "--dir",
                str(directory),
                "--dbfilename",
                target.name,
                "--save",
                "",
                "--appendonly",
                "no",
            ]
        )
        try:
            return _count_keys(runner, process, client, socket, started, sleep)
        finally:
            process.terminate()


def _is_gzip(path: Path) -> bool:
    """
    Args:
        path: A file.

    Returns:
        Whether it starts with gzip's magic bytes.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _count_keys(
    runner: CommandRunner,
    process: ProcessHandle,
    client: str,
    socket: Path,
    started: float,
    sleep: Callable[[float], None],
) -> RestoreTest:
    """
    Ask the second server how many keys it holds, once it has finished loading.

    Args:
        runner: Runs the client.
        process: The server's handle, to notice that it ended.
        client: The client program.
        socket: The server's socket.
        started: When the test began.
        sleep: Pauses between attempts.

    Returns:
        The result.
    """
    last = ""
    for attempt in range(LOAD_ATTEMPTS):
        if not process.is_alive():
            said = process.stderr_tail()
            return RestoreTest(
                False,
                "",
                "The second server stopped instead of loading the snapshot: "
                + (said or f"exit code {process.exit_code}"),
                time.monotonic() - started,
            )
        result = runner.run([client, "-s", str(socket), "DBSIZE"], timeout=CLIENT_TIMEOUT)
        last = (result.stdout or result.stderr).strip()
        found = _COUNT.fullmatch(last) if result.success else None
        if found:
            seconds = time.monotonic() - started
            return RestoreTest(
                True,
                "",
                f"Loaded into a second server in {seconds:.1f} s ({int(found.group(1))} keys); "
                "the server was stopped afterwards.",
                seconds,
            )
        if attempt + 1 < LOAD_ATTEMPTS:
            sleep(LOAD_INTERVAL)
    return RestoreTest(
        False,
        "",
        f"The second server never answered after loading the snapshot: {last or 'no output'}",
        time.monotonic() - started,
    )
