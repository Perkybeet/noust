# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust DNS in the store (4.1, item 104).

What is pinned: the tables exist in a fresh store and in one a development build stamped v16
before the area had its fragment, every value is judged before it is written, a record is one
name, type and content, a zone goes with its records, and a zone can be put back as it was.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from noust.core import schema_v16
from noust.core.authdns_store import (
    MAX_RECORDS_PER_ZONE,
    PRIMARY,
    SECONDARY,
    AuthDnsServer,
    AuthDnsStore,
    AuthDnsZone,
    check_address,
)
from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore


@pytest.fixture
def dns(store: NoustStore) -> AuthDnsStore:
    return AuthDnsStore(store)


def a_zone(name: str = "example.com") -> AuthDnsZone:
    return AuthDnsZone(name, ("ns1.example.net", "ns2.example.net"), "hostmaster@example.com")


class TestTheFragment:
    def test_a_fresh_store_has_the_tables(self, tmp_path: Path, store: NoustStore) -> None:
        connection = sqlite3.connect(tmp_path / "noust.db")
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        finally:
            connection.close()

        assert {
            "authdns_server",
            "authdns_zones",
            "authdns_records",
            "authdns_secondaries",
        } <= tables

    def test_a_store_stamped_v16_without_the_area_is_completed(self, tmp_path: Path) -> None:
        path = tmp_path / "noust.db"
        NoustStore.reset_instance()
        NoustStore(path)
        NoustStore.reset_instance()
        connection = sqlite3.connect(path)
        for table in ("authdns_records", "authdns_zones", "authdns_secondaries", "authdns_server"):
            connection.execute(f"DROP TABLE {table}")
        connection.commit()
        assert schema_v16.incomplete(connection.cursor())
        connection.close()

        NoustStore(path)
        NoustStore.reset_instance()

        connection = sqlite3.connect(path)
        try:
            assert not schema_v16.incomplete(connection.cursor())
        finally:
            connection.close()


class TestThisServer:
    def test_there_is_no_part_until_one_is_saved(self, dns: AuthDnsStore) -> None:
        assert dns.server() is None

    def test_a_part_is_kept_and_replaced(self, dns: AuthDnsStore) -> None:
        first = dns.save_server(
            AuthDnsServer(
                engine="knot",
                role=PRIMARY,
                hostname="NS1.Example.net.",
                listen=("203.0.113.10", "2001:0db8::10"),
            ),
            actor="cli:root",
        )
        second = dns.save_server(
            AuthDnsServer(
                engine="knot",
                role=PRIMARY,
                hostname="ns1.example.net",
                listen=("203.0.113.11",),
                firewall={"made": [{"port": 53, "proto": "udp"}]},
            ),
            actor="someone else",
        )

        assert first.hostname == "ns1.example.net"
        assert first.listen == ("203.0.113.10", "2001:db8::10")
        assert second.listen == ("203.0.113.11",)
        assert second.created_by == "cli:root"
        assert second.created_at == first.created_at
        assert dns.server() == second
        assert second.firewall["made"][0]["port"] == 53

    @pytest.mark.parametrize(
        "server",
        [
            AuthDnsServer("knot", "boss"),
            AuthDnsServer("", PRIMARY),
            AuthDnsServer("knot", PRIMARY, hostname="not a host"),
            AuthDnsServer("knot", PRIMARY, listen=("0.0.0.0",)),  # noqa: S104 - refused, never bound
            AuthDnsServer("knot", PRIMARY, listen=("127.0.0.1",)),
            AuthDnsServer("knot", PRIMARY, listen=("224.0.0.1",)),
            AuthDnsServer("knot", PRIMARY, listen=("nope",)),
            AuthDnsServer("knot", SECONDARY, primary_address="x"),
        ],
    )
    def test_what_cannot_be_kept_is_refused_before_it_is_written(
        self, dns: AuthDnsStore, server: AuthDnsServer
    ) -> None:
        with pytest.raises(ValidationError):
            dns.save_server(server)

        assert dns.server() is None

    def test_an_address_is_canonical_and_not_one_a_server_cannot_have(self) -> None:
        assert check_address(" 2001:0DB8:0:0:0:0:0:1 ") == "2001:db8::1"
        for bad in ("::", "::1", "ff02::1", "0.0.0.0", "10.0.0.1/8", "", None, 5):  # noqa: S104
            with pytest.raises(ValidationError):
                check_address(bad)

    def test_forgetting_the_part_forgets_the_zones_and_the_secondaries(
        self, dns: AuthDnsStore
    ) -> None:
        dns.save_server(AuthDnsServer("knot", PRIMARY))
        dns.create_zone(a_zone())
        dns.add_record("example.com", "www", "A", "192.0.2.1")
        dns.add_secondary("198.51.100.7", "ns2.example.net")

        assert dns.delete_server() is True

        assert dns.server() is None and dns.zones() == [] and dns.secondaries() == []
        assert dns.delete_server() is False


