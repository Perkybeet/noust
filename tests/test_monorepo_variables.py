# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A monorepo has the variables a process application has (checklist B-43, B-46, B-48).

Each workspace is told where it answers and which commit and release it runs, in a file beside it
that its unit loads; a variable only the build is given reaches the install and the build and no
file; one only the workspaces are given is taken out of the build; and a reference or a rotated
secret is resolved again before the build and reaches the workspaces' own files, the ones their
units read. Over the harness of ``test_monorepo_releases.py``: the real deployer and store, with
processes and the fetch faked.
"""

# The monorepo harness is imported rather than replicated.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.core.store import MonorepoWorkspace, NoustStore
from noust.core.variables.model import SERVER
from noust.core.variables.store import VariableStore
from noust.deployers.helpers.app_env import write_app_env
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.sandbox import BuildPhase
from noust.deployers.monorepo import MonorepoDeployer
from noust.validators.environment import EnvironmentValidationError
from tests.test_monorepo_releases import (  # noqa: F401  (pytest resolves fixtures by name)
    APP,
    COMMIT,
    DOMAIN,
    Probe,
    Units,
    as_root,
    deployer,
    no_waiting,
    probe,
    released_monorepo,
    staging,
    store,
)


def read(path: Path) -> dict[str, str]:
    return EnvManager().read_env_file(path)


def test_an_update_tells_each_workspace_where_it_answers_and_what_it_runs(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, probe: Probe, staging
) -> None:
    root, _app = released_monorepo(tmp_path, store)

    deployer(root, runner, Units()).update()

    new = staging[0]
    web = read(new.path / "apps" / "web" / ".noust-env")
    api = read(new.path / "apps" / "api" / ".noust-env")
    assert web["NOUST_COMMIT"] == COMMIT[:7] == api["NOUST_COMMIT"]
    assert web["NOUST_RELEASE"] == new.id == api["NOUST_RELEASE"]
    # Each answers on a name of its own, and is told that one.
    assert web["NOUST_DOMAIN"].endswith(f".{DOMAIN}")
    assert web["NOUST_DOMAIN"] != api["NOUST_DOMAIN"]
    assert web["NOUST_URL"].endswith(f"://{web['NOUST_DOMAIN']}")


def test_an_older_release_keeps_the_commit_it_was_built_from(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, probe: Probe, staging
) -> None:
    root, _app = released_monorepo(tmp_path, store)
    deployer(root, runner, Units()).update()
    first = staging[0]

    deployer(root, runner, Units()).update()

    assert read(first.path / "apps" / "web" / ".noust-env")["NOUST_RELEASE"] == first.id
    assert read(staging[1].path / "apps" / "web" / ".noust-env")["NOUST_RELEASE"] == staging[1].id


class TestAVariableOnlyTheBuildIsGiven:
    def test_the_install_and_the_build_get_it_and_no_file_does(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, probe: Probe, staging
    ) -> None:
        root, app = released_monorepo(tmp_path, store)
        write_app_env(
            app,
            {"DATABASE_URL": "postgresql://x", "NPM_TOKEN": "tok-123"},
            audience={"NPM_TOKEN": "build"},
        )

        deployer(root, runner, Units()).update()

        steps = [
            i
            for i, call in enumerate(runner.calls)
            if call[:2] in (("pnpm", "install"), ("pnpm", "build"))
        ]
        assert steps
        for index in steps:
            assert (runner.envs[index] or {}).get("NPM_TOKEN") == "tok-123"
        assert "NPM_TOKEN" not in read(root / "shared" / ".env")
        for name in ("web", "api"):
            assert "NPM_TOKEN" not in read(staging[0].path / "apps" / name / ".env.production")
            assert "NPM_TOKEN" not in read(root / "current" / "apps" / name / ".env.production")

    def test_a_command_that_is_not_the_build_does_not_get_it(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner
    ) -> None:
        root, app = released_monorepo(tmp_path, store)
        write_app_env(
            app,
            {"DATABASE_URL": "postgresql://x", "NPM_TOKEN": "tok-123"},
            audience={"NPM_TOKEN": "build"},
        )

        instance = deployer(root, runner, Units())
        instance._run(["pnpm", "prisma", "migrate", "deploy"], phase=BuildPhase.RELEASE)

        assert "NPM_TOKEN" not in (runner.envs[-1] or {})


class TestAVariableOnlyTheWorkspacesAreGiven:
    def test_the_build_does_not_see_it_and_the_files_keep_it(
        self, tmp_path: Path, runner: FakeRunner, store: NoustStore
    ) -> None:
        root = tmp_path / "apps" / APP
        root.mkdir(parents=True)
        instance = MonorepoDeployer(runner=runner)
        instance.configure(
            DOMAIN,
            "https://example.com/mono.git",
            app_path=root,
            env_vars={"SENTRY_DSN": "https://k@sentry", "KEPT": "1"},
            env_audience={"SENTRY_DSN": "runtime"},
        )

        instance.workspaces = [
            MonorepoWorkspace(name="web", path="apps/web", subdomain="www", port=3001)
        ]
        (root / "apps" / "web").mkdir(parents=True)
        instance._configure_environment()

        instance._run(["pnpm", "build"], phase=BuildPhase.BUILD)

        assert "SENTRY_DSN" not in (runner.envs[-1] or {})
        assert (runner.envs[-1] or {})["KEPT"] == "1"
        assert read(root / "apps" / "web" / ".env.production")["SENTRY_DSN"] == "https://k@sentry"


class TestReferences:
    def test_a_rotated_value_reaches_the_workspaces_files_of_the_next_release(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, probe: Probe, staging
    ) -> None:
        root, app = released_monorepo(tmp_path, store)
        variables = VariableStore(store)
        variables.set(SERVER, 0, "TOKEN", "v1")
        write_app_env(app, {"DATABASE_URL": "postgresql://x", "API_TOKEN": "{{server.TOKEN}}"})
        # The edit reached the files the units of the serving release read.
        assert read(root / "current" / "apps" / "web" / ".env.production")["API_TOKEN"] == "v1"
        variables.set(SERVER, 0, "TOKEN", "v2")

        deployer(root, runner, Units()).update()

        for name in ("web", "api"):
            assert read(staging[0].path / "apps" / name / ".env.production")["API_TOKEN"] == "v2"
            assert read(root / "current" / "apps" / name / ".env.production")["API_TOKEN"] == "v2"

    def test_a_reference_that_no_longer_resolves_stops_the_update_before_it_builds(
        self, tmp_path: Path, store: NoustStore, runner: FakeRunner, probe: Probe, staging
    ) -> None:
        root, app = released_monorepo(tmp_path, store)
        variables = VariableStore(store)
        variables.set(SERVER, 0, "TOKEN", "v1")
        write_app_env(app, {"DATABASE_URL": "postgresql://x", "API_TOKEN": "{{server.TOKEN}}"})
        variables.delete(SERVER, 0, "TOKEN")

        with pytest.raises(Exception, match="TOKEN"):
            deployer(root, runner, Units()).update()

        assert not any(call[:2] == ("pnpm", "build") for call in runner.calls)
        assert not any(call[:2] == ("pnpm", "install") for call in runner.calls)

    def test_a_name_noust_sets_itself_is_refused_at_creation(
        self, tmp_path: Path, runner: FakeRunner, store: NoustStore
    ) -> None:
        instance = MonorepoDeployer(runner=runner)
        instance.configure(
            DOMAIN,
            "https://example.com/mono.git",
            app_path=tmp_path / "apps" / APP,
            env_vars={"NOUST_URL": "https://elsewhere.example"},
        )

        with pytest.raises(EnvironmentValidationError):
            instance._configure_environment()
