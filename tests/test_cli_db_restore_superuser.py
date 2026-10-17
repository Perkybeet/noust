# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db restore`` and who a PostgreSQL dump is loaded as (4.0, checklist H-19).

A file the operator names is a dump from somewhere, and PostgreSQL loads it as
the database's owner. A file in the engine's own dump folder is one Noust took,
and goes back as it was. ``--as-superuser`` is the explicit way to load a file
from anywhere else with the server's superuser: asked twice (the restore, then
the superuser), audited, and refused for an engine that has none.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.commands import db as db_cli
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.database.base import BaseDatabaseManager
from tests.database_backup_support import PG_LISTING, RcloneRunner, make_engine

SQL = b"-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner
) -> type[BaseDatabaseManager]:
    (tmp_path / "dumps").mkdir()
    cls = make_engine(tmp_path / "dumps", suffix=".sql", content=SQL, databases=("shop",))
    monkeypatch.setattr(db_cli, "get_db_manager", lambda engine, verbose=False: cls())
    return cls


@pytest.fixture
def loads(engine: type[BaseDatabaseManager]) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    original = engine._load_backup

    def spy(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
        seen.append(kwargs)
        original(self, database, backup_path, **kwargs)

    engine._load_backup = spy  # type: ignore[method-assign]
    return seen


def restore(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(cli_app.cli, ["db", "restore", *args], input=input)


def foreign(tmp_path: Path) -> Path:
    path = tmp_path / "from-another-provider.sql"
    path.write_bytes(SQL)
    return path


class TestWhoTheFileIsLoadedAs:
    def test_a_file_from_elsewhere_is_loaded_as_the_owner(
        self, loads: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        result = restore("shop", str(foreign(tmp_path)), "-e", "postgresql", "--force")

        assert result.exit_code == 0, result.output
        assert "as_superuser" not in loads[0]

    def test_a_dump_in_the_engines_own_folder_goes_back_as_it_was(
        self, loads: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        kept = tmp_path / "dumps" / "postgresql-shop-20260101_120000.sql"
        kept.write_bytes(SQL)

        result = restore("shop", str(kept), "-e", "postgresql", "--force")

        assert result.exit_code == 0, result.output
        assert loads[0]["as_superuser"] is True

    def test_the_superuser_is_asked_for_by_name(
        self, loads: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        result = restore(
            "shop", str(foreign(tmp_path)), "-e", "postgresql", "--force", "--as-superuser"
        )

        assert result.exit_code == 0, result.output
        assert loads[0]["as_superuser"] is True

    def test_declining_the_superuser_question_loads_nothing(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        result = restore(
            "shop",
            str(foreign(tmp_path)),
            "-e",
            "postgresql",
            "--as-superuser",
            input="y\nn\n",
        )

        assert result.exit_code == 0
        assert "COPY ... FROM PROGRAM" in result.output
        assert engine.state["calls"] == []

    def test_an_engine_with_no_superuser_load_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner
    ) -> None:
        (tmp_path / "mdumps").mkdir()
        mysql = make_engine(
            tmp_path / "mdumps", engine="mysql", suffix=".sql", content=SQL, databases=("shop",)
        )
        monkeypatch.setattr(db_cli, "get_db_manager", lambda engine, verbose=False: mysql())

        result = restore("shop", str(foreign(tmp_path)), "-e", "mysql", "--force", "--as-superuser")

        assert result.exit_code != 0
        assert "no superuser load" in result.output
        assert mysql.state["calls"] == []