class TestZones:
    def test_a_zone_is_kept_with_its_name_servers_in_order(self, dns: AuthDnsStore) -> None:
        zone = dns.create_zone(a_zone("Example.COM."), actor="api:alice")

        assert zone.name == "example.com"
        assert zone.nameservers == ("ns1.example.net", "ns2.example.net")
        assert (zone.default_ttl, zone.dnssec, zone.created_by) == (3600, False, "api:alice")
        assert dns.zones() == [zone] and dns.zone("example.com") == zone

    def test_a_zone_cannot_be_made_twice(self, dns: AuthDnsStore) -> None:
        dns.create_zone(a_zone())

        with pytest.raises(ValidationError) as raised:
            dns.create_zone(a_zone())

        assert "already a zone" in raised.value.message

    @pytest.mark.parametrize(
        "zone",
        [
            AuthDnsZone("bad zone", ("ns1.example.net",), "a@example.com"),
            AuthDnsZone("example.com", (), "a@example.com"),
            AuthDnsZone("example.com", ("ns1",), "a@example.com"),
            AuthDnsZone("example.com", ("ns1.example.net",), "nobody"),
            AuthDnsZone("example.com", ("ns1.example.net",), "a@example.com", default_ttl=1),
        ],
    )
    def test_what_cannot_be_kept_is_refused(self, dns: AuthDnsStore, zone: AuthDnsZone) -> None:
        with pytest.raises(ValidationError):
            dns.create_zone(zone)

        assert dns.zones() == []

    def test_a_change_keeps_what_is_not_given(self, dns: AuthDnsStore) -> None:
        dns.create_zone(a_zone())

        changed = dns.update_zone("example.com", dnssec=True, default_ttl=600, actor="cli:root")

        assert changed.dnssec is True and changed.default_ttl == 600
        assert changed.nameservers == ("ns1.example.net", "ns2.example.net")
        assert changed.contact == "hostmaster@example.com" and changed.updated_by == "cli:root"

    def test_changing_a_zone_that_is_not_there_is_an_error(self, dns: AuthDnsStore) -> None:
        with pytest.raises(ValidationError):
            dns.update_zone("nope.example", dnssec=True)

    def test_a_zone_goes_with_its_records(self, dns: AuthDnsStore) -> None:
        dns.create_zone(a_zone())
        dns.add_record("example.com", "www", "A", "192.0.2.1")

        assert dns.delete_zone("example.com") is True

        assert dns.records("example.com") == []
        assert dns.delete_zone("example.com") is False


