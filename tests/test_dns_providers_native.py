# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS providers Noust speaks to itself besides Cloudflare: DigitalOcean, Hetzner, Gandi and
Amazon Route 53.

Each is run against an in-memory copy of its API (``tests/dns_wires.py``), so what is pinned is the
whole path from the ownership rules to the request on the wire: the records are read, created,
changed and removed in the shape each provider documents; a credential travels in one header and
nowhere else; the provider's own words reach the operator unchanged; a record the operator made
(an alias, a routing policy, a value edited by hand) is never replaced or deleted; and a provider
that keeps no note with a record still obeys the ownership rules on what Noust made.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from noust.core.dns_store import DnsAccount, DnsStore
from noust.core.exceptions import NoustError, ValidationError
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers.dns import digitalocean, gandi, hetzner, route53
from noust.managers.dns.accounts import DnsAccounts, secret_name
from noust.managers.dns.catalog import KINDS
from noust.managers.dns.challenge import CHALLENGE_COMMENT, DEFAULT_PROPAGATION, DnsChallenge
from noust.managers.dns.digitalocean import DigitalOceanProvider
from noust.managers.dns.gandi import GandiProvider
from noust.managers.dns.hetzner import HetznerProvider
from noust.managers.dns.provider import AUTOMATIC_TTL, DnsProvider, DnsProviderError, Zone
from noust.managers.dns.records import Ensured, ManagedDns, Release, ownership_comment
from noust.managers.dns.route53 import Route53Provider, sign
from noust.managers.dns.rrset import SINGLE_VALUE_TYPES, parse_record_id, record_id
from tests.dns_wires import (
    ACCESS_KEY,
    SECRET_KEY,
    TOKEN,
    DigitalOceanWire,
    GandiWire,
    HetznerWire,
    Route53Wire,
    Wire,
)

PUBLIC = "8.8.4.4"
OTHER = "9.9.9.9"
ORDER = "A" * 43
SPF = "v=spf1 include:_spf.example.com ~all"
ALL_KINDS = ["digitalocean", "hetzner", "gandi", "route53"]
SET_KINDS = ["hetzner", "gandi", "route53"]


@dataclass
class Case:
    """
    One provider over its in-memory API.

    Attributes:
        kind: The catalog's name for it.
        wire: Its API.
        provider: The client under test.
        denial: ``(status, body, words)``: what the provider answers a credential it refuses, and
            the part of that which must reach the operator verbatim.
        sleeps: The pauses the provider asked for.
    """

    kind: str
    wire: Wire
    provider: DnsProvider
    denial: tuple[int, bytes, str]
    sleeps: list[float]

    @property
    def zone(self) -> Zone:
        return self.provider.zones()[0]


def build(kind: str) -> Case:
    sleeps: list[float] = []
    if kind == "digitalocean":
        wire = DigitalOceanWire("example.com")
        return Case(
            kind,
            wire,
            DigitalOceanProvider(digitalocean.client(TOKEN, wire)),
            (
                401,
                b'{"id":"unauthorized","message":"Unable to authenticate you"}',
                "Unable to authenticate you",
            ),
            sleeps,
        )
    if kind == "hetzner":
        hz = HetznerWire("example.com")
        return Case(
            kind,
            hz,
            HetznerProvider(hetzner.client(TOKEN, hz), sleep=sleeps.append),
            (
                401,
                b'{"error":{"code":"unauthorized","message":"unable to authenticate"}}',
                "unable to authenticate",
            ),
            sleeps,
        )
    if kind == "gandi":
        gw = GandiWire("example.com")
        return Case(
            kind,
            gw,
            GandiProvider(gandi.client(TOKEN, gw)),
            (
                403,
                b'{"status":"error","message":"The access token is invalid"}',
                "access token is invalid",
            ),
            sleeps,
        )
    rw = Route53Wire("example.com")
    return Case(
        kind,
        rw,
        Route53Provider(route53.client(ACCESS_KEY, SECRET_KEY, rw)),
        (
            403,
            b'<ErrorResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><Error>'
            b"<Type>Sender</Type><Code>SignatureDoesNotMatch</Code><Message>The request signature "
            b"we calculated does not match</Message></Error></ErrorResponse>",
            "SignatureDoesNotMatch: The request signature we calculated does not match",
        ),
        sleeps,
    )


@pytest.fixture(params=ALL_KINDS)
def case(request: pytest.FixtureRequest) -> Case:
    return build(request.param)


@pytest.fixture(params=SET_KINDS)
def set_case(request: pytest.FixtureRequest) -> Case:
    return build(request.param)


# -- the shape every provider shares -------------------------------------------------


def test_the_zones_of_the_account_are_listed_by_name(case: Case) -> None:
    assert [zone.name for zone in case.provider.zones()] == ["example.com"]


