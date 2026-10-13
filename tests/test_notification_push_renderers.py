# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pushover, ntfy, Gotify and Microsoft Teams: what each receives, for every event.

Snapshots (``NOUST_UPDATE_SNAPSHOTS=1`` rewrites them) for the wording and the
layout, and invariants for what a service refuses: a message over its limit, a
link that is not one, a hostile branch name that becomes markup.
"""

from __future__ import annotations

import json

import pytest

from noust.core.notifications.render import gotify, ntfy, pushover, teams
from tests.notifications_support import catalog, dump, hostile
from tests.snapshot_files import assert_snapshot

LOCALES = ("en", "es")
ROOT = "notifications-push"


def events(locale: str) -> dict:
    return {**catalog(locale), **hostile(locale)}  # type: ignore[arg-type]


@pytest.mark.parametrize("locale", LOCALES)
def test_pushover(locale: str) -> None:
    payloads = {code: pushover.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/pushover.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_ntfy(locale: str) -> None:
    payloads = {code: ntfy.render(n, "noust-alerts") for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/ntfy.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_gotify(locale: str) -> None:
    payloads = {code: gotify.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/gotify.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_teams(locale: str) -> None:
    payloads = {code: teams.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/teams.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
class TestLimits:
    def test_pushover_stays_inside_its_limits(self, locale: str) -> None:
        for code, n in events(locale).items():
            fields = pushover.render(n)

            assert len(fields["message"]) <= pushover.MESSAGE_LIMIT, code
            assert len(fields["title"]) <= pushover.TITLE_LIMIT, code
            assert fields["priority"] in {"-1", "0", "1"}, code

    def test_ntfy_stays_under_four_kilobytes_of_utf8(self, locale: str) -> None:
        for code, n in events(locale).items():
            body = ntfy.render(n, "t")

            assert len(body["message"].encode("utf-8")) < 4096, code
            assert 1 <= body["priority"] <= 5, code

    def test_gotify_priority_is_in_its_scale(self, locale: str) -> None:
        for code, n in events(locale).items():
            assert 0 <= gotify.render(n)["priority"] <= 10, code

    def test_a_teams_card_stays_well_inside_its_size(self, locale: str) -> None:
        for code, n in events(locale).items():
            assert len(json.dumps(teams.render(n))) < 28_000, code


@pytest.mark.parametrize("locale", LOCALES)
class TestTheLinkAndTheState:
    def test_the_console_link_is_a_field_of_its_own_and_not_repeated_in_the_text(
        self, locale: str
    ) -> None:
        n = catalog(locale)["deploy.failed"]  # type: ignore[arg-type]
        url = n.console_link.url if n.console_link else ""
        assert url

        assert pushover.render(n)["url"] == url and url not in pushover.render(n)["message"]
        assert ntfy.render(n, "t")["click"] == url and url not in ntfy.render(n, "t")["message"]
        assert gotify.render(n)["extras"]["client::notification"]["click"]["url"] == url
        assert gotify.render(n)["message"].count(url) == 0
        assert teams.render(n)["attachments"][0]["content"]["actions"][0]["url"] == url

    def test_a_failure_is_loud_and_a_success_is_quiet(self, locale: str) -> None:
        failed = catalog(locale)["deploy.failed"]  # type: ignore[arg-type]
        done = catalog(locale)["deploy.succeeded"]  # type: ignore[arg-type]

        assert pushover.render(failed)["priority"] == "1"
        assert pushover.render(done)["priority"] == "-1"
        assert ntfy.render(failed, "t")["priority"] > ntfy.render(done, "t")["priority"]
        assert gotify.render(failed)["priority"] > gotify.render(done)["priority"]

    def test_the_card_colours_follow_the_state(self, locale: str) -> None:
        failed = teams.render(catalog(locale)["deploy.failed"])  # type: ignore[arg-type]
        done = teams.render(catalog(locale)["deploy.succeeded"])  # type: ignore[arg-type]

        heading = lambda card: card["attachments"][0]["content"]["body"][0]["color"]  # noqa: E731
        assert (heading(failed), heading(done)) == ("Attention", "Good")

    def test_no_picture_stands_in_for_a_word(self, locale: str) -> None:
        n = catalog(locale)["deploy.failed"]  # type: ignore[arg-type]

        assert ntfy.render(n, "t")["tags"] == [n.kind, n.server]
