"""
Tests for what a terminal session leaves behind: its recording.

A recording is evidence of what happened in a root-equivalent shell, so each
test is a way it could fail the one reading it: a secret in it, a flood that
pushes out what was typed, a file the reader's own terminal would obey, a
cast that no longer matches what the audit trail recorded, or a retention
pass that deletes more than it should.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from noust.core.exceptions import NoustError, ValidationError
from noust.core.redact import Scrubber
from noust.terminal.recording import (
    HIDDEN_INPUT,
    RECORDING_ID,
    SessionRecorder,
    StreamScrubber,
    list_recordings,
    plain_text,
    prune,
    read_recording,
    transcript,
)

SESSION = "0123456789abcdef"
SECRET = "s3cr3t-value-123456"


class Clock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def recorder(
    tmp_path: Path, *, clock: Clock | None = None, max_bytes: int = 1_000_000, secrets=(), **meta
):
    return SessionRecorder(
        tmp_path / "recordings",
        session_id=meta.pop("session_id", SESSION),
        meta={"kind": "app", "domain": "shop.example.com", "actor": "maria", **meta},
        rows=24,
        cols=80,
        max_bytes=max_bytes,
        scrubber=Scrubber(secrets),
        clock=clock or Clock(),
    )


def cast_lines(rec) -> list:
    return [json.loads(line) for line in rec.path.read_text(encoding="utf-8").splitlines()]


class TestTheCast:
    def test_it_is_an_asciicast_any_player_plays(self, tmp_path):
        clock = Clock()
        rec = recorder(tmp_path, clock=clock)
        rec.output("$ ")
        clock.advance(1.5)
        rec.input("ls\r")
        clock.advance(0.25)
        rec.output("ls\r\nfile\r\n")
        rec.resize(40, 120)
        rec.close(reason="exited", exit_code=0)

        header, *events = cast_lines(rec)

        assert header["version"] == 2
        assert (header["width"], header["height"]) == (80, 24)
        assert header["noust"]["session"] == SESSION
        assert header["noust"]["actor"] == "maria"
        assert events == [
            [0.0, "o", "$ "],
            [1.5, "i", "ls\r"],
            [1.75, "o", "ls\r\nfile\r\n"],
            [1.75, "r", "120x40"],
        ]

    def test_its_id_says_when_it_started_and_which_session_it_is(self, tmp_path):
        rec = recorder(tmp_path)

        assert RECORDING_ID.fullmatch(rec.id)
        assert rec.id.endswith(SESSION)
        assert rec.path.name == f"{rec.id}.cast"
        rec.close(reason="exited", exit_code=0)

    def test_the_file_and_the_folder_are_private(self, tmp_path):
        rec = recorder(tmp_path)
        rec.close(reason="exited", exit_code=0)

        assert stat.S_IMODE(rec.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(rec.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((rec.directory / f"{rec.id}.json").stat().st_mode) == 0o600

    def test_a_session_id_that_is_not_one_is_refused(self, tmp_path):
        with pytest.raises(ValidationError):
            recorder(tmp_path, session_id="../../etc/passwd")

    def test_a_recording_is_never_written_over(self, tmp_path):
        first = recorder(tmp_path, clock=Clock(100.0))
        first.close(reason="exited", exit_code=0)

        with pytest.raises(FileExistsError):
            recorder(tmp_path, clock=Clock(100.0))

    def test_what_is_written_after_the_end_is_ignored(self, tmp_path):
        rec = recorder(tmp_path)
        rec.close(reason="closed", exit_code=None)
        before = rec.path.read_bytes()

        rec.output("late")
        rec.input("late")
        rec.resize(1, 1)
        rec.close(reason="again", exit_code=1)

        assert rec.path.read_bytes() == before


class TestTheSidecarAndTheDigest:
    def test_the_sidecar_says_how_it_ended_and_what_the_cast_hashes_to(self, tmp_path):
        clock = Clock()
        rec = recorder(tmp_path, clock=clock, label="shop.example.com", account="noust-app-shop")
        rec.output("hello")
        clock.advance(12)
        summary = rec.close(reason="idle", exit_code=-1)

        sidecar = json.loads((rec.directory / f"{rec.id}.json").read_text(encoding="utf-8"))

        assert sidecar["status"] == "complete"
        assert sidecar["end_reason"] == "idle"
        assert sidecar["exit_code"] == -1
        assert sidecar["duration"] == 12
        assert sidecar["domain"] == "shop.example.com"
        assert (
            sidecar["sha256"] == summary.sha256 == hashlib.sha256(rec.path.read_bytes()).hexdigest()
        )
        assert sidecar["bytes"] == summary.bytes == rec.path.stat().st_size

    def test_a_recording_that_was_changed_is_not_intact(self, tmp_path):
        rec = recorder(tmp_path)
        rec.output("rm -rf things")
        rec.close(reason="exited", exit_code=0)
        assert read_recording(rec.id, rec.directory).intact is True

        rec.path.write_text(rec.path.read_text().replace("rm -rf things", "ls"), encoding="utf-8")

        assert read_recording(rec.id, rec.directory).intact is False

    def test_a_session_still_open_has_no_verdict_yet(self, tmp_path):
        rec = recorder(tmp_path)
        rec.output("running")

        assert read_recording(rec.id, rec.directory).intact is None
        rec.close(reason="exited", exit_code=0)

    def test_an_interrupted_session_is_marked_as_such(self, tmp_path):
        rec = recorder(tmp_path)
        rec.close(reason="shutdown", exit_code=None, interrupted=True)

        assert list_recordings(rec.directory)[0].status == "interrupted"


class TestSecrets:
    def test_a_known_secret_is_not_written(self, tmp_path):
        rec = recorder(tmp_path, secrets=[SECRET])
        rec.output(f"DATABASE_PASSWORD={SECRET}\r\n")
        rec.input(f"echo {SECRET}\r")
        rec.close(reason="exited", exit_code=0)

        text = rec.path.read_text(encoding="utf-8")
        events = read_recording(rec.id, rec.directory).events
        output = "".join(data for _t, kind, data in events if kind == "o")
        typed = "".join(data for _t, kind, data in events if kind == "i")

        assert SECRET not in text
        assert output == "DATABASE_PASSWORD=***\r\n"
        assert typed == "echo ***\r"

    def test_a_secret_split_across_two_reads_is_still_found(self, tmp_path):
        rec = recorder(tmp_path, secrets=[SECRET])
        half = len(SECRET) // 2
        rec.output("password: " + SECRET[:half])
        rec.output(SECRET[half:] + " and then more output")
        rec.close(reason="exited", exit_code=0)

        output = "".join(
            data for _t, kind, data in read_recording(rec.id, rec.directory).events if kind == "o"
        )

        assert SECRET not in output and SECRET[:half] not in output.replace("***", "")
        assert "password: ***" in output
        assert output.endswith("and then more output")

    def test_a_secret_split_in_three_pieces_is_found(self, tmp_path):
        rec = recorder(tmp_path, secrets=[SECRET])
        for piece in (SECRET[:5], SECRET[5:11], SECRET[11:]):
            rec.output(piece)
        rec.close(reason="exited", exit_code=0)

        assert SECRET not in rec.path.read_text(encoding="utf-8")

    def test_what_is_not_a_secret_is_written_whole_and_in_order(self, tmp_path):
        rec = recorder(tmp_path, secrets=[SECRET])
        for piece in ("one ", "two ", "three"):
            rec.output(piece)
        rec.close(reason="exited", exit_code=0)

        output = "".join(
            data for _t, kind, data in read_recording(rec.id, rec.directory).events if kind == "o"
        )

        assert output == "one two three"

    def test_what_is_typed_at_a_password_prompt_is_not_recorded(self, tmp_path):
        rec = recorder(tmp_path)
        rec.output("[sudo] password for maria: ")
        rec.input("hunter2", hidden=True)
        rec.input("\r", hidden=True)
        rec.input("whoami\r")
        rec.close(reason="exited", exit_code=0)

        text = rec.path.read_text(encoding="utf-8")
        inputs = [
            data for _t, kind, data in read_recording(rec.id, rec.directory).events if kind == "i"
        ]

        assert "hunter2" not in text
        assert inputs == [HIDDEN_INPUT, "whoami\r"]

    def test_the_stream_scrubber_holds_back_only_what_could_still_become_a_secret(self):
        stream = StreamScrubber(Scrubber([SECRET]))

        emitted = stream.push("a" * 100)

        assert emitted == "a" * (100 - (len(SECRET) - 1))
        assert stream.flush() == "a" * (len(SECRET) - 1)

    def test_without_secrets_nothing_is_held_back(self):
        stream = StreamScrubber(Scrubber())

        assert stream.push("hello") == "hello"
        assert stream.flush() == ""


class TestTheBudget:
    def test_a_flood_of_output_does_not_push_out_what_was_typed(self, tmp_path):
        rec = recorder(tmp_path, max_bytes=2000)
        for _ in range(500):
            rec.output("y\r\n" * 10)
        rec.input("rm -rf /srv/shop\r")
        summary = rec.close(reason="closed", exit_code=None)

        events = read_recording(rec.id, rec.directory).events

        assert ("i", "rm -rf /srv/shop\r") in [(kind, data) for _t, kind, data in events]
        assert summary.truncated_output is True
        assert summary.truncated_input is False
        assert summary.dropped_output_bytes > 0
        assert sum(1 for _t, kind, data in events if kind == "m" and "output" in data) == 1

    def test_the_recording_never_outgrows_its_limit_by_more_than_its_markers(self, tmp_path):
        rec = recorder(tmp_path, max_bytes=4000)
        for _ in range(1000):
            rec.output("x" * 50)
            rec.input("k" * 20)
        summary = rec.close(reason="closed", exit_code=None)

        header = len(rec.path.read_text(encoding="utf-8").splitlines()[0]) + 1
        assert summary.bytes <= 4000 + header + 500
        assert summary.truncated_output and summary.truncated_input

    def test_a_recording_within_its_limit_says_nothing_was_cut(self, tmp_path):
        rec = recorder(tmp_path)
        rec.output("short")
        summary = rec.close(reason="exited", exit_code=0)

        assert (summary.truncated_output, summary.truncated_input) == (False, False)
        assert summary.dropped_output_bytes == summary.dropped_input_bytes == 0

    def test_a_full_disk_ends_the_recording_not_the_session(self, tmp_path, monkeypatch):
        rec = recorder(tmp_path)
        real_write = os.write

        def refuse(descriptor, data):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "write", refuse)
        rec.output("this cannot be written")
        monkeypatch.setattr(os, "write", real_write)
        summary = rec.close(reason="exited", exit_code=0)

        assert summary.failed is True


class TestReadingThemBack:
    def make(self, tmp_path, **meta):
        clock = Clock()
        rec = recorder(tmp_path, clock=clock, **meta)
        rec.output("hello\x1b[31m red\x1b[0m\r\n")
        clock.advance(1)
        rec.input("exit\r")
        rec.close(reason="exited", exit_code=0)
        return rec

    def test_the_list_is_newest_first_and_filterable(self, tmp_path):
        old = recorder(tmp_path, clock=Clock(1_700_000_000.0), session_id="a" * 16, actor="pedro")
        old.close(reason="exited", exit_code=0)
        new = recorder(
            tmp_path, clock=Clock(1_800_000_000.0), session_id="b" * 16, domain="blog.example.com"
        )
        new.close(reason="exited", exit_code=0)
        server = recorder(
            tmp_path, clock=Clock(1_790_000_000.0), session_id="c" * 16, kind="server", domain=None
        )
        server.close(reason="exited", exit_code=0)
        directory = new.directory

        assert [info.id for info in list_recordings(directory)] == [new.id, server.id, old.id]
        assert [i.id for i in list_recordings(directory, domain="blog.example.com")] == [new.id]
        assert [i.id for i in list_recordings(directory, actor="pedro")] == [old.id]
        assert [i.id for i in list_recordings(directory, kind="server")] == [server.id]
        assert len(list_recordings(directory, limit=2)) == 2

    def test_a_session_that_is_still_open_is_recording_and_a_lost_one_is_interrupted(
        self, tmp_path
    ):
        rec = recorder(tmp_path)

        live = list_recordings(rec.directory, active=frozenset({rec.id}))
        lost = list_recordings(rec.directory)

        assert live[0].status == "recording"
        assert lost[0].status == "interrupted"
        rec.close(reason="exited", exit_code=0)

    def test_the_list_of_an_empty_or_missing_folder_is_empty(self, tmp_path):
        assert list_recordings(tmp_path / "nothing") == []

    def test_a_damaged_sidecar_is_skipped_not_fatal(self, tmp_path):
        rec = self.make(tmp_path)
        (rec.directory / f"{rec.id}.json").write_text("{not json", encoding="utf-8")

        assert list_recordings(rec.directory) == []

    def test_reading_one_gives_its_events_and_its_verdict(self, tmp_path):
        rec = self.make(tmp_path, label="shop.example.com")

        recording = read_recording(rec.id, rec.directory)

        assert recording.intact is True
        assert recording.header["noust"]["domain"] == "shop.example.com"
        assert [kind for _t, kind, _d in recording.events] == ["o", "i"]
        assert recording.info.status == "complete"
        assert recording.info.meta["end_reason"] == "exited"

    def test_an_id_that_could_name_another_file_is_refused(self, tmp_path):
        for bad in ("../secret", "..", "x" * 40, "20260101T000000Z-../../etc", ""):
            with pytest.raises(ValidationError):
                read_recording(bad, tmp_path)

    def test_a_recording_that_is_not_there_says_how_to_list_them(self, tmp_path):
        with pytest.raises(NoustError) as missing:
            read_recording("20260101T000000Z-" + "a" * 16, tmp_path)

        assert "audit recordings" in str(missing.value.details)

    def test_a_link_in_place_of_a_cast_is_not_followed(self, tmp_path):
        directory = tmp_path / "recordings"
        directory.mkdir()
        target = tmp_path / "outside.txt"
        target.write_text("{}", encoding="utf-8")
        identifier = "20260101T000000Z-" + "a" * 16
        (directory / f"{identifier}.cast").symlink_to(target)

        with pytest.raises(NoustError):
            read_recording(identifier, directory)

    def test_a_damaged_cast_is_reported_not_a_traceback(self, tmp_path):
        rec = self.make(tmp_path)
        rec.path.write_text("not a cast\n", encoding="utf-8")

        with pytest.raises(NoustError, match="damaged"):
            read_recording(rec.id, rec.directory)


class TestPrintingOne:
    def test_the_programs_escape_sequences_never_reach_the_readers_terminal(self):
        hostile = (
            "ok\x1b[2J\x1b[H"  # clear the screen
            "\x1b]0;stolen title\x07"  # set the window title
            "\x1b]52;c;ZGF0YQ==\x07"  # write the clipboard
            "\x1b[6n"  # ask the terminal to answer
            "\x1bPq#0;2;0;0;0\x1b\\"  # a device control string
            "\x07\x08\x00 end"
        )

        cleaned = plain_text(hostile)

        assert "\x1b" not in cleaned
        assert all(char in "\n\t" or char >= " " for char in cleaned)
        assert cleaned.startswith("ok") and cleaned.endswith(" end")

    def test_line_breaks_survive(self):
        assert plain_text("a\r\nb\rc\n") == "a\nb\nc\n"

    def test_the_transcript_shows_the_output_and_marks_what_was_typed(self, tmp_path):
        clock = Clock()
        rec = recorder(tmp_path, clock=clock)
        rec.output("$ ")
        rec.input("whoami\r")
        rec.output("noust-app-shop\r\n")
        rec.close(reason="exited", exit_code=0)

        text = transcript(read_recording(rec.id, rec.directory))

        assert "noust-app-shop" in text
        assert "> whoami<enter>" in text
        assert "whoami" not in transcript(read_recording(rec.id, rec.directory), with_input=False)


class TestRetention:
    def test_what_ended_before_the_cutoff_is_deleted_with_its_sidecar(self, tmp_path):
        clock = Clock(1_700_000_000.0)
        old = recorder(tmp_path, clock=clock, session_id="a" * 16)
        old.close(reason="exited", exit_code=0)
        recent = recorder(tmp_path, clock=Clock(1_800_000_000.0), session_id="b" * 16)
        recent.close(reason="exited", exit_code=0)

        deleted = prune(datetime.fromtimestamp(1_750_000_000.0), old.directory)

        assert deleted == 1
        assert not old.path.exists()
        assert not (old.directory / f"{old.id}.json").exists()
        assert recent.path.exists()

    def test_a_session_still_recording_is_never_deleted(self, tmp_path):
        old = recorder(tmp_path, clock=Clock(1_700_000_000.0))

        assert prune(datetime.now() + timedelta(days=1), old.directory) == 0
        assert old.path.exists()
        old.close(reason="exited", exit_code=0)

    def test_a_link_is_never_followed_nor_deleted(self, tmp_path):
        old = recorder(tmp_path, clock=Clock(1_700_000_000.0))
        old.close(reason="exited", exit_code=0)
        precious = tmp_path / "precious.txt"
        precious.write_text("keep", encoding="utf-8")
        old.path.unlink()
        old.path.symlink_to(precious)

        prune(datetime.now(), old.directory)

        assert precious.read_text(encoding="utf-8") == "keep"

    def test_the_retention_pass_prunes_recordings_too(self, tmp_path, monkeypatch):
        from noust.core.audit import retention

        old = recorder(tmp_path, clock=Clock(1_700_000_000.0))
        old.close(reason="exited", exit_code=0)
        monkeypatch.setattr(
            "noust.terminal.recording.recordings_dir", lambda: old.directory, raising=True
        )
        report = retention.RetentionReport()

        retention._prune_terminal_recordings(report, datetime.now())

        assert report.deleted["terminal"] == 1
