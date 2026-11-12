# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The image the copies of a Docker volume run in is pinned by digest, like the database engines'.

A copy mounts the volume in a throw-away container and ``tar`` does the work. The image used to be
the bare name ``alpine``: whatever the registry served that day, pulled the first time a volume was
copied, which without a network or without Docker Hub failed. The table of database engines pins
tag and digest (:mod:`noust.managers.database.instance_catalog`); this holds the helper to the same
rule, and holds every argv that runs it to one spelling (rule 3).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import noust
from noust.core.runner import FakeRunner
from noust.managers import docker_volumes
from noust.managers.docker_volumes import (
    HELPER_IMAGE,
    docker_archive_argv,
    docker_restore_argv,
)

SRC = Path(noust.__file__).resolve().parent


def test_the_helper_is_named_by_tag_and_by_digest() -> None:
    assert re.fullmatch(r"alpine:\d+\.\d+@sha256:[0-9a-f]{64}", HELPER_IMAGE)


def test_a_copy_and_a_restore_run_in_the_pinned_image() -> None:
    archive = docker_archive_argv("shop_data", Path("/var/backups/x"), "shop_data.tar.gz")
    restore = docker_restore_argv("shop_data", Path("/var/backups/x"), "shop_data.tar.gz")

    assert HELPER_IMAGE in archive
    assert HELPER_IMAGE in restore
    assert "alpine" not in archive and "alpine" not in restore


def test_the_restore_of_an_application_backup_runs_in_the_pinned_image(tmp_path: Path) -> None:
    from noust.managers.backup_manager import BackupManager

    runner = FakeRunner()
    archive = tmp_path / "shop_data.tar.gz"
    archive.write_bytes(b"x")
    manager = BackupManager(runner=runner)
    manifest = {
        "volumes": [{"volume": "shop_data", "archive_path": "volumes/shop_data.tar.gz"}],
    }
    (tmp_path / "volumes").mkdir()
    (tmp_path / "volumes" / "shop_data.tar.gz").write_bytes(b"x")

    manager._restore_docker_volumes(manifest, tmp_path, None)

    argv = next(call for call in runner.calls if call[:2] == ("docker", "run"))
    assert HELPER_IMAGE in argv
    assert "alpine" not in argv


def test_every_helper_says_it_is_nousts_so_the_gate_to_docker_never_hands_it_to_anybody(
    tmp_path: Path,
) -> None:
    """The label is a claim that can only take access away: see ``noust.core.docker.owners``."""
    from noust.managers.backup_manager import BackupManager

    archive = docker_archive_argv("v", Path("/b"), "v.tar.gz")
    restore = docker_restore_argv("v", Path("/b"), "v.tar.gz")
    runner = FakeRunner()
    (tmp_path / "volumes").mkdir()
    (tmp_path / "volumes" / "v.tar.gz").write_bytes(b"x")
    BackupManager(runner=runner)._restore_docker_volumes(
        {"volumes": [{"volume": "v", "archive_path": "volumes/v.tar.gz"}]}, tmp_path, None
    )
    merge = next(call for call in runner.calls if call[:2] == ("docker", "run"))

    for argv in (archive, restore, merge):
        index = list(argv).index("--label")
        assert argv[index + 1] == "io.noust.helper=volume-backup"
        assert index < list(argv).index(HELPER_IMAGE)


def test_no_command_of_the_package_runs_a_bare_alpine() -> None:
    """A new spelling of the helper is a new way to be unpinned: say where it is allowed."""
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.List | ast.Tuple) or not node.elts:
                continue
            first = node.elts[0]
            if not (isinstance(first, ast.Constant) and first.value == "docker"):
                continue
            words = {e.value for e in node.elts if isinstance(e, ast.Constant)}
            if "alpine" in words:
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not found, "These commands name alpine without its digest: " + ", ".join(found)


def test_the_module_says_where_the_pin_was_read_from() -> None:
    assert "database_pins.py" in (docker_volumes.__doc__ or "")
