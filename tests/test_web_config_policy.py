# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/config/policy``: the four settings of "General" that say how a server behaves (item 166).

Whether the server asks for new releases, where certificate notices go, how many backups it keeps
of each application and how long a deployment may build are one form and one typed route, so the
central that sets them for the fleet writes one request per server and the server judges each with
the rule of its own key. The folders, the web server and the console's bind that sit beside them
in the old form are the machine's, and this route does not touch them. What is pinned: the defaults
a server answers with and whether its operator chose something else, that all four are written
together and nothing else, that a value out of range is refused as a whole before any of the four
is written, and the permissions the routes declare.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import delivery
from noust.core.config import Config
from noust.web.api import config_policy
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated

DEFAULTS: dict[str, Any] = {
    "updates_check": True,
    "ssl_email": "",
    "backup_max_per_app": 10,
    "delivery_max_minutes": 60,
}
OWN: dict[str, Any] = {
    "updates_check": False,
    "ssl_email": "ops@example.com",
    "backup_max_per_app": 5,
    "delivery_max_minutes": 30,
}
URL = "/api/config/policy"


@pytest.fixture
def config_path(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = sandbox / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    delivery.forget()
    try:
        yield path
    finally:
        Config.reset_instance()
        delivery.forget()


@pytest.fixture
def client(config_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(config_policy.router, prefix="/api/config/policy")
    install_error_handlers(app)
    session = {"session_id": "test", "type": "master"}
    app.dependency_overrides[get_current_session] = lambda: session
    app.dependency_overrides[require_elevated] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


def stored(config_path: Path) -> dict[str, Any]:
    return yaml.safe_load(config_path.read_text())


class TestWhatAServerAnswers:
    def test_an_untouched_server_says_the_defaults_and_that_nobody_chose_otherwise(
        self, client: TestClient
    ) -> None:
        body = client.get(URL).json()

        assert body == {**DEFAULTS, "customized": False}

    def test_what_its_operator_chose_makes_it_customised(
        self, client: TestClient, config_path: Path
    ) -> None:
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.safe_dump({"ssl": {"email": "me@example.com"}}))
        Config.reset_instance()

        body = client.get(URL).json()

        assert (body["ssl_email"], body["customized"]) == ("me@example.com", True)

    def test_it_reads_the_keys_the_servers_other_routes_and_commands_write(
        self, client: TestClient
    ) -> None:
        config = Config()
        config.set("deploy.max_minutes", 15)
        config.set("backup.max_per_app", 3)
        config.write()

        body = client.get(URL).json()

        assert (body["delivery_max_minutes"], body["backup_max_per_app"]) == (15, 3)


class TestWritingIt:
    def test_the_four_are_written_together(self, client: TestClient, config_path: Path) -> None:
        response = client.put(URL, json=OWN)

        assert response.status_code == 200, response.text
        assert client.get(URL).json() == {**OWN, "customized": True}
        written = stored(config_path)
        assert written["updates"]["check"] is False
        assert written["ssl"]["email"] == "ops@example.com"
        assert written["backup"]["max_per_app"] == 5
        assert written["deploy"]["max_minutes"] == 30

    def test_what_is_beside_them_in_the_same_sections_is_left_as_it_was(
        self, client: TestClient, config_path: Path
    ) -> None:
        config = Config()
        config.set("backup.directory", "/srv/backups")
        config.set("deploy.max_concurrent_builds", 4)
        config.set("deploy.queue_limit", 40)
        config.set("ssl.provider", "certbot")
        config.set("ssl.enabled", False)
        config.write()

        assert client.put(URL, json=OWN).status_code == 200

        written = stored(config_path)
        assert written["backup"]["directory"] == "/srv/backups"
        assert (written["deploy"]["max_concurrent_builds"], written["deploy"]["queue_limit"]) == (
            4,
            40,
        )
        assert written["ssl"]["enabled"] is False

    def test_the_queue_obeys_the_new_time_limit_without_a_restart(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        looked: list[str] = []
        monkeypatch.setattr(delivery, "forget", lambda: looked.append("forgotten"))

        client.put(URL, json=OWN)

        assert looked == ["forgotten"]
        Config.reset_instance()
        assert delivery.DeliverySettings.read(Config()).max_minutes == 30

    def test_writing_what_is_there_changes_nothing_and_is_not_an_error(
        self, client: TestClient
    ) -> None:
        assert client.put(URL, json=DEFAULTS).status_code == 200
        assert client.get(URL).json()["customized"] is False

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("delivery_max_minutes", 1441),
            ("delivery_max_minutes", -1),
            ("backup_max_per_app", 0),
            ("backup_max_per_app", 101),
        ],
    )
    def test_a_value_out_of_range_is_refused_whole_and_nothing_is_written(
        self, client: TestClient, config_path: Path, field: str, value: int
    ) -> None:
        response = client.put(URL, json={**OWN, field: value})

        assert response.status_code == 400, response.text
        assert field in response.json()["fields"]
        assert "must be between" in response.json()["detail"]
        assert not config_path.exists()
        assert client.get(URL).json() == {**DEFAULTS, "customized": False}

    def test_a_field_that_is_missing_is_refused_naming_it(self, client: TestClient) -> None:
        body = {k: v for k, v in OWN.items() if k != "ssl_email"}

        response = client.put(URL, json=body)

        assert response.status_code == 422
        assert "ssl_email" in response.json()["fields"]

    def test_a_field_that_is_not_one_of_the_four_is_not_written(
        self, client: TestClient, config_path: Path
    ) -> None:
        response = client.put(URL, json={**OWN, "apps_directory": "/srv/elsewhere"})

        assert response.status_code == 200
        assert stored(config_path)["apps_directory"] != "/srv/elsewhere"


def test_the_routes_declare_their_permissions() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_config import ROUTES

    assert ROUTES[("GET", URL)] == Permission.SETTINGS_READ
    assert ROUTES[("PUT", URL)] == Permission.SETTINGS_MANAGE
