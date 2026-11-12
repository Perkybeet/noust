# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Taking one file from a server: pieces that resume, never half a file under a good name (item 195).

Each test is a way a copy can be lost or a lie can be believed:

- a transfer that stops in the middle leaves a part nothing offers, and the next one resumes from
  where it ended, and ends with the same bytes;
- a part of a file that is not the same file any more (its digest or its size changed on the
  server) is not resumed;
- a digest that does not match is a discarded part and a named error, not a stored copy;
- a server that sends more than was asked, or has less than it listed, is not believed;
- the speed limit paces the reads and the disk guard is asked before each piece.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from noust.core.exceptions import NodeRefusedError, NodeUnreachableError
from noust.fleet.collect.fetch import (
    DigestMismatch,
    Fetcher,
    SourceEndedEarly,
    TooManyFailures,
    TransferError,
    part_name,
)
from noust.fleet.collect.space import DiskFullError, Guard
from noust.fleet.collect.throttle import Throttle
from tests.support.backup_collection_support import Disk, sha
from tests.support.collect_support import FakeApi, drops_once

DATA = bytes(range(256)) * 400  # 102400 bytes


@pytest.fixture
def disk(tmp_path: Path) -> Disk:
    made = Disk(tmp_path / "server")
    made.apps.add(data=DATA)
    return made


@pytest.fixture
def api(disk: Disk) -> FakeApi:
    return FakeApi(disk)


def item_and_file(api: FakeApi, role: str = "archive"):  # type: ignore[no-untyped-def]
    (item,) = api.list().items
    return item, next(f for f in item.files if f.role == role)


def fetcher(api: FakeApi, **kw):  # type: ignore[no-untyped-def]
    kw.setdefault("piece", 10_000)
    kw.setdefault("sleep", lambda _s: None)
    return Fetcher(api, **kw)


class TestAFileThatArrives:
    def test_it_arrives_whole_with_the_digest_checked(self, api: FakeApi, tmp_path: Path) -> None:
        item, file = item_and_file(api)

        got = fetcher(api).receive(item, file, tmp_path / "out")

        assert got is not None
        assert got.part.read_bytes() == DATA and got.sha256 == sha(DATA)
        assert got.verified == "digest" and got.size == len(DATA)

    def test_it_is_asked_for_in_pieces_never_more_than_the_piece(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)

        fetcher(api, piece=30_000).receive(item, file, tmp_path / "out")

        lengths = [length for _k, _i, _f, _o, length in api.reads]
        assert lengths == [30_000, 30_000, 30_000, 12_400]

    def test_a_file_with_no_digest_is_checked_by_its_length_and_says_so(
        self, disk: Disk, tmp_path: Path
    ) -> None:
        disk.databases.add(recorded=False, data=b"dump bytes" * 30)
        api = FakeApi(disk)
        item = next(i for i in api.list().items if i.kind == "database")

        got = fetcher(api).receive(item, item.main, tmp_path / "out")

        assert got is not None and got.verified == "size"

    def test_the_part_is_not_the_copy_until_it_is_placed(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)

        got = fetcher(api).receive(item, file, tmp_path / "out")

        assert got is not None
        assert got.part.name.startswith(".") and got.part.name.endswith(".part")
        assert not (tmp_path / "out" / file.name).exists()


