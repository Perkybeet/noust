# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the provider's key goes, and what the consent to call a private host is worth (item 171).

The key was given for a provider. Whoever may change the settings, which is not whoever may read the key, points the
assistant somewhere else, and the next request carries what the settings hold: so a request that changes the
provider's origin and brings no key of its own takes the key away, and the assistant stays off until a key is given.
And the consent that says "this server may call that address on my network" is the assistant's own: it opens
nothing for any other feature, and it is not the area of the security settings.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.config import Config
from noust.web.server import get_token_manager
from tests.support.assistant_lab import GOOD_PROBE, KEY, Lab, build_lab
from tests.support.scripted_model import ScriptedModel

pytestmark = pytest.mark.allow_sockets


@pytest.fixture(autouse=True)
def cheap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def real_config(real_config: Any) -> Any:
    return real_config


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
        yield scripted


@pytest.fixture
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)


class TestTheKeyDoesNotFollowTheAddress:
    def test_a_new_address_without_a_key_sends_the_provider_nothing_it_was_given(
        self, lab: Lab
    ) -> None:
        lab.configure()
        with ScriptedModel(models=["tiny"], api_key="") as elsewhere:
            elsewhere.script(GOOD_PROBE)
            lab.marta.elevate()

            response = lab.marta.put(
                "/api/assistant/settings",
                {"base_url": elsewhere.base_url, "model": "tiny", "allow_private_host": True},
            )

            assert response.status_code == 200, response.text
            received = [r.headers.get("authorization") for r in elsewhere.requests]
        assert f"Bearer {KEY}" not in received
        assert KEY not in elsewhere.everything_received()
        settings = response.json()
        assert (settings["has_key"], settings["enabled"]) == (False, False)
        assert lab.marta.get("/api/assistant/settings").json()["has_key"] is False

    def test_the_key_is_said_to_be_gone_on_the_audit_trail(self, lab: Lab) -> None:
        lab.configure()
        with ScriptedModel(models=["tiny"], api_key="") as elsewhere:
            lab.marta.elevate()
            lab.marta.put(
                "/api/assistant/settings",
                {
                    "base_url": elsewhere.base_url,
                    "model": "tiny",
                    "allow_private_host": True,
                    "probe": False,
                },
            )

        changed = [e for e in lab.audit_events() if e.get("action") == "assistant.settings"]
        assert changed[-1]["details"]["dropped"] == "the provider key"
        turned_off = [e for e in lab.audit_events() if e.get("action") == "assistant.disable"]
        assert turned_off

    def test_a_conversation_that_was_under_way_is_told_the_assistant_is_off(self, lab: Lab) -> None:
        lab.configure()
        conversation = lab.conversation()
        with ScriptedModel(models=["tiny"], api_key="") as elsewhere:
            lab.marta.elevate()
            lab.marta.put(
                "/api/assistant/settings",
                {
                    "base_url": elsewhere.base_url,
                    "model": "tiny",
                    "allow_private_host": True,
                    "probe": False,
                },
            )

        refused = lab.begin(conversation, "still there?")

        assert refused.status_code == 403
        assert refused.json()["coded"]["code"] == "assistant.unavailable.off"

    def test_a_new_key_in_the_same_request_is_kept_and_used(self, lab: Lab) -> None:
        lab.configure()
        with ScriptedModel(models=["tiny"], api_key="sk-another-9d8c7b6a5f4e") as elsewhere:
            elsewhere.script(GOOD_PROBE)
            lab.marta.elevate()

            response = lab.marta.put(
                "/api/assistant/settings",
                {
                    "base_url": elsewhere.base_url,
                    "model": "tiny",
                    "api_key": "sk-another-9d8c7b6a5f4e",
                    "allow_private_host": True,
                },
            )

            assert response.status_code == 200, response.text
            assert response.json()["has_key"] is True


class TestTheConsentIsTheAssistantsOwn:
    def settings_only_token(self, lab: Lab) -> dict[str, str]:
        issued = get_token_manager().create_api_token(
            "settings-only",
            "admin",
            None,
            owner=lab.marta.account,
            permissions=["settings.manage", "settings.read"],
            allow_elevated=True,
        )
        return {"Authorization": f"Bearer {issued['token']}"}

    def test_it_does_not_write_the_list_of_the_security_area(self, lab: Lab) -> None:
        headers = self.settings_only_token(lab)
        direct = lab.client.patch(
            "/api/config",
            headers=headers,
            json={"path": "security.allow_private_hosts", "value": ["127.0.0.77"]},
        )

        through = lab.client.put(
            "/api/assistant/settings",
            headers=headers,
            json={
                "base_url": "http://127.0.0.77:9/v1",
                "model": "tiny",
                "allow_private_host": True,
                "probe": False,
            },
        )

        assert direct.status_code == 403
        assert through.status_code == 200, through.text
        assert through.json()["private_host"] == "127.0.0.77:9"
        assert not (Config().get("security.allow_private_hosts") or [])

    def test_it_is_said_in_the_settings_so_the_console_can_ask_for_it_again(self, lab: Lab) -> None:
        lab.configure()

        settings = lab.marta.get("/api/assistant/settings").json()

        assert settings["private_host"].startswith("127.0.0.1:")

    def test_turning_it_off_takes_the_consent_with_it(self, lab: Lab) -> None:
        lab.configure()
        lab.marta.elevate()

        off = lab.marta.put("/api/assistant/settings", {"enabled": False})
        again = lab.marta.put("/api/assistant/settings", {"enabled": True})

        assert off.json()["private_host"] == ""
        assert again.status_code == 400
        assert again.json()["coded"]["code"] == "assistant.provider.destination_refused"
        lab.marta.elevate()
        with_consent = lab.marta.put(
            "/api/assistant/settings", {"enabled": True, "allow_private_host": True}
        )
        assert with_consent.status_code == 200, with_consent.text
        assert with_consent.json()["enabled"] is True
