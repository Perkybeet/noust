# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
:mod:`noust.core.outbound`: the one guard on where a configured host may send a
request (checklist L-21).

No test resolves a real name: the resolver is replaced, as it is everywhere.
The notifier's own behaviour is pinned by tests/test_notifier.py and
tests/test_security_hardening.py; here is the module those now share.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import outbound
from noust.core.config import Config, ConfigError
from noust.core.net import ALL_INTERFACES
from noust.core.outbound import DestinationRefused


def answers(**table: str):
    """A resolver answering from a table; an unknown name does not resolve."""

    def resolve(host: str) -> tuple[str, ...]:
        if host in table:
            return tuple(table[host].split(","))
        raise OSError("Name or service not known")

    return resolve


class TestWhatIsForbidden:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.9",
            "192.168.1.5",
            "100.64.0.1",
            "169.254.169.254",
            ALL_INTERFACES,
            "::",
            "::1",
            "fd00::1",
            "fe80::1",
            # An IPv4 address in an IPv6 wrapper is judged by the address inside.
            "::ffff:169.254.169.254",
            "64:ff9b::a00:1",
            "2002:7f00:1::",
        ],
    )
    def test_the_machines_own_networks_are_refused(self, address: str) -> None:
        assert outbound.is_forbidden(ipaddress.ip_address(address))

    @pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:4700::1111"])
    def test_a_public_address_is_not(self, address: str) -> None:
        assert not outbound.is_forbidden(ipaddress.ip_address(address))

    def test_a_lan_is_not_among_the_addresses_that_can_never_be_a_destination(self) -> None:
        for address in ("10.0.0.5", "192.168.1.5", "172.20.0.1", "127.0.0.1", "fd00::1"):
            assert not outbound.is_never_a_destination(ipaddress.ip_address(address))

    @pytest.mark.parametrize(
        "address", ["169.254.169.254", ALL_INTERFACES, "::", "fe80::1", "::ffff:169.254.169.254"]
    )
    def test_metadata_and_the_aliases_of_this_machine_never_are(self, address: str) -> None:
        assert outbound.is_never_a_destination(ipaddress.ip_address(address))


class TestRequirePublicHost:
    def test_a_public_name_passes(self) -> None:
        outbound.require_public_host(
            "backup.example.com", resolver=answers(**{"backup.example.com": "93.184.216.34"})
        )

    def test_one_internal_record_among_public_ones_refuses_the_name(self) -> None:
        resolver = answers(**{"mixed.example.com": "93.184.216.34,10.0.0.7"})

        with pytest.raises(DestinationRefused) as raised:
            outbound.require_public_host("mixed.example.com", resolver=resolver)

        assert raised.value.reason == "internal" and raised.value.address == "10.0.0.7"

    def test_a_name_that_rebinds_to_metadata_is_refused(self) -> None:
        with pytest.raises(DestinationRefused, match=re.escape("169.254.169.254")):
            outbound.require_public_host(
                "evil.example.com", resolver=answers(**{"evil.example.com": "169.254.169.254"})
            )

    def test_the_operator_can_name_an_internal_host(self) -> None:
        outbound.require_public_host(
            "NAS.lan", allowed={"nas.lan"}, resolver=answers(**{"NAS.lan": "192.168.1.5"})
        )

    def test_a_name_that_does_not_resolve_is_refused_by_default(self) -> None:
        with pytest.raises(DestinationRefused) as raised:
            outbound.require_public_host("nowhere.example.com", resolver=answers())

        assert raised.value.reason == "unresolved"

    def test_a_destination_configured_while_it_is_down_may_be_accepted(self) -> None:
        outbound.require_public_host("nowhere.example.com", resolver=answers(), unresolved_ok=True)

    def test_a_name_with_no_addresses_is_refused(self) -> None:
        with pytest.raises(DestinationRefused) as raised:
            outbound.require_public_host("empty.example.com", resolver=lambda host: ())

        assert raised.value.reason == "no_address"

    def test_the_narrow_look_lets_a_lan_through_and_stops_metadata(self) -> None:
        lan = answers(**{"nas.lan": "192.168.1.5"})
        metadata = answers(**{"nas.lan": "169.254.169.254"})

        outbound.require_public_host("nas.lan", resolver=lan, narrow=True)
        with pytest.raises(DestinationRefused):
            outbound.require_public_host("nas.lan", resolver=metadata, narrow=True)

    def test_an_empty_host_is_refused(self) -> None:
        with pytest.raises(DestinationRefused):
            outbound.require_public_host("")

    def test_the_default_resolver_is_looked_up_when_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A test (or a tool) that replaces outbound.resolve_host reaches every caller.
        monkeypatch.setattr(outbound, "resolve_host", lambda host: ("10.9.9.9",))

        with pytest.raises(DestinationRefused):
            outbound.require_public_host("anything.example.com")

    def test_an_address_literal_resolves_to_itself(self) -> None:
        assert outbound.resolve_host("93.184.216.34") == ("93.184.216.34",)
        assert outbound.resolve_host("::1") == ("::1",)


@pytest.fixture
def config(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """A configuration of its own, carrying only the defaults."""
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", sandbox / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        yield Config()
    finally:
        Config.reset_instance()


class TestTrustedHosts:
    def test_they_are_read_from_the_setting_and_lowercased(self, config: Config) -> None:
        config.set("security.allow_private_hosts", ["NAS.lan", "10.0.0.5"])

        assert outbound.trusted_hosts(config) == frozenset({"nas.lan", "10.0.0.5"})

    def test_nothing_is_trusted_by_default(self, config: Config) -> None:
        assert outbound.trusted_hosts(config) == frozenset()

    def test_the_setting_takes_names_and_addresses_not_urls(self, config: Config) -> None:
        with pytest.raises(ConfigError, match="not a host name"):
            config.set("security.allow_private_hosts", ["https://nas.lan"])

    def test_a_comma_separated_value_is_a_list(self, config: Config) -> None:
        config.set("security.allow_private_hosts", "nas.lan, 10.0.0.5")

        assert config.get("security.allow_private_hosts") == ["nas.lan", "10.0.0.5"]
