# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audit log's chain, rotation and retention.

Every event carries the MAC of the one before it, so changing, removing or
reordering a line breaks the chain exactly there, and ``verify`` must name
that line. Rotation must not break the chain, retention must not break it
either (the purge event anchors the new start), and a log from Noust 3.0 must
keep reading.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.audit import Actor, bind
from noust.core.audit import log as log_module
from noust.core.audit.chain import GENESIS, compute_mac
from noust.core.audit.legacy import AuditLogger
from noust.core.audit.log import AuditLog
from noust.core.audit.settings import AuditSettings
from noust.core.fs import DryRunFileSystem, set_fs


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    """A log in the test's directory with default settings and nothing shipped."""
    return AuditLog(tmp_path / "state" / "web-audit.log", settings=AuditSettings(journald="off"))


def lines(path: Path) -> list[dict]:
    return [json.loads(text) for text in path.read_text().splitlines() if text.strip()]


def rewrite(path: Path, entries: list[dict]) -> None:
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def fill(log: AuditLog, count: int) -> None:
    for index in range(count):
        log.append("apps.update", actor=Actor(kind="user", name="maria"), target=f"app:{index}")


class TestWriting:
    def test_the_first_event_starts_a_chain_from_genesis(self, log: AuditLog) -> None:
        log.append("apps.delete", actor=Actor(kind="user", name="maria", role="admin"))

        first, second = lines(log.path)
        assert first["action"] == "audit.chain_start"
        assert (first["seq"], first["prev"]) == (1, GENESIS)
        assert second["seq"] == 2
        assert second["prev"] == first["mac"]
        assert second["who"] == {"kind": "user", "name": "maria", "role": "admin"}

    def test_each_line_carries_the_fields_readers_have_always_used(self, log: AuditLog) -> None:
        log.append(
            "apps.env.reveal",
            actor=Actor(kind="token", name="ci", source="10.0.0.9"),
            target="app:shop.example.com",
            outcome="ok",
            details={"keys": ["DATABASE_URL"]},
        )
        entry = lines(log.path)[-1]
        assert entry["v"] == 2
        assert entry["action"] == "apps.env.reveal"
        assert entry["result"] == "ok"
        assert entry["actor"] == "token:ci"
        assert entry["ip"] == "10.0.0.9"
        assert entry["resource"] == "app:shop.example.com"
        assert entry["cat"] == "read"
        assert entry["sensitive"] is True
        assert entry["details"] == {"keys": ["DATABASE_URL"]}
        assert datetime.fromisoformat(entry["ts"]).tzinfo is not None

    def test_log_and_key_are_private(self, log: AuditLog) -> None:
        fill(log, 1)
        assert log.path.stat().st_mode & 0o777 == 0o600
        assert log.key_path.stat().st_mode & 0o777 == 0o600
        assert log.path.parent.stat().st_mode & 0o777 == 0o700

    def test_the_mac_covers_every_field(self, log: AuditLog) -> None:
        fill(log, 1)
        key = bytes.fromhex(log.key_path.read_text().strip())
        entry = lines(log.path)[-1]
        assert compute_mac(key, entry) == entry["mac"]
        entry["result"] = "failure"
        assert compute_mac(key, entry) != entry["mac"]

    def test_two_writers_on_one_file_extend_one_chain(self, log: AuditLog) -> None:
        """The console and a CLI command are two processes writing the same log."""
        other = AuditLog(log.path, settings=log.settings)
        for _ in range(3):
            log.append("apps.update", actor=Actor.system())
            other.append("cli.command", actor=Actor(kind="cli", name="root"))

        assert [entry["seq"] for entry in lines(log.path)] == list(range(1, 8))
        assert log.verify().ok

    def test_the_correlation_id_bound_by_the_caller_is_recorded(self, log: AuditLog) -> None:
        with bind(actor=Actor(kind="cli", name="ana"), correlation_id="req-123"):
            log.append("apps.restart")
        entry = lines(log.path)[-1]
        assert entry["corr"] == "req-123"
        assert entry["actor"] == "cli:ana"

    def test_a_rehearsal_writes_nothing(
        self, log: AuditLog, caplog: pytest.LogCaptureFixture
    ) -> None:
        set_fs(DryRunFileSystem())
        with caplog.at_level("INFO", logger="noust.core.audit.log"):
            assert log.append("apps.delete", actor=Actor.system()) is None
        assert not log.path.exists()
        assert "not written during a rehearsal" in caplog.text

    def test_a_line_cut_short_by_a_crash_is_not_glued_to_the_next(self, log: AuditLog) -> None:
        fill(log, 1)
        with open(log.path, "a") as handle:
            handle.write('{"v":2,"seq":')
        fill(log, 1)
        assert json.loads(log.path.read_text().splitlines()[-1])["action"] == "apps.update"
        result = log.verify()
        assert not result.ok
        assert result.broken is not None and "not valid JSON" in result.broken.reason


