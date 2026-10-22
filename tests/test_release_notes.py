# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an update will change, before the operator updates (checklist N-24), and its routes.

``release_notes.read`` asks GitHub for the release of a version, through the bounded reader the
update check uses, and never fails a request: a release it cannot read is an answer saying why.
``GET /api/system/changelog`` is the notes of the releases this installation ships with, and
``GET /api/system/release-notes`` the notes of one it does not.
"""

from __future__ import annotations

import json
import urllib.error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import changelog, package_index, release_notes
from noust.core.changelog import Entry, Item
from noust.core.update_checker import UpdateChecker
from noust.web.api import system as system_api
from noust.web.api.auth import get_current_session

BODY = (
    "## Changes in v3.4.0\n\n"
    "* A new thing\n"
    "* Action required: run noust setup doctor\n\n"
    "## Installation\n\n* not a change\n"
)


def github(body: str = BODY, url: str = "https://github.com/Perkybeet/noust/releases/tag/v3.4.0"):
    """A reader that answers like GitHub's release API, and records what it was asked."""
    asked: list[tuple[str, str | None]] = []

    def fetch(address: str, *, accept: str | None = None) -> bytes:
        asked.append((address, accept))
        return json.dumps({"body": body, "html_url": url}).encode()

    fetch.asked = asked  # type: ignore[attr-defined]
    return fetch


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    release_notes.clear_cache()


# -- reading a release


def test_the_lines_of_a_release_are_read_from_its_github_page() -> None:
    reader = github()

    notes = release_notes.read("3.4.0", fetch=reader)

    assert notes.available
    assert notes.entry is not None
    assert [(i.text, i.action_required) for i in notes.entry.items] == [
        ("A new thing", False),
        ("run noust setup doctor", True),
    ]
    assert notes.url == "https://github.com/Perkybeet/noust/releases/tag/v3.4.0"
    assert reader.asked == [  # type: ignore[attr-defined]
        (
            "https://api.github.com/repos/Perkybeet/noust/releases/tags/v3.4.0",
            "application/vnd.github+json",
        )
    ]


def test_an_answer_is_kept_so_a_polling_console_does_not_ask_every_time() -> None:
    reader = github()
    clock = {"now": 0.0}

    release_notes.read("3.4.0", fetch=reader, now=lambda: clock["now"])
    clock["now"] = 600
    release_notes.read("3.4.0", fetch=reader, now=lambda: clock["now"])
    assert len(reader.asked) == 1  # type: ignore[attr-defined]

    clock["now"] = release_notes.CACHE_SECONDS + 1
    release_notes.read("3.4.0", fetch=reader, now=lambda: clock["now"])
    assert len(reader.asked) == 2  # type: ignore[attr-defined]


@pytest.mark.parametrize("version", ["latest", "3.4", "3.4.0; rm -rf /", "../../etc", "v3.4.0", ""])
def test_a_version_that_is_not_a_release_is_never_asked_after(version: str) -> None:
    reader = github()

    notes = release_notes.read(version, fetch=reader)

    assert not notes.available
    assert "is not a release" in notes.reason
    assert reader.asked == []  # type: ignore[attr-defined]


def test_with_the_update_check_off_nothing_is_requested() -> None:
    reader = github()

    notes = release_notes.read("3.4.0", enabled=False, fetch=reader)

    assert not notes.available
    assert "updates.check" in notes.reason
    assert notes.url.endswith("/releases/tag/v3.4.0")
    assert reader.asked == []  # type: ignore[attr-defined]


def test_a_github_that_does_not_answer_is_a_reason_not_an_error() -> None:
    def down(address: str, *, accept: str | None = None) -> bytes:
        raise urllib.error.URLError("temporary failure in name resolution")

    notes = release_notes.read("3.4.0", fetch=down)

    assert not notes.available
    assert "GitHub did not answer for 3.4.0" in notes.reason
    assert notes.url.endswith("/releases/tag/v3.4.0")


def test_a_failure_is_not_remembered() -> None:
    def down(address: str, *, accept: str | None = None) -> bytes:
        raise urllib.error.URLError("down")

    release_notes.read("3.4.0", fetch=down)
    again = release_notes.read("3.4.0", fetch=github())

    assert again.available


