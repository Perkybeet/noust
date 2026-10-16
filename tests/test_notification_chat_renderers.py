# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Mattermost, Matrix, Lark and PagerDuty: what each receives, for every event.

Snapshots (``NOUST_UPDATE_SNAPSHOTS=1`` rewrites them) for the wording and the
layout, and invariants for what a service refuses or must never do: a message
over its limit, a mention that pings somebody, markup built out of a hostile
branch name, an incident that is opened and never closed.
"""

from __future__ import annotations

import json
import re

import pytest

from noust.core.notifications.model import State
from noust.core.notifications.render import lark, matrix, mattermost, pagerduty
from tests.notifications_support import catalog, dump, hostile
from tests.snapshot_files import assert_snapshot

LOCALES = ("en", "es")
ROOT = "notifications-chat"
ROUTING_KEY = "R" * 32


def events(locale: str) -> dict:
    return {**catalog(locale), **hostile(locale)}  # type: ignore[arg-type]


def pages(locale: str) -> dict:
    out = {}
    for code, n in events(locale).items():
        decided = pagerduty.plan(n)
        out[code] = None if decided is None else pagerduty.render(n, ROUTING_KEY, decided)
    return out


@pytest.mark.parametrize("locale", LOCALES)
def test_mattermost(locale: str) -> None:
    payloads = {code: mattermost.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/mattermost.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_matrix(locale: str) -> None:
    payloads = {code: matrix.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/matrix.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_lark(locale: str) -> None:
    payloads = {code: lark.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/lark.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_pagerduty(locale: str) -> None:
    assert_snapshot(f"{ROOT}/pagerduty.{locale}.json", dump(pages(locale)))


@pytest.mark.parametrize("locale", LOCALES)
class TestLimits:
    def test_a_mattermost_message_stays_inside_its_budget(self, locale: str) -> None:
        for code, n in events(locale).items():
            body = mattermost.render(n)

            assert len(json.dumps(body)) <= mattermost.BODY_BUDGET + 2_000, code
            assert len(body["attachments"][0]["text"]) <= mattermost.TEXT_LIMIT, code

    def test_a_matrix_event_stays_far_under_the_protocol_limit(self, locale: str) -> None:
        for code, n in events(locale).items():
            body = matrix.render(n)

            assert len(json.dumps(body).encode("utf-8")) < 40_000, code

    def test_a_lark_card_stays_inside_its_size(self, locale: str) -> None:
        for code, n in events(locale).items():
            assert len(json.dumps(lark.render(n))) < 28_000, code

    def test_a_pagerduty_summary_and_key_stay_inside_theirs(self, locale: str) -> None:
        for code, event in pages(locale).items():
            if event is None:
                continue
            assert len(event["dedup_key"]) <= 255, code
            assert len(event.get("payload", {}).get("summary", "")) <= 1024, code


@pytest.mark.parametrize("locale", LOCALES)
class TestTheLinkAndTheState:
    def test_the_console_link_is_where_each_channel_puts_it(self, locale: str) -> None:
        n = catalog(locale)["deploy.failed"]  # type: ignore[arg-type]
        url = n.console_link.url if n.console_link else ""
        assert url

        assert mattermost.render(n)["attachments"][0]["title_link"] == url
        assert f'href="{url}"' in matrix.render(n)["formatted_body"]
        assert lark.render(n)["card"]["elements"][-1]["actions"][0]["url"] == url
        decided = pagerduty.plan(n)
        assert decided is not None
        assert pagerduty.render(n, ROUTING_KEY, decided)["links"][0]["href"] == url

    def test_the_strip_and_the_header_follow_the_state(self, locale: str) -> None:
        failed = catalog(locale)["deploy.failed"]  # type: ignore[arg-type]
        done = catalog(locale)["deploy.succeeded"]  # type: ignore[arg-type]

        assert (
            mattermost.render(failed)["attachments"][0]["color"]
            != mattermost.render(done)["attachments"][0]["color"]
        )
        assert lark.render(failed)["card"]["header"]["template"] == "red"
        assert lark.render(done)["card"]["header"]["template"] == "green"

    def test_matrix_sends_text_and_not_a_notice(self, locale: str) -> None:
        body = matrix.render(catalog(locale)["deploy.failed"])  # type: ignore[arg-type]

        assert body["msgtype"] == "m.text"
        assert body["format"] == "org.matrix.custom.html"
        assert body["m.mentions"] == {}


@pytest.mark.parametrize("locale", LOCALES)
class TestHostileText:
    """A branch name or a commit message is somebody else's text."""

    def test_mattermost_does_not_mention_anybody(self, locale: str) -> None:
        n = hostile(locale)["hostile.deploy_failed"]  # type: ignore[arg-type]
        text = json.dumps(mattermost.render(n), ensure_ascii=False)

        assert not re.search(r"(?<![\w.])@(?=\w)", text), "an @ starts a mention"

    def test_a_mattermost_excerpt_cannot_close_its_own_code_block(self, locale: str) -> None:
        n = hostile(locale)["hostile.deploy_failed"]  # type: ignore[arg-type]
        body = mattermost.render(n)["attachments"][0]["text"]

        fences = [line for line in body.split("\n") if line.strip().startswith("```")]
        assert len(fences) in (0, 2)

    def test_matrix_escapes_every_value_as_html(self, locale: str) -> None:
        n = hostile(locale)["hostile.deploy_failed"]  # type: ignore[arg-type]
        formatted = matrix.render(n)["formatted_body"]

        allowed = {"b", "i", "br", "code", "pre", "ul", "li", "a"}
        tags = set(re.findall(r"</?([a-zA-Z0-9]+)", formatted))
        assert tags <= allowed, tags - allowed
        assert "<script" not in formatted.lower()

    def test_matrix_does_not_leave_a_room_mention_in_the_text(self, locale: str) -> None:
        n = hostile(locale)["hostile.deploy_failed"]  # type: ignore[arg-type]
        body = matrix.render(n)

        for text in (body["body"], body["formatted_body"]):
            assert not re.search(r"@(?=[\w#!])", text)

    def test_a_lark_card_shows_every_value_as_plain_text(self, locale: str) -> None:
        n = hostile(locale)["hostile.deploy_failed"]  # type: ignore[arg-type]
        card = json.dumps(lark.render(n))

        assert "lark_md" not in card
        assert '"tag": "plain_text"' in card


