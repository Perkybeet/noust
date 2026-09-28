# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A reload never shows another thread the defaults.

``Config()`` is one instance per process. ``_load_config`` used to set the
defaults on it and then merge the file into them, so a thread reading
``apps_directory`` while another reloaded - the notification thread, once per
deploy - could see ``/var/www/apps`` instead of what config.yaml says. The
merged mapping is now built aside and swapped in once, and the notification
thread reads a detached :meth:`Config.snapshot` instead of reloading the
shared instance at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from wasm.core.config import DEFAULT_CONFIG, Config

CUSTOM_APPS = "/srv/custom-apps"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A config.yaml that moves apps_directory away from the default."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"apps_directory": CUSTOM_APPS}))
    monkeypatch.setattr("wasm.core.config.DEFAULT_CONFIG_PATH", path)
    monkeypatch.delenv("WASM_APPS_DIR", raising=False)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


def test_a_reader_during_reload_sees_the_previous_values(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config()
    assert str(config.apps_directory) == CUSTOM_APPS
    assert DEFAULT_CONFIG["apps_directory"] != CUSTOM_APPS

    seen: list[str] = []
    original = Config._deep_merge

    def observing_merge(self: Config, base: dict[str, Any], override: dict[str, Any]) -> Any:
        # Exactly the moment the old loader had the defaults in place.
        seen.append(str(Config().apps_directory))
        return original(self, base, override)

    monkeypatch.setattr(Config, "_deep_merge", observing_merge)
    config.reload()

    assert seen == [CUSTOM_APPS]
    assert str(config.apps_directory) == CUSTOM_APPS


def test_env_overrides_still_apply_after_the_swap(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WASM_APPS_DIR", "/from/env")
    assert str(Config().apps_directory) == "/from/env"


def test_a_snapshot_is_detached_from_the_shared_instance(config_path: Path) -> None:
    shared = Config()
    shared.set("apps_directory", "/unsaved/in/memory")

    snapshot = Config.snapshot()

    assert snapshot is not shared
    assert str(snapshot.apps_directory) == CUSTOM_APPS
    # Reading the file afresh must not have touched what the process shares.
    assert str(shared.apps_directory) == "/unsaved/in/memory"
    assert Config() is shared


def test_a_snapshot_follows_the_file(config_path: Path) -> None:
    Config()
    config_path.write_text(yaml.safe_dump({"apps_directory": "/srv/changed"}))
    assert str(Config.snapshot().apps_directory) == "/srv/changed"
