"""
Operators are told, once, that WASM is now Noust; programs are never told.

``wasm`` keeps working through 3.x, so a script or a cron line must see
exactly what it saw before: nothing on a pipe, nothing under ``--json``. A
person at a terminal who types ``wasm`` reads the notice once.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli import rename_notice
from noust.cli.app import cli
from noust.core import paths


class Terminal(io.StringIO):
    """A stream that says it is a terminal, or not."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the "already told" marker inside the test."""
    monkeypatch.setattr(paths, "user_state_dir", lambda home=None: tmp_path / "state")
    return tmp_path / "state"


def test_typed_as_wasm_at_a_terminal_it_is_shown_once(state_home: Path) -> None:
    first, second = Terminal(tty=True), Terminal(tty=True)

    assert rename_notice.maybe_announce("/usr/bin/wasm", ["list"], first)
    assert not rename_notice.maybe_announce("/usr/bin/wasm", ["list"], second)

    assert "WASM is now called Noust. The command is `noust`" in first.getvalue()
    assert "`wasm` keeps working throughout 3.x" in first.getvalue()
    assert paths.UPGRADE_NOTES_URL in first.getvalue()
    assert second.getvalue() == ""
    assert (state_home / rename_notice.MARKER_NAME).exists()


def test_typed_as_noust_nothing_is_said() -> None:
    stream = Terminal(tty=True)
    assert not rename_notice.maybe_announce("/usr/bin/noust", ["list"], stream)
    assert stream.getvalue() == ""


def test_on_a_pipe_nothing_is_said_and_nothing_remembered(state_home: Path) -> None:
    stream = Terminal(tty=False)
    assert not rename_notice.maybe_announce("wasm", ["list"], stream)
    assert stream.getvalue() == ""
    assert not state_home.exists()


def test_under_json_nothing_is_said(state_home: Path) -> None:
    stream = Terminal(tty=True)
    assert not rename_notice.maybe_announce("wasm", ["list", "--json"], stream)
    assert stream.getvalue() == ""
    assert not state_home.exists()


def test_a_rehearsal_says_it_but_remembers_nothing(state_home: Path) -> None:
    stream = Terminal(tty=True)
    assert rename_notice.maybe_announce("wasm", ["--dry-run", "list"], stream)
    assert "Noust" in stream.getvalue()
    assert not state_home.exists()


def test_version_names_both() -> None:
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith("Noust ")
    assert result.output.rstrip().endswith("(formerly WASM)")


class TestMigrateCommand:
    """``noust migrate-from-wasm`` shows what was, or would be, done."""

    @pytest.fixture
    def nothing_left(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import noust.cli.commands.migrate_from_wasm as command
        from noust.core.migrate_from_wasm import Layout, Migrator

        empty = Layout(
            config_dir=tmp_path / "etc/noust",
            legacy_config_dir=tmp_path / "etc/wasm",
            state_dir=tmp_path / "var/lib/noust",
            legacy_state_dir=tmp_path / "var/lib/wasm",
            backup_dir=tmp_path / "var/backups/noust",
            legacy_backup_dir=tmp_path / "var/backups/wasm",
            user_data_dir=tmp_path / "home/.local/share/noust",
            legacy_user_data_dir=tmp_path / "home/.local/share/wasm",
            upstreams_dir=tmp_path / "etc/nginx/noust-upstreams",
            legacy_upstreams_dir=tmp_path / "etc/nginx/wasm-upstreams",
            nginx_site_dirs=(),
            systemd_dir=tmp_path / "systemd",
            php_root=tmp_path,
            lock_file=tmp_path / "lock",
        )
        monkeypatch.setattr(command, "needs_migration", lambda: False)
        monkeypatch.setattr(command, "Migrator", lambda: Migrator(empty))
        monkeypatch.setattr(paths, "MIGRATION_RECORD", tmp_path / "none.json")

    @pytest.mark.usefixtures("nothing_left")
    def test_nothing_to_do_says_so(self) -> None:
        result = CliRunner().invoke(cli, ["migrate-from-wasm"])

        assert result.exit_code == 0, result.output
        assert "Nothing of WASM's is left to migrate" in result.output

    @pytest.mark.usefixtures("nothing_left")
    def test_json_is_the_plan(self) -> None:
        result = CliRunner().invoke(cli, ["migrate-from-wasm", "--json"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"dry_run": True, "complete": True, "steps": []}

    def test_dry_run_lists_the_steps(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import noust.cli.commands.migrate_from_wasm as command
        from noust.core.migrate_from_wasm import Layout, Migrator

        legacy = tmp_path / "etc/wasm"
        legacy.mkdir(parents=True)
        (tmp_path / "etc").mkdir(exist_ok=True)
        layout = Layout(
            config_dir=tmp_path / "etc/noust",
            legacy_config_dir=legacy,
            state_dir=tmp_path / "var/lib/noust",
            legacy_state_dir=tmp_path / "var/lib/wasm",
            backup_dir=tmp_path / "var/backups/noust",
            legacy_backup_dir=tmp_path / "var/backups/wasm",
            user_data_dir=tmp_path / "home/noust",
            legacy_user_data_dir=tmp_path / "home/wasm",
            upstreams_dir=tmp_path / "etc/nginx/noust-upstreams",
            legacy_upstreams_dir=tmp_path / "etc/nginx/wasm-upstreams",
            nginx_site_dirs=(),
            systemd_dir=tmp_path / "systemd",
            php_root=tmp_path,
            lock_file=tmp_path / "lock",
        )
        monkeypatch.setattr(command, "Migrator", lambda: Migrator(layout))

        result = CliRunner().invoke(cli, ["--dry-run", "migrate-from-wasm"])

        assert result.exit_code == 0, result.output
        assert "would do" in result.output
        assert str(legacy) in result.output
        assert legacy.is_dir() and not legacy.is_symlink()
