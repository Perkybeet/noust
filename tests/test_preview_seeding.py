# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A preview whose database starts as a copy of the application's (checklist B-32).

The dump, the load and the creation of the database are the database layer's, covered
there; here they are fakes. What is pinned: the copy is made before the preview's first
build, from the right database, never leaves a dump of production behind, and a failed
copy fails the deployment saying the database was kept.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.core.preview_store import DATA_CLONE
from noust.core.store import Database, NoustStore
from noust.managers import preview_env, previews
from noust.managers.database import seeding
from noust.managers.database.base import BackupInfo
from noust.managers.database.isolation import DatabaseWish
from tests.test_previews import (
    PARENT,
    FakeContext,
    clock,
    jobs,
    posted,
    store,
    timer,
)

FIXTURES = (clock, jobs, posted, store, timer)


class FakeService:
    """The database service: dumps to a file in a directory, and notes what was asked."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.dumps: list[tuple[str, str]] = []
        self.logger = SimpleNamespace(info=lambda _m: None)

    def dump(self, engine: str, database: str, **_k: Any) -> BackupInfo:
        self.dumps.append((engine, database))
        path = self.directory / f"{database}.dump"
        path.write_bytes(b"PGDMP" + b"x" * 100)
        return BackupInfo(path, database, engine, 105, datetime(2026, 10, 20), False)


class FakeImports:
    """Stands in for the one import: loads whatever it is given, or fails."""

    loaded: list[dict[str, Any]] = []
    fail: Exception | None = None

    def __init__(self, service: Any) -> None:
        self.service = service

    def run(self, engine: str, database: str, source: Path, **kw: Any) -> None:
        FakeImports.loaded.append(
            {"engine": engine, "database": database, "exists": source.exists(), **kw}
        )
        if FakeImports.fail is not None:
            raise FakeImports.fail


@pytest.fixture(autouse=True)
def imports(monkeypatch: pytest.MonkeyPatch) -> type[FakeImports]:
    FakeImports.loaded = []
    FakeImports.fail = None
    monkeypatch.setattr(seeding, "DatabaseImports", FakeImports)
    return FakeImports


class TestCopyingADatabase:
    def test_the_source_is_dumped_loaded_and_the_dump_deleted(
        self, tmp_path: Path, imports: type[FakeImports]
    ) -> None:
        service = FakeService(tmp_path)
        steps: list[str] = []

        outcome = seeding.copy_database(
            service,  # type: ignore[arg-type]
            source_engine="postgresql",
            source_database="shop",
            target_engine="postgresql",
            target_database="shop_pr7",
            on_step=steps.append,
        )

        assert service.dumps == [("postgresql", "shop")]
        assert len(imports.loaded) == 1
        loaded = imports.loaded[0]
        assert (loaded["engine"], loaded["database"]) == ("postgresql", "shop_pr7")
        # The dump exists while it is loaded, and nothing is copied twice for safety.
        assert loaded["exists"] is True and loaded["safety_backup"] is False
        assert (outcome.source, outcome.target, outcome.size) == (
            "postgresql/shop",
            "postgresql/shop_pr7",
            105,
        )
        assert list(tmp_path.glob("*.dump")) == []

    def test_a_failed_load_still_deletes_the_dump_and_raises(
        self, tmp_path: Path, imports: type[FakeImports]
    ) -> None:
        imports.fail = DatabaseBackupError("the dump did not load")

        with pytest.raises(DatabaseBackupError, match="did not load"):
            seeding.copy_database(
                FakeService(tmp_path),  # type: ignore[arg-type]
                source_engine="postgresql@pg16",
                source_database="shop",
                target_engine="postgresql@pg16",
                target_database="shop_pr7",
            )

        assert list(tmp_path.glob("*.dump")) == []


class TestThePreviewAsksForIt:
    def test_a_clone_names_the_database_to_start_from(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = store.get_app(PARENT)
        assert app is not None
        store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))
        previews.enable_previews(PARENT, "previews.example.com", data_mode=DATA_CLONE)
        monkeypatch.setattr(
            preview_env,
            "database_wish",
            lambda parent, sequence: DatabaseWish(
                "postgresql", "shop_pr7", "DATABASE_URL", False, "shop", None
            ),
        )
        seen: dict[str, Any] = {}
        import noust.web.jobs as jobs_module

        monkeypatch.setattr(
            jobs_module, "deploy_app_job", lambda **kwargs: seen.update(kwargs) or {}
        )
        monkeypatch.setattr(previews, "_reserve_port", lambda: 4000)
        parent = store.get_app(PARENT)
        assert parent is not None
        record = store.save_preview(
            previews.PreviewRecord(
                parent_domain=PARENT,
                domain="pr-7-shop-example-com.previews.example.com",
                number=7,
                branch="feature/x",
                provider="github",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        )
        settings = store.get_preview_settings(PARENT)
        assert settings is not None

        previews._deploy(parent, record, FakeContext(), settings)  # type: ignore[arg-type]

        assert seen["database"]["seed"] == {"engine": "postgresql", "database": "shop"}
        assert seen["database"]["name"] == "shop_pr7"

    def test_an_empty_one_asks_for_no_seed(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = store.get_app(PARENT)
        assert app is not None
        store.create_database(Database(app_id=app.id, name="shop", engine="postgresql"))
        previews.enable_previews(PARENT, "previews.example.com")
        monkeypatch.setattr(
            preview_env,
            "database_wish",
            lambda parent, sequence: DatabaseWish(
                "postgresql", "shop_pr7", "DATABASE_URL", False, "shop", "pg16"
            ),
        )
        seen: dict[str, Any] = {}
        import noust.web.jobs as jobs_module

        monkeypatch.setattr(
            jobs_module, "deploy_app_job", lambda **kwargs: seen.update(kwargs) or {}
        )
        monkeypatch.setattr(previews, "_reserve_port", lambda: 4000)
        parent = store.get_app(PARENT)
        settings = store.get_preview_settings(PARENT)
        assert parent is not None and settings is not None
        record = store.save_preview(
            previews.PreviewRecord(
                parent_domain=PARENT,
                domain="pr-7-shop-example-com.previews.example.com",
                number=7,
                branch="feature/x",
                provider="github",
                expires_at="2099-01-01T00:00:00+00:00",
            )
        )

        previews._deploy(parent, record, FakeContext(), settings)  # type: ignore[arg-type]

        assert "seed" not in seen["database"]
        assert seen["database"]["instance"] == "pg16"


class TestTheDeployJob:
    def run(
        self, monkeypatch: pytest.MonkeyPatch, *, fails: bool
    ) -> tuple[list[str], list[str], Exception | None]:
        from noust.core.exceptions import NoustError
        from noust.web.jobs import Job, JobContext, JobType, deploy_app_job

        order: list[str] = []
        kept: list[str] = []

        class Prepared:
            engine = "postgresql@pg16"
            database = "shop_pr7"
            values = {"DATABASE_URL": "postgres://x"}

            def configure_options(self, marks: Any) -> dict[str, Any]:
                return {"env_secret_marks": marks}

        class Databases:
            def __init__(self, logger: Any = None) -> None:
                pass

            def prepare_for_new_app(self, *a: Any, **k: Any) -> Prepared:
                order.append("prepare")
                return Prepared()

            def keep_after_failed_deploy(self, prepared: Any, error: Exception) -> None:
                kept.append(prepared.database)

            def link_new_app(self, prepared: Any) -> Any:
                return SimpleNamespace(to_dict=lambda: {})

        class Deployer:
            last_deployment_id = None

            def configure(self, **kwargs: Any) -> None:
                order.append("configure")

            def deploy(self) -> bool:
                order.append("deploy")
                return True

        def copy(service: Any, **kwargs: Any) -> None:
            order.append(f"copy {kwargs['source_engine']}/{kwargs['source_database']}")
            order.append(f"into {kwargs['target_engine']}/{kwargs['target_database']}")
            if fails:
                raise NoustError("the copy failed")

        monkeypatch.setattr("noust.deployers.get_deployer", lambda *a, **k: Deployer())
        monkeypatch.setattr("noust.managers.database.service.DatabaseService", Databases)
        monkeypatch.setattr(seeding, "copy_database", copy)

        job = Job(id="job-test", type=JobType.DEPLOY, name="deploy", description="")
        error: Exception | None = None
        try:
            deploy_app_job(
                "pr-7.example.com",
                "https://github.com/you/app",
                "nodejs",
                database={
                    "engine": "postgresql",
                    "name": "shop_pr7",
                    "env_var": "DATABASE_URL",
                    "extra_vars": False,
                    "seed": {"engine": "postgresql@pg16", "database": "shop"},
                },
                job_context=JobContext(job, lambda _job: None),
            )
        except NoustError as exc:
            error = exc
        return order, kept, error

    def test_the_copy_is_made_after_the_database_and_before_the_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order, kept, error = self.run(monkeypatch, fails=False)

        assert error is None
        assert order == [
            "prepare",
            "copy postgresql@pg16/shop",
            "into postgresql@pg16/shop_pr7",
            "configure",
            "deploy",
        ]
        assert kept == []

    def test_a_failed_copy_builds_nothing_and_says_the_database_was_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        order, kept, error = self.run(monkeypatch, fails=True)

        assert error is not None and "the copy failed" in str(error)
        assert "deploy" not in order and "configure" not in order
        assert kept == ["shop_pr7"]
