# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
GET/PUT /api/config/email-api: the provider the monitor's mail goes through (checklist K-06).

The same promises as the SMTP form: no credential is ever a field of an answer,
a blank one keeps what is stored (only for the same provider), and the rules of
``noust config set monitor.email_api.*`` hold here in the same words.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from noust.core.config import Config
from tests.test_web_config_api import client, config_path, stored_value  # noqa: F401

# ruff: noqa: F811


def put(client: TestClient, **fields: str):
    body = {
        "provider": "resend",
        "from_address": "alerts@example.com",
        "api_key": "re_live_secret_key_123",
        "region": "",
        "access_key_id": "",
        "secret_access_key": "",
    }
    body.update(fields)
    return client.put("/api/config/email-api", json=body)


class TestReading:
    def test_a_fresh_server_has_no_provider(self, client: TestClient) -> None:
        body = client.get("/api/config/email-api").json()

        assert body["provider"] == ""
        assert body["providers"] == ["resend", "postmark", "brevo", "ses"]
        assert body["configured"] is False
        assert (body["api_key_set"], body["secret_access_key_set"]) == (False, False)

    def test_no_credential_is_a_field_of_the_answer(
        self, client: TestClient, config_path: Path
    ) -> None:
        assert put(client).status_code == 200

        response = client.get("/api/config/email-api")

        assert "re_live_secret_key_123" not in response.text
        assert set(response.json()) == {
            "provider",
            "providers",
            "from_address",
            "region",
            "api_key_set",
            "access_key_id_set",
            "secret_access_key_set",
            "recipients",
            "configured",
        }
        assert response.json()["api_key_set"] is True


class TestWriting:
    def test_it_stores_the_provider_and_is_configured_with_recipients(
        self, client: TestClient, config_path: Path
    ) -> None:
        config = Config()
        config.set("monitor.email_recipients", ["ops@example.com"])
        assert config.save() is True
        Config.reset_instance()

        response = put(client)

        assert response.status_code == 200, response.text
        assert stored_value(config_path, "monitor.email_api.provider") == "resend"
        assert stored_value(config_path, "monitor.email_api.api_key") == "re_live_secret_key_123"
        assert client.get("/api/config/email-api").json()["configured"] is True

    def test_a_blank_key_keeps_the_stored_one_for_the_same_provider(
        self, client: TestClient, config_path: Path
    ) -> None:
        put(client)

        again = put(client, api_key="", from_address="other@example.com")

        assert again.status_code == 200, again.text
        assert stored_value(config_path, "monitor.email_api.api_key") == "re_live_secret_key_123"
        assert stored_value(config_path, "monitor.email_api.from_address") == "other@example.com"

    def test_a_blank_key_is_cleared_when_the_provider_changes(
        self, client: TestClient, config_path: Path
    ) -> None:
        put(client)

        changed = put(client, provider="postmark", api_key="")

        assert changed.status_code == 200, changed.text
        assert stored_value(config_path, "monitor.email_api.provider") == "postmark"
        assert stored_value(config_path, "monitor.email_api.api_key") == ""

    def test_amazon_ses_keeps_its_two_credentials_apart(
        self, client: TestClient, config_path: Path
    ) -> None:
        first = put(
            client,
            provider="ses",
            api_key="",
            region="eu-west-1",
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        assert first.status_code == 200, first.text

        again = put(
            client,
            provider="ses",
            api_key="",
            region="us-east-1",
            access_key_id="",
            secret_access_key="",
        )

        assert again.status_code == 200, again.text
        assert stored_value(config_path, "monitor.email_api.region") == "us-east-1"
        assert (
            stored_value(config_path, "monitor.email_api.access_key_id") == "AKIAIOSFODNN7EXAMPLE"
        )
        body = client.get("/api/config/email-api").json()
        assert (body["access_key_id_set"], body["secret_access_key_set"]) == (True, True)

    def test_choosing_none_goes_back_to_smtp(self, client: TestClient, config_path: Path) -> None:
        put(client)

        back = put(client, provider="", api_key="")

        assert back.status_code == 200, back.text
        assert stored_value(config_path, "monitor.email_api.provider") == ""

    def test_the_rules_of_the_configuration_hold_in_the_same_words(
        self, client: TestClient
    ) -> None:
        assert put(client, provider="mailgun").status_code == 400
        assert put(client, provider="ses", region="evil.example.test/").status_code == 400
        assert put(client, from_address="not an address").status_code == 400
