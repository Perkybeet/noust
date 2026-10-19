# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The rules a point-in-time recovery of PostgreSQL stands on (checklist H-32).

They are pure functions, so they are pinned without an engine: what an archived segment is,
which of them a set of base backups still needs, which base backups a window keeps, which one
an instant is recovered from, and the commands the engine runs to archive and to fetch. The
same rules are proved against a real PostgreSQL in ``tests/integration/engines_run.py``
(``pitr_postgresql``).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.managers.database.pitr_archive import (
    BaseBackup,
    Fork,
    archive_command,
    base_id_at,
    bases_to_drop,
    choose_timeline,
    format_recovery_time,
    missing_segments,
    parse_backup_label,
    parse_base_id,
    parse_history,
    parse_recovery_time,
    prunable_wal,
    restore_command,
    select_base,
    wal_position,
)

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


def base(
    hours_ago: float, start_file: str, *, timeline: int = 1, length_min: int = 2
) -> BaseBackup:
    """
    Build a base backup that finished some hours before NOW.

    Args:
        hours_ago: How long before NOW it finished.
        start_file: The first segment it needs.
        timeline: The timeline it was taken on.
        length_min: How long it took, in minutes.

    Returns:
        The base backup.
    """
    finished = NOW - timedelta(hours=hours_ago)
    started = finished - timedelta(minutes=length_min)
    return BaseBackup(
        id=base_id_at(started),
        started_at=started,
        finished_at=finished,
        start_wal_file=start_file,
        timeline=timeline,
        size=1000,
        sha256="0" * 64,
        server_version="16.4",
        system_id="7430000000000000001",
    )


def segment(number: int, *, timeline: int = 1, log: int = 0) -> str:
    """
    Name a segment.

    Args:
        number: Its number inside the log file.
        timeline: Its timeline.
        log: Which log file.

    Returns:
        The 24-digit name.
    """
    return f"{timeline:08X}{log:08X}{number:08X}"


# ------------------------------------------------------------------ names


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("000000010000000000000003", (1, 0, 3)),
        ("0000000200000001000000FF", (2, 1, 255)),
        ("000000010000000000000003.00000028.backup", (1, 0, 3)),
        ("00000002.history", None),
        ("000000010000000000000003.partial", None),
        ("000000010000000000000003.noust-partial", None),
        ("00000001000000000000000g", None),
        ("0000000100000000000000030", None),
        ("", None),
    ],
)
def test_a_segment_is_ordered_by_timeline_then_position(
    name: str, expected: tuple[int, int, int] | None
) -> None:
    assert wal_position(name) == expected


def test_a_base_backup_is_named_by_the_instant_it_starts() -> None:
    started = datetime(2026, 10, 17, 12, 34, 56, tzinfo=timezone.utc)

    assert base_id_at(started) == "base-20261017T123456Z"
    assert parse_base_id("base-20261017T123456Z.tar.gz") == started
    assert parse_base_id("base-20261017T123456Z") == started
    assert parse_base_id("notabase.tar.gz") is None
    assert parse_base_id("base-2026.tar.gz") is None


# ------------------------------------------------------------------ the label


LABEL = """START WAL LOCATION: 0/3000028 (file 000000010000000000000003)
STOP WAL LOCATION: 0/3000138 (file 000000010000000000000003)
CHECKPOINT LOCATION: 0/3000060
BACKUP METHOD: streamed
BACKUP FROM: primary
START TIME: 2026-10-17 12:34:56 UTC
LABEL: noust-base-20261017T123456Z
START TIMELINE: 1
STOP TIME: 2026-10-17 12:35:07 UTC
STOP TIMELINE: 1
"""


def test_the_archived_backup_file_says_where_recovery_starts_and_when_it_can_stop() -> None:
    label = parse_backup_label(LABEL)

    assert label.label == "noust-base-20261017T123456Z"
    assert label.start_wal_file == "000000010000000000000003"
    assert label.timeline == 1
    assert label.started_at == datetime(2026, 10, 17, 12, 34, 56, tzinfo=timezone.utc)
    assert label.stopped_at == datetime(2026, 10, 17, 12, 35, 7, tzinfo=timezone.utc)


