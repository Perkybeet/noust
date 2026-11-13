# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The built-in assistant's settings: off until turned on, a provider judged when it is chosen, a key
that is sealed and never given back, and limits that always have a number (item 171).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import outbound
from noust.core.assistant.client import ProviderError
from noust.core.assistant.limits import Limits, Price
from noust.core.assistant.settings import AssistantKeyError, AssistantSettings
from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.fs import RealFileSystem, set_fs
from noust.core.store import NoustStore

KEY = "sk-provider-9d8c7b6a5f4e3d2c1b0a"
PUBLIC = "203.0.113.9"


@pytest.fixture(autouse=True)
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A configuration file and a key beside it that belong to the test, on the real filesystem."""
    target = tmp_path / "etc" / "config.yaml"
    target.parent.mkdir()
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", target)
    set_fs(RealFileSystem())
    Config.reset_instance()
    yield target
    Config.reset_instance()
    set_fs(None)


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
def settings(store: NoustStore) -> AssistantSettings:
    return AssistantSettings(store)


def resolve(host: str) -> tuple[str, ...]:
    """What the names of these tests resolve to, with no DNS."""
    if host == "localhost":
        return ("127.0.0.1",)
    if host == "gpu-box.lan":
        return ("192.168.1.20",)
    return (PUBLIC,)


@pytest.fixture(autouse=True)
def names(monkeypatch: pytest.MonkeyPatch) -> None:
    """``localhost`` is this machine, ``gpu-box.lan`` a host of the network, the rest public."""
    monkeypatch.setattr(outbound, "resolve_host", resolve)


class TestOffByDefault:
    def test_nothing_is_configured_and_it_is_off(self, settings: AssistantSettings) -> None:
        read = settings.read()
        assert read.enabled is False
        assert read.configured is False
        assert read.has_key is False
        assert read.limits == Limits()
        assert read.prices == {}
        assert read.mask_personal_effective is True

    def test_reading_writes_nothing(self, store: NoustStore, settings: AssistantSettings) -> None:
        settings.read()
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM assistant_settings")
            assert cursor.fetchone()[0] == 0


class TestChoosingAProvider:
    def test_a_preset_fills_the_address_and_says_where_the_data_goes(
        self, settings: AssistantSettings
    ) -> None:
        read = settings.update(preset="mistral", model="mistral-small-latest", actor="ana")
        assert read.base_url == "https://api.mistral.ai/v1"
        assert read.location == "eu"
        assert read.updated_by == "ana"
        assert read.configured is True

    def test_an_address_of_ones_own_needs_the_operators_consent_to_be_called(
        self, settings: AssistantSettings
    ) -> None:
        with pytest.raises(ProviderError) as caught:
            settings.update(preset="ollama", model="llama3", actor="ana")
        assert caught.value.code == "assistant.provider.destination_refused"
        assert settings.read().configured is False

    def test_the_consent_lists_the_host_and_the_place_is_this_machine(
        self, settings: AssistantSettings
    ) -> None:
        Config().set("security.allow_private_hosts", ["nas.example.net"])
        read = settings.update(
            preset="ollama", model="llama3", actor="ana", allow_private_host=True
        )
        assert read.location == "machine"
        assert Config().get("security.allow_private_hosts") == ["nas.example.net", "localhost"]

    def test_a_host_on_the_network_is_the_operators_network(
        self, settings: AssistantSettings
    ) -> None:
        read = settings.update(
            base_url="http://gpu-box.lan:8000/v1",
            model="qwen",
            actor="ana",
            allow_private_host=True,
        )
        assert read.location == "network"

    def test_a_public_address_cannot_be_declared_to_be_this_machine(
        self, settings: AssistantSettings
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            settings.update(
                base_url="https://models.example.org/v1", model="m", actor="a", location="machine"
            )
        assert caught.value.field == "location"

    def test_a_provider_the_operator_has_a_contract_with_may_be_declared_european(
        self, settings: AssistantSettings
    ) -> None:
        read = settings.update(
            base_url="https://models.example.org/v1", model="m", actor="a", location="eu"
        )
        assert read.location == "eu"

    def test_an_unnamed_public_provider_is_not_said_to_be_anywhere(
        self, settings: AssistantSettings
    ) -> None:
        read = settings.update(base_url="https://models.example.org/v1", model="m", actor="a")
        assert read.location == "unknown"

    @pytest.mark.parametrize("model", ["", "  ", "two words", "a\nb", "x" * 201])
    def test_a_model_name_is_one_word_of_reasonable_length(
        self, settings: AssistantSettings, model: str
    ) -> None:
        with pytest.raises(ValidationError):
            settings.update(base_url="https://models.example.org/v1", model=model, actor="a")

    def test_what_is_not_changed_stays(self, settings: AssistantSettings) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a", api_key=KEY)
        read = settings.update(model="other", actor="b")
        assert read.base_url == "https://models.example.org/v1"
        assert read.has_key is True
        assert read.updated_by == "b"

    def test_a_change_of_provider_forgets_what_the_probe_found(
        self, settings: AssistantSettings
    ) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a")
        settings.record_probe({"reachable": True, "tools": True})
        assert settings.read().probe == {"reachable": True, "tools": True}
        settings.update(model="other", actor="a")
        assert settings.read().probe == {}
        assert settings.read().probed_at is None


class TestTheKey:
    def test_it_is_sealed_in_the_store_and_opens_only_through_the_one_door(
        self, store: NoustStore, settings: AssistantSettings
    ) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a", api_key=KEY)
        with store._transaction() as cursor:
            cursor.execute("SELECT api_key FROM assistant_settings")
            stored = cursor.fetchone()[0]
        assert stored.startswith("noust-assistant:v1:")
        assert KEY not in stored
        assert settings.api_key() == KEY

    def test_what_is_read_says_that_there_is_one_and_when_and_never_what_it_is(
        self, settings: AssistantSettings
    ) -> None:
        read = settings.update(
            base_url="https://models.example.org/v1", model="m", actor="a", api_key=KEY
        )
        assert read.has_key is True
        assert read.key_set_at is not None
        assert KEY not in repr(read)
        assert KEY not in str(read.to_public())
        assert "api_key" not in read.to_public()

    def test_an_empty_key_takes_it_away(self, settings: AssistantSettings) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a", api_key=KEY)
        read = settings.update(api_key="", actor="a")
        assert read.has_key is False
        assert read.key_set_at is None
        assert settings.api_key() == ""

    @pytest.mark.parametrize("key", ["short", "has space in it 123456", "a\nb123456789", "k" * 513])
    def test_a_key_is_one_word_of_reasonable_length(
        self, settings: AssistantSettings, key: str
    ) -> None:
        with pytest.raises(ValidationError):
            settings.update(
                base_url="https://models.example.org/v1", model="m", actor="a", api_key=key
            )

    def test_a_key_moved_to_another_row_or_altered_is_refused_not_read(
        self, store: NoustStore, settings: AssistantSettings
    ) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a", api_key=KEY)
        with store._transaction() as cursor:
            cursor.execute("SELECT api_key FROM assistant_settings")
            stored = cursor.fetchone()[0]
            cursor.execute("UPDATE assistant_settings SET api_key = ?", (stored[:-4] + "AAAA",))
        with pytest.raises(AssistantKeyError) as caught:
            settings.api_key()
        assert KEY not in str(caught.value)

    def test_the_key_is_not_in_the_configuration_file(self, settings: AssistantSettings) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a", api_key=KEY)
        assert KEY not in str(Config().to_dict())


class TestPersonalData:
    def test_it_is_always_disguised_for_a_provider_outside(
        self, settings: AssistantSettings
    ) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a")
        with pytest.raises(ValidationError) as caught:
            settings.update(mask_personal=False, actor="a")
        assert caught.value.field == "mask_personal"
        assert settings.read().mask_personal_effective is True

    def test_it_may_be_off_for_a_model_of_ones_own(self, settings: AssistantSettings) -> None:
        settings.update(
            preset="ollama", model="llama3", actor="a", allow_private_host=True, mask_personal=False
        )
        read = settings.read()
        assert read.mask_personal_effective is False

    def test_moving_to_a_provider_outside_disguises_it_again(
        self, settings: AssistantSettings
    ) -> None:
        settings.update(
            preset="ollama", model="llama3", actor="a", allow_private_host=True, mask_personal=False
        )
        read = settings.update(base_url="https://models.example.org/v1", actor="a")
        assert read.mask_personal_effective is True


class TestLimitsAndPrices:
    def test_a_limit_is_changed_and_the_others_stay(self, settings: AssistantSettings) -> None:
        read = settings.update(limits={"steps_per_turn": 12}, actor="a")
        assert read.limits.steps_per_turn == 12
        assert read.limits.actions_per_hour == 10
        read = settings.update(limits={"spend_per_month": 50}, actor="a")
        assert read.limits.steps_per_turn == 12
        assert read.limits.spend_per_month == 50

    def test_a_limit_that_is_no_limit_is_refused_and_nothing_is_written(
        self, settings: AssistantSettings
    ) -> None:
        with pytest.raises(ValidationError):
            settings.update(limits={"steps_per_turn": 0}, actor="a")
        assert settings.read().limits == Limits()

    def test_prices_are_written_by_the_operator_in_their_currency(
        self, settings: AssistantSettings
    ) -> None:
        read = settings.update(
            prices={"gpt-x": {"input_per_million": 0.5, "output_per_million": 1.5}},
            currency="EUR",
            actor="a",
        )
        assert read.prices == {"gpt-x": Price(0.5, 1.5)}
        assert read.currency == "EUR"

    @pytest.mark.parametrize(
        "prices",
        [
            {"m": {"input_per_million": -1, "output_per_million": 1}},
            {"m": {"input_per_million": 1}},
            {"": {"input_per_million": 1, "output_per_million": 1}},
            {"two words": {"input_per_million": 1, "output_per_million": 1}},
        ],
    )
    def test_a_price_that_means_nothing_is_refused(
        self, settings: AssistantSettings, prices: dict[str, dict[str, float]]
    ) -> None:
        with pytest.raises(ValidationError):
            settings.update(prices=prices, actor="a")

    @pytest.mark.parametrize("currency", ["", "two words", "TOOLONGCUR", "a\nb"])
    def test_a_currency_is_a_short_word(self, settings: AssistantSettings, currency: str) -> None:
        with pytest.raises(ValidationError):
            settings.update(currency=currency, actor="a")


class TestTurningItOnAndOff:
    def configured(self, settings: AssistantSettings) -> None:
        settings.update(base_url="https://models.example.org/v1", model="m", actor="a")

    def test_it_cannot_be_turned_on_before_a_provider_is_chosen(
        self, settings: AssistantSettings
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            settings.enable(actor="a")
        assert "provider" in caught.value.message.lower()

    def test_it_cannot_be_turned_on_before_a_probe_found_the_provider_answering(
        self, settings: AssistantSettings
    ) -> None:
        self.configured(settings)
        with pytest.raises(ValidationError) as caught:
            settings.enable(actor="a")
        assert "probe" in caught.value.message.lower()
        settings.record_probe({"reachable": False, "tools": None})
        with pytest.raises(ValidationError):
            settings.enable(actor="a")

    def test_after_a_probe_that_answered_it_is_turned_on_and_off(
        self, settings: AssistantSettings
    ) -> None:
        self.configured(settings)
        settings.record_probe({"reachable": True, "tools": False})
        assert settings.enable(actor="ana").enabled is True
        assert settings.read().updated_by == "ana"
        assert settings.disable(actor="bea").enabled is False
        assert settings.read().updated_by == "bea"

    def test_turning_off_a_thing_never_on_is_fine(self, settings: AssistantSettings) -> None:
        assert settings.disable(actor="a").enabled is False

    def test_it_is_not_turned_on_by_a_provider_that_would_now_be_refused(
        self, settings: AssistantSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.configured(settings)
        settings.record_probe({"reachable": True, "tools": True})
        monkeypatch.setattr(outbound, "resolve_host", lambda host: ("10.0.0.7",))
        with pytest.raises(ProviderError):
            settings.enable(actor="a")

    def test_what_the_provider_can_do_is_what_the_probe_found(
        self, settings: AssistantSettings
    ) -> None:
        self.configured(settings)
        assert settings.read().calls_tools is False
        settings.record_probe({"reachable": True, "tools": True})
        assert settings.read().calls_tools is True
        settings.record_probe({"reachable": True, "tools": False})
        assert settings.read().calls_tools is False


def test_the_store_holds_one_row_at_most(store: NoustStore, settings: AssistantSettings) -> None:
    settings.update(base_url="https://models.example.org/v1", model="m", actor="a")
    settings.update(model="n", actor="a")
    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM assistant_settings")
        assert cursor.fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO assistant_settings (id, updated_at) VALUES (2, '2026-01-01')"
            )
