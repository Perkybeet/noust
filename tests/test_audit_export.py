# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one export of the audit log (item 164).

The command line and the console both ask :mod:`noust.core.audit.export`: the events of a period
that a filter keeps, as NDJSON (every field and the chain) or as CSV (for a spreadsheet). What
the console's page filters on, the export filters on, with the same words.
"""

from __future__ import annotations

import csv
import io
import json

from noust.core.audit import Actor, get_log, record
from noust.core.audit.export import (
    CSV_COLUMNS,
    ExportFilter,
    render_csv,
    render_ndjson,
    select_events,
)


def seed() -> None:
    """Four events by two people, one of them refused, with a target a spreadsheet would run."""
    ana = Actor(kind="user", name="ana")
    luis = Actor(kind="user", name="luis")
    record("apps.update", actor=ana, target="app:shop.example.com", details={"branch": "main"})
    record("apps.delete", actor=luis, target="app:blog.example.com", outcome="denied")
    record("apps.update", actor=luis, target="=HYPERLINK(1)")
    record("auth.login", actor=ana, target="session", correlation_id="req-7")


def rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


class TestWhatIsSelected:
    def test_every_event_oldest_first_without_a_filter(self) -> None:
        seed()
        selected = select_events(get_log(), ExportFilter())
        assert selected[0]["action"] == "audit.chain_start"
        assert [entry["seq"] for entry in selected] == sorted(entry["seq"] for entry in selected)

    def test_the_filter_keeps_what_the_page_filters_on(self) -> None:
        seed()
        log = get_log()
        by_result = select_events(log, ExportFilter(result="denied"))
        assert [entry["action"] for entry in by_result] == ["apps.delete"]
        by_actor = select_events(log, ExportFilter(actor="ana"))
        assert {entry["action"] for entry in by_actor} == {"apps.update", "auth.login"}
        by_request = select_events(log, ExportFilter(correlation_id="req-7"))
        assert [entry["action"] for entry in by_request] == ["auth.login"]
        by_action = select_events(
            log, ExportFilter(action="apps.update", target="app:shop.example.com")
        )
        assert len(by_action) == 1

    def test_the_search_box_matches_what_the_page_matches(self) -> None:
        seed()
        log = get_log()
        # The target, the actor's name and what the event recorded, in any case.
        assert len(select_events(log, ExportFilter(q="SHOP.example"))) == 1
        assert len(select_events(log, ExportFilter(q="luis"))) == 2
        assert len(select_events(log, ExportFilter(q="branch=main"))) == 1
        assert select_events(log, ExportFilter(q="no such thing")) == []
        # Blank is no filter at all.
        assert len(select_events(log, ExportFilter(q="  "))) == len(
            select_events(log, ExportFilter())
        )

    def test_the_range_is_inclusive_and_only_what_is_inside(self) -> None:
        seed()
        everything = select_events(get_log(), ExportFilter())
        first, last = str(everything[0]["ts"]), str(everything[-1]["ts"])
        assert len(select_events(get_log(), ExportFilter(since=first, until=last))) == len(
            everything
        )
        assert select_events(get_log(), ExportFilter(until="2000-01-01T00:00:00+00:00")) == []
        assert select_events(get_log(), ExportFilter(since="2100-01-01T00:00:00+00:00")) == []


class TestTheTwoFormats:
    def test_ndjson_keeps_every_field_and_the_chain(self) -> None:
        seed()
        text = render_ndjson(select_events(get_log(), ExportFilter()))
        events = [json.loads(line) for line in text.splitlines()]
        assert events[0]["action"] == "audit.chain_start"
        assert all("mac" in event for event in events)

    def test_csv_has_a_header_and_one_row_per_event(self) -> None:
        seed()
        text = render_csv(select_events(get_log(), ExportFilter(result="denied")))
        table = rows(text)
        assert list(table[0].keys()) == list(CSV_COLUMNS)
        assert [row["action"] for row in table] == ["apps.delete"]
        assert table[0]["result"] == "denied"
        assert table[0]["resource"] == "app:blog.example.com"

    def test_csv_says_what_the_event_recorded(self) -> None:
        seed()
        table = rows(render_csv(select_events(get_log(), ExportFilter(action="apps.update"))))
        assert any("branch=main" in row["detail"] for row in table)

    def test_a_cell_a_spreadsheet_would_run_is_written_as_text(self) -> None:
        seed()
        table = rows(render_csv(select_events(get_log(), ExportFilter(q="HYPERLINK"))))
        assert table[0]["resource"] == "'=HYPERLINK(1)"

    def test_a_credential_in_what_an_event_recorded_is_masked(self) -> None:
        record(
            "apps.update",
            actor=Actor(kind="user", name="ana"),
            target="app:shop.example.com",
            details={"note": "pulled with ghp_abcdefghijklmnopqrstuvwxyz0123456789ab"},
        )
        text = render_csv(select_events(get_log(), ExportFilter(action="apps.update")))
        assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789ab" not in text

    def test_an_empty_selection_is_the_header_alone_in_csv_and_nothing_in_ndjson(self) -> None:
        assert render_ndjson([]) == ""
        assert render_csv([]).strip() == ",".join(CSV_COLUMNS)