class TestVerify:
    def test_an_untouched_log_verifies(self, log: AuditLog) -> None:
        fill(log, 5)
        result = log.verify()
        assert result.ok, result.broken
        assert (result.checked, result.first_seq, result.last_seq) == (6, 1, 6)
        assert result.last_mac == lines(log.path)[-1]["mac"]

    def test_a_changed_field_is_found_on_its_line(self, log: AuditLog) -> None:
        fill(log, 5)
        entries = lines(log.path)
        entries[3]["actor"] = "someone-else"
        rewrite(log.path, entries)

        result = log.verify()
        assert result.broken is not None
        assert (result.broken.line, result.broken.seq) == (4, 4)
        assert "MAC does not match" in result.broken.reason

    def test_a_removed_line_is_found(self, log: AuditLog) -> None:
        fill(log, 5)
        entries = lines(log.path)
        del entries[2]
        rewrite(log.path, entries)

        result = log.verify()
        assert result.broken is not None
        assert result.broken.seq == 4
        assert "previous event" in result.broken.reason

    def test_reordered_lines_are_found(self, log: AuditLog) -> None:
        fill(log, 5)
        entries = lines(log.path)
        entries[2], entries[3] = entries[3], entries[2]
        rewrite(log.path, entries)
        assert not log.verify().ok

    def test_a_cut_off_head_is_found(self, log: AuditLog) -> None:
        fill(log, 5)
        rewrite(log.path, lines(log.path)[2:])
        result = log.verify()
        assert result.broken is not None
        assert "no retention purge accounts for it" in result.broken.reason

    def test_an_unchained_line_after_the_chain_is_found(self, log: AuditLog) -> None:
        fill(log, 2)
        with open(log.path, "a") as handle:
            handle.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "action": "x"}) + "\n")
        result = log.verify()
        assert result.broken is not None and "unchained line" in result.broken.reason

    def test_a_missing_key_is_reported_not_passed(self, log: AuditLog) -> None:
        fill(log, 2)
        log.key_path.unlink()
        result = AuditLog(log.path, settings=log.settings).verify()
        assert result.broken is not None and "key is missing" in result.broken.reason

    def test_a_new_key_is_recorded_and_older_events_no_longer_verify(self, log: AuditLog) -> None:
        """Losing the key is evidence too: it is said, and nothing old passes as intact."""
        fill(log, 2)
        log.key_path.unlink()
        fresh = AuditLog(log.path, settings=log.settings)
        fresh.append("apps.update", actor=Actor.system())

        assert [entry["action"] for entry in lines(log.path)][-2:] == [
            "audit.key_created",
            "apps.update",
        ]
        assert not fresh.verify().ok


