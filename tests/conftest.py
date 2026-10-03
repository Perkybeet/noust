"""
Shared test fixtures.

The important thing in this file is :func:`forbid_real_subprocess`. It is
autouse, so every test in the suite runs with real process execution disabled.
Any code path that shells out without going through an injected
:class:`~noust.core.runner.CommandRunner` fails loudly instead of silently
touching the developer's machine.

Tests that genuinely need to spawn a process, such as the runner's own tests,
opt out with ``@pytest.mark.allow_subprocess``.
"""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from noust.core.fs import set_fs
from noust.core.runner import FakeRunner, set_runner


class RealSubprocessAttempted(AssertionError):
    """Raised when a test tries to execute a real process."""


def pytest_configure(config: pytest.Config) -> None:
    """
    Register the markers this suite uses.

    Args:
        config: The pytest configuration object.
    """
    config.addinivalue_line(
        "markers",
        "allow_subprocess: permit this test to execute real processes",
    )
    config.addinivalue_line(
        "markers",
        "allow_sockets: permit this test to open real network connections",
    )
    config.addinivalue_line(
        "markers",
        "real_ownership: let RealFileSystem.set_owner really chown (to this account only)",
    )


@pytest.fixture(autouse=True)
def forbid_real_subprocess(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """
    Make real process execution fail for the duration of a test.

    Args:
        request: The pytest request, used to honour the opt-out marker.
        monkeypatch: Patching helper, scoped to the test.
    """
    if request.node.get_closest_marker("allow_subprocess"):
        return

    def _blocked(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        raise RealSubprocessAttempted(
            f"This test tried to execute a real process: {argv!r}. "
            "Route the call through a CommandRunner and inject a FakeRunner, "
            "or mark the test with @pytest.mark.allow_subprocess."
        )

    for name in ("run", "Popen", "call", "check_call", "check_output", "getoutput"):
        monkeypatch.setattr(subprocess, name, _blocked, raising=False)


@dataclass
class OwnershipChange:
    """
    One hand-over :meth:`~noust.core.fs.RealFileSystem.set_owner` was asked for.

    Attributes:
        path: The entry.
        user: Account it was given to.
        group: Group it was given to.
        mode: Mode it was given.
    """

    path: Path
    user: str
    group: str
    mode: int


@pytest.fixture(autouse=True)
def ownership_changes(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> list[OwnershipChange]:
    """
    Record hand-overs instead of making them, keeping their other effects.

    A chown to another account needs root, which the suite never is, and the
    accounts a test names (``redis``, ``www-data``) need not exist here. So
    the owner is recorded rather than changed, while a symbolic link is still
    refused and the mode is still applied without following one: what the
    callers rely on keeps happening for real.

    Args:
        request: The pytest request, used to honour the opt-out marker.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The hand-overs, in order.
    """
    from noust.core import fs as fs_module

    changes: list[OwnershipChange] = []
    if request.node.get_closest_marker("real_ownership"):
        return changes

    def record(_self: object, path: Path, *, user: str, group: str, mode: int) -> None:
        if not os.path.lexists(path):
            # A file the FakeRunner was asked to produce (a ``cp``, a
            # ``gzip -dc``) and so never did: there is nothing to change.
            changes.append(OwnershipChange(path, user, group, mode))
            return
        if stat.S_ISLNK(path.lstat().st_mode):
            raise OSError(errno.ELOOP, "refusing to change the owner through a link", str(path))
        fs_module._chmod_entry(path, mode & 0o777)
        changes.append(OwnershipChange(path, user, group, mode))

    monkeypatch.setattr(fs_module.RealFileSystem, "set_owner", record)
    return changes


class PortProbe:
    """
    Stands in for a connection attempt against a port.

    Attributes:
        closed: Ports that refuse connections. Everything else answers, which
            keeps "systemd says active" meaning "running" for the tests that
            are not about the probe.
        taken: Ports something else already holds, so the panel cannot bind
            them. Empty by default: a test machine is not required to have
            8080 free for the suite to pass.
        asked: Every port that was checked, in order.
    """

    def __init__(self) -> None:
        self.closed: set[int] = set()
        self.taken: set[int] = set()
        self.asked: list[int] = []

    def in_use(self, host: str, port: int) -> bool:
        """
        Answer whether the panel's address is already bound.

        Args:
            host: Ignored.
            port: The port asked about.

        Returns:
            True when the test declared this port taken.
        """
        self.asked.append(port)
        return port in self.taken

    def __call__(self, port: int, host: str = "127.0.0.1", timeout: float = 0.0) -> bool:
        """
        Answer whether a port accepts connections.

        Args:
            port: The port asked about.
            host: Ignored; recorded by the caller's signature only.
            timeout: Ignored.

        Returns:
            True unless the test declared this port closed.
        """
        self.asked.append(port)
        return port not in self.closed


@pytest.fixture(autouse=True)
def ports(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> PortProbe | None:
    """
    Stop the application state probe from opening real connections.

    :func:`~noust.core.app_state.resolve_state` asks the port whether anything
    answers, because a systemd unit can be active while the application behind
    it is refusing every request. In a test that would reach the developer's
    own machine and give a different answer depending on what happens to be
    listening, so it is replaced here.

    Args:
        request: The pytest request, used to honour the opt-out marker.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The probe, so a test can declare which ports refuse connections.
    """
    if request.node.get_closest_marker("allow_sockets"):
        return None

    probe = PortProbe()
    monkeypatch.setattr("noust.core.app_state.port_answers", probe)
    monkeypatch.setattr("noust.cli.commands.web._port_in_use", probe.in_use)
    return probe


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """
    Give every test a home directory of its own, empty and outside ``tmp_path``.

    The observation store, the metrics database, the per-user store, cache and
    state directories all default to ``Path.home()`` when they are asked. With
    one shared home a test run wrote into the developer's real
    ``~/.local/share/noust``, and under ``pytest -n`` the workers opened the
    same ``metrics.db`` and saw each other's rows. It is a sibling of
    ``tmp_path`` rather than a child so tests that list ``tmp_path`` still
    find only what they wrote; the ``sandbox`` fixture still points ``HOME``
    at ``tmp_path`` for the tests that want that.

    Args:
        tmp_path_factory: Session temporary directory factory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The home directory.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    for variable in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(variable, raising=False)
    # Resolved when the class was defined, so it still names the real home:
    # a package-index refresh under test forgets the check by removing it.
    from noust.core.update_checker import UpdateChecker

    monkeypatch.setattr(
        UpdateChecker, "CACHE_FILE", home / ".cache" / "noust" / "version_check.json"
    )
    return home


@pytest.fixture(autouse=True)
def isolated_store_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Point the store's default locations inside the test's own directory.

    ``USER_DB_PATH`` is computed from ``Path.home()`` when the module is
    imported, so redirecting ``HOME`` inside a test is too late: a test that
    reached ``get_store()`` without a store fixture opened the developer's real
    ``~/.local/share/wasm/wasm.db`` - and, on a machine that also ran a newer
    WASM, failed on its schema. The system location is redirected too, so a
    test run as root can never touch ``/var/lib/wasm``. Tests that pin their
    own paths still do; their monkeypatch runs after this one.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
    """
    from noust.core import store as store_module

    monkeypatch.setattr(store_module, "USER_DB_PATH", tmp_path / "user-store" / "noust.db")
    monkeypatch.setattr(store_module, "DEFAULT_DB_PATH", tmp_path / "system-store" / "noust.db")
    # WASM's locations, read before the migration moves them, are redirected
    # just as much: a test must never find the developer's real 2.x store.
    monkeypatch.setattr(
        store_module, "LEGACY_USER_DB_PATH", tmp_path / "legacy-user-store" / "wasm.db"
    )
    monkeypatch.setattr(
        store_module, "LEGACY_DB_PATH", tmp_path / "legacy-system-store" / "wasm.db"
    )


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Keep the audit trail inside the test's own directory and off the host's journal.

    A CLI command under test records ``cli.command``; without this it would
    write to ``/etc/noust`` (or fail to, and flag the audit trail as broken
    for the next test) and send a datagram to the developer's own journald.
    ``NOUST_WEB_STATE_DIR``, when a test sets it, still decides the location,
    exactly as it does for the console.

    Yields:
        Nothing; the process-wide audit state is forgotten on the way out.
    """
    from noust.core import audit, paths
    from noust.core.audit import ledger, sinks

    def default_log_path() -> Path:
        state = paths.getenv("WEB_STATE_DIR")
        return Path(state) / audit.LOG_NAME if state else tmp_path / "audit-state" / audit.LOG_NAME

    monkeypatch.setattr(sinks, "JOURNALD_SOCKET", tmp_path / "no-journald.socket")
    monkeypatch.setattr(audit, "default_log_path", default_log_path)
    audit.reset()
    yield
    ledger.uninstall_ledger()
    audit.reset()


@pytest.fixture(autouse=True)
def quiet_deploy_events() -> Iterator[None]:
    """
    Keep the default deployment listeners (notifications, GitHub) out of tests.

    A test that deploys must not try to notify a channel or call GitHub; a
    test of a listener calls it directly or subscribes explicitly.

    Yields:
        Nothing; the listeners are forgotten on the way out.
    """
    from noust.deployers import deploy_events

    deploy_events.reset()
    deploy_events.suspend_defaults(True)
    yield
    deploy_events.reset()
    deploy_events.suspend_defaults(False)


@pytest.fixture(autouse=True)
def default_filesystem() -> Iterator[None]:
    """
    Put the process-wide filesystem back to the real one after every test.

    ``--dry-run`` swaps a :class:`~noust.core.fs.DryRunFileSystem` in globally,
    and a CLI test that exercised it left it installed for whatever ran next:
    a later test's store then refused to create its database file, and only
    when the two happened to run in that order.

    Yields:
        Nothing; the reset happens on the way out.
    """
    yield
    set_fs(None)


@pytest.fixture(autouse=True)
def fresh_upstream_answers() -> Iterator[None]:
    """
    Forget what the remote said about any application, before and after every test.

    :func:`~noust.deployers.lifecycle.check_upstream` reuses an answer for a
    few seconds; one test's remote must not answer the next test's question.

    Yields:
        Nothing; the answers are dropped on the way in and out.
    """

    def forget() -> None:
        lifecycle = sys.modules.get("noust.deployers.lifecycle")
        if lifecycle is not None:
            lifecycle._upstream_answers.clear()

    forget()
    yield
    forget()


@pytest.fixture
def runner() -> FakeRunner:
    """
    Provide a FakeRunner installed as the process-wide runner.

    Returns:
        The fake runner, for scripting responses and asserting on calls.
    """
    fake = FakeRunner()
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Provide an isolated filesystem root with HOME pointed at it.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The sandbox root.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path