def test_an_answer_that_is_not_a_release_document_is_a_reason() -> None:
    def odd(address: str, *, accept: str | None = None) -> bytes:
        return b'["not", "a", "release"]'

    assert not release_notes.read("3.4.0", fetch=odd).available


def test_a_release_without_a_list_of_changes_says_so() -> None:
    notes = release_notes.read("3.4.0", fetch=github(body="Just text."))

    assert not notes.available
    assert "no list of changes" in notes.reason


def test_an_address_that_is_not_githubs_is_replaced_by_the_release_page() -> None:
    notes = release_notes.read("3.4.0", fetch=github(url="http://169.254.169.254/latest"))

    assert notes.url == "https://github.com/Perkybeet/noust/releases/tag/v3.4.0"


def test_the_notes_are_bounded() -> None:
    many = "## Changes in v3.4.0\n\n" + "\n".join(f"* line {n}" for n in range(500))

    notes = release_notes.read("3.4.0", fetch=github(body=many))

    assert notes.entry is not None
    assert len(notes.entry.items) == release_notes.MAX_ITEMS


def test_the_default_reader_is_the_bounded_public_https_one() -> None:
    """Not urllib directly: the update check's reader refuses private addresses and big bodies."""
    import inspect

    source = inspect.getsource(release_notes.read)

    assert "package_index.fetch" in source
    assert "urllib" not in inspect.getsource(release_notes)


# -- the routes


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router, prefix="/api/system")
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "t", "scope": "read"}
    return TestClient(app)


ENTRIES = [
    Entry("3.4.0", "2026-10-13", (Item("A", False), Item("do this", True))),
    Entry("3.3.1", "2026-10-05", (Item("B"),)),
    Entry("3.3.0", "2026-10-03", (Item("C"),)),
]


def test_the_changelog_route_lists_the_releases_this_installation_ships_with(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(changelog, "packaged", lambda: ENTRIES)

    body = client.get("/api/system/changelog").json()

    assert [e["version"] for e in body["entries"]] == ["3.4.0", "3.3.1", "3.3.0"]
    assert body["entries"][0]["action_required"] is True
    assert body["entries"][0]["items"][1] == {"text": "do this", "action_required": True}
    assert body["entries"][1]["action_required"] is False
    assert body["current_version"]


def test_the_changelog_can_be_asked_for_what_is_newer_than_a_version(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(changelog, "packaged", lambda: ENTRIES)

    body = client.get("/api/system/changelog?since=3.3.0&limit=1").json()

    assert [e["version"] for e in body["entries"]] == ["3.4.0"]


@pytest.mark.parametrize("query", ["since=latest", "since=3.3", "limit=0", "limit=61"])
def test_a_malformed_changelog_query_is_refused(client: TestClient, query: str) -> None:
    assert client.get(f"/api/system/changelog?{query}").status_code == 422


def test_the_release_notes_route_answers_what_an_update_will_change(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(package_index, "fetch", github())
    monkeypatch.setattr(UpdateChecker, "enabled", classmethod(lambda cls: True))

    body = client.get("/api/system/release-notes?version=3.4.0").json()

    assert body["available"] is True
    assert body["entry"]["items"][1] == {"text": "run noust setup doctor", "action_required": True}
    assert body["reason"] == ""


def test_the_release_notes_route_says_why_when_it_cannot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(UpdateChecker, "enabled", classmethod(lambda cls: False))

    body = client.get("/api/system/release-notes?version=3.4.0").json()

    assert body["available"] is False
    assert body["entry"] is None
    assert "updates.check" in body["reason"]
    assert body["url"].endswith("/releases/tag/v3.4.0")


@pytest.mark.parametrize("version", ["latest", "3.4", "3.4.0%3Bid", "..%2F..%2Fetc"])
def test_the_release_notes_route_refuses_what_is_not_a_release(
    client: TestClient, version: str
) -> None:
    assert client.get(f"/api/system/release-notes?version={version}").status_code == 422
