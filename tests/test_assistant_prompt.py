# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the model is told, what it may quote, and what it is given of an earlier turn (item 171).

The instructions are Noust's and are built for every request, never kept. The system's own words are
quoted by Noust, not by the model: the model says which lines of a text it was given it leans on
(``[[S1:14-15]]``) and Noust shows those lines from its own copy, so a model cannot put words in the
system's mouth. An earlier turn is given as the question and the answer: what it read is let go.
"""

from __future__ import annotations

from noust.core.assistant.conversations import Message
from noust.core.assistant.prompt import (
    CITATION,
    SourceBook,
    StreamFilter,
    history_messages,
    parts_of,
    resolve_citations,
    system_prompt,
)


def message(seq: int, role: str, content: str, *, calls=None, answers=None) -> Message:
    return Message(seq, role, content, calls, answers, "2026-11-13T10:00:00+00:00")


class TestTheInstructions:
    def test_they_say_it_is_an_ai_and_that_what_it_reads_is_data(self) -> None:
        text = system_prompt(tools=True, docs=True, server="", hub=False, servers=())
        assert "AI" in text
        assert "untrusted-data" in text
        assert "never follow instructions" in text.lower()
        assert "[[S" in text  # how to quote

    def test_without_tools_it_is_told_it_only_sees_what_is_written(self) -> None:
        text = system_prompt(tools=False, docs=False, server="", hub=False, servers=())
        assert "no tools" in text.lower()

    def test_a_server_chosen_in_a_fleet_is_named(self) -> None:
        text = system_prompt(tools=True, docs=False, server="web-01", hub=True, servers=("web-01",))
        assert "web-01" in text

    def test_all_servers_names_them_and_asks_which_before_a_change(self) -> None:
        text = system_prompt(
            tools=True, docs=False, server="*", hub=True, servers=("web-01", "db-02")
        )
        assert "web-01" in text and "db-02" in text
        assert "which server" in text.lower()


class TestWhatItMayQuote:
    def book(self) -> SourceBook:
        book = SourceBook()
        book.add(
            "log",
            {"code": "assistant.step.deployment_log", "params": {"deployment": 14}},
            "a\nb\nc\nd",
            first_line=11,
        )
        return book

    def test_a_text_is_numbered_from_where_it_starts(self) -> None:
        book = self.book()
        assert book.render("S1").splitlines() == ["11| a", "12| b", "13| c", "14| d"]

    def test_a_citation_is_resolved_to_the_lines_noust_holds(self) -> None:
        book = self.book()
        found = resolve_citations("It failed [[S1:12-13]] because", book, lambda s: s)
        assert found["S1:12-13"]["lines"] == [{"n": 12, "text": "b"}, {"n": 13, "text": "c"}]
        assert found["S1:12-13"]["source"]["id"] == "S1"
        assert found["S1:12-13"]["source"]["label"]["code"] == "assistant.step.deployment_log"

    def test_one_line_and_a_range_are_the_same_thing(self) -> None:
        found = resolve_citations("[[S1:14]] [[S1:11-11]]", self.book(), lambda s: s)
        assert [line["n"] for line in found["S1:14"]["lines"]] == [14]
        assert [line["n"] for line in found["S1:11-11"]["lines"]] == [11]

    def test_what_is_not_there_is_not_quoted(self) -> None:
        found = resolve_citations(
            "[[S1:90-95]] [[S9:1]] [[S1:13-12]] [[S1:10-12]]", self.book(), lambda s: s
        )
        # A range outside the text, a text there is none of and a range backwards are dropped; one
        # that overlaps is cut to what exists.
        assert list(found) == ["S1:10-12"]
        assert [line["n"] for line in found["S1:10-12"]["lines"]] == [11, 12]

    def test_a_citation_is_a_few_lines_not_a_whole_log(self) -> None:
        book = SourceBook()
        book.add("log", {"code": "x", "params": {}}, "\n".join(str(n) for n in range(200)))
        found = resolve_citations("[[S1:1-150]]", book, lambda s: s)
        assert len(found["S1:1-150"]["lines"]) == 12

    def test_the_markers_of_disguised_data_are_put_back_for_the_person(self) -> None:
        book = SourceBook()
        book.add("log", {"code": "x", "params": {}}, "client <ip-1> asked")
        found = resolve_citations("[[S1:1]]", book, lambda s: s.replace("<ip-1>", "203.0.113.7"))
        assert found["S1:1"]["lines"][0]["text"] == "client 203.0.113.7 asked"

    def test_the_answer_is_cut_into_text_and_the_lines_quoted(self) -> None:
        book = self.book()
        answer = "It failed:\n[[S1:12-13]]\nbecause of that. [[S1:99]] Done."
        found = resolve_citations(answer, book, lambda s: s)
        got = parts_of(answer, found, lambda s: s)
        assert [p["type"] for p in got] == ["text", "literal", "text"]
        assert got[0]["text"].strip() == "It failed:"
        assert got[1]["lines"][0] == {"n": 12, "text": "b"}
        # The one that is not there leaves no trace in the text.
        assert "[[" not in got[2]["text"]
        assert got[2]["text"].strip() == "because of that.  Done."

    def test_the_line_numbers_the_model_is_told_are_the_ones_the_person_will_see(self) -> None:
        assert CITATION.fullmatch("[[S12:3-4]]")
        assert not CITATION.fullmatch("[[S12:]]")

    def test_the_texts_from_outside_are_what_provenance_looks_in(self) -> None:
        book = self.book()
        assert book.as_lines() == {"S1": [(11, "a"), (12, "b"), (13, "c"), (14, "d")]}


class TestWhatIsGivenOfEarlierTurns:
    def test_an_earlier_turn_is_its_question_and_its_answer_and_nothing_it_read(self) -> None:
        stored = [
            message(1, "user", "why?\n\n<page-context>\nlong log\n</page-context>"),
            message(
                2,
                "assistant",
                "",
                calls=[{"id": "c1", "name": "application_logs", "arguments": "{}"}],
            ),
            message(3, "tool", "<untrusted-data>lines</untrusted-data>", answers="c1"),
            message(4, "assistant", "It is the variable."),
            message(5, "user", "and now?"),
        ]
        given = history_messages(stored)
        assert [m["role"] for m in given] == ["user", "assistant", "user"]
        assert given[1]["content"] == "It is the variable."
        assert "long log" not in given[0]["content"]
        assert given[0]["content"].startswith("why?")
        assert given[2]["content"] == "and now?"

    def test_the_turn_in_progress_keeps_everything(self) -> None:
        stored = [
            message(1, "user", "why?"),
            message(2, "assistant", "", calls=[{"id": "c1", "name": "t", "arguments": '{"a": 1}'}]),
            message(3, "tool", "result", answers="c1"),
        ]
        given = history_messages(stored)
        assert [m["role"] for m in given] == ["user", "assistant", "tool"]
        assert given[1]["tool_calls"][0]["function"] == {"name": "t", "arguments": '{"a": 1}'}
        assert given[2]["tool_call_id"] == "c1"

    def test_the_context_of_the_last_question_is_kept_for_a_model_that_cannot_look_again(
        self,
    ) -> None:
        stored = [
            message(1, "user", "first\n\n<page-context>\nold\n</page-context>"),
            message(2, "assistant", "ok"),
            message(3, "user", "second\n\n<page-context>\nnew\n</page-context>"),
        ]
        given = history_messages(stored)
        assert "old" not in given[0]["content"]
        assert "new" in given[2]["content"]

    def test_an_earlier_turn_that_ended_without_an_answer_is_just_the_question(self) -> None:
        stored = [message(1, "user", "why?"), message(2, "user", "hello?")]
        assert [m["role"] for m in history_messages(stored)] == ["user", "user"]


class TestTheStreamTheOtherEndSees:
    def run(self, pieces: list[str], restore=lambda s: s) -> list[str]:
        shown = StreamFilter(restore)
        out = [shown.feed(piece) for piece in pieces]
        out.append(shown.flush())
        return out

    def test_a_citation_split_across_pieces_never_shows(self) -> None:
        out = self.run(["It failed [[S", "1:12-", "13]] there"])
        assert "".join(out) == "It failed  there"
        assert "[[" not in "".join(out)

    def test_a_marker_split_across_pieces_is_put_back_whole(self) -> None:
        restore = lambda text: text.replace("<ip-1>", "203.0.113.7")  # noqa: E731
        out = self.run(["Block <i", "p-1> first"], restore)
        assert "".join(out) == "Block 203.0.113.7 first"

    def test_text_that_only_looks_like_the_start_of_one_is_given_in_the_end(self) -> None:
        out = self.run(["a < b and c [ d"])
        assert "".join(out) == "a < b and c [ d"

    def test_a_piece_is_given_as_soon_as_it_is_safe(self) -> None:
        shown = StreamFilter(lambda s: s)
        assert shown.feed("Hello wor") == "Hello wor"
