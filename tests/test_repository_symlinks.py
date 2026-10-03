# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A repository is untrusted input: no link it commits makes root touch a host file.

A repository that committed ``apps/web/.env.production -> /etc/passwd`` made
the permissions pass chmod ``/etc/passwd`` to 0600, because the env-file
discovery asked ``is_file()`` (which follows links) and the filesystem seam's
chmod followed them too. These tests plant such links and check the target is
left exactly as it was.
"""

from __future__ import annotations

import errno
import io
import stat
from pathlib import Path

import pytest

from noust.core.exceptions import SecurityError
from noust.core.fs import SECRET_MODE, RealFileSystem, RecordingFileSystem
from noust.core.logger import Logger
from noust.core.runner import FakeRunner
from noust.core.store import MonorepoWorkspace, NoustStore
from noust.deployers.helpers.env_manager import EnvConfig, EnvManager
from noust.deployers.helpers.permissions import hand_over_file, hand_over_tree
from noust.deployers.monorepo import MonorepoDeployer
from noust.deployers.nextjs import NextJSDeployer


@pytest.fixture
def store(tmp_path: Path):
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        An isolated store installed as the singleton.
    """
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def host_file(tmp_path: Path) -> Path:
    """
    Returns:
        A file outside the application standing in for ``/etc/passwd``.
    """
    target = tmp_path / "host" / "passwd"
    target.parent.mkdir()
    target.write_text("root:x:0:0:root:/root:/bin/bash\n")
    target.chmod(0o644)
    return target


def mode(path: Path) -> int:
    """
    Returns:
        The permission bits of ``path`` itself, following nothing.
    """
    return stat.S_IMODE(path.stat().st_mode)


def test_the_seam_refuses_to_chmod_through_a_link(tmp_path: Path, host_file: Path) -> None:
    link = tmp_path / ".env"
    link.symlink_to(host_file)

    with pytest.raises(OSError) as raised:
        RealFileSystem().chmod(link, SECRET_MODE, follow_symlinks=False)

    assert raised.value.errno == errno.ELOOP
    assert mode(host_file) == 0o644


def test_the_seam_changes_a_regular_file_without_following(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("A=1\n")
    target.chmod(0o644)

    RealFileSystem().chmod(target, SECRET_MODE, follow_symlinks=False)

    assert mode(target) == SECRET_MODE


def test_hand_over_tree_leaves_the_target_of_a_linked_env_file_alone(
    tmp_path: Path, host_file: Path
) -> None:
    tree = tmp_path / "app"
    tree.mkdir()
    link = tree / ".env.production"
    link.symlink_to(host_file)
    out = io.StringIO()

    hand_over_tree(
        tree,
        user="www-data",
        group="www-data",
        runner=FakeRunner(),
        fs=RealFileSystem(),
        logger=Logger(no_color=True, stream=out),
        env_files=[link],
    )

    assert mode(host_file) == 0o644
    assert "symbolic link" in out.getvalue()


def test_hand_over_tree_leaves_an_env_file_under_a_linked_directory_alone(
    tmp_path: Path, host_file: Path
) -> None:
    """``apps/web -> /etc`` puts ``apps/web/.env.production`` outside the tree."""
    tree = tmp_path / "app"
    (tree / "apps").mkdir(parents=True)
    (tree / "apps" / "web").symlink_to(host_file.parent)
    (host_file.parent / ".env.production").write_text("X=1\n")
    (host_file.parent / ".env.production").chmod(0o644)

    hand_over_tree(
        tree,
        user="www-data",
        group="www-data",
        runner=FakeRunner(),
        fs=RealFileSystem(),
        logger=Logger(no_color=True, stream=io.StringIO()),
        env_files=[tree / "apps" / "web" / ".env.production"],
    )

    assert mode(host_file.parent / ".env.production") == 0o644


def test_hand_over_file_refuses_a_link(tmp_path: Path, host_file: Path) -> None:
    link = tmp_path / ".env"
    link.symlink_to(host_file)
    before = mode(host_file)

    ok = hand_over_file(
        link,
        user="www-data",
        group="www-data",
        mode=SECRET_MODE,
        logger=Logger(no_color=True, stream=io.StringIO()),
    )

    assert ok is False
    assert mode(host_file) == before


def test_the_base_env_file_discovery_skips_links(
    tmp_path: Path, store: NoustStore, host_file: Path
) -> None:
    deployer = NextJSDeployer(runner=FakeRunner(), fs=RecordingFileSystem())
    app = tmp_path / "app"
    app.mkdir()
    deployer.configure("app.example.com", "src", app_path=app)
    (app / ".env").write_text("A=1\n")
    (app / ".env.production").symlink_to(host_file)

    assert deployer._env_files() == [app / ".env"]

    deployer._set_permissions()

    assert mode(host_file) == 0o644
    assert mode(app / ".env") == SECRET_MODE


def test_the_monorepo_permissions_pass_never_chmods_a_linked_env_file(
    tmp_path: Path, store: NoustStore, host_file: Path
) -> None:
    deployer = MonorepoDeployer(runner=FakeRunner(), fs=RecordingFileSystem())
    deployer.configure("example.com", "src", app_path=tmp_path / "app")
    deployer.workspaces = [
        MonorepoWorkspace(name="web", path="apps/web", subdomain="www", port=3000)
    ]
    workspace = tmp_path / "app" / "apps" / "web"
    workspace.mkdir(parents=True)
    (workspace / ".env.production").symlink_to(host_file)

    deployer._set_permissions()

    assert mode(host_file) == 0o644


def test_the_monorepo_refuses_to_write_env_files_into_a_linked_workspace(
    tmp_path: Path, store: NoustStore, host_file: Path
) -> None:
    deployer = MonorepoDeployer(runner=FakeRunner(), fs=RecordingFileSystem())
    deployer.configure("example.com", "src", app_path=tmp_path / "app")
    (tmp_path / "app" / "apps").mkdir(parents=True)
    (tmp_path / "app" / "apps" / "web").symlink_to(host_file.parent)
    workspace = MonorepoWorkspace(name="web", path="apps/web", subdomain="www", port=3000)

    with pytest.raises(SecurityError):
        deployer._write_env_file(deployer._workspace_env_file(workspace), {"A": "1"})

    assert not (host_file.parent / ".env.production").exists()


def test_the_env_config_directory_is_never_tightened_through_a_link(
    tmp_path: Path, host_file: Path
) -> None:
    """A repository's ``.wasm -> /etc`` made root chmod /etc to 0700."""
    app = tmp_path / "app"
    app.mkdir()
    host_dir = host_file.parent
    host_dir.chmod(0o755)
    (app / ".wasm").symlink_to(host_dir)
    manager = EnvManager(fs=RealFileSystem())

    with pytest.raises(SecurityError):
        manager.save_config(app, EnvConfig())

    assert mode(host_dir) == 0o755
