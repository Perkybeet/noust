# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a person may write in a zone of Noust DNS (4.1, item 104).

A zone file is a language, so the validators read every record into the fields its type has and
the file is written from the fields. What is pinned: the canonical form of each type (the same
record always has the same text), the names that are refused, and the shapes that have injected a
line into a zone file elsewhere.
"""

from __future__ import annotations

import pytest

from noust.core.exceptions import ValidationError
from noust.validators.dns_zone import (
    check_contact,
    check_hostname,
    check_nameservers,
    check_record,
    check_record_name,
    check_record_type,
    check_ttl,
    check_zone_name,
)


class TestZoneNames:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("example.com", "example.com"),
            ("Example.COM.", "example.com"),
            ("  sub.example.co.uk ", "sub.example.co.uk"),
            ("xn--bcher-kva.example", "xn--bcher-kva.example"),
            ("10.in-addr.arpa", "10.in-addr.arpa"),
        ],
    )
    def test_a_name_is_read_in_lower_case_without_the_final_dot(
        self, given: str, expected: str
    ) -> None:
        assert check_zone_name(given) == expected

    @pytest.mark.parametrize(
        "given",
        [
            "",
            "com",
            "-a.example.com",
            "a-.example.com",
            "a..example.com",
            "exa mple.com",
            "example.com\n",
            "example.com/../x",
            "under_score.example.com",
            "1.2.3.4",
            "catalog.invalid",
            "a" * 64 + ".com",
            ".".join(["abc"] * 80) + ".com",
            "example.com;",
        ],
    )
    def test_what_cannot_be_a_zone_is_refused(self, given: str) -> None:
        with pytest.raises(ValidationError):
            check_zone_name(given)

    def test_something_that_is_not_text_is_refused(self) -> None:
        for value in (None, 5, ["example.com"], b"example.com"):
            with pytest.raises(ValidationError):
                check_zone_name(value)


class TestRecordNames:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("@", "@"),
            ("www", "www"),
            ("WWW.Apps", "www.apps"),
            ("*", "*"),
            ("*.apps", "*.apps"),
            ("_dmarc", "_dmarc"),
            ("_sip._tcp", "_sip._tcp"),
            ("www.example.com.", "www"),
            ("example.com.", "@"),
        ],
    )
    def test_a_name_is_relative_to_its_zone(self, given: str, expected: str) -> None:
        assert check_record_name(given, "example.com") == expected

    @pytest.mark.parametrize(
        "given",
        [
            "",
            "a..b",
            "a.*.b",
            "-a",
            "a-",
            "a b",
            "a\nb",
            "www.example.org.",
            "$ORIGIN",
            "a" * 64,
            "a;b",
            "(x)",
        ],
    )
    def test_what_cannot_name_a_record_is_refused(self, given: str) -> None:
        with pytest.raises(ValidationError):
            check_record_name(given, "example.com")


class TestRecordContent:
    @pytest.mark.parametrize(
        ("kind", "given", "expected"),
        [
            ("A", " 203.0.113.7 ", "203.0.113.7"),
            ("AAAA", "2001:0DB8:0:0::7", "2001:db8::7"),
            ("CNAME", "Target.Example.NET.", "target.example.net"),
            ("NS", "ns1.example.net", "ns1.example.net"),
            ("PTR", "host.example.net", "host.example.net"),
            ("MX", "10   Mail.example.net.", "10 mail.example.net"),
            ("MX", "0 .", "0 ."),
            ("SRV", "10 5 5060 sip.example.net", "10 5 5060 sip.example.net"),
            ("CAA", '0 issue "letsencrypt.org"', '0 issue "letsencrypt.org"'),
            ("CAA", "0 ISSUE letsencrypt.org", '0 issue "letsencrypt.org"'),
            ("DS", "12345 13 2 abcdef0123456789", "12345 13 2 ABCDEF0123456789"),
            ("TXT", '"v=spf1 -all"', "v=spf1 -all"),
            ("TXT", "v=DKIM1; k=rsa; p=MIIB", "v=DKIM1; k=rsa; p=MIIB"),
        ],
    )
    def test_the_same_record_always_has_the_same_text(
        self, kind: str, given: str, expected: str
    ) -> None:
        assert check_record(kind, given) == expected

    @pytest.mark.parametrize(
        ("kind", "given"),
        [
            ("A", "999.1.1.1"),
            ("A", "2001:db8::1"),
            ("A", "1.2.3.4\n"),
            ("A", "1.2.3.4 ; x"),
            ("AAAA", "1.2.3.4"),
            ("CNAME", "."),
            ("CNAME", "a b.example.net"),
            ("CNAME", "target"),
            ("MX", "mail.example.net"),
            ("MX", "70000 mail.example.net"),
            ("MX", "-1 mail.example.net"),
            ("MX", "10 mail.example.net extra"),
            ("SRV", "10 5 sip.example.net"),
            ("SRV", "10 5 70000 sip.example.net"),
            ("CAA", "0 issue"),
            ("CAA", '0 issue "a"b"'),
            ("CAA", "256 issue x"),
            ("DS", "1 13 2 xyz"),
            ("DS", "1 13 2 abc"),
            ("TXT", ""),
            ("TXT", "line\nbreak"),
            ("TXT", "$INCLUDE /etc/passwd\r\nx"),
            ("TXT", "a\x00b"),
            ("TXT", "a" * 3000),
            ("TXT", "reorder \u202e text"),
        ],
    )
    def test_what_is_not_the_content_of_that_type_is_refused(self, kind: str, given: str) -> None:
        with pytest.raises(ValidationError):
            check_record(kind, given)

    def test_a_txt_with_quotes_inside_stays_as_it_is_and_is_the_renderers_to_escape(self) -> None:
        assert check_record("TXT", 'say "hi"') == 'say "hi"'


class TestTheRest:
    def test_a_type_is_upper_case_and_only_the_offered_ones(self) -> None:
        assert check_record_type(" mx ") == "MX"
        for bad in ("SOA", "DNSKEY", "RRSIG", "AXFR", "ANY", "", "A\n"):
            with pytest.raises(ValidationError):
                check_record_type(bad)

    def test_a_ttl_is_a_whole_number_in_range(self) -> None:
        assert check_ttl(3600) == 3600
        for bad in (0, 29, 604801, -1, True, "3600", 3600.0, None):
            with pytest.raises(ValidationError):
                check_ttl(bad)

    def test_name_servers_are_host_names_without_repeats(self) -> None:
        assert check_nameservers(["NS1.example.net.", "ns2.example.net", "ns1.example.net"]) == [
            "ns1.example.net",
            "ns2.example.net",
        ]
        for bad in ([], ["."], ["ns1"], "ns1.example.net", ["a"] * 9, [["x"]], [None]):
            with pytest.raises(ValidationError):
                check_nameservers(bad)

    def test_a_contact_is_an_address_with_a_host_name_domain(self) -> None:
        assert check_contact("Hostmaster@Example.COM") == "Hostmaster@example.com"
        for bad in ("", "hostmaster", "@example.com", "a b@example.com", "a@b", ".a@example.com"):
            with pytest.raises(ValidationError):
                check_contact(bad)

    def test_a_host_name_may_be_the_root_only_where_the_type_says_so(self) -> None:
        assert check_hostname(".") == "."
        assert check_hostname("A.Example.com.") == "a.example.com"
        for bad in ("", "x", "a_b.example.com", "a.example.com\n"):
            with pytest.raises(ValidationError):
                check_hostname(bad)
