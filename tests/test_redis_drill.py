# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Redis or Valkey snapshot is loaded into a server of its own to prove it comes back (checklist H-31).

A snapshot replaces every key of an instance, so it cannot be tried beside the real one the way
a SQL dump is tried in a temporary database. It is tried in a second server that listens on a
private socket in a private directory, reads the snapshot as its data, and is stopped.
"""

from __future__ import annotations

import gzip
from pathlib import Path

from noust.core.runner import FakeRunner
from noust.managers.database.redis_drill import redis_restore_test


def snapshot(tmp_path: Path, *, compressed: bool = True, magic: bytes = b"REDIS0011") -> Path:
    """A snapshot as a backup stores it."""
    path = tmp_path / ("redis-0.rdb.gz" if compressed else "redis-0.rdb")
    if compressed:
        with gzip.open(path, "wb") as handle:
            handle.write(magic + b"\xff" * 8)
    else:
        path.write_bytes(magic + b"\xff" * 8)
    return path


def runner_with(keys: str = "7\n") -> FakeRunner:
    """A runner whose second server, once loaded, counts ``keys``."""
    runner = FakeRunner()
    runner.script(["redis-cli", "-s"], stdout=keys)
    runner.script(["valkey-cli", "-s"], stdout=keys)
    return runner


class TestASnapshotThatLoads:
    def test_the_server_reads_it_and_the_keys_are_counted(self, tmp_path: Path) -> None:
        runner = runner_with("7\n")

        result = redis_restore_test(
            runner,
            snapshot(tmp_path),
            server="redis-server",
            client="redis-cli",
            sleep=lambda s: None,
        )

        assert result.ok and not result.skipped
        assert "7 keys" in result.detail
        [process] = runner.processes
        argv = process.argv
        assert argv[0] == "redis-server"
        assert argv[argv.index("--port") + 1] == "0"
        assert "--unixsocket" in argv and "--save" in argv
        assert process.terminated

    def test_it_runs_on_a_private_socket_and_directory_that_are_gone_afterwards(
        self, tmp_path: Path
    ) -> None:
        runner = runner_with()

        redis_restore_test(
            runner,
            snapshot(tmp_path),
            server="valkey-server",
            client="valkey-cli",
            sleep=lambda s: None,
        )

        argv = runner.processes[0].argv
        socket = Path(argv[argv.index("--unixsocket") + 1])
        directory = Path(argv[argv.index("--dir") + 1])
        assert directory == socket.parent
        assert not directory.exists()
        assert directory != tmp_path

    def test_an_uncompressed_snapshot_is_loaded_too(self, tmp_path: Path) -> None:
        runner = runner_with()

        result = redis_restore_test(
            runner,
            snapshot(tmp_path, compressed=False),
            server="redis-server",
            client="redis-cli",
            sleep=lambda s: None,
        )

        assert result.ok


class TestASnapshotThatDoesNot:
    def test_a_server_that_exits_is_a_failure_with_its_own_words(self, tmp_path: Path) -> None:
        runner = runner_with()
        runner.start_dead = True  # type: ignore[attr-defined]

        def start(argv, **kwargs):  # type: ignore[no-untyped-def]
            from noust.core.runner import FakeProcess

            process = FakeProcess(
                tuple(argv),
                alive=False,
                exit_code=1,
                stderr="Fatal error loading the DB, check server logs. Exiting.",
            )
            runner.processes.append(process)
            return process

        runner.start = start  # type: ignore[method-assign]

        result = redis_restore_test(
            runner,
            snapshot(tmp_path),
            server="redis-server",
            client="redis-cli",
            sleep=lambda s: None,
        )

        assert not result.ok
        assert "Fatal error loading the DB" in result.detail

    def test_a_file_that_is_not_a_snapshot_never_starts_a_server(self, tmp_path: Path) -> None:
        runner = runner_with()

        result = redis_restore_test(
            runner,
            snapshot(tmp_path, magic=b"*3\r\n$3\r\n"),
            server="redis-server",
            client="redis-cli",
            sleep=lambda s: None,
        )

        assert result.skipped and "append-only" in result.detail
        assert runner.processes == []


class TestWhenTheProgramIsNotThere:
    def test_the_test_is_skipped_and_says_what_to_install(self, tmp_path: Path) -> None:
        runner = runner_with().only_knows()

        result = redis_restore_test(
            runner,
            snapshot(tmp_path),
            server="redis-server",
            client="redis-cli",
            sleep=lambda s: None,
        )

        assert result.skipped and "redis-server" in result.detail
        assert runner.processes == []
