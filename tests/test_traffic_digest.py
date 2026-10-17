# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an access log says about a site, by the hour (checklist G-08).

The promises defended here are about what is kept and what is not: no query
string ever, a client is its network and not its address, a page is a pattern and
not an id, and a probe of a thousand paths cannot make an hour grow without limit.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from noust.monitor.traffic import count_lines
from noust.monitor.traffic_digest import (
    DIRECT,
    KEYS_PER_HOUR,
    analyse,
    network_of,
    normalize_path,
    parse_time,
    referrer_host,
)

HOUR_10 = int(datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc).timestamp())


def line(
    status: int = 200,
    path: str = "/",
    *,
    ip: str = "203.0.113.9",
    time: str = "29/Sep/2026:10:15:01 +0000",
    sent: str = "512",
    referrer: str = "-",
    method: str = "GET",
) -> str:
    return (
        f'{ip} - - [{time}] "{method} {path} HTTP/1.1" {status} {sent} '
        f'"{referrer}" "Mozilla/5.0 (X11; Linux)"\n'
    )


class TestTheTime:
    def test_a_utc_time_is_read(self) -> None:
        assert parse_time(b"29/Sep/2026:10:15:01 +0000") == HOUR_10 + 15 * 60 + 1

    def test_a_zone_is_taken_into_account(self) -> None:
        # 12:15 in UTC+2 is 10:15 UTC; 06:15 in UTC-4 is 10:15 UTC.
        assert parse_time(b"29/Sep/2026:12:15:01 +0200") == HOUR_10 + 15 * 60 + 1
        assert parse_time(b"29/Sep/2026:06:15:01 -0400") == HOUR_10 + 15 * 60 + 1

    @pytest.mark.parametrize(
        "raw", [b"", b"nonsense", b"29/Foo/2026:10:15:01 +0000", b"32/Sep/2026:10:15:01 +0000"]
    )
    def test_a_time_that_is_not_one_is_none(self, raw: bytes) -> None:
        assert parse_time(raw) is None


class TestThePage:
    @pytest.mark.parametrize(
        ("request_line", "page"),
        [
            ("GET / HTTP/1.1", "GET /"),
            ("GET /pricing HTTP/1.1", "GET /pricing"),
            ("GET /orders/48211 HTTP/1.1", "GET /orders/:n"),
            ("POST /api/v1/users/7/avatar HTTP/2.0", "POST /api/v1/users/:n/avatar"),
            (
                "GET /files/3f2504e0-4f89-11d3-9a0c-0305e82c3301/download HTTP/1.1",
                "GET /files/:uuid/download",
            ),
            (
                "GET /static/4f8a9c0e1b2d3f4a5b6c7d8e9f001122.js HTTP/1.1",
                "GET /static/4f8a9c0e1b2d3f4a5b6c7d8e9f001122.js",
            ),
            ("GET /s/4f8a9c0e1b2d3f4a5b6c7d8e9f001122 HTTP/1.1", "GET /s/:hash"),
        ],
    )
    def test_an_id_is_a_pattern(self, request_line: str, page: str) -> None:
        assert normalize_path(request_line) == page

    def test_the_query_string_is_never_kept(self) -> None:
        page = normalize_path("GET /reset?token=SECRET123&email=a@b.co HTTP/1.1")

        assert page == "GET /reset"
        assert "SECRET" not in page and "@" not in page

    def test_a_fragment_is_never_kept(self) -> None:
        assert normalize_path("GET /a#frag HTTP/1.1") == "GET /a"

    @pytest.mark.parametrize(
        "garbage",
        [
            "",
            "\\x16\\x03\\x01",
            "GET",
            "CONNECT example.com:443 HTTP/1.1",
            "GET http://x/y HTTP/1.1",
        ],
    )
    def test_what_is_not_a_page_of_the_site_has_none(self, garbage: str) -> None:
        assert normalize_path(garbage) is None

    def test_a_very_long_path_is_cut(self) -> None:
        assert len(normalize_path("GET /" + "a" * 5000 + " HTTP/1.1") or "") <= 200


