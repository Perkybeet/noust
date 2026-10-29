# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The zone file Noust DNS writes (4.1, item 104).

The file is written from the fields of each record, never from text a person typed, so what is
pinned is that every type comes out as the one line the name server reads, that a value with
quotes or a backslash or a very long text cannot end its line or add another, and that the
output is the same for the same zone.
"""

from __future__ import annotations

import pytest

from noust.core.authdns_store import AuthDnsRecord, AuthDnsZone
from noust.managers.authdns.zonefile import (
    ZONE_FILE_MARKER,
    render_txt,
    render_zone,
    soa_contact,
)

ZONE = AuthDnsZone(
    "example.com",
    ("ns1.example.net", "ns2.example.net"),
    "hostmaster@example.com",
    default_ttl=3600,
)


def record(name: str, kind: str, content: str, ttl: int | None = None) -> AuthDnsRecord:
    return AuthDnsRecord(zone="example.com", name=name, type=kind, content=content, ttl=ttl)


def lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line and not line.startswith(";")]


class TestTheApex:
    def test_the_soa_and_the_name_servers_come_first_with_the_serial_given(self) -> None:
        text = render_zone(ZONE, [], serial=1793266825)

        assert lines(text) == [
            "$ORIGIN example.com.",
            "$TTL 3600",
            "@ IN SOA ns1.example.net. hostmaster.example.com. 1793266825 7200 3600 1209600 3600",
            "@ IN NS ns1.example.net.",
            "@ IN NS ns2.example.net.",
        ]
        assert ZONE_FILE_MARKER in text.splitlines()[0]

    def test_a_dot_in_the_contacts_name_is_escaped_and_the_domain_is_the_contacts(self) -> None:
        assert soa_contact("first.last@Example.org") == "first\\.last.example.org."


class TestEachType:
    @pytest.mark.parametrize(
        ("kind", "content", "line"),
        [
            ("A", "192.0.2.1", "www IN A 192.0.2.1"),
            ("AAAA", "2001:db8::1", "www IN AAAA 2001:db8::1"),
            ("CNAME", "target.example.net", "www IN CNAME target.example.net."),
            ("NS", "ns.child.example.net", "www IN NS ns.child.example.net."),
            ("PTR", "host.example.net", "www IN PTR host.example.net."),
            ("MX", "10 mail.example.net", "www IN MX 10 mail.example.net."),
            ("MX", "0 .", "www IN MX 0 ."),
            ("SRV", "10 5 5060 sip.example.net", "www IN SRV 10 5 5060 sip.example.net."),
            ("CAA", '0 issue "letsencrypt.org"', 'www IN CAA 0 issue "letsencrypt.org"'),
            ("DS", "12345 13 2 ABCDEF01", "www IN DS 12345 13 2 ABCDEF01"),
            ("TXT", "v=spf1 -all", 'www IN TXT "v=spf1 -all"'),
        ],
    )
    def test_a_record_is_one_line_in_the_form_the_server_reads(
        self, kind: str, content: str, line: str
    ) -> None:
        text = render_zone(ZONE, [record("www", kind, content)], serial=1)

        assert lines(text)[-1] == line

    def test_a_ttl_of_its_own_is_written_and_the_zones_is_not(self) -> None:
        text = render_zone(
            ZONE, [record("a", "A", "192.0.2.1", 300), record("b", "A", "192.0.2.2")], serial=1
        )

        assert lines(text)[-2:] == ["a 300 IN A 192.0.2.1", "b IN A 192.0.2.2"]

    def test_the_apex_and_a_wildcard_are_written_as_they_are(self) -> None:
        text = render_zone(
            ZONE, [record("@", "TXT", "hi"), record("*.apps", "A", "192.0.2.3")], serial=1
        )

        assert 'IN TXT "hi"' in text and "\n@ IN TXT" in text
        assert "*.apps IN A 192.0.2.3" in text


class TestText:
    def test_quotes_and_backslashes_are_escaped_and_cannot_end_the_string(self) -> None:
        assert render_txt('say "hi" \\ done') == '"say \\"hi\\" \\\\ done"'

    def test_a_character_outside_ascii_is_written_as_its_bytes(self) -> None:
        assert render_txt("café") == '"caf\\195\\169"'

    def test_a_long_text_is_cut_into_strings_of_at_most_255_bytes(self) -> None:
        rendered = render_txt("a" * 600)

        chunks = rendered.split(" ")
        assert [len(c) - 2 for c in chunks] == [255, 255, 90]
        assert all(c.startswith('"') and c.endswith('"') for c in chunks)

    def test_a_cut_never_lands_inside_an_escape(self) -> None:
        rendered = render_txt('"' * 300)

        for chunk in rendered.split(" "):
            body = chunk[1:-1]
            assert body.count('\\"') == body.count('"') and "\\" not in body.replace('\\"', "")

    def test_nothing_a_text_holds_can_add_a_line_or_a_directive(self) -> None:
        hostile = 'x"\n$INCLUDE /etc/passwd\n@ IN A 6.6.6.6'

        # The validator refuses this text; if one ever got through, the renderer still would not
        # let it end its line.
        rendered = render_zone(ZONE, [record("t", "TXT", hostile)], serial=1)

        assert "\n$INCLUDE" not in rendered and "\n@ IN A 6.6.6.6" not in rendered
        assert len(lines(rendered)) == 6


class TestTheSameZoneIsTheSameFile:
    def test_the_order_is_the_apex_then_name_type_and_content_whatever_the_order_given(
        self,
    ) -> None:
        mixed = [
            record("www", "A", "192.0.2.2"),
            record("@", "MX", "10 mail.example.net"),
            record("www", "A", "192.0.2.1"),
            record("api", "TXT", "x"),
        ]

        first = render_zone(ZONE, mixed, serial=5)
        second = render_zone(ZONE, list(reversed(mixed)), serial=5)

        assert first == second
        body = lines(first)[5:]
        assert [line.split()[0] for line in body] == ["@", "api", "www", "www"]
        assert body[-2:] == ["www IN A 192.0.2.1", "www IN A 192.0.2.2"]
