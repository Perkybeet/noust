# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cloning an application (checklist B-80): the same application on another domain,
with its settings, variables, tags and previews, and a decision about its database.

The deploy is the normal one and is covered elsewhere; here it is a fake that
creates the application's row, and what is pinned is what the clone decides to
copy and to leave out.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import app_build_store, app_mount_store
from noust.core.app_tags import AppTags
from noust.core.exceptions import DomainConflictError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import App, Database, DomainKind, NoustStore
from noust.core.teams import TeamStore
from noust.core.variables.model import SERVER
from noust.core.variables.store import VariableStore
from noust.deployers import app_clone, app_export
from noust.deployers.app_clone import apply_clone, plan_clone
from noust.deployers.app_export import CreateSpec
from noust.deployers.helpers import app_env as app_env_module
from noust.deployers.helpers.app_env import write_app_env
from noust.deployers.helpers.hooks import operator_hooks, set_operator_hooks
from noust.managers.app_mounts import MountManager
from noust.web.auth import STATE_DIR_ENV
from tests.test_app_mounts import OwnerFs

SOURCE = "shop.example.com"
CLONE = "shop-copy.example.com"
GIT = "https://github.com/acme/shop.git"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore, runner: FakeRunner
) -> App:
    root = tmp_path / "apps" / "shop-example-com"
    root.mkdir(parents=True)
    monkeypatch.setattr(
        app_env_module,
        "Config",
        lambda: SimpleNamespace(
            apps_directory=tmp_path / "apps", service_user="www-data", service_group="www-data"
        ),
    )
    store.create_app(
        App(
            domain=SOURCE,
            app_type="nodejs",
            source=GIT,
            branch="main",
            port=3001,
            app_path=str(root),
            layout="releases",
        )
    )
    store.add_domain(SOURCE, f"alias.{SOURCE}", DomainKind.ALIAS.value) if hasattr(
        store, "add_domain"
    ) else None
    found = store.get_app(SOURCE)
    assert found is not None
    write_app_env(found, {"API_URL": "https://shop.example.com", "FLAG": "1"})
    return found


def deployed(store: NoustStore, specs: list[CreateSpec]):
    """A deploy that creates the row the real one would."""

    def deploy(spec: CreateSpec) -> None:
        specs.append(spec)
        store.create_app(
            App(
                domain=spec.domain,
                app_type=spec.app_type,
                source=spec.source,
                branch=spec.branch,
                port=spec.port,
                app_path=f"/tmp/{spec.domain}",
                layout="releases",
            )
        )

    return deploy


class TestPlanning:
    def test_the_clone_is_the_same_application_on_another_domain(self, app: App) -> None:
        plan = plan_clone(SOURCE, CLONE)

        create = plan.plan.create
        assert (create.domain, create.source, create.branch, create.app_type) == (
            CLONE,
            GIT,
            "main",
            "nodejs",
        )
        assert create.env_vars == {"API_URL": "https://shop.example.com", "FLAG": "1"}
        assert create.include_www is False

    def test_it_does_not_take_the_names_the_original_answers_on(
        self, app: App, store: NoustStore
    ) -> None:
        plan = plan_clone(SOURCE, CLONE)

        assert plan.plan.document["domains"] == {"aliases": [], "redirects": []}
        assert any("names it answers on" in line for line in plan.omitted)

    def test_variables_made_of_references_are_copied_as_the_references(
        self, app: App, store: NoustStore
    ) -> None:
        VariableStore(store).set(SERVER, 0, "REGION", "eu")
        write_app_env(
            app,
            {
                "API_URL": "https://shop.example.com",
                "SITE": "{{app.URL}}",
                "R": "{{server.REGION}}",
            },
        )

        plan = plan_clone(SOURCE, CLONE)

        assert plan.plan.create.env_vars["SITE"] == "{{app.URL}}"
        assert plan.plan.create.env_vars["R"] == "{{server.REGION}}"
        assert plan.plan.create.env_vars["API_URL"] == "https://shop.example.com"

    def test_the_audience_of_a_variable_is_kept(self, app: App) -> None:
        write_app_env(app, {"NPM_TOKEN": "npm_abcdefghijkl"}, audience={"NPM_TOKEN": "build"})

        plan = plan_clone(SOURCE, CLONE)

        assert plan.plan.create.env_audience == {"NPM_TOKEN": "build"}
        assert plan.plan.create.env_vars["NPM_TOKEN"] == "npm_abcdefghijkl"

    def test_a_domain_that_is_taken_is_refused(self, app: App, store: NoustStore) -> None:
        store.create_app(App(domain=CLONE, app_path="/x"))

        with pytest.raises(DomainConflictError):
            plan_clone(SOURCE, CLONE)

    def test_a_preview_or_an_application_in_place_is_not_cloned(
        self, app: App, store: NoustStore
    ) -> None:
        store.set_preview_parent(SOURCE, "other.example.com")
        with pytest.raises(ValidationError, match="preview"):
            plan_clone(SOURCE, CLONE)
        store.set_preview_parent(SOURCE, None)
        found = store.get_app(SOURCE)
        assert found is not None
        found.layout = "inplace"
        store.update_app(found)
        with pytest.raises(ValidationError, match="release layout"):
            plan_clone(SOURCE, CLONE)

    def test_something_that_does_not_exist(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError, match="not found"):
            plan_clone("ghost.example.com", CLONE)

    def test_the_server_parts_are_left_out_for_a_caller_that_does_not_see_the_server(
        self, app: App
    ) -> None:
        plan = plan_clone(SOURCE, CLONE, server_parts=False)

        assert plan.plan.document["cron"] == [] and plan.plan.document["backup"] is None


