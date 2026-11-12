# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A pass of the central over a server: what is taken, what is never done, and what can bite (item 195).

The pass is held to what each part of the brief says could go wrong:

- a 4.2 server has none of the new routes: its dumps are taken and its row says what waits for an
  update, and nothing breaks;
- a transfer cut in the middle is resumed and never leaves half a file under the name of a good copy;
- a server that lies (a digest, a size, a name that climbs) costs only what it lied about, and the
  central never deletes a copy of its own because a server said so;
- a full disk stops the pass, says so, and deletes nothing to make room;
- the central's own retention lets go of scheduled copies only, and a server that still has one it
  let go of is not asked for it again.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.exceptions import NodeRefusedError, NodeUnreachableError
from noust.core.store import NodeRecord, NoustStore
from noust.fleet.collect.collector import NOTIFY_AFTER, NodeCollector
from noust.fleet.collect.layout import Layout
from noust.fleet.collect.names import MAX_PLAUSIBLE_BYTES
from noust.fleet.collect.records import HELD, RETIRED, CollectedStore, NodeStates
from noust.fleet.collect.settings import Follows, SettingsStore
from noust.fleet.collect.space import Space
from noust.fleet.collect.throttle import Throttle
from tests.support.backup_collection_support import Disk, sha
from tests.support.collect_support import FakeApi, drops_once

NODE = "web-1"


class Said:
    """What the operator was told."""

    def __init__(self) -> None:
        self.told: list[tuple[str, str, str]] = []

    def __call__(self, node: str, code: str, text: str) -> None:
        self.told.append((node, code, text))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.save_node(
        NodeRecord(
            name=NODE,
            ssh_host="web1.example.com",
            ssh_port=22,
            ssh_user="noust-tunnel",
            host_key="noust-node-web-1 ssh-ed25519 AAAA",
            console_port=8080,
        )
    )
    SettingsStore(instance).set(enabled=True)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def disk(tmp_path: Path) -> Disk:
    return Disk(tmp_path / "server")


@pytest.fixture
def api(disk: Disk) -> FakeApi:
    return FakeApi(disk)


@pytest.fixture
def said() -> Said:
    return Said()


@pytest.fixture
def central(tmp_path: Path) -> Path:
    return tmp_path / "central"


def collector(store: NoustStore, api: Any, central: Path, said: Said, **kw: Any) -> NodeCollector:
    kw.setdefault("sleep", lambda _s: None)
    return NodeCollector(
        store=store,
        api_for=lambda _node: api,
        layout=Layout(central),
        notify=said,
        **kw,
    )


def on_disk(central: Path) -> set[str]:
    return {str(p.relative_to(central)) for p in central.rglob("*") if p.is_file()}


