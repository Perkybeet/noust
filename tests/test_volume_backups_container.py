# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The volumes of a container application have copies, as the volumes of a Compose stack do.

``noust app container`` runs an image as a Compose project Noust writes, with named volumes the
operator chose (``--volume /data``). The documentation said "the data in its volumes is not part of
`noust backup`: volume backups are a separate feature" and the separate feature offered only
``docker-compose`` applications: ``volume_backups.py`` compared the type with that literal, the way
fifty other places do, while ``store.is_compose_backed`` is the one answer to "does this run as a
Compose project". A container application's targets, its calm (``docker pause`` of its
containers, not a ``systemctl freeze`` of a unit that holds none) and its copy are now the stack's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.store import App, get_store
from noust.managers.backup_manager import BackupManager
from noust.managers.volume_backups import VolumeBackupManager
from tests.support.database_backup_support import RcloneRunner
from tests.support.volume_backups import (  # noqa: F401
    _reset_store,
    app,
    app_dir,
    audited,
    manager,
    runner,
    services,
)

# A fixture imported from the support module is found by pytest in this module's namespace, and a
# test that asks for it by name looks like a redefinition of an import that is never used.
# ruff: noqa: F811

DOMAIN = "box.example.com"


def container_app(tmp_path: Path) -> App:
    """A container application whose compose file Noust wrote, in place."""
    root = tmp_path / "apps" / "box-example-com"
    root.mkdir(parents=True)
    return get_store().create_app(
        App(domain=DOMAIN, app_type="container", app_path=str(root), layout="inplace")
    )


def test_a_container_application_has_its_named_volumes_as_targets(
    manager: VolumeBackupManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container_app(tmp_path)
    monkeypatch.setattr(
        BackupManager, "docker_volumes", lambda self, domain: ["box-example-com_data", "bad name!"]
    )

    found = manager.targets(DOMAIN)

    assert [(t.key, t.kind, t.declared_by) for t in found] == [
        ("volume:box-example-com_data", "docker", "Compose volume")
    ]


def test_an_application_that_is_not_a_compose_project_still_has_none(
    manager: VolumeBackupManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "apps" / "plain-example-com"
    root.mkdir(parents=True)
    get_store().create_app(
        App(domain="plain.example.com", app_type="nodejs", app_path=str(root), layout="inplace")
    )
    monkeypatch.setattr(BackupManager, "docker_volumes", lambda self, domain: ["not_its_own"])

    assert manager.targets("plain.example.com") == []


def test_pause_of_a_container_application_pauses_its_containers_and_unpauses_them(
    manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path
) -> None:
    """Its unit is a oneshot that starts the project and stays active: freezing it holds nothing."""
    runner.script(("docker", "ps", "-q"), stdout="abc123\n")
    app = container_app(tmp_path)
    (Path(app.app_path) / "data").mkdir()

    manager.create(DOMAIN, extra_paths=["data"], consistency="pause")

    assert ("docker", "pause", "abc123") in runner.calls
    assert ("docker", "unpause", "abc123") in runner.calls
    assert not any(call[:2] == ("systemctl", "freeze") for call in runner.calls)
    listed = next(call for call in runner.calls if call[:3] == ("docker", "ps", "-q"))
    assert f"label=com.docker.compose.project.working_dir={app.app_path}" in listed


def test_the_volumes_are_asked_of_compose_through_the_file_noust_wrote(
    manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path
) -> None:
    """No stand-in for the manager that names them: the container deployer resolves its own file."""
    import json

    app = container_app(tmp_path)
    runner.script(
        ("docker", "compose"),
        stdout=json.dumps({"name": "box-example-com", "volumes": {"data": {}, "cache": {}}}),
    )

    found = manager.targets(DOMAIN)

    assert [t.key for t in found] == [
        "volume:box-example-com_data",
        "volume:box-example-com_cache",
    ]
    asked = next(call for call in runner.calls if call[:2] == ("docker", "compose"))
    assert str(Path(app.app_path) / "compose.noust.yaml") in asked
    assert asked[-3:] == ("config", "--format", "json")
