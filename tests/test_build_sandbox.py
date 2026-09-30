"""
Tests for unprivileged builds: who builds in the sandbox, as whom, seeing what.

The deployer is driven end to end over a real temporary tree with the machine
faked, as in test_release_pipeline.py, whose fakes these tests reuse; the
runner is the fake one, so every assertion is on the exact command a real
deploy would run. "Running as root" is the only thing pretended: the sandbox
exists to take root's privileges away, and a test runs as nobody special.
"""

# The pipeline's fixtures are imported rather than replicated, as in
# test_rebuild_commit.py; pytest resolves them by name.
# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core import paths
from noust.core.config import Config
from noust.core.exceptions import BuildError, ValidationError
from noust.core.logger import Logger
from noust.core.runner import FakeRunner, SandboxSpec
from noust.core.store import NoustStore
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers import sandbox_trial
from noust.deployers.helpers.sandbox import BuildPhase, SandboxState
from noust.deployers.nodejs import NodeJSDeployer
from tests.test_release_pipeline import (  # noqa: F401 - fixtures used by name
    DOMAIN,
    deploy_new,
    git,
    machine,
    node_tree,
    root,
    store,
    update,
    wire,
)


@pytest.fixture
def as_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """
    Pretend to be root, with a self-test that passes and caches in the test's directory.

    Returns:
        Where the cache root and the protected configuration directory are.
    """
    cache_root = tmp_path / "cache" / "build"
    config = tmp_path / "etc-noust"
    config.mkdir()
    monkeypatch.setattr(build_sandbox, "running_as_root", lambda: True)
    monkeypatch.setattr(paths, "BUILD_CACHE_DIR", cache_root)
    monkeypatch.setattr(paths, "config_dir", lambda: config)
    monkeypatch.setattr(
        build_sandbox,
        "self_test",
        lambda runner, fs=None: build_sandbox.SelfTest(passed=True, checks=(("ok", True, ""),)),
    )
    return SimpleNamespace(cache_root=cache_root, config=config)


def sandboxed(runner: FakeRunner, *argv: str) -> list[SandboxSpec]:
    """The specs a command ran in, in order."""
    return [spec for inner, spec in runner.sandboxed if inner[: len(argv)] == argv]


def index_of(runner: FakeRunner, predicate: Any) -> int:
    """The position of the first call that matches."""
    return next(i for i, call in enumerate(runner.calls) if predicate(call))


# ---------------------------------------------------------------------------
# A new application builds in the sandbox
# ---------------------------------------------------------------------------


def test_a_new_app_installs_and_builds_as_noust_build(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: Any
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))

    deploy_new(root, machine)

    [spec] = sandboxed(machine.runner, "npm", "ci")
    release = next((root / "releases").iterdir())
    assert spec.user == "noust-build"
    assert spec.writable_paths == (release, as_root.cache_root / "rel-example-com")
    assert spec.read_only_paths == (root / "shared",)
    assert spec.hidden_paths == (Config().apps_directory, as_root.cache_root)
    assert as_root.config in spec.inaccessible_paths
    assert spec.env_files == (root / "shared" / ".env",)
    assert spec.masked_files == (root / "shared" / ".env",)
    assert spec.network == "full"
    assert spec.working_dir == release
    wrapped = next(call for call in machine.runner.calls if call[-2:] == ("npm", "ci"))
    assert wrapped[0] == "systemd-run"
    assert "--property=User=noust-build" in wrapped
    assert build_sandbox.get_state(DOMAIN, store=store).mode == "on"


def test_the_build_account_is_created_without_a_home_or_a_shell(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: Any
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))

    deploy_new(root, machine)

    [useradd] = machine.runner.calls_to("useradd")
    assert useradd[:5] == ("useradd", "--system", "--user-group", "--no-create-home", "--home-dir")
    assert useradd[5] == "/nonexistent"
    assert useradd[6] == "--shell"
    assert useradd[7].endswith("nologin")
    assert useradd[-1] == "noust-build"


