"""
Tests for the sandbox by default (Noust 3.2, spec section 4.1).

An application from before 3.1 that still builds as root tries the sandbox on
its next update, with the same trial ``noust app sandbox test`` runs, before
the update builds: a passing trial turns the sandbox on and the update builds
in it; a failing one leaves the update building as root, exactly as before,
and says so (the deployment's log, the warning the application page, ``noust
health`` and ``noust ens check`` show, and a notification). An operator's
``sandbox disable --reason`` is respected, and a server where the sandbox does
not hold behaves exactly as it did.

The deployer runs end to end over a real temporary tree with the machine
faked, reusing the fixtures of test_release_pipeline.py and test_build_sandbox.py.
"""

# The pipeline's fixtures are imported rather than replicated; pytest resolves
# them by name.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import State
from noust.core.store import App, NoustStore
from noust.deployers.helpers import sandbox as build_sandbox
from noust.deployers.helpers import sandbox_trial
from noust.deployers.nodejs import NodeJSDeployer
from tests.test_build_sandbox import as_root, forget_regime  # noqa: F401
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

NEW_LOCKFILE = '{"lockfileVersion": 3, "v": 2}\n'


@pytest.fixture
def notices(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every notification composed, built over a fixed context."""
    sent: list[Any] = []
    ctx = NotificationContext(server="web-1", public_url="https://console.example.com")

    def capture(build: Any) -> None:
        sent.append(build(ctx))

    monkeypatch.setattr("noust.core.notifier.notify_composed", capture)
    return sent


def legacy_app(tmp_path: Path, root: Path, store: NoustStore, machine: SimpleNamespace) -> str:
    """
    Deploy an application on releases and make it look like one from before 3.1.

    Returns:
        The commit the next update builds.
    """
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    forget_regime(store)
    return machine.git.publish(node_tree(tmp_path / "v2", lockfile=NEW_LOCKFILE))


def fail_in_the_sandbox(runner: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make an install fail in the sandbox and pass as root: a build that needs root."""
    original = runner.stream

    def stream(argv: Any, **kwargs: Any) -> Any:
        result = original(argv, **kwargs)
        if kwargs.get("sandbox") is not None and tuple(argv) == ("npm", "ci"):
            return type(result)(
                argv=result.argv, exit_code=1, stderr="npm ERR! 401 Unauthorized: registry.local"
            )
        return result

    monkeypatch.setattr(runner, "stream", stream)


def update_installs(runner: Any, since: int) -> list[int]:
    """The positions of the update's own installs (not the trial's), after ``since``."""
    return [
        i
        for i, call in enumerate(runner.calls)
        if i >= since
        and call[-2:] == ("npm", "ci")
        and (runner.sandboxes[i] is None or "trial-" not in str(runner.sandboxes[i].working_dir))
    ]


# ---------------------------------------------------------------------------
# The trial passes: the sandbox is turned on and the update builds in it
# ---------------------------------------------------------------------------


def test_a_passing_trial_turns_the_sandbox_on_and_the_update_builds_in_it(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    notices: list[Any],
) -> None:
    commit = legacy_app(tmp_path, root, store, machine)
    before = len(machine.runner.calls)

    update(machine, monkeypatch)

    state = build_sandbox.get_state(DOMAIN, store=store)
    assert state.mode == "on"
    assert state.changed_by == "noust"
    assert state.test_passed is True
    assert state.tested_commit == commit, "the trial builds what the update builds"
    assert state.auto_trial_at is not None
    trial = [
        spec
        for inner, spec in machine.runner.sandboxed
        if inner == ("npm", "ci") and "trial-" in str(spec.working_dir)
    ]
    assert len(trial) == 1
    [install] = update_installs(machine.runner, before)
    spec = machine.runner.sandboxes[install]
    assert spec is not None and spec.user == "noust-build"
    assert spec.working_dir is not None and spec.working_dir.parent == root / "releases"
    assert notices == []
    app = store.get_app(DOMAIN)
    assert app is not None
    assert build_sandbox.sandbox_warning(app) is None


# ---------------------------------------------------------------------------
# The trial fails: the update builds as root, as before, and says so
# ---------------------------------------------------------------------------


def test_a_failing_trial_builds_as_before_records_why_and_tells_the_operator(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    notices: list[Any],
) -> None:
    commit = legacy_app(tmp_path, root, store, machine)
    fail_in_the_sandbox(machine.runner, monkeypatch)
    before = len(machine.runner.calls)

    outcome = update(machine, monkeypatch)

    assert outcome.active
    [install] = update_installs(machine.runner, before)
    assert machine.runner.sandboxes[install] is None, "built as root, exactly as before"
    state = build_sandbox.get_state(DOMAIN, store=store)
    assert state.mode == "legacy"
    assert state.test_passed is False
    assert state.tested_commit == commit
    assert "401 Unauthorized" in (state.test_detail or "")
    assert state.auto_trial_at is not None
    app = store.get_app(DOMAIN)
    assert app is not None
    warning = build_sandbox.sandbox_warning(app)
    assert warning is not None
    assert "trial" in warning and "failed" in warning
    assert f"noust app sandbox test {DOMAIN}" in warning
    assert f"noust app sandbox disable {DOMAIN}" in warning
    [notice] = notices
    assert notice.kind == "deploy_failed"
    assert notice.code == "sandbox.trial_failed"
    assert notice.state is State.WARNING
    assert notice.domain == DOMAIN
    assert notice.excerpt is not None
    assert any("401 Unauthorized" in line for line in notice.excerpt.lines)


def test_after_a_failed_trial_the_next_update_does_not_try_again(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    notices: list[Any],
) -> None:
    legacy_app(tmp_path, root, store, machine)
    fail_in_the_sandbox(machine.runner, monkeypatch)
    update(machine, monkeypatch)
    machine.git.publish(node_tree(tmp_path / "v3", lockfile='{"lockfileVersion": 3, "v": 3}\n'))
    before = len(machine.runner.sandboxed)

    update(machine, monkeypatch)

    assert machine.runner.sandboxed[before:] == []
    assert len(notices) == 1


# ---------------------------------------------------------------------------
# What is left alone
# ---------------------------------------------------------------------------


def test_an_operators_decision_to_build_as_root_is_respected(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    notices: list[Any],
) -> None:
    legacy_app(tmp_path, root, store, machine)
    build_sandbox.disable(DOMAIN, actor="alice", reason="private registry", store=store)
    before = len(machine.runner.calls)
    sandboxed = len(machine.runner.sandboxed)

    update(machine, monkeypatch)

    assert machine.runner.sandboxed[sandboxed:] == []
    [install] = update_installs(machine.runner, before)
    assert machine.runner.sandboxes[install] is None
    state = build_sandbox.get_state(DOMAIN, store=store)
    assert (state.mode, state.reason, state.tested_at) == ("off", "private registry", None)
    assert notices == []


def test_where_the_sandbox_does_not_hold_the_update_is_exactly_as_before(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    notices: list[Any],
) -> None:
    legacy_app(tmp_path, root, store, machine)
    monkeypatch.setattr(
        build_sandbox,
        "self_test",
        lambda runner, fs=None: build_sandbox.SelfTest(
            passed=False, checks=(("it cannot write /root (ProtectHome)", False, "created"),)
        ),
    )
    before = len(machine.runner.calls)
    sandboxed = len(machine.runner.sandboxed)

    outcome = update(machine, monkeypatch)

    assert outcome.active
    assert machine.runner.sandboxed[sandboxed:] == []
    [install] = update_installs(machine.runner, before)
    assert machine.runner.sandboxes[install] is None
    state = build_sandbox.get_state(DOMAIN, store=store)
    assert (state.mode, state.tested_at, state.auto_trial_at) == ("legacy", None, None)
    assert notices == []


def test_a_noust_that_is_not_root_tries_nothing(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    notices: list[Any],
) -> None:
    legacy_app(tmp_path, root, store, machine)
    monkeypatch.setattr(build_sandbox, "running_as_root", lambda: False)
    sandboxed = len(machine.runner.sandboxed)
    accounts = len(machine.runner.calls_to("useradd"))

    update(machine, monkeypatch)

    assert machine.runner.sandboxed[sandboxed:] == []
    assert len(machine.runner.calls_to("useradd")) == accounts
    assert build_sandbox.get_state(DOMAIN, store=store).auto_trial_at is None


def test_an_application_already_in_the_sandbox_is_not_tried_again(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine.git.publish(node_tree(tmp_path / "v1"))
    deploy_new(root, machine)
    machine.git.publish(node_tree(tmp_path / "v2", lockfile=NEW_LOCKFILE))

    update(machine, monkeypatch)

    assert not [
        spec for _inner, spec in machine.runner.sandboxed if "trial-" in str(spec.working_dir)
    ]
    assert build_sandbox.get_state(DOMAIN, store=store).auto_trial_at is None


def test_a_trial_that_cannot_run_leaves_the_update_as_before(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
    notices: list[Any],
) -> None:
    """A source that is not git has no commit to try: nothing is recorded or sent."""
    legacy_app(tmp_path, root, store, machine)

    def no_commit(*args: Any, **kwargs: Any) -> Any:
        from noust.core.exceptions import ValidationError

        raise ValidationError("rel.example.com has no active release built from git")

    monkeypatch.setattr(sandbox_trial, "current_commit", no_commit)
    before = len(machine.runner.calls)

    outcome = update(machine, monkeypatch)

    assert outcome.active
    [install] = update_installs(machine.runner, before)
    assert machine.runner.sandboxes[install] is None
    state = build_sandbox.get_state(DOMAIN, store=store)
    assert (state.mode, state.tested_at, state.auto_trial_at) == ("legacy", None, None)
    assert notices == []


# ---------------------------------------------------------------------------
# In place: the trial copies the tree the update builds
# ---------------------------------------------------------------------------


def test_an_in_place_app_tries_its_tree_and_then_builds_in_the_sandbox(
    tmp_path: Path,
    root: Path,
    store: NoustStore,
    machine: SimpleNamespace,
    as_root: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.deployers.helpers.layout import INPLACE

    node_tree(root)
    (root / ".git").mkdir()
    store.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://example.com/repo.git",
            port=3000,
            app_path=str(root),
            layout=INPLACE,
        )
    )
    forget_regime(store)
    commit = "c" * 40
    machine.git.get_repo_info = lambda path: {"is_git": True, "commit": commit[:7]}
    machine.git.resolve_commit = lambda repository, short: commit
    copied: list[Path] = []

    def export_worktree(repository: Path, destination: Path) -> list[str]:
        copied.append(repository)
        for name in ("package.json", "package-lock.json", "server.js"):
            (destination / name).write_bytes((repository / name).read_bytes())
        return []

    machine.git.export_worktree = export_worktree
    deployer = wire(NodeJSDeployer(verbose=False, runner=machine.runner), machine)
    deployer.configure(DOMAIN, str(root), port=3000, app_path=root)

    deployer.update()

    assert copied == [root]
    state = build_sandbox.get_state(DOMAIN, store=store)
    assert (state.mode, state.tested_commit) == ("on", commit)
    live = [spec for inner, spec in machine.runner.sandboxed if spec.working_dir == root]
    assert live, "the update's own build ran in the sandbox, in the served tree"
    assert all(spec.user == "www-data" for spec in live)


def test_a_compose_stack_is_never_tried(store: NoustStore, as_root: Any) -> None:
    store.create_app(App(domain=DOMAIN, app_type="docker-compose", app_path="/srv/stack"))
    forget_regime(store)
    logger = SimpleNamespace(
        substep=lambda *a, **k: None,
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        success=lambda *a, **k: None,
    )

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nothing to try")

    outcome = sandbox_trial.trial_before_update(
        DOMAIN,
        store=store,
        logger=logger,  # type: ignore[arg-type]
        runner=SimpleNamespace(run=refuse),  # type: ignore[arg-type]
        commit=None,
        make_deployer=refuse,
    )

    assert outcome is None
