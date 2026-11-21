# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the consent to call a model of one's own does not reach (item 171).

Whoever may change the assistant's settings consents, with that permission, to this server calling a provider that
is on this machine or on the operator's network, and no more than the settings say. That consent is for a model:
it does not reach the addresses that are never a provider (link-local, where a cloud's metadata service answers,
and the aliases of this machine) nor the ports of the services a machine runs for itself (the container engine,
the databases, a queue, remote access, Noust's own console). The list of the security area, which another
permission writes on purpose, is the operator's decision and is not narrowed by this.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import outbound
from noust.core.assistant.client import ProviderError, check_address
from noust.core.config import Config
from tests.support.assistant_lab import KEY, Lab, build_lab
from tests.support.scripted_model import ScriptedModel


def resolve(*addresses: str):  # type: ignore[no-untyped-def]
    return lambda _host: addresses


def refused(address: str, host: str, *resolved: str) -> str:
    with pytest.raises(ProviderError) as caught:
        check_address(
            address, allowed_hosts=[], consented=[host], resolver=resolve(*resolved or (host,))
        )
    return caught.value.code


@pytest.fixture(autouse=True)
def this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    # What the machine's own interfaces say is not what is asked here.
    monkeypatch.setattr(outbound, "own_addresses", lambda: frozenset({"192.168.1.5", "fd00::5"}))


class TestWhatIsNeverAProvider:
    @pytest.mark.parametrize(
        ("address", "host"),
        [
            ("http://169.254.169.254/latest/meta-data", "169.254.169.254"),
            ("http://169.254.0.1:11434/v1", "169.254.0.1"),
            ("http://[fe80::1]:11434/v1", "fe80::1"),
            ("http://[::ffff:169.254.169.254]/v1", "::ffff:169.254.169.254"),
            ("http://[64:ff9b::a9fe:a9fe]/v1", "64:ff9b::a9fe:a9fe"),
            ("http://0.0.0.0:11434/v1", "0.0.0.0"),  # noqa: S104 - the address that is judged, not one that is bound
            ("http://[::]:11434/v1", "::"),
        ],
    )
    def test_a_consent_to_it_is_refused(self, address: str, host: str) -> None:
        assert refused(address, host) == "assistant.provider.never_a_provider"

    def test_nor_does_a_name_that_resolves_to_it(self) -> None:
        code = refused("http://models.lan:11434/v1", "models.lan", "10.0.0.5", "169.254.169.254")

        assert code == "assistant.provider.never_a_provider"

    def test_without_the_consent_it_is_the_refusal_it_always_was(self) -> None:
        with pytest.raises(ProviderError) as caught:
            check_address(
                "http://169.254.169.254/v1", allowed_hosts=[], resolver=resolve("169.254.169.254")
            )

        assert caught.value.code == "assistant.provider.destination_refused"

    def test_the_list_of_the_security_area_is_the_operators_own_decision(self) -> None:
        endpoint = check_address(
            "http://169.254.169.254/v1",
            allowed_hosts=["169.254.169.254"],
            resolver=resolve("169.254.169.254"),
        )

        assert endpoint.addresses == ("169.254.169.254",)


class TestThePortsOfTheMachinesOwnServices:
    @pytest.mark.parametrize(
        ("address", "host", "resolved"),
        [
            ("http://127.0.0.1:2375/v1", "127.0.0.1", "127.0.0.1"),
            ("http://localhost:5432/v1", "localhost", "127.0.0.1"),
            ("http://[::1]:6379/v1", "::1", "::1"),
            ("http://127.0.0.2:22/v1", "127.0.0.2", "127.0.0.2"),
            ("http://[::ffff:127.0.0.1]:3306/v1", "::ffff:127.0.0.1", "::ffff:127.0.0.1"),
            ("http://192.168.1.5:2375/v1", "192.168.1.5", "192.168.1.5"),
            ("http://[fd00::5]:9200/v1", "fd00::5", "fd00::5"),
            ("http://127.0.0.1:27017/v1", "127.0.0.1", "127.0.0.1"),
        ],
    )
    def test_a_consent_to_one_is_refused(self, address: str, host: str, resolved: str) -> None:
        assert refused(address, host, resolved) == "assistant.provider.not_a_model"

    def test_nor_is_the_console_of_this_server_a_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            Config, "get", lambda self, key, default=None: 8443 if key == "web.port" else default
        )

        assert refused("http://127.0.0.1:8443/v1", "127.0.0.1") == "assistant.provider.not_a_model"

    @pytest.mark.parametrize(
        ("address", "host"),
        [
            ("http://localhost:11434/v1", "localhost"),
            ("http://127.0.0.1:1234/v1", "127.0.0.1"),
            ("http://127.0.0.1:8000/v1", "127.0.0.1"),
            ("http://[::1]:8081/v1", "::1"),
            ("http://127.0.0.1:41873/v1", "127.0.0.1"),
            ("http://192.168.1.5:11434/v1", "192.168.1.5"),
        ],
    )
    def test_a_model_on_this_machine_is_still_consented_to(self, address: str, host: str) -> None:
        resolved = "127.0.0.1" if host == "localhost" else host
        endpoint = check_address(
            address, allowed_hosts=[], consented=[host], resolver=resolve(resolved)
        )

        assert endpoint.host == host

    @pytest.mark.parametrize(
        ("address", "host"),
        [
            ("http://10.0.0.5:8080/v1", "10.0.0.5"),
            ("http://10.0.0.5:2375/v1", "10.0.0.5"),
            ("http://192.168.1.77:11434/v1", "192.168.1.77"),
            ("http://[fd00::77]:11434/v1", "fd00::77"),
            ("http://100.64.0.9:11434/v1", "100.64.0.9"),
        ],
    )
    def test_a_machine_of_the_operators_network_is_still_consented_to(
        self, address: str, host: str
    ) -> None:
        # The ports of another machine are not this machine's own: the operator names it, and the network
        # is theirs to answer for.
        endpoint = check_address(
            address, allowed_hosts=[], consented=[host], resolver=resolve(host)
        )

        assert endpoint.host == host

    def test_a_service_port_on_a_public_address_is_not_a_service_of_this_machine(self) -> None:
        endpoint = check_address(
            "https://models.example.org:6379/v1",
            allowed_hosts=[],
            consented=["models.example.org"],
            resolver=resolve("203.0.113.9"),
        )

        assert endpoint.port == 6379

    def test_the_list_of_the_security_area_may_name_a_port_of_its_own(self) -> None:
        endpoint = check_address(
            "http://127.0.0.1:2375/v1", allowed_hosts=["127.0.0.1"], resolver=resolve("127.0.0.1")
        )

        assert endpoint.port == 2375


