# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the link of a server's notification opens: the central's console, on that server.

A server's own console is normally reachable only through the central's tunnel, so a link built
from its ``web.public_url`` opens nowhere. A central that manages the server sets
``notifications.console_url`` on it, to the server's address inside the central's own console
(``https://central/n/web-2``), and the link of every notification the server raises opens the right
page there. What is pinned: the link uses it for every page of the server (the console takes a
page that is the central's own and drops the server), it wins over ``web.public_url`` for the link
and leaves alone the link to a failure's whole output, the central's own composers (which name a
server) are as they were, a server without it is as it was, and the key is one the server accepts
only as an https address, on a route that needs sudo mode and leaves a trace.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.exceptions import ConfigError
from noust.core.notifications.context import NotificationContext
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

BASE = "https://central.example.com/n/web-2"


class Cfg(dict):
    def get(self, key: str, default: object = None) -> object:
        return super().get(key, default)


class TestTheLink:
    def test_a_page_of_the_server_opens_on_the_server_inside_the_centrals_console(self) -> None:
        ctx = NotificationContext.from_config(Cfg({"notifications.console_url": BASE}))

        link = ctx.link("/apps/shop.example.com")

        assert link is not None
        assert link.url == f"{BASE}/apps/shop.example.com"

    def test_a_trailing_slash_is_not_doubled(self) -> None:
        ctx = NotificationContext.from_config(Cfg({"notifications.console_url": BASE + "/"}))

        assert ctx.link("/backups").url == f"{BASE}/backups"  # type: ignore[union-attr]

    @pytest.mark.parametrize("path", ["/settings/about", "/fleet", "/servers", "/integrations/x"])
    def test_every_page_goes_under_the_server_and_the_console_takes_the_centrals_own(
        self, path: str
    ) -> None:
        # The console reads /n/<server>/<a page of the central> as the central's page and
        # remembers the server, so the server never has to know which pages those are.
        ctx = NotificationContext.from_config(Cfg({"notifications.console_url": BASE}))

        assert ctx.link(path).url == f"{BASE}{path}"  # type: ignore[union-attr]

    def test_a_name_in_the_path_cannot_add_a_query_or_a_fragment(self) -> None:
        ctx = NotificationContext.from_config(Cfg({"notifications.console_url": BASE}))

        assert ctx.link("/apps/a b?x=1#y").url == f"{BASE}/apps/a%20b%3Fx%3D1%23y"  # type: ignore[union-attr]

    def test_it_is_the_link_even_when_the_server_has_an_address_of_its_own(self) -> None:
        ctx = NotificationContext.from_config(
            Cfg({"notifications.console_url": BASE, "web.public_url": "https://web-2.example.com"})
        )

        assert ctx.link("/server").url == f"{BASE}/server"  # type: ignore[union-attr]

    def test_the_whole_output_of_a_failure_is_still_the_servers_own_address(self) -> None:
        # That page is the server's API, read in a browser with the server's own session.
        both = NotificationContext.from_config(
            Cfg({"notifications.console_url": BASE, "web.public_url": "https://web-2.example.com"})
        )
        only_ours = NotificationContext.from_config(Cfg({"notifications.console_url": BASE}))

        assert both.output_link("/api/backups/failures/4/output").url == (  # type: ignore[union-attr]
            "https://web-2.example.com/api/backups/failures/4/output"
        )
        assert only_ours.output_link("/api/backups/failures/4/output") is None

    def test_without_it_the_link_is_what_it_was(self) -> None:
        ctx = NotificationContext.from_config(Cfg({"web.public_url": "https://web-2.example.com"}))

        assert ctx.link("/server").url == "https://web-2.example.com/server"  # type: ignore[union-attr]
        assert NotificationContext.from_config(Cfg({})).link("/server") is None

    def test_a_central_speaking_about_a_server_names_it_as_it_always_did(self) -> None:
        ctx = NotificationContext.from_config(
            Cfg(
                {"web.public_url": "https://central.example.com", "notifications.console_url": BASE}
            )
        )

        assert ctx.link("/apps/x", node="web-5").url == (  # type: ignore[union-attr]
            "https://central.example.com/n/web-5/apps/x"
        )
        assert ctx.link("/fleet", node="web-5").url == "https://central.example.com/fleet"  # type: ignore[union-attr]


class TestTheKey:
    def test_it_is_unset_by_default(self) -> None:
        assert DEFAULT_CONFIG["notifications"]["console_url"] == ""

    def test_it_takes_an_https_address_with_a_path(self, tmp_path: Path) -> None:
        config = Config(tmp_path / "config.yaml")

        config.set("notifications.console_url", BASE + "/")

        assert config.get("notifications.console_url") == BASE

    def test_it_can_be_cleared(self, tmp_path: Path) -> None:
        config = Config(tmp_path / "config.yaml")
        config.set("notifications.console_url", BASE)

        config.set("notifications.console_url", "")

        assert config.get("notifications.console_url") == ""

    @pytest.mark.parametrize(
        "bad", ["http://central.example.com", "central.example.com", "https://"]
    )
    def test_anything_else_is_refused_naming_the_key(self, tmp_path: Path, bad: str) -> None:
        config = Config(tmp_path / "config.yaml")

        with pytest.raises(ConfigError) as caught:
            config.set("notifications.console_url", bad)

        assert "notifications.console_url" in caught.value.message


# -- The route ----------------------------------------------------------------------


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def client(tmp_path: Path, store: NoustStore, config_path: Path) -> TestClient:
    app: FastAPI = create_app(
        SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000)
    )
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


def elevate(client: TestClient) -> None:
    response = client.post(
        "/api/auth/elevate", json={"token": get_token_manager().generate_master_token()}
    )
    assert response.status_code == 200, response.text


class TestTheRoute:
    def test_it_says_nothing_until_one_is_set(self, client: TestClient) -> None:
        response = client.get("/api/notifications/console-url")

        assert response.status_code == 200
        assert response.json() == {"url": ""}

    def test_setting_it_needs_sudo_mode(self, client: TestClient) -> None:
        response = client.put("/api/notifications/console-url", json={"url": BASE})

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert client.get("/api/notifications/console-url").json() == {"url": ""}

    def test_it_is_set_read_and_cleared(self, client: TestClient) -> None:
        elevate(client)

        put = client.put("/api/notifications/console-url", json={"url": BASE + "/"})
        assert put.status_code == 200, put.text
        assert put.json() == {"url": BASE}
        assert client.get("/api/notifications/console-url").json() == {"url": BASE}

        cleared = client.put("/api/notifications/console-url", json={"url": ""})
        assert cleared.json() == {"url": ""}

    def test_the_servers_own_validator_refuses_what_is_not_an_https_address(
        self, client: TestClient
    ) -> None:
        elevate(client)

        response = client.put(
            "/api/notifications/console-url", json={"url": "http://central.example.com"}
        )

        assert response.status_code == 400
        assert "notifications.console_url" in response.text
        assert client.get("/api/notifications/console-url").json() == {"url": ""}

    def test_a_change_leaves_a_trace_that_names_the_address(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict] = []
        monkeypatch.setattr(
            "noust.web.api.notifications.audit.record",
            lambda event, **fields: events.append({"event": event, **fields}),
        )
        elevate(client)

        client.put("/api/notifications/console-url", json={"url": BASE})

        assert [e["event"] for e in events] == ["notify.console_url.set"]
        assert events[0]["target"] == "notifications:console_url"
        assert events[0]["details"] == {"url": BASE}