class TestThreeZeroLogs:
    """A server upgraded from 3.0 has a log of unchained lines, and 3.0 readers."""

    V1 = {
        "ts": "2026-09-01T10:00:00+00:00",
        "action": "auth.login",
        "result": "success",
        "actor": "master",
        "ip": "10.0.0.1",
        "resource": "/api/auth/login",
    }

    def test_old_lines_are_kept_and_the_chain_starts_after_them(self, log: AuditLog) -> None:
        log.path.parent.mkdir(parents=True)
        rewrite(log.path, [self.V1, self.V1])
        fill(log, 1)

        # The 3.0 lines are from an earlier day, so their file was closed first.
        entries = [entry for path in log.files_oldest_first() for entry in lines(path)]
        assert entries[:2] == [self.V1, self.V1]
        assert entries[2]["action"] == "audit.chain_start"
        result = log.verify()
        assert result.ok
        assert result.legacy == 2
        assert any("predate the chain" in note for note in result.notes)

    def test_old_numbered_backups_are_read_oldest_last(self, log: AuditLog) -> None:
        log.path.parent.mkdir(parents=True)
        rewrite(log.path.with_name("web-audit.log.2"), [{**self.V1, "detail": "oldest"}])
        rewrite(log.path.with_name("web-audit.log.1"), [{**self.V1, "detail": "older"}])
        fill(log, 1)

        details = [entry.get("detail") for entry in log.iter_newest_first()]
        assert details[-2:] == ["older", "oldest"]
        assert log.verify().ok

    def test_the_old_interface_writes_and_reads_the_same_file(self, tmp_path: Path) -> None:
        path = tmp_path / "state" / "web-audit.log"
        old = AuditLogger(path)
        old.record(
            action="apps.delete",
            result="success",
            client_ip="10.0.0.1",
            actor="token:ci",
            resource="/api/apps/shop",
            detail="deleted",
        )
        old.record(action="custom.thing", result="denied", client_ip="10.0.0.2", actor="sid1")

        newest = old.read(limit=10)
        assert [entry["action"] for entry in newest[:2]] == ["custom.thing", "apps.delete"]
        assert newest[1]["detail"] == "deleted"
        assert newest[1]["who"]["kind"] == "token"
        # A 3.0 caller's own name survives, flagged, instead of being rewritten.
        assert newest[0]["cat"] == "uncatalogued"
        assert old.read(actor="sid1")[0]["result"] == "denied"
        assert path.stat().st_mode & 0o777 == 0o600
        assert old.log.verify().ok


