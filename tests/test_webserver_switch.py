# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Changing the web server while the other one serves sites (backlog item 105, the guard).

``webserver`` in the configuration is the web server **new** applications are put behind: every
application remembers its own (``apps.webserver``). Changing it to Apache with 25 applications on
nginx quietly sent the next one to Apache while nginx held ports 80 and 443, and nobody was told.
The setting is refused, at the one place every way of changing it passes (``Config.set``), while
applications or sites are still behind the other server, and the refusal says how many are where.
Moving an application from one server to the other is a migration of its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import webserver_switch
from noust.core.config import Config
from noust.core.exceptions import ConfigError, WebserverInUseError
from noust.core.store import App, NoustStore, Site, get_store
from noust.web.api import config as config_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers


@pytest.fixture
def store() -> NoustStore:
    return get_store()


@pytest.fixture(autouse=True)
def sandboxed_config(real_config: object) -> None:
    """Every test here changes the configuration of a sandbox, never the machine's."""


def app(store: NoustStore, domain: str, webserver: str = "nginx") -> None:
    store.create_app(App(domain=domain, app_path=f"/var/www/apps/{domain}", webserver=webserver))


def test_with_applications_behind_the_other_server_the_setting_is_refused_with_the_counts(
    store: NoustStore,
) -> None:
    for number in range(25):
        app(store, f"a{number}.example.com")
    app(store, "z.example.com", "apache")
    config = Config()
    config.set("webserver", "nginx")

    with pytest.raises(WebserverInUseError) as refused:
        config.set("webserver", "apache")

    assert isinstance(refused.value, ConfigError), "the command line catches ConfigError"
    assert "25 applications" in refused.value.message and "nginx" in refused.value.message
    details = refused.value.details or ""
    assert "1 application" in details and "apache" in details
    assert "new applications" in details
    assert config.get("webserver") == "nginx", "nothing was changed"


def test_a_server_with_nothing_behind_the_other_one_may_change(store: NoustStore) -> None:
    config = Config()

    config.set("webserver", "apache")

    assert config.get("webserver") == "apache"


def test_applications_already_behind_the_server_it_is_changed_to_do_not_block_it(
    store: NoustStore,
) -> None:
    app(store, "a.example.com", "apache")
    config = Config()
    config.set("webserver", "nginx")

    config.set("webserver", "apache")

    assert config.get("webserver") == "apache"


def test_saying_what_it_already_is_changes_nothing_and_asks_nothing(store: NoustStore) -> None:
    app(store, "a.example.com", "nginx")
    config = Config()
    config.set("webserver", "nginx")

    config.set("webserver", "nginx")


def test_a_site_nobody_deployed_counts_because_it_holds_the_ports_too(store: NoustStore) -> None:
    store.create_site(
        Site(domain="docs.example.com", webserver="nginx", config_path="/etc/nginx/x")
    )
    config = Config()
    config.set("webserver", "nginx")

    with pytest.raises(WebserverInUseError) as refused:
        config.set("webserver", "apache")

    assert "1 site" in refused.value.message


def test_the_counts_are_one_answer(store: NoustStore) -> None:
    app(store, "a.example.com", "nginx")
    app(store, "b.example.com", "apache")
    store.create_site(Site(domain="docs.example.com", webserver="nginx", config_path="/x"))

    use = webserver_switch.usage()

    assert use.applications == {"nginx": 1, "apache": 1}
    assert use.sites == {"nginx": 1}
    assert use.behind_other_than("apache") == (1, 1)


def test_a_store_that_cannot_be_read_does_not_stop_a_setting_that_only_affects_new_applications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> NoustStore:
        raise OSError("disk")

    monkeypatch.setattr(webserver_switch, "get_store", broken)
    config = Config()

    config.set("webserver", "apache")

    assert config.get("webserver") == "apache"


# -- The command line --------------------------------------------------------------------


def test_the_command_says_what_the_api_says(store: NoustStore) -> None:
    from click.testing import CliRunner

    from noust.cli.app import cli

    app(store, "a.example.com", "nginx")

    result = CliRunner().invoke(cli, ["config", "set", "webserver", "apache"])

    assert result.exit_code == 1
    assert "1 application" in result.output and "nginx" in result.output
    assert "new applications" in result.output


def test_the_command_says_that_the_setting_is_for_new_applications_when_it_goes_through(
    store: NoustStore,
) -> None:
    from click.testing import CliRunner

    from noust.cli.app import cli

    result = CliRunner().invoke(cli, ["config", "set", "webserver", "apache"])

    assert result.exit_code == 0, result.output
    assert "applications already deployed keep" in result.output.lower()


# -- The API -----------------------------------------------------------------------------


@pytest.fixture
def client(sandbox: Path) -> TestClient:
    api = FastAPI()
    install_error_handlers(api)
    api.include_router(config_api.router, prefix="/api/config")
    api.dependency_overrides[get_current_session] = lambda: {"session_id": "test", "type": "master"}
    return TestClient(api, raise_server_exceptions=False)


def test_the_api_refuses_with_a_conflict_that_counts_what_is_where(
    client: TestClient, store: NoustStore
) -> None:
    app(store, "a.example.com", "nginx")
    app(store, "b.example.com", "nginx")

    response = client.put("/api/config/webserver", json={"webserver": "apache"})

    body = response.json()
    assert response.status_code == 409, response.text
    assert body["error"] == "webserver_in_use"
    assert "2 applications" in body["detail"]
    assert "new applications" in body["hint"]


def test_the_api_says_the_setting_is_the_one_for_new_applications(
    client: TestClient, store: NoustStore
) -> None:
    app(store, "a.example.com", "nginx")

    got = client.get("/api/config/webserver").json()

    assert got["webserver"] == "nginx"
    assert got["applies_to"] == "new_applications"
    assert got["applications"] == {"nginx": 1}
    assert got["sites"] == {}


def test_the_api_confirms_in_the_same_words_when_it_changes(
    client: TestClient, store: NoustStore, sandbox: Path
) -> None:
    response = client.put("/api/config/webserver", json={"webserver": "apache"})

    assert response.status_code == 200, response.text
    assert "new applications" in response.json()["message"].lower()
    stored = yaml.safe_load((sandbox / "etc" / "config.yaml").read_text())
    assert stored["webserver"] == "apache"