class TestACutTransferResumes:
    def test_the_next_one_asks_only_for_what_is_missing_and_ends_with_the_same_bytes(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)
        api.on_read = drops_once(50_000)

        with pytest.raises(TooManyFailures):
            fetcher(api, retries=0).receive(item, file, tmp_path / "out")
        part = tmp_path / "out" / part_name(file)
        assert part.exists() and 0 < part.stat().st_size < len(DATA)
        api.reads.clear()
        api.on_read = None

        got = fetcher(api).receive(item, file, tmp_path / "out")

        assert got is not None and got.part.read_bytes() == DATA
        assert got.sha256 == sha(DATA)
        # It did not start again from the beginning.
        assert min(offset for _k, _i, _f, offset, _l in api.reads) == 50_000

    def test_a_piece_that_fails_is_asked_again_within_the_same_transfer(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)
        api.on_read = drops_once(30_000)
        pauses: list[float] = []

        got = fetcher(api, sleep=pauses.append).receive(item, file, tmp_path / "out")

        assert got is not None and got.part.read_bytes() == DATA
        assert len(pauses) == 1

    def test_a_server_that_keeps_failing_is_given_up_on_with_the_part_kept(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)

        def always(*_a: object) -> bytes:
            raise NodeUnreachableError("no answer")

        api.on_read = always  # type: ignore[assignment]

        with pytest.raises(TooManyFailures) as raised:
            fetcher(api, retries=3).receive(item, file, tmp_path / "out")

        assert "4 times in a row" in raised.value.message
        assert not (tmp_path / "out" / file.name).exists()

    def test_a_part_of_a_file_that_is_not_the_same_any_more_is_not_resumed(
        self, disk: Disk, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)
        out = tmp_path / "out"
        out.mkdir()
        # What an earlier pass left of a file that has since changed on the server: other digest.
        stale = out / f".{file.name}.{file.size}.{'0' * 12}.part"
        stale.write_bytes(b"garbage")

        got = fetcher(api).receive(item, file, out)

        assert got is not None and got.part.read_bytes() == DATA
        assert not stale.exists()

    def test_a_part_longer_than_the_file_is_started_again(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)
        out = tmp_path / "out"
        out.mkdir()
        (out / part_name(file)).write_bytes(b"x" * (len(DATA) + 5))

        got = fetcher(api).receive(item, file, out)

        assert got is not None and got.part.read_bytes() == DATA

    def test_a_part_with_wrong_bytes_in_it_cannot_make_a_copy_that_passes(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        # The digest is of the whole, so damage in what was kept is found at the end.
        item, file = item_and_file(api)
        out = tmp_path / "out"
        out.mkdir()
        (out / part_name(file)).write_bytes(b"\xff" * 5000)

        with pytest.raises(DigestMismatch):
            fetcher(api).receive(item, file, out)

        assert not (out / part_name(file)).exists()


class TestAServerThatLies:
    def test_a_digest_that_does_not_match_is_a_discarded_part_and_a_named_error(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)

        def other_bytes(it: object, f: object, offset: int, length: int, data: bytes) -> bytes:
            return b"\x00" * len(data)

        api.on_read = other_bytes  # type: ignore[assignment]

        with pytest.raises(DigestMismatch) as raised:
            fetcher(api).receive(item, file, tmp_path / "out")

        assert raised.value.code == "mismatch"
        assert file.sha256 in (raised.value.details or "")
        assert list((tmp_path / "out").glob("*")) == []

    def test_a_server_that_sends_more_than_was_asked_is_not_believed(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)
        api.on_read = lambda it, f, offset, length, data: data + b"extra"  # type: ignore[assignment,misc]

        with pytest.raises(TransferError) as raised:
            fetcher(api).receive(item, file, tmp_path / "out")

        assert "more" in raised.value.message

    def test_a_file_shorter_than_the_server_listed_is_an_error_not_a_short_copy(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)

        def stops_at_half(it: object, f: object, offset: int, length: int, data: bytes) -> bytes:
            return b"" if offset >= 50_000 else data[: max(0, 50_000 - offset)]

        api.on_read = stops_at_half  # type: ignore[assignment]

        with pytest.raises(SourceEndedEarly):
            fetcher(api, retries=1).receive(item, file, tmp_path / "out")

        assert not (tmp_path / "out" / file.name).exists()

    def test_a_server_that_refuses_the_token_is_not_asked_again(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)

        def refuses(*_a: object) -> bytes:
            raise NodeRefusedError("refused")

        api.on_read = refuses  # type: ignore[assignment]

        with pytest.raises(NodeRefusedError):
            fetcher(api).receive(item, file, tmp_path / "out")

        assert len(api.reads) == 1


class TestTheSidecar:
    def test_it_is_taken_as_it_is_read(self, api: FakeApi, tmp_path: Path) -> None:
        item, file = item_and_file(api, "sidecar")

        got = fetcher(api).receive(item, file, tmp_path / "out")

        assert got is not None and got.verified == "size"
        assert (
            got.part.read_bytes()
            == (api.disk.apps.root / "shop-example-com").joinpath(file.name).read_bytes()
        )

    def test_one_that_is_not_a_json_object_is_left_out_and_does_not_fail_the_copy(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api, "sidecar")
        api.on_read = lambda it, f, offset, length, data: b"[1, 2" if offset == 0 else b""  # type: ignore[assignment,misc]

        got = fetcher(api).receive(item, file, tmp_path / "out")

        assert got is None
        assert list((tmp_path / "out").glob("*")) == []


class TestPacingAndRoom:
    def test_the_reads_are_paced_to_the_limit(self, api: FakeApi, tmp_path: Path) -> None:
        item, file = item_and_file(api)
        now = [0.0]
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            now[0] += seconds

        throttle = Throttle(50_000, clock=lambda: now[0], sleep=sleep)

        fetcher(api, throttle=throttle).receive(item, file, tmp_path / "out")

        # 102400 bytes at 50000 a second cannot take less than two seconds.
        assert sum(waits) == pytest.approx(len(DATA) / 50_000, rel=0.01)

    def test_the_disk_is_asked_before_every_piece_and_a_full_one_stops_it_keeping_the_part(
        self, api: FakeApi, tmp_path: Path
    ) -> None:
        item, file = item_and_file(api)
        asked: list[int] = []

        class Full(Guard):
            def require(self, needed: int) -> None:
                asked.append(needed)
                if len(asked) == 3:
                    raise DiskFullError("no room", details="free some")

        guard = Full(tmp_path, 0)

        with pytest.raises(DiskFullError):
            fetcher(api, guard=guard).receive(item, file, tmp_path / "out")

        assert asked == [10_000, 10_000, 10_000]
        assert (tmp_path / "out" / part_name(file)).stat().st_size == 20_000

    def test_a_stopping_console_stops_between_pieces(self, api: FakeApi, tmp_path: Path) -> None:
        item, file = item_and_file(api)
        calls = [0]

        def should_stop() -> bool:
            calls[0] += 1
            return calls[0] > 2

        with pytest.raises(TransferError):
            fetcher(api, should_stop=should_stop).receive(item, file, tmp_path / "out")

        assert len(api.reads) == 2

    def test_progress_is_told_after_every_piece(self, api: FakeApi, tmp_path: Path) -> None:
        item, file = item_and_file(api)
        seen: list[tuple[int, int]] = []

        fetcher(api, piece=50_000, progress=lambda done, total: seen.append((done, total))).receive(
            item, file, tmp_path / "out"
        )

        assert seen == [(50_000, len(DATA)), (100_000, len(DATA)), (len(DATA), len(DATA))]


class TestTheThrottleItself:
    def test_no_limit_never_waits(self) -> None:
        waits: list[float] = []

        Throttle(None, sleep=waits.append).take(10**9)

        assert waits == []

    def test_a_limit_spaces_out_the_reads(self) -> None:
        now = [100.0]
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            now[0] += seconds

        throttle = Throttle(1000, clock=lambda: now[0], sleep=sleep)
        for _ in range(3):
            throttle.take(500)

        assert waits == [0.5, 0.5, 0.5]

    def test_time_that_passed_by_itself_is_not_owed(self) -> None:
        now = [0.0]
        waits: list[float] = []
        throttle = Throttle(1000, clock=lambda: now[0], sleep=waits.append)

        throttle.take(500)
        now[0] = 60.0
        throttle.take(500)

        assert waits == [0.5, 0.5]


def test_the_digest_helper_agrees_with_hashlib() -> None:
    assert sha(b"x") == hashlib.sha256(b"x").hexdigest()