@pytest.mark.parametrize("missing", ["START WAL LOCATION", "LABEL", "START TIME", "STOP TIME"])
def test_a_backup_file_without_what_recovery_needs_is_refused(missing: str) -> None:
    text = "\n".join(line for line in LABEL.splitlines() if not line.startswith(missing))

    with pytest.raises(ValueError):
        parse_backup_label(text)


# ------------------------------------------------------------------ pruning the log


def test_the_log_before_the_oldest_base_backup_is_prunable() -> None:
    names = [segment(n) for n in range(1, 9)]

    assert prunable_wal(names, [base(1, segment(5))]) == [segment(n) for n in range(1, 5)]


def test_the_oldest_base_backup_that_is_kept_decides() -> None:
    names = [segment(n) for n in range(1, 9)]
    bases = [base(30, segment(2)), base(6, segment(6))]

    assert prunable_wal(names, bases) == [segment(1)]


def test_a_later_timeline_is_never_pruned_by_an_earlier_base_backup() -> None:
    names = [segment(1, timeline=1), segment(9, timeline=1), segment(1, timeline=2)]

    assert prunable_wal(names, [base(1, segment(9, timeline=1))]) == [segment(1, timeline=1)]


def test_an_older_timeline_goes_once_every_base_backup_is_on_a_later_one() -> None:
    names = [segment(7, timeline=1), segment(7, timeline=2)]

    assert prunable_wal(names, [base(1, segment(3, timeline=2), timeline=2)]) == [
        segment(7, timeline=1)
    ]


def test_histories_unknown_files_and_a_log_with_no_base_backup_are_never_pruned() -> None:
    names = [segment(1), "00000002.history", "README", segment(2) + ".partial"]

    assert prunable_wal(names, []) == []
    assert prunable_wal(names, [base(1, segment(9))]) == [segment(1)]


def test_a_backup_file_is_pruned_with_its_segment() -> None:
    names = [segment(1) + ".00000028.backup", segment(5) + ".00000028.backup"]

    assert prunable_wal(names, [base(1, segment(5))]) == [segment(1) + ".00000028.backup"]


def test_log_files_with_more_than_one_log_number_order_by_log_first() -> None:
    names = [segment(255, log=0), segment(0, log=1)]

    assert prunable_wal(names, [base(1, segment(0, log=1))]) == [segment(255, log=0)]


# ------------------------------------------------------------------ retention


def test_nothing_is_dropped_without_a_limit() -> None:
    assert (
        bases_to_drop([base(1000, segment(1)), base(1, segment(9))], now=NOW, count=None, days=None)
        == []
    )


def test_the_newest_base_backups_are_kept_by_number() -> None:
    bases = [base(h, segment(h)) for h in (100, 50, 20, 5)]

    dropped = bases_to_drop(bases, now=NOW, count=2, days=None)

    assert [b.start_wal_file for b in dropped] == [segment(100), segment(50)]


def test_the_window_keeps_the_base_backup_that_came_before_it_opens() -> None:
    """Seven days back must be recoverable, so the base from before the seventh day stays."""
    bases = [
        base(24 * 20, segment(1)),
        base(24 * 9, segment(2)),
        base(24 * 6, segment(3)),
        base(2, segment(4)),
    ]

    dropped = bases_to_drop(bases, now=NOW, count=None, days=7)

    assert [b.start_wal_file for b in dropped] == [segment(1)]


def test_both_limits_keep_the_union() -> None:
    bases = [
        base(24 * 20, segment(1)),
        base(24 * 9, segment(2)),
        base(24 * 6, segment(3)),
        base(2, segment(4)),
    ]

    # Three by number: the 9-day, 6-day and 2-hour ones. Seven days: the last two, and the 9-day
    # one that came before the window opened. The union is those three.
    assert [b.start_wal_file for b in bases_to_drop(bases, now=NOW, count=3, days=7)] == [
        segment(1)
    ]
    # Four by number keeps all of them, whatever the window says.
    assert bases_to_drop(bases, now=NOW, count=4, days=7) == []