class TestRotation:
    def test_size_rotation_keeps_one_chain_across_files(self, tmp_path: Path) -> None:
        log = AuditLog(
            tmp_path / "web-audit.log", settings=AuditSettings(journald="off"), rotate_bytes=1500
        )
        fill(log, 20)

        files = log.files_oldest_first()
        assert len(files) > 2
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in files)
        result = log.verify()
        assert result.ok, result.broken
        assert result.checked == 21

    def test_the_file_is_closed_when_the_day_changes(
        self, log: AuditLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        monkeypatch.setattr(log_module, "_utcnow", lambda: yesterday)
        fill(log, 2)
        monkeypatch.setattr(log_module, "_utcnow", lambda: datetime.now(timezone.utc))
        fill(log, 1)

        closed = log.closed_files()
        assert len(closed) == 1
        assert [entry["seq"] for entry in lines(closed[0])] == [1, 2, 3]
        assert [entry["seq"] for entry in lines(log.path)] == [4]
        assert log.verify().ok


class TestRetention:
    def age(
        self, log: AuditLog, monkeypatch: pytest.MonkeyPatch, days_ago: int, count: int
    ) -> None:
        moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
        monkeypatch.setattr(log_module, "_utcnow", lambda: moment)
        fill(log, count)
        monkeypatch.setattr(log_module, "_utcnow", lambda: datetime.now(timezone.utc))

    def test_files_past_retention_go_and_the_chain_still_verifies(
        self, log: AuditLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.age(log, monkeypatch, 400, 3)
        self.age(log, monkeypatch, 399, 3)
        self.age(log, monkeypatch, 10, 3)
        fill(log, 1)
        assert len(log.closed_files()) == 3

        report = log.purge(retention_days=365, shipped_seq=None)

        assert report is not None
        assert len(report.files) == 2
        assert report.last_seq == 7
        assert len(log.closed_files()) == 1
        purge = lines(log.path)[-1]
        assert purge["action"] == "audit.purge"
        assert purge["details"]["last_mac"] == report.last_mac
        result = log.verify()
        assert result.ok, result.broken
        assert result.first_seq == 8

    def test_the_purge_is_anchored_before_anything_is_deleted(
        self, log: AuditLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deleted first, a crash or a failed append left a gap nothing explained."""
        self.age(log, monkeypatch, 400, 3)
        fill(log, 1)
        doomed = log.closed_files()

        def cannot_append(*args: object, **kwargs: object) -> None:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(log, "append", cannot_append)

        with pytest.raises(OSError):
            log.purge(retention_days=365, shipped_seq=None)

        assert all(path.exists() for path in doomed)

    def test_nothing_within_retention_is_deleted(
        self, log: AuditLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.age(log, monkeypatch, 100, 3)
        fill(log, 1)
        assert log.purge(retention_days=365, shipped_seq=None) is None
        assert len(log.closed_files()) == 1

    def test_a_file_a_destination_has_not_received_stays(
        self, log: AuditLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.age(log, monkeypatch, 400, 3)
        fill(log, 1)
        assert log.purge(retention_days=365, shipped_seq=2) is None
        assert log.purge(retention_days=365, shipped_seq=4) is not None

    def test_a_rehearsal_deletes_nothing(
        self, log: AuditLog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.age(log, monkeypatch, 400, 3)
        fill(log, 1)
        set_fs(DryRunFileSystem())
        assert log.purge(retention_days=365, shipped_seq=None) is None
        set_fs(None)
        assert len(log.closed_files()) == 1


class TestReading:
    def test_filters_and_keyset_pagination(self, log: AuditLog) -> None:
        for index in range(5):
            log.append("apps.update", actor=Actor(kind="user", name=f"u{index}"))
        log.append("apps.delete", actor=Actor(kind="user", name="u9"), outcome="denied")

        assert [entry["actor"] for entry in log.read(action="apps.update", limit=2)] == ["u4", "u3"]
        page = log.read(action="apps.update", limit=2)
        older = log.read(action="apps.update", limit=2, before=page[-1]["ts"])
        assert [entry["actor"] for entry in older] == ["u2", "u1"]
        assert log.read(result="denied")[0]["actor"] == "u9"
        assert all(entry["cat"] == "system" for entry in log.read(category="system"))

    def test_find_by_seq_or_id(self, log: AuditLog) -> None:
        fill(log, 3)
        third = log.find("3")
        assert third is not None and third["seq"] == 3
        assert log.find(third["id"][:10]) == third
        assert log.find("99") is None

    def test_an_id_made_only_of_digits_is_found(self, log: AuditLog) -> None:
        """CI drew the id 7282496076 and find() read it as a sequence number."""
        fill(log, 3)
        second = log.find("2")
        assert second is not None
        numeric = "7282496076" + second["id"][10:]
        lines = log.path.read_text().replace(second["id"], numeric)
        log.path.write_text(lines)

        found = log.find(numeric[:10])

        assert found is not None and found["seq"] == 2
        # A sequence number that exists still wins over an id that starts with it.
        assert log.find("3")["seq"] == 3

    def test_iter_since_skips_whole_files_already_shipped(self, tmp_path: Path) -> None:
        log = AuditLog(
            tmp_path / "web-audit.log", settings=AuditSettings(journald="off"), rotate_bytes=1500
        )
        fill(log, 20)
        assert [entry["seq"] for entry in log.iter_since(18)] == [19, 20, 21]
        assert len(list(log.iter_since(0))) == 21


def test_a_write_failure_is_reported_not_raised(tmp_path: Path) -> None:
    from noust.core.audit import health, status

    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the log directory should be")
    log = AuditLog(blocked / "web-audit.log", settings=AuditSettings(journald="off"))

    assert log.append("apps.delete", actor=Actor.system()) is None

    failing, failures, message, _ = status.failure()
    assert failing and failures == 1
    assert message is not None and str(blocked) in message
    report = health(log)
    assert report.status == "error"
    assert "not being written" in report.problems[0]


def test_writes_that_work_again_say_so(tmp_path: Path) -> None:
    from noust.core.audit import status

    target = tmp_path / "later"
    target.write_text("in the way")
    log = AuditLog(target / "web-audit.log", settings=AuditSettings(journald="off"))
    log.append("apps.delete", actor=Actor.system())
    os.unlink(target)

    log.append("apps.update", actor=Actor.system())

    assert [entry["action"] for entry in lines(log.path)][-2:] == ["apps.update", "audit.recovered"]
    assert not status.failure()[0]
