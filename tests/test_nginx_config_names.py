# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A repository's nginx overrides file is ``noust.nginx.yaml``; the name it had before 4.0 is read.

A deployment that reads the old name says so with the command that renames it in the
repository, so the file is changed once, while it is a sentence and not a surprise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import legacy_names
from noust.deployers.helpers.nginx_config import NginxConfigBuilder


@pytest.fixture
def builder() -> NginxConfigBuilder:
    return NginxConfigBuilder()


def test_the_lookup_order_is_the_current_name_then_the_old_one_then_the_plain_names() -> None:
    assert NginxConfigBuilder.CONFIG_FILENAMES == [
        "noust.nginx.yaml",
        "noust.nginx.yml",
        *legacy_names.NGINX_OVERRIDE_FILES,
        "nginx.yaml",
        "nginx.yml",
    ]


def test_the_current_name_wins_over_the_old_one(
    builder: NginxConfigBuilder, tmp_path: Path
) -> None:
    (tmp_path / "noust.nginx.yaml").write_text("routes: []\n")
    (tmp_path / legacy_names.NGINX_OVERRIDE_FILES[0]).write_text("routes: []\n")

    assert builder.detect(tmp_path) == tmp_path / "noust.nginx.yaml"


@pytest.mark.parametrize("name", legacy_names.NGINX_OVERRIDE_FILES)
def test_a_file_under_the_old_name_is_read_and_asked_to_be_renamed(
    builder: NginxConfigBuilder, tmp_path: Path, name: str
) -> None:
    (tmp_path / name).write_text("routes: []\n")

    found = builder.detect(tmp_path)
    notice = builder.rename_notice(found) if found else None

    assert found == tmp_path / name
    assert notice is not None
    assert f"git mv {name} " in notice
    assert name.replace(name.split(".", 1)[0], "noust", 1) in notice


@pytest.mark.parametrize("name", ["noust.nginx.yaml", "noust.nginx.yml", "nginx.yaml"])
def test_a_current_name_has_nothing_to_rename(name: str) -> None:
    assert NginxConfigBuilder.rename_notice(Path("/repo") / name) is None


class TestADeploymentThatReadsIt:
    """A deployment prints the command that renames the file, where it reads it."""

    def deployer(self, tree: Path) -> tuple[Any, list[str]]:
        from noust.deployers.nodejs import NodeJSDeployer

        deployer = NodeJSDeployer(verbose=False)
        deployer.configure(
            "shop.example.com", "/srv/source", port=3000, ssl=False, app_path=tree, layout="inplace"
        )
        lines: list[str] = []
        deployer.logger.attach_sink(lines.append)
        return deployer, lines

    @pytest.mark.parametrize("name", legacy_names.NGINX_OVERRIDE_FILES)
    def test_the_old_name_is_read_and_the_rename_is_said(self, tmp_path: Path, name: str) -> None:
        (tmp_path / name).write_text("max_body_size: 5m\n")
        deployer, lines = self.deployer(tmp_path)

        deployer._detect_nginx_config()

        assert deployer._nginx_advanced_config is not None
        assert any(f"git mv {name} " in line for line in lines), lines

    def test_the_current_name_says_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "noust.nginx.yaml").write_text("max_body_size: 5m\n")
        deployer, lines = self.deployer(tmp_path)

        deployer._detect_nginx_config()

        assert deployer._nginx_advanced_config is not None
        assert not any("git mv" in line for line in lines), lines
