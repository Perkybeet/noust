"""
The store is found wherever WASM, or a migration stopped halfway, left it.

On 2026-08-13 creating one directory moved a server's store to an empty one
and seventeen sites were reported as "No applications deployed". The rename
to Noust adds four names a store can have (``/var/lib/noust/noust.db``, the
same file before its directory moved, WASM's ``/var/lib/wasm/wasm.db`` and
the per-user twins), and every one of them must be found before a new, empty
store is ever chosen.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import store as store_module
from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore


@pytest.fixture
def fresh() -> Iterator[None]:
    """A store singleton this test owns."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """
    Every store location, under ``tmp_path``, none of them created.

    Returns:
        Name to path.
    """
    locations = {
        "new": tmp_path / "var/lib/noust/noust.db",
        "legacy": tmp_path / "var/lib/wasm/wasm.db",
        "user": tmp_path / "home/.local/share/noust/noust.db",
        "legacy_user": tmp_path / "home/.local/share/wasm/wasm.db",
    }
    monkeypatch.setattr(store_module, "DEFAULT_DB_PATH", locations["new"])
    monkeypatch.setattr(store_module, "LEGACY_DB_PATH", locations["legacy"])
    monkeypatch.setattr(store_module, "USER_DB_PATH", locations["user"])
    monkeypatch.setattr(store_module, "LEGACY_USER_DB_PATH", locations["legacy_user"])
    return locations


def populate(path: Path) -> None:
    """
    Create a non-empty database file.

    Args:
        path: Where.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE apps (name TEXT)")
    connection.execute("INSERT INTO apps VALUES ('shop')")
    connection.commit()
    connection.close()


def resolve(machine: dict[str, Path]) -> Path:
    """Ask a store where it would live when given no path."""
    explicit = machine["new"].parents[3] / "explicit.db"
    return NoustStore(explicit, fs=RecordingFileSystem())._resolve_db_path()


@pytest.mark.usefixtures("fresh")
class TestLegacyStore:
    def test_a_wasm_store_is_used_where_it_is(self, machine: dict[str, Path]) -> None:
        populate(machine["legacy"])
        assert resolve(machine) == machine["legacy"]

    def test_a_package_created_noust_directory_does_not_hide_it(
        self, machine: dict[str, Path]
    ) -> None:
        populate(machine["legacy"])
        machine["new"].parent.mkdir(parents=True)
        assert resolve(machine) == machine["legacy"]
        assert not machine["new"].exists()

    def test_the_noust_store_wins_once_it_exists(self, machine: dict[str, Path]) -> None:
        populate(machine["legacy"])
        populate(machine["new"])
        assert resolve(machine) == machine["new"]

    def test_a_store_renamed_before_its_directory_moved(self, machine: dict[str, Path]) -> None:
        renamed = machine["legacy"].parent / "noust.db"
        populate(renamed)
        assert resolve(machine) == renamed

    def test_a_directory_moved_before_its_store_was_renamed(self, machine: dict[str, Path]) -> None:
        moved = machine["new"].parent / "wasm.db"
        populate(moved)
        assert resolve(machine) == moved

    def test_after_the_migration_through_the_link(self, machine: dict[str, Path]) -> None:
        populate(machine["new"])
        machine["legacy"].parent.parent.mkdir(parents=True, exist_ok=True)
        machine["legacy"].parent.symlink_to(machine["new"].parent)
        assert resolve(machine) == machine["new"]

    def test_a_per_user_wasm_store_is_not_abandoned(self, machine: dict[str, Path]) -> None:
        populate(machine["legacy_user"])
        machine["new"].parent.mkdir(parents=True)
        assert resolve(machine) == machine["legacy_user"]

    def test_a_fresh_machine_gets_the_noust_store(self, machine: dict[str, Path]) -> None:
        machine["new"].parent.mkdir(parents=True)
        assert resolve(machine) == machine["new"]

    def test_nothing_anywhere_is_the_per_user_noust_store(self, machine: dict[str, Path]) -> None:
        assert resolve(machine) == machine["user"]
        assert not machine["user"].parent.exists()


def test_the_suite_never_reaches_a_real_legacy_store() -> None:
    home = Path.home()
    assert home not in store_module.LEGACY_USER_DB_PATH.parents
    assert Path("/var/lib/wasm") not in store_module.LEGACY_DB_PATH.parents
