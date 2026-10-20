# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Building with, without and emptying the cache (checklist A-46).

A release pipeline test drives the real thing over a temporary tree: an update
normally reuses the serving release's dependencies; one asked to build without
the cache installs again, and so does an application whose build block says
``cache: false``. The cache itself is a directory the account that builds owns,
emptied without following what the build left in it.
"""

# The pipeline's fixtures are imported rather than replicated; pytest resolves
# them by name.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import paths
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import App, NoustStore
from noust.deployers.helpers import build_cache
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers.build_config import BuildConfig, refuse_unsupported
from noust.deployers.helpers.project_file import load_project_file, parse_build_document
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    active_id,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
)


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the build caches live in these tests."""
    base = tmp_path / "var-cache-build"
    monkeypatch.setattr(paths, "BUILD_CACHE_DIR", base)
    return base


def fill(directory: Path) -> None:
    """Leave what a package manager would: files, a directory, and a link out."""
    (directory / "npm" / "_cacache").mkdir(parents=True)
    (directory / "npm" / "_cacache" / "blob").write_bytes(b"x" * 2048)
    (directory / "pnpm-store").mkdir()
    (directory / "pnpm-store" / "index").write_bytes(b"y" * 1024)
    (directory / "stray.txt").write_text("z")


class TestTheCacheDirectory:
    def test_it_is_measured_without_following_links(self, cache_root: Path, tmp_path: Path) -> None:
        cache = cache_root / "shop-example-com"
        cache.mkdir(parents=True)
        fill(cache)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "big").write_bytes(b"q" * 100_000)
        (cache / "escape").symlink_to(outside)

        assert build_sandbox.cache_size("shop-example-com") == 2048 + 1024 + 1

    def test_emptying_it_keeps_the_directory_and_never_touches_what_a_link_names(
        self, cache_root: Path, tmp_path: Path
    ) -> None:
        cache = cache_root / "shop-example-com"
        cache.mkdir(parents=True)
        fill(cache)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "precious").write_text("keep")
        (cache / "escape").symlink_to(outside)

        freed = build_sandbox.clear_cache_dir("shop-example-com")

        assert freed == 2048 + 1024 + 1
        assert cache.is_dir() and list(cache.iterdir()) == []
        assert (outside / "precious").read_text() == "keep"

    def test_an_application_that_never_built_has_nothing_to_empty(self, cache_root: Path) -> None:
        assert build_sandbox.clear_cache_dir("never-built") == 0
        assert build_sandbox.cache_size("never-built") == 0

    def test_a_cache_that_is_itself_a_link_is_left_alone(
        self, cache_root: Path, tmp_path: Path
    ) -> None:
        cache_root.mkdir(parents=True)
        target = tmp_path / "elsewhere"
        target.mkdir()
        (target / "file").write_text("keep")
        (cache_root / "linked").symlink_to(target)

        assert build_sandbox.clear_cache_dir("linked") == 0
        assert (target / "file").read_text() == "keep"


class TestTheBlockKey:
    def test_cache_false_is_read_from_the_repository_and_written_back(self, tmp_path: Path) -> None:
        (tmp_path / "noust.yaml").write_text("build:\n  cache: false\n")

        build = load_project_file(tmp_path).build

        assert build.cache is False
        assert build.declared_keys == ("cache",)
        assert build.to_dict()["cache"] is False
        assert "cache: false" in build.to_document()

    def test_it_must_be_true_or_false(self) -> None:
        with pytest.raises(ValidationError) as caught:
            parse_build_document("build:\n  cache: sometimes\n", source="operator")
        assert caught.value.field == "build.cache"

    def test_every_type_that_is_built_may_say_it(self) -> None:
        refuse_unsupported(BuildConfig(cache=False), "nodejs", {"start"}, set())

    def test_a_stack_may_say_it_because_compose_builds_without_the_cache_too(self) -> None:
        refuse_unsupported(BuildConfig(cache=False), "docker-compose", None, set())

    def test_a_type_with_no_build_block_still_refuses_it(self) -> None:
        with pytest.raises(ValidationError):
            refuse_unsupported(BuildConfig(cache=False), "monorepo", None, set())


