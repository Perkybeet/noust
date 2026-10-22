# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQLite databases an application keeps: found, checked and copied (checklist C-15).

The manager runs against real SQLite files in a temporary application directory, since the
point is what SQLite itself says; the API against the two-team world, since what an
application shows depends on who asks; the command line through Click.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import DatabaseError, ValidationError
from noust.core.store import App, NoustStore
from noust.managers import sqlite_files
from noust.managers.sqlite_snapshot import open_readonly
from noust.web.auth import STATE_DIR_ENV
from tests.teams_support import World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


def make_database(
    path: Path, rows: int = 10, *, wal: bool = False, page_size: int | None = None
) -> sqlite3.Connection:
    """
    Write a SQLite database.

    Args:
        path: Where.
        rows: Rows in its one table, which has an index.
        wal: Put it in WAL mode with the last rows left in the -wal file.
        page_size: Bytes per page, SQLite's default when omitted.

    Returns:
        The open connection: closing a WAL database checkpoints it, and an application keeps
        its database open.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    if page_size is not None:
        connection.execute(f"PRAGMA page_size={page_size}")
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    connection.execute("CREATE INDEX notes_body ON notes (body)")
    connection.execute("BEGIN")
    connection.executemany(
        "INSERT INTO notes (body) VALUES (?)", [(f"row {n}",) for n in range(rows)]
    )
    connection.execute("COMMIT")
    return connection


@pytest.fixture
def app(tmp_path: Path) -> App:
    """An application deployed in place, in a directory of its own."""
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    return App(domain="shop.example.com", app_path=str(root), layout="inplace")


@pytest.fixture
def opened() -> Iterator[list[sqlite3.Connection]]:
    """Connections a test leaves open, closed afterwards."""
    held: list[sqlite3.Connection] = []
    yield held
    for connection in held:
        connection.close()


class TestFindingThem:
    def test_the_databases_in_the_data_directory_are_listed_with_what_the_header_says(
        self, app: App, opened: list[sqlite3.Connection]
    ) -> None:
        root = Path(app.app_path)
        make_database(root / "data" / "app.db", rows=50, page_size=4096).close()
        opened.append(make_database(root / "storage" / "live.sqlite", wal=True))

        listing = sqlite_files.find(app)

        assert [item.path for item in listing.files] == ["data/app.db", "storage/live.sqlite"]
        plain, live = listing.files
        assert (plain.journal_mode, plain.page_size) == ("rollback", 4096)
        assert plain.pages == plain.size // 4096 > 0
        assert live.journal_mode == "wal" and live.wal_bytes > 0
        assert listing.searched == ["."] and not listing.truncated

    def test_what_is_not_a_database_is_left_out(self, app: App) -> None:
        root = Path(app.app_path)
        make_database(root / "real.db").close()
        (root / "notes.txt").write_text("SQLite format 3 is the header, not this file")
        (root / "empty.db").write_bytes(b"")
        (root / "short.db").write_bytes(b"SQLite format 3\x00")
        (root / "image.png").write_bytes(b"\x89PNG" + bytes(200))

        assert [item.path for item in sqlite_files.find(app).files] == ["real.db"]

    def test_the_files_sqlite_keeps_beside_a_database_are_not_listed_as_databases(
        self, app: App, opened: list[sqlite3.Connection]
    ) -> None:
        root = Path(app.app_path)
        opened.append(make_database(root / "live.db", wal=True))
        assert (root / "live.db-wal").exists()

        assert [item.path for item in sqlite_files.find(app).files] == ["live.db"]

    def test_dependencies_and_history_are_not_searched(self, app: App) -> None:
        root = Path(app.app_path)
        make_database(root / "node_modules" / "pkg" / "cache.db").close()
        make_database(root / ".git" / "cache.db").close()
        make_database(root / ".venv" / "lib" / "x.db").close()
        make_database(root / "data" / "app.db").close()

        assert [item.path for item in sqlite_files.find(app).files] == ["data/app.db"]

    def test_a_link_is_not_followed_out_of_the_directory(self, app: App, tmp_path: Path) -> None:
        root = Path(app.app_path)
        outside = tmp_path / "elsewhere"
        make_database(outside / "secret.db").close()
        (root / "linked").symlink_to(outside)
        (root / "file-link.db").symlink_to(outside / "secret.db")
        make_database(root / "own.db").close()

        assert [item.path for item in sqlite_files.find(app).files] == ["own.db"]

    def test_a_pipe_is_not_opened(self, app: App) -> None:
        root = Path(app.app_path)
        os.mkfifo(root / "pipe.db")
        make_database(root / "own.db").close()

        assert [item.path for item in sqlite_files.find(app).files] == ["own.db"]

    def test_a_release_application_is_searched_where_its_data_lives_not_in_its_releases(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "apps" / "vault-example-com"
        make_database(root / "shared" / "data" / "db.sqlite3").close()
        make_database(root / "releases" / "20261001" / "fixtures.db").close()
        make_database(root / "repo" / "objects.db").close()
        release_app = App(domain="vault.example.com", app_path=str(root), layout="releases")

        listing = sqlite_files.find(release_app)

        assert [item.path for item in listing.files] == ["shared/data/db.sqlite3"]
        assert listing.searched == ["shared"]

    def test_an_application_with_no_directory_has_none(self, tmp_path: Path) -> None:
        ghost = App(domain="gone.example.com", app_path=str(tmp_path / "missing"), layout="inplace")

        assert sqlite_files.find(ghost).files == []

    def test_a_search_that_hits_its_limit_says_so(
        self, app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = Path(app.app_path)
        for number in range(5):
            make_database(root / f"db{number}.db").close()
        monkeypatch.setattr(sqlite_files, "MAX_FOUND", 3)

        listing = sqlite_files.find(app)

        assert len(listing.files) == 3 and listing.truncated

    def test_a_search_that_takes_too_long_stops_and_says_so(
        self, app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        make_database(Path(app.app_path) / "app.db").close()
        monkeypatch.setattr(sqlite_files, "SEARCH_SECONDS", -1)

        listing = sqlite_files.find(app)

        assert listing.truncated and listing.files == []

    def test_a_damaged_page_size_is_reported_as_unknown_not_divided_by(self, app: App) -> None:
        path = Path(app.app_path) / "odd.db"
        make_database(path).close()
        with path.open("r+b") as handle:
            handle.seek(16)
            handle.write((777).to_bytes(2, "big"))

        (item,) = sqlite_files.find(app).files

        assert (item.page_size, item.pages) == (0, 0)

    def test_a_page_size_of_65536_is_stored_as_one(self, app: App) -> None:
        path = Path(app.app_path) / "big-pages.db"
        make_database(path, page_size=65536).close()

        (item,) = sqlite_files.find(app).files

        assert item.page_size == 65536


class TestNamingOne:
    @pytest.mark.parametrize(
        "name",
        [
            "",
            "/etc/passwd",
            "../outside.db",
            "data/../../x.db",
            "data//app.db",
            "./app.db",
            "a\\b.db",
            "a\x00b.db",
        ],
    )
    def test_a_path_that_is_not_inside_the_directory_is_refused(self, app: App, name: str) -> None:
        with pytest.raises(ValidationError):
            sqlite_files.locate(app, name)

    def test_a_link_in_the_path_is_refused_even_when_it_ends_inside(self, app: App) -> None:
        root = Path(app.app_path)
        make_database(root / "real" / "app.db").close()
        (root / "alias").symlink_to(root / "real")
        (root / "real" / "latest.db").symlink_to(root / "real" / "app.db")

        with pytest.raises(ValidationError, match="link"):
            sqlite_files.locate(app, "alias/app.db")
        with pytest.raises(ValidationError, match="link"):
            sqlite_files.locate(app, "real/latest.db")

    def test_what_is_missing_or_not_a_database_is_refused(self, app: App) -> None:
        root = Path(app.app_path)
        (root / "config.json").write_text("{}")

        with pytest.raises(ValidationError, match="not in"):
            sqlite_files.locate(app, "ghost.db")
        with pytest.raises(ValidationError, match="not a SQLite database"):
            sqlite_files.locate(app, "config.json")
        with pytest.raises(ValidationError, match="not a SQLite database"):
            sqlite_files.locate(app, ".")

    def test_a_name_holding_uri_characters_opens_that_file_and_no_other(self, app: App) -> None:
        root = Path(app.app_path)
        make_database(root / "a#b?c%20d.db", rows=3).close()
        make_database(root / "a.db", rows=99).close()

        connection = open_readonly(sqlite_files.locate(app, "a#b?c%20d.db"))
        try:
            assert connection.execute("SELECT count(*) FROM notes").fetchone() == (3,)
        finally:
            connection.close()


class TestChecking:
    def test_a_sound_database_is_sound_with_either_check(self, app: App) -> None:
        make_database(Path(app.app_path) / "app.db", rows=200).close()

        quick = sqlite_files.check(app, "app.db")
        full = sqlite_files.check(app, "app.db", full=True)

        assert (quick.ok, quick.complete, quick.full, quick.problems) == (True, True, False, [])
        assert (full.ok, full.complete, full.full) == (True, True, True)

    def test_a_database_in_use_is_checked_without_being_closed_or_changed(
        self, app: App, opened: list[sqlite3.Connection]
    ) -> None:
        path = Path(app.app_path) / "live.db"
        writer = make_database(path, rows=20, wal=True)
        opened.append(writer)
        before = (path.stat().st_mtime_ns, (path.parent / "live.db-wal").stat().st_size)

        result = sqlite_files.check(app, "live.db", full=True)

        assert result.ok
        assert (path.stat().st_mtime_ns, (path.parent / "live.db-wal").stat().st_size) == before
        writer.execute("INSERT INTO notes (body) VALUES ('still writable')")

    def test_damage_is_reported_not_raised(self, app: App) -> None:
        path = Path(app.app_path) / "broken.db"
        make_database(path, rows=3000, page_size=1024).close()
        with path.open("r+b") as handle:
            handle.seek(1024 * 6)
            handle.write(b"\xff" * 1024 * 3)

        result = sqlite_files.check(app, "broken.db", full=True)

        assert not result.ok
        assert result.problems and len(result.problems) <= sqlite_files.MAX_PROBLEMS

    def test_a_file_that_only_starts_like_a_database_is_reported_not_raised(self, app: App) -> None:
        path = Path(app.app_path) / "fake.db"
        path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 400)

        result = sqlite_files.check(app, "fake.db")

        assert not result.ok and result.problems

    def test_a_check_that_runs_out_of_time_says_it_did_not_finish(self, app: App) -> None:
        make_database(Path(app.app_path) / "big.db", rows=20000).close()

        result = sqlite_files.check(app, "big.db", full=True, budget=-1)

        assert result.complete is False

    def test_a_database_a_writer_holds_is_a_clear_error_not_a_hang(
        self, app: App, monkeypatch: pytest.MonkeyPatch, opened: list[sqlite3.Connection]
    ) -> None:
        path = Path(app.app_path) / "busy.db"
        writer = make_database(path)
        opened.append(writer)
        writer.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(sqlite_files, "LOCK_WAIT", 0.05)

        with pytest.raises(DatabaseError, match="could not be read"):
            sqlite_files.check(app, "busy.db")

    def test_it_never_writes_into_the_directory(self, app: App) -> None:
        root = Path(app.app_path)
        make_database(root / "app.db").close()
        before = sorted(p.name for p in root.iterdir())

        sqlite_files.check(app, "app.db", full=True)
        sqlite_files.find(app)

        assert sorted(p.name for p in root.iterdir()) == before


class TestCheckingATree:
    def test_every_database_under_a_directory_is_checked_and_a_bad_one_does_not_hide_the_rest(
        self, tmp_path: Path
    ) -> None:
        make_database(tmp_path / "a" / "good.db").close()
        make_database(tmp_path / "b" / "also-good.db").close()
        (tmp_path / "c").mkdir()
        (tmp_path / "c" / "fake.db").write_bytes(b"SQLite format 3\x00" + b"\xff" * 400)

        results = sqlite_files.check_tree(tmp_path)

        assert [(item.path, item.ok) for item in results] == [
            ("a/good.db", True),
            ("b/also-good.db", True),
            ("c/fake.db", False),
        ]
        assert results[2].problems

    def test_a_directory_with_none_gives_none(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("nothing here")

        assert sqlite_files.check_tree(tmp_path) == []

    def test_only_so_many_are_checked(self, tmp_path: Path) -> None:
        for number in range(5):
            make_database(tmp_path / f"db{number}.db").close()

        assert len(sqlite_files.check_tree(tmp_path, limit=3)) == 3


class TestCopying:
    def test_a_database_in_use_is_copied_with_what_is_still_in_its_log(
        self, app: App, tmp_path: Path, opened: list[sqlite3.Connection]
    ) -> None:
        source = Path(app.app_path) / "live.db"
        opened.append(make_database(source, rows=7, wal=True))
        target = tmp_path / "copy.sqlite"

        written = sqlite_files.copy_to(app, "live.db", target)

        assert written == target
        copy = sqlite3.connect(target)
        try:
            assert copy.execute("SELECT count(*) FROM notes").fetchone() == (7,)
            assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        finally:
            copy.close()

    def test_the_copy_is_private(self, app: App, tmp_path: Path) -> None:
        make_database(Path(app.app_path) / "app.db").close()
        target = tmp_path / "copy.sqlite"

        sqlite_files.copy_to(app, "app.db", target)

        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_a_copy_never_replaces_a_file(self, app: App, tmp_path: Path) -> None:
        make_database(Path(app.app_path) / "app.db").close()
        target = tmp_path / "taken.sqlite"
        target.write_text("someone's file")

        with pytest.raises(ValidationError, match="already exists"):
            sqlite_files.copy_to(app, "app.db", target)

        assert target.read_text() == "someone's file"

    def test_a_missing_directory_is_said_before_anything_is_created(
        self, app: App, tmp_path: Path
    ) -> None:
        make_database(Path(app.app_path) / "app.db").close()

        with pytest.raises(ValidationError, match="does not exist"):
            sqlite_files.copy_to(app, "app.db", tmp_path / "nowhere" / "copy.sqlite")

    def test_a_file_sqlite_cannot_read_leaves_no_partial_copy(
        self, app: App, tmp_path: Path
    ) -> None:
        (Path(app.app_path) / "fake.db").write_bytes(b"SQLite format 3\x00" + b"\xff" * 400)
        target = tmp_path / "copy.sqlite"

        with pytest.raises(DatabaseError, match="could not be copied"):
            sqlite_files.copy_to(app, "fake.db", target)

        assert not target.exists()


# ------------------------------------------------------------------ the API


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def give_acme_a_database(world: World, tmp_path: Path, rows: int = 5) -> Path:
    """
    Put a database in the data directory of the acme application.

    Returns:
        Its path.
    """
    path = tmp_path / ACME / "data" / "app.db"
    make_database(path, rows=rows).close()
    return path


class TestTheApi:
    def test_the_listing_is_the_applications_own(self, world: World, tmp_path: Path) -> None:
        give_acme_a_database(world, tmp_path)

        response = world.client.get(f"/api/apps/{ACME}/sqlite", headers=world.as_("alice"))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["domain"] == ACME and not body["truncated"]
        assert [item["path"] for item in body["files"]] == ["data/app.db"]
        assert body["files"][0]["journal_mode"] == "rollback"

    def test_another_teams_application_does_not_exist_for_you(
        self, world: World, tmp_path: Path
    ) -> None:
        give_acme_a_database(world, tmp_path)

        listing = world.client.get(f"/api/apps/{ACME}/sqlite", headers=world.as_("bob"))
        check = world.client.post(
            f"/api/apps/{ACME}/sqlite/check", json={"path": "data/app.db"}, headers=world.as_("bob")
        )
        unknown = world.client.get("/api/apps/ghost.example.com/sqlite", headers=world.as_("bob"))

        assert listing.status_code == check.status_code == 404
        # The body of one that exists is the body of one that does not, but for the name asked.
        assert listing.json()["detail"].replace(ACME, "X") == unknown.json()["detail"].replace(
            "ghost.example.com", "X"
        )

    def test_a_check_asks_sqlite_and_changes_nothing(self, world: World, tmp_path: Path) -> None:
        path = give_acme_a_database(world, tmp_path)
        before = path.read_bytes()

        response = world.client.post(
            f"/api/apps/{ACME}/sqlite/check",
            json={"path": "data/app.db", "full": True},
            headers=world.as_("alice"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["ok"], body["complete"], body["full"], body["problems"]) == (
            True,
            True,
            True,
            [],
        )
        assert path.read_bytes() == before

    def test_a_viewer_may_look_but_not_check(self, world: World, tmp_path: Path) -> None:
        give_acme_a_database(world, tmp_path)

        seen = world.client.get(f"/api/apps/{ACME}/sqlite", headers=world.as_("dana"))
        refused = world.client.post(
            f"/api/apps/{ACME}/sqlite/check",
            json={"path": "data/app.db"},
            headers=world.as_("dana"),
        )
        allowed = world.client.post(
            f"/api/apps/{ACME}/sqlite/check",
            json={"path": "data/app.db"},
            headers=world.as_("omar"),
        )

        assert seen.status_code == 200
        assert refused.status_code == 403
        assert allowed.status_code == 200

    @pytest.mark.parametrize("path", ["../x.db", "/etc/passwd", "data/missing.db", "data"])
    def test_a_path_that_is_not_one_of_its_databases_is_a_400_that_says_what(
        self, world: World, tmp_path: Path, path: str
    ) -> None:
        give_acme_a_database(world, tmp_path)

        response = world.client.post(
            f"/api/apps/{ACME}/sqlite/check", json={"path": path}, headers=world.as_("alice")
        )

        assert response.status_code == 400, response.text

    def test_a_database_too_large_for_a_request_is_sent_to_the_terminal(
        self, world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        give_acme_a_database(world, tmp_path)
        monkeypatch.setattr(sqlite_files, "MAX_REQUEST_BYTES", 10)

        response = world.client.post(
            f"/api/apps/{ACME}/sqlite/check",
            json={"path": "data/app.db"},
            headers=world.as_("alice"),
        )

        assert response.status_code == 400
        assert f"noust db sqlite check {ACME} data/app.db" in response.text

    def test_the_copy_is_not_offered_over_http(self, world: World, tmp_path: Path) -> None:
        give_acme_a_database(world, tmp_path)

        for method in ("get", "post"):
            response = getattr(world.client, method)(
                f"/api/apps/{ACME}/sqlite/copy", headers=world.as_("admin")
            )
            assert response.status_code in (404, 405)


# ------------------------------------------------------------------ the command line


class TestTheCommandLine:
    @pytest.fixture(autouse=True)
    def state_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        directory = tmp_path / "state"
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(STATE_DIR_ENV, str(directory))

    @pytest.fixture
    def store(self, tmp_path: Path, runner: Any) -> Iterator[NoustStore]:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        root = tmp_path / "apps" / "shop-example-com"
        make_database(root / "data" / "app.db", rows=12).close()
        make_database(tmp_path / "apps" / "blog-example-com" / "blog.sqlite", rows=2).close()
        instance.create_app(App(domain="shop.example.com", app_type="nodejs", app_path=str(root)))
        instance.create_app(
            App(
                domain="blog.example.com",
                app_type="nodejs",
                app_path=str(tmp_path / "apps" / "blog-example-com"),
            )
        )
        instance.create_app(
            App(domain="empty.example.com", app_type="nodejs", app_path=str(tmp_path / "none"))
        )
        yield instance
        instance.close()
        NoustStore.reset_instance()

    def run(self, *args: str, code: int = 0) -> str:
        result = CliRunner().invoke(root_cli, list(args))
        assert result.exit_code == code, f"{result.output} {result.exception}"
        return f"{result.output}{result.exception or ''}"

    def test_list_names_every_applications_databases(self, store: NoustStore) -> None:
        listed = json.loads(self.run("--json", "db", "sqlite", "list"))

        by_domain = {item["domain"]: [f["path"] for f in item["files"]] for item in listed}
        assert by_domain == {
            "shop.example.com": ["data/app.db"],
            "blog.example.com": ["blog.sqlite"],
            "empty.example.com": [],
        }

    def test_list_of_one_application_as_a_table(self, store: NoustStore) -> None:
        out = self.run("db", "sqlite", "list", "shop.example.com")

        assert "data/app.db" in out and "rollback" in out
        assert "blog.sqlite" not in out

    def test_list_of_an_application_without_any_says_so(self, store: NoustStore) -> None:
        out = self.run("db", "sqlite", "list", "empty.example.com")

        assert "No SQLite database found in empty.example.com" in out

    def test_an_unknown_application_is_named(self, store: NoustStore) -> None:
        out = self.run("db", "sqlite", "list", "ghost.example.com", code=1)

        assert "No application is deployed at ghost.example.com" in out

    def test_check_succeeds_on_a_sound_database_and_fails_on_a_damaged_one(
        self, store: NoustStore, tmp_path: Path
    ) -> None:
        good = self.run("db", "sqlite", "check", "shop.example.com", "data/app.db")
        broken = tmp_path / "apps" / "blog-example-com" / "blog.sqlite"
        make_database(broken.with_name("worse.db"), rows=3000, page_size=1024).close()
        with broken.with_name("worse.db").open("r+b") as handle:
            handle.seek(1024 * 6)
            handle.write(b"\xff" * 1024 * 3)

        bad = self.run("db", "sqlite", "check", "blog.example.com", "worse.db", "--full", code=1)

        assert "nothing wrong found" in good
        assert "SQLite found problems" in bad

    def test_check_as_json(self, store: NoustStore) -> None:
        result = json.loads(
            self.run("--json", "db", "sqlite", "check", "shop.example.com", "data/app.db")
        )

        assert result["ok"] is True and result["path"] == "data/app.db"

    def test_copy_writes_a_consistent_private_file(self, store: NoustStore, tmp_path: Path) -> None:
        target = tmp_path / "out" / "shop.sqlite"
        target.parent.mkdir()

        out = self.run("db", "sqlite", "copy", "shop.example.com", "data/app.db", "-o", str(target))

        assert "copied to" in out
        connection = sqlite3.connect(target)
        try:
            assert connection.execute("SELECT count(*) FROM notes").fetchone() == (12,)
        finally:
            connection.close()
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_copy_in_a_rehearsal_writes_nothing(self, store: NoustStore, tmp_path: Path) -> None:
        target = tmp_path / "shop.sqlite"

        out = self.run(
            "--dry-run",
            "db",
            "sqlite",
            "copy",
            "shop.example.com",
            "data/app.db",
            "-o",
            str(target),
        )

        assert "Would copy" in out and not target.exists()

    def test_copy_over_an_existing_file_is_refused(self, store: NoustStore, tmp_path: Path) -> None:
        target = tmp_path / "taken"
        target.write_text("x")

        out = self.run(
            "db", "sqlite", "copy", "shop.example.com", "data/app.db", "-o", str(target), code=1
        )

        assert "already exists" in out
