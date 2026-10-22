# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A monorepo's units load a file in each workspace, so a change of its variables has to reach it
(checklist B-43, B-46, B-48).

The root ``.env`` is the one place ``noust env`` and the console edit; the workspaces' own
``.env.production`` is what the processes read. A change goes to every workspace's file, naming
only what it changes, so a workspace keeps its port and what its own configuration asked for; a
link or a file that leaves the tree is never written.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from noust.core.store import App, NoustStore
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers import workspace_env
from noust.deployers.helpers.app_env import write_app_env
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.workspace_env import ENV_FILE_NAME, propagate
from tests.conftest import OwnershipChange

DOMAIN = "mono.example.com"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture(autouse=True)
def account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = SimpleNamespace(
        apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
    )
    monkeypatch.setattr(app_env_module, "Config", lambda: config)
    monkeypatch.setattr(workspace_env, "Config", lambda: config)


@pytest.fixture
def app(store: NoustStore, tmp_path: Path) -> App:
    root = tmp_path / "apps" / "mono-example-com"
    for relative, own in (
        ("apps/web", "PORT=3001\nNODE_ENV=production\n"),
        ("apps/api", "PORT=3002\n"),
    ):
        (root / relative).mkdir(parents=True)
        (root / relative / ENV_FILE_NAME).write_text(own + "API_KEY=old\n")
    workspaces = json.dumps([{"path": "apps/web"}, {"path": "apps/api"}])
    store.create_app(
        App(
            domain=DOMAIN,
            app_path=str(root),
            app_type="monorepo",
            port=3001,
            env_vars={"_workspaces": workspaces},
        )
    )
    found = store.get_app(DOMAIN)
    assert found is not None
    return found


def of(app: App, relative: str) -> dict[str, str]:
    return EnvManager().read_env_file(Path(app.app_path) / relative / ENV_FILE_NAME)


class TestPropagate:
    def test_a_changed_value_reaches_every_workspace_and_their_own_lines_stay(
        self, app: App
    ) -> None:
        propagate(app, changed={"API_KEY": "new", "EXTRA": "1"})

        assert of(app, "apps/web") == {
            "PORT": "3001",
            "NODE_ENV": "production",
            "API_KEY": "new",
            "EXTRA": "1",
        }
        assert of(app, "apps/api") == {"PORT": "3002", "API_KEY": "new", "EXTRA": "1"}

    def test_a_removed_name_goes_from_every_workspace(self, app: App) -> None:
        propagate(app, changed={}, removed=["API_KEY"])

        assert "API_KEY" not in of(app, "apps/web")
        assert of(app, "apps/api") == {"PORT": "3002"}

    def test_the_port_and_the_mode_are_never_carried(self, app: App) -> None:
        propagate(app, changed={"PORT": "9", "NODE_ENV": "development"}, removed=["PORT"])

        assert of(app, "apps/web")["PORT"] == "3001"
        assert of(app, "apps/web")["NODE_ENV"] == "production"

    def test_nothing_is_written_when_the_file_already_says_it(
        self, app: App, tmp_path: Path
    ) -> None:
        assert propagate(app, changed={"API_KEY": "old"}) == []

    def test_the_file_stays_the_units_to_read(
        self, app: App, ownership_changes: list[OwnershipChange]
    ) -> None:
        written = propagate(app, changed={"API_KEY": "new"})

        handed = {change.path: change for change in ownership_changes}
        assert set(handed) == set(written)
        assert all(c.user == "www-data" and c.mode == 0o600 for c in handed.values())

    def test_a_workspace_file_that_is_a_link_is_not_written_through(
        self, app: App, tmp_path: Path
    ) -> None:
        outside = tmp_path / "host-secret"
        outside.write_text("ROOT_ONLY=1\n")
        link = Path(app.app_path) / "apps/web" / ENV_FILE_NAME
        link.unlink()
        link.symlink_to(outside)

        propagate(app, changed={"API_KEY": "new"})

        assert outside.read_text() == "ROOT_ONLY=1\n"
        assert of(app, "apps/api")["API_KEY"] == "new"

    def test_a_workspace_that_is_not_deployed_has_no_file_made(self, app: App) -> None:
        (Path(app.app_path) / "apps/api" / ENV_FILE_NAME).unlink()

        propagate(app, changed={"API_KEY": "new"})

        assert not (Path(app.app_path) / "apps/api" / ENV_FILE_NAME).exists()


class TestTheEditorOfTheApplicationsVariables:
    def test_an_edit_reaches_the_workspaces_through_the_one_writer(self, app: App) -> None:
        write_app_env(app, {"API_KEY": "rotated", "NEW": "1"})

        assert of(app, "apps/web")["API_KEY"] == "rotated"
        assert of(app, "apps/api")["NEW"] == "1"
        assert of(app, "apps/api")["PORT"] == "3002"

    def test_removing_a_variable_takes_it_from_the_workspaces_too(self, app: App) -> None:
        write_app_env(app, {"API_KEY": "x", "GONE": "1"})
        write_app_env(app, {"API_KEY": "x"})

        assert "GONE" not in of(app, "apps/web")