class TestAnUpdate:
    def deploy_and_publish_v2(
        self, tmp_path: Path, root: Path, machine: SimpleNamespace
    ) -> tuple[str, Any]:
        machine.git.publish(node_tree(tmp_path / "v1"))
        deploy_new(root, machine)
        first = active_id(root)
        machine.git.publish(node_tree(tmp_path / "v2", server="// v2\n"))
        return first, None

    def test_it_reuses_the_installed_dependencies_by_default(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        cache_root: Path,
    ) -> None:
        self.deploy_and_publish_v2(tmp_path, root, machine)

        update(machine, monkeypatch)

        assert len(machine.runner.cwd_of("npm", "ci")) == 1

    def test_without_the_cache_it_installs_again(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        cache_root: Path,
    ) -> None:
        self.deploy_and_publish_v2(tmp_path, root, machine)

        update(machine, monkeypatch, no_cache=True)

        assert len(machine.runner.cwd_of("npm", "ci")) == 2
        assert not machine.runner.ran("cp")

    def test_and_the_cache_it_would_have_used_is_emptied_first(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        cache_root: Path,
    ) -> None:
        self.deploy_and_publish_v2(tmp_path, root, machine)
        cache = cache_root / "rel-example-com"
        cache.mkdir(parents=True)
        fill(cache)

        update(machine, monkeypatch, no_cache=True)

        assert [child.name for child in cache.iterdir()] == []

    def test_an_ordinary_update_leaves_the_cache_alone(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        cache_root: Path,
    ) -> None:
        self.deploy_and_publish_v2(tmp_path, root, machine)
        cache = cache_root / "rel-example-com"
        cache.mkdir(parents=True)
        fill(cache)

        update(machine, monkeypatch)

        assert (cache / "npm" / "_cacache" / "blob").is_file()

    def test_a_block_that_says_cache_false_makes_every_build_from_scratch(
        self,
        tmp_path: Path,
        root: Path,
        store: NoustStore,
        machine: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        cache_root: Path,
    ) -> None:
        from noust.deployers.helpers.build_config import set_operator_build

        self.deploy_and_publish_v2(tmp_path, root, machine)
        set_operator_build(DOMAIN, "build:\n  cache: false\n", actor="test")

        update(machine, monkeypatch)

        assert len(machine.runner.cwd_of("npm", "ci")) == 2


class TestTheSurfaces:
    @pytest.fixture
    def app(self, store: NoustStore, tmp_path: Path) -> str:
        store.create_app(
            App(
                domain="shop.example.com",
                app_type="nodejs",
                source="x",
                app_path=str(tmp_path / "app"),
            )
        )
        return "shop.example.com"

    def test_describe_and_clear(self, app: str, store: NoustStore, cache_root: Path) -> None:
        cache = cache_root / "shop-example-com"
        cache.mkdir(parents=True)
        fill(cache)

        shown = build_cache.describe(app, store=store)
        assert shown.exists and shown.size_bytes == 2048 + 1024 + 1

        cleared = build_cache.clear(app, store=store)
        assert cleared.size_bytes == 2048 + 1024 + 1
        assert build_cache.describe(app, store=store).size_bytes == 0

    def test_an_unknown_application_is_an_error(self, store: NoustStore, cache_root: Path) -> None:
        with pytest.raises(NoustError):
            build_cache.describe("nobody.example.com", store=store)

    def test_the_commands(self, app: str, store: NoustStore, cache_root: Path) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli

        cache = cache_root / "shop-example-com"
        cache.mkdir(parents=True)
        fill(cache)

        shown = CliRunner().invoke(cli, ["app", "cache", "show", app, "--json"])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["size_bytes"] == 2048 + 1024 + 1

        dry = CliRunner().invoke(cli, ["--dry-run", "app", "cache", "clear", app])
        assert dry.exit_code == 0 and "Would empty" in dry.output
        assert (cache / "stray.txt").exists()
        # A rehearsal installs its own runner and filesystem for the process.
        from noust.core.fs import set_fs
        from noust.core.runner import set_runner

        set_fs(None)
        set_runner(None)

        cleared = CliRunner().invoke(cli, ["app", "cache", "clear", app, "--json"])
        assert cleared.exit_code == 0, cleared.output
        assert not (cache / "stray.txt").exists()
        assert os.path.isdir(cache)

    def test_the_update_command_has_the_flag(self) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli

        result = CliRunner().invoke(cli, ["update", "--help"])
        assert "--no-cache" in result.output

    def test_the_update_job_asks_for_it_and_never_refuses_it_as_nothing_new(
        self, app: str, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import HTTPException

        from noust.web.api import jobs as jobs_api

        created: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                created.append(kwargs)
                return SimpleNamespace(id="j1")

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Manager())
        monkeypatch.setattr(jobs_api, "_queued", lambda message, job: message)
        monkeypatch.setattr(
            jobs_api,
            "check_upstream",
            lambda domain, **_k: SimpleNamespace(has_new_commits=False, summary="nothing new"),
        )
        session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}

        with pytest.raises(HTTPException) as refused:
            jobs_api.create_update_job(jobs_api.UpdateRequest(domain=app), session)
        assert refused.value.status_code == 409

        jobs_api.create_update_job(jobs_api.UpdateRequest(domain=app, no_cache=True), session)

        assert created[-1]["kwargs"]["no_cache"] is True
        assert created[-1]["merge"] == {"no_cache": True}
        assert created[-1]["metadata"]["no_cache"] is True

    def test_the_job_hands_it_to_the_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.web import jobs as web_jobs

        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            web_jobs,
            "run_update",
            lambda domain, **kwargs: seen.update(kwargs) or {"ok": True},
        )

        web_jobs.update_app_job("shop.example.com", no_cache=True)

        assert seen["no_cache"] is True