class TestTheDatabase:
    @pytest.fixture(autouse=True)
    def with_a_database(self, app: App, store: NoustStore) -> None:
        store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))
        write_app_env(
            app,
            {"DATABASE_URL": "postgres://u:p@localhost:5432/shop", "API_URL": "https://x"},
        )

    def test_there_is_no_default(self) -> None:
        with pytest.raises(ValidationError, match="--database empty"):
            plan_clone(SOURCE, CLONE)

    def test_a_choice_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Unknown way"):
            plan_clone(SOURCE, CLONE, database="mirror")

    def test_empty_gives_the_clone_one_of_its_own_and_none_of_the_originals_variables(
        self,
    ) -> None:
        plan = plan_clone(SOURCE, CLONE, database="empty")

        create = plan.plan.create
        assert "DATABASE_URL" not in create.env_vars and create.env_vars["API_URL"] == "https://x"
        assert create.database is not None
        assert (create.database["engine"], create.database["name"]) == ("postgresql", "shop_clone")
        assert create.database["env_var"] == "DATABASE_URL"
        assert plan.database == "empty"
        assert any("a database of its own" in line for line in plan.extras)

    def test_share_keeps_the_variables_and_makes_nothing(self) -> None:
        plan = plan_clone(SOURCE, CLONE, database="share")

        assert plan.plan.create.database is None
        assert "DATABASE_URL" in plan.plan.create.env_vars
        assert any("shared" in line for line in plan.extras)


class TestApplying:
    def test_the_clone_is_created_and_what_the_export_lacks_is_copied(
        self, app: App, store: NoustStore
    ) -> None:
        AppTags(store).set(SOURCE, ["prod", "eu"])
        app_build_store.set_document(SOURCE, "build:\n  start: node server.js\n", updated_by="ana")
        set_operator_hooks(
            SOURCE, "hooks:\n  pre_deploy:\n    - run: npm run migrate\n", actor="ana", store=store
        )
        specs: list[CreateSpec] = []

        report = apply_clone(
            plan_clone(SOURCE, CLONE), deploy=deployed(store, specs), logger=Logger()
        )

        assert specs[0].domain == CLONE
        assert AppTags(store).of(CLONE) == ["eu", "prod"]
        assert app_build_store.get_document(CLONE) == "build:\n  start: node server.js\n"
        assert operator_hooks(CLONE, store=store) is not None
        assert not report.not_applied

    def test_how_previews_are_made_is_copied_with_the_variables_they_are_given(
        self, app: App, store: NoustStore
    ) -> None:
        from noust.managers import previews

        previews.sync_sweep_timer = lambda **_: True  # type: ignore[assignment]
        previews.enable_previews(SOURCE, "previews.example.com", allow_env=["API_URL"])
        found = store.get_app(SOURCE)
        assert found is not None and found.id is not None
        from noust.core.variables.model import PREVIEW

        VariableStore(store).set(PREVIEW, found.id, "SANDBOX", "yes")
        specs: list[CreateSpec] = []

        apply_clone(plan_clone(SOURCE, CLONE), deploy=deployed(store, specs), logger=Logger())

        from noust.core.preview_store import PreviewStore

        assert PreviewStore(store).options(CLONE).allow_env == ("API_URL",)
        clone = store.get_app(CLONE)
        assert clone is not None and clone.id is not None
        assert [v.name for v in VariableStore(store).list(PREVIEW, clone.id)] == ["SANDBOX"]

    def test_a_deploy_that_fails_stops_everything(self, app: App, store: NoustStore) -> None:
        def deploy(spec: CreateSpec) -> None:
            raise ValidationError("the build failed")

        AppTags(store).set(SOURCE, ["prod"])

        with pytest.raises(ValidationError):
            apply_clone(plan_clone(SOURCE, CLONE), deploy=deploy, logger=Logger())

        assert store.get_app(CLONE) is None


