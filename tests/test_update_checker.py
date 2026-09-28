# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the ``updates.check`` switch.

The GitHub check used to be unconditional: every ``wasm`` command started a
background thread that hit the network, and there was no way to turn it off
for a server with no route to GitHub, or one where an operator simply does
not want the request made. The switch must be read fresh every time - a
long-lived process such as the panel must not need a restart for
``wasm config set updates.check false`` to take effect - and turning it off
must never make the check itself block anything: it already runs off the
command's own path, in a background thread with a short timeout.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from wasm import __version__
from wasm.core import package_index
from wasm.core.config import Config
from wasm.core.update_checker import UpdateChecker, VersionCheck, _location


def _check(
    *,
    installable: str | None = None,
    published: str | None = None,
    method: str = "pip",
    current: str = __version__,
    checked_at: float | None = None,
    noted: bool = False,
) -> VersionCheck:
    """
    Build a check as this installation would have made it.

    Args:
        installable: What the installation's source offers.
        published: The latest GitHub release.
        method: The installation method.
        current: The version the check was made by.
        checked_at: When; now by default.
        noted: Whether the on-the-way note was already shown.

    Returns:
        The check.
    """
    import time

    return VersionCheck(
        current=current,
        location=_location(),
        method=method,
        installable=installable,
        published=published,
        checked_at=time.time() if checked_at is None else checked_at,
        noted=noted,
    )


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """
    Point the configuration singleton at a sandbox file.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Yields:
        The path the singleton reads from and writes to.
    """
    path = tmp_path / "etc" / "wasm" / "config.yaml"
    monkeypatch.setattr("wasm.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture(autouse=True)
def _reset_checker_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """
    Isolate the checker's class-level state and cache file between tests.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
    """
    monkeypatch.setattr(UpdateChecker, "CACHE_FILE", tmp_path / "version_check.json")
    UpdateChecker._check_thread = None
    UpdateChecker._pending = None
    try:
        yield
    finally:
        UpdateChecker._check_thread = None
        UpdateChecker._pending = None


def test_enabled_by_default(config_path: Path) -> None:
    """No configuration file at all means the check is on."""
    assert UpdateChecker.enabled() is True


def test_disabled_when_configured_off(config_path: Path) -> None:
    """'wasm config set updates.check false' must be honoured."""
    Config().set("updates.check", False)

    assert UpdateChecker.enabled() is False


def test_enabled_reads_the_configuration_fresh_each_time(config_path: Path) -> None:
    """A long-lived process must not need a restart for the switch to take effect."""
    assert UpdateChecker.enabled() is True

    Config().set("updates.check", False)
    assert UpdateChecker.enabled() is False

    Config().set("updates.check", True)
    assert UpdateChecker.enabled() is True


def test_a_configuration_that_cannot_be_read_defaults_to_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cosmetic check failing to read its own switch must not look like a crash."""

    def _broken(self: object, key: str, default: object = None) -> object:
        raise OSError("no such file or directory")

    monkeypatch.setattr("wasm.core.config.Config.get", _broken)

    assert UpdateChecker.enabled() is True


def test_start_background_check_makes_no_request_when_disabled(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabled must mean no thread, no cache read and no cached message left over."""
    Config().set("updates.check", False)
    calls: list[str] = []
    monkeypatch.setattr(
        UpdateChecker, "_background_check", classmethod(lambda cls: calls.append("ran"))
    )
    monkeypatch.setattr(
        UpdateChecker, "_cached_check", classmethod(lambda cls: calls.append("cache") or None)
    )
    UpdateChecker._pending = _check(installable="9.9.9")  # stale, from a previous enabled run

    UpdateChecker.start_background_check()

    assert calls == []
    assert UpdateChecker._pending is None
    assert UpdateChecker._check_thread is None


def test_start_background_check_still_runs_when_enabled(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary case - left on - is unaffected by the switch's presence."""
    started = []
    monkeypatch.setattr(
        UpdateChecker,
        "_cached_check",
        classmethod(lambda cls: started.append("checked") or None),
    )
    monkeypatch.setattr(UpdateChecker, "_background_check", classmethod(lambda cls: None))

    UpdateChecker.start_background_check()
    if UpdateChecker._check_thread is not None:
        UpdateChecker._check_thread.join(timeout=2)

    assert started == ["checked"]


def test_start_background_check_returns_immediately_when_disabled(
    config_path: Path,
) -> None:
    """Disabling the check must never be the thing that makes a command slow to start."""
    import time

    Config().set("updates.check", False)

    started = time.monotonic()
    UpdateChecker.start_background_check()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5


# The banner. It used to print to stdout after every command, so it landed
# after the JSON document of `wasm ... --json | jq` and broke the parse.


class _Stream:
    """A text stream that reports whether it is a terminal."""

    def __init__(self, tty: bool) -> None:
        self.tty = tty
        self.written: list[str] = []

    def isatty(self) -> bool:
        return self.tty

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        return None


@pytest.fixture
def pending_update() -> None:
    """A finished check that found a newer installable version, on a pip install."""
    UpdateChecker._pending = _check(installable="99.0.0", published="99.0.0")


def test_the_banner_is_written_to_stderr_never_stdout(
    pending_update: None, capsys: pytest.CaptureFixture[str]
) -> None:
    UpdateChecker.show_update_if_available(timeout=0)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "New version available: 99.0.0" in captured.err
    assert "pip install --upgrade wasm-cli" in captured.err


@pytest.mark.parametrize(
    ("argv", "stdout_tty", "stderr_tty", "expected"),
    [
        (["app", "list"], True, True, True),
        (["app", "list", "--json"], True, True, False),
        (["--json", "app", "list"], True, True, False),
        (["app", "list"], False, True, False),  # piped: `wasm app list | grep`
        (["app", "list"], True, False, False),  # stderr to a log file
    ],
)
def test_the_banner_is_only_announced_to_a_person(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    stdout_tty: bool,
    stderr_tty: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr("sys.stdout", _Stream(stdout_tty))
    monkeypatch.setattr("sys.stderr", _Stream(stderr_tty))

    assert UpdateChecker.should_announce(argv) is expected


def test_entrypoint_neither_checks_nor_announces_under_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--json output must be exactly the document: no request, no banner."""
    from wasm.cli import app

    calls: list[str] = []
    monkeypatch.setattr("sys.argv", ["wasm", "app", "list", "--json"])
    monkeypatch.setattr("sys.stdout", _Stream(True))
    monkeypatch.setattr("sys.stderr", _Stream(True))
    monkeypatch.setattr(app, "main", lambda argv=None: 0)
    monkeypatch.setattr(
        UpdateChecker, "start_background_check", classmethod(lambda cls: calls.append("start"))
    )
    monkeypatch.setattr(
        UpdateChecker,
        "show_update_if_available",
        classmethod(lambda cls, timeout=0.1: calls.append("show")),
    )

    with pytest.raises(SystemExit) as exited:
        app.entrypoint()

    assert exited.value.code == 0
    assert calls == []


def test_entrypoint_checks_and_announces_on_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from wasm.cli import app

    calls: list[str] = []
    monkeypatch.setattr("sys.argv", ["wasm", "app", "list"])
    monkeypatch.setattr("sys.stdout", _Stream(True))
    monkeypatch.setattr("sys.stderr", _Stream(True))
    monkeypatch.setattr(app, "main", lambda argv=None: 0)
    monkeypatch.setattr(
        UpdateChecker, "start_background_check", classmethod(lambda cls: calls.append("start"))
    )
    monkeypatch.setattr(
        UpdateChecker,
        "show_update_if_available",
        classmethod(lambda cls, timeout=0.1: calls.append("show")),
    )

    with pytest.raises(SystemExit):
        app.entrypoint()

    assert calls == ["start", "show"]


def test_a_banner_that_cannot_be_written_is_logged_not_raised(
    pending_update: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The reader of `wasm ... | head` went away: the command's exit must not change."""

    class Closed(_Stream):
        def write(self, text: str) -> int:
            raise BrokenPipeError("reader went away")

    monkeypatch.setattr("sys.stderr", Closed(True))

    with caplog.at_level("DEBUG", logger="wasm.core.update_checker"):
        UpdateChecker.show_update_if_available(timeout=0)

    assert "reader went away" in caplog.text


def test_a_programming_error_in_the_banner_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare except turned bugs like this one into silence for whole releases (rule 2)."""
    UpdateChecker._pending = _check(installable="99.0.0")

    def broken(cls: type[UpdateChecker], check: object) -> None:
        raise AttributeError("no such method")

    monkeypatch.setattr(UpdateChecker, "_show_update_message", classmethod(broken))

    with pytest.raises(AttributeError):
        UpdateChecker.show_update_if_available(timeout=0)


def test_an_unreachable_github_is_logged_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import urllib.error

    def unreachable(url: str, **kwargs: object) -> bytes:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(package_index, "fetch", unreachable)

    with caplog.at_level("DEBUG", logger="wasm.core.package_index"):
        assert UpdateChecker._fetch_published_version() is None

    assert "no route to host" in caplog.text


def test_a_cache_that_is_not_an_object_reads_as_no_cache() -> None:
    UpdateChecker.CACHE_FILE.write_text("[1, 2, 3]")

    assert UpdateChecker._read_cache() is None
    assert UpdateChecker._is_cache_valid() is False


def test_a_cache_with_a_non_numeric_timestamp_is_not_valid() -> None:
    UpdateChecker.CACHE_FILE.write_text('{"checked_at": "yesterday"}')

    assert UpdateChecker._is_cache_valid() is False


# What an update is announced for. A GitHub release exists as soon as its tag
# is pushed; the package this server installs from arrives minutes (PyPI) to
# half an hour (OBS) later. Announcing the GitHub release sent operators to an
# upgrade that did nothing.


@pytest.mark.parametrize(
    ("installable", "published", "expected"),
    [
        ("99.0.0", "99.0.0", "update_available"),
        ("99.0.0", None, "update_available"),  # GitHub unreachable, the repository answered
        (__version__, "99.0.0", "on_the_way"),  # published, not yet in the repository
        (None, "99.0.0", "on_the_way"),  # repository unreadable: never claim an upgrade
        (__version__, __version__, "up_to_date"),
        (None, None, "up_to_date"),
        ("0.0.1", "0.0.1", "up_to_date"),
    ],
)
def test_the_state_follows_the_installable_version(
    installable: str | None, published: str | None, expected: str
) -> None:
    check = _check(installable=installable, published=published)

    assert check.state == expected


def test_on_the_way_announces_the_published_version_and_its_notes() -> None:
    check = _check(installable=__version__, published="99.0.0")

    assert check.announced_version == "99.0.0"
    assert check.release_url == "https://github.com/Perkybeet/wasm/releases/tag/v99.0.0"


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """
    Replace detection and both fetches with recorded, scripted answers.

    Returns:
        ``calls`` records every probe; ``installable``/``published`` hold the
        answers (the first element is used).
    """
    state: dict[str, list[str]] = {"calls": [], "installable": ["99.0.0"], "published": ["99.0.0"]}

    def detect(cls: type[UpdateChecker]) -> str:
        state["calls"].append("detect")
        return "apt"

    def installable(cls: type[UpdateChecker], method: str) -> str | None:
        state["calls"].append(f"installable:{method}")
        return state["installable"][0] or None

    def published(cls: type[UpdateChecker]) -> str | None:
        state["calls"].append("published")
        return state["published"][0] or None

    monkeypatch.setattr(UpdateChecker, "_detect_installation_method", classmethod(detect))
    monkeypatch.setattr(UpdateChecker, "_fetch_installable_version", classmethod(installable))
    monkeypatch.setattr(UpdateChecker, "_fetch_published_version", classmethod(published))
    return state


def test_a_check_records_method_installable_and_published(sources: dict[str, list[str]]) -> None:
    sources["installable"] = [__version__]

    check = UpdateChecker.check()

    assert check.state == "on_the_way"
    cached = UpdateChecker._read_cache()
    assert cached is not None
    assert cached["method"] == "apt"
    assert cached["installable"] == __version__
    assert cached["published"] == "99.0.0"
    assert cached["current"] == __version__
    assert isinstance(cached["checked_at"], float)
    assert sorted(sources["calls"]) == ["detect", "installable:apt", "published"]


def test_a_fresh_cache_answers_without_a_request(sources: dict[str, list[str]]) -> None:
    UpdateChecker.check()
    sources["calls"].clear()

    assert UpdateChecker.check().installable == "99.0.0"
    assert sources["calls"] == []


def test_the_cache_is_invalidated_when_the_version_changes(
    sources: dict[str, list[str]],
) -> None:
    """After an upgrade, the answer the old version cached is about another version."""
    UpdateChecker._write_cache(_check(installable="99.0.0", current="0.0.1").to_cache())

    UpdateChecker.check()

    assert "detect" in sources["calls"]
    assert "published" in sources["calls"]
    assert UpdateChecker._read_cache()["current"] == __version__  # type: ignore[index]


def test_the_cache_is_invalidated_when_the_installation_moves(
    sources: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """From pip to apt, say: the method and the command are different now."""
    UpdateChecker._write_cache(_check(installable="99.0.0", method="pip").to_cache())
    monkeypatch.setattr(
        "wasm.core.update_checker._location", lambda: "/usr/lib/python3/dist-packages/wasm"
    )

    check = UpdateChecker.check()

    assert "detect" in sources["calls"]
    assert check.method == "apt"


def test_an_expired_cache_reuses_the_method_but_asks_again(
    sources: dict[str, list[str]],
) -> None:
    UpdateChecker._write_cache(
        _check(installable="1.0.0", method="zypper", checked_at=0.0).to_cache()
    )

    check = UpdateChecker.check()

    assert "detect" not in sources["calls"]
    assert "installable:zypper" in sources["calls"]
    assert check.installable == "99.0.0"


def test_an_offline_check_is_up_to_date_and_still_cached(sources: dict[str, list[str]]) -> None:
    """No route out: say nothing, and do not retry on every command."""
    sources["installable"] = [""]
    sources["published"] = [""]

    check = UpdateChecker.check()

    assert check.state == "up_to_date"
    assert UpdateChecker._is_cache_valid() is True


def test_an_old_format_cache_is_not_used() -> None:
    """2.2 cached only GitHub's answer; it must not be read as an installable version."""
    import json
    import time

    UpdateChecker.CACHE_FILE.write_text(
        json.dumps({"latest_version": "99.0.0", "has_update": True, "checked_at": time.time()})
    )

    assert UpdateChecker._is_cache_valid() is False


def test_the_banner_names_the_installable_version_and_the_right_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    UpdateChecker._pending = _check(installable="99.0.0", published="99.1.0", method="apt")

    UpdateChecker.show_update_if_available(timeout=0)

    err = capsys.readouterr().err
    assert "New version available: 99.0.0" in err
    assert "sudo apt update && sudo apt install --only-upgrade wasm" in err
    assert "releases/tag/v99.0.0" in err


def test_on_the_way_is_a_quiet_note_shown_once_per_cache_period(
    sources: dict[str, list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    sources["installable"] = [__version__]
    UpdateChecker.check()

    UpdateChecker.start_background_check()
    UpdateChecker.show_update_if_available(timeout=0)
    first = capsys.readouterr().err

    assert (
        "WASM 99.0.0 is published; the package for this system is not available yet "
        "(usually 15-30 minutes). Nothing to do now." in first
    )
    assert "New version available" not in first
    assert "Update with" not in first

    UpdateChecker.start_background_check()
    UpdateChecker.show_update_if_available(timeout=0)

    assert capsys.readouterr().err == ""


def test_up_to_date_says_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    UpdateChecker._pending = _check(installable=__version__, published=__version__)

    UpdateChecker.show_update_if_available(timeout=0)

    assert capsys.readouterr().err == ""


# How the running WASM was installed, which decides both where the installable
# version is read and the command offered.


@pytest.fixture
def installed_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Place the running package somewhere, with no dpkg database and no metadata."""
    import importlib.metadata

    def place(location: str, *, dpkg: bool = False) -> None:
        monkeypatch.setattr("wasm.core.update_checker._location", lambda: location)
        status = tmp_path / "dpkg-status"
        if dpkg:
            status.write_text("")
        monkeypatch.setattr(UpdateChecker, "DPKG_STATUS", status)

    def no_distribution(name: str) -> object:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", no_distribution)
    monkeypatch.setattr("sys.prefix", "/usr")
    return place


def test_detects_the_debian_package(installed_at, runner) -> None:
    installed_at("/usr/lib/python3/dist-packages/wasm", dpkg=True)
    runner.script(["dpkg-query", "-W"], stdout="install ok installed")

    assert UpdateChecker._detect_installation_method() == "apt"
    assert runner.calls[0] == ("dpkg-query", "-W", "-f=${Status}", "wasm")


def test_detects_the_rpm_and_the_manager_that_upgrades_it(installed_at, runner) -> None:
    installed_at("/usr/lib/python3.12/site-packages/wasm")
    runner.only_knows("rpm", "dnf")

    assert UpdateChecker._detect_installation_method() == "dnf"
    assert ("rpm", "-q", "wasm-cli") in runner.calls


def test_a_pip_install_beside_a_leftover_package_is_pip(installed_at, runner, monkeypatch) -> None:
    """dpkg knowing `wasm` says nothing about a WASM running from /usr/local."""
    import importlib.metadata

    installed_at("/usr/local/lib/python3.12/dist-packages/wasm", dpkg=True)

    class Distribution:
        def read_text(self, name: str) -> str | None:
            return None

    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: Distribution())

    assert UpdateChecker._detect_installation_method() == "pip"
    assert runner.calls == []


def test_detects_pipx_from_the_running_interpreter(installed_at, runner, monkeypatch) -> None:
    installed_at("/root/.local/share/pipx/venvs/wasm-cli/lib/python3.12/site-packages/wasm")
    monkeypatch.setattr("sys.prefix", "/root/.local/share/pipx/venvs/wasm-cli")

    assert UpdateChecker._detect_installation_method() == "pipx"


def test_detects_an_editable_checkout_as_source(installed_at, runner, monkeypatch) -> None:
    import importlib.metadata

    installed_at("/opt/wasm/src/wasm")

    class Distribution:
        def read_text(self, name: str) -> str | None:
            return '{"url": "file:///opt/wasm", "dir_info": {"editable": true}}'

    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: Distribution())

    assert UpdateChecker._detect_installation_method() == "source"


def test_the_installable_version_is_read_where_the_method_upgrades_from(
    monkeypatch: pytest.MonkeyPatch, runner
) -> None:
    asked: list[str] = []

    def fetch(url: str, **kwargs: object) -> bytes:
        asked.append(url)
        return b'{"info": {"version": "99.0.0"}, "tag_name": "v99.1.0"}'

    monkeypatch.setattr(package_index, "fetch", fetch)

    assert UpdateChecker._fetch_installable_version("pip") == "99.0.0"
    assert asked == ["https://pypi.org/pypi/wasm-cli/json"]
    assert UpdateChecker._fetch_installable_version("source") == "99.1.0"
    assert asked[-1] == "https://api.github.com/repos/Perkybeet/wasm/releases/latest"