def test_a_record_is_created_found_read_by_id_and_is_on_the_wire(case: Case) -> None:
    zone = case.zone

    created = case.provider.create_record(
        zone, "api.example.com", "A", PUBLIC, ttl=AUTOMATIC_TTL, comment="mine"
    )

    assert (created.type, created.name, created.content) == ("A", "api.example.com", PUBLIC)
    found = case.provider.find_records(zone, "api.example.com", "A")
    assert [(r.name, r.type, r.content) for r in found] == [("api.example.com", "A", PUBLIC)]
    again = case.provider.get_record(zone, created.id)
    assert again is not None and again.content == PUBLIC
    assert case.wire.values("api.example.com", "A") == [PUBLIC]


def test_only_the_name_and_type_asked_for_are_found(case: Case) -> None:
    case.wire.seed("api.example.com", "A", PUBLIC)
    case.wire.seed("api.example.com", "TXT", "hello")
    case.wire.seed("apix.example.com", "A", OTHER)
    case.wire.seed("example.com", "A", OTHER)

    found = case.provider.find_records(case.zone, "api.example.com", "A")

    assert [(r.name, r.content) for r in found] == [("api.example.com", PUBLIC)]
    assert case.provider.find_records(case.zone, "nothing.example.com", "A") == []


def test_a_txt_value_is_given_back_without_the_quotes_the_provider_wraps_it_in(
    case: Case,
) -> None:
    created = case.provider.create_record(
        case.zone, "_acme-challenge.example.com", "TXT", SPF, comment="x"
    )

    assert created.content == SPF
    (found,) = case.provider.find_records(case.zone, "_acme-challenge.example.com", "TXT")
    assert found.content == SPF
    assert case.wire.values("_acme-challenge.example.com", "TXT") == [SPF]


def test_the_apex_and_a_wildcard_are_names_like_any_other(case: Case) -> None:
    case.provider.create_record(case.zone, "example.com", "A", PUBLIC)
    case.provider.create_record(case.zone, "*.example.com", "A", OTHER)

    assert [r.content for r in case.provider.find_records(case.zone, "example.com", "A")] == [
        PUBLIC
    ]
    assert [r.content for r in case.provider.find_records(case.zone, "*.example.com", "A")] == [
        OTHER
    ]


def test_a_record_is_changed_in_place_and_followed_by_the_id_it_answers_with(case: Case) -> None:
    created = case.provider.create_record(case.zone, "api.example.com", "A", PUBLIC)

    updated = case.provider.update_record(case.zone, created.id, OTHER)

    assert updated.content == OTHER
    assert [r.content for r in case.provider.find_records(case.zone, "api.example.com", "A")] == [
        OTHER
    ]
    kept = case.provider.get_record(case.zone, updated.id)
    assert kept is not None and kept.content == OTHER
    assert case.wire.values("api.example.com", "A") == [OTHER]


def test_a_record_is_deleted_once_and_a_second_time_is_already_gone(case: Case) -> None:
    created = case.provider.create_record(case.zone, "api.example.com", "A", PUBLIC)

    assert case.provider.delete_record(case.zone, created.id) is True
    assert case.provider.delete_record(case.zone, created.id) is False
    assert case.wire.values("api.example.com", "A") == []
    assert case.provider.get_record(case.zone, created.id) is None


def test_a_refused_credential_reaches_the_operator_in_the_providers_own_words(
    case: Case,
) -> None:
    status, body, words = case.denial
    case.wire.refuse_next = (status, body)

    with pytest.raises(DnsProviderError) as raised:
        case.provider.zones()

    assert raised.value.status == status
    assert words in (raised.value.output or "")
    assert raised.value.details
    everything = f"{raised.value.message} {raised.value.details} {raised.value.output}"
    assert TOKEN not in everything and SECRET_KEY not in everything


def test_a_provider_that_cannot_be_reached_says_what_to_check(case: Case) -> None:
    case.wire.down = True

    with pytest.raises(DnsProviderError) as raised:
        case.provider.zones()

    assert "could not be reached" in raised.value.message
    assert "outbound HTTPS" in raised.value.details
    assert "connection refused" in (raised.value.output or "")


def test_the_credential_goes_in_the_signature_or_the_header_and_nowhere_else(case: Case) -> None:
    created = case.provider.create_record(case.zone, "api.example.com", "A", PUBLIC)
    case.provider.update_record(case.zone, created.id, OTHER)
    case.provider.delete_record(case.zone, created.id)

    assert case.wire.requests
    for seen in case.wire.requests:
        assert TOKEN not in seen.url and SECRET_KEY not in seen.url
        assert TOKEN.encode() not in seen.body and SECRET_KEY.encode() not in seen.body
        assert seen.url.startswith("https://")


# -- providers that keep records in sets ---------------------------------------------