class TestAnIncidentOpensAndCloses:
    def test_a_problem_triggers_and_its_recovery_resolves_the_same_incident(self) -> None:
        down = catalog("en")["app.unreachable"]  # type: ignore[arg-type]
        up = catalog("en")["app.recovered"]  # type: ignore[arg-type]

        opened, closed = pagerduty.plan(down), pagerduty.plan(up)

        assert opened is not None and closed is not None
        assert (opened.action, closed.action) == ("trigger", "resolve")
        assert opened.dedup_key == closed.dedup_key

    def test_two_rules_on_one_application_are_two_incidents(self) -> None:
        from noust.core.notifications.composers_ops import compose_alert_firing
        from tests.notifications_support import context

        def firing(rule: str):
            return compose_alert_firing(
                rule, "CPU", "95%", "90%", ">", 300, context("en"), domain="shop.example.com"
            )

        assert pagerduty.dedup_key(firing("cpu")) != pagerduty.dedup_key(firing("memory"))
        assert pagerduty.dedup_key(firing("cpu")) == pagerduty.dedup_key(firing("cpu"))

    def test_a_deploy_that_worked_resolves_the_one_that_failed(self) -> None:
        failed = pagerduty.plan(catalog("en")["deploy.failed"])  # type: ignore[arg-type]
        worked = pagerduty.plan(catalog("en")["deploy.succeeded"])  # type: ignore[arg-type]

        assert failed is not None and worked is not None
        assert (failed.action, worked.action) == ("trigger", "resolve")
        assert failed.dedup_key == worked.dedup_key

    def test_what_is_not_a_problem_and_closes_nothing_is_not_sent(self) -> None:
        started = catalog("en")["deploy.started"]  # type: ignore[arg-type]
        # An alert somebody took is the one information a pager does hear: it acknowledges.
        info = [
            n
            for n in catalog("en").values()  # type: ignore[attr-defined]
            if n.state is State.INFO and n.kind not in ("test", "alert_acknowledged")
        ]

        assert pagerduty.plan(started) is None
        assert all(pagerduty.plan(n) is None for n in info)

    def test_an_alert_somebody_took_is_the_acknowledgement_of_its_incident(self) -> None:
        taken = catalog("en")["alert.acknowledged"]  # type: ignore[arg-type]
        decided = pagerduty.plan(taken)

        assert decided is not None and decided.action == "acknowledge"

    def test_a_report_a_digest_and_an_approval_are_never_a_page(self) -> None:
        for code in ("report.observations", "digest.held", "approval.rejected"):
            n = catalog("en").get(code)  # type: ignore[arg-type]
            if n is not None:
                assert pagerduty.plan(n) is None, code

    def test_a_resolve_carries_only_what_closes_it(self) -> None:
        up = catalog("en")["app.recovered"]  # type: ignore[arg-type]
        decided = pagerduty.plan(up)
        assert decided is not None

        event = pagerduty.render(up, ROUTING_KEY, decided)

        assert set(event) == {"routing_key", "event_action", "dedup_key"}

    def test_a_failure_pages_as_critical_and_a_warning_as_a_warning(self) -> None:
        for code, severity in (("deploy.failed", "critical"), ("cert.expiring", "warning")):
            n = catalog("en").get(code)  # type: ignore[arg-type]
            if n is None:
                continue
            decided = pagerduty.plan(n)
            assert decided is not None
            assert pagerduty.render(n, ROUTING_KEY, decided)["payload"]["severity"] == severity

    def test_the_test_message_is_an_info_trigger(self) -> None:
        test = catalog("en")["test"]  # type: ignore[arg-type]
        decided = pagerduty.plan(test)
        assert decided is not None and decided.action == "trigger"

        event = pagerduty.render(test, ROUTING_KEY, decided)

        assert event["payload"]["severity"] == "info"

    def test_the_acknowledge_event_names_the_incident(self) -> None:
        assert pagerduty.acknowledge(ROUTING_KEY, "k") == {
            "routing_key": ROUTING_KEY,
            "event_action": "acknowledge",
            "dedup_key": "k",
        }

    def test_a_very_long_key_is_shortened_to_what_pagerduty_takes(self) -> None:
        n = hostile("en")["hostile.huge_subject"]  # type: ignore[arg-type]

        assert len(pagerduty.dedup_key(n)) <= 255
