# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The notes and the details Noust writes on a page are sentences with a code, for the console to say in its language (item 151).

The storage page said "Reduce it to 200 MB" and "24 backups; their retention is a backup setting", the
logs page "its containers could not be listed (Docker is not installed on this server)" and the retention
settings "The journal is kept in memory and lost at every reboot": Noust's own words, in English, in a
Spanish console, because each was a string put in a list or a field. They are sentences of the catalog
now, sent beside the English at the same place of the list (``notes_coded``) or the same field
(``detail_coded``), and what a program printed is still a plain note.
"""

from __future__ import annotations

from noust.core.server_text import say
from noust.managers.log_retention import RetentionStatus
from noust.managers.log_search import SearchResult
from noust.managers.server.storage import Candidate


def test_a_note_of_the_log_search_is_kept_as_english_and_as_its_sentence_at_the_same_place() -> (
    None
):
    result = SearchResult()

    result.note(say("log_search.journal_failed", reason="Failed to open the journal"))
    result.notes.append("Something a program said")

    shown = result.to_dict()
    assert shown["notes"] == [
        "The journal could not be searched: Failed to open the journal",
        "Something a program said",
    ]
    assert shown["notes_coded"] == [
        {"code": "log_search.journal_failed", "params": {"reason": "Failed to open the journal"}},
        None,
    ]


def test_a_note_of_the_retention_says_what_an_operator_should_know_with_its_code() -> None:
    status = RetentionStatus()

    status.note(say("log_retention.journal_volatile"))

    assert status.to_dict()["notes"] == [
        "The journal is kept in memory and lost at every reboot. Add --persistent to keep it."
    ]
    assert status.to_dict()["notes_coded"] == [
        {"code": "log_retention.journal_volatile", "params": {}}
    ]


def test_what_the_storage_page_found_is_a_sentence_and_a_path_stays_a_path() -> None:
    found = say("storage.backups_summary", count=24)

    candidate = Candidate(id="backups", detail=found.text, detail_coded=found)
    path = Candidate(id="noust-logs", detail="/var/log/noust")

    assert candidate.to_dict()["detail"] == "24 backups; their retention is a backup setting"
    assert candidate.to_dict()["detail_coded"] == {
        "code": "storage.backups_summary",
        "params": {"count": 24},
    }
    assert path.to_dict()["detail_coded"] is None
