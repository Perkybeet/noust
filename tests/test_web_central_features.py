# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a Noust can do, as the session says it to the console.

``GET /api/auth/session`` carries ``central.features``: for each function of
``noust.central.features`` whether it works here and, when it does not, the sentence for why, with
its code and its English beside it, so the console offers what works and nothing else. The shape is
part of the API (the console's types are generated from it, one property per function), the words
are the ones the guard of a manager says, and a hub's answers are what its managers answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.central.features import FEATURES
from noust.central.machine import MachineFacts
from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.web.server import create_app
from tests.support.web_auth import make_config
from tests.support.web_central import sign_in

#: The Noust central container of a NAS: nothing of the machine a server has.
CONTAINER = MachineFacts(systemd=False, web_server=False, docker=False, root=False, journal=False)


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """This test's own configuration and store."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def client(sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def hub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    Config.reset_instance()


def features_of(client: TestClient) -> dict[str, Any]:
    sign_in(client)
    body = client.get("/api/auth/session").json()
    return body["central"]["features"]


class TestTheSessionSaysWhatWorksHere:
    def test_a_server_on_an_ordinary_machine_can_do_everything(self, client: TestClient) -> None:
        features = features_of(client)

        assert set(features) == set(FEATURES)
        assert all(entry == {"available": True, "reason": None} for entry in features.values())

    def test_a_hub_says_why_with_the_words_of_the_refusal_its_managers_give(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hub(monkeypatch)
        features = features_of(client)
        sign_in(client)

        refusal = client.get("/api/services")

        assert features["services"]["available"] is False
        reason = features["services"]["reason"]
        assert reason["code"] == "features.hub_role"
        assert reason["params"] == {
            "feature": "Services",
            "command": "noust config set central.role server",
        }
        assert refusal.status_code == 409
        assert refusal.json()["error"] == "hub_role"
        assert reason["text"] == refusal.json()["detail"]

    def test_a_hub_keeps_what_its_own_machine_does(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hub(monkeypatch)

        features = features_of(client)

        for key in ("system_clock", "system_logs", "power", "root_shell"):
            assert features[key] == {"available": True, "reason": None}, key

    def test_a_machine_without_systemd_or_a_journal_says_which(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.central.machine.read_machine_facts", lambda *args, **kwargs: CONTAINER
        )

        features = features_of(client)

        assert features["system_clock"]["reason"]["code"] == "features.needs_systemd"
        assert features["system_logs"]["reason"]["code"] == "features.needs_journal"
        assert features["root_shell"]["reason"]["code"] == "features.needs_root"
        assert features["sites"]["reason"]["code"] == "features.needs_web_server"
        # No Docker is said by the engine's doctor, in its own words.
        assert features["volume_backups"]["reason"]["code"] == "docker.engine.missing"
        assert features["volume_backups"]["reason"]["hint"]

    def test_a_machine_that_cannot_be_read_does_not_keep_the_console_from_starting(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unreadable(*args: object, **kwargs: object) -> MachineFacts:
            raise OSError("too many open files")

        monkeypatch.setattr("noust.central.machine.read_machine_facts", unreadable)
        sign_in(client)

        body = client.get("/api/auth/session").json()

        assert body["authenticated"] is True
        assert body["central"]["role"] == "server"
        assert body["central"]["features"] is None

    def test_an_anonymous_caller_is_told_nothing_of_it(self, client: TestClient) -> None:
        assert client.get("/api/auth/session").json()["central"] is None


class TestTheShapeIsPartOfTheApi:
    def schemas(self, client: TestClient) -> dict[str, Any]:
        return client.app.openapi()["components"]["schemas"]  # type: ignore[attr-defined]

    def test_each_function_is_a_property_so_the_console_cannot_name_one_that_is_not_there(
        self, client: TestClient
    ) -> None:
        schemas = self.schemas(client)

        states = schemas["FeatureStates"]
        assert set(states["properties"]) == set(FEATURES)
        assert set(states["required"]) == set(FEATURES)
        assert all(
            prop["$ref"] == "#/components/schemas/FeatureState"
            for prop in states["properties"].values()
        )

    def test_a_state_is_available_or_not_with_a_reason_that_is_a_sentence_with_a_code(
        self, client: TestClient
    ) -> None:
        schemas = self.schemas(client)

        assert set(schemas["FeatureState"]["properties"]) == {"available", "reason"}
        assert set(schemas["FeatureReason"]["properties"]) == {"code", "params", "text", "hint"}

    def test_the_session_carries_them_and_the_unlock_answer_does_not_change(
        self, client: TestClient
    ) -> None:
        schemas = self.schemas(client)

        assert "features" in schemas["SessionCentral"]["properties"]
        assert set(schemas["CentralInfo"]["properties"]) == {"role", "sealed", "locked"}
