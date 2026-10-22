# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The PostgreSQL loader and an import's "keep owners" option (4.0, checklist H-18).

Off, the owners and privileges a dump names (the roles of the server it came
from) are left out and every object is created as the database's own owner:
``pg_restore`` is told ``--no-owner --no-acl --role=<owner>``, and a plain dump
is cleaned of those statements before it is staged for psql. On, nothing about
the command changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.managers.database import postgres as postgres_module
from noust.managers.database.postgres import PostgresManager
from tests.test_database_import_formats import DUMP_WITH_OWNERS, PG_CUSTOM
from tests.test_database_managers import StubConfig


@pytest.fixture
def postgres(tmp_path: Path) -> PostgresManager:
    manager = PostgresManager()
    manager.BACKUP_DIR = tmp_path / "engine-backups"
    manager.config = StubConfig({})  # type: ignore[assignment]
    return manager


@pytest.fixture(autouse=True)
def database_exists(runner: FakeRunner) -> None:
    runner.script(("runuser", "-u", "postgres", "--", "psql"), stdout="1")


def test_pg_restore_is_told_no_owner_no_acl_and_no_role_to_become(
    postgres: PostgresManager, runner: FakeRunner, tmp_path: Path, ownership_changes
) -> None:
    dump = tmp_path / "d.dump"
    dump.write_bytes(PG_CUSTOM)

    postgres.restore("shop", dump, safety_backup=False, keep_owners=False, as_superuser=True)

    argv = runner.calls[-1]
    assert argv[argv.index("pg_restore") :][:4] == (
        "pg_restore",
        "--no-password",
        "--no-owner",
        "--no-acl",
    )
    # The superuser asked for by name is one: a role switched to is one RESET ROLE from gone.
    assert not any(part.startswith("--role") for part in argv)


def test_keeping_owners_is_what_pg_restore_always_did(
    postgres: PostgresManager, runner: FakeRunner, tmp_path: Path, ownership_changes
) -> None:
    dump = tmp_path / "d.dump"
    dump.write_bytes(PG_CUSTOM)

    postgres.restore("shop", dump, safety_backup=False, as_superuser=True)

    argv = runner.calls[-1]
    assert "--no-owner" not in argv and "--no-acl" not in argv
    assert argv[argv.index("pg_restore") :][:3] == ("pg_restore", "--no-password", "-d")


def test_a_plain_dump_is_cleaned_of_owners_before_psql_reads_it(
    postgres: PostgresManager,
    runner: FakeRunner,
    tmp_path: Path,
    ownership_changes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    real = postgres_module.strip_owners

    def spy(source: Path, destination: Path, *, role: str | None = None) -> int:
        seen["role"] = role
        seen["source"] = source.name
        return real(source, destination, role=role)

    monkeypatch.setattr(postgres_module, "strip_owners", spy)
    dump = tmp_path / "d.sql"
    dump.write_text(DUMP_WITH_OWNERS)

    postgres.restore("shop", dump, safety_backup=False, keep_owners=False, as_superuser=True)

    assert seen == {"role": None, "source": "d.sql"}
    # psql reads the cleaned copy as its stdin: not the dump the operator gave.
    assert runner.stdin_paths[-1].name == "owners-stripped.sql"
    assert runner.stdin_paths[-1] != dump


def test_a_plain_dump_is_left_alone_when_owners_are_kept(
    postgres: PostgresManager,
    runner: FakeRunner,
    tmp_path: Path,
    ownership_changes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> int:
        raise AssertionError("the dump must not be filtered")

    monkeypatch.setattr(postgres_module, "strip_owners", refuse)
    dump = tmp_path / "d.sql"
    dump.write_text(DUMP_WITH_OWNERS)

    postgres.restore("shop", dump, safety_backup=False, keep_owners=True, as_superuser=True)

    assert runner.stdin_paths[-1] == dump
    assert not runner.calls_to("cp")