class TestRecords:
    @pytest.fixture(autouse=True)
    def _zone(self, dns: AuthDnsStore) -> None:
        dns.create_zone(a_zone())

    def test_a_record_is_kept_in_its_canonical_form(self, dns: AuthDnsStore) -> None:
        record = dns.add_record("example.com", "WWW", "a", " 192.0.2.1 ", ttl=300, actor="cli:root")
        mail = dns.add_record("example.com", "@", "mx", "10 Mail.Example.net.")

        assert (record.name, record.type, record.content, record.ttl) == (
            "www",
            "A",
            "192.0.2.1",
            300,
        )
        assert mail.content == "10 mail.example.net" and mail.ttl is None
        assert record.id is not None and dns.record(record.id) == record

    def test_the_apex_comes_first(self, dns: AuthDnsStore) -> None:
        dns.add_record("example.com", "www", "A", "192.0.2.1")
        dns.add_record("example.com", "@", "TXT", "hello")
        dns.add_record("example.com", "api", "A", "192.0.2.2")

        assert [r.name for r in dns.records("example.com")] == ["@", "api", "www"]

    def test_the_same_record_twice_is_refused_but_two_values_at_one_name_are_fine(
        self, dns: AuthDnsStore
    ) -> None:
        dns.add_record("example.com", "www", "A", "192.0.2.1")
        dns.add_record("example.com", "www", "A", "192.0.2.2")

        with pytest.raises(ValidationError) as raised:
            dns.add_record("example.com", "www", "A", "192.0.2.1")

        assert "already has that A record" in raised.value.message
        assert len(dns.records("example.com")) == 2

    @pytest.mark.parametrize(
        ("name", "kind", "content"),
        [
            ("www", "A", "not an address"),
            ("www", "SOA", "x"),
            ("www example", "A", "192.0.2.1"),
            ("www", "TXT", "a\nb"),
        ],
    )
    def test_what_cannot_be_kept_is_refused(
        self, dns: AuthDnsStore, name: str, kind: str, content: str
    ) -> None:
        with pytest.raises(ValidationError):
            dns.add_record("example.com", name, kind, content)

        assert dns.records("example.com") == []

    def test_a_record_of_a_zone_that_is_not_there_is_refused(self, dns: AuthDnsStore) -> None:
        with pytest.raises(ValidationError):
            dns.add_record("nope.example", "www", "A", "192.0.2.1")

    def test_a_record_changes_its_content_and_ttl_and_goes_back_to_the_zones_ttl(
        self, dns: AuthDnsStore
    ) -> None:
        record = dns.add_record("example.com", "www", "A", "192.0.2.1", ttl=300)
        assert record.id is not None

        moved = dns.update_record(record.id, content="192.0.2.9", actor="api:bob")
        slower = dns.update_record(record.id, ttl=900)
        default = dns.update_record(record.id, clear_ttl=True)

        assert moved.content == "192.0.2.9" and moved.ttl == 300 and moved.updated_by == "api:bob"
        assert slower.ttl == 900 and default.ttl is None

    def test_a_change_into_a_record_that_is_there_is_refused(self, dns: AuthDnsStore) -> None:
        dns.add_record("example.com", "www", "A", "192.0.2.1")
        other = dns.add_record("example.com", "www", "A", "192.0.2.2")
        assert other.id is not None

        with pytest.raises(ValidationError):
            dns.update_record(other.id, content="192.0.2.1")

        assert dns.record(other.id) == other

    def test_a_record_that_is_not_there_cannot_be_changed_and_deleting_it_says_so(
        self, dns: AuthDnsStore
    ) -> None:
        with pytest.raises(ValidationError):
            dns.update_record(999, content="192.0.2.1")
        assert dns.delete_record(999) is False

    def test_a_zone_holds_a_bounded_number_of_records(
        self, dns: AuthDnsStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.core.authdns_store.MAX_RECORDS_PER_ZONE", 2)
        dns.add_record("example.com", "a", "A", "192.0.2.1")
        dns.add_record("example.com", "b", "A", "192.0.2.2")

        with pytest.raises(ValidationError) as raised:
            dns.add_record("example.com", "c", "A", "192.0.2.3")

        assert "already has 2 records" in raised.value.message
        assert MAX_RECORDS_PER_ZONE >= 1000


class TestPuttingAZoneBack:
    def test_a_zone_comes_back_as_it_was_with_the_numbers_of_its_records(
        self, dns: AuthDnsStore
    ) -> None:
        dns.create_zone(a_zone())
        keep = dns.add_record("example.com", "www", "A", "192.0.2.1")
        snapshot = dns.snapshot("example.com")
        assert snapshot is not None

        dns.update_zone("example.com", dnssec=True)
        dns.delete_record(keep.id or 0)
        dns.add_record("example.com", "other", "A", "192.0.2.5")
        dns.restore(snapshot)

        assert dns.zone("example.com") == snapshot.zone
        assert dns.records("example.com") == [keep]

    def test_a_zone_that_was_deleted_is_made_again(self, dns: AuthDnsStore) -> None:
        dns.create_zone(a_zone())
        dns.add_record("example.com", "www", "A", "192.0.2.1")
        snapshot = dns.snapshot("example.com")
        assert snapshot is not None

        dns.delete_zone("example.com")
        dns.restore(snapshot)

        assert [r.content for r in dns.records("example.com")] == ["192.0.2.1"]

    def test_there_is_nothing_to_take_of_a_zone_that_is_not_there(self, dns: AuthDnsStore) -> None:
        assert dns.snapshot("nope.example") is None


class TestSecondaries:
    def test_a_secondary_is_kept_by_address_and_named_again_without_duplicating(
        self, dns: AuthDnsStore
    ) -> None:
        dns.add_secondary("198.51.100.7", "ns2.example.net", actor="cli:root")
        again = dns.add_secondary("198.51.100.7", "NS3.example.net")

        assert [(s.address, s.hostname) for s in dns.secondaries()] == [
            ("198.51.100.7", "ns3.example.net")
        ]
        assert again.added_by == "cli:root"

    def test_a_secondary_is_removed_once(self, dns: AuthDnsStore) -> None:
        dns.add_secondary("198.51.100.7")

        assert dns.remove_secondary("198.51.100.7") is True
        assert dns.remove_secondary("198.51.100.7") is False

    def test_what_is_not_an_address_or_a_name_is_refused(self, dns: AuthDnsStore) -> None:
        for address, name in (("nope", None), ("198.51.100.7", "bad name"), ("127.0.0.1", None)):
            with pytest.raises(ValidationError):
                dns.add_secondary(address, name)
        assert dns.secondaries() == []
