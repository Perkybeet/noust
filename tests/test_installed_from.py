# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the new versions of a Noust that an app store installed arrive from (item 199).

A central installed from the App Center of a NAS is a container the store made, and the store is what
replaces it with the next version: each version is a package of its own, reviewed by the store. The
console used to say "On the Docker host: docker compose pull", which is a way to leave a store's app
in a state the store knows nothing about. The package says where it came from in
``NOUST_INSTALLED_FROM``, a closed list, and the version check and the self-update say where the
versions arrive, never a command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import install_source, package_index
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.update_checker import UpdateChecker
from noust.managers.self_update import SelfUpdate
from noust.web.api import system as system_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import require_elevated


class TestTheSetting:
    def test_nothing_says_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOUST_INSTALLED_FROM", raising=False)

        assert install_source.installed_from() is None

    def test_the_app_center_of_ugreen_is_known(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOUST_INSTALLED_FROM", "ugreen-app-center")

        assert install_source.installed_from() == install_source.UGREEN_APP_CENTER

    @pytest.mark.parametrize(
        "value",
        ["", "docker", "UGREEN-APP-CENTER ", "ugreen-app-center\n", "ugreen", "../x", "a" * 400],
    )
    def test_a_name_that_is_not_in_the_closed_list_is_nobody(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("NOUST_INSTALLED_FROM", value)

        assert install_source.installed_from() is None

    def test_every_source_has_a_name_to_say(self) -> None:
        assert install_source.SOURCES[install_source.UGREEN_APP_CENTER] == "the UGREEN App Center"


def manager(tmp_path: Path, **changes: Any) -> SelfUpdate:
    options: dict[str, Any] = {
        "method": "pip",
        "container": True,
        "systemd": False,
        "version": "4.3.0",
        "process_started": 0.0,
        "installed_from": install_source.UGREEN_APP_CENTER,
    }
    options.update(changes)
    return SelfUpdate(runner=FakeRunner(), record_path=tmp_path / "self-update.json", **options)


class TestTheSelfUpdate:
    def test_it_says_the_versions_arrive_through_the_store_and_not_a_docker_command(
        self, tmp_path: Path
    ) -> None:
        refusal = manager(tmp_path).refusal()

        assert refusal is not None and refusal.code == "app_store"
        assert "UGREEN App Center" in refusal.message
        assert "docker" not in (refusal.message + refusal.hint).lower()
        assert refusal.hint.startswith("Open the UGREEN App Center")

    def test_it_comes_before_the_container_it_also_is(self, tmp_path: Path) -> None:
        # A store's app is a container, and "replace the image" is what the store does.
        refusal = manager(tmp_path, container=True).refusal()

        assert refusal is not None and refusal.code == "app_store"

    def test_a_noust_the_store_did_not_install_is_what_it_was(self, tmp_path: Path) -> None:
        refusal = manager(tmp_path, installed_from=None).refusal()

        assert refusal is not None and refusal.code == "container_image"

    def test_the_status_says_where_it_came_from(self, tmp_path: Path) -> None:
        status = manager(tmp_path).status()

        assert status["installed_from"] == "ugreen-app-center"
        assert status["supported"] is False and status["code"] == "app_store"
        assert status["command"] is None


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    monkeypatch.setenv("NOUST_INSTALLED_FROM", "ugreen-app-center")
    monkeypatch.setenv("NOUST_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(system_api.router, prefix="/api/system")
    session = {"session_id": "test", "scope": "admin"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    yield TestClient(app)
    Config.reset_instance()


def answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, newest: str) -> None:
    """Script the check: an apt installation, and a release that is newer than this one."""
    monkeypatch.setattr(UpdateChecker, "CACHE_FILE", tmp_path / "version_check.json")
    monkeypatch.setattr(
        UpdateChecker, "_detect_installation_method", classmethod(lambda cls: "apt")
    )
    monkeypatch.setattr(
        UpdateChecker,
        "_fetch_offer",
        classmethod(lambda cls, method: package_index.Offer(repository=newest, index=newest)),
    )
    monkeypatch.setattr(UpdateChecker, "_fetch_published_version", classmethod(lambda cls: newest))
    monkeypatch.setattr(UpdateChecker, "_fetch_security_releases", classmethod(lambda cls: []))


class TestTheApi:
    def test_the_update_page_is_told_where_the_versions_arrive_from(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SelfUpdate, "in_container", lambda self: True)

        body = client.get("/api/system/update").json()

        assert body["installed_from"] == "ugreen-app-center"
        assert body["supported"] is False and body["code"] == "app_store"
        assert body["command"] is None

    def test_asking_it_to_update_itself_is_refused_with_where_to_do_it(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/system/update")

        assert response.status_code == 501
        assert response.json()["detail"]["error"] == "app_store"
        assert "UGREEN App Center" in response.json()["detail"]["hint"]

    def test_the_version_check_names_the_store_and_gives_no_command(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        answer(monkeypatch, tmp_path, "99.0.0")

        body = client.get("/api/system/version").json()

        assert body["installed_from"] == "ugreen-app-center"
        # It is still said that a version is there: what changes is how it arrives.
        assert body["update_state"] == "update_available" and body["latest_version"] == "99.0.0"
        assert body["update_command"] is None
        assert body["refresh_command"] is None

    def test_without_the_store_the_same_check_offers_the_command_it_always_did(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("NOUST_INSTALLED_FROM")
        answer(monkeypatch, tmp_path, "99.0.0")

        body = client.get("/api/system/version").json()

        assert body["installed_from"] is None
        assert body["update_command"] == "sudo apt update && sudo apt install noust"

    def test_a_noust_the_store_did_not_install_says_nothing_of_one(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NOUST_INSTALLED_FROM")
        Config().set("updates.check", False)

        assert client.get("/api/system/version").json()["installed_from"] is None
