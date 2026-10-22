# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Promoting a release from one application to another (checklist B-78).

The release trees are real directories and links: what a release holds is read
back from disk. Only the machine is faked (the copy and the search go through a
runner that does them on the temporary tree, systemd and the health probe).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DeploymentError, SchemaChangedError, ValidationError
from noust.core.store import App, NoustStore
from noust.core.teams import TeamStore
from noust.deployers import base as base_module
from noust.deployers import promote as promote_module
from noust.deployers.base import BaseDeployer
from noust.deployers.nodejs import NodeJSDeployer
from noust.deployers.promote import plan_promotion, promote, promotion_targets
from noust.deployers.releases import ReleaseManager
from tests.test_release_pipeline import (
    GIT_URL,
    TreeRunner,
    git,
    machine,
    root,
    store,
    wire,
)

FIXTURES = (git, machine, root, store)

PRODUCTION = "rel.example.com"
STAGING = "staging.example.com"
RELEASE = "20261013-100000-abc1234"
OLD = "20261012-090000-1111111"


class SearchingRunner(TreeRunner):
    """A runner whose ``grep -rIlZ -F`` really searches the tree it is given."""

    def run(self, argv: Any, **kwargs: Any) -> Any:
        if list(argv[:4]) == ["grep", "-rIlZ", "-F", "--"]:
            needle, base = argv[4], Path(argv[5])
            found = []
            for path in sorted(base.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    data = path.read_bytes()
                    if b"\x00" not in data and needle.encode() in data:
                        found.append(str(path))
            self.calls.append(tuple(argv))
            self.envs.append(kwargs.get("env"))
            from noust.core.runner import CommandResult

            return CommandResult(
                argv=tuple(argv),
                exit_code=0 if found else 1,
                stdout="".join(name + "\0" for name in found),
                stderr="",
            )
        return super().run(argv, **kwargs)


def make_tree(app_root: Path, release: str, *, server: str = "ok") -> Path:
    """An application directory with one active release."""
    path = app_root / "releases" / release
    path.mkdir(parents=True)
    (path / "server.js").write_text(server)
    (path / "package.json").write_text('{"scripts": {"start": "node server.js"}}')
    (app_root / "shared").mkdir(exist_ok=True)
    (app_root / "shared" / ".env").write_text("SECRET=1\n")
    os.symlink("../../shared/.env", path / ".env")
    if not (app_root / "current").is_symlink():
        os.symlink(f"releases/{release}", app_root / "current")
    return path


@pytest.fixture
def world(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace
) -> SimpleNamespace:
    """Production (``root``) and staging, one project, two environments."""
    machine.runner = SearchingRunner()
    teams = TeamStore(store)
    project = teams.get_project("default", "default")
    assert project is not None
    staging_env = teams.create_environment("default", "default", "Staging", slug="staging")
    production_env = teams.get_environment("default", "default", "production")
    assert production_env is not None
    staging_root = tmp_path / "apps" / "staging-example-com"
    for domain, path, env in (
        (PRODUCTION, root, production_env),
        (STAGING, staging_root, staging_env),
    ):
        store.create_app(
            App(
                domain=domain,
                app_type="nodejs",
                source=GIT_URL,
                port=3100,
                app_path=str(path),
                layout="releases",
                branch="main",
            )
        )
        teams.move_app(domain, project.id, env.id)
    make_tree(root, OLD, server="old")
    release = make_tree(staging_root, RELEASE, server="new")
    (release / ".venv").mkdir()
    (release / ".venv" / "pyvenv.cfg").write_text(f"home = {staging_root}/releases/{RELEASE}/bin\n")
    (release / "paths.txt").write_text(
        f"{staging_root}/shared and {staging_root}-old/shared and {staging_root}\n"
    )
    (release / "binary.bin").write_bytes(b"\x00\x01" + str(staging_root).encode())
    return SimpleNamespace(staging_root=staging_root, release=release, teams=teams, project=project)


@pytest.fixture(autouse=True)
def wired(machine: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployers made for the target are wired to the fakes, and the gate sees what is active."""
    probes: list[str] = []

    def probe(url: str, **kwargs: Any) -> bool:
        probes.append(url)
        return not getattr(machine, "unhealthy", False)

    monkeypatch.setattr(base_module, "wait_until_healthy", probe)
    monkeypatch.setattr(
        promote_module,
        "get_deployer",
        lambda app_type, verbose=False: wire(
            NodeJSDeployer(verbose=False, runner=machine.runner), machine
        ),
    )
    monkeypatch.setattr(promote_module, "get_runner", lambda: machine.runner)


class TestPromoting:
    def test_the_release_is_put_on_the_target_and_activated(
        self, world: SimpleNamespace, root: Path, store: NoustStore, machine: SimpleNamespace
    ) -> None:
        outcome = promote(STAGING, PRODUCTION)

        assert (outcome.source, outcome.target, outcome.release) == (STAGING, PRODUCTION, RELEASE)
        assert Path(os.readlink(root / "current")).name == RELEASE
        assert (root / "releases" / RELEASE / "server.js").read_text() == "new"
        assert machine.services.restarts[-1] == f"releases/{RELEASE}"
        # The release that served is still there to go back to; the source is untouched.
        assert (root / "releases" / OLD).is_dir()
        assert (world.staging_root / "releases" / RELEASE / "server.js").read_text() == "new"

    def test_the_target_keeps_its_own_environment(self, world: SimpleNamespace, root: Path) -> None:
        promote(STAGING, PRODUCTION)

        link = root / "releases" / RELEASE / ".env"
        assert os.readlink(link) == "../../shared/.env"
        assert (root / "shared" / ".env").read_text() == "SECRET=1\n"
        assert link.resolve() == (root / "shared" / ".env").resolve()

    def test_it_is_recorded_as_a_deployment_of_the_target(
        self, world: SimpleNamespace, store: NoustStore
    ) -> None:
        outcome = promote(STAGING, PRODUCTION)

        rows = store.list_deployments(PRODUCTION)
        assert rows[0].id == outcome.deployment_id and rows[0].status == "success"
        assert rows[0].release_id == RELEASE
        assert rows[0].git_commit == "abc1234"
        app = store.get_app(PRODUCTION)
        assert app is not None
        assert [r.id for r in store.list_releases(app.id) if r.status == "active"] == [RELEASE]

    def test_the_old_root_is_rewritten_where_it_stands_alone(
        self, world: SimpleNamespace, root: Path
    ) -> None:
        promote(STAGING, PRODUCTION)

        copied = root / "releases" / RELEASE
        assert (copied / "paths.txt").read_text() == (
            f"{root}/shared and {world.staging_root}-old/shared and {root}\n"
        )
        assert (copied / ".venv" / "pyvenv.cfg").read_text() == (
            f"home = {root}/releases/{RELEASE}/bin\n"
        )
        # A binary is never rewritten.
        assert str(world.staging_root).encode() in (copied / "binary.bin").read_bytes()

    def test_the_targets_pre_deploy_hooks_run_before_it_serves(
        self, world: SimpleNamespace, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order: list[str] = []
        original = BaseDeployer._activate_release

        def hooks(self: BaseDeployer, report: Any = None) -> None:
            order.append(f"hooks while current is {Path(os.readlink(root / 'current')).name}")

        def activate(self: BaseDeployer) -> None:
            order.append("activate")
            original(self)

        monkeypatch.setattr(BaseDeployer, "_run_pre_deploy_hooks", hooks)
        monkeypatch.setattr(BaseDeployer, "_activate_release", activate)

        promote(STAGING, PRODUCTION)

        assert order == [f"hooks while current is {OLD}", "activate"]

    def test_a_release_that_does_not_answer_is_put_back_and_removed(
        self, world: SimpleNamespace, root: Path, machine: SimpleNamespace
    ) -> None:
        machine.unhealthy = True

        with pytest.raises(DeploymentError, match="health check"):
            promote(STAGING, PRODUCTION)

        assert Path(os.readlink(root / "current")).name == OLD
        assert not (root / "releases" / RELEASE).exists()

    def test_a_named_release_is_promoted(self, world: SimpleNamespace, root: Path) -> None:
        older = make_tree(world.staging_root, "20261010-080000-9999999", server="older")
        assert older.is_dir()

        outcome = promote(STAGING, PRODUCTION, release_id="20261010-080000-9999999")

        assert Path(os.readlink(root / "current")).name == outcome.release
        assert (root / "releases" / outcome.release / "server.js").read_text() == "older"

    def test_going_back_past_a_schema_change_is_asked_first(
        self, world: SimpleNamespace, store: NoustStore
    ) -> None:
        first = store.record_deployment_start(PRODUCTION, "cli", git_commit="abc1234")
        later = store.record_deployment_start(PRODUCTION, "cli", git_commit="fff0000")
        store.annotate_deployment(later, git_commit="fff0000")
        store._get_connection().execute(
            "UPDATE deployments SET schema_changed = 1 WHERE id = ?", (later,)
        )
        store._get_connection().commit()
        assert first < later

        with pytest.raises(SchemaChangedError):
            promote(STAGING, PRODUCTION)
        promote(STAGING, PRODUCTION, schema_changed_ok=True)

    def test_a_warning_says_what_a_build_bakes_in(
        self, world: SimpleNamespace, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            promote_module, "read_app_env", lambda app: {"NEXT_PUBLIC_URL": "https://staging"}
        )

        plan = plan_promotion(STAGING, PRODUCTION)

        assert "NEXT_PUBLIC_URL" in plan.warnings[0]


class TestRefusals:
    def test_to_itself(self, world: SimpleNamespace) -> None:
        with pytest.raises(ValidationError, match="itself"):
            plan_promotion(STAGING, STAGING)

    def test_an_unknown_application(self, world: SimpleNamespace) -> None:
        with pytest.raises(ValidationError, match="not found"):
            plan_promotion(STAGING, "ghost.example.com")

    def test_another_project(self, world: SimpleNamespace, store: NoustStore) -> None:
        world.teams.create_project("default", "Other", slug="other")
        other = world.teams.get_project("default", "other")
        stage = world.teams.get_environment("default", "other", "production")
        assert other is not None and stage is not None
        world.teams.move_app(PRODUCTION, other.id, stage.id)

        with pytest.raises(ValidationError, match="same project"):
            plan_promotion(STAGING, PRODUCTION)

    def test_the_same_environment(self, world: SimpleNamespace) -> None:
        production = world.teams.get_environment("default", "default", "production")
        assert production is not None
        world.teams.move_app(STAGING, world.project.id, production.id)

        with pytest.raises(ValidationError, match="same environment"):
            plan_promotion(STAGING, PRODUCTION)

    def test_another_type_or_repository(self, world: SimpleNamespace, store: NoustStore) -> None:
        production = store.get_app(PRODUCTION)
        assert production is not None
        production.app_type = "python"
        store.update_app(production)
        with pytest.raises(ValidationError, match="python"):
            plan_promotion(STAGING, PRODUCTION)
        production.app_type = "nodejs"
        production.source = "https://github.com/other/repo.git"
        store.update_app(production)
        with pytest.raises(ValidationError, match="same repository"):
            plan_promotion(STAGING, PRODUCTION)

    def test_an_application_in_place(self, world: SimpleNamespace, store: NoustStore) -> None:
        production = store.get_app(PRODUCTION)
        assert production is not None
        production.layout = "inplace"
        store.update_app(production)

        with pytest.raises(ValidationError, match="release layout"):
            plan_promotion(STAGING, PRODUCTION)

    def test_a_release_that_does_not_exist_lists_the_ones_that_do(
        self, world: SimpleNamespace
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            plan_promotion(STAGING, PRODUCTION, "20990101-000000-0000000")

        assert RELEASE in (caught.value.details or "")

    def test_what_already_runs_there(self, world: SimpleNamespace, root: Path) -> None:
        promote(STAGING, PRODUCTION)

        with pytest.raises(ValidationError, match="already runs"):
            promote(STAGING, PRODUCTION)

    def test_a_release_the_target_already_has_on_disk(
        self, world: SimpleNamespace, root: Path
    ) -> None:
        shutil.copytree(world.release, root / "releases" / RELEASE, symlinks=True)

        with pytest.raises(DeploymentError, match="already has a release"):
            promote(STAGING, PRODUCTION)


class TestWhereToPromote:
    def test_the_siblings_in_other_environments(
        self, world: SimpleNamespace, store: NoustStore
    ) -> None:
        assert [app.domain for app in promotion_targets(STAGING)] == [PRODUCTION]
        assert [app.domain for app in promotion_targets(PRODUCTION)] == [STAGING]

    def test_a_preview_has_none_and_is_not_one(
        self, world: SimpleNamespace, store: NoustStore
    ) -> None:
        store.set_preview_parent(STAGING, PRODUCTION)

        assert promotion_targets(STAGING) == []
        assert promotion_targets(PRODUCTION) == []

    def test_an_application_of_another_repository_has_no_sibling(
        self, world: SimpleNamespace, store: NoustStore
    ) -> None:
        store.create_app(
            App(
                domain="alone.example.com",
                app_type="nodejs",
                source="https://github.com/other/repo.git",
                layout="releases",
                app_path="/x",
            )
        )

        assert promotion_targets("alone.example.com") == []
        assert "alone.example.com" not in [a.domain for a in promotion_targets(STAGING)]


def test_the_releases_of_the_source_are_not_touched(world: SimpleNamespace) -> None:
    before = sorted(r.id for r in ReleaseManager(world.staging_root).list())

    promote(STAGING, PRODUCTION)

    assert sorted(r.id for r in ReleaseManager(world.staging_root).list()) == before
