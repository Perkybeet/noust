# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the database backup tests share: an engine that really writes dumps, and rclone.

Two fakes, both honest about the one thing the code under test depends on:

- :func:`make_engine` builds an engine manager class whose ``backup`` writes a
  file, whose ``_load_backup`` records what it loaded (and can be made to
  fail), and whose ``restore`` is the real
  :meth:`~noust.managers.database.base.BaseDatabaseManager.restore`, safety
  copy and put-back included. A fake dump that is only a return value cannot
  be hashed, checked, retained or sent, which is most of what is tested.
- :class:`RcloneRunner` is a :class:`~noust.core.runner.FakeRunner` that answers
  the handful of rclone verbs Noust uses out of a directory tree, so a push, a
  listing, a download and a deletion run through the real destination manager
  and only the network is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noust.core.exceptions import DatabaseBackupError, DatabaseNotFoundError
from noust.core.runner import DEFAULT_TIMEOUT, CommandResult, FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.database.base import BackupInfo, BaseDatabaseManager, DatabaseInfo, UserInfo
from noust.managers.database.service import DatabaseService

#: The first bytes of a pg_dump custom archive, and a listing of one.
PG_ARCHIVE = b"PGDMP\x01\x0e\x00fake archive"
PG_LISTING = "; Archive created at 2026-09-29\n1; 2615 2200 SCHEMA - public postgres\n"

#: A plain pg_dump, whole and cut short.
PG_PLAIN = b"-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"
PG_PLAIN_CUT = b"-- PostgreSQL database dump\nSELECT 1;\nCREATE TABLE half ("

#: A mysqldump, whole and cut short.
MYSQL_DUMP = b"-- MySQL dump\nCREATE TABLE t (i int);\n-- Dump completed on 2026-09-29 10:00:00\n"
MYSQL_DUMP_CUT = b"-- MySQL dump\nCREATE TABLE t (i int);\nINSERT INTO t VALUES (1"