def test_the_same_value_twice_is_one_record_and_one_write(set_case: Case) -> None:
    first = set_case.provider.create_record(set_case.zone, "api.example.com", "A", PUBLIC)
    writes = len(set_case.wire.writes())

    second = set_case.provider.create_record(set_case.zone, "api.example.com", "A", PUBLIC)

    assert second.id == first.id
    assert len(set_case.wire.writes()) == writes


def test_a_second_value_joins_the_set_and_deleting_one_leaves_the_other(set_case: Case) -> None:
    zone = set_case.zone
    first = set_case.provider.create_record(zone, "_acme-challenge.example.com", "TXT", ORDER)
    second = set_case.provider.create_record(zone, "_acme-challenge.example.com", "TXT", "B" * 43)

    found = set_case.provider.find_records(zone, "_acme-challenge.example.com", "TXT")
    assert sorted(r.content for r in found) == sorted([ORDER, "B" * 43])

    assert set_case.provider.delete_record(zone, first.id) is True
    assert set_case.wire.values("_acme-challenge.example.com", "TXT") == ["B" * 43]
    assert set_case.provider.delete_record(zone, second.id) is True
    assert set_case.wire.values("_acme-challenge.example.com", "TXT") == []


def test_a_name_has_only_one_cname_and_the_second_is_refused_before_anything_is_sent(
    set_case: Case,
) -> None:
    assert "CNAME" in SINGLE_VALUE_TYPES
    set_case.provider.create_record(set_case.zone, "go.example.com", "CNAME", "a.example.net")
    writes = len(set_case.wire.writes())

    with pytest.raises(DnsProviderError) as raised:
        set_case.provider.create_record(set_case.zone, "go.example.com", "CNAME", "b.example.net")

    assert "only one" in raised.value.message
    assert len(set_case.wire.writes()) == writes


def test_the_id_is_made_of_what_identifies_the_record_and_survives_odd_values() -> None:
    odd = "v=a|b c%d"
    identifier = record_id("Api.Example.com", "TXT", odd)

    assert identifier.count("|") == 2
    assert parse_record_id(identifier) == ("api.example.com", "TXT", odd)
    assert parse_record_id("not an id") is None
    assert parse_record_id("a||b") is None


def test_a_record_that_is_not_there_any_more_is_none_and_changing_it_is_a_404(
    set_case: Case,
) -> None:
    gone = record_id("api.example.com", "A", PUBLIC)

    assert set_case.provider.get_record(set_case.zone, gone) is None
    assert set_case.provider.get_record(set_case.zone, "garbage") is None
    assert set_case.provider.delete_record(set_case.zone, gone) is False
    with pytest.raises(DnsProviderError) as raised:
        set_case.provider.update_record(set_case.zone, gone, OTHER)
    assert raised.value.status == 404


def test_an_edited_value_is_found_under_its_new_id_only(set_case: Case) -> None:
    created = set_case.provider.create_record(set_case.zone, "api.example.com", "A", PUBLIC)

    updated = set_case.provider.update_record(set_case.zone, created.id, OTHER)

    assert updated.id == record_id("api.example.com", "A", OTHER)
    assert set_case.provider.get_record(set_case.zone, created.id) is None


# -- DigitalOcean ---------------------------------------------------------------------


def test_digitalocean_names_are_relative_a_cname_target_ends_with_a_dot_and_ttl_defaults() -> None:
    wire = DigitalOceanWire("example.com")
    provider = DigitalOceanProvider(digitalocean.client(TOKEN, wire))
    zone = provider.zones()[0]

    provider.create_record(zone, "go.example.com", "CNAME", "target.example.net")
    provider.create_record(zone, "example.com", "A", PUBLIC, ttl=600)

    cname, apex = [r.json() for r in wire.writes()]
    assert cname == {"type": "CNAME", "name": "go", "data": "target.example.net.", "ttl": 300}
    assert apex == {"type": "A", "name": "@", "data": PUBLIC, "ttl": 600}
    assert wire.requests[-1].headers["authorization"] == f"Bearer {TOKEN}"


def test_digitalocean_domains_are_read_page_by_page() -> None:
    wire = DigitalOceanWire("a.com", "b.com", "c.com", page_size=2)

    zones = DigitalOceanProvider(digitalocean.client(TOKEN, wire)).zones()

    assert [z.name for z in zones] == ["a.com", "b.com", "c.com"]
    assert [r.query["page"] for r in wire.requests] == ["1", "2"]


def test_digitalocean_numbers_survive_an_update_and_a_missing_record_is_none() -> None:
    wire = DigitalOceanWire("example.com")
    provider = DigitalOceanProvider(digitalocean.client(TOKEN, wire))
    zone = provider.zones()[0]
    created = provider.create_record(zone, "api.example.com", "A", PUBLIC)

    updated = provider.update_record(zone, created.id, OTHER)

    assert updated.id == created.id
    assert provider.get_record(zone, "999999") is None
    assert provider.delete_record(zone, "999999") is False


