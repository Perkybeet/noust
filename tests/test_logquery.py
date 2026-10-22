# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The journal's query language (checklist M-40): what is read, what it means, what it refuses, what
journalctl is asked for, and the entries around one entry.
"""

from __future__ import annotations

import json

import pytest

from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.managers.server import logquery
from noust.managers.server.errors import ServerError
from noust.managers.server.journal import MAX_CONTEXT, MAX_SCAN, JournalReader
from noust.managers.server.logquery import And, Anything, Entry, Field, Not, Or, QueryError, Term


def entry(
    message: str = "", *, unit: str = "nginx.service", priority: int = 6, pid: int | None = None
) -> Entry:
    return Entry(unit=unit, priority=priority, pid=pid, message=message)


NO_APPS = lambda domain: ()  # noqa: E731
SHOP = lambda domain: ("shop-example-com",) if domain == "shop.example.com" else ()  # noqa: E731


def says(query: str, candidate: Entry, apps=NO_APPS) -> bool:
    return logquery.matches(logquery.parse(query), candidate, apps)


class TestReading:
    def test_an_empty_query_is_everything(self) -> None:
        assert logquery.parse("") == Anything()
        assert logquery.parse("   ") == Anything()
        assert says("", entry("anything"))

    def test_words_are_anded_and_a_phrase_is_one_term(self) -> None:
        assert logquery.parse("error timeout") == And((Term("error"), Term("timeout")))
        assert logquery.parse('"connection refused"') == Term("connection refused")
        assert logquery.parse("error AND timeout") == logquery.parse("error timeout")

    def test_a_hyphen_inside_a_word_is_part_of_it_and_one_in_front_excludes(self) -> None:
        assert logquery.parse("out-of-memory") == Term("out-of-memory")
        assert logquery.parse("-debug") == Not(Term("debug"))
        assert logquery.parse("NOT debug") == Not(Term("debug"))
        assert logquery.parse("- ") == Term("-")

    def test_not_binds_tighter_than_and_and_and_tighter_than_or(self) -> None:
        assert logquery.parse("a b OR c") == Or((And((Term("a"), Term("b"))), Term("c")))
        assert logquery.parse("a OR b c") == Or((Term("a"), And((Term("b"), Term("c")))))
        assert logquery.parse("-a b") == And((Not(Term("a")), Term("b")))

    def test_parentheses_group(self) -> None:
        assert logquery.parse("(a OR b) c") == And((Or((Term("a"), Term("b"))), Term("c")))
        assert logquery.parse("-(a OR b)") == Not(Or((Term("a"), Term("b"))))

    def test_lower_case_or_is_a_word_to_look_for(self) -> None:
        assert logquery.parse("this or that") == And((Term("this"), Term("or"), Term("that")))

    def test_fields_take_a_word_or_a_phrase_and_their_names_have_synonyms(self) -> None:
        assert logquery.parse("@unit:nginx") == Field("unit", "nginx")
        assert logquery.parse("@service:nginx") == Field("unit", "nginx")
        assert logquery.parse('@app:"shop.example.com"') == Field("app", "shop.example.com")
        assert logquery.parse("@priority:err") == Field("level", "err")
        assert logquery.parse("@UNIT:nginx") == Field("unit", "nginx")

    @pytest.mark.parametrize(
        ("query", "fragment"),
        [
            ('"open', "quote is not closed"),
            ("(a b", "parenthesis is not closed"),
            ("a b)", "Unexpected closing parenthesis"),
            ("()", "Unexpected closing parenthesis"),
            ("a AND", "ends where"),
            ("AND a", "needs something on both sides"),
            ("a OR OR b", "needs something on both sides"),
            ("@colour:red", "There is no field @colour"),
            ("@unit:", "needs a value"),
            ("@unit", "A field is written"),
            ("@level:loud", "is not a level"),
            ("@level:9", "is not a level"),
            ("@pid:abc", "not a process number"),
            ("@unit:" + "*a" * 6, "wildcards"),
            ("x" * 501, "longer than"),
            (" ".join(["a"] * 60), "more than"),
            ("(" * 12 + "a" + ")" * 12, "nested more than"),
        ],
    )
    def test_what_cannot_be_read_says_where_and_what_the_syntax_is(
        self, query: str, fragment: str
    ) -> None:
        with pytest.raises(QueryError) as raised:
            logquery.parse(query)
        assert fragment in str(raised.value)
        assert "@unit:" in (raised.value.details or "")
        assert isinstance(raised.value, ValidationError)

    def test_the_position_is_counted_from_one_for_a_person(self) -> None:
        with pytest.raises(QueryError, match=r"at character 7"):
            logquery.parse("error @colour:red")


class TestRenderedBack:
    @pytest.mark.parametrize(
        ("query", "written"),
        [
            ("", ""),
            ("@unit:nginx error -timeout", "@unit:nginx AND error AND NOT timeout"),
            ("a b OR c", "a AND b OR c"),
            ("(a OR b) c", "(a OR b) AND c"),
            ("-(a OR b)", "NOT (a OR b)"),
            ('"connection refused"', '"connection refused"'),
            ("@service:nginx", "@unit:nginx"),
        ],
    )
    def test_the_query_as_it_was_understood(self, query: str, written: str) -> None:
        assert logquery.render(logquery.parse(query)) == written

    @pytest.mark.parametrize(
        "query", ["@unit:nginx error -timeout", "(a OR b) c", "-(a OR b) d", 'x "y z"']
    )
    def test_what_is_written_back_reads_the_same(self, query: str) -> None:
        tree = logquery.parse(query)
        assert logquery.parse(logquery.render(tree)) == tree


class TestJudging:
    def test_a_word_is_found_in_the_message_ignoring_case(self) -> None:
        assert says("TIMEOUT", entry("upstream timed out: Timeout"))
        assert not says("refused", entry("upstream timed out"))
        assert says('"timed out"', entry("upstream timed out"))
        assert not says('"timed  out"', entry("upstream timed out"))

    def test_a_unit_is_the_same_with_or_without_service_and_in_any_case(self) -> None:
        assert says("@unit:nginx", entry(unit="nginx.service"))
        assert says("@unit:NGINX.service", entry(unit="nginx"))
        assert not says("@unit:nginx", entry(unit="nginx-exporter.service"))

    def test_a_unit_may_use_a_wildcard(self) -> None:
        assert says("@unit:noust-*", entry(unit="noust-web.service"))
        assert says("@unit:*-example-com", entry(unit="shop-example-com.service"))
        assert not says("@unit:noust-*", entry(unit="nginx.service"))

    def test_an_application_is_the_units_it_runs_as(self) -> None:
        assert says("@app:shop.example.com", entry(unit="shop-example-com.service"), SHOP)
        assert not says("@app:shop.example.com", entry(unit="nginx.service"), SHOP)
        assert not says("@app:unknown.example.com", entry(unit="shop-example-com.service"), SHOP)

    def test_a_level_is_that_level_or_worse_by_name_or_number(self) -> None:
        assert says("@level:err", entry(priority=3))
        assert says("@level:err", entry(priority=0))
        assert not says("@level:err", entry(priority=4))
        assert says("@level:warn", entry(priority=4))
        assert says("@level:4", entry(priority=4))
        assert says("@priority:error", entry(priority=3))

    def test_a_process(self) -> None:
        assert says("@pid:4821", entry(pid=4821))
        assert not says("@pid:4821", entry(pid=4822))
        assert not says("@pid:4821", entry(pid=None))

    def test_and_or_not(self) -> None:
        assert says("error -timeout", entry("error: refused"))
        assert not says("error -timeout", entry("error: timeout"))
        assert says("error OR fatal", entry("fatal: x"))
        assert says("(error OR fatal) @unit:nginx", entry("fatal: x", unit="nginx"))
        assert not says("(error OR fatal) @unit:nginx", entry("fatal: x", unit="sshd"))
        assert says("NOT (error OR fatal)", entry("all fine"))


class TestWhatJournalctlIsAskedToDo:
    def narrowed(self, query: str, apps=NO_APPS) -> logquery.Pushdown:
        return logquery.pushdown(logquery.parse(query), apps)

    def test_a_unit_and_a_level_that_every_match_must_satisfy_are_pushed(self) -> None:
        assert self.narrowed("@unit:nginx @level:err timeout") == logquery.Pushdown(
            units=("nginx",), priority=3
        )

    def test_an_applications_units_are_pushed(self) -> None:
        assert self.narrowed("@app:shop.example.com", SHOP).units == ("shop-example-com",)
        assert self.narrowed("@app:unknown.example.com", SHOP).units == ()

    def test_nothing_under_an_or_or_a_not_is_pushed(self) -> None:
        assert self.narrowed("@unit:nginx OR @unit:sshd") == logquery.Pushdown()
        assert self.narrowed("-@unit:nginx") == logquery.Pushdown()
        assert self.narrowed("(@level:err OR timeout)") == logquery.Pushdown()

    def test_two_units_anded_are_not_pushed_because_nothing_is_logged_as_both(self) -> None:
        assert self.narrowed("@unit:nginx @unit:sshd").units == ()

    def test_a_wildcard_is_judged_here_not_by_journalctl(self) -> None:
        assert self.narrowed("@unit:noust-*").units == ()

    def test_the_stricter_of_two_levels_is_the_read(self) -> None:
        assert self.narrowed("@level:warn @level:err").priority == 3


def line(
    cursor: str,
    message: str,
    *,
    unit: str = "nginx.service",
    priority: int = 6,
    pid: int = 1,
    at: int = 0,
) -> str:
    return json.dumps(
        {
            "__CURSOR": cursor,
            "__REALTIME_TIMESTAMP": str(1_790_000_000_000_000 + at * 1_000_000),
            "MESSAGE": message,
            "_SYSTEMD_UNIT": unit,
            "PRIORITY": str(priority),
            "_PID": str(pid),
        }
    )


def journal(*lines: str, exit_code: int = 0, stderr: str = "") -> JournalReader:
    return JournalReader(
        runner=FakeRunner().script(
            ["journalctl"], stdout="\n".join(lines) + "\n", exit_code=exit_code, stderr=stderr
        )
    )


class TestSearching:
    def test_returns_the_matches_and_how_much_was_looked_at(self) -> None:
        reader = journal(
            line("c1", "GET / 200", at=1),
            line("c2", "upstream timed out", priority=3, at=2),
            line("c3", "GET / 200", at=3),
            line("c4", "upstream timed out again", priority=3, at=4),
        )

        page = reader.search("timed out @level:err")

        assert [e.cursor for e in page.entries] == ["c2", "c4"]
        assert (page.matched, page.scanned, page.truncated, page.scan_limit_reached) == (
            2,
            4,
            False,
            False,
        )
        assert page.normalised == "timed AND out AND @level:err"
        assert page.oldest is not None and page.oldest.startswith("2026-")

    def test_asks_journalctl_for_the_unit_and_level_and_the_range(self) -> None:
        runner = FakeRunner().script(["journalctl"], stdout="")
        JournalReader(runner=runner).search(
            "@unit:nginx @level:err timeout", since="-2h", until="-30min", scan=300
        )

        argv = runner.calls[0]
        assert argv[0] == "journalctl"
        assert "--unit=nginx" in argv and "--priority=3" in argv
        assert "--since=-2h" in argv and "--until=-30min" in argv
        assert "--lines=301" in argv  # one more than examined: how an unread past is known

    def test_judges_the_whole_query_again_whatever_was_pushed_down(self) -> None:
        reader = journal(
            line("a", "boom", unit="sshd.service"), line("b", "boom", unit="nginx.service")
        )
        page = reader.search("@unit:nginx boom")
        assert [e.cursor for e in page.entries] == ["b"]

    def test_keeps_the_newest_matches_when_there_are_more_than_asked_for(self) -> None:
        reader = journal(*(line(f"c{n}", "match", at=n) for n in range(5)))
        page = reader.search("match", lines=2)
        assert [e.cursor for e in page.entries] == ["c3", "c4"]
        assert (page.matched, page.truncated) == (5, True)

    def test_says_when_the_range_held_older_entries_it_did_not_look_at(self) -> None:
        reader = journal(*(line(f"c{n}", "x", at=n) for n in range(4)))
        page = reader.search("x", scan=3)
        assert page.scan_limit_reached is True
        assert page.scanned == 3
        assert [e.cursor for e in page.entries] == ["c1", "c2", "c3"]

    def test_resolves_an_application_to_its_units(self) -> None:
        runner = FakeRunner().script(
            ["journalctl"], stdout=line("a", "up", unit="shop-example-com.service")
        )
        page = JournalReader(runner=runner).search("@app:shop.example.com", app_units=SHOP)
        assert "--unit=shop-example-com" in runner.calls[0]
        assert [e.cursor for e in page.entries] == ["a"]

    @pytest.mark.parametrize("unit", ["--all", "a b", "$(id)", "../x"])
    def test_a_unit_that_is_not_a_name_never_reaches_journalctl(self, unit: str) -> None:
        runner = FakeRunner()
        with pytest.raises(ValidationError, match="Not a unit name"):
            JournalReader(runner=runner).search(f'@unit:"{unit}"')
        assert runner.calls == []

    def test_a_query_that_cannot_be_read_never_reaches_journalctl(self) -> None:
        runner = FakeRunner()
        with pytest.raises(QueryError):
            JournalReader(runner=runner).search("(unclosed")
        assert runner.calls == []

    @pytest.mark.parametrize(
        ("lines", "scan"), [(0, 100), (1001, 100), (10, 0), (10, MAX_SCAN + 1)]
    )
    def test_the_bounds_are_checked(self, lines: int, scan: int) -> None:
        with pytest.raises(ValidationError):
            journal().search("x", lines=lines, scan=scan)

    def test_a_failure_carries_journalctls_own_words(self) -> None:
        with pytest.raises(ServerError) as raised:
            journal(exit_code=1, stderr="Failed to open journal: Permission denied").search("x")
        assert "Permission denied" in (raised.value.output or "")


class TestContext:
    def reader(self) -> tuple[JournalReader, FakeRunner]:
        runner = FakeRunner()
        # Before it, journalctl --reverse prints the entry itself first and then what came earlier.
        runner.script(
            ["journalctl", "--no-pager", "--output=json", "--reverse"],
            stdout="\n".join(
                [
                    line("t", "the one", at=10),
                    line("b2", "before 2", at=9),
                    line("b1", "before 1", at=8),
                ]
            ),
        )
        runner.script(
            ["journalctl", "--no-pager", "--output=json", "--lines=2"],
            stdout="\n".join([line("a1", "after 1", at=11), line("a2", "after 2", at=12)]),
        )
        return JournalReader(runner=runner), runner

    def test_shows_what_led_to_an_entry_and_what_came_of_it_in_order(self) -> None:
        reader, runner = self.reader()

        page = reader.context("t", before=2, after=2)

        assert [e.cursor for e in page.entries] == ["b1", "b2", "t", "a1", "a2"]
        assert page.target == 2
        before, after = runner.calls
        assert "--cursor=t" in before and "--reverse" in before and "--lines=3" in before
        assert "--after-cursor=t" in after and "--lines=2" in after

    def test_can_stay_with_one_unit(self) -> None:
        reader, runner = self.reader()
        reader.context("t", before=2, after=2, unit="nginx")
        assert all("--unit=nginx" in call for call in runner.calls)

    def test_reads_nothing_after_when_nothing_after_is_asked(self) -> None:
        reader, runner = self.reader()
        page = reader.context("t", before=2, after=0)
        assert len(runner.calls) == 1
        assert page.entries[-1].cursor == "t"

    def test_says_when_the_entry_is_no_longer_in_the_journal(self) -> None:
        runner = FakeRunner().script(["journalctl"], stdout=line("other", "x"))
        page = JournalReader(runner=runner).context("gone", before=1, after=0)
        assert page.target is None

    @pytest.mark.parametrize("cursor", ["--all", "a b", "", "x" * 300, "$(id)"])
    def test_a_cursor_that_is_not_one_never_reaches_journalctl(self, cursor: str) -> None:
        runner = FakeRunner()
        with pytest.raises(ValidationError, match="not a journal cursor"):
            JournalReader(runner=runner).context(cursor)
        assert runner.calls == []

    @pytest.mark.parametrize(
        ("before", "after"), [(-1, 1), (1, -1), (MAX_CONTEXT + 1, 1), (1, MAX_CONTEXT + 1)]
    )
    def test_the_counts_are_bounded(self, before: int, after: int) -> None:
        with pytest.raises(ValidationError):
            journal().context("t", before=before, after=after)


class TestReadingForwardFromACursor:
    def test_keeps_the_entries_that_follow_it_when_there_are_more_than_asked_for(self) -> None:
        # journalctl --after-cursor prints the entries right after it, oldest first: the extra one is the newest.
        reader = journal(line("n1", "1"), line("n2", "2"), line("n3", "3"))
        page = reader.read(lines=2, after_cursor="c0")
        assert [e.cursor for e in page.entries] == ["n1", "n2"]
        assert page.truncated is True
        assert page.next_cursor == "n2"

    def test_still_keeps_the_newest_without_a_cursor(self) -> None:
        reader = journal(line("n1", "1"), line("n2", "2"), line("n3", "3"))
        page = reader.read(lines=2)
        assert [e.cursor for e in page.entries] == ["n2", "n3"]
