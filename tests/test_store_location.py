"""
The store is found wherever it already is before a new, empty one is ever chosen.

On 2026-08-13 creating one directory moved a server's store to an empty one and seventeen sites
were reported as "No applications deployed". A store that exists wins over a location that would
be created, whichever of the two places it is in.
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
        "user": tmp_path / "home/.local/share/noust/noust.db",
    }
    monkeypatch.setattr(store_module, "DEFAULT_DB_PATH", locations["new"])
    monkeypatch.setattr(store_module, "USER_DB_PATH", locations["user"])
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
class TestStoreLocation:
    def test_a_package_created_system_directory_does_not_hide_the_users_store(
        self, machine: dict[str, Path]
    ) -> None:
        populate(machine["user"])
        machine["new"].parent.mkdir(parents=True)
        assert resolve(machine) == machine["user"]
        assert not machine["new"].exists()

    def test_the_system_store_wins_once_it_exists(self, machine: dict[str, Path]) -> None:
        populate(machine["user"])
        populate(machine["new"])
        assert resolve(machine) == machine["new"]

    def test_an_empty_file_is_not_an_inventory(self, machine: dict[str, Path]) -> None:
        populate(machine["user"])
        machine["new"].parent.mkdir(parents=True)
        machine["new"].touch()
        assert resolve(machine) == machine["user"]

    def test_a_fresh_machine_gets_the_system_store(self, machine: dict[str, Path]) -> None:
        machine["new"].parent.mkdir(parents=True)
        assert resolve(machine) == machine["new"]

    def test_nothing_anywhere_is_the_per_user_store(self, machine: dict[str, Path]) -> None:
        assert resolve(machine) == machine["user"]