def test_the_newest_base_backup_stays_whatever_its_age() -> None:
    old = base(24 * 400, segment(1))

    assert bases_to_drop([old], now=NOW, count=1, days=1) == []
    assert bases_to_drop([old, base(24 * 500, segment(0))], now=NOW, count=None, days=1)[
        0
    ].start_wal_file == segment(0)


# ------------------------------------------------------------------ choosing a base


def test_the_base_for_an_instant_is_the_newest_that_finished_before_it() -> None:
    bases = [base(48, segment(1)), base(24, segment(2)), base(2, segment(3))]

    assert select_base(bases, NOW - timedelta(hours=10)).start_wal_file == segment(2)
    assert select_base(bases, NOW - timedelta(hours=2)).start_wal_file == segment(3)
    assert select_base(bases, None).start_wal_file == segment(3)


def test_an_instant_before_every_base_backup_says_how_far_back_it_can_go() -> None:
    with pytest.raises(ValidationError) as caught:
        select_base([base(24, segment(2))], NOW - timedelta(hours=30))

    assert caught.value.field == "time"
    assert "oldest one finished at 2026-10-16 12:00:00 UTC" in str(caught.value.details)


def test_with_no_base_backup_the_error_names_the_command() -> None:
    with pytest.raises(ValidationError, match="no base backup") as caught:
        select_base([], NOW)

    assert "noust db pitr base" in str(caught.value.details)


# ------------------------------------------------------------------ gaps


def test_a_whole_log_has_no_gap() -> None:
    names = [segment(n) for n in range(3, 8)]

    assert missing_segments(names, base(1, segment(3)), segment(7)) == []


def test_a_gap_is_named_so_a_recovery_is_refused_before_anything_is_touched() -> None:
    names = [segment(3), segment(4), segment(6), segment(7)]

    assert missing_segments(names, base(1, segment(3)), segment(7)) == [segment(5)]


def test_the_first_segment_is_always_required() -> None:
    assert missing_segments([], base(1, segment(3)), None) == [segment(3)]


def test_the_log_rolls_over_into_the_next_log_file_after_256_segments() -> None:
    names = [segment(254), segment(255), segment(0, log=1)]

    assert missing_segments(names, base(1, segment(254)), segment(0, log=1)) == []
    assert missing_segments(
        [segment(254), segment(0, log=1)], base(1, segment(254)), segment(0, log=1)
    ) == [segment(255)]


# ------------------------------------------------------------------ instants


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-17 12:34:56",
        "2026-10-17T12:34:56",
        "2026-10-17 12:34:56 UTC",
        "2026-10-17T12:34:56Z",
        "2026-10-17 14:34:56+02:00",
        "2026-10-17T07:34:56-05:00",
    ],
)
def test_an_instant_is_read_as_utc_unless_it_says_otherwise(text: str) -> None:
    assert parse_recovery_time(text) == datetime(2026, 10, 17, 12, 34, 56, tzinfo=timezone.utc)


@pytest.mark.parametrize("text", ["", "yesterday", "2026-13-45 99:00:00", "12:34", "17/10/2026"])
def test_something_that_is_not_an_instant_is_refused_with_how_to_write_one(text: str) -> None:
    with pytest.raises(ValidationError) as caught:
        parse_recovery_time(text)

    assert caught.value.field == "time"
    assert "2026-10-17 12:34:56" in str(caught.value.details)


def test_the_engine_is_given_the_instant_with_an_explicit_offset() -> None:
    assert format_recovery_time(parse_recovery_time("2026-10-17 14:34:56+02:00")) == (
        "2026-10-17 12:34:56+00"
    )