def test_the_release_is_handed_to_the_build_account_then_to_the_service(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: Any
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))

    deploy_new(root, machine)

    release = str(next((root / "releases").iterdir()))
    to_build = index_of(
        machine.runner, lambda c: c == ("chown", "-R", "noust-build:noust-build", release)
    )
    install = index_of(machine.runner, lambda c: c[-2:] == ("npm", "ci"))
    to_service = index_of(
        machine.runner, lambda c: c == ("chown", "-R", "www-data:www-data", release)
    )
    assert to_build < install < to_service


def test_the_build_sees_its_caches_and_not_this_process_s_secrets(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOUST_SECRET_SENTINEL", "x")
    machine.git.publish(node_tree(tmp_path / "v1"))

    deploy_new(root, machine)

    index = index_of(machine.runner, lambda c: c[-2:] == ("npm", "ci"))
    env = machine.runner.envs[index]
    assert env is not None
    cache = str(as_root.cache_root / "rel-example-com")
    assert env["HOME"] == cache
    assert env["npm_config_cache"] == f"{cache}/npm"
    assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    assert "NOUST_SECRET_SENTINEL" not in env


def test_the_sandbox_failing_its_self_test_stops_the_build_instead_of_running_as_root(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
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
    machine.git.publish(node_tree(tmp_path / "v1"))
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(
        DOMAIN, "https://github.com/example/app.git", ssl=False, app_path=root, layout="releases"
    )

    with pytest.raises(BuildError) as raised:
        deployer.deploy()

    assert "instead of running as root" in raised.value.message
    assert "FAILED: it cannot write /root" in (raised.value.details or "")
    assert f"noust app sandbox disable {DOMAIN}" in (raised.value.details or "")
    assert not machine.runner.ran("npm", "ci")
    assert not machine.runner.sandboxed


# ---------------------------------------------------------------------------
# Applications from before 3.1, and an explicit root build
# ---------------------------------------------------------------------------


def forget_regime(store: NoustStore) -> None:
    """Make the application look like one from before 3.1: no row."""
    with store._transaction() as cursor:
        cursor.execute("DELETE FROM build_sandbox")


def test_an_application_from_before_keeps_building_as_root_with_a_warning_and_a_clean_env(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    forget_regime(store)
    # A new lockfile, so the update installs instead of reusing the release's.
    machine.git.publish(node_tree(tmp_path / "v2", lockfile='{"lockfileVersion": 3, "v": 2}\n'))
    runner = machine.runner
    before = len(runner.calls)

    update(machine, monkeypatch)

    installs = [i for i, c in enumerate(runner.calls) if i >= before and c == ("npm", "ci")]
    assert installs, runner.calls[before:]
    assert all(runner.clean_envs[i] for i in installs)
    assert runner.sandboxes[installs[0]] is None
    app = store.get_app(DOMAIN)
    assert app is not None
    warning = build_sandbox.sandbox_warning(app)
    assert warning is not None
    assert "still builds as root" in warning
    assert f"noust app sandbox test {DOMAIN}" in warning


def test_an_explicit_root_build_names_who_decided_and_why(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: Any
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)

    state = build_sandbox.disable(DOMAIN, actor="alice", reason="private registry", store=store)

    app = store.get_app(DOMAIN)
    assert app is not None
    assert state.mode == "off"
    warning = build_sandbox.sandbox_warning(app, state)
    assert warning is not None
    assert "alice" in warning
    assert "private registry" in warning


def test_building_as_root_needs_a_reason(root: Path, store: NoustStore) -> None:
    with pytest.raises(ValidationError, match="reason"):
        build_sandbox.disable(DOMAIN, actor="alice", reason="  ")


# ---------------------------------------------------------------------------
# Enabling
# ---------------------------------------------------------------------------


def test_enabling_asks_for_a_passing_trial_unless_forced(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: Any
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    forget_regime(store)

    with pytest.raises(ValidationError, match="no passing trial"):
        build_sandbox.enable(DOMAIN, actor="alice", store=store)

    build_sandbox.record_trial(DOMAIN, passed=True, commit="a" * 40, detail=None, store=store)
    assert build_sandbox.enable(DOMAIN, actor="alice", store=store).enabled
    forget_regime(store)
    assert build_sandbox.enable(DOMAIN, actor="alice", force=True, store=store).enabled


def test_a_compose_stack_cannot_be_enabled(store: NoustStore) -> None:
    from noust.core.store import App

    with pytest.raises(ValidationError, match="do not run in the sandbox"):
        build_sandbox.refuse_unsupported(App(domain="c.example.com", app_type="docker-compose"))
    # A monorepo builds in the sandbox too (tests/test_monorepo_sandbox.py).
    build_sandbox.refuse_unsupported(App(domain="m.example.com", app_type="monorepo"))


def test_the_compose_exception_needs_a_reason_names_a_domain_and_can_be_revoked(
    store: NoustStore,
) -> None:
    with pytest.raises(ValidationError, match="reason"):
        build_sandbox.set_compose_exception("new.example.com", allowed=True, actor="alice")

    # Before the stack exists: a new stack has to be allowed before it deploys.
    allowed = build_sandbox.set_compose_exception(
        "new.example.com", allowed=True, actor="alice", reason="runs portainer", store=store
    )

    assert allowed is not None
    assert build_sandbox.get_compose_exception("new.example.com", store=store) == allowed
    assert (allowed.allowed_by, allowed.reason) == ("alice", "runs portainer")
    build_sandbox.set_compose_exception("new.example.com", allowed=False, actor="bob", store=store)
    assert build_sandbox.get_compose_exception("new.example.com", store=store) is None


# ---------------------------------------------------------------------------
# Phases: the strict profile, migrations
# ---------------------------------------------------------------------------


def primed(root: Path, *, network: str = "full", runner: FakeRunner | None = None) -> Any:
    """A deployer with its regime decided and its host prepared, as mid-deploy."""
    deployer = NodeJSDeployer(verbose=False, runner=runner or FakeRunner())
    deployer.configure(
        DOMAIN, "https://example.com/x.git", app_path=root, env_vars={"SECRET_KEY": "s3cret"}
    )
    deployer._layout = "releases"
    deployer._sandbox_regime = SandboxState(domain=DOMAIN, mode="on", network=network)
    deployer._sandbox_cache = root.parent / "cache"
    deployer._sandbox_tree = deployer.build_path
    return deployer


def test_the_strict_profile_installs_without_secrets_and_builds_without_a_network(
    tmp_path: Path, store: NoustStore
) -> None:
    runner = FakeRunner()
    deployer = primed(tmp_path / "apps" / "x", network="strict", runner=runner)

    deployer._run(["npm", "ci"], phase=BuildPhase.INSTALL)
    deployer._run(["npm", "run", "build"], phase=BuildPhase.BUILD)

    install, build = runner.sandboxes
    assert install is not None and build is not None
    assert install.network == "full"
    assert install.env_files == ()
    assert "SECRET_KEY" not in (runner.envs[0] or {})
    assert build.network == "none"
    assert (runner.envs[1] or {})["SECRET_KEY"] == "s3cret"


def test_the_full_profile_keeps_the_network_and_the_variables(
    tmp_path: Path, store: NoustStore
) -> None:
    runner = FakeRunner()
    deployer = primed(tmp_path / "apps" / "x", runner=runner)

    deployer._run(["npm", "ci"], phase=BuildPhase.INSTALL)

    [spec] = runner.sandboxes
    assert spec is not None
    assert spec.network == "full"
    assert (runner.envs[0] or {})["SECRET_KEY"] == "s3cret"


def test_a_migration_runs_as_the_application_not_as_the_build(
    tmp_path: Path, store: NoustStore
) -> None:
    runner = FakeRunner()
    deployer = primed(tmp_path / "apps" / "x", runner=runner)
    deployer.has_prisma = True
    (deployer.build_path / "prisma" / "migrations").mkdir(parents=True)

    deployer.run_prisma_migrate()

    [(inner, spec)] = runner.sandboxed
    assert "migrate" in inner
    assert spec.user == "www-data"
    assert spec.strict is False


def test_a_privileged_command_runs_as_this_process_with_its_environment(
    tmp_path: Path, store: NoustStore
) -> None:
    runner = FakeRunner()
    deployer = primed(tmp_path / "apps" / "x", runner=runner)

    deployer._run(["true"], phase=BuildPhase.PRIVILEGED)

    assert runner.sandboxes == [None]
    assert runner.clean_envs == [False]


def test_without_root_a_build_runs_directly_with_a_clean_environment(
    tmp_path: Path, store: NoustStore
) -> None:
    runner = FakeRunner()
    deployer = NodeJSDeployer(verbose=False, runner=runner)
    deployer.configure(DOMAIN, "src", app_path=tmp_path)

    deployer._run(["npm", "ci"])

    assert runner.calls == [("npm", "ci")]
    assert runner.clean_envs == [True]


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------


def test_another_applications_env_outside_the_apps_directory_is_inaccessible(
    tmp_path: Path, store: NoustStore
) -> None:
    from noust.core.store import App

    apps = tmp_path / "apps"
    store.create_app(App(domain="a.example.com", app_type="nodejs", app_path=str(apps / "a")))
    store.create_app(
        App(domain="b.example.com", app_type="nodejs", app_path=str(tmp_path / "srv" / "b"))
    )
    me = store.get_app("a.example.com")

    spec = build_sandbox.build_spec(
        app=me,
        app_name="a-example-com",
        phase=BuildPhase.BUILD,
        state=SandboxState(domain="a.example.com", mode="on"),
        user="noust-build",
        group="noust-build",
        build_path=apps / "a" / "releases" / "r1",
        apps_dir=apps,
        cache=tmp_path / "cache" / "a-example-com",
        env_file=None,
        shared=apps / "a" / "shared",
    )

    assert tmp_path / "srv" / "b" / ".env" in spec.inaccessible_paths


def test_an_application_under_home_keeps_home_visible(tmp_path: Path) -> None:
    spec = build_sandbox.build_spec(
        app=None,
        app_name="x",
        phase=BuildPhase.BUILD,
        state=SandboxState(domain="x.example.com", mode="on"),
        user="noust-build",
        group="noust-build",
        build_path=Path("/home/deploy/apps/x/releases/r1"),
        apps_dir=Path("/home/deploy/apps"),
        cache=Path("/var/cache/noust/build/x"),
        env_file=None,
        shared=None,
    )

    assert spec.protect_home is False


# ---------------------------------------------------------------------------
# The host: account and self-test
# ---------------------------------------------------------------------------


def test_an_existing_account_is_left_alone() -> None:
    runner = FakeRunner().script(["getent", "passwd", "noust-build"], stdout="noust-build:x:998")

    build_sandbox.ensure_build_account(runner)

    assert runner.calls_to("useradd") == []


def test_an_account_that_cannot_be_created_says_why_verbatim() -> None:
    runner = FakeRunner().script(
        ["useradd"], exit_code=1, stderr="useradd: cannot lock /etc/passwd"
    )

    with pytest.raises(BuildError) as raised:
        build_sandbox.ensure_build_account(runner)

    assert "cannot lock /etc/passwd" in (raised.value.details or "")


def test_an_account_created_meanwhile_is_fine() -> None:
    build_sandbox.ensure_build_account(FakeRunner().script(["useradd"], exit_code=9))


class SandboxMachine(FakeRunner):
    """
    Carries a sandboxed ``touch`` and ``cat`` out as the sandbox would, or would not.

    Attributes:
        holds: Whether the sandbox blocks what it must.
    """

    def __init__(self, holds: bool) -> None:
        super().__init__()
        self.holds = holds

    def run(self, argv: Any, **kwargs: Any) -> Any:
        result = super().run(argv, **kwargs)
        if kwargs.get("sandbox") is None:
            return result
        spec: SandboxSpec = kwargs["sandbox"]
        if argv[0] == "touch":
            for target in map(Path, argv[1:]):
                allowed = any(parent in target.parents for parent in spec.writable_paths)
                if allowed or not self.holds:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
            return result
        if argv[0] == "cat":
            if self.holds:
                return type(result)(argv=result.argv, exit_code=1, stderr="Permission denied")
            return type(result)(argv=result.argv, exit_code=0, stdout=Path(argv[1]).read_text())
        return result


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A host whose protected places are in the test's directory."""
    config = tmp_path / "etc-noust"
    config.mkdir()
    home = tmp_path / "root"
    home.mkdir()
    monkeypatch.setattr(paths, "BUILD_CACHE_DIR", tmp_path / "cache" / "build")
    monkeypatch.setattr(paths, "config_dir", lambda: config)
    monkeypatch.setattr(build_sandbox, "ROOT_HOME", home)
    build_sandbox.forget_self_test()
    yield SimpleNamespace(config=config, home=home)
    build_sandbox.forget_self_test()


def test_a_sandbox_that_holds_passes_its_self_test_and_leaves_nothing(
    tmp_path: Path, host: SimpleNamespace
) -> None:
    runner = SandboxMachine(holds=True)

    outcome = build_sandbox.self_test(runner)

    assert outcome.passed, outcome.detail
    assert not list(host.home.iterdir())
    assert not list(host.config.iterdir())
    assert not list((tmp_path / "cache" / "sandbox-canary").iterdir())
    touch, cat = runner.sandboxed[0][0], runner.sandboxed[1][0]
    assert touch[0] == "touch" and len(touch) == 4
    assert cat == ("cat", str(host.config / ".noust-sandbox-canary"))
    assert all(spec.user == "noust-build" for _inner, spec in runner.sandboxed)


def test_a_sandbox_that_does_not_hold_fails_and_says_which_check(
    tmp_path: Path, host: SimpleNamespace
) -> None:
    outcome = build_sandbox.self_test(SandboxMachine(holds=False))

    assert not outcome.passed
    assert "FAILED: it cannot write a world-writable directory" in outcome.detail
    assert "FAILED: it cannot write /root" in outcome.detail
    assert f"FAILED: it cannot read {host.config}" in outcome.detail
    assert not list(host.home.iterdir())


def test_a_sandbox_that_cannot_run_at_all_does_not_pass_for_one_that_blocks(
    tmp_path: Path, host: SimpleNamespace
) -> None:
    runner = FakeRunner().script(["systemd-run"], exit_code=1, stderr="Failed to connect to bus")

    outcome = build_sandbox.self_test(runner)

    assert not outcome.passed
    assert "FAILED: a sandboxed command runs as noust-build" in outcome.detail


def test_a_passing_self_test_is_not_repeated(tmp_path: Path, host: SimpleNamespace) -> None:
    runner = SandboxMachine(holds=True)

    build_sandbox.self_test(runner)
    calls = len(runner.calls)
    build_sandbox.self_test(runner)

    assert len(runner.calls) == calls


# ---------------------------------------------------------------------------
# The trial build
# ---------------------------------------------------------------------------


def test_a_trial_builds_the_active_commit_in_the_sandbox_and_activates_nothing(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    forget_regime(store)
    restarts = list(machine.services.restarts)
    deployments = len(store.list_deployments(DOMAIN))
    machine.git.resolve_commit = lambda repository, short: commit
    monkeypatch.setattr(
        "noust.deployers.registry.get_deployer",
        lambda app_type, verbose=False: wire(
            NodeJSDeployer(verbose=False, runner=machine.runner), machine
        ),
    )
    before = len(machine.runner.sandboxed)

    result = sandbox_trial.run_trial(DOMAIN, logger=Logger(verbose=False), store=store)

    assert result.passed, result.detail
    assert result.commit == commit
    [(inner, spec)] = [
        entry for entry in machine.runner.sandboxed[before:] if entry[0] == ("npm", "ci")
    ]
    assert spec.user == "noust-build"
    scratch = spec.working_dir
    assert scratch is not None
    assert scratch.parent == as_root.cache_root / "rel-example-com"
    assert not scratch.exists(), "the trial's tree is thrown away"
    assert machine.services.restarts == restarts
    assert len(store.list_deployments(DOMAIN)) == deployments
    state = build_sandbox.get_state(DOMAIN, store=store)
    assert state.mode == "legacy"
    assert state.test_passed is True
    assert state.tested_commit == commit


def test_a_failing_trial_is_recorded_with_the_builds_own_words(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    forget_regime(store)
    machine.git.resolve_commit = lambda repository, short: commit
    machine.runner.script(["npm", "ci"], exit_code=1, stderr="npm ERR! 401 Unauthorized")
    monkeypatch.setattr(
        "noust.deployers.registry.get_deployer",
        lambda app_type, verbose=False: wire(
            NodeJSDeployer(verbose=False, runner=machine.runner), machine
        ),
    )

    result = sandbox_trial.run_trial(DOMAIN, logger=Logger(verbose=False), store=store)

    assert not result.passed
    assert "401 Unauthorized" in (result.detail or "")
    state = build_sandbox.get_state(DOMAIN, store=store)
    assert state.test_passed is False
    with pytest.raises(ValidationError, match="no passing trial"):
        build_sandbox.enable(DOMAIN, actor="alice", store=store)


def test_a_trial_needs_root(root: Path, store: NoustStore, tmp_path: Path) -> None:
    from noust.core.store import App

    store.create_app(App(domain=DOMAIN, app_type="nodejs", app_path=str(root)))

    with pytest.raises(ValidationError, match="needs root"):
        sandbox_trial.run_trial(DOMAIN, store=store)


def test_the_regime_row_goes_with_its_application(
    tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace, as_root: Any
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)

    store.delete_app(DOMAIN)

    rows = store._get_connection().execute("SELECT COUNT(*) FROM build_sandbox").fetchone()[0]
    assert rows == 0
    assert os.path.isdir(root)


# ---------------------------------------------------------------------------
# A deadline or the memory limit says so
# ---------------------------------------------------------------------------


class Killed(FakeRunner):
    """A runner whose build was killed, as the sandbox reports it."""

    def __init__(self, how: str) -> None:
        super().__init__()
        self.how = how

    def stream(self, argv: Any, **kwargs: Any) -> Any:
        from dataclasses import replace

        from noust.core.runner import EXIT_TIMEOUT

        result = super().stream(argv, **kwargs)
        if self.how == "timeout":
            return replace(
                result,
                exit_code=EXIT_TIMEOUT,
                timed_out=True,
                stderr="The command ran longer than its 2700s deadline; its sandbox was stopped.",
            )
        return replace(
            result,
            exit_code=137,
            sandbox_result="oom-kill",
            stderr="The command was killed because it ran out of memory: it reached "
            "its memory limit (MemoryMax=2048M).",
        )


@pytest.mark.parametrize(
    ("how", "error", "words"),
    [
        ("timeout", BuildError, "did not finish within"),
        ("oom", build_sandbox.BuildError, "insufficient memory"),
    ],
)
def test_a_build_killed_by_a_limit_names_the_limit(
    tmp_path: Path, store: NoustStore, how: str, error: type, words: str
) -> None:
    from noust.core.exceptions import OutOfMemoryError

    deployer = primed(tmp_path / "apps" / "x", runner=Killed(how))
    deployer.package_manager = "npm"
    deployer.pre_build = lambda: True  # type: ignore[method-assign]
    deployer.get_build_command = lambda: ["npm", "run", "build"]  # type: ignore[method-assign]

    with pytest.raises(error) as raised:
        deployer.build()

    assert words in raised.value.message
    if how == "oom":
        assert isinstance(raised.value, OutOfMemoryError)
        assert "MemoryMax=2048M" in (raised.value.details or "")
    else:
        assert "deadline" in (raised.value.details or "")
