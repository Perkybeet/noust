# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.core.notifications.excerpt`.

The excerpt is the one place a notification shows another program's words, so
what is defended is that it stays *theirs*: whole lines, verbatim, the end of
the output (where a failure says why), never reworded, never cut in the middle
of a line unless the line itself is absurdly long.
"""

from __future__ import annotations

from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.notifications.excerpt import (
    make_excerpt,
    normalize,
    split_error_text,
    split_evidence,
    strip_journal_prefix,
    trim_excerpt,
)
from noust.core.notifications.model import Excerpt
from noust.deployers.helpers.health_gate import HealthGate

JOURNAL = [
    "Sep 29 10:45:12 web-1 systemd[1]: Started shop-example-com.service.",
    "Sep 29 10:45:13 web-1 npm[48211]: > shop@1.4.2 start",
    "Sep 29 10:45:15 web-1 npm[48227]: Error: Could not find a production build",
    "Sep 29 10:45:15 web-1 systemd[1]: shop-example-com.service: Failed with result 'exit-code'.",
]


class TestMakeExcerpt:
    def test_keeps_whole_lines_in_order(self) -> None:
        excerpt = make_excerpt(JOURNAL, label="Journal")

        assert excerpt is not None
        assert excerpt.lines == tuple(JOURNAL)
        assert excerpt.omitted == 0
        assert excerpt.label == "Journal"

    def test_takes_the_end_of_the_output(self) -> None:
        lines = [f"line {i}" for i in range(30)]

        excerpt = make_excerpt(lines, label="x", max_lines=5)

        assert excerpt is not None
        assert excerpt.lines == tuple(f"line {i}" for i in range(25, 30))
        assert excerpt.omitted == 25

    def test_accepts_text_as_well_as_lines(self) -> None:
        excerpt = make_excerpt("one\ntwo\n\nthree\n", label="x")

        assert excerpt is not None
        assert excerpt.lines == ("one", "two", "three")

    def test_drops_the_oldest_lines_to_fit_the_character_budget(self) -> None:
        lines = [f"{i:02d}" + "a" * 88 for i in range(20)]

        excerpt = make_excerpt(lines, label="x", max_lines=20, max_chars=500)

        assert excerpt is not None
        assert sum(len(line) + 1 for line in excerpt.lines) <= 500
        assert excerpt.omitted == 20 - len(excerpt.lines)
        assert len(excerpt.lines) >= 1

    def test_a_single_huge_line_is_kept_but_cut_with_an_ellipsis(self) -> None:
        excerpt = make_excerpt(["x" * 5000], label="x", max_line=100)

        assert excerpt is not None
        assert len(excerpt.lines) == 1
        assert len(excerpt.lines[0]) == 100
        assert excerpt.lines[0].endswith("…")

    def test_a_line_within_the_limit_is_never_altered(self) -> None:
        line = "  at Object.<anonymous> (/srv/app/next.js:412:19)"

        excerpt = make_excerpt([line], label="x")

        assert excerpt is not None
        assert excerpt.lines == (line,)

    def test_folds_consecutive_identical_lines(self) -> None:
        excerpt = make_excerpt(["boom", "boom", "boom", "other", "boom"], label="x")

        assert excerpt is not None
        assert excerpt.lines == ("boom", "other", "boom")
        assert excerpt.omitted == 0

    def test_terminal_escapes_and_blank_lines_are_gone(self) -> None:
        excerpt = make_excerpt(["\x1b[31mred\x1b[0m", "   ", "", "plain"], label="x")

        assert excerpt is not None
        assert excerpt.lines == ("red", "plain")

    def test_avoids_lines_the_message_already_says(self) -> None:
        excerpt = make_excerpt(
            ["Release 1 did not pass", "real evidence"],
            label="x",
            avoid=["release 1 did not pass!"],
        )

        assert excerpt is not None
        assert excerpt.lines == ("real evidence",)

    def test_nothing_to_show_is_none(self) -> None:
        assert make_excerpt("", label="x") is None
        assert make_excerpt(["  ", "\x1b[0m"], label="x") is None
        assert make_excerpt(["dup"], label="x", avoid=["dup"]) is None

    def test_a_first_line_can_lead_the_excerpt(self) -> None:
        excerpt = make_excerpt(["b", "c"], label="x", lead="Failed to clone", max_lines=3)

        assert excerpt is not None
        assert excerpt.lines == ("Failed to clone", "b", "c")

    def test_normalize_ignores_case_and_punctuation(self) -> None:
        assert normalize("Deploy  failed!") == normalize("deploy failed")


class TestTrimExcerpt:
    def test_leaves_a_short_excerpt_alone(self) -> None:
        excerpt = Excerpt("x", ("a", "b"), 3)

        assert trim_excerpt(excerpt, max_lines=8, max_chars=1000) == excerpt

    def test_keeps_the_end_and_counts_what_it_dropped(self) -> None:
        excerpt = Excerpt("x", tuple(f"l{i}" for i in range(10)), omitted=5)

        trimmed = trim_excerpt(excerpt, max_lines=3, max_chars=1000)

        assert trimmed is not None
        assert trimmed.lines == ("l7", "l8", "l9")
        assert trimmed.omitted == 12

    def test_a_character_budget_can_leave_nothing(self) -> None:
        assert trim_excerpt(Excerpt("x", ("a" * 50,)), max_lines=8, max_chars=10) is None

    def test_an_enormous_line_is_cut_not_lost(self) -> None:
        """An excerpt built by hand with one huge line is shortened, not dropped."""
        trimmed = trim_excerpt(Excerpt("x", ("y" * 5000,)), max_lines=8, max_chars=1000)

        assert trimmed is not None
        assert len(trimmed.lines[0]) == 200
        assert trimmed.lines[0].endswith("\u2026")

    def test_none_stays_none(self) -> None:
        assert trim_excerpt(None, max_lines=8, max_chars=100) is None


class TestJournalPrefix:
    def test_removes_date_and_host_when_every_line_has_them(self) -> None:
        stripped = strip_journal_prefix(tuple(JOURNAL))

        assert stripped[0] == "systemd[1]: Started shop-example-com.service."
        assert stripped[2] == "npm[48227]: Error: Could not find a production build"

    def test_keeps_everything_when_one_line_lacks_it(self) -> None:
        lines = (*JOURNAL, "  at Object.<anonymous> (file.js:1:1)")

        assert strip_journal_prefix(lines) == lines

    def test_handles_the_iso_format(self) -> None:
        lines = ("2026-09-29T10:45:12+0000 web-1 systemd[1]: Started.",)

        assert strip_journal_prefix(lines) == ("systemd[1]: Started.",)

    def test_no_lines_is_no_lines(self) -> None:
        assert strip_journal_prefix(()) == ()


class _Journal:
    def restart(self, name: str) -> None:
        return None

    def logs(self, name: str, lines: int = 50) -> str:
        return "\n".join(JOURNAL)


class TestSplitEvidence:
    def test_reads_what_the_health_gate_really_writes(self) -> None:
        """The gate's evidence format and this parser are tied together here."""
        gate = HealthGate(
            unit="shop-example-com",
            url="http://127.0.0.1:1/",
            services=_Journal(),
            logger=Logger(),
        )
        gate._failures = ["Attempt 1/15: <urlopen error [Errno 111] Connection refused>"]

        evidence = gate.evidence("The application did not answer the health check.")
        parts = split_evidence(evidence)

        assert parts.unit == "shop-example-com"
        assert parts.journal == tuple(JOURNAL)
        assert parts.summary.startswith("The application did not answer the health check.")
        assert "Connection refused" in parts.summary
        assert parts.trailing == ""

    def test_text_after_the_journal_is_kept_apart(self) -> None:
        text = "Summary\n\nLast lines of the journal of app:\nj1\nj2\n\nPutting it back failed."

        parts = split_evidence(text)

        assert parts.journal == ("j1", "j2")
        assert parts.trailing == "Putting it back failed."

    def test_no_journal_means_everything_is_the_summary(self) -> None:
        parts = split_evidence("npm ERR! code ELIFECYCLE\nnpm ERR! errno 1")

        assert parts.unit is None
        assert parts.journal == ()
        assert parts.summary == "npm ERR! code ELIFECYCLE\nnpm ERR! errno 1"


class TestSplitErrorText:
    def test_reads_what_a_noust_error_prints(self) -> None:
        error = NoustError("Backup failed", "tar: Unexpected EOF")

        message, output = split_error_text(str(error))

        assert message == "Backup failed"
        assert output == "tar: Unexpected EOF"

    def test_reads_a_job_error_with_the_tools_output_after_a_blank_line(self) -> None:
        text = "Certificate request failed\n  Details: see below\n\ncertbot: rate limited"

        message, output = split_error_text(text)

        assert message == "Certificate request failed"
        assert output == "see below\n\ncertbot: rate limited"

    def test_a_bare_message_has_no_output(self) -> None:
        assert split_error_text("boom") == ("boom", "")

    def test_nothing_is_nothing(self) -> None:
        assert split_error_text(None) == ("", "")
        assert split_error_text("") == ("", "")
