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


def test_an_archive_is_handed_to_whoever_asked_for_it_when_that_is_not_root() -> None:
    # Seen on a real central (Docker 24.0.2): the helper container is root, so the archive of a volume was root's,
    # and the central, which is user 10001, failed to give it its mode ("Operation not permitted") and so never
    # kept a scheduled copy of a volume.
    argv = docker_archive_argv("v", Path("/b"), "v.tar.gz", owner="10001:10001")

    assert argv[-5:-3] == ["sh", "-c"] or argv[argv.index("sh") + 1] == "-c"
    script = argv[argv.index("-c") + 1]
    assert script == 'tar czf "/backup/$1" -C /data . && chown "$2" "/backup/$1"'
    assert argv[-3:] == ["sh", "v.tar.gz", "10001:10001"]
    # The name and the owner are parameters of the shell, never part of its script.
    assert "v.tar.gz" not in script and "10001" not in script


def test_the_owner_is_a_user_and_a_group_number_and_nothing_a_shell_could_read() -> None:
    import pytest

    for bad in ("root", "1;rm -rf /", "10001", "10001:", ":10001", "-1:5"):
        with pytest.raises(ValueError):
            docker_archive_argv("v", Path("/b"), "v.tar.gz", owner=bad)


def test_a_server_as_root_asks_for_the_archive_exactly_as_it_always_did(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from noust.managers import docker_volumes

    monkeypatch.setattr(docker_volumes.os, "geteuid", lambda: 0)

    assert docker_volumes.handed_to() is None
    assert docker_archive_argv("v", Path("/b"), "v.tar.gz", owner=docker_volumes.handed_to())[
        -5:
    ] == ["czf", "/backup/v.tar.gz", "-C", "/data", "."]


def test_the_central_as_an_unprivileged_user_is_the_owner_of_what_it_asks_for(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from noust.managers import docker_volumes

    monkeypatch.setattr(docker_volumes.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(docker_volumes.os, "getegid", lambda: 10002)

    assert docker_volumes.handed_to() == "10001:10002"


def test_the_archive_every_caller_keeps_is_handed_to_the_account_that_runs_it(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from noust.managers import docker_volumes

    monkeypatch.setattr(docker_volumes.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(docker_volumes.os, "getegid", lambda: 10001)
    unprivileged = docker_volumes.handed_archive_argv("v", Path("/b"), "v.tar.gz")

    monkeypatch.setattr(docker_volumes.os, "geteuid", lambda: 0)
    root = docker_volumes.handed_archive_argv("v", Path("/b"), "v.tar.gz")

    assert unprivileged[-3:] == ["sh", "v.tar.gz", "10001:10001"]
    assert root == docker_archive_argv("v", Path("/b"), "v.tar.gz")
