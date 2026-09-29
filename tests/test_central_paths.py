"""
NOUST_DATA_DIR: one directory holding everything a central keeps.

A container has one volume, and everything that must survive a restart - the
configuration, the store, the secrets, the console's keys and certificate -
has to land on it. The precedence is: an explicit ``NOUST_DATA_DIR`` puts
every location under it; without it, the system locations resolve as they
always did (a real WASM directory wins over a new, empty Noust one).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from noust.core import paths


def load_paths(monkeypatch: pytest.MonkeyPatch, **env: str) -> ModuleType:
    """
    Import a private copy of noust.core.paths under a given environment.

    The module computes its constants when it is imported, which is the
    point: every module that reads ``paths.STATE_DIR`` at import time agrees
    with it. A private copy tests that without reloading the one every other
    module already holds.

    Args:
        monkeypatch: Patching helper, scoped to the test.
        **env: Environment variables to set; an empty value unsets it.

    Returns:
        The freshly imported module.
    """
    for name, value in env.items():
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("noust_paths_under_test", paths.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "noust_paths_under_test", module)
    spec.loader.exec_module(module)
    return module


class TestDataDir:
    def test_unset_means_no_data_dir(self) -> None:
        assert paths.data_dir({}) is None

    def test_empty_means_no_data_dir(self) -> None:
        assert paths.data_dir({"NOUST_DATA_DIR": ""}) is None
        assert paths.data_dir({"NOUST_DATA_DIR": "   "}) is None

    def test_an_absolute_directory(self) -> None:
        assert paths.data_dir({"NOUST_DATA_DIR": "/data"}) == Path("/data")

    def test_a_relative_directory_is_refused(self) -> None:
        from noust.core.exceptions import ConfigError

        with pytest.raises(ConfigError, match="absolute"):
            paths.data_dir({"NOUST_DATA_DIR": "data"})

    def test_the_wasm_spelling_does_not_exist(self) -> None:
        # A variable 3.0 introduced has no WASM spelling to honour.
        assert paths.data_dir({"WASM_DATA_DIR": "/data"}) is None

    def test_the_layout_under_it(self) -> None:
        layout = paths.data_layout(Path("/data"))
        assert layout.config == Path("/data/config")
        assert layout.state == Path("/data/state")
        assert layout.backups == Path("/data/backups")
        assert layout.logs == Path("/data/log")
        assert layout.all() == (
            Path("/data/config"),
            Path("/data/state"),
            Path("/data/backups"),
            Path("/data/log"),
        )


class TestPrecedence:
    def test_without_it_the_system_locations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        module = load_paths(monkeypatch, NOUST_DATA_DIR="")
        assert module.DATA_DIR is None
        assert module.CONFIG_DIR == Path("/etc/noust")
        assert module.STATE_DIR == Path("/var/lib/noust")
        assert module.BACKUP_DIR == Path("/var/backups/noust")
        assert module.LOG_DIR == Path("/var/log/noust")

    def test_with_it_every_location_moves_under_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        module = load_paths(monkeypatch, NOUST_DATA_DIR=str(tmp_path))
        assert module.DATA_DIR == tmp_path
        assert module.CONFIG_DIR == tmp_path / "config"
        assert module.STATE_DIR == tmp_path / "state"
        assert module.BACKUP_DIR == tmp_path / "backups"
        assert module.LOG_DIR == tmp_path / "log"

    def test_an_explicit_data_dir_wins_over_a_legacy_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Without a data dir, a real /etc/wasm wins over /etc/noust. With one,
        # the operator named where Noust lives, and the resolvers answer it.
        legacy = tmp_path / "etc-wasm"
        legacy.mkdir()
        module = load_paths(monkeypatch, NOUST_DATA_DIR=str(tmp_path / "data"))
        monkeypatch.setattr(module, "LEGACY_CONFIG_DIR", legacy)
        monkeypatch.setattr(module, "LEGACY_STATE_DIR", legacy)
        assert module.config_dir() == tmp_path / "data" / "config"
        assert module.state_dir() == tmp_path / "data" / "state"
        assert module.backup_dir() == tmp_path / "data" / "backups"
        assert module.log_dir() == tmp_path / "data" / "log"

    def test_the_process_wide_constants_agree_with_the_environment(self) -> None:
        # Whatever the suite runs under, the imported module is consistent
        # with data_dir(): the store and config read these constants.
        expected = paths.data_dir()
        assert paths.DATA_DIR == expected
        if expected is None:
            assert paths.STATE_DIR == Path("/var/lib/noust")
        else:
            assert paths.STATE_DIR == paths.data_layout(expected).state

    def test_config_follows_the_config_and_log_dirs(self) -> None:
        from noust.core import config

        assert config.DEFAULT_CONFIG_PATH == paths.config_dir() / "config.yaml"
        assert config.DEFAULT_LOG_DIR == paths.LOG_DIR