# -- Hetzner --------------------------------------------------------------------------


def test_hetzner_keeps_a_comment_with_each_value_and_a_new_value_leaves_the_others_alone() -> None:
    case = build("hetzner")
    wire = case.wire
    assert isinstance(wire, HetznerWire)
    zone = case.zone
    case.provider.create_record(zone, "_acme-challenge.example.com", "TXT", ORDER, comment="first")

    case.provider.create_record(
        zone, "_acme-challenge.example.com", "TXT", "B" * 43, comment="second"
    )

    assert wire.comments("_acme-challenge.example.com", "TXT") == ["first", "second"]
    sent = [r for r in wire.writes() if r.path.endswith("/actions/set_records")]
    assert sent[0].json()["records"][0] == {"value": f'"{ORDER}"', "comment": "first"}
    found = case.provider.find_records(zone, "_acme-challenge.example.com", "TXT")
    assert sorted((r.content[:1], r.comment) for r in found) == [("A", "first"), ("B", "second")]


def test_hetzner_waits_for_the_change_to_finish_and_says_how_long_it_looked() -> None:
    case = build("hetzner")
    case.wire.slow = 2  # type: ignore[attr-defined]

    case.provider.create_record(case.zone, "api.example.com", "A", PUBLIC)

    assert case.wire.polls == 3  # type: ignore[attr-defined]
    assert case.sleeps == [hetzner.ACTION_PAUSE, hetzner.ACTION_PAUSE]


def test_a_change_hetzner_could_not_apply_is_an_error_with_its_words() -> None:
    case = build("hetzner")
    case.wire.action_error = "the zone is locked"  # type: ignore[attr-defined]

    with pytest.raises(DnsProviderError) as raised:
        case.provider.create_record(case.zone, "api.example.com", "A", PUBLIC)

    assert raised.value.output == "the zone is locked"


def test_hetzner_apex_is_an_at_sign_a_new_set_gets_a_ttl_only_when_one_was_asked() -> None:
    case = build("hetzner")
    zone = case.zone

    case.provider.create_record(zone, "example.com", "A", PUBLIC, ttl=AUTOMATIC_TTL)
    case.provider.create_record(zone, "www.example.com", "A", PUBLIC, ttl=20)

    first, second = [r.json() for r in case.wire.writes()]
    assert first["name"] == "@" and "ttl" not in first
    assert second["ttl"] == 60  # Hetzner's floor


def test_a_zone_hetzner_only_mirrors_is_not_offered() -> None:
    wire = HetznerWire("example.com")
    wire.secondary = ["mirror.example.org"]

    zones = HetznerProvider(hetzner.client(TOKEN, wire)).zones()

    assert [z.name for z in zones] == ["example.com"]


def test_hetzner_reads_a_missing_set_as_nothing_and_deleting_it_as_already_gone() -> None:
    case = build("hetzner")

    assert case.provider.find_records(case.zone, "nothing.example.com", "A") == []
    assert (
        case.provider.delete_record(case.zone, record_id("nothing.example.com", "A", PUBLIC))
        is False
    )


def test_a_token_of_the_old_dns_console_is_told_where_to_get_the_right_one() -> None:
    case = build("hetzner")
    case.wire.refuse_next = (401, b'{"error":{"code":"unauthorized","message":"no"}}')

    with pytest.raises(DnsProviderError) as raised:
        case.provider.zones()

    assert "Cloud" in raised.value.details and "migrated" in raised.value.details


# -- Gandi ----------------------------------------------------------------------------


def test_gandi_never_asks_for_less_than_its_smallest_ttl() -> None:
    case = build("gandi")
    assert isinstance(case.wire, GandiWire)

    case.provider.create_record(case.zone, "a.example.com", "A", PUBLIC, ttl=60)
    case.provider.create_record(case.zone, "b.example.com", "A", PUBLIC, ttl=AUTOMATIC_TTL)
    case.provider.create_record(case.zone, "c.example.com", "A", PUBLIC, ttl=900)

    assert [case.wire.ttl(f"{n}.example.com", "A") for n in "abc"] == [300, 300, 900]


def test_gandi_sends_a_txt_value_in_quotes_and_writes_the_set_whole() -> None:
    case = build("gandi")
    case.provider.create_record(case.zone, "_acme-challenge.example.com", "TXT", ORDER)

    (put,) = case.wire.writes()

    assert put.method == "PUT"
    assert put.path == "/domains/example.com/records/_acme-challenge/TXT"
    assert put.json()["rrset_values"] == [f'"{ORDER}"']


def test_gandi_reads_a_set_that_is_not_there_as_nothing() -> None:
    case = build("gandi")

    assert case.provider.find_records(case.zone, "nothing.example.com", "A") == []
    assert (
        case.provider.delete_record(case.zone, record_id("nothing.example.com", "A", PUBLIC))
        is False
    )


