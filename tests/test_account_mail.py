# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The mail an account gets: an invitation to the console, and a link to choose a new password."""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.accounts.mail import TEXTS, AccountMailer, MailUnavailable
from noust.core.exceptions import EmailError


class FakeNotifier:
    """Stands in for the email transport; remembers what it was asked to send."""

    sent: list[dict[str, Any]] = []

    def __init__(self, recipients: list[str] | None = None, **_: Any) -> None:
        self.recipients = recipients or []
        self.has_transport = FakeNotifier.transport
        self.fail = False

    transport = True

    def send_message(self, subject: str, text: str, html: str, **_: Any) -> bool:
        if FakeNotifier.failing:
            raise EmailError("The mail server said no", details="550 mailbox unavailable")
        FakeNotifier.sent.append(
            {"to": list(self.recipients), "subject": subject, "text": text, "html": html}
        )
        return True

    failing = False


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeNotifier.sent = []
    FakeNotifier.transport = True
    FakeNotifier.failing = False


def _mailer(url: str | None = "https://noust.example.com", language: str = "en") -> AccountMailer:
    settings = {"web.public_url": url or "", "notifications.language": language}
    return AccountMailer(lambda key, default=None: settings.get(key, default), FakeNotifier)  # type: ignore[arg-type]


class TestWhenItIsPossible:
    def test_it_needs_a_way_to_send_and_an_address_for_the_link(self) -> None:
        assert _mailer().ready() is True
        assert _mailer(url=None).ready() is False
        FakeNotifier.transport = False
        assert _mailer().ready() is False

    def test_sending_without_a_way_to_send_says_what_to_set(self) -> None:
        FakeNotifier.transport = False

        with pytest.raises(MailUnavailable, match=r"monitor\.smtp"):
            _mailer().send_invitation("a@example.com", "ana", "noust_inv_x", 24)

    def test_sending_without_a_public_address_says_what_to_set(self) -> None:
        with pytest.raises(MailUnavailable, match=r"web\.public_url"):
            _mailer(url=None).send_reset("a@example.com", "ana", "noust_reset_x")


class TestTheInvitation:
    def test_it_goes_to_the_address_with_a_link_the_console_opens(self) -> None:
        _mailer().send_invitation("ana@example.com", "ana", "noust_inv_abc", 24, invited_by="root")

        (mail,) = FakeNotifier.sent
        assert mail["to"] == ["ana@example.com"]
        assert "https://noust.example.com/invite#code=noust_inv_abc" in mail["text"]
        assert "https://noust.example.com/invite#code=noust_inv_abc" in mail["html"]
        assert "ana" in mail["text"] and "24" in mail["text"] and "root" in mail["text"]

    def test_the_code_rides_in_the_fragment_and_never_in_the_query(self) -> None:
        _mailer().send_invitation("ana@example.com", "ana", "noust_inv_abc", 24)

        (mail,) = FakeNotifier.sent
        assert "?code=" not in mail["text"]
        assert "invite?" not in mail["text"]

    def test_a_trailing_slash_on_the_public_address_is_not_doubled(self) -> None:
        _mailer(url="https://noust.example.com/").send_invitation(
            "ana@example.com", "ana", "noust_inv_abc", 24
        )

        assert "https://noust.example.com/invite#code=" in FakeNotifier.sent[0]["text"]
        assert "//invite" not in FakeNotifier.sent[0]["text"]

    def test_it_is_written_in_the_configured_language(self) -> None:
        _mailer(language="es").send_invitation("ana@example.com", "ana", "noust_inv_abc", 24)

        assert "invit" in FakeNotifier.sent[0]["subject"].lower()
        assert "Noust" in FakeNotifier.sent[0]["subject"]
        assert FakeNotifier.sent[0]["subject"] != _subject_in("en")

    def test_a_name_that_is_markup_cannot_inject_it(self) -> None:
        _mailer().send_invitation(
            "ana@example.com", "ana", "noust_inv_abc", 24, invited_by="<script>x</script>"
        )

        assert "<script>" not in FakeNotifier.sent[0]["html"]


def _subject_in(language: str) -> str:
    FakeNotifier.sent = []
    _mailer(language=language).send_invitation("ana@example.com", "ana", "noust_inv_abc", 24)
    return FakeNotifier.sent[0]["subject"]


class TestTheReset:
    def test_it_carries_the_link_and_says_what_to_do_if_it_was_not_asked_for(self) -> None:
        _mailer().send_reset("ana@example.com", "ana", "noust_reset_abc")

        (mail,) = FakeNotifier.sent
        assert "https://noust.example.com/reset-password#code=noust_reset_abc" in mail["text"]
        assert "60" in mail["text"]
        assert "ignore" in mail["text"].lower()

    def test_a_refusal_by_the_mail_server_is_not_hidden(self) -> None:
        FakeNotifier.failing = True

        with pytest.raises(EmailError, match="mail server"):
            _mailer().send_reset("ana@example.com", "ana", "noust_reset_abc")


class TestTheTexts:
    def test_both_languages_say_the_same_things(self) -> None:
        keys = {language: set(items) for language, items in TEXTS.items()}
        assert keys["en"] == keys["es"]

    def test_both_languages_use_the_same_placeholders(self) -> None:
        import string

        for key in TEXTS["en"]:
            names = {
                language: {
                    field
                    for _, field, _, _ in string.Formatter().parse(TEXTS[language][key])
                    if field
                }
                for language in TEXTS
            }
            assert names["en"] == names["es"], key
