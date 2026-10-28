# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's unit and the one peer nginx makes it believe.

A console behind nginx reads the client's address and ``https`` from headers it
believes only from a peer it was started with ``--trusted-proxy``. Publishing
the console rewrites its unit with that option and nothing else: every other
option the operator gave ``noust web enable`` stays, and removing it later
puts the unit back as it was.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

import pytest

from noust.core.exceptions import ServiceError
from noust.core.runner import FakeRunner
from noust.managers import console_service
from noust.managers.service_manager import ServiceManager
from tests.support.console_patch_support import patch_console

NOUST_BIN = "/usr/bin/noust"


@pytest.fixture(autouse=True)
def unit_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Path:
    """The managed unit directory, a temporary tree, and a fixed place for the entry point."""
    managed = tmp_path / "etc/systemd/system"
    distro = tmp_path / "usr/lib/systemd/system"
    for directory in (managed, distro):
        directory.mkdir(parents=True)
    monkeypatch.setattr(ServiceManager, "SYSTEMD_DIR", managed)
    monkeypatch.setattr(ServiceManager, "UNIT_SEARCH_DIRS", (managed, distro))
    monkeypatch.setattr(shutil, "which", lambda name: NOUST_BIN if name == "noust" else None)
    return managed


def enable(options: console_service.StartOptions) -> Path:
    """Write the console's unit the way ``noust web enable`` does."""
    console_service._service_manager(False).install_unit(
        console_service.WEB_UNIT,
        console_service.WEB_UNIT_TEMPLATE,
        {"exec_start": console_service._service_exec_start(options)},
    )
    return Path(ServiceManager.SYSTEMD_DIR) / console_service.WEB_UNIT_FILE


def exec_argv(unit: Path) -> list[str]:
    start = console_service._unit_exec_start(unit.read_text(encoding="utf-8"))
    assert start is not None
    return shlex.split(start)


def test_the_peer_is_added_and_every_other_option_stays() -> None:
    unit = enable(console_service.StartOptions(host="127.0.0.1", port=8443, self_signed=True))

    changed = console_service.change_trusted_proxies(add=["127.0.0.1"])

    argv = exec_argv(unit)
    assert changed is True
    assert argv[argv.index("--trusted-proxy") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "8443"
    assert "--self-signed" in argv
    assert "--under-systemd" in argv


def test_asking_again_changes_nothing() -> None:
    unit = enable(console_service.StartOptions(trusted_proxy=("127.0.0.1",)))
    before = unit.read_text(encoding="utf-8")

    assert console_service.change_trusted_proxies(add=["127.0.0.1"]) is False
    assert unit.read_text(encoding="utf-8") == before


def test_removing_the_peer_puts_the_unit_back() -> None:
    unit = enable(console_service.StartOptions(port=8080))
    original = unit.read_text(encoding="utf-8")
    console_service.change_trusted_proxies(add=["127.0.0.1"])

    changed = console_service.change_trusted_proxies(remove=["127.0.0.1"])

    assert changed is True
    assert "--trusted-proxy" not in exec_argv(unit)
    assert unit.read_text(encoding="utf-8") == original


def test_another_proxy_the_operator_trusts_is_kept() -> None:
    unit = enable(console_service.StartOptions(trusted_proxy=("10.0.0.2",)))

    console_service.change_trusted_proxies(add=["127.0.0.1"])
    console_service.change_trusted_proxies(remove=["127.0.0.1"])

    argv = exec_argv(unit)
    assert argv.count("--trusted-proxy") == 1
    assert argv[argv.index("--trusted-proxy") + 1] == "10.0.0.2"


def test_without_a_service_it_says_how_to_make_one() -> None:
    with pytest.raises(ServiceError) as raised:
        console_service.change_trusted_proxies(add=["127.0.0.1"])

    assert "noust web enable" in (raised.value.details or "")


def test_the_restart_waits_on_the_port_the_unit_binds(
    monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    enable(console_service.StartOptions(port=9191))
    waited: list[tuple[str, int]] = []
    patch_console(
        monkeypatch, "_wait_until_serving", lambda manager, host, port: waited.append((host, port))
    )
    runner.script(["systemctl", "show"], stdout="ActiveState=active\nSubState=running\n")

    console_service.restart_console_service()

    assert runner.ran("systemctl", "restart")
    assert waited == [("127.0.0.1", 9191)]


def test_the_port_and_scheme_come_from_the_unit() -> None:
    enable(console_service.StartOptions(port=8443, self_signed=True))

    assert console_service.console_service_port() == (8443, "https")


# ---------------------------------------------------------------------------
# Writing the unit again does not undo a published console
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path):
    from noust.core.store import NoustStore

    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


def test_enabling_the_console_again_keeps_the_proxy_a_published_console_needs(store) -> None:
    from noust.core.console_exposure_store import ConsoleExposureStore, ExposureState

    ConsoleExposureStore(store).save(
        ExposureState(
            domain="console.example.com", port=8080, proxy_added="127.0.0.1", state="active"
        )
    )

    options = console_service._keeping_published_console(console_service.StartOptions(port=8443))

    assert options.trusted_proxy == ("127.0.0.1",)
    assert options.port == 8443, "nothing else about the options changes"
    assert "--trusted-proxy" in console_service._service_exec_start(options)


def test_a_console_that_is_not_published_is_written_as_asked(store) -> None:
    options = console_service.StartOptions(port=8443)

    assert console_service._keeping_published_console(options) is options


def test_a_proxy_the_operator_already_trusted_is_not_listed_twice(store) -> None:
    from noust.core.console_exposure_store import ConsoleExposureStore, ExposureState

    ConsoleExposureStore(store).save(
        ExposureState(domain="console.example.com", port=8080, proxy_added=None, state="active")
    )
    options = console_service.StartOptions(trusted_proxy=("127.0.0.1",))

    assert console_service._keeping_published_console(options) is options


def test_the_loopback_service_a_new_node_gets_does_not_drop_the_proxy(
    store, monkeypatch: pytest.MonkeyPatch, unit_dirs: Path
) -> None:
    from noust.core.console_exposure_store import ConsoleExposureStore, ExposureState
    from noust.core.logger import Logger

    ConsoleExposureStore(store).save(
        ExposureState(
            domain="console.example.com", port=8080, proxy_added="127.0.0.1", state="active"
        )
    )
    patch_console(monkeypatch, "_wait_until_serving", lambda manager, host, port: None)
    patch_console(monkeypatch, "remember_exposure", lambda host, port, logger=None: None)
    options = console_service.StartOptions(host="127.0.0.1", port=8080)

    console_service._install_and_start(
        options, console_service._build_security_config(options), False, Logger(), issue_token=False
    )

    assert "--trusted-proxy" in exec_argv(unit_dirs / console_service.WEB_UNIT_FILE), (
        "the one place the unit is written keeps it"
    )
