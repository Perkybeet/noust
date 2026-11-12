# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server says of its copies is judged before anything of it is used (item 195).

A server is not trusted with the central's disk, so every value that would reach a path or the store
is held to an allowlist, and what does not pass is not taken and is said, with why. What is pinned:
a good list is accepted whole; each way a server can be wrong or hostile (a name that climbs, a size
that fills the disk, a digest that is not one, a copy listed twice, a thousand copies of nothing)
costs only that copy; and a body that is not a list at all is a list of nothing, never a crash.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.fleet.collect.names import (
    MAX_ITEMS,
    MAX_PLAUSIBLE_BYTES,
    parse_catalogue,
    safe_segment,
)

DIGEST = "a" * 64


def item(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "app",
        "id": "shop-example-com_20261110_020000",
        "owner": "shop.example.com",
        "folder": "shop-example-com",
        "created_at": "2026-11-10T02:00:00+00:00",
        "origin": "scheduled",
        "sealed": True,
        "size": 130,
        "files": [
            {
                "name": "shop-example-com_20261110_020000.tar.gz",
                "size": 123,
                "sha256": DIGEST,
                "role": "archive",
            },
            {
                "name": "shop-example-com_20261110_020000.json",
                "size": 7,
                "sha256": None,
                "role": "sidecar",
            },
        ],
        "meta": {"domain": "shop.example.com"},
        "blocked": None,
    }
    body.update(changes)
    return body


def listed(*items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"fingerprint": "f" * 24, "items": list(items), **extra}


class TestAGoodList:
    def test_it_is_accepted_whole(self) -> None:
        catalogue = parse_catalogue(listed(item()))

        (accepted,) = catalogue.items
        assert catalogue.rejected == () and catalogue.fingerprint == "f" * 24
        assert accepted.main.name.endswith(".tar.gz") and accepted.size == 130
        assert accepted.created_at == "2026-11-10T02:00:00+00:00"
        assert accepted.origin == "scheduled" and accepted.sealed is True

    def test_a_server_that_says_nothing_changed_has_no_items(self) -> None:
        catalogue = parse_catalogue({"fingerprint": "abc", "unchanged": True, "items": [item()]})

        assert catalogue.unchanged and catalogue.items == ()

    def test_pieces_come_from_the_server_within_what_it_serves(self) -> None:
        catalogue = parse_catalogue(listed(piece_size=9_000_000, max_piece=2_000_000))

        assert catalogue.piece_size == 2_000_000 and catalogue.max_piece == 2_000_000


class TestACopyThatIsWrongCostsOnlyThatCopy:
    @pytest.mark.parametrize(
        ("change", "why"),
        [
            ({"kind": "secrets"}, "kind"),
            ({"id": ""}, "identifier"),
            ({"id": "a\nb"}, "identifier"),
            ({"folder": "../etc"}, "folder"),
            ({"folder": ".hidden"}, "folder"),
            ({"folder": "a/b"}, "folder"),
            ({"owner": "x\x00y"}, "copy of"),
            ({"created_at": "yesterday"}, "date"),
            ({"files": []}, "files"),
            ({"files": "none"}, "files"),
        ],
    )
    def test_the_copy_is_not_taken_and_the_reason_says_what(
        self, change: dict[str, Any], why: str
    ) -> None:
        bad = item(**{"id": "bad_1", **change})
        catalogue = parse_catalogue(listed(item(), item(id="good_20261111_020000"), bad))

        assert [i.id for i in catalogue.items] == [
            "shop-example-com_20261110_020000",
            "good_20261111_020000",
        ]
        (refusal,) = catalogue.rejected
        assert why in refusal.reason

    @pytest.mark.parametrize(
        "name",
        [
            "../../etc/passwd",
            "/etc/passwd",
            ".hidden",
            "a/b",
            "a\\b",
            "a\x00b",
            "",
            ".",
            "..",
            "x" * 300,
        ],
    )
    def test_a_file_whose_name_is_not_plain_is_not_taken(self, name: str) -> None:
        files = [
            {"name": name, "size": 1, "sha256": None, "role": "archive"},
        ]

        catalogue = parse_catalogue(listed(item(files=files)))

        assert catalogue.items == () and "plain file name" in catalogue.rejected[0].reason

    @pytest.mark.parametrize("size", [-1, 1.5, "10", None, True, MAX_PLAUSIBLE_BYTES + 1, 10**30])
    def test_a_size_no_file_can_have_is_not_taken(self, size: Any) -> None:
        files = [{"name": "a.tar.gz", "size": size, "sha256": None, "role": "archive"}]

        catalogue = parse_catalogue(listed(item(files=files)))

        assert catalogue.items == () and "size" in catalogue.rejected[0].reason

    @pytest.mark.parametrize("digest", ["nope", "A" * 64, "a" * 63, "a" * 65, 5, "g" * 64])
    def test_a_digest_that_is_not_a_sha256_is_not_taken(self, digest: Any) -> None:
        files = [{"name": "a.tar.gz", "size": 1, "sha256": digest, "role": "archive"}]

        assert parse_catalogue(listed(item(files=files))).items == ()

    def test_the_first_file_is_the_copy(self) -> None:
        files = [{"name": "a.json", "size": 1, "sha256": None, "role": "sidecar"}]

        assert parse_catalogue(listed(item(files=files))).items == ()

    def test_a_copy_listed_twice_is_taken_once(self) -> None:
        catalogue = parse_catalogue(listed(item(), item()))

        assert len(catalogue.items) == 1 and "twice" in catalogue.rejected[0].reason

    def test_a_copy_may_not_have_two_files_of_one_name(self) -> None:
        files = [
            {"name": "a", "size": 1, "sha256": None, "role": "archive"},
            {"name": "a", "size": 1, "sha256": None, "role": "sidecar"},
        ]

        assert parse_catalogue(listed(item(files=files))).items == ()

    def test_an_origin_nobody_knows_is_a_copy_by_hand(self) -> None:
        (accepted,) = parse_catalogue(listed(item(origin="everything"))).items

        assert accepted.origin == "manual"

    def test_a_server_that_lists_too_many_is_cut_and_said(self) -> None:
        many = [item(id=f"x{i}") for i in range(MAX_ITEMS + 1)]

        catalogue = parse_catalogue(listed(*many))

        assert len(catalogue.items) == MAX_ITEMS
        assert any("more than" in r.reason for r in catalogue.rejected)


class TestAnswerThatIsNotAList:
    @pytest.mark.parametrize(
        "body", [None, [], "items", 5, {"items": 5}, {"items": [1, "a", None]}]
    )
    def test_it_is_a_list_of_nothing_never_a_crash(self, body: Any) -> None:
        catalogue = parse_catalogue(body)

        assert catalogue.items == ()


class TestWhatBecomesAPath:
    @pytest.mark.parametrize("name", ["shop-example-com", "a.tar.gz", "x_1", "db$1", "a@b+c=d,e~f"])
    def test_plain_names_pass(self, name: str) -> None:
        assert safe_segment(name) == name

    @pytest.mark.parametrize(
        "name", ["", ".", "..", ".x", "-x", "a/b", "a\\b", "a b", "a\n", "a\x00", "é", 5, None]
    )
    def test_nothing_else_does(self, name: Any) -> None:
        assert safe_segment(name) is None
