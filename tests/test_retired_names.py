# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What 4.0 does about the names the product had before 3.0.

3.x moved a 2.x server onto Noust's names the first time root ran a command, and left links with the
old names. 4.0 does not move anything. It does two things, both about not hurting a server:

- it refuses to start on a server whose data 3.x never finished moving, because every path now
  means the new directory and an empty store under it would answer "No applications deployed" about
  a full server (the trap that cost a night in 2026);
- it retires the links 3.x left, and only the ones it can prove it made and nothing uses: a link is
  removed only when it points exactly where the migration pointed it, its target exists, and neither
  the configuration, nor an nginx site, nor a row of the store names it. A real file or directory is
  never touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.core import paths, retired_names
from noust.core.exceptions import ConfigError
from noust.core.fs import DryRunFileSystem, RealFileSystem

OLD = "wasm"


@pytest.fixture
def server(tmp_path: Path) -> Path:
    """A root with the directories a migrated 3.x server has, and nothing under the old names."""
    root = tmp_path / "root"
    for directory in (
        "etc/noust",
        "var/lib/noust",
        "var/backups/noust",
        "etc/nginx/noust-upstreams",
        "etc/nginx/sites-available",
        "etc/nginx/sites-enabled",
        "etc/nginx/conf.d",
        "usr/local/bin",
        "opt/noust/bin",
    ):
        (root / directory).mkdir(parents=True)
    (root / "etc/noust/config.yaml").write_text("backup:\n  directory: /var/backups/noust\n")
    return root


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A home directory whose data directory was moved."""
    directory = tmp_path / "home"
    (directory / ".local/share/noust").mkdir(parents=True)
    return directory


def make_store(root: Path, *rows: str) -> Path:
    """Create the store with one text column holding the given values."""
    path = root / "var/lib/noust/noust.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        connection.executemany("INSERT INTO notes (body) VALUES (?)", [(row,) for row in rows])
    return path


def link_old_names(root: Path, home: Path) -> dict[str, Path]:
    """Leave the links 3.x left, exactly as it made them."""
    links = {
        "config": (root / f"etc/{OLD}", root / "etc/noust"),
        "state": (root / f"var/lib/{OLD}", root / "var/lib/noust"),
        "backups": (root / f"var/backups/{OLD}", root / "var/backups/noust"),
        "upstreams": (root / f"etc/nginx/{OLD}-upstreams", root / "etc/nginx/noust-upstreams"),
        "user": (home / f".local/share/{OLD}", home / ".local/share/noust"),
    }
    for link, target in links.values():
        link.symlink_to(target)
    return {name: link for name, (link, _target) in links.items()}


class TestServerNotMoved:
    """4.0 refuses a server whose data is still under the old names."""

    def test_a_moved_server_is_fine(self, server: Path, home: Path) -> None:
        link_old_names(server, home)

        retired_names.require_moved(root=server, home=home)

    def test_a_server_that_never_had_the_old_names_is_fine(self, server: Path, home: Path) -> None:
        retired_names.require_moved(root=server, home=home)

    @pytest.mark.parametrize(
        ("directory", "marker"),
        [
            (f"etc/{OLD}", "config.yaml"),
            (f"var/lib/{OLD}", f"{OLD}.db"),
        ],
    )
    def test_data_under_the_old_names_is_refused(
        self, server: Path, home: Path, directory: str, marker: str
    ) -> None:
        (server / directory).mkdir(parents=True)
        (server / directory / marker).write_text("data")

        with pytest.raises(ConfigError) as raised:
            retired_names.require_moved(root=server, home=home)

        assert "3.3" in (raised.value.details or "")
        assert str(server / directory) in raised.value.message

    def test_the_users_store_under_the_old_name_is_refused(self, server: Path, home: Path) -> None:
        old = home / f".local/share/{OLD}"
        old.mkdir(parents=True)
        (old / f"{OLD}.db").write_text("data")

        with pytest.raises(ConfigError):
            retired_names.require_moved(root=server, home=home)

    def test_a_directory_that_holds_something_else_is_not_ours(
        self, server: Path, home: Path
    ) -> None:
        """The name is also WebAssembly's: a runtime's directory must not stop Noust."""
        (server / f"var/lib/{OLD}/modules").mkdir(parents=True)
        (server / f"var/lib/{OLD}/modules/hello.bin").write_text("x")
        (server / f"etc/{OLD}").mkdir()

        retired_names.require_moved(root=server, home=home)

    def test_a_named_data_directory_is_the_operators_choice(
        self, server: Path, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (server / f"etc/{OLD}").mkdir()
        (server / f"etc/{OLD}/config.yaml").write_text("data")
        monkeypatch.setattr(paths, "DATA_DIR", server / "data")

        retired_names.require_moved(root=server, home=home)


class TestRetireLinks:
    """Links 3.x left are removed when they are provably its own and unused."""

    def test_every_link_it_made_is_removed(self, server: Path, home: Path) -> None:
        links = link_old_names(server, home)

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert {outcome.link for outcome in outcomes if outcome.retired} == set(links.values())
        assert not any(link.is_symlink() for link in links.values())
        # What the links pointed at is untouched.
        assert (server / "etc/noust/config.yaml").is_file()

    def test_a_second_run_has_nothing_to_say(self, server: Path, home: Path) -> None:
        link_old_names(server, home)
        retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert retired_names.retire_links(root=server, home=home, fs=RealFileSystem()) == []

    def test_a_link_pointing_elsewhere_is_not_ours(self, server: Path, home: Path) -> None:
        other = server / "srv/other"
        other.mkdir(parents=True)
        link = server / f"etc/{OLD}"
        link.symlink_to(other)

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert link.is_symlink() and link.resolve() == other
        (outcome,) = [o for o in outcomes if o.link == link]
        assert not outcome.retired
        assert "points" in outcome.reason

    def test_a_relative_link_is_not_proved_to_be_ours(self, server: Path, home: Path) -> None:
        link = server / f"etc/{OLD}"
        link.symlink_to("noust")

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert link.is_symlink()
        assert [o.retired for o in outcomes if o.link == link] == [False]

    def test_a_real_directory_is_never_removed(self, server: Path, home: Path) -> None:
        real = server / f"var/backups/{OLD}"
        real.mkdir()
        (real / "old.tar.gz").write_text("a backup")

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert (real / "old.tar.gz").read_text() == "a backup"
        (outcome,) = [o for o in outcomes if o.link == real]
        assert not outcome.retired
        assert "not a link" in outcome.reason

    def test_a_real_file_is_never_removed(self, server: Path, home: Path) -> None:
        real = server / f"etc/{OLD}"
        real.write_text("mine")

        retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert real.read_text() == "mine"

    def test_a_link_whose_target_is_gone_is_kept(self, server: Path, home: Path) -> None:
        link = server / f"var/lib/{OLD}"
        link.symlink_to(server / "var/lib/noust")
        (server / "var/lib/noust").rmdir()

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert link.is_symlink()
        assert [o.retired for o in outcomes if o.link == link] == [False]

    def test_a_rehearsal_removes_nothing(self, server: Path, home: Path) -> None:
        links = link_old_names(server, home)

        outcomes = retired_names.retire_links(root=server, home=home, fs=DryRunFileSystem())

        assert all(link.is_symlink() for link in links.values())
        assert {o.link for o in outcomes if o.retired} == set(links.values())

    def test_a_named_data_directory_has_no_links_to_retire(
        self, server: Path, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        links = link_old_names(server, home)
        monkeypatch.setattr(paths, "DATA_DIR", server / "data")

        assert retired_names.retire_links(root=server, home=home, fs=RealFileSystem()) == []
        assert all(link.is_symlink() for link in links.values())


class TestWhatStillUsesALink:
    """A link something names stays, and the reason says what."""

    def test_the_configuration_naming_it_keeps_it(self, server: Path, home: Path) -> None:
        links = link_old_names(server, home)
        (server / "etc/noust/config.yaml").write_text(
            f"backup:\n  directory: /var/backups/{OLD}/weekly\n"
        )

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert links["backups"].is_symlink()
        kept = [o for o in outcomes if o.link == links["backups"]]
        assert "config.yaml" in kept[0].reason
        # The others did not depend on it.
        assert not links["config"].is_symlink()

    def test_a_site_including_the_old_upstreams_keeps_it(self, server: Path, home: Path) -> None:
        links = link_old_names(server, home)
        site = server / "etc/nginx/sites-available/edge"
        site.write_text(
            f"upstream edge {{ include /etc/nginx/{OLD}-upstreams/app/web.servers; }}\n"
        )

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert links["upstreams"].is_symlink()
        kept = [o for o in outcomes if o.link == links["upstreams"]]
        assert str(site) in kept[0].reason

    def test_a_site_naming_it_does_not_hold_the_other_links(self, server: Path, home: Path) -> None:
        links = link_old_names(server, home)
        (server / "etc/nginx/conf.d/edge.conf").write_text(
            f"include /etc/nginx/{OLD}-upstreams/app/web.servers;\n"
        )

        retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert not links["config"].is_symlink()
        assert not links["state"].is_symlink()

    def test_a_row_of_the_store_naming_it_keeps_it(self, server: Path, home: Path) -> None:
        links = link_old_names(server, home)
        make_store(server, f"/var/backups/{OLD}/databases/shop.sql.gz")

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert links["backups"].is_symlink()
        kept = [o for o in outcomes if o.link == links["backups"]]
        assert "store" in kept[0].reason and "notes" in kept[0].reason

    def test_a_store_that_names_nothing_old_holds_nothing(self, server: Path, home: Path) -> None:
        links = link_old_names(server, home)
        make_store(server, "/var/backups/noust/databases/shop.sql.gz", "unrelated")

        retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert not any(link.is_symlink() for link in links.values())

    def test_a_store_that_cannot_be_read_keeps_the_links_it_could_have_held(
        self, server: Path, home: Path
    ) -> None:
        links = link_old_names(server, home)
        (server / "var/lib/noust/noust.db").write_text("this is not a database")

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert links["backups"].is_symlink()
        assert links["state"].is_symlink()
        kept = [o for o in outcomes if o.link == links["state"]]
        assert "could not be read" in kept[0].reason
        # An nginx include is not something the store records.
        assert not links["upstreams"].is_symlink()


class TestTheCommandAlias:
    """The ``wasm`` entry the installer linked into /usr/local/bin."""

    def test_a_dangling_alias_into_the_venv_is_retired(self, server: Path, home: Path) -> None:
        alias = server / f"usr/local/bin/{OLD}"
        alias.symlink_to(server / f"opt/noust/bin/{OLD}")

        outcomes = retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert not alias.is_symlink()
        assert [o.retired for o in outcomes if o.link == alias] == [True]

    def test_an_alias_beside_the_noust_entry_point_is_retired(
        self, server: Path, home: Path
    ) -> None:
        (server / "opt/noust/bin/noust").write_text("#!/bin/sh\n")
        (server / f"opt/noust/bin/{OLD}").write_text("#!/bin/sh\n")
        alias = server / f"usr/local/bin/{OLD}"
        alias.symlink_to(server / f"opt/noust/bin/{OLD}")

        retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert not alias.is_symlink()
        # What it pointed at is the venv's to clean up.
        assert (server / f"opt/noust/bin/{OLD}").exists()

    def test_a_program_of_the_same_name_is_not_ours(self, server: Path, home: Path) -> None:
        """WebAssembly runtimes install a ``wasm`` too."""
        elsewhere = server / "opt/runtime/bin"
        elsewhere.mkdir(parents=True)
        (elsewhere / OLD).write_text("#!/bin/sh\n")
        alias = server / f"usr/local/bin/{OLD}"
        alias.symlink_to(elsewhere / OLD)

        retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert alias.is_symlink()

    def test_a_real_program_is_never_removed(self, server: Path, home: Path) -> None:
        program = server / f"usr/local/bin/{OLD}"
        program.write_text("#!/bin/sh\n")

        retired_names.retire_links(root=server, home=home, fs=RealFileSystem())

        assert program.read_text() == "#!/bin/sh\n"


class TestTheCommand:
    """``noust setup retire-links`` and the guard in the entry point."""

    def test_the_command_says_what_it_retired(
        self, server: Path, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.app import cli

        link_old_names(server, home)
        monkeypatch.setattr(retired_names, "SYSTEM_ROOT", server)
        monkeypatch.setattr(retired_names, "_home", lambda: home)

        result = CliRunner().invoke(cli, ["setup", "retire-links"])

        assert result.exit_code == 0, result.output
        assert "retired" in result.output
        assert not (server / f"etc/{OLD}").is_symlink()

    def test_a_server_with_nothing_to_retire_says_so(
        self, server: Path, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.app import cli

        monkeypatch.setattr(retired_names, "SYSTEM_ROOT", server)
        monkeypatch.setattr(retired_names, "_home", lambda: home)

        result = CliRunner().invoke(cli, ["setup", "retire-links"])

        assert result.exit_code == 0, result.output
        assert "Nothing" in result.output

    def test_a_rehearsal_changes_nothing_and_says_so(
        self, server: Path, home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.app import cli

        links = link_old_names(server, home)
        monkeypatch.setattr(retired_names, "SYSTEM_ROOT", server)
        monkeypatch.setattr(retired_names, "_home", lambda: home)

        result = CliRunner().invoke(cli, ["--dry-run", "setup", "retire-links"])

        assert result.exit_code == 0, result.output
        assert "would" in result.output
        assert all(link.is_symlink() for link in links.values())


class TestEntryPointGuard:
    """The error boundary refuses before any command reads a path."""

    def test_it_refuses_and_names_the_way_out(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from noust.cli import app as app_module

        def refuse(**_: object) -> None:
            raise ConfigError("data is under the old names", details="install 3.3.1 first")

        monkeypatch.setattr(retired_names, "require_moved", refuse)
        ran: list[bool] = []
        monkeypatch.setattr(app_module.cli, "main", lambda *a, **k: ran.append(True) or 0)

        assert app_module.main(["list"]) == 1

        captured = capsys.readouterr()
        assert not ran
        assert "data is under the old names" in captured.out + captured.err
        assert "install 3.3.1 first" in captured.out + captured.err

    @pytest.mark.parametrize(
        "args", [[], ["--help"], ["-h"], ["--version"], ["-V"], ["--changelog"]]
    )
    def test_help_and_version_still_answer(
        self, monkeypatch: pytest.MonkeyPatch, args: list[str]
    ) -> None:
        from noust.cli import app as app_module

        def refuse(**_: object) -> None:
            raise ConfigError("data is under the old names")

        monkeypatch.setattr(retired_names, "require_moved", refuse)
        monkeypatch.setattr(app_module.cli, "main", lambda *a, **k: 0)

        assert app_module.main(args) == 0
