# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What reads and writes the files of an application's tree understands both generations of names.

An application deployed before 4.0 has its inventory, its PHP settings and its runtime stamps
under the old names. The first deployment after the update reads them as they are and leaves
them under the new ones, with nothing dropped on the way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.core import legacy_names
from noust.core.fs import get_fs
from noust.core.runner import FakeRunner
from noust.deployers.helpers import tree_names
from noust.deployers.helpers.env_manager import EnvConfig, EnvManager, EnvVariable
from noust.deployers.helpers.php_fpm import PHP_TMP_ROOT, pool_tmp_dir, prepare_tmp_dir
from noust.deployers.helpers.release_build import _read_runtime_stamp
from noust.deployers.php_fpm import PHP_SETTINGS_FILE, load_php_settings


class TestInventory:
    def legacy_inventory(self, app: Path) -> Path:
        directory = app / legacy_names.TREE_ENV_DIR
        directory.mkdir(mode=0o700)
        config = EnvConfig(variables=[EnvVariable(name="API_KEY")])
        (directory / "env-config.json").write_text(json.dumps(config.to_dict()))
        return directory

    def test_the_inventory_under_the_old_name_is_read(self, tmp_path: Path) -> None:
        self.legacy_inventory(tmp_path)

        loaded = EnvManager().load_config(tmp_path)

        assert loaded is not None
        assert [variable.name for variable in loaded.variables] == ["API_KEY"]

    def test_saving_moves_it_to_the_new_name_and_nothing_stays_behind(self, tmp_path: Path) -> None:
        legacy = self.legacy_inventory(tmp_path)

        EnvManager().save_config(
            tmp_path, EnvConfig(variables=[EnvVariable(name="API_KEY"), EnvVariable(name="B")])
        )

        assert not legacy.exists()
        saved = json.loads((tmp_path / ".noust" / "env-config.json").read_text())
        assert [v["name"] for v in saved["variables"]] == ["API_KEY", "B"]

    def test_a_link_in_the_repository_under_the_old_name_is_not_followed(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / legacy_names.TREE_ENV_DIR).symlink_to("/etc")

        assert EnvManager().load_config(tmp_path) is None
        EnvManager().save_config(tmp_path, EnvConfig())

        assert (tmp_path / ".noust" / "env-config.json").is_file()
        assert (tmp_path / legacy_names.TREE_ENV_DIR).is_symlink()


class TestPhp:
    def test_the_settings_under_the_old_name_are_read(self, tmp_path: Path) -> None:
        (tmp_path / legacy_names.TREE_PHP_SETTINGS).write_text(json.dumps({"webroot": "public"}))

        assert load_php_settings(tmp_path).webroot == "public"

    def test_the_new_name_is_the_one_written(self) -> None:
        assert PHP_SETTINGS_FILE == ".noust-php.json"

    def test_the_temporary_directory_comes_with_what_is_in_it(self, tmp_path: Path) -> None:
        """Sessions and uploads in flight are in there: they move, they are not dropped."""
        apps = tmp_path / "apps"
        app = apps / "shop-example-com"
        legacy = apps / legacy_names.TREE_PHP_TMP / "shop-example-com"
        legacy.mkdir(parents=True)
        (legacy / "sess_abc").write_text("data")

        prepare_tmp_dir(
            pool_tmp_dir(app), user="root", group="root", fs=get_fs(), runner=FakeRunner()
        )

        moved = apps / PHP_TMP_ROOT / "shop-example-com" / "sess_abc"
        assert moved.read_text() == "data"
        assert not (apps / legacy_names.TREE_PHP_TMP).exists()

    def test_another_applications_directory_keeps_the_old_root_alive(self, tmp_path: Path) -> None:
        apps = tmp_path / "apps"
        for name in ("a", "b"):
            (apps / legacy_names.TREE_PHP_TMP / name).mkdir(parents=True)

        prepare_tmp_dir(
            pool_tmp_dir(apps / "a"), user="root", group="root", fs=get_fs(), runner=FakeRunner()
        )

        assert (apps / legacy_names.TREE_PHP_TMP / "b").is_dir()
        assert (apps / PHP_TMP_ROOT / "a").is_dir()


class TestRuntimeStamp:
    def test_a_stamp_under_the_old_name_is_read(self, tmp_path: Path) -> None:
        (tmp_path / legacy_names.TREE_RUNTIME_STAMP).write_text(json.dumps({"node": "v22"}))

        assert _read_runtime_stamp(tmp_path) == {"node": "v22"}

    def test_the_new_stamp_wins(self, tmp_path: Path) -> None:
        (tmp_path / legacy_names.TREE_RUNTIME_STAMP).write_text(json.dumps({"node": "v20"}))
        (tmp_path / tree_names.RUNTIME_STAMP).write_text(json.dumps({"node": "v22"}))

        assert _read_runtime_stamp(tmp_path) == {"node": "v22"}

    @pytest.mark.parametrize("text", ["not json", "[]", '{"node": 22}'])
    def test_a_stamp_that_says_nothing_usable_is_none(self, tmp_path: Path, text: str) -> None:
        (tmp_path / legacy_names.TREE_RUNTIME_STAMP).write_text(text)

        assert _read_runtime_stamp(tmp_path) is None
