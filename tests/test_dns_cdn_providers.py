# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS check knows Bunny, Fastly and ArvanCloud as well as Cloudflare (the gap the parity audit
found beside E-39).

An application behind Bunny or Fastly read as ``elsewhere``: only Cloudflare's addresses made a name
``cdn``. A CDN that proxies a name reaches this server for the visitor and for the certificate
authority, so the check calls it a CDN and says which one.
"""

from __future__ import annotations

import ipaddress

import pytest

from noust.core import cdn_networks
from noust.deployers.domains import check_dns

SERVER = ["93.184.216.34"]


def resolver(*addresses: str):
    def resolve(name: str, port: object, type: int = 0) -> list[tuple[object, ...]]:
        return [(2, 1, 6, "", (address, 0)) for address in addresses]

    return resolve


def checked(*addresses: str):
    return check_dns(
        "shop.example.com",
        resolver=resolver(*addresses),
        local_addresses=lambda: SERVER,
    )


@pytest.mark.parametrize(
    ("address", "provider"),
    [
        ("151.101.1.1", "fastly"),
        ("199.232.69.1", "fastly"),
        ("2a04:4e42::1", "fastly"),
        ("89.187.188.227", "bunnycdn"),
        ("138.199.24.209", "bunnycdn"),
        ("185.143.232.10", "arvancloud"),
        ("94.101.182.5", "arvancloud"),
    ],
)
def test_a_name_behind_another_cdn_is_called_by_its_name(address: str, provider: str) -> None:
    found = checked(address)

    assert found.verdict == "cdn"
    assert found.cdn == provider
    assert found.points_here is True


def test_cloudflare_is_still_cloudflare() -> None:
    found = checked("104.16.0.1")

    assert (found.verdict, found.cdn) == ("cdn", "cloudflare")


def test_a_name_that_mixes_two_cdns_is_not_behind_one_proxy() -> None:
    found = checked("151.101.1.1", "89.187.188.227")

    assert found.verdict == "elsewhere" and found.cdn is None


def test_a_name_with_one_address_outside_the_cdn_is_elsewhere() -> None:
    found = checked("151.101.1.1", "198.51.100.4")

    assert found.verdict == "elsewhere"


def test_an_address_of_nobody_is_elsewhere() -> None:
    assert checked("198.51.100.4").verdict == "elsewhere"


def test_the_server_itself_is_here_not_a_cdn() -> None:
    assert checked("93.184.216.34").verdict == "here"


def test_every_listed_network_is_valid_and_not_a_slice_of_the_internet() -> None:
    for provider, entries in cdn_networks.PROVIDERS.items():
        for entry in entries:
            network = ipaddress.ip_network(entry, strict=False)
            assert network.prefixlen >= (16 if network.version == 4 else 29), (provider, entry)


def test_no_address_belongs_to_two_providers() -> None:
    for provider, entries in cdn_networks.PROVIDERS.items():
        for other, others in cdn_networks.PROVIDERS.items():
            if provider >= other:
                continue
            for entry in entries[:50]:
                address = ipaddress.ip_network(entry, strict=False).network_address
                assert cdn_networks.provider_of([str(address)]) == provider, (entry, other)
                assert str(address) not in others


def test_no_addresses_no_provider() -> None:
    assert cdn_networks.provider_of([]) is None