class TestMounts:
    """The files Noust keeps for the original are the clone's too (checklist B-80)."""

    @pytest.fixture
    def mounts(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> MountManager:
        manager = MountManager(store=store, fs=OwnerFs())
        monkeypatch.setattr(app_export, "MountManager", lambda log=None: manager)
        return manager

    def deploy(self, store: NoustStore, tmp_path: Path, specs: list[CreateSpec]):
        def deploy(spec: CreateSpec) -> None:
            specs.append(spec)
            root = tmp_path / "apps" / spec.domain.replace(".", "-")
            (root / "shared").mkdir(parents=True)
            store.create_app(
                App(
                    domain=spec.domain,
                    app_type=spec.app_type,
                    source=spec.source,
                    port=spec.port,
                    app_path=str(root),
                    layout="releases",
                )
            )

        return deploy

    def test_a_mounted_file_comes_with_its_text_and_is_there_for_the_first_start(
        self, app: App, store: NoustStore, mounts: MountManager, tmp_path: Path
    ) -> None:
        text = '{"token": "from-the-original"}\n'
        mounts.put_file(SOURCE, "config/production.json", text, mode=0o640)
        specs: list[CreateSpec] = []

        plan = plan_clone(SOURCE, CLONE)
        report = apply_clone(plan, deploy=self.deploy(store, tmp_path, specs), logger=Logger())

        assert specs[0].seed_files == {"config/production.json": text}
        (record,) = app_mount_store.list_mounts(CLONE)
        assert (record.kind, record.target, record.mode) == (
            "file",
            "config/production.json",
            0o640,
        )
        assert mounts.read_file(CLONE, "config/production.json") == text
        assert not [step for step in report.not_applied if "mount" in step.part]

    def test_the_plan_says_which_files_it_copies(
        self, app: App, store: NoustStore, mounts: MountManager
    ) -> None:
        mounts.put_file(SOURCE, "certs/ca.pem", "pem\n")

        plan = plan_clone(SOURCE, CLONE)

        assert any(line.startswith("file mount certs/ca.pem") for line in plan.plan.steps)

    def test_the_original_keeps_its_own(
        self, app: App, store: NoustStore, mounts: MountManager, tmp_path: Path
    ) -> None:
        mounts.put_file(SOURCE, "a.conf", "one\n")

        apply_clone(
            plan_clone(SOURCE, CLONE),
            deploy=self.deploy(store, tmp_path, []),
            logger=Logger(),
        )
        mounts.put_file(CLONE, "a.conf", "two\n")

        assert mounts.read_file(SOURCE, "a.conf") == "one\n"


class NoCron:
    """No cron jobs: the real manager asks systemd."""

    def list_jobs(self) -> list[dict[str, Any]]:
        return []


class TestTheCommandLine:
    @pytest.fixture(autouse=True)
    def state(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setattr(app_export, "CronManager", NoCron)
        yield
        set_runner(None)

    @pytest.fixture(autouse=True)
    def state_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        directory = tmp_path / "state"
        directory.mkdir(mode=0o700, exist_ok=True)
        monkeypatch.setenv(STATE_DIR_ENV, str(directory))

    def run(self, *args: str, code: int = 0) -> str:
        result = CliRunner().invoke(root_cli, list(args))
        assert result.exit_code == code, f"{result.output} {result.exception}"
        return f"{result.output}{result.exception or ''}"

    def test_a_rehearsal_shows_the_plan_and_creates_nothing(
        self, app: App, store: NoustStore, runner: FakeRunner
    ) -> None:
        AppTags(store).set(SOURCE, ["prod"])

        out = self.run("app", "clone", SOURCE, CLONE, "--dry-run")

        assert "tags prod" in out and "not copied" in out and "Rehearsal" in out
        assert store.get_app(CLONE) is None

    def test_cloning_deploys_through_the_console_job_and_puts_the_clone_where_the_original_is(
        self, app: App, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        teams = TeamStore(store)
        teams.create_team("Acme", slug="acme")
        project = teams.create_project("acme", "Shop", slug="shop")
        stage = teams.create_environment("acme", "shop", "Staging", slug="staging")
        teams.move_app(SOURCE, project.id, stage.id)
        specs: list[CreateSpec] = []
        monkeypatch.setattr(app_clone, "deploy_through_job", lambda context: deployed(store, specs))
        monkeypatch.setattr(
            "noust.cli.commands.app_promote.deploy_through_job",
            lambda context: deployed(store, specs),
        )

        out = self.run("app", "clone", SOURCE, CLONE, "--yes")

        clone = store.get_app(CLONE)
        assert clone is not None and "is a clone of" in out
        assert (clone.project_id, clone.environment_id) == (project.id, stage.id)

    def test_json(self, app: App, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
        specs: list[CreateSpec] = []
        monkeypatch.setattr(
            "noust.cli.commands.app_promote.deploy_through_job",
            lambda context: deployed(store, specs),
        )

        result = CliRunner().invoke(root_cli, ["--json", "app", "clone", SOURCE, CLONE])
        assert result.exit_code == 0, f"{result.output} {result.exception}"
        body = json.loads(result.stdout)

        assert body["domain"] == CLONE

    def test_a_database_with_no_choice_is_refused(self, app: App, store: NoustStore) -> None:
        store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))
        write_app_env(app, {"DATABASE_URL": "postgres://u:p@localhost:5432/shop"})

        out = self.run("app", "clone", SOURCE, CLONE, "--yes", code=1)

        assert "--database" in out