# -- Amazon Route 53 ------------------------------------------------------------------


def test_the_signature_is_the_one_aws_publishes_for_its_own_test_suite() -> None:
    # "get-vanilla" of the AWS Signature Version 4 test suite.
    headers = sign(
        "GET",
        "example.amazonaws.com",
        "/",
        {},
        b"",
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        service="service",
        now=datetime(2015, 8, 30, 12, 36, 0, tzinfo=timezone.utc),
    )

    assert headers["x-amz-date"] == "20150830T123600Z"
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )


def test_a_query_and_a_body_change_the_signature_and_the_secret_is_not_in_it() -> None:
    moment = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def signed(query: dict[str, str], payload: bytes) -> str:
        return sign(
            "POST",
            "route53.amazonaws.com",
            "/2013-04-01/hostedzone/Z1/rrset",
            query,
            payload,
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            now=moment,
        )["Authorization"]

    base = signed({}, b"<a/>")
    assert signed({"x": "1"}, b"<a/>") != base
    assert signed({}, b"<b/>") != base
    assert signed({}, b"<a/>") == base
    assert SECRET_KEY not in base


def test_route53_zones_skip_private_ones_strip_the_path_from_the_id_and_page() -> None:
    wire = Route53Wire("example.com", "example.org", page_size=1)
    provider = Route53Provider(route53.client(ACCESS_KEY, SECRET_KEY, wire))

    zones = provider.zones()

    assert [(z.id, z.name) for z in zones] == [("Z1000", "example.com"), ("Z1001", "example.org")]
    assert len(wire.requests) == 3  # one page each, and the private zone's page


def test_route53_reads_a_wildcard_as_it_escapes_it() -> None:
    case = build("route53")
    case.wire.seed("*.example.com", "A", PUBLIC)

    found = case.provider.find_records(case.zone, "*.example.com", "A")

    assert [(r.name, r.content) for r in found] == [("*.example.com", PUBLIC)]


def test_route53_quotes_a_txt_value_and_escapes_what_xml_and_txt_need_escaped() -> None:
    case = build("route53")
    assert isinstance(case.wire, Route53Wire)
    value = 'a "quoted" & <tagged> value'

    case.provider.create_record(case.zone, "t.example.com", "TXT", value)

    (action, name, kind, ttl, values) = case.wire.changes[-1]
    assert (action, name, kind, ttl) == ("UPSERT", "t.example.com", "TXT", 300)
    assert values == ['"a \\"quoted\\" & <tagged> value"']


def test_an_alias_is_never_replaced_or_deleted_and_the_name_is_refused() -> None:
    case = build("route53")
    assert isinstance(case.wire, Route53Wire)
    case.wire.seed("www.example.com", "A", "", alias=True)

    with pytest.raises(DnsProviderError) as raised:
        case.provider.find_records(case.zone, "www.example.com", "A")
    assert "an alias" in raised.value.message
    with pytest.raises(DnsProviderError):
        case.provider.create_record(case.zone, "www.example.com", "A", PUBLIC)
    gone = record_id("www.example.com", "A", PUBLIC)
    assert case.provider.get_record(case.zone, gone) is None
    assert case.provider.delete_record(case.zone, gone) is False

    assert case.wire.changes == []


def test_a_record_with_a_routing_policy_is_left_alone_too() -> None:
    case = build("route53")
    assert isinstance(case.wire, Route53Wire)
    case.wire.seed("api.example.com", "A", OTHER, set_id="blue")

    with pytest.raises(DnsProviderError) as raised:
        case.provider.create_record(case.zone, "api.example.com", "A", PUBLIC)

    assert "routing policy" in raised.value.message
    assert case.wire.changes == []


def test_a_delete_sends_exactly_what_route53_holds_because_it_refuses_anything_else() -> None:
    case = build("route53")
    assert isinstance(case.wire, Route53Wire)
    case.wire.seed("api.example.com", "A", PUBLIC, ttl=120)

    assert case.provider.delete_record(case.zone, record_id("api.example.com", "A", PUBLIC))

    assert case.wire.changes == [("DELETE", "api.example.com", "A", 120, [PUBLIC])]
    assert case.wire.values("api.example.com", "A") == []


def test_route53s_own_code_and_message_are_the_error() -> None:
    case = build("route53")
    case.wire.refuse_next = (
        400,
        b'<ErrorResponse xmlns="https://route53.amazonaws.com/doc/2013-04-01/"><Error>'
        b"<Code>InvalidChangeBatch</Code><Message>RRSet is not permitted</Message></Error>"
        b"</ErrorResponse>",
    )

    with pytest.raises(DnsProviderError) as raised:
        case.provider.create_record(case.zone, "api.example.com", "A", PUBLIC)

    assert raised.value.output == "InvalidChangeBatch: RRSet is not permitted"
    assert raised.value.status == 400


