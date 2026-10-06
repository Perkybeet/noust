# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Telling what a dump is, and making it loadable (3.4, checklist H-18).

A file from another provider arrives with whatever name its owner gave it, in
whatever compression was to hand. What is pinned here:

- the format is read from the file's bytes, never its name, through gzip,
  bzip2, xz and zip wrappers, and a file that is none of the known dumps is
  ``unknown`` without a word of its contents in the message;
- bzip2, xz and zip are decompressed into a private scratch file the engine
  reads, a tar holding one dump gives up that dump, and a decompression is
  held to a size limit;
- the PostgreSQL option "keep owners" off takes ``ALTER ... OWNER TO``,
  ``GRANT`` and ``REVOKE`` out of a plain dump where they are statements, and
  nowhere else (a function body, COPY data), and has ``pg_restore`` told
  ``--no-owner --no-acl --role=<owner>``;
- a path from the API must be inside a folder dumps may be imported from.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest

from noust.core.config import Config
from noust.core.exceptions import DatabaseBackupError, ValidationError
from noust.managers.database import imports
from noust.managers.database.imports import (
    allowed_directories,
    detect_format,
    prepare,
    resolve_server_path,
)
from noust.managers.database.psql_script import strip_owners
from noust.managers.database.uploads import check_file_name

SQL = b"-- PostgreSQL database dump\nSET statement_timeout = 0;\nCREATE TABLE t (i int);\n"
PG_CUSTOM = b"PGDMP\x01\x0e\x00" + b"\x00" * 64
MONGO_ARCHIVE = struct.pack("<I", 0x8199E26D) + b"\x05\x00\x00\x00\x00" + b"\xff\xff\xff\xff"


def tar_bytes(members: dict[str, bytes], mode: str = "w") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:  # type: ignore[call-overload]
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def write(directory: Path, name: str, data: bytes) -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


# ============================================================== what it is


class TestDetectFormat:
    @pytest.mark.parametrize(
        ("wrap", "compression"),
        [
            (lambda data: data, "none"),
            (gzip.compress, "gzip"),
            (bz2.compress, "bz2"),
            (lzma.compress, "xz"),
        ],
    )
    def test_plain_sql_through_every_wrapper(self, tmp_path: Path, wrap, compression: str) -> None:
        found = detect_format(write(tmp_path, "d.sql", wrap(SQL)))

        assert (found.kind, found.compression) == ("sql", compression)
        assert set(found.engines) == {"postgresql", "mysql"}

    def test_a_zip_of_one_dump(self, tmp_path: Path) -> None:
        path = tmp_path / "d.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("whatever-name.txt", SQL)

        found = detect_format(path)

        assert (found.kind, found.compression) == ("sql", "zip")

    def test_the_name_does_not_decide(self, tmp_path: Path) -> None:
        # A custom archive named .sql, and SQL named .dump.
        assert detect_format(write(tmp_path, "a.sql", PG_CUSTOM)).kind == "pg-custom"
        assert detect_format(write(tmp_path, "b.dump", SQL)).kind == "sql"

    def test_postgresql_custom_and_its_gzip(self, tmp_path: Path) -> None:
        assert detect_format(write(tmp_path, "a.dump", PG_CUSTOM)).engines == ("postgresql",)
        found = detect_format(write(tmp_path, "a.dump.gz", gzip.compress(PG_CUSTOM)))
        assert (found.kind, found.compression) == ("pg-custom", "gzip")

    def test_a_mongodump_archive_gzipped_or_not(self, tmp_path: Path) -> None:
        assert detect_format(write(tmp_path, "m.archive", MONGO_ARCHIVE)).kind == "mongo-archive"
        found = detect_format(write(tmp_path, "m.archive.gz", gzip.compress(MONGO_ARCHIVE)))
        assert (found.kind, found.compression, found.engines) == (
            "mongo-archive",
            "gzip",
            ("mongodb",),
        )

    def test_tars_are_looked_into(self, tmp_path: Path) -> None:
        pg = tar_bytes({"toc.dat": b"x", "3.dat": b"y"})
        sql = tar_bytes({"dump.sql": SQL})
        tree = tar_bytes({"shop/": b"", "shop/users.bson": b"b", "shop/users.metadata.json": b"{}"})

        assert detect_format(write(tmp_path, "pg.tar", pg)).kind == "pg-tar"
        assert detect_format(write(tmp_path, "one.tar", sql)).kind == "tar-sql"
        assert detect_format(write(tmp_path, "tree.tar", tree)).kind == "mongo-tree"
        assert detect_format(write(tmp_path, "tree.tar.gz", gzip.compress(tree))).compression == (
            "gzip"
        )

    def test_a_tar_of_something_else_is_unknown(self, tmp_path: Path) -> None:
        other = tar_bytes({"photos/a.jpg": b"j", "photos/b.jpg": b"j"})

        assert detect_format(write(tmp_path, "x.tar", other)).kind == "unknown"

    @pytest.mark.parametrize(
        "data",
        [
            b"root:$6$salt$hash:19000:0:99999:7:::\n",
            b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 100,
            b"",
            b"just some words about databases\n",
            b"<html><body>not a dump</body></html>",
        ],
    )
    def test_anything_else_is_unknown_and_says_nothing_of_its_contents(
        self, tmp_path: Path, data: bytes
    ) -> None:
        found = detect_format(write(tmp_path, "x.sql", data))

        assert found.kind == "unknown"
        assert found.engines == ()
        assert "root:" not in found.description

    def test_a_zip_of_several_files_or_a_damaged_one_is_refused(self, tmp_path: Path) -> None:
        many = tmp_path / "many.zip"
        with zipfile.ZipFile(many, "w") as archive:
            archive.writestr("a.sql", SQL)
            archive.writestr("b.sql", SQL)
        with pytest.raises(DatabaseBackupError, match="2 files"):
            detect_format(many)

        broken = write(tmp_path, "broken.zip", b"PK\x03\x04" + b"junk" * 10)
        with pytest.raises(DatabaseBackupError, match="Could not read"):
            detect_format(broken)

    def test_a_missing_file_is_an_error_with_a_fix(self, tmp_path: Path) -> None:
        with pytest.raises(DatabaseBackupError, match="Could not read"):
            detect_format(tmp_path / "gone.sql")