class TestTheClient:
    def test_an_ipv4_client_is_its_24(self) -> None:
        assert network_of("203.0.113.77") == "203.0.113.0/24"

    def test_an_ipv6_client_is_its_48(self) -> None:
        assert network_of("2001:db8:1234:5678::9") == "2001:db8:1234::/48"

    def test_an_ipv4_mapped_address_is_the_ipv4_network(self) -> None:
        assert network_of("::ffff:203.0.113.77") == "203.0.113.0/24"

    @pytest.mark.parametrize("raw", ["-", "", "localhost", "999.1.1.1"])
    def test_what_is_not_an_address_has_no_network(self, raw: str) -> None:
        assert network_of(raw) is None

    def test_the_exact_address_is_nowhere_in_the_digest(self) -> None:
        digest = analyse(line(ip="203.0.113.77").encode())

        keys = [key for counter in digest.hours[HOUR_10].keys.values() for key in counter]
        assert not any("203.0.113.77" in key for key in keys)
        assert "203.0.113.0/24" in keys


class TestTheReferrer:
    def test_only_the_host_is_kept(self) -> None:
        assert referrer_host("https://news.example.org/a/b?utm=1#x") == "news.example.org"

    @pytest.mark.parametrize("raw", ["-", "", "not a url"])
    def test_no_referrer_is_direct(self, raw: str) -> None:
        assert referrer_host(raw) == DIRECT


class TestTheDigest:
    def test_the_totals_are_what_the_plain_count_gives(self) -> None:
        data = (line(200) + line(404) + line(500) + line(502) + line(301)).encode()

        digest = analyse(data)

        assert (digest.requests, digest.errors_5xx) == count_lines(data) == (5, 2)

    def test_a_request_is_counted_in_the_hour_it_happened(self) -> None:
        data = (
            line(time="29/Sep/2026:10:59:59 +0000")
            + line(time="29/Sep/2026:11:00:00 +0000")
            + line(500, time="29/Sep/2026:11:30:00 +0000")
        ).encode()

        digest = analyse(data)

        assert sorted(digest.hours) == [HOUR_10, HOUR_10 + 3600]
        assert digest.hours[HOUR_10].requests == 1
        assert digest.hours[HOUR_10 + 3600].requests == 2
        assert digest.hours[HOUR_10 + 3600].classes[5] == 1

    def test_how_requests_ended_bytes_and_the_rankings(self) -> None:
        data = (
            line(200, "/", sent="1000", referrer="https://news.example.org/x")
            + line(200, "/", sent="500")
            + line(404, "/missing", sent="-")
            + line(500, "/orders/1", ip="198.51.100.4")
        ).encode()

        counts = analyse(data).hours[HOUR_10]

        assert counts.requests == 4
        assert (counts.classes[2], counts.classes[4], counts.classes[5]) == (2, 1, 1)
        assert counts.bytes == 1000 + 500 + 512
        assert counts.keys["path"]["GET /"] == 2
        assert counts.keys["path"]["GET /orders/:n"] == 1
        assert counts.keys["status"] == {"200": 2, "404": 1, "500": 1}
        assert counts.keys["network"]["203.0.113.0/24"] == 3
        assert counts.keys["network"]["198.51.100.0/24"] == 1
        assert counts.keys["referrer"]["news.example.org"] == 1
        assert counts.keys["referrer"][DIRECT] == 3

    def test_a_line_in_the_common_format_is_still_a_request(self) -> None:
        common = b'203.0.113.9 - - [29/Sep/2026:10:15:01 +0000] "GET /a HTTP/1.1" 200 12\n'

        digest = analyse(common)

        assert digest.requests == 1
        assert digest.hours[HOUR_10].keys["referrer"][DIRECT] == 1

    def test_a_line_whose_time_is_unreadable_counts_in_the_totals_only(self) -> None:
        digest = analyse(line(time="garbled").encode())

        assert digest.requests == 1 and digest.hours == {}

    def test_garbage_is_not_a_request(self) -> None:
        assert analyse(b"\n\nnot a log line\n1.2.3.4 - -\n").requests == 0

    def test_a_scan_of_a_thousand_paths_keeps_a_bounded_ranking(self) -> None:
        data = "".join(line(404, f"/probe-{i}.php") for i in range(1000))
        data += line(200, "/") * 5

        counts = analyse(data.encode()).hours[HOUR_10]

        assert counts.requests == 1005
        assert len(counts.keys["path"]) == KEYS_PER_HOUR
        assert counts.keys["path"]["GET /"] == 5  # the real page survives the noise

    def test_merging_adds(self) -> None:
        one = analyse(line(200, "/a").encode()).hours[HOUR_10]
        two = analyse((line(500, "/a") + line(200, "/b")).encode()).hours[HOUR_10]

        one.merge(two)

        assert one.requests == 3 and one.classes[5] == 1
        assert one.keys["path"]["GET /a"] == 2
