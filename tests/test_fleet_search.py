# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Searching the things of every server (``noust.fleet.search``).

What is pinned: every word of the query must be found, in the name, the second identifier, the
words the record carries or the server it is on; a name that is the query comes before one that
starts with it, before one that has it inside, before one only a word of the record has; kinds
order the rest; the answer says how many matched and whether it was cut; and the query is data,
never a pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.fleet.search import KINDS, search


def _row(
    kind: str,
    name: str,
    node: str = "web-1",
    detail: str | None = None,
    words: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "node": node,
        "local": False,
        "page": f"/{kind}s/{name}",
        "href": f"/n/{node}/{kind}s/{name}",
        "detail": detail,
        "words": words or [],
    }


ROWS = [
    _row("app", "shop.example.com", "web-1", "nodejs", ["https://git.example.net/shop.git"]),
    _row("app", "blog.example.com", "web-2", "static"),
    _row("database", "shop", "web-1", "postgresql", ["shop_owner", "shop.example.com"]),
    _row(
        "site", "shop.example.com", "web-1", "nginx", ["shop.example.com", "www.shop.example.com"]
    ),
    _row("certificate", "shop.example.com", "web-1", "Let's Encrypt"),
    _row("cron", "nightly", "web-2", "daily", ["/usr/bin/true", "blog.example.com"]),
    _row("server", "web-1", "web-1", "4.1.0", ["env=prod", "env", "prod"]),
    _row("server", "web-2", "web-2", "4.0.0"),
]


def _names(hits: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(hit["kind"], hit["name"], hit["node"]) for hit in hits]


class TestMatching:
    def test_a_word_is_found_in_the_name(self) -> None:
        hits, total, truncated = search(ROWS, "blog")

        assert _names(hits) == [
            ("app", "blog.example.com", "web-2"),
            ("cron", "nightly", "web-2"),  # its command names the blog's domain
        ]
        assert (total, truncated) == (2, False)

    def test_case_does_not_matter(self) -> None:
        upper, _, _ = search(ROWS, "SHOP")
        lower, _, _ = search(ROWS, "shop")

        assert upper == lower and upper

    def test_every_word_must_be_found_somewhere_in_the_same_record(self) -> None:
        hits, _, _ = search(ROWS, "shop postgresql")

        assert _names(hits) == [("database", "shop", "web-1")]

    def test_the_server_a_thing_is_on_is_one_of_its_words(self) -> None:
        hits, _, _ = search(ROWS, "web-2 example")

        assert _names(hits) == [
            ("app", "blog.example.com", "web-2"),
            ("cron", "nightly", "web-2"),
        ]

    def test_the_labels_of_a_server_find_it(self) -> None:
        hits, _, _ = search(ROWS, "env=prod")

        assert _names(hits) == [("server", "web-1", "web-1")]

    def test_nothing_matching_is_an_empty_answer(self) -> None:
        assert search(ROWS, "no-such-thing") == ([], 0, False)

    @pytest.mark.parametrize("query", ["(", ".*", "[a-z]+", "shop|blog", "\\", "%", "a" * 500])
    def test_the_query_is_text_never_a_pattern(self, query: str) -> None:
        hits, total, _ = search(ROWS, query)

        assert (hits, total) == ([], 0)


class TestOrder:
    def test_the_name_that_is_the_query_comes_first_then_the_ones_that_start_with_it(self) -> None:
        hits, _, _ = search(ROWS, "shop")

        assert _names(hits)[:2] == [
            ("database", "shop", "web-1"),
            ("app", "shop.example.com", "web-1"),
        ]

    def test_a_name_that_has_the_word_comes_before_a_record_that_only_mentions_it(self) -> None:
        rows = [
            _row("cron", "backup", "web-1", None, ["shop.example.com"]),
            _row("app", "old-shop.example.com", "web-1"),
        ]

        hits, _, _ = search(rows, "shop")

        assert _names(hits) == [
            ("app", "old-shop.example.com", "web-1"),
            ("cron", "backup", "web-1"),
        ]

    def test_among_equals_the_kind_decides_then_the_server(self) -> None:
        hits, _, _ = search(ROWS, "shop.example.com")

        assert _names(hits)[:3] == [
            ("app", "shop.example.com", "web-1"),
            ("site", "shop.example.com", "web-1"),
            ("certificate", "shop.example.com", "web-1"),
        ]

    def test_the_same_name_on_two_servers_is_two_hits_in_the_order_of_the_servers(self) -> None:
        rows = [_row("app", "api", "web-2"), _row("app", "api", "web-1")]

        hits, _, _ = search(rows, "api")

        assert [hit["node"] for hit in hits] == ["web-1", "web-2"]


class TestKindsAndLimit:
    def test_only_the_kinds_asked(self) -> None:
        hits, total, _ = search(ROWS, "shop", kinds=["database", "site"])

        assert _names(hits) == [
            ("database", "shop", "web-1"),
            ("site", "shop.example.com", "web-1"),
        ]
        assert total == 2

    def test_the_answer_is_cut_at_the_limit_and_says_how_many_matched(self) -> None:
        hits, total, truncated = search(ROWS, "shop", limit=2)

        assert len(hits) == 2
        assert total > 2
        assert truncated is True

    def test_the_limit_is_at_least_one(self) -> None:
        hits, _, _ = search(ROWS, "shop", limit=0)

        assert len(hits) == 1

    def test_the_kinds_are_the_ones_the_index_makes(self) -> None:
        from noust.fleet.aggregate import RESOURCES

        assert {"server", "app", "database", "site", "certificate", "cron"} == set(KINDS)
        assert RESOURCES["index"].parts, "the index exists for the search to read"

    def test_a_hit_does_not_carry_the_words_it_was_found_by(self) -> None:
        hits, _, _ = search(ROWS, "shop")

        assert all("words" not in hit for hit in hits)
        assert all(
            {"kind", "name", "node", "local", "page", "href", "detail"} <= set(h) for h in hits
        )

    def test_a_server_keeps_its_labels(self) -> None:
        rows = [{**_row("server", "web-1", "web-1"), "labels": {"env": "prod"}}]

        hits, _, _ = search(rows, "web-1")

        assert hits[0]["labels"] == {"env": "prod"}