# ============================================================== loadable


class TestPrepare:
    def test_gzip_is_left_to_the_engine_under_a_name_that_says_what_it_is(
        self, tmp_path: Path
    ) -> None:
        source = write(tmp_path, "export", gzip.compress(SQL))
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        prepared = prepare(source, detect_format(source), "postgresql", scratch)

        assert prepared.path.name == "import.sql.gz"
        assert prepared.path.is_symlink()
        assert prepared.path.resolve() == source.resolve()
        assert prepared.converted is False

    def test_a_custom_archive_is_named_dump_and_a_pg_tar_tar(self, tmp_path: Path) -> None:
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        custom = write(tmp_path, "x.bin", PG_CUSTOM)

        assert prepare(custom, detect_format(custom), "postgresql", scratch).path.name == (
            "import.dump"
        )

    @pytest.mark.parametrize(
        ("name", "wrap"),
        [("d.sql.bz2", bz2.compress), ("d.sql.xz", lzma.compress)],
    )
    def test_bzip2_and_xz_become_the_plain_file(self, tmp_path: Path, name: str, wrap) -> None:
        source = write(tmp_path, name, wrap(SQL))
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        prepared = prepare(source, detect_format(source), "mysql", scratch)

        assert prepared.path == scratch / "import.sql"
        assert prepared.path.read_bytes() == SQL
        assert prepared.converted is True
        assert oct(prepared.path.stat().st_mode & 0o777) == "0o600"

    def test_a_zip_becomes_the_plain_file(self, tmp_path: Path) -> None:
        source = tmp_path / "d.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("../../etc/evil.sql", SQL)
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        prepared = prepare(source, detect_format(source), "postgresql", scratch)

        # The member's own name is never used as a path.
        assert prepared.path == scratch / "import.sql"
        assert prepared.path.read_bytes() == SQL
        assert not (tmp_path.parent / "etc").exists()

    def test_a_tar_of_one_dump_gives_up_the_dump(self, tmp_path: Path) -> None:
        source = write(tmp_path, "one.tar", tar_bytes({"dump.sql": SQL}))
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        prepared = prepare(source, detect_format(source), "mysql", scratch)

        assert prepared.path.read_bytes() == SQL

    def test_an_engine_that_cannot_load_it_says_what_it_can(self, tmp_path: Path) -> None:
        source = write(tmp_path, "m.archive", MONGO_ARCHIVE)
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        with pytest.raises(DatabaseBackupError) as failure:
            prepare(source, detect_format(source), "postgresql", scratch)

        assert "mongodump archive" in str(failure.value)
        assert "postgresql imports" in str(failure.value)

    def test_a_mongodump_tree_must_be_a_gzipped_tar(self, tmp_path: Path) -> None:
        tree = tar_bytes({"shop/users.bson": b"b"})
        source = write(tmp_path, "tree.tar", tree)
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        with pytest.raises(DatabaseBackupError, match=r"\.tar\.gz"):
            prepare(source, detect_format(source), "mongodb", scratch)

        gzipped = write(tmp_path, "tree.tar.gz", gzip.compress(tree))
        assert prepare(gzipped, detect_format(gzipped), "mongodb", scratch).path.name == (
            "import.tar.gz"
        )

    def test_a_decompression_is_held_to_a_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = write(tmp_path, "bomb.sql.bz2", bz2.compress(SQL * 1000))
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        monkeypatch.setattr(imports, "MAX_EXPANDED_BYTES", 1024)

        with pytest.raises(DatabaseBackupError, match="expands to more than"):
            prepare(source, detect_format(source), "postgresql", scratch)

    def test_a_damaged_bzip2_is_an_error_that_names_the_file(self, tmp_path: Path) -> None:
        data = bz2.compress(SQL * 100)
        source = write(tmp_path, "cut.sql.bz2", data[: len(data) // 2])
        scratch = tmp_path / "scratch"
        scratch.mkdir()

        with pytest.raises(DatabaseBackupError, match=r"cut\.sql\.bz2"):
            prepare(source, detect_format(source), "postgresql", scratch)


# ============================================================ owners, in SQL


DUMP_WITH_OWNERS = """\
--
-- PostgreSQL database dump
--

SET statement_timeout = 0;
CREATE TABLE public.users (id integer, note text);
ALTER TABLE public.users OWNER TO app_prod;
CREATE FUNCTION public.f() RETURNS text
    LANGUAGE plpgsql AS $$
BEGIN
GRANT ALL ON TABLE public.users TO nobody;
ALTER TABLE public.users OWNER TO inside_a_function;
RETURN 'x';
END;
$$;
ALTER FUNCTION public.f() OWNER TO app_prod;
COPY public.users (id, note) FROM stdin;
1	ALTER TABLE public.users OWNER TO data_not_a_statement;
2	GRANT ALL ON TABLE public.users TO data_not_a_grant;
\\.
GRANT SELECT ON TABLE public.users TO reader;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE app_prod IN SCHEMA public GRANT SELECT ON TABLES TO reader;
INSERT INTO public.users VALUES (3, 'GRANT ALL ON TABLE t TO x;');
"""


class TestStripOwners:
    def test_statements_go_and_everything_else_stays(self, tmp_path: Path) -> None:
        source = tmp_path / "d.sql"
        source.write_text(DUMP_WITH_OWNERS)
        destination = tmp_path / "out.sql"

        left_out = strip_owners(source, destination)

        text = destination.read_text()
        assert left_out == 5
        assert "OWNER TO app_prod" not in text
        assert "GRANT SELECT ON TABLE public.users TO reader" not in text
        assert "REVOKE ALL ON SCHEMA" not in text
        assert "ALTER DEFAULT PRIVILEGES" not in text
        # What is not a statement at the head of one is not touched.
        assert "GRANT ALL ON TABLE public.users TO nobody;" in text
        assert "ALTER TABLE public.users OWNER TO inside_a_function;" in text
        assert "data_not_a_statement" in text
        assert "data_not_a_grant" in text
        assert "INSERT INTO public.users VALUES (3, 'GRANT ALL ON TABLE t TO x;');" in text
        assert "CREATE TABLE public.users" in text
        assert oct(destination.stat().st_mode & 0o777) == "0o600"

    def test_a_gzipped_dump_is_read_through_and_written_plain(self, tmp_path: Path) -> None:
        source = tmp_path / "d.sql.gz"
        source.write_bytes(gzip.compress(DUMP_WITH_OWNERS.encode()))
        destination = tmp_path / "out.sql"

        strip_owners(source, destination)

        assert destination.read_text().startswith("--\n-- PostgreSQL database dump")

    def test_the_role_is_set_first_so_the_owner_ends_up_owning_everything(
        self, tmp_path: Path
    ) -> None:
        source = tmp_path / "d.sql"
        source.write_text(DUMP_WITH_OWNERS)
        destination = tmp_path / "out.sql"

        strip_owners(source, destination, role="app_prod")

        assert destination.read_text().startswith('SET ROLE "app_prod";\n--')

    @pytest.mark.parametrize("role", ['a"b', "a b", "a;b", "", "x" * 64, "a\nb"])
    def test_a_role_that_is_not_a_plain_name_is_refused(self, tmp_path: Path, role: str) -> None:
        source = tmp_path / "d.sql"
        source.write_text(DUMP_WITH_OWNERS)

        with pytest.raises(DatabaseBackupError, match="will not write"):
            strip_owners(source, tmp_path / "out.sql", role=role)


# ========================================================== names and paths


class TestFileNames:
    @pytest.mark.parametrize(
        "name",
        ["a.sql", "a.SQL", "a.sql.gz", "a.tar.bz2", "a.tar.xz", "a.dump", "a.archive", "a.zip"],
    )
    def test_the_allowed_extensions(self, name: str) -> None:
        assert check_file_name(name) == name

    @pytest.mark.parametrize(
        "name",
        ["a.sh", "a.exe", "a", "a.sql.sh", "a.php", ".", "..", "a/b.sql", "a\\b.sql", "a\x00.sql"],
    )
    def test_anything_else_is_refused(self, name: str) -> None:
        with pytest.raises(ValidationError):
            check_file_name(name)

    def test_the_refusal_lists_what_is_allowed(self) -> None:
        with pytest.raises(ValidationError) as failure:
            check_file_name("backup.bak")

        assert ".sql" in failure.value.details and ".archive" in failure.value.details


class TestServerPaths:
    @pytest.fixture
    def folders(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        backups = tmp_path / "backups"
        (backups / "imports").mkdir(parents=True)
        monkeypatch.setattr(
            Config, "backup_directory", property(lambda self: backups), raising=False
        )
        return backups

    def test_the_api_reads_from_the_imports_folder(self, folders: Path) -> None:
        path = folders / "imports" / "d.sql"
        path.write_bytes(SQL)

        assert resolve_server_path(str(path), restrict=True) == path.resolve()

    def test_the_api_refuses_a_file_elsewhere_and_a_link_out_of_the_folder(
        self, folders: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "elsewhere.sql"
        outside.write_bytes(SQL)
        link = folders / "imports" / "link.sql"
        link.symlink_to(outside)

        with pytest.raises(ValidationError, match="not in a folder"):
            resolve_server_path(str(outside), restrict=True)
        with pytest.raises(ValidationError, match="not in a folder"):
            resolve_server_path(str(link), restrict=True)

    def test_a_listed_directory_is_allowed(
        self, folders: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        shared = tmp_path / "shared"
        shared.mkdir()
        (shared / "d.sql").write_bytes(SQL)
        monkeypatch.setattr(
            Config,
            "get",
            lambda self, key, default=None: (
                [str(shared)] if key == "databases.import_directories" else default
            ),
        )

        assert shared in allowed_directories()
        assert resolve_server_path(str(shared / "d.sql"), restrict=True)

    def test_the_cli_may_name_any_file(self, tmp_path: Path) -> None:
        path = tmp_path / "anywhere.sql"
        path.write_bytes(SQL)

        assert resolve_server_path(str(path), restrict=False) == path.resolve()

    def test_relative_missing_and_directories_are_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="absolute"):
            resolve_server_path("d.sql", restrict=False)
        with pytest.raises(ValidationError, match="no file"):
            resolve_server_path(str(tmp_path / "gone.sql"), restrict=False)
        (tmp_path / "dir.sql").mkdir()
        with pytest.raises(ValidationError, match="not a regular file"):
            resolve_server_path(str(tmp_path / "dir.sql"), restrict=False)

    def test_the_extension_is_checked_before_the_file_is_looked_at(self, tmp_path: Path) -> None:
        (tmp_path / "shadow").write_text("root:x")

        with pytest.raises(ValidationError, match="does not look like a database dump"):
            resolve_server_path(str(tmp_path / "shadow"), restrict=False)
