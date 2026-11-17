# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The consent to deploy on the machine's Docker is never given from the console or the API.

It is the second of two things asked on purpose, outside the console, for a hub to deploy on its
machine's Docker (the first is the socket mounted into it). What a write of the configuration
does is judged by its result, in the one place every write passes (``Config.set`` and
``Config.replace``), and not by the route that carried it: a key, a section, a whole document.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from noust import central
from noust.core.central_settings import from_the_console, writing_from_the_console
from noust.core.config import Config
from noust.core.exceptions import PermissionError as NoustPermissionError
from noust.core.runner import FakeRunner
from noust.core.server_text import CATALOG
from noust.core.store import NoustStore
from noust.web.server import create_app
from tests.support.web_auth import make_config
from tests.support.web_central import elevate, sign_in


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A configuration read from an empty file in the test's directory."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


def written(tmp_path: Path) -> dict[str, Any]:
    """What config.yaml says now."""
    path = tmp_path / "config.yaml"
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


class TestAWriteFromTheConsoleIsJudgedByItsResult:
    """``Config.set`` and ``Config.replace`` refuse to leave the consent on when it was not."""

    def test_nothing_is_the_console_until_a_request_says_so(self) -> None:
        assert not writing_from_the_console()
        with from_the_console():
            assert writing_from_the_console()
        assert not writing_from_the_console()

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("central.deploy_here", "containers"),
            ("central", {"deploy_here": "containers"}),
            ("central", {"role": "hub", "deploy_here": "containers"}),
            ("central", {"role": "hub", "deploy_here": " Containers "}),
        ],
    )
    def test_a_set_that_leaves_it_on_is_refused_whatever_the_key(
        self, key: str, value: object
    ) -> None:
        config = Config()
        with from_the_console(), pytest.raises(NoustPermissionError) as caught:
            config.set(key, value)

        assert caught.value.coded is not None
        assert caught.value.coded.code == "config.deploy_here_not_from_console"
        assert "compose" in (caught.value.details or "")

    def test_what_was_refused_does_not_stay_in_memory(self) -> None:
        """A process that read the singleton in between would have deployed on the machine."""
        config = Config()
        with from_the_console(), pytest.raises(NoustPermissionError):
            config.set("central", {"deploy_here": "containers", "role": "hub"})

        assert central.deploy_here() == ""
        assert central.role() == "server"
        assert config.get("central.deploy_here") == ""

    def test_a_whole_document_that_turns_it_on_is_refused_and_leaves_the_old_one(self) -> None:
        config = Config()
        document = config.to_dict()
        document["central"] = {**document["central"], "deploy_here": "containers"}

        with from_the_console(), pytest.raises(NoustPermissionError):
            config.replace(document)

        assert config.get("central.deploy_here") == ""

    def test_the_command_line_and_whoever_has_a_shell_still_turn_it_on(
        self, tmp_path: Path
    ) -> None:
        config = Config()

        config.set("central.deploy_here", "containers")
        config.set("central", {"deploy_here": "containers", "role": "hub"})
        config.write()

        assert written(tmp_path)["central"]["deploy_here"] == "containers"

    def test_turning_it_off_and_leaving_it_alone_pass_from_the_console(
        self, tmp_path: Path
    ) -> None:
        config = Config()
        config.set("central.deploy_here", "containers")
        config.write()
        Config.reset_instance()
        config = Config()

        with from_the_console():
            config.set("central.role", "hub")
            config.set("central", {"deploy_here": "containers", "role": "hub"})
            assert config.get("central.deploy_here") == "containers"
            config.set("central.deploy_here", "")

        assert config.get("central.deploy_here") == ""

    def test_the_consent_the_environment_gave_may_be_sent_back_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A save that sends the whole configuration carries the environment's value in it."""
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
        monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
        Config.reset_instance()
        config = Config()

        with from_the_console():
            config.replace(config.to_dict())
            config.set("central", {"role": "hub", "deploy_here": "containers"})

        assert central.deploy_here() == "containers"

    def test_the_sentence_is_in_the_catalog(self) -> None:
        assert "config.deploy_here_not_from_console" in CATALOG
        assert "config.deploy_here_not_from_console.hint" in CATALOG


class TestEveryRouteOfTheConsoleIsInIt:
    """The request is the console: the middleware says so once, no handler has to remember."""

    @pytest.fixture
    def client(self, sandbox: Path, runner: FakeRunner) -> TestClient:
        client = TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))
        elevate(client, sign_in(client))
        return client

    def test_a_patch_of_the_section_is_refused_and_nothing_is_written(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        response = client.patch(
            "/api/config", json={"path": "central", "value": {"deploy_here": "containers"}}
        )

        assert response.status_code == 403, response.text
        assert "compose" in response.json()["hint"]
        assert central.deploy_here() == ""
        assert (written(tmp_path).get("central") or {}).get("deploy_here") in (None, "")

    def test_a_patch_of_the_key_is_refused_as_it_always_was(self, client: TestClient) -> None:
        response = client.patch(
            "/api/config", json={"path": "central.deploy_here", "value": "containers"}
        )

        assert response.status_code == 403, response.text
        assert central.deploy_here() == ""

    def test_a_whole_configuration_that_turns_it_on_is_refused(self, client: TestClient) -> None:
        current = client.get("/api/config").json()["config"]
        current.setdefault("central", {})["deploy_here"] = "containers"

        response = client.put("/api/config", json={"config": current})

        assert response.status_code == 403, response.text
        assert central.deploy_here() == ""

    def test_a_section_that_leaves_it_off_is_not_in_the_way(self, client: TestClient) -> None:
        response = client.patch(
            "/api/config", json={"path": "central", "value": {"role": "server", "deploy_here": ""}}
        )

        assert response.status_code == 200, response.text

    def test_the_refusal_reads_the_same_whichever_way_it_came(self, client: TestClient) -> None:
        by_key = client.patch(
            "/api/config", json={"path": "central.deploy_here", "value": "containers"}
        )
        by_section = client.patch(
            "/api/config", json={"path": "central", "value": {"deploy_here": "containers"}}
        )

        assert by_key.json() == by_section.json()
