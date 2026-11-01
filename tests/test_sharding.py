# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The suite can be cut into slices that CI runs on machines of their own.

One job for forty-one thousand tests took half an hour on a good day and over an hour on a bad one,
and the runner that ran it with coverage on Python 3.14 was lost before it finished. ``--shard K/N``
(or ``NOUST_TEST_SHARD=K/N``) keeps the test files that slice K of N owns. The cut is made on whole
files, deterministically and from nothing but what was collected, so every worker of an xdist run
and every job of the matrix agrees on it without talking to each other, and a file's module-level
caches stay in one process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.sharding import assign, parse_shard, select

ROOT = Path(__file__).resolve().parent.parent


def item(nodeid: str) -> SimpleNamespace:
    """A stand-in for a collected test: the cut only reads its node id."""
    return SimpleNamespace(nodeid=nodeid)


def suite(counts: dict[str, int]) -> list[SimpleNamespace]:
    """A collection of ``tests/<file>`` with that many tests each, in collection order."""
    return [
        item(f"tests/{name}::test_{index}")
        for name, count in counts.items()
        for index in range(count)
    ]


class TestTheSpelling:
    @pytest.mark.parametrize(
        ("text", "expected"), [("1/4", (1, 4)), ("4/4", (4, 4)), ("1/1", (1, 1)), (" 2/3 ", (2, 3))]
    )
    def test_a_slice_of_a_count(self, text: str, expected: tuple[int, int]) -> None:
        assert parse_shard(text) == expected

    @pytest.mark.parametrize(
        "text", ["", "2", "0/4", "5/4", "1/0", "-1/4", "a/b", "1/2/3", "1.5/4", "1/\uff14"]
    )
    def test_anything_else_is_refused_naming_what_is_expected(self, text: str) -> None:
        with pytest.raises(pytest.UsageError, match=r"K/N"):
            parse_shard(text)


class TestTheCut:
    COUNTS = {
        "test_big.py": 500,
        "test_a.py": 120,
        "test_b.py": 90,
        "test_c.py": 90,
        "test_d.py": 60,
        "test_e.py": 40,
        "test_f.py": 30,
        "test_g.py": 5,
        "test_h.py": 1,
    }

    def test_every_test_is_in_exactly_one_slice(self) -> None:
        items = suite(self.COUNTS)
        taken = [[test.nodeid for test in select(items, k, 4)[0]] for k in (1, 2, 3, 4)]
        flat = [nodeid for part in taken for nodeid in part]
        assert sorted(flat) == sorted(test.nodeid for test in items)
        assert len(flat) == len(set(flat))

    def test_the_rest_is_what_was_dropped(self) -> None:
        items = suite(self.COUNTS)
        kept, dropped = select(items, 2, 4)
        assert sorted(test.nodeid for test in kept + dropped) == sorted(
            test.nodeid for test in items
        )

    def test_a_file_is_never_split(self) -> None:
        items = suite(self.COUNTS)
        owners: dict[str, set[int]] = {}
        for k in (1, 2, 3):
            for test in select(items, k, 3)[0]:
                owners.setdefault(test.nodeid.split("::")[0], set()).add(k)
        assert all(len(found) == 1 for found in owners.values())

    def test_the_order_inside_a_slice_is_the_collection_order(self) -> None:
        items = suite(self.COUNTS)
        kept = select(items, 1, 3)[0]
        assert [test.nodeid for test in kept] == [test.nodeid for test in items if test in kept]

    def test_it_does_not_depend_on_the_order_things_were_collected_in(self) -> None:
        items = suite(self.COUNTS)
        forward = {test.nodeid for test in select(items, 2, 3)[0]}
        backward = {test.nodeid for test in select(list(reversed(items)), 2, 3)[0]}
        assert forward == backward

    def test_a_worker_and_the_next_one_cut_the_same_way(self) -> None:
        """Every xdist worker collects the whole suite and must agree on its slice."""
        assert [test.nodeid for test in select(suite(self.COUNTS), 3, 4)[0]] == [
            test.nodeid for test in select(suite(self.COUNTS), 3, 4)[0]
        ]

    def test_the_slices_are_even_by_what_they_hold(self) -> None:
        """Greedy by size: no slice is more than the biggest file above the lightest one."""
        items = suite(self.COUNTS)
        sizes = [len(select(items, k, 4)[0]) for k in (1, 2, 3, 4)]
        assert max(sizes) - min(sizes) <= max(self.COUNTS.values())
        assert min(sizes) > 0

    def test_a_suite_smaller_than_the_slices_leaves_some_empty_and_loses_nothing(self) -> None:
        items = suite({"test_x.py": 3, "test_y.py": 2})
        sizes = [len(select(items, k, 5)[0]) for k in range(1, 6)]
        assert sum(sizes) == 5

    def test_one_slice_of_one_is_the_whole_suite(self) -> None:
        items = suite(self.COUNTS)
        kept, dropped = select(items, 1, 1)
        assert kept == items and dropped == []

    def test_the_owner_of_each_file_is_a_pure_function_of_the_counts(self) -> None:
        owners = assign({"a": 10, "b": 10, "c": 3}, 2)
        assert owners == assign({"c": 3, "b": 10, "a": 10}, 2)
        assert set(owners) == {"a", "b", "c"}
        assert set(owners.values()) <= {1, 2}


class TestTheOption:
    """``--shard`` reaches the collection: the hook in ``conftest.py`` applies the cut."""

    @pytest.mark.allow_subprocess
    def test_slices_of_two_files_add_up_to_the_two_files(self) -> None:
        files = ["tests/test_sharding.py", "tests/test_regex_anchors.py"]

        def collected(*extra: str) -> list[str]:
            done = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--collect-only",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "no:randomly",
                    *extra,
                    *files,
                ],
                cwd=ROOT,
                # CI gives each machine its slice through NOUST_TEST_SHARD: inherited, the child would take
                # that slice of these two files before the one asked here, and find nothing to list.
                env={
                    name: value for name, value in os.environ.items() if name != "NOUST_TEST_SHARD"
                },
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            assert done.returncode in (0, 5), done.stdout + done.stderr
            return [line for line in done.stdout.splitlines() if "::" in line]

        whole = collected()
        first = collected("--shard", "1/2")
        second = collected("--shard", "2/2")
        assert whole and first and second
        assert sorted(first + second) == sorted(whole)
        assert not set(first) & set(second)

    @pytest.mark.allow_subprocess
    def test_a_bad_slice_stops_the_run_before_any_test(self) -> None:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "--shard",
                "3/2",
                "tests/test_sharding.py",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert done.returncode != 0
        assert "K/N" in done.stdout + done.stderr
