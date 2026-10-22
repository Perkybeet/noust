# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files Noust keeps in an application's tree carry its name from 4.0.

An application deployed before keeps the old ones until its next deployment (or ``noust setup
rename``) moves them: they are read under either name in the meantime, and a repository's own
link under either name is never followed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core import legacy_names
from noust.core.fs import DryRunFileSystem, get_fs
from noust.deployers.helpers import tree_names


def test_the_names_are_noust_s() -> None:
    assert tree_names.ENV_DIR == ".noust"
    assert tree_names.RUNTIME_STAMP == ".noust-runtime.json"
    assert tree_names.PHP_SETTINGS == ".noust-php.json"
    assert tree_names.PHP_TMP == ".noust-php-tmp"


class TestPresent:
    def test_the_new_name_is_found_first(self, tmp_path: Path) -> None:
        (tmp_path / tree_names.ENV_DIR).mkdir()
        (tmp_path / legacy_names.TREE_ENV_DIR).mkdir()

        assert tree_names.present(tmp_path, tree_names.ENV_DIR) == tmp_path / tree_names.ENV_DIR

    def test_the_old_name_is_found_when_it_is_all_there_is(self, tmp_path: Path) -> None:
        (tmp_path / legacy_names.TREE_RUNTIME_STAMP).write_text("{}")

        assert (
            tree_names.present(tmp_path, tree_names.RUNTIME_STAMP)
            == tmp_path / legacy_names.TREE_RUNTIME_STAMP
        )

    def test_nothing_when_neither_is_there(self, tmp_path: Path) -> None:
        assert tree_names.present(tmp_path, tree_names.PHP_SETTINGS) is None

    def test_a_link_in_the_tree_is_not_followed(self, tmp_path: Path) -> None:
        """A repository is untrusted input: ``.wasm -> /etc`` found in it is nothing of ours."""
        (tmp_path / legacy_names.TREE_ENV_DIR).symlink_to("/etc")

        assert tree_names.present(tmp_path, tree_names.ENV_DIR) is None


class TestAdopt:
    def test_a_directory_is_renamed_with_what_is_in_it(self, tmp_path: Path) -> None:
        old = tmp_path / legacy_names.TREE_ENV_DIR
        old.mkdir(mode=0o700)
        (old / "env-config.json").write_text('{"a": 1}')

        assert tree_names.adopt(tmp_path, tree_names.ENV_DIR, fs=get_fs()) is True

        assert not old.exists()
        assert json.loads((tmp_path / ".noust" / "env-config.json").read_text()) == {"a": 1}
        assert (tmp_path / ".noust").stat().st_mode & 0o777 == 0o700

    def test_a_file_is_renamed(self, tmp_path: Path) -> None:
        (tmp_path / legacy_names.TREE_PHP_SETTINGS).write_text("{}")

        assert tree_names.adopt(tmp_path, tree_names.PHP_SETTINGS, fs=get_fs()) is True

        assert (tmp_path / tree_names.PHP_SETTINGS).read_text() == "{}"
        assert not (tmp_path / legacy_names.TREE_PHP_SETTINGS).exists()

    def test_when_both_are_there_the_new_one_wins_and_the_stale_one_goes(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / legacy_names.TREE_PHP_SETTINGS).write_text("old")
        (tmp_path / tree_names.PHP_SETTINGS).write_text("new")

        assert tree_names.adopt(tmp_path, tree_names.PHP_SETTINGS, fs=get_fs()) is True

        assert (tmp_path / tree_names.PHP_SETTINGS).read_text() == "new"
        assert not (tmp_path / legacy_names.TREE_PHP_SETTINGS).exists()

    def test_nothing_to_do_is_not_an_error(self, tmp_path: Path) -> None:
        assert tree_names.adopt(tmp_path, tree_names.ENV_DIR, fs=get_fs()) is False

    def test_a_link_is_left_where_it_is(self, tmp_path: Path) -> None:
        link = tmp_path / legacy_names.TREE_ENV_DIR
        link.symlink_to("/etc")

        assert tree_names.adopt(tmp_path, tree_names.ENV_DIR, fs=get_fs()) is False

        assert link.is_symlink()
        assert not (tmp_path / tree_names.ENV_DIR).exists()

    def test_a_rehearsal_changes_nothing(self, tmp_path: Path) -> None:
        old = tmp_path / legacy_names.TREE_PHP_SETTINGS
        old.write_text("{}")

        tree_names.adopt(tmp_path, tree_names.PHP_SETTINGS, fs=DryRunFileSystem())

        assert old.exists()
        assert not (tmp_path / tree_names.PHP_SETTINGS).exists()


class TestSweep:
    """Everything an application's tree holds under the old names, in one pass."""

    def make_tree(self, root: Path) -> Path:
        app = root / "shop-example-com"
        (app / legacy_names.TREE_ENV_DIR).mkdir(parents=True, mode=0o700)
        (app / legacy_names.TREE_ENV_DIR / "env-config.json").write_text("{}")
        (app / legacy_names.TREE_PHP_SETTINGS).write_text("{}")
        for release in ("20260101-000000", "20260102-000000"):
            modules = app / "releases" / release / "node_modules"
            modules.mkdir(parents=True)
            (modules / legacy_names.TREE_RUNTIME_STAMP).write_text('{"node": "22"}')
        shared = app / "shared" / legacy_names.TREE_ENV_DIR
        shared.mkdir(parents=True)
        return app

    def test_every_old_name_in_the_tree_is_renamed(self, tmp_path: Path) -> None:
        app = self.make_tree(tmp_path)

        renamed = tree_names.sweep(app, fs=get_fs())

        assert len(renamed) == 5
        leftovers = [p.name for p in app.rglob(".wasm*")]
        assert leftovers == []
        assert (app / ".noust" / "env-config.json").is_file()
        assert (
            app / "releases" / "20260101-000000" / "node_modules" / ".noust-runtime.json"
        ).is_file()

    def test_running_it_again_finds_nothing(self, tmp_path: Path) -> None:
        app = self.make_tree(tmp_path)
        tree_names.sweep(app, fs=get_fs())

        assert tree_names.sweep(app, fs=get_fs()) == []

    def test_a_tree_that_is_not_there_is_nothing(self, tmp_path: Path) -> None:
        assert tree_names.sweep(tmp_path / "gone", fs=get_fs()) == []

    @pytest.mark.parametrize("name", [".venv", "venv", "node_modules"])
    def test_the_runtime_stamp_is_found_in_each_dependency_directory(
        self, tmp_path: Path, name: str
    ) -> None:
        directory = tmp_path / "releases" / "r1" / name
        directory.mkdir(parents=True)
        (directory / legacy_names.TREE_RUNTIME_STAMP).write_text("{}")

        tree_names.sweep(tmp_path, fs=get_fs())

        assert (directory / tree_names.RUNTIME_STAMP).is_file()