# ------------------------------------------------------------------ the commands


def test_the_archive_command_never_overwrites_and_renames_into_place() -> None:
    command = archive_command("/var/lib/postgresql/noust-wal/7430")

    assert "'" not in command, "it is placed inside a single-quoted statement"
    assert command.startswith('test -f "/var/lib/postgresql/noust-wal/7430/%f" && cmp -s "%p" ')
    assert 'cp "%p" "/var/lib/postgresql/noust-wal/7430/%f.noust-partial"' in command
    assert command.index("sync") < command.index("mv ")
    assert restore_command("/var/lib/postgresql/noust-wal/7430") == (
        'cp "/var/lib/postgresql/noust-wal/7430/%f" "%p"'
    )


@pytest.mark.parametrize(
    "directory",
    [
        "relative/dir",
        "/with space",
        "/with'quote",
        '/with"dq',
        "/a;b",
        "/a$(b)",
        "/a/../b",
        "/a\nb",
        "",
    ],
)
def test_a_directory_that_is_not_a_plain_path_never_reaches_the_command(directory: str) -> None:
    with pytest.raises(ValidationError):
        archive_command(directory)
    with pytest.raises(ValidationError):
        restore_command(directory)


def run_archive(command: str, source: Path, name: str) -> subprocess.CompletedProcess[str]:
    """
    Run the archive command the way the engine does: through ``sh -c`` with %p and %f replaced.

    Args:
        command: The text of ``archive_command``.
        source: The segment as the engine names it (``%p``).
        name: Its file name (``%f``).

    Returns:
        The finished process.
    """
    filled = command.replace("%p", str(source)).replace("%f", name)
    return subprocess.run(["/bin/sh", "-c", filled], capture_output=True, text=True, check=False)


@pytest.mark.allow_subprocess
class TestTheArchiveCommandRunsAsTheEngineRunsIt:
    """The command is shell text, so it is run by a shell: a test of its text alone proves little."""

    def test_a_segment_appears_under_its_name_with_its_bytes(self, tmp_path: Path) -> None:
        archive = tmp_path / "archive"
        archive.mkdir()
        source = tmp_path / "pg_wal_segment"
        source.write_bytes(b"A" * 4096)

        done = run_archive(archive_command(archive), source, segment(3))

        assert done.returncode == 0, done.stderr
        assert (archive / segment(3)).read_bytes() == b"A" * 4096
        assert [p.name for p in archive.iterdir()] == [segment(3)]

    def test_the_same_segment_twice_succeeds(self, tmp_path: Path) -> None:
        archive = tmp_path / "archive"
        archive.mkdir()
        source = tmp_path / "seg"
        source.write_bytes(b"B" * 100)
        command = archive_command(archive)
        assert run_archive(command, source, segment(1)).returncode == 0

        assert run_archive(command, source, segment(1)).returncode == 0

    def test_another_content_under_an_archived_name_fails_and_changes_nothing(
        self, tmp_path: Path
    ) -> None:
        archive = tmp_path / "archive"
        archive.mkdir()
        source = tmp_path / "seg"
        source.write_bytes(b"first")
        command = archive_command(archive)
        run_archive(command, source, segment(1))
        source.write_bytes(b"second")

        done = run_archive(command, source, segment(1))

        assert done.returncode != 0
        assert (archive / segment(1)).read_bytes() == b"first"

    def test_a_copy_that_cannot_complete_leaves_no_segment(self, tmp_path: Path) -> None:
        archive = tmp_path / "archive"
        archive.mkdir()

        done = run_archive(archive_command(archive), tmp_path / "does-not-exist", segment(1))

        assert done.returncode != 0
        assert not (archive / segment(1)).exists()

    def test_the_restore_command_copies_a_segment_back_and_fails_for_one_that_is_not_there(
        self, tmp_path: Path
    ) -> None:
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / segment(2)).write_bytes(b"C" * 10)
        target = tmp_path / "RECOVERYXLOG"
        command = restore_command(archive)

        found = run_archive(command, target, segment(2))
        absent = run_archive(command, tmp_path / "other", segment(9))

        assert found.returncode == 0 and target.read_bytes() == b"C" * 10
        assert absent.returncode != 0