def age(path: Path, days: float) -> None:
    """
    Make a file look ``days`` old.

    Args:
        path: The file.
        days: How old.
    """
    stamp = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def make_engine(
    backup_dir: Path,
    *,
    engine: str = "postgresql",
    suffix: str = ".dump",
    content: bytes = PG_ARCHIVE,
    databases: Sequence[str] = ("shop",),
) -> type[BaseDatabaseManager]:
    """
    Build an engine manager class that writes real dump files.

    State lives on the class, because the service builds a manager per call and
    two calls must see the same databases; a fresh class per test keeps tests
    apart.

    Args:
        backup_dir: Where the engine keeps its dumps.
        engine: The engine's name.
        suffix: The dump file extension it writes.
        content: What each dump holds.
        databases: The databases it starts with.

    Returns:
        The manager class. ``state`` holds ``dbs``, ``calls`` (what was done,
        in order), ``fail_load`` (a message that makes loading a dump fail)
        and ``dumps`` (how many were taken).
    """

    class Fake(BaseDatabaseManager):
        ENGINE_NAME = engine
        DISPLAY_NAME = engine.title()
        DEFAULT_PORT = 5432
        SERVICE_NAME = engine
        CLIENT_BINARY = "fake-client"
        BACKUP_SUFFIX = suffix
        CAPABILITIES = frozenset({"sql", "dump"})
        BACKUP_DIR = backup_dir

        state: dict[str, Any] = {
            "dbs": {name: {"owner": "app", "tables": 3} for name in databases},
            "calls": [],
            "fail_load": None,
            "dumps": 0,
            "content": content,
            "installed": True,
            "running": True,
        }

        def is_installed(self) -> bool:
            return True

        def is_running(self) -> bool:
            return type(self).state["running"]

        def get_version(self) -> str | None:
            return "16.4"

        def server_port(self) -> int:
            return 5432

        def warnings(self) -> list[str]:
            return []

        def create_database(self, name, owner=None, encoding=None, **kwargs):
            type(self).state["calls"].append(("create", name))
            type(self).state["dbs"][name] = {"owner": owner, "tables": 0}
            return DatabaseInfo(name=name, engine=engine, owner=owner)

        def drop_database(self, name, force=False):
            type(self).state["calls"].append(("drop", name))
            type(self).state["dbs"].pop(name, None)

        def database_exists(self, name):
            return name in type(self).state["dbs"]

        def list_databases(self):
            return [
                DatabaseInfo(name=name, engine=engine, owner=entry["owner"], tables=entry["tables"])
                for name, entry in type(self).state["dbs"].items()
            ]

        def get_database_info(self, name):
            entry = type(self).state["dbs"].get(name)
            if entry is None:
                raise DatabaseNotFoundError(f"Database '{name}' does not exist")
            return DatabaseInfo(
                name=name, engine=engine, owner=entry["owner"], tables=entry["tables"]
            )

        def create_user(self, username, password=None, host="localhost", **kwargs):
            return UserInfo(username=username, engine=engine, host=host), password or "x"

        def drop_user(self, username, host="localhost"):
            return None

        def user_exists(self, username, host="localhost"):
            return False

        def list_users(self):
            return []

        def grant_privileges(self, username, database, privileges=None, host="localhost"):
            return None

        def revoke_privileges(self, username, database, privileges=None, host="localhost"):
            return None

        def backup(self, database, output_path=None, compress=True, **kwargs):
            state = type(self).state
            state["dumps"] += 1
            state["calls"].append(("backup", database))
            stamp = f"20260101_{120000 + state['dumps']:06d}"
            path = output_path or (backup_dir / f"{engine}-{database}-{stamp}{suffix}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(state["content"])
            return BackupInfo(
                path=path,
                database=database,
                engine=engine,
                size=path.stat().st_size,
                created=datetime.fromtimestamp(path.stat().st_mtime),
            )

        def _load_backup(self, database, backup_path, **kwargs):
            state = type(self).state
            state["calls"].append(("load", database, Path(backup_path).name))
            if state["fail_load"]:
                raise DatabaseBackupError("Failed to restore", details=state["fail_load"])
            state["dbs"].setdefault(database, {"owner": None, "tables": 0})["tables"] = 7

        def execute_query(self, database, query, **kwargs):
            return True, ""

    return Fake


def make_service(
    tmp_path: Path, engine_classes: Sequence[type[BaseDatabaseManager]]
) -> DatabaseService:
    """
    Build a database service over fake engines and an isolated store.

    Args:
        tmp_path: Per-test temporary directory.
        engine_classes: The fake engines.

    Returns:
        The service. Its store is the process-wide singleton, which the caller
        resets between tests.
    """
    by_name = {cls.ENGINE_NAME: cls for cls in engine_classes}

    def resolve(name: str) -> BaseDatabaseManager | None:
        cls = by_name.get(name)
        return cls() if cls else None

    return DatabaseService(
        store=NoustStore(tmp_path / "noust.db"),
        secrets=SecretStore(root=tmp_path / "secrets"),
        resolve=resolve,
        engines=lambda: list(by_name),
    )


class RcloneRunner(FakeRunner):
    """
    A fake runner that answers rclone out of a directory tree.

    A remote reference such as ``nas:wasm-backups/databases/postgresql/shop``
    is the directory ``<root>/nas/wasm-backups/databases/postgresql/shop``.
    Every other command behaves as :class:`FakeRunner` does, so a test scripts
    the engine's tools and leaves rclone to this.

    Attributes:
        root: Where the remotes live.
        fail: Verb to the stderr it fails with.
        refusing: Remote name to the stderr every command touching it fails with.
    """

    def __init__(self, root: Path, **kwargs: Any) -> None:
        """
        Args:
            root: Where the remotes live.
            **kwargs: For :class:`FakeRunner`.
        """
        super().__init__(**kwargs)
        self.root = root
        self.fail: dict[str, str] = {}
        self.refusing: dict[str, str] = {}
        self.only_knows("rclone", "gzip", "tar", "pg_restore")

    def remote_path(self, ref: str) -> Path | None:
        """
        Args:
            ref: An rclone reference.

        Returns:
            The directory or file it stands for, or None for a local path.
        """
        match = re.match(r"^([a-z0-9-]+):(.*)$", ref)
        if not match:
            return None
        return self.root / match.group(1) / match.group(2).lstrip("/")

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input: str | None = None,
        stdin_path: Path | None = None,
        user: str | None = None,
        check: bool = False,
        secrets: Sequence[str] = (),
    ) -> CommandResult:
        scripted = super().run(
            argv, cwd=cwd, env=env, timeout=timeout, input=input, user=user, check=check
        )
        if len(argv) < 2 or argv[0] != "rclone" or argv[1] == "obscure":
            return scripted
        verb = argv[1]
        if verb in self.fail:
            return CommandResult(argv=tuple(argv), exit_code=1, stderr=self.fail[verb])
        for remote, stderr in self.refusing.items():
            if any(re.match(rf"^{remote}(crypt)?:", str(arg)) for arg in argv[2:]):
                return CommandResult(argv=tuple(argv), exit_code=1, stderr=stderr)
        handler = getattr(self, f"_rclone_{verb}", None)
        if handler is None:
            return scripted
        code, out, err = handler(list(argv[2:]), input)
        return CommandResult(argv=tuple(argv), exit_code=code, stdout=out, stderr=err)

    # ------------------------------------------------------------- the verbs

    def _rclone_mkdir(self, args: list[str], _: str | None) -> tuple[int, str, str]:
        target = self.remote_path(args[0])
        assert target is not None
        target.mkdir(parents=True, exist_ok=True)
        return 0, "", ""

    def _rclone_lsf(self, args: list[str], _: str | None) -> tuple[int, str, str]:
        target = self.remote_path(args[0])
        assert target is not None
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        return 0, "\n".join(names), ""

    def _rclone_lsjson(self, args: list[str], _: str | None) -> tuple[int, str, str]:
        hashes = "--hash" in args
        reference = next(a for a in args if not a.startswith("--"))
        target = self.remote_path(reference)
        assert target is not None
        if not target.is_dir():
            return 3, "", "directory not found"
        crypt = "crypt" in reference.split(":")[0]
        entries = []
        for path in sorted(target.iterdir()):
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            entry: dict[str, Any] = {
                "Name": path.name,
                "Size": path.stat().st_size if path.is_file() else -1,
                "ModTime": stamp.isoformat().replace("+00:00", "Z"),
                "IsDir": path.is_dir(),
            }
            if hashes and path.is_file() and not crypt:
                entry["Hashes"] = {"md5": hashlib.md5(path.read_bytes()).hexdigest()}  # noqa: S324
            entries.append(entry)
        return 0, json.dumps(entries), ""

    def _rclone_copyto(self, args: list[str], _: str | None) -> tuple[int, str, str]:
        source = self.remote_path(args[0]) or Path(args[0])
        destination = self.remote_path(args[1]) or Path(args[1])
        if not source.is_file():
            return 3, "", f"Failed to copyto: object not found: {args[0]}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return 0, "", ""

    def _rclone_rcat(self, args: list[str], text: str | None) -> tuple[int, str, str]:
        destination = self.remote_path(args[0])
        assert destination is not None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text or "")
        return 0, "", ""

    def _rclone_cat(self, args: list[str], _: str | None) -> tuple[int, str, str]:
        target = self.remote_path(args[0])
        assert target is not None
        if target.is_dir():
            return 0, "".join(p.read_text() for p in sorted(target.glob("*.json"))), ""
        if not target.is_file():
            return 3, "", f"object not found: {args[0]}"
        return 0, target.read_text(), ""

    def _rclone_deletefile(self, args: list[str], _: str | None) -> tuple[int, str, str]:
        target = self.remote_path(args[0])
        assert target is not None
        if not target.is_file():
            return 4, "", f"Failed to deletefile: object not found: {args[0]}"
        target.unlink()
        return 0, "", ""