def test_an_access_key_that_is_not_the_shape_of_one_is_refused_before_a_request() -> None:
    wire = Route53Wire("example.com")

    with pytest.raises(DnsProviderError) as raised:
        route53.client("not a key!", SECRET_KEY, wire)

    assert "access key id" in raised.value.message
    assert wire.requests == []


# -- the accounts ---------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    for name in ("shop.example.com", "blog.example.com"):
        instance.create_app(App(domain=name, app_path=f"/var/www/apps/{name}"))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "state" / "secrets")


CREDENTIALS = {
    "digitalocean": {"token": TOKEN},
    "hetzner": {"token": TOKEN},
    "gandi": {"token": TOKEN},
    "route53": {"access_key_id": ACCESS_KEY, "secret_access_key": SECRET_KEY},
}


def accounts(store: NoustStore, secrets: SecretStore, wire: Wire) -> DnsAccounts:
    return DnsAccounts(store=store, secrets=secrets, opener=wire)


def test_an_account_is_proven_before_it_is_kept_and_its_credential_is_only_a_secret(
    case: Case, store: NoustStore, secrets: SecretStore
) -> None:
    manager = accounts(store, secrets, case.wire)

    account = manager.add("dns", case.kind, CREDENTIALS[case.kind], automatic=True)

    assert (account.kind, account.automatic) == (case.kind, True)
    assert account.verified_at is not None
    path = secrets.path(secret_name("dns"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with store._transaction() as cursor:
        cursor.execute("SELECT * FROM dns_providers")
        rows = json.dumps([dict(row) for row in cursor.fetchall()])
    assert TOKEN not in rows and SECRET_KEY not in rows
    assert [z.name for z in manager.provider("dns").zones()] == ["example.com"]
    assert [z.name for z in manager.verify("dns")] == ["example.com"]


def test_a_single_token_is_kept_as_it_is_and_route53s_two_fields_as_one_document(
    store: NoustStore, secrets: SecretStore
) -> None:
    accounts(store, secrets, HetznerWire("example.com")).add("hz", "hetzner", {"token": TOKEN})
    accounts(store, secrets, Route53Wire("example.com")).add(
        "aws", "route53", CREDENTIALS["route53"]
    )

    assert secrets.read(secret_name("hz")) == TOKEN
    assert json.loads(secrets.read(secret_name("aws"))) == CREDENTIALS["route53"]


def test_a_credential_the_provider_refuses_is_not_kept(
    case: Case, store: NoustStore, secrets: SecretStore
) -> None:
    status, body, words = case.denial
    case.wire.refuse_next = (status, body)

    with pytest.raises(DnsProviderError) as raised:
        accounts(store, secrets, case.wire).add("dns", case.kind, CREDENTIALS[case.kind])

    assert words in (raised.value.output or "")
    assert DnsStore(store).get_account("dns") is None
    assert not secrets.path(secret_name("dns")).exists()


def test_a_credential_that_sees_no_zone_is_refused(store: NoustStore, secrets: SecretStore) -> None:
    with pytest.raises(DnsProviderError) as raised:
        accounts(store, secrets, GandiWire()).add("dns", "gandi", {"token": TOKEN})

    assert "sees no zones" in raised.value.message
    assert DnsStore(store).get_account("dns") is None


def test_a_missing_field_and_a_key_that_is_not_a_key_are_refused(
    store: NoustStore, secrets: SecretStore
) -> None:
    wire = Route53Wire("example.com")
    with pytest.raises(ValidationError):
        accounts(store, secrets, wire).add("aws", "route53", {"access_key_id": ACCESS_KEY})
    with pytest.raises(DnsProviderError):
        accounts(store, secrets, wire).add(
            "aws", "route53", {"access_key_id": "nope", "secret_access_key": SECRET_KEY}
        )
    assert wire.requests == []


def test_a_damaged_credential_is_said_to_be_damaged_not_crashed_on(
    store: NoustStore, secrets: SecretStore
) -> None:
    manager = accounts(store, secrets, Route53Wire("example.com"))
    manager.add("aws", "route53", CREDENTIALS["route53"])
    secrets.write(secret_name("aws"), "{not json")

    with pytest.raises(NoustError) as raised:
        manager.provider("aws")

    assert "damaged" in raised.value.message


def test_the_catalog_says_what_each_account_can_do() -> None:
    assert {k for k, s in KINDS.items() if s.native} == {
        "cloudflare",
        "digitalocean",
        "hetzner",
        "gandi",
        "route53",
    }
    assert {k for k, s in KINDS.items() if s.tunnels} == {"cloudflare"}
    assert {k for k, s in KINDS.items() if s.native and not s.comments} == {
        "digitalocean",
        "gandi",
        "route53",
    }
    assert [f.name for f in KINDS["route53"].fields if not f.secret] == ["access_key_id"]


def test_a_plugin_account_is_told_which_accounts_can_keep_records(
    store: NoustStore, secrets: SecretStore
) -> None:
    from noust.core.runner import FakeRunner

    runner = FakeRunner()
    runner.script(["certbot", "plugins"], stdout="* dns-linode\n")
    manager = DnsAccounts(store=store, secrets=secrets, runner=runner)
    manager.add("old", "linode", {"key": TOKEN})

    with pytest.raises(NoustError) as raised:
        manager.provider("old")

    assert "digitalocean" in raised.value.details and "route53" in raised.value.details


# -- ownership where the provider keeps no note ----------------------------------------


@dataclass
class Managed:
    dns: ManagedDns
    wire: Wire
    kind: str


@pytest.fixture(params=["digitalocean", "gandi", "route53", "hetzner"])
def managed(
    request: pytest.FixtureRequest, store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> Managed:
    one = build(request.param)
    DnsStore(store).save_account(DnsAccount(name="acct", kind=one.kind, automatic=True))
    monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: one.provider)
    dns = ManagedDns(accounts=DnsAccounts(store=store), store=store, label="srv")
    return Managed(dns, one.wire, one.kind)


def test_what_noust_created_is_ours_and_goes_when_the_last_user_does(managed: Managed) -> None:
    result = managed.dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    assert result.outcome is Ensured.CREATED and result.record.owned is True
    assert managed.wire.values("api.example.com", "A") == [PUBLIC]
    assert [r.outcome for r in managed.dns.release("shop.example.com")] == [Release.DELETED]
    assert managed.wire.values("api.example.com", "A") == []


def test_the_marker_is_written_where_the_provider_keeps_notes_and_only_there(
    managed: Managed,
) -> None:
    result = managed.dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    if managed.kind == "hetzner":
        assert isinstance(managed.wire, HetznerWire)
        assert managed.wire.comments("api.example.com", "A") == [
            ownership_comment(result.record, "srv")
        ]
    else:
        assert "managed-by" not in "".join(r.body.decode() for r in managed.wire.writes())


def test_a_record_that_was_already_there_is_adopted_and_never_deleted(managed: Managed) -> None:
    managed.wire.seed("api.example.com", "A", PUBLIC)
    before = len(managed.wire.writes())

    result = managed.dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    released = managed.dns.release("shop.example.com")

    assert result.outcome is Ensured.ADOPTED and result.record.owned is False
    assert [r.outcome for r in released] == [Release.NOT_OWNED]
    assert managed.wire.values("api.example.com", "A") == [PUBLIC]
    assert len(managed.wire.writes()) == before


def test_a_record_somebody_changed_is_left_as_they_left_it(managed: Managed) -> None:
    managed.dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")
    if isinstance(managed.wire, DigitalOceanWire):
        managed.wire.edit(1000, data=OTHER)
    else:
        # A provider that names a record by its value sees an edit as the record going away and a
        # different one appearing; either way Noust deletes nothing it did not make.
        _replace_value(managed.wire, "api.example.com", PUBLIC, OTHER)
    writes = len(managed.wire.writes())

    released = managed.dns.release("shop.example.com")

    assert [r.outcome for r in released] in (
        [Release.CHANGED_EXTERNALLY],
        [Release.ALREADY_GONE],
    )
    assert managed.wire.values("api.example.com", "A") == [OTHER]
    assert len(managed.wire.writes()) == writes


def _replace_value(wire: Wire, name: str, old: str, new: str) -> None:
    if isinstance(wire, HetznerWire):
        for key, found in wire.sets.items():
            if key[1] == "api" and key[2] == "A":
                found["records"] = [{"value": new}]
    elif isinstance(wire, GandiWire):
        wire.sets[("example.com", "api", "A")]["rrset_values"] = [new]
    elif isinstance(wire, Route53Wire):
        wire.sets[(next(iter(wire.zones)), name, "A")]["values"] = [new]
    else:  # pragma: no cover - the wires above are all there are
        raise AssertionError("unknown wire")


def test_replacing_a_record_the_operator_made_follows_the_id_the_provider_gives(
    managed: Managed,
) -> None:
    managed.wire.seed("api.example.com", "A", OTHER)
    (stale,) = managed.dns.accounts.provider("acct").find_records(
        Zone(_zone_id(managed.wire), "example.com"), "api.example.com", "A"
    )

    result = managed.dns.ensure(
        "api.example.com",
        PUBLIC,
        app_domain="shop.example.com",
        replace_record=stale.id,
        replace_current=OTHER,
    )

    assert result.outcome is Ensured.REPLACED and result.record.owned is False
    assert managed.wire.values("api.example.com", "A") == [PUBLIC]
    (now,) = managed.dns.accounts.provider("acct").find_records(
        Zone(_zone_id(managed.wire), "example.com"), "api.example.com", "A"
    )
    assert result.record.remote_id == now.id


def _zone_id(wire: Wire) -> str:
    if isinstance(wire, HetznerWire):
        return next(iter(wire.zones))
    if isinstance(wire, Route53Wire):
        return next(iter(wire.zones))
    return "example.com"


def test_a_different_record_in_the_way_is_a_conflict_and_nothing_is_written(
    managed: Managed,
) -> None:
    from noust.managers.dns.records import DnsRecordConflict

    managed.wire.seed("api.example.com", "A", OTHER)

    with pytest.raises(DnsRecordConflict):
        managed.dns.ensure("api.example.com", PUBLIC, app_domain="shop.example.com")

    assert managed.wire.writes() == []


# -- the DNS-01 challenge ---------------------------------------------------------------


@pytest.fixture
def challenge_for(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str, dict[str, object] | None], tuple[DnsChallenge, Case, list[float]]]:
    def make(
        kind: str, settings: dict[str, object] | None = None
    ) -> tuple[DnsChallenge, Case, list[float]]:
        one = build(kind)
        DnsStore(store).save_account(DnsAccount(name="acct", kind=kind, settings=settings or {}))
        monkeypatch.setattr(DnsAccounts, "provider", lambda self, name: one.provider)
        waits: list[float] = []
        return DnsChallenge(DnsAccounts(store=store), sleep=waits.append), one, waits

    return make


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("digitalocean", DEFAULT_PROPAGATION),
        ("gandi", DEFAULT_PROPAGATION),
        ("hetzner", KINDS["hetzner"].propagation),
        ("route53", KINDS["route53"].propagation),
    ],
)
def test_a_challenge_waits_as_long_as_the_provider_usually_takes(
    challenge_for: Callable[..., tuple[DnsChallenge, Case, list[float]]],
    kind: str,
    expected: int,
) -> None:
    challenge, _, waits = challenge_for(kind)

    challenge.present("acct", "shop.example.com", ORDER)

    assert waits == [float(expected)]
    assert KINDS["route53"].propagation > DEFAULT_PROPAGATION


