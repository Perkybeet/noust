"""
A monorepo's install and build run in the build sandbox, like every other application's.

Until 3.1.0's integration pass a monorepo (backlog 44) was the one application
type built on BaseDeployer's policy without its chokepoint: ``pnpm install``
ran every workspace's ``postinstall`` as root with Noust's whole environment.
It now goes through the same policy (:mod:`noust.deployers.helpers.sandbox`)
with the same activation rules: a monorepo created from now on builds in the
sandbox, one from before keeps building as root with a warning until it is
tested and enabled. A monorepo is always in place, so it builds as its units'
account, in its live tree, with the rest of the machine hidden.
"""

# The sandbox fixture is imported rather than replicated; pytest resolves it
# by name.
# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import BuildError
from noust.core.runner import FakeRunner, SandboxSpec, set_runner
from noust.core.store import App, NoustStore
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers import sandbox_trial
from noust.deployers.helpers.release_build import StagedRelease
from noust.deployers.monorepo import MonorepoDeployer
from noust.deployers.releases import ReleaseManager
from tests.test_build_sandbox import as_root  # noqa: F401 - fixture used by name

DOMAIN = "mono.example.com"
APP_NAME = "mono-example-com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoustStore]:
    """A store in the test directory, where the deployer and the sandbox policy look."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr("noust.deployers.monorepo.get_store", lambda: instance)
    monkeypatch.setattr(build_sandbox, "get_store", lambda: instance)
    monkeypatch.setattr(sandbox_trial, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


def workspace_tree(root: Path, scripts: dict[str, str] | None = None) -> Path:
    """A turbo/pnpm workspace with two deployable apps."""
    for name in ("web", "api"):
        (root / "apps" / name).mkdir(parents=True, exist_ok=True)
        (root / "apps" / name / "package.json").write_text(f'{{"name": "{name}"}}')
    body = {"name": "mono", "workspaces": ["apps/*"], "scripts": scripts or {}}
    import json

    (root / "package.json").write_text(json.dumps(body))
    (root / "pnpm-workspace.yaml").write_text("packages:\n  - apps/*\n")
    (root / "turbo.json").write_text("{}")
    return root


def registered(store: NoustStore, root: Path, *, sandboxed: bool) -> App:
    """A monorepo row; created from 3.1 when ``sandboxed``, from before otherwise."""
    app = store.create_app(App(domain=DOMAIN, app_type="monorepo", app_path=str(root)))
    if sandboxed:
        build_sandbox.adopt_new_app(DOMAIN, preview=False, store=store)
    return app


def deployer(root: Path, runner: FakeRunner) -> MonorepoDeployer:
    """A monorepo deployer over the tree."""
    instance = MonorepoDeployer(verbose=False, runner=runner)
    instance.configure(DOMAIN, str(root), app_path=root)
    return instance


def sandboxed(runner: FakeRunner, *argv: str) -> list[SandboxSpec]:
    """The specs a command ran in, in order."""
    return [spec for inner, spec in runner.sandboxed if inner[: len(argv)] == argv]


def index_of(runner: FakeRunner, predicate: Any) -> int:
    """The position of the first call that matches."""
    return next(i for i, call in enumerate(runner.calls) if predicate(call))


# ---------------------------------------------------------------------------
# A monorepo from 3.1 builds in the sandbox
# ---------------------------------------------------------------------------


def test_a_new_monorepo_is_put_in_the_sandbox_before_its_first_build(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_tree(tmp_path / "mono")
    seen: list[str] = []

    def steps(self: MonorepoDeployer, app: App, total_steps: int) -> bool:
        seen.append(build_sandbox.get_state(DOMAIN, store=store).mode)
        return True

    monkeypatch.setattr(MonorepoDeployer, "_deploy_steps", steps)
    monkeypatch.setattr(MonorepoDeployer, "_pre_flight_check", lambda self: None)

    deployer(tmp_path / "fresh", runner)._deploy()

    assert seen == ["on"]


def test_a_redeployed_monorepo_keeps_the_regime_it_has(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = workspace_tree(tmp_path / "mono")
    registered(store, root, sandboxed=False)
    monkeypatch.setattr(MonorepoDeployer, "_deploy_steps", lambda self, app, total: True)
    monkeypatch.setattr(MonorepoDeployer, "_pre_flight_check", lambda self: None)

    instance = deployer(root, runner)
    instance.replace_existing = True
    instance._deploy()

    assert build_sandbox.get_state(DOMAIN, store=store).mode == "legacy"


def test_a_sandboxed_monorepo_installs_and_builds_as_its_units_account(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOUST_SECRET_SENTINEL", "x")
    root = workspace_tree(tmp_path / "mono")
    registered(store, root, sandboxed=True)

    deployer(root, runner).update()

    [install] = sandboxed(runner, "pnpm", "install")
    [build] = sandboxed(runner, "pnpm", "build")
    cache = as_root.cache_root / APP_NAME
    for spec in (install, build):
        assert spec.user == Config().service_user
        assert spec.strict
        assert spec.writable_paths == (root, cache)
        assert spec.read_only_paths == ()
        assert spec.hidden_paths == (Config().apps_directory, as_root.cache_root)
        assert as_root.config in spec.inaccessible_paths
        assert spec.network == "full"
        assert spec.working_dir == root
    env = runner.envs[
        index_of(runner, lambda c: c[-3:] == ("pnpm", "install", "--frozen-lockfile"))
    ]
    assert env is not None
    assert env["HOME"] == str(cache)
    assert env["npm_config_store_dir"] == f"{cache}/pnpm-store"
    assert "NOUST_SECRET_SENTINEL" not in env
    wrapped = next(call for call in runner.calls if call[-2:] == ("pnpm", "build"))
    assert wrapped[0] == "systemd-run"


def test_the_tree_is_handed_to_the_units_account_before_the_install(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, as_root: Any
) -> None:
    root = workspace_tree(tmp_path / "mono")
    registered(store, root, sandboxed=True)

    deployer(root, runner).update()

    account = f"{Config().service_user}:{Config().service_group}"
    handed = index_of(runner, lambda c: c == ("chown", "-R", account, str(root)))
    install = index_of(runner, lambda c: c[-3:] == ("pnpm", "install", "--frozen-lockfile"))
    assert handed < install


def test_a_monorepo_migration_runs_as_the_application_not_as_the_build(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, as_root: Any
) -> None:
    root = workspace_tree(
        tmp_path / "mono", scripts={"db:generate": "prisma generate", "db:migrate": "m"}
    )
    registered(store, root, sandboxed=True)

    deployer(root, runner).update()

    [generate] = sandboxed(runner, "pnpm", "db:generate")
    [migrate] = sandboxed(runner, "pnpm", "db:migrate")
    assert generate.strict
    assert not migrate.strict
    assert migrate.user == Config().service_user
    assert migrate.name == f"{APP_NAME}-release"


def test_a_sandbox_that_does_not_hold_stops_a_monorepo_build_instead_of_running_as_root(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        build_sandbox,
        "self_test",
        lambda runner, fs=None: build_sandbox.SelfTest(
            passed=False, checks=(("it cannot write /root (ProtectHome)", False, "created"),)
        ),
    )
    root = workspace_tree(tmp_path / "mono")
    registered(store, root, sandboxed=True)

    with pytest.raises(BuildError) as raised:
        deployer(root, runner).update()

    assert "instead of running as root" in raised.value.message
    assert not runner.ran("pnpm", "install")
    assert not runner.sandboxed


# ---------------------------------------------------------------------------
# A monorepo from before keeps building as root, warned
# ---------------------------------------------------------------------------


def test_a_monorepo_from_before_builds_as_root_with_its_environment_and_a_warning(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, as_root: Any
) -> None:
    root = workspace_tree(tmp_path / "mono")
    app = registered(store, root, sandboxed=False)

    deployer(root, runner).update()

    install = index_of(runner, lambda c: c == ("pnpm", "install", "--frozen-lockfile"))
    build = index_of(runner, lambda c: c == ("pnpm", "build"))
    assert runner.sandboxes[install] is None and runner.sandboxes[build] is None
    # As before 3.1: only the sandbox starts from a clean environment.
    assert not runner.clean_envs[install] and not runner.clean_envs[build]
    warning = build_sandbox.sandbox_warning(app)
    assert warning is not None
    assert "still builds as root" in warning
    assert f"noust app sandbox test {DOMAIN}" in warning


def test_a_sandboxed_monorepo_has_no_warning(tmp_path: Path, store: NoustStore) -> None:
    app = registered(store, workspace_tree(tmp_path / "mono"), sandboxed=True)

    assert build_sandbox.sandbox_warning(app) is None
    assert DOMAIN not in build_sandbox.sandbox_warnings(store)


def test_a_monorepo_can_be_tested_and_enabled_and_a_compose_stack_still_cannot(
    store: NoustStore,
) -> None:
    from noust.core.exceptions import ValidationError

    build_sandbox.refuse_unsupported(App(domain=DOMAIN, app_type="monorepo"))
    with pytest.raises(ValidationError, match="do not run in the sandbox"):
        build_sandbox.refuse_unsupported(App(domain="c.example.com", app_type="docker-compose"))


# ---------------------------------------------------------------------------
# The trial build
# ---------------------------------------------------------------------------


def test_a_monorepo_trial_builds_a_scratch_tree_in_the_sandbox_and_activates_nothing(
    tmp_path: Path, store: NoustStore, runner: FakeRunner, as_root: Any
) -> None:
    root = workspace_tree(tmp_path / "mono")
    registered(store, root, sandboxed=False)
    scratch = workspace_tree(as_root.cache_root / APP_NAME / "trial-1")

    deployer(root, runner).sandbox_trial(
        StagedRelease(path=scratch, commit="a" * 40, manager=ReleaseManager(root))
    )

    [install] = sandboxed(runner, "pnpm", "install")
    [build] = sandboxed(runner, "pnpm", "build")
    assert install.working_dir == scratch and build.working_dir == scratch
    assert root not in install.writable_paths
    assert not runner.ran("systemctl", "restart")
    assert not sandboxed(runner, "pnpm", "db:migrate")
    assert build_sandbox.get_state(DOMAIN, store=store).mode == "legacy"


def test_run_trial_accepts_a_monorepo(
    tmp_path: Path,
    store: NoustStore,
    runner: FakeRunner,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = workspace_tree(tmp_path / "mono")
    registered(store, root, sandboxed=False)
    monkeypatch.setattr(sandbox_trial, "current_commit", lambda app, sm: ("b" * 40, root))
    set_runner(runner)
    try:
        result = sandbox_trial.run_trial(DOMAIN, store=store)
    finally:
        set_runner(None)

    assert result.passed, result.detail
    assert sandboxed(runner, "pnpm", "install")
    assert build_sandbox.get_state(DOMAIN, store=store).test_passed is True