class TestTheCopiesArrive:
    def test_every_kind_is_taken_whole_into_the_servers_own_folder(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.everything()

        result = collector(store, api, central, said).collect(NODE)

        assert result.state == "ok" and result.fetched == 4
        held = CollectedStore(store).list(node=NODE)
        assert {c.kind for c in held} == {"app", "database", "volume", "noust"}
        for copy in held:
            folder = central / NODE / copy.folder / copy.kind
            for file in copy.files:
                found = next(folder.glob(f"*/{file.name}"))
                assert sha(found.read_bytes()) == file.sha256
        assert all(c.verified in ("digest", "size") for c in held)
        assert not [p for p in on_disk(central) if p.rsplit("/", 1)[-1].startswith(".")]

    def test_what_is_taken_is_private_to_the_central(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()

        collector(store, api, central, said).collect(NODE)

        for path in central.rglob("*"):
            if path.is_file():
                assert path.stat().st_mode & 0o077 == 0, path

    def test_the_server_is_told_which_copies_the_central_holds(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        meta = disk.apps.add(data=b"archive")
        disk.databases.add()

        collector(store, api, central, said).collect(NODE)

        assert api.held is not None
        assert ("app", meta.id, sha(b"archive")) in api.held
        assert len(api.held) == 2

    def test_a_second_pass_takes_nothing_again(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.everything()
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        api.reads.clear()

        second = runner.collect(NODE, force=True)

        assert second.fetched == 0 and second.held == 4 and api.reads == []

    def test_a_server_whose_list_did_not_change_costs_one_look(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.everything()
        runner = collector(store, api, central, said)
        runner.collect(NODE)

        second = runner.collect(NODE)

        assert second.state == "unchanged"

    def test_a_new_copy_after_that_is_taken_and_only_that_one(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add(stamp="20261101_020000")
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        api.reads.clear()
        disk.apps.add(stamp="20261102_020000")

        result = runner.collect(NODE)

        assert result.fetched == 1
        assert {read[1] for read in api.reads} == {"shop-example-com_20261102_020000"}

    def test_the_newest_copy_is_taken_first(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        for day in (1, 2, 3):
            disk.apps.add(stamp=f"2026110{day}_020000")

        collector(store, api, central, said).collect(NODE)

        taken = [read[1] for read in api.reads if read[3] == 0 and read[2].endswith(".tar.gz")]
        assert taken == [
            "shop-example-com_20261103_020000",
            "shop-example-com_20261102_020000",
            "shop-example-com_20261101_020000",
        ]

    def test_it_is_audited_per_copy(
        self,
        store: NoustStore,
        disk: Disk,
        api: FakeApi,
        central: Path,
        said: Said,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(audit, "record", lambda event, **kw: recorded.append((event, kw)))
        disk.apps.add()

        collector(store, api, central, said).collect(NODE)

        ((event, kw),) = recorded
        assert event == "central_backup.collect" and kw["details"]["bytes"] > 0


class TestWhoIsFollowed:
    def test_a_server_not_followed_is_not_touched(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        SettingsStore(store).set(enabled=False)

        result = collector(store, api, central, said).collect(NODE)

        assert result.state == "skipped" and api.lists == 0

    def test_a_server_can_follow_on_its_own_while_the_fleet_does_not(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        SettingsStore(store).set(enabled=False)
        Follows(store).set_follow(NODE, True)

        assert collector(store, api, central, said).collect(NODE).fetched == 1

    def test_and_not_follow_while_the_fleet_does(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        Follows(store).set_follow(NODE, False)

        assert collector(store, api, central, said).collect(NODE).state == "skipped"

    def test_only_the_kinds_that_are_turned_on_are_taken(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.everything()
        SettingsStore(store).set(kinds=["app", "noust"])

        result = collector(store, api, central, said).collect(NODE)

        assert {c.kind for c in CollectedStore(store).list()} == {"app", "noust"}
        assert result.fetched == 2


class TestAServerBefore43:
    class Old(FakeApi):
        """A 4.2 server: no routes of the new kind; its dumps by the old ones."""

        def __init__(self, disk: Disk) -> None:
            super().__init__(disk)
            self.asked_legacy = False

    def test_its_database_dumps_are_taken_and_the_row_says_what_waits_for_an_update(
        self, store: NoustStore, disk: Disk, central: Path, said: Said
    ) -> None:
        disk.everything()
        new = FakeApi(disk)
        new.unsupported = True
        old = FakeApi(disk)
        old.legacy = True
        old.mutate = lambda body: body.update(
            items=[i for i in body["items"] if i["kind"] == "database"]
        )

        runner = NodeCollector(
            store=store,
            api_for=lambda _n: new,
            legacy_for=lambda _api: old,
            layout=Layout(central),
            notify=said,
            sleep=lambda _s: None,
        )

        result = runner.collect(NODE)

        assert result.legacy and result.fetched == 1
        assert {c.kind for c in CollectedStore(store).list()} == {"database"}
        assert any("4.3" in note for note in result.notes)
        assert NodeStates(store).get(NODE).support == {"mode": "databases_only"}

    def test_a_server_before_43_costs_no_failure_and_no_notice(
        self, store: NoustStore, disk: Disk, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        new = FakeApi(disk)
        new.unsupported = True
        old = FakeApi(disk)
        old.mutate = lambda body: body.update(items=[])
        runner = NodeCollector(
            store=store,
            api_for=lambda _n: new,
            legacy_for=lambda _a: old,
            layout=Layout(central),
            notify=said,
            sleep=lambda _s: None,
        )

        result = runner.collect(NODE)

        assert result.state == "ok" and said.told == []


class TestACutTransfer:
    def test_nothing_is_left_under_the_name_of_a_good_copy_and_the_next_pass_resumes(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        data = bytes(range(256)) * 400
        meta = disk.apps.add(data=data)
        api.on_read = drops_once(30_000)
        runner = collector(store, api, central, said, sleep=lambda _s: None)

        # The tunnel drops and stays down for the length of the pass.
        def down(*_a: object) -> bytes:
            raise NodeUnreachableError("The tunnel dropped")

        api.on_read = down  # type: ignore[assignment]
        first = runner.collect(NODE)

        assert first.state == "failed" and CollectedStore(store).list(node=NODE) == []
        names = on_disk(central)
        assert not any(name.endswith(".tar.gz") for name in names)
        assert all(Path(name).name.startswith(".") for name in names)
        api.on_read = None
        api.reads.clear()

        second = runner.collect(NODE)

        assert second.state == "ok" and second.fetched == 1
        (copy,) = CollectedStore(store).list(node=NODE)
        final = next((central / NODE).rglob(f"{meta.id}.tar.gz"))
        assert final.read_bytes() == data

    def test_a_pass_that_ends_before_the_last_file_records_no_copy(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()

        def sidecar_never_comes(
            item: Any, file: Any, offset: int, length: int, data: bytes
        ) -> bytes:
            if file.role == "sidecar":
                raise NodeUnreachableError("no answer")
            return data

        api.on_read = sidecar_never_comes
        collector(store, api, central, said).collect(NODE)

        assert CollectedStore(store).list(node=NODE) == []


class TestAServerThatLies:
    def test_a_digest_that_does_not_match_costs_only_that_copy_and_is_said_once(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        bad = disk.apps.add(stamp="20261101_020000", data=b"A" * 5000)
        disk.apps.add(stamp="20261102_020000", data=b"B" * 5000)

        def tampered(item: Any, file: Any, offset: int, length: int, data: bytes) -> bytes:
            return b"Z" * len(data) if item.id == bad.id and file.role == "archive" else data

        api.on_read = tampered
        runner = collector(store, api, central, said)

        result = runner.collect(NODE)

        assert result.fetched == 1 and result.code == "mismatch"
        assert {c.item_id for c in CollectedStore(store).list()} == {
            "shop-example-com_20261102_020000"
        }
        assert [(n, c) for n, c, _t in said.told] == [(NODE, "mismatch")]
        assert not any(bad.id in name and name.endswith(".tar.gz") for name in on_disk(central))
        api.reads.clear()
        runner.collect(NODE, force=True)
        # Not asked for again while it is the same copy; nor said again.
        assert not any(read[1] == bad.id for read in api.reads) and len(said.told) == 1

    def test_a_list_that_names_a_path_that_climbs_takes_nothing_of_it_and_writes_nothing_outside(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said, tmp_path: Path
    ) -> None:
        disk.apps.add()

        def hostile(body: dict[str, Any]) -> None:
            evil = dict(body["items"][0])
            evil.update(id="evil_1", folder="../../escape")
            evil["files"] = [dict(evil["files"][0], name="../../pwned")]
            body["items"].append(evil)

        api.mutate = hostile

        result = collector(store, api, central, said).collect(NODE)

        assert result.fetched == 1
        assert any("folder" in r["reason"] for r in result.rejected)
        assert not (tmp_path / "escape").exists() and not (tmp_path / "pwned").exists()
        assert not list(tmp_path.rglob("pwned"))

    def test_a_size_no_disk_has_is_not_taken_and_does_not_stop_the_others(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()

        def absurd(body: dict[str, Any]) -> None:
            huge = dict(body["items"][0])
            huge.update(id="huge_1")
            huge["files"] = [dict(huge["files"][0], size=MAX_PLAUSIBLE_BYTES + 1)]
            body["items"].insert(0, huge)

        api.mutate = absurd

        result = collector(store, api, central, said).collect(NODE)

        assert result.fetched == 1 and any("size" in r["reason"] for r in result.rejected)

    def test_a_server_that_forgets_a_copy_does_not_make_the_central_forget_it(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        meta = disk.apps.add()
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        disk.apps.items.clear()

        runner.collect(NODE, force=True)

        (copy,) = CollectedStore(store).list(node=NODE)
        assert copy.item_id == meta.id and copy.state == HELD
        assert any(central.rglob(f"{meta.id}.tar.gz"))

    def test_a_server_that_lists_nothing_deletes_nothing_either(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.everything()
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        before = on_disk(central)
        api.mutate = lambda body: body.update(items=[])

        runner.collect(NODE, force=True)

        assert on_disk(central) == before and len(CollectedStore(store).list()) == 4


class TestAFullDisk:
    def measure(self, free: int) -> Callable[..., Space]:
        return lambda path, *, used=0, min_free=0: Space(1000 * 1024**2, free, used, min_free)

    def test_the_pass_stops_says_so_and_deletes_nothing(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        first = disk.apps.add(stamp="20261101_020000", data=b"A" * 4000)
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        before = on_disk(central)
        disk.apps.add(stamp="20261102_020000", data=b"B" * 4000)
        full = collector(store, api, central, said, measure=self.measure(100))

        result = full.collect(NODE, force=True)

        assert result.stopped == "disk_full" and result.code == "disk_full"
        assert "Nothing was deleted to make room" in result.failures[0]
        assert on_disk(central) == before
        assert {c.item_id for c in CollectedStore(store).list()} == {first.id}
        assert [(n, c) for n, c, _t in said.told] == [(NODE, "disk_full")]
        assert NodeStates(store).get(NODE).last_error_code == "disk_full"

    def test_it_is_said_once_and_again_only_when_it_happens_again_after_working(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add(data=b"A" * 4000)
        full = collector(store, api, central, said, measure=self.measure(100))

        full.collect(NODE)
        full.collect(NODE)

        assert len(said.told) == 1

    def test_what_is_kept_free_counts_and_room_that_comes_back_is_used(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add(data=b"A" * 4000)
        SettingsStore(store).set(min_free_gb=1)
        tight = collector(store, api, central, said, measure=self.measure(1024**3 + 2000))
        assert tight.collect(NODE).stopped == "disk_full"

        roomy = collector(store, api, central, said, measure=self.measure(10 * 1024**3))

        assert roomy.collect(NODE).state == "ok"


class TestTheCentralsOwnRetention:
    def fill(self, disk: Disk, count: int) -> list[str]:
        return [
            disk.apps.add(stamp=f"202611{day:02d}_020000", data=f"day {day}".encode()).id
            for day in range(1, count + 1)
        ]

    def test_scheduled_copies_beyond_the_count_are_let_go_and_the_newest_stays(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        ids = self.fill(disk, 5)
        SettingsStore(store).set(retention_count=2)

        result = collector(store, api, central, said).collect(NODE)

        assert result.let_go == 3
        assert {c.item_id for c in CollectedStore(store).list()} == set(ids[3:])
        assert not any(central.rglob(f"{ids[0]}*"))

    def test_a_copy_by_hand_and_a_safety_copy_are_never_let_go(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        manual = disk.apps.add(stamp="20260101_020000", scheduled=False)
        self.fill(disk, 4)
        SettingsStore(store).set(retention_count=1)

        collector(store, api, central, said).collect(NODE)

        assert manual.id in {c.item_id for c in CollectedStore(store).list()}

    def test_a_copy_let_go_is_not_asked_for_again_while_the_server_still_lists_it(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        ids = self.fill(disk, 3)
        SettingsStore(store).set(retention_count=1)
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        api.reads.clear()

        runner.collect(NODE, force=True)

        assert api.reads == []
        retired = CollectedStore(store).get(NODE, "app", ids[0])
        assert retired is not None and retired.state == RETIRED

    def test_the_record_of_it_goes_when_the_server_stops_listing_it(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        ids = self.fill(disk, 3)
        SettingsStore(store).set(retention_count=1)
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        disk.apps.items[:] = [m for m in disk.apps.items if m.id != ids[0]]

        runner.collect(NODE, force=True)

        assert CollectedStore(store).get(NODE, "app", ids[0]) is None

    def test_with_no_limit_nothing_is_let_go(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        self.fill(disk, 5)
        SettingsStore(store).set(retention_count=None)

        result = collector(store, api, central, said).collect(NODE)

        assert result.let_go == 0 and len(CollectedStore(store).list()) == 5

    def test_the_operator_letting_one_go_is_the_same_door(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        ids = self.fill(disk, 1)
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        (copy,) = CollectedStore(store).list()

        runner.let_go(copy, reason="operator")

        assert not any(central.rglob(f"{ids[0]}*"))
        assert CollectedStore(store).get(NODE, "app", ids[0]).state == RETIRED  # type: ignore[union-attr]


class TestTheSpeedLimit:
    def test_the_reads_are_paced_by_the_setting(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add(data=b"x" * 64_000)
        SettingsStore(store).set(rate_limit_kbps=64)
        waits: list[float] = []
        made: list[float | None] = []

        def factory(rate: float | None) -> Throttle:
            made.append(rate)
            return Throttle(rate, clock=lambda: 0.0, sleep=waits.append)

        collector(store, api, central, said, throttle=factory).collect(NODE)

        assert made == [64 * 1024.0] and sum(waits) > 0.5


class TestWhenItGoesWrongWithTheServer:
    def test_a_server_that_cannot_be_reached_is_said_the_third_time_in_a_row_and_once(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        def down(known: Any = None) -> Any:
            raise NodeUnreachableError("ssh: connect to host web1 port 22: timed out")

        api.list = down  # type: ignore[method-assign,assignment]
        runner = collector(store, api, central, said)

        for _ in range(NOTIFY_AFTER + 2):
            result = runner.collect(NODE)

        assert result.state == "failed" and result.code == "unreachable"
        assert len(said.told) == 1 and said.told[0][1] == "unreachable"
        assert "timed out" in said.told[0][2]
        assert NodeStates(store).get(NODE).failures == NOTIFY_AFTER + 2

    def test_a_server_that_answers_again_clears_it(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        good = api.list

        def down(known: Any = None) -> Any:
            raise NodeUnreachableError("no route")

        runner = collector(store, api, central, said)
        api.list = down  # type: ignore[method-assign,assignment]
        runner.collect(NODE)
        api.list = good  # type: ignore[method-assign]

        runner.collect(NODE)

        state = NodeStates(store).get(NODE)
        assert state.failures == 0 and state.last_error is None and state.last_ok_at

    def test_a_server_whose_ceiling_does_not_let_its_central_collect_says_which_setting(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        refusal = NodeRefusedError(
            "web-1 refused the fleet token (HTTP 403)", details="backups.collect"
        )
        refusal.status_code = 403

        def refuses(known: Any = None) -> Any:
            raise refusal

        api.list = refuses  # type: ignore[method-assign,assignment]

        result = collector(store, api, central, said).collect(NODE)

        assert result.code == "ceiling"
        assert [c for _n, c, _t in said.told] == ["ceiling"]

    def test_a_revoked_token_is_a_refusal_and_not_a_ceiling(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        refusal = NodeRefusedError("refused")
        refusal.status_code = 401

        def refuses(known: Any = None) -> Any:
            raise refusal

        api.list = refuses  # type: ignore[method-assign,assignment]

        assert collector(store, api, central, said).collect(NODE).code == "refused"

    def test_a_server_that_could_not_be_told_what_is_held_still_has_its_copies_taken(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        api.refuse_held = True

        result = collector(store, api, central, said).collect(NODE)

        assert result.state == "ok" and result.fetched == 1
        assert any("could not be told" in note for note in result.notes)


class TestOnePassAtATime:
    def test_a_second_pass_over_a_server_that_is_being_taken_says_so_and_waits_for_nothing(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        entered, release = threading.Event(), threading.Event()
        original = api.list

        def slow(known: Any = None) -> Any:
            entered.set()
            release.wait(5)
            return original(known=known)

        api.list = slow  # type: ignore[method-assign,assignment]
        runner = collector(store, api, central, said)
        outcome: list[Any] = []
        thread = threading.Thread(target=lambda: outcome.append(runner.collect(NODE)))
        thread.start()
        assert entered.wait(5)

        second = runner.collect(NODE)
        release.set()
        thread.join(10)

        assert second.state == "skipped" and "already running" in second.notes[0]
        assert outcome[0].fetched == 1


class TestWhatTheServerWillNotLetGo:
    def test_a_copy_the_server_blocks_is_left_and_said_and_not_a_failure(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        disk.apps.add()
        api.sealing_required = True

        result = collector(store, api, central, said).collect(NODE)

        assert result.fetched == 0 and result.state == "ok"
        assert [(entry["reason"]) for entry in result.left] == ["blocked"]
        assert said.told == []


class TestACopyThatChangedOnTheServer:
    def test_the_central_keeps_what_it_took_and_says_the_server_changed_it(
        self, store: NoustStore, disk: Disk, api: FakeApi, central: Path, said: Said
    ) -> None:
        meta = disk.apps.add(data=b"plain archive")
        runner = collector(store, api, central, said)
        runner.collect(NODE)
        # Sealed in place on the server since: another file under the same name.
        archive = disk.apps.local_files(meta)[0]
        archive.write_bytes(b"SEALED archive")
        meta.checksum = sha(b"SEALED archive")

        result = runner.collect(NODE, force=True)

        assert result.fetched == 0 and any("another digest" in note for note in result.notes)
        (copy,) = CollectedStore(store).list()
        assert copy.files[0].sha256 == sha(b"plain archive")