class TestTheRuleLivesWhereTheListsAre:
    def test_what_may_be_consented_is_asked_of_the_outbound_module(self) -> None:
        outbound.require_consentable("127.0.0.1", 11434, ("127.0.0.1",))
        with pytest.raises(outbound.DestinationRefused) as never:
            outbound.require_consentable("169.254.169.254", 80, ("169.254.169.254",))
        with pytest.raises(outbound.DestinationRefused) as service:
            outbound.require_consentable("127.0.0.1", 2375, ("127.0.0.1",))
        assert never.value.reason == "never"
        assert service.value.reason == "service"

    def test_every_port_of_the_table_is_a_port(self) -> None:
        assert all(0 < port < 65536 for port in outbound.SERVICE_PORTS)
        assert 2375 in outbound.SERVICE_PORTS and 22 in outbound.SERVICE_PORTS
        # What a model's server is commonly found on is never in the table: that is the point of the consent.
        assert not {11434, 1234, 8000, 8080, 8081, 5000, 1337, 3000}.intersection(
            outbound.SERVICE_PORTS
        )

    def test_the_networks_it_names_are_the_ones_that_are_never_a_destination(self) -> None:
        for network in outbound.NEVER_A_DESTINATION:
            assert isinstance(network, (ipaddress.IPv4Network, ipaddress.IPv6Network))


@pytest.fixture
def real_config(real_config: Any) -> Any:
    return real_config


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
        yield scripted


@pytest.fixture
def lab(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel, real_config: Any
) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)


@pytest.mark.allow_sockets
class TestTheSettingsDoNotTakeIt:
    @pytest.mark.parametrize(
        ("address", "code"),
        [
            ("http://169.254.169.254/v1", "assistant.provider.never_a_provider"),
            ("http://[fe80::1]:11434/v1", "assistant.provider.never_a_provider"),
            ("http://127.0.0.1:2375/v1", "assistant.provider.not_a_model"),
        ],
    )
    def test_a_consent_to_what_is_never_a_provider_is_a_refusal_that_says_so(
        self, lab: Lab, address: str, code: str
    ) -> None:
        lab.marta.elevate()

        refused = lab.marta.put(
            "/api/assistant/settings",
            {"base_url": address, "model": "tiny", "api_key": KEY, "allow_private_host": True},
        )

        assert refused.status_code == 400, refused.text
        assert refused.json()["coded"]["code"] == code
        assert lab.marta.get("/api/assistant/settings").json()["private_host"] == ""
        assert "169.254.169.254" not in (Config().get("security.allow_private_hosts") or [])

    def test_the_model_of_the_lab_is_still_consented_to(self, lab: Lab) -> None:
        lab.configure()

        assert (
            lab.marta.get("/api/assistant/settings").json()["private_host"].startswith("127.0.0.1:")
        )
