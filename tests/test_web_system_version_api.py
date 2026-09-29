# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for ``GET /api/system/version``.

``updates.check`` is the switch an operator on an airgapped or tightly
firewalled server needs: without it, every load of the panel's version
display made a GitHub request that could only time out. Disabled, the
endpoint must say so plainly rather than quietly reporting "no update found",
which looks identical to a check that actually ran and found nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust import __version__
from noust.core.config import Config
from noust.core.update_checker import UpdateChecker
from noust.web.api import system as system_api
from noust.web.api.auth import get_current_session


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Point the configuration singleton at a sandbox file.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The path the singleton reads from and writes to.
    """
    path = tmp_path / "etc" / "wasm" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    yield path
    Config.reset_instance()


@pytest.fixture
def client(config_path: Path) -> TestClient:
    """
    Build a client for the system router alone, authentication stubbed.

    Args:
        config_path: Fixture redirecting configuration reads into the sandbox.

    Returns:
        A client whose requests are already authenticated.
    """
    app = FastAPI()
    app.include_router(system_api.router, prefix="/api/system")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test", "scope": "read"}
    return TestClient(app)


def test_disabled_reports_status_disabled_and_makes_no_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint must not even try GitHub when the operator turned it off."""
    Config().set("updates.check", False)

    def _boom() -> str | None:
        raise AssertionError("the update check must not run a request while disabled")

    monkeypatch.setattr(UpdateChecker, "_fetch_published_version", classmethod(lambda cls: _boom()))
    monkeypatch.setattr(
        UpdateChecker, "_fetch_installable_version", classmethod(lambda cls, method: _boom())
    )

    response = client.get("/api/system/version")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "disabled"
    assert body["current_version"] == __version__
    assert body["has_update"] is False
    assert body["latest_version"] is None
    assert body["update_command"] is None


def _answer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    installable: str | None,
    published: str | None,
    method: str = "apt",
) -> None:
    """Script the check: where WASM came from, and what each source says."""
    monkeypatch.setattr(UpdateChecker, "CACHE_FILE", tmp_path / "version_check.json")
    monkeypatch.setattr(
        UpdateChecker, "_detect_installation_method", classmethod(lambda cls: method)
    )
    monkeypatch.setattr(
        UpdateChecker, "_fetch_installable_version", classmethod(lambda cls, m: installable)
    )
    monkeypatch.setattr(
        UpdateChecker, "_fetch_published_version", classmethod(lambda cls: published)
    )


def test_enabled_reports_status_checked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ordinary case - checking left on - is unaffected and reports 'checked'."""
    _answer(monkeypatch, tmp_path, installable=__version__, published=__version__)

    response = client.get("/api/system/version")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "checked"
    assert body["has_update"] is False
    assert body["update_state"] == "up_to_date"
    assert body["latest_version"] == __version__
    assert body["published_version"] == __version__
    assert body["update_command"] is None
    assert body["release_url"] is None


def test_an_installable_update_is_offered_with_its_command(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _answer(monkeypatch, tmp_path, installable="99.0.0", published="99.0.0")

    body = client.get("/api/system/version").json()

    assert body["update_state"] == "update_available"
    assert body["has_update"] is True
    assert body["latest_version"] == "99.0.0"
    assert body["update_command"] == "sudo apt update && sudo apt install noust"
    assert body["release_url"] == "https://github.com/Perkybeet/noust/releases/tag/v99.0.0"


def test_a_published_release_not_yet_packaged_is_on_the_way(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug: GitHub has it, the repository does not yet, and has_update must say no."""
    _answer(monkeypatch, tmp_path, installable=__version__, published="99.0.0")

    body = client.get("/api/system/version").json()

    assert body["update_state"] == "on_the_way"
    assert body["has_update"] is False
    assert body["latest_version"] == __version__
    assert body["published_version"] == "99.0.0"
    assert body["release_url"] == "https://github.com/Perkybeet/noust/releases/tag/v99.0.0"
    # Nothing is installable yet: the CLI banner shows no command either
    # (UpdateChecker._show_on_the_way_message), so the API must not offer one.
    assert body["update_command"] is None


def test_a_concurrent_check_already_in_flight_reports_status_checking(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A slow or trickling repository must not hold this request open: a second
    caller arriving while the first is still fetching, with nothing cached
    yet, gets told to check back rather than opening its own connection.
    """
    _answer(monkeypatch, tmp_path, installable="99.0.0", published="99.0.0")
    UpdateChecker._check_lock.acquire()
    try:
        response = client.get("/api/system/version")
    finally:
        UpdateChecker._check_lock.release()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "checking"
    assert body["current_version"] == __version__
    assert body["has_update"] is False
    assert body["latest_version"] is None
    assert body["update_command"] is None