def test_the_accounts_own_wait_beats_the_providers_default(
    challenge_for: Callable[..., tuple[DnsChallenge, Case, list[float]]],
) -> None:
    challenge, _, waits = challenge_for("route53", {"propagation_seconds": 5})

    challenge.present("acct", "shop.example.com", ORDER)

    assert waits == [5.0]


@pytest.mark.parametrize("kind", ["digitalocean", "gandi", "route53"])
def test_where_there_is_no_note_cleanup_removes_this_orders_value_and_only_that(
    challenge_for: Callable[..., tuple[DnsChallenge, Case, list[float]]], kind: str
) -> None:
    challenge, one, _ = challenge_for(kind)
    name = "_acme-challenge.shop.example.com"
    one.wire.seed(name, "TXT", "Z" * 43)  # another tool's, with another value

    challenge.present("acct", "shop.example.com", ORDER)
    assert sorted(one.wire.values(name, "TXT")) == sorted(["Z" * 43, ORDER])

    assert challenge.cleanup("acct", "shop.example.com", ORDER) == 1
    assert one.wire.values(name, "TXT") == ["Z" * 43]
    assert challenge.cleanup("acct", "shop.example.com", ORDER) == 0


def test_where_notes_are_kept_cleanup_still_needs_the_note(
    challenge_for: Callable[..., tuple[DnsChallenge, Case, list[float]]],
) -> None:
    challenge, one, _ = challenge_for("hetzner")
    assert isinstance(one.wire, HetznerWire)
    name = "_acme-challenge.shop.example.com"
    one.wire.seed(name, "TXT", ORDER, comment="added by hand for another tool")

    assert challenge.cleanup("acct", "shop.example.com", ORDER) == 0
    challenge.present("acct", "shop.example.com", "B" * 43)
    assert one.wire.comments(name, "TXT") == ["added by hand for another tool", CHALLENGE_COMMENT]
    assert challenge.cleanup("acct", "shop.example.com", "B" * 43) == 1
    assert one.wire.values(name, "TXT") == [ORDER]


def test_a_wildcard_and_its_base_share_a_name_at_a_provider_that_keeps_sets(
    challenge_for: Callable[..., tuple[DnsChallenge, Case, list[float]]],
) -> None:
    challenge, one, _ = challenge_for("gandi")
    name = "_acme-challenge.example.com"

    challenge.present("acct", "example.com", ORDER)
    challenge.present("acct", "*.example.com", "B" * 43)

    assert sorted(one.wire.values(name, "TXT")) == sorted([ORDER, "B" * 43])
    assert challenge.cleanup("acct", "*.example.com", "B" * 43) == 1
    assert one.wire.values(name, "TXT") == [ORDER]