# ------------------------------------------------------------------ timelines


def fork(timeline: int, parent: int, hours_ago: float) -> Fork:
    """
    Build a fork that happened some hours before NOW.

    Args:
        timeline: The new timeline.
        parent: The timeline it continued from.
        hours_ago: How long before NOW.

    Returns:
        The fork.
    """
    return Fork(timeline=timeline, parent=parent, at=NOW - timedelta(hours=hours_ago))


def test_a_history_file_names_the_instant_a_timeline_split_off() -> None:
    parsed = parse_history(
        "00000002.history", "1\t0/3432538\tbefore 2026-10-17 13:08:26.594565+00\n", NOW
    )

    assert parsed == Fork(
        timeline=2,
        parent=1,
        at=datetime(2026, 10, 17, 13, 8, 26, 594565, tzinfo=timezone.utc),
    )


def test_the_last_line_of_a_history_is_the_fork_from_the_parent() -> None:
    text = (
        "1\t0/3432538\tbefore 2026-10-17 13:08:26+00\n2\t0/5000000\tafter 2026-10-17 14:00:00+00\n"
    )

    parsed = parse_history("00000003.history", text, NOW)

    assert parsed is not None and parsed.parent == 2
    assert parsed.at == datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)


def test_an_offset_other_than_utc_is_applied() -> None:
    parsed = parse_history("00000004.history", "1\t0/3\tbefore 2026-10-17 15:08:26+02\n", NOW)

    assert parsed is not None
    assert parsed.at == datetime(2026, 10, 17, 13, 8, 26, tzinfo=timezone.utc)


def test_a_fork_with_no_instant_takes_the_time_the_file_was_archived() -> None:
    parsed = parse_history("00000002.history", "1\t0/3\tno recovery target specified\n", NOW)

    assert parsed is not None and parsed.at == NOW


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("000000010000000000000003", "1\t0/3\tx"),
        ("00000002.history", ""),
        ("00000002.history", "# only a comment\n"),
        ("00000002.history", "not a number\t0/3\tx\n"),
        ("00000002.history", "1\n"),
    ],
)
def test_something_that_is_not_a_history_is_not_read_as_one(name: str, text: str) -> None:
    assert parse_history(name, text, NOW) is None


def test_an_instant_before_a_branch_stays_on_the_branch_it_started_from() -> None:
    """Recovering to the same instant twice: the first recovery's branch has nothing after it."""
    forks = [fork(2, 1, 3)]

    assert choose_timeline(1, NOW - timedelta(hours=5), forks) == 1


def test_an_instant_after_a_branch_followed_it() -> None:
    forks = [fork(2, 1, 3)]

    assert choose_timeline(1, NOW - timedelta(hours=1), forks) == 2


def test_branches_are_followed_in_order_and_no_further_than_the_instant() -> None:
    forks = [fork(2, 1, 10), fork(3, 2, 5), fork(4, 3, 1)]

    assert choose_timeline(1, NOW - timedelta(hours=7), forks) == 2
    assert choose_timeline(1, NOW - timedelta(hours=3), forks) == 3
    assert choose_timeline(1, NOW, forks) == 4
    assert choose_timeline(2, NOW - timedelta(hours=20), forks) == 2


def test_a_branch_of_another_timeline_is_not_followed() -> None:
    forks = [fork(2, 1, 10), fork(5, 4, 8)]

    assert choose_timeline(1, NOW, forks) == 2


def test_of_two_branches_of_the_same_parent_the_later_one_wins() -> None:
    forks = [fork(2, 1, 10), fork(3, 1, 6)]

    assert choose_timeline(1, NOW, forks) == 3
    assert choose_timeline(1, NOW - timedelta(hours=8), forks) == 2
