# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Snapshots of every notification, on every channel, in both languages.

What a message *says and looks like* is a decision, and a decision is best
reviewed as a diff. Each channel and language has one file under
``tests/snapshots/notifications`` holding the payload of every event of
``tests/notifications_support.py`` (plus the hostile variants); the email's HTML
has a file per event so it opens in a browser. When a text changes on purpose:

    NOUST_UPDATE_SNAPSHOTS=1 pytest tests/test_notification_snapshots.py

and read the diff. A snapshot nobody generates any more is a failure too, so the
directory never keeps stale files.
"""

from __future__ import annotations

import pytest

from noust.core.notifications.render import discord, email, slack, telegram, webhook
from tests.notifications_support import catalog, dump, hostile
from tests.snapshot_files import SNAPSHOT_ROOT, assert_snapshot, updating

LOCALES = ("en", "es")
CHAT_ID = "-1001234567890"
ROOT = "notifications"


def events(locale: str) -> dict:
    return {**catalog(locale), **hostile(locale)}  # type: ignore[arg-type]


def _email_text(rendered: email.RenderedEmail) -> str:
    headers = "\n".join(f"{name}: {value}" for name, value in rendered.headers.items())
    return f"Subject: {rendered.subject}\n{headers}\n\n{rendered.text}"


@pytest.mark.parametrize("locale", LOCALES)
def test_telegram(locale: str) -> None:
    payloads = {code: telegram.render(n, CHAT_ID) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/telegram.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_telegram_plain_retry(locale: str) -> None:
    payloads = {
        code: telegram.render(n, CHAT_ID, plain=True)
        for code, n in catalog(locale).items()  # type: ignore[arg-type]
        if code in ("deploy.rolled_back", "cert.expired", "test")
    }

    assert_snapshot(f"{ROOT}/telegram-plain.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_slack(locale: str) -> None:
    payloads = {code: slack.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/slack.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_discord(locale: str) -> None:
    payloads = {code: discord.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/discord.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_webhook(locale: str) -> None:
    payloads = {code: webhook.render(n) for code, n in events(locale).items()}

    assert_snapshot(f"{ROOT}/webhook.{locale}.json", dump(payloads))


@pytest.mark.parametrize("locale", LOCALES)
def test_email_text_and_headers(locale: str) -> None:
    rule = "\n" + "=" * 72 + "\n"
    blocks = [f"### {code}\n{_email_text(email.render(n))}" for code, n in events(locale).items()]

    assert_snapshot(f"{ROOT}/email.{locale}.txt", rule.join(blocks))


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("code", list(catalog("en")) + list(hostile("en")))
def test_email_html(locale: str, code: str) -> None:
    rendered = email.render(events(locale)[code])

    assert_snapshot(f"{ROOT}/email-html/{locale}/{code}.html", rendered.html)


def test_no_snapshot_is_left_over() -> None:
    if updating():
        pytest.skip("regenerating")
    expected = {
        f"{ROOT}/{channel}.{locale}.json"
        for locale in LOCALES
        for channel in ("telegram", "telegram-plain", "slack", "discord", "webhook")
    }
    expected |= {f"{ROOT}/email.{locale}.txt" for locale in LOCALES}
    expected |= {
        f"{ROOT}/email-html/{locale}/{code}.html"
        for locale in LOCALES
        for code in list(catalog("en")) + list(hostile("en"))
    }
    present = {
        path.relative_to(SNAPSHOT_ROOT).as_posix()
        for path in (SNAPSHOT_ROOT / ROOT).rglob("*")
        if path.is_file()
    }

    assert sorted(present - expected) == []
