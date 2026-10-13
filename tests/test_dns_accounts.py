# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
DNS accounts, Cloudflare's API, the DNS-01 challenge and the resolver.

What is pinned: a token is proven before it is kept and is never anywhere but its 0600
secret file; Cloudflare's own words reach the operator unchanged; the challenge
record is published, waited for and removed, and only the one this order published is
removed; and the resolver reads the answer of the question it asked, and nothing else.
"""

from __future__ import annotations

import io
import json
import stat
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.certificate_store import CertificateSettings, CertificateStore
from noust.core.dns_store import DnsStore
from noust.core.exceptions import CertificateError, NoustError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.dns.accounts import DnsAccounts, secret_name
from noust.managers.dns.challenge import (
    CHALLENGE_COMMENT,
    DnsChallenge,
    challenge_name,
    read_environment,
)
from noust.managers.dns.cloudflare import CloudflareClient, CloudflareProvider
from noust.managers.dns.provider import DnsProviderError, is_public_address, same_value, zone_for
from noust.managers.dns.resolver import parse_addresses, resolve
from tests.dns_support import Cloudflare, FakeProvider, dns_answer, zones

TOKEN = "cf-token-" + "x" * 31
VALIDATION = "A" * 43


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "state" / "secrets")


# -- Cloudflare's client -----------------------------------------------------------


def test_the_token_is_a_bearer_credential_and_the_host_is_fixed() -> None:
    fake = Cloudflare()
    fake.say(zones("example.com"))
    client = CloudflareClient(TOKEN, opener=fake)

    found = CloudflareProvider(client).zones()

    (request,) = fake.requests
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"
    assert request.full_url.startswith("https://api.cloudflare.com/client/v4/zones?")
    assert [(z.name, z.account_id) for z in found] == [("example.com", "acc")]


@pytest.mark.parametrize("path", ["https://evil.test/x", "//evil.test/x", "zones"])
def test_a_path_that_is_not_a_path_on_the_api_is_refused(path: str) -> None:
    with pytest.raises(DnsProviderError):
        CloudflareClient(TOKEN, opener=Cloudflare()).request("GET", path)


def test_zones_are_read_page_by_page() -> None:
    fake = Cloudflare()
    first = zones("a.com")
    first["result_info"] = {"total_pages": 2}
    fake.say(first)
    fake.say(zones("b.com"))

    found = CloudflareProvider(CloudflareClient(TOKEN, opener=fake)).zones()

    assert [z.name for z in found] == ["a.com", "b.com"]
    assert len(fake.requests) == 2


def test_cloudflares_own_words_reach_the_operator_and_the_token_does_not() -> None:
    fake = Cloudflare()
    fake.say(
        {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}, 403
    )

    with pytest.raises(DnsProviderError) as raised:
        CloudflareProvider(CloudflareClient(TOKEN, opener=fake)).zones()

    assert raised.value.status == 403
    assert raised.value.output == "10000: Authentication error"
    assert "Zone:Read" in raised.value.details
    assert TOKEN not in str(raised.value) and TOKEN not in (raised.value.output or "")


def test_a_success_false_answer_is_an_error_even_with_status_200() -> None:
    fake = Cloudflare()
    fake.say(
        {"success": False, "errors": [{"code": 1003, "message": "Invalid or missing zone id"}]}
    )

    with pytest.raises(DnsProviderError) as raised:
        CloudflareClient(TOKEN, opener=fake).request("GET", "/zones/x")

    assert "1003" in (raised.value.output or "")


def test_a_network_failure_is_one_error_that_says_what_to_check() -> None:
    def down(request: Any, timeout: float = 0) -> Any:
        raise OSError("Network is unreachable")

    with pytest.raises(DnsProviderError) as raised:
        CloudflareClient(TOKEN, opener=down).request("GET", "/zones")

    assert "api.cloudflare.com" in raised.value.details


def test_a_txt_value_is_compared_without_the_quotes_cloudflare_adds() -> None:
    assert same_value('"abc"', "abc")
    assert same_value("2001:db8::1", "2001:0DB8:0:0::1")
    assert same_value("Target.Example.COM.", "target.example.com")
    assert not same_value("abc", None)


def test_the_zone_of_a_name_is_the_longest_one_it_is_in() -> None:
    zone_list = CloudflareProvider(
        CloudflareClient(TOKEN, opener=_zones("example.com", "sub.example.com"))
    ).zones()

    assert zone_for("a.sub.example.com", zone_list).name == "sub.example.com"  # type: ignore[union-attr]
    assert zone_for("example.com", zone_list).name == "example.com"  # type: ignore[union-attr]
    assert zone_for("notexample.com", zone_list) is None


def _zones(*names: str) -> Cloudflare:
    fake = Cloudflare()
    fake.say(zones(*names))
    return fake


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.4.4", True),
        ("2606:4700:4700::1111", True),
        ("10.1.2.3", False),
        ("::1", False),
        ("x", False),
    ],
)
def test_which_addresses_may_be_published(address: str, public: bool) -> None:
    assert is_public_address(address) is public


# -- accounts ---------------------------------------------------------------------


def accounts(
    store: NoustStore, secrets: SecretStore, opener: Any = None, runner: Any = None
) -> DnsAccounts:
    return DnsAccounts(store=store, secrets=secrets, opener=opener, runner=runner)


def test_a_cloudflare_token_is_proven_before_it_is_kept_and_kept_only_as_a_secret(
    store: NoustStore, secrets: SecretStore
) -> None:
    fake = _zones("example.com")

    account = accounts(store, secrets, fake).add(
        "cf", "cloudflare", {"token": TOKEN}, automatic=True
    )

    assert (account.name, account.kind, account.automatic) == ("cf", "cloudflare", True)
    assert account.verified_at is not None
    path = secrets.path(secret_name("cf"))
    assert path.read_text() == TOKEN
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with store._transaction() as cursor:
        cursor.execute("SELECT * FROM dns_providers")
        assert TOKEN not in json.dumps([dict(row) for row in cursor.fetchall()])


def test_a_token_cloudflare_refuses_is_not_kept(store: NoustStore, secrets: SecretStore) -> None:
    fake = Cloudflare()
    fake.say({"success": False, "errors": [{"code": 9109, "message": "Invalid access token"}]}, 403)

    with pytest.raises(DnsProviderError):
        accounts(store, secrets, fake).add("cf", "cloudflare", {"token": TOKEN})

    assert DnsStore(store).list_accounts() == []
    assert not secrets.path(secret_name("cf")).exists()


def test_a_token_that_sees_no_zone_is_refused(store: NoustStore, secrets: SecretStore) -> None:
    with pytest.raises(DnsProviderError) as raised:
        accounts(store, secrets, _zones()).add("cf", "cloudflare", {"token": TOKEN})

    assert "no zones" in raised.value.message


@pytest.mark.parametrize(
    ("name", "kind", "fields"),
    [
        ("Bad Name", "cloudflare", {"token": TOKEN}),
        ("cf", "route53", {"token": TOKEN}),
        ("cf", "cloudflare", {"token": "short"}),
        ("cf", "cloudflare", {}),
        ("cf", "cloudflare", {"token": TOKEN, "password": "x"}),
        ("cf", "digitalocean", {"token": "ok\nsneaky = 1"}),
    ],
)
def test_names_kinds_and_credentials_that_cannot_work_are_refused(
    store: NoustStore, secrets: SecretStore, name: str, kind: str, fields: dict[str, str]
) -> None:
    with pytest.raises(ValidationError):
        accounts(store, secrets, _zones("example.com")).add(name, kind, fields)

    assert DnsStore(store).list_accounts() == []


def test_two_accounts_cannot_share_a_name(store: NoustStore, secrets: SecretStore) -> None:
    manager = accounts(store, secrets, _zones("example.com"))
    manager.add("cf", "cloudflare", {"token": TOKEN})

    with pytest.raises(ValidationError):
        manager.add("cf", "cloudflare", {"token": TOKEN})


def test_a_plugin_account_writes_the_file_certbot_reads_and_needs_the_plugin(
    store: NoustStore, secrets: SecretStore, runner: FakeRunner
) -> None:
    runner.script(["certbot", "plugins"], stdout="* dns-digitalocean\nDescription: DigitalOcean\n")

    account = accounts(store, secrets, runner=runner).add("do", "digitalocean", {"token": TOKEN})

    assert account.automatic is False
    assert secrets.path(secret_name("do")).read_text() == f"dns_digitalocean_token = {TOKEN}\n"
    assert accounts(store, secrets, runner=runner).credentials_file("do") == secrets.path(
        secret_name("do")
    )


def test_a_plugin_that_is_not_installed_is_named_with_its_package(
    store: NoustStore, secrets: SecretStore, runner: FakeRunner
) -> None:
    runner.script(["certbot", "plugins"], stdout="* nginx\n")

    with pytest.raises(NoustError) as raised:
        accounts(store, secrets, runner=runner).add("do", "digitalocean", {"token": TOKEN})

    assert "python3-certbot-dns-digitalocean" in raised.value.details
    assert DnsStore(store).list_accounts() == []


def test_a_plugin_account_cannot_create_records(
    store: NoustStore, secrets: SecretStore, runner: FakeRunner
) -> None:
    runner.script(["certbot", "plugins"], stdout="* dns-digitalocean\n")
    manager = accounts(store, secrets, runner=runner)
    manager.add("do", "digitalocean", {"token": TOKEN}, automatic=True)

    assert manager.get("do").automatic is False
    with pytest.raises(ValidationError):
        manager.set_automatic("do", True)
    with pytest.raises(NoustError):
        manager.provider("do")


def test_an_account_a_certificate_is_ordered_through_cannot_be_removed(
    store: NoustStore, secrets: SecretStore
) -> None:
    manager = accounts(store, secrets, _zones("example.com"))
    manager.add("cf", "cloudflare", {"token": TOKEN})
    CertificateStore(store).set_settings(
        CertificateSettings(domain="shop.example.com", challenge="dns-01", dns_provider="cf")
    )

    with pytest.raises(ValidationError) as raised:
        manager.remove("cf")

    assert "shop.example.com" in raised.value.message
    CertificateStore(store).set_settings(CertificateSettings(domain="shop.example.com"))
    manager.remove("cf")
    assert not secrets.path(secret_name("cf")).exists()


def test_verifying_records_the_reason_a_token_stopped_working(
    store: NoustStore, secrets: SecretStore
) -> None:
    fake = _zones("example.com")
    manager = accounts(store, secrets, fake)
    manager.add("cf", "cloudflare", {"token": TOKEN})
    fake.say(
        {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]}, 403
    )

    with pytest.raises(DnsProviderError):
        manager.verify("cf")

    assert manager.get("cf").last_error == "10000: Authentication error"
    assert manager.get("cf").verified_at is None


# -- the DNS-01 challenge ----------------------------------------------------------


def challenge(provider: FakeProvider, store: NoustStore, waits: list[float]) -> DnsChallenge:
    DnsStore(store).save_account(
        __import__("noust.core.dns_store", fromlist=["DnsAccount"]).DnsAccount(
            name="cf", kind="cloudflare", settings={"propagation_seconds": 7}
        )
    )
    manager = DnsAccounts(store=store)
    manager.provider = lambda name: provider  # type: ignore[method-assign]
    return DnsChallenge(manager, sleep=waits.append)


def test_the_challenge_record_is_published_marked_and_waited_for(store: NoustStore) -> None:
    provider, waits = FakeProvider("example.com"), []

    name = challenge(provider, store, waits).present("cf", "shop.example.com", VALIDATION)

    assert name == "_acme-challenge.shop.example.com"
    (record,) = provider.records["zone-example.com"]
    assert (record.type, record.name, record.content) == ("TXT", name, VALIDATION)
    assert record.comment == CHALLENGE_COMMENT
    assert waits == [7.0]


def test_a_wildcard_and_its_base_share_one_record_name_with_two_values(store: NoustStore) -> None:
    provider, waits = FakeProvider("example.com"), []
    runner = challenge(provider, store, waits)

    runner.present("cf", "example.com", VALIDATION)
    runner.present("cf", "*.example.com", "B" * 43)

    assert challenge_name("*.example.com") == challenge_name("example.com")
    assert len(provider.records["zone-example.com"]) == 2
    assert runner.cleanup("cf", "*.example.com", "B" * 43) == 1
    (left,) = provider.records["zone-example.com"]
    assert left.content == VALIDATION


def test_presenting_the_same_value_twice_publishes_it_once(store: NoustStore) -> None:
    provider = FakeProvider("example.com")
    runner = challenge(provider, store, [])

    runner.present("cf", "example.com", VALIDATION)
    runner.present("cf", "example.com", VALIDATION)

    assert len(provider.records["zone-example.com"]) == 1


def test_cleanup_removes_only_the_record_this_order_published(store: NoustStore) -> None:
    provider = FakeProvider("example.com")
    runner = challenge(provider, store, [])
    name = "_acme-challenge.example.com"
    provider.seed("example.com", name, "TXT", VALIDATION, comment="added by hand for another tool")
    provider.seed("example.com", name, "TXT", "Z" * 43, comment=CHALLENGE_COMMENT)

    assert runner.cleanup("cf", "example.com", VALIDATION) == 0  # same value, not ours
    assert runner.cleanup("cf", "example.com", "Z" * 43) == 1
    assert runner.cleanup("cf", "example.com", "Z" * 43) == 0  # nothing left is not an error
    assert len(provider.records["zone-example.com"]) == 1


def test_an_account_without_the_zone_says_which_to_use(store: NoustStore) -> None:
    provider = FakeProvider("other.org")

    with pytest.raises(CertificateError) as raised:
        challenge(provider, store, []).present("cf", "example.com", VALIDATION)

    assert "noust cert use APP --acme --dns NAME" in raised.value.details


def test_the_hook_reads_certbots_environment_and_nothing_else() -> None:
    assert read_environment(
        {"CERTBOT_DOMAIN": "Example.com", "CERTBOT_VALIDATION": VALIDATION}
    ) == (
        "example.com",
        VALIDATION,
    )
    with pytest.raises(CertificateError):
        read_environment({})
    with pytest.raises(CertificateError):
        read_environment({"CERTBOT_DOMAIN": "example.com", "CERTBOT_VALIDATION": "not a token!"})


# -- the resolver -----------------------------------------------------------------


def test_the_answers_of_both_types_are_read_and_a_missing_name_is_an_answer() -> None:
    seen: list[bytes] = []

    def transport(server: str, packet: bytes, timeout: float) -> bytes:
        seen.append(packet)
        ident = int.from_bytes(packet[:2], "big")
        qtype = int.from_bytes(packet[-4:-2], "big")
        return dns_answer(ident, qtype, ["8.8.4.4", "2606:4700:4700::1111"])

    addresses, server = resolve("test.example.com", ["1.1.1.1"], transport=transport)

    assert addresses == ["2606:4700:4700::1111", "8.8.4.4"]
    assert server == "1.1.1.1"
    assert len(seen) == 2

    def missing(server: str, packet: bytes, timeout: float) -> bytes:
        return dns_answer(int.from_bytes(packet[:2], "big"), 1, [], rcode=3)

    assert resolve("test.example.com", ["1.1.1.1"], transport=missing) == ([], "1.1.1.1")


def test_an_answer_to_another_question_is_ignored() -> None:
    packet = dns_answer(1234, 1, ["8.8.4.4"])

    assert parse_addresses(packet, 1234, 1) == ["8.8.4.4"]
    assert parse_addresses(packet, 4321, 1) == []


def test_the_next_resolver_is_asked_when_one_does_not_answer() -> None:
    asked: list[str] = []

    def transport(server: str, packet: bytes, timeout: float) -> bytes:
        asked.append(server)
        if server == "1.1.1.1":
            raise OSError("timed out")
        return dns_answer(
            int.from_bytes(packet[:2], "big"), int.from_bytes(packet[-4:-2], "big"), ["8.8.4.4"]
        )

    assert resolve("test.example.com", ["1.1.1.1", "9.9.9.9"], transport=transport) == (
        ["8.8.4.4"],
        "9.9.9.9",
    )
    assert asked[0] == "1.1.1.1" and "9.9.9.9" in asked

    def nobody(server: str, packet: bytes, timeout: float) -> bytes:
        raise OSError("timed out")

    assert resolve("test.example.com", ["1.1.1.1"], transport=nobody) == ([], None)


@pytest.mark.parametrize(
    "garbage", [b"", b"\x00" * 5, b"\x12\x34\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"]
)
def test_a_malformed_answer_is_refused_not_interpreted(garbage: bytes) -> None:
    def transport(server: str, packet: bytes, timeout: float) -> bytes:
        return garbage

    assert resolve("test.example.com", ["1.1.1.1"], transport=transport) == ([], None)


def test_a_name_with_an_empty_label_is_never_sent() -> None:
    sent: list[bytes] = []

    def transport(server: str, packet: bytes, timeout: float) -> bytes:
        sent.append(packet)
        return b""

    assert resolve("a..example.com", ["1.1.1.1"], transport=transport) == ([], None)
    assert sent == []


def test_stdlib_urllib_is_what_the_client_uses_by_default() -> None:
    assert CloudflareClient(TOKEN)._opener is urllib.request.urlopen
    assert stat is not None and io is not None
