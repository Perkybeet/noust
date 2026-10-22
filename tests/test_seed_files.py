# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Files a deployment is seeded with, for any deployer (checklist D-11): written once, never through a link."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.exceptions import DeploymentError
from noust.core.fs import RealFileSystem
from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.deployers import lifecycle
from noust.deployers.helpers.seed_files import DEFAULT_MODE, seed_files, seeded_paths
from noust.deployers.php_fpm import PhpFpmDeployer
from tests.test_php_fpm import (
    DOMAIN,
    WORDPRESS,
    WORDPRESS_OPTIONS,
    deploy,
    machine,  # noqa: F401
    store,  # noqa: F401
    wire,
)
from tests.test_release_pipeline import write_tree


def seed(root: Path, files: dict[str, str], modes: dict[str, int] | None = None) -> list[str]:
    """Seed a directory the way a deployer does."""
    return seed_files(
        root, files, modes or {}, fs=RealFileSystem(), logger=Logger(), label="shared/"
    )


class TestTheHelper:
    def test_a_file_is_written_with_its_parents_and_its_mode(self, tmp_path: Path) -> None:
        written = seed(tmp_path, {"config/app.json": '{"a": 1}'}, {"config/app.json": 0o600})

        assert written == ["config/app.json"]
        target = tmp_path / "config/app.json"
        assert target.read_text() == '{"a": 1}'
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_the_default_mode_keeps_everyone_else_out(self, tmp_path: Path) -> None:
        seed(tmp_path, {"a.conf": "x"})

        assert stat.S_IMODE((tmp_path / "a.conf").stat().st_mode) == DEFAULT_MODE == 0o640

    def test_a_file_that_exists_is_the_operators_and_is_left_alone(self, tmp_path: Path) -> None:
        (tmp_path / "a.conf").write_text("edited")

        written = seed(tmp_path, {"a.conf": "recipe"})

        assert written == [] and (tmp_path / "a.conf").read_text() == "edited"

    def test_a_link_in_the_way_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "shared"
        root.mkdir()
        os.symlink(outside, root / "config")

        with pytest.raises(DeploymentError, match="not a plain directory"):
            seed(root, {"config/app.json": "x"})

        assert list(outside.iterdir()) == []

    def test_a_root_that_is_a_link_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        os.symlink(real, tmp_path / "shared")

        with pytest.raises(DeploymentError, match="symlink"):
            seed(tmp_path / "shared", {"a.conf": "x"})

    @pytest.mark.parametrize("path", ["../escape", "/etc/passwd", ""])
    def test_a_path_that_leaves_the_application_is_refused(self, path: str) -> None:
        with pytest.raises(DeploymentError):
            seeded_paths({path: "x"})

    def test_paths_are_normalised(self) -> None:
        assert seeded_paths({"a//b/./c.conf": "x"}) == {"a/b/c.conf": "x"}


class TestThroughADeployer:
    """The PHP deployer is a BaseDeployer: the generic option reaches ``shared/`` and the release."""

    def options(self) -> dict[str, object]:
        return {
            **WORDPRESS_OPTIONS,
            "persistent_paths": [*WORDPRESS_OPTIONS["persistent_paths"], "config/app.json"],
            "seed_files": {"config/app.json": '{"url": "https://blog.example.com"}\n'},
            "seed_modes": {"config/app.json": 0o600},
        }

    def test_a_seeded_file_is_written_into_shared_and_linked_into_the_release(
        self,
        tmp_path: Path,
        machine: SimpleNamespace,  # noqa: F811
        store: NoustStore,  # noqa: F811
    ) -> None:
        machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))

        deploy(machine, **self.options())

        shared = machine.root / "shared/config/app.json"
        assert shared.read_text() == '{"url": "https://blog.example.com"}\n'
        assert stat.S_IMODE(shared.stat().st_mode) == 0o600
        assert (machine.root / "current/config/app.json").is_symlink()
        assert (machine.root / "current/config/app.json").read_text() == shared.read_text()

    def test_php_files_still_seed_as_they_did(
        self,
        tmp_path: Path,
        machine: SimpleNamespace,  # noqa: F811
        store: NoustStore,  # noqa: F811
    ) -> None:
        machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))

        deploy(machine, **WORDPRESS_OPTIONS)

        assert stat.S_IMODE((machine.root / "shared/wp-config.php").stat().st_mode) == 0o640

    def test_an_edit_survives_the_next_update(
        self,
        tmp_path: Path,
        machine: SimpleNamespace,  # noqa: F811
        store: NoustStore,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        machine.git.publish(write_tree(tmp_path / "v1", WORDPRESS))
        deploy(machine, **self.options())
        (machine.root / "shared/config/app.json").write_text("{edited}")

        machine.git.publish(write_tree(tmp_path / "v2", {**WORDPRESS, "index.php": "<?php // 2\n"}))
        monkeypatch.setattr(
            lifecycle,
            "get_deployer",
            lambda app_type, verbose=False: wire(
                PhpFpmDeployer(verbose=False, runner=machine.runner), machine
            ),
        )
        lifecycle.update_app(DOMAIN)

        assert (machine.root / "shared/config/app.json").read_text() == "{edited}"
        assert (machine.root / "current/config/app.json").read_text() == "{edited}"
