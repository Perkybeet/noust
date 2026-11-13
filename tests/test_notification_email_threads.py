# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Mail that groups: a stable subject for each thing, and the headers a mail client threads by.

Item 190. A recovery used to be a different conversation from its failure because the subject
carried the state (``Deploy failed``, then ``Deployed``): Gmail breaks a conversation off when the
subject changes. Now every message about one thing has the same subject, and the later ones carry
``In-Reply-To`` and ``References`` pointing at the first, so a client that threads by headers (and
one that threads by subject) shows one conversation.

Nothing here sends mail: the SMTP message is built and read, and a provider's request is captured.
"""

from __future__ import annotations

import base64
import email as email_lib
import io
import json
from collections.abc import Iterator
from dataclasses import replace
from email import policy
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core.config import Config
from noust.core.notification_ledger import DeliveryLedger
from noust.core.notification_state import NotificationState
from noust.core.notifications import grouping
from noust.core.notifications.model import EVENT_KINDS, State
from noust.core.notifications.render import email as render_email
from noust.core.notifier import Notifier
from noust.core.store import NoustStore
from noust.monitor.email_notifier import EmailContent, EmailNotifier, SMTPConfig
from tests.support.telegram_double import Clock, note

FAILED = ("app_unreachable", State.FAILED)
FIXED = ("app_recovered", State.OK)


class TestTheSubject:
    def test_a_failure_and_its_recovery_have_the_same_subject(self) -> None:
        failed = render_email.subject_line(note(*FAILED, title="Unreachable"))
        fixed = render_email.subject_line(note(*FIXED, title="Recovered"))

        assert failed == fixed == "[Noust] Application: shop.example.com (web-1)"

    def test_a_deploy_has_one_subject_from_its_start_to_its_end(self) -> None:
        subjects = {
            render_email.subject_line(note(kind, state, title=title))
            for kind, state, title in (
                ("deploy_started", State.PROGRESS, "Deploying"),
                ("deploy_failed", State.FAILED, "Deploy failed"),
                ("deploy_rolled_back", State.WARNING, "Rolled back"),
                ("deploy_success", State.OK, "Deployed"),
            )
        }

        assert subjects == {"[Noust] Deploy: shop.example.com (web-1)"}

    def test_in_spanish_the_noun_is_spanish(self) -> None:
        spanish = replace(note(*FAILED), locale="es")

        assert render_email.subject_line(spanish) == "[Noust] Aplicación: shop.example.com (web-1)"

    def test_what_has_no_way_back_keeps_the_subject_that_says_how_it_went(self) -> None:
        subject = render_email.subject_line(note("cert_expiring", State.WARNING, title="Expiring"))

        assert subject == "[Noust] Expiring: shop.example.com (web-1)"

    def test_a_thing_without_a_subject_is_named_by_its_noun(self) -> None:
        bare = note("server_back", State.OK, domain=None, title="Back")

        assert render_email.subject_line(bare) == "[Noust] Server (web-1)"

    def test_every_family_has_a_noun_in_both_languages(self) -> None:
        for family in set(grouping.FAMILY.values()):
            for locale in ("en", "es"):
                kind = next(k for k, f in grouping.FAMILY.items() if f == family)
                subject = render_email.subject_line(
                    replace(note(kind, State.FAILED), locale=locale)  # type: ignore[arg-type]
                )
                assert subject.startswith("[Noust] ") and not subject.startswith("[Noust] thing"), (
                    family,
                    subject,
                )

    def test_two_things_are_two_subjects(self) -> None:
        assert render_email.subject_line(note(*FAILED)) != render_email.subject_line(
            note(*FAILED, "blog.example.com")
        )

    def test_every_event_still_has_a_subject_line_of_bounded_length(self) -> None:
        for kind in EVENT_KINDS:
            line = render_email.subject_line(note(kind, State.FAILED, "x" * 500))
            assert line.startswith("[Noust]") and len(line) <= 200

    def test_the_state_is_still_in_the_body(self) -> None:
        text = render_email.render(note(*FIXED, title="Recovered")).text

        assert "Recovered" in text


class TestTheHeaders:
    def sender(self) -> EmailNotifier:
        return EmailNotifier(
            smtp_config=SMTPConfig(
                host="smtp.example.com", port=465, use_ssl=True, from_address="noust@example.org"
            ),
            recipients=["ops@example.com"],
        )

    def test_a_first_message_answers_nothing(self) -> None:
        content = EmailNotifier.content_for(note(*FAILED))

        mail = self.sender()._build_message(content)

        assert mail["In-Reply-To"] is None and mail["References"] is None

    def test_a_later_message_answers_the_first_in_the_header_clients_thread_by(self) -> None:
        content = EmailNotifier.content_for(note(*FIXED), in_reply_to="root-id-1")

        mail = self.sender()._build_message(content)

        assert mail["In-Reply-To"] == "<root-id-1@example.org>"
        assert mail["References"] == "<root-id-1@example.org>"

    def test_the_root_is_named_as_the_first_message_was_sent(self) -> None:
        first = note(*FAILED)
        first_mail = self.sender()._build_message(EmailNotifier.content_for(first))
        answer = self.sender()._build_message(
            EmailNotifier.content_for(note(*FIXED), in_reply_to=first.id)
        )

        assert answer["In-Reply-To"] == first_mail["Message-ID"]

    def test_the_content_says_what_it_answers(self) -> None:
        assert EmailNotifier.content_for(note(*FIXED), in_reply_to="x").in_reply_to == "x"
        assert EmailNotifier.content_for(note(*FIXED)).in_reply_to is None
        assert EmailContent(subject="s", text="t", html="h").in_reply_to is None

    def test_the_headers_a_notification_always_had_are_still_there(self) -> None:
        mail = self.sender()._build_message(
            EmailNotifier.content_for(note(*FIXED), in_reply_to="x")
        )

        assert mail["Auto-Submitted"] == "auto-generated"
        assert mail["X-Noust-Event"] == "app_recovered"


class Opener:
    """Stands in for the opener of a provider's API: records what it is sent."""

    def __init__(self) -> None:
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        self.requests.append(request)
        return io.BytesIO(b"{}")


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


def provider(settings: dict[str, Any]) -> tuple[EmailNotifier, Opener]:
    opener = Opener()
    notifier = EmailNotifier(
        smtp_config=SMTPConfig(host="smtp.example.com", port=465, use_ssl=True),
        recipients=["ops@example.com"],
        api_settings=settings,
        opener=opener,
    )
    return notifier, opener


KEYED = {"api_key": "re_live_secret_key_123", "from_address": "alerts@example.org"}
SES = {
    "provider": "ses",
    "region": "eu-west-1",
    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "from_address": "alerts@example.org",
}


class TestThroughAProvider:
    def test_resend_carries_the_headers(self, config: Config) -> None:
        sender, opener = provider({"provider": "resend", **KEYED})

        sender.send_notification(note(*FIXED), in_reply_to="root-1")

        headers = json.loads(opener.requests[0].data)["headers"]  # type: ignore[arg-type]
        assert headers["In-Reply-To"] == headers["References"] == "<root-1@example.org>"

    def test_postmark_carries_the_headers(self, config: Config) -> None:
        sender, opener = provider({"provider": "postmark", **KEYED})

        sender.send_notification(note(*FIXED), in_reply_to="root-1")

        sent = json.loads(opener.requests[0].data)["Headers"]  # type: ignore[arg-type]
        assert {"Name": "In-Reply-To", "Value": "<root-1@example.org>"} in sent
        assert {"Name": "References", "Value": "<root-1@example.org>"} in sent

    def test_brevo_carries_the_headers(self, config: Config) -> None:
        sender, opener = provider({"provider": "brevo", **KEYED})

        sender.send_notification(note(*FIXED), in_reply_to="root-1")

        headers = json.loads(opener.requests[0].data)["headers"]  # type: ignore[arg-type]
        assert headers["In-Reply-To"] == "<root-1@example.org>"

    def test_ses_carries_them_in_the_message_it_sends(self, config: Config) -> None:
        sender, opener = provider(SES)

        sender.send_notification(note(*FIXED), in_reply_to="root-1")

        raw = base64.b64decode(json.loads(opener.requests[0].data)["Content"]["Raw"]["Data"])  # type: ignore[arg-type]
        parsed = email_lib.message_from_bytes(raw, policy=policy.default)
        assert parsed["In-Reply-To"] == "<root-1@example.org>"
        assert parsed["References"] == "<root-1@example.org>"

    def test_a_first_message_through_a_provider_has_none(self, config: Config) -> None:
        sender, opener = provider({"provider": "resend", **KEYED})

        sender.send_notification(note(*FAILED))

        headers = json.loads(opener.requests[0].data)["headers"]  # type: ignore[arg-type]
        assert "In-Reply-To" not in headers and "References" not in headers


class FakeEmail:
    """Records what would have been mailed, instead of opening SMTP."""

    is_configured = True

    def __init__(self) -> None:
        self.sent: list[tuple[Any, str | None]] = []

    def send_notification(self, notification: Any, in_reply_to: str | None = None) -> bool:
        self.sent.append((notification, in_reply_to))
        return True


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    try:
        cfg = Config()
        cfg.set("notifications.enabled", True)
        cfg.set("notifications.channels.email.enabled", True)
        cfg.set("monitor.smtp.host", "smtp.example.com")
        yield cfg
    finally:
        Config.reset_instance()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ledger(tmp_path: Path, clock: Clock) -> Iterator[DeliveryLedger]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    yield DeliveryLedger(store, clock=clock.epoch)
    NoustStore.reset_instance()


def mailer(
    config: Config, fake: FakeEmail, ledger: DeliveryLedger | None, clock: Clock
) -> Notifier:
    return Notifier(
        config,
        opener=Opener(),
        email_notifier=fake,  # type: ignore[arg-type]
        ledger=ledger,
        record=ledger is not None,
        clock=clock,
        placement=lambda _domain: None,
    )


class TestTheChannel:
    def test_a_recovery_is_mailed_as_an_answer_to_the_failure(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        fake = FakeEmail()
        sender = mailer(config, fake, ledger, clock)
        failure = note(*FAILED)

        sender.notify(failure)
        sender.notify(note(*FIXED))

        assert [reply for _, reply in fake.sent] == [None, failure.id]

    def test_after_it_is_fine_again_the_next_problem_starts_a_new_conversation(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        fake = FakeEmail()
        sender = mailer(config, fake, ledger, clock)
        first, second = note(*FAILED), note(*FAILED)
        sender.notify(first)
        sender.notify(note(*FIXED))

        sender.notify(second)
        sender.notify(note(*FIXED))

        assert [reply for _, reply in fake.sent] == [None, first.id, None, second.id]

    def test_what_has_no_way_back_is_never_threaded(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        fake = FakeEmail()
        sender = mailer(config, fake, ledger, clock)

        sender.notify(note("cert_expiring", State.WARNING))
        sender.notify(note("cert_expiring", State.WARNING))

        assert [reply for _, reply in fake.sent] == [None, None]

    def test_without_a_record_nothing_is_threaded_and_nothing_fails(
        self, config: Config, clock: Clock
    ) -> None:
        fake = FakeEmail()
        sender = mailer(config, fake, None, clock)

        sender.notify(note(*FAILED))
        sender.notify(note(*FIXED))

        assert [reply for _, reply in fake.sent] == [None, None]

    def test_the_memory_holds_no_address(
        self, config: Config, ledger: DeliveryLedger, clock: Clock
    ) -> None:
        fake = FakeEmail()
        sender = mailer(config, fake, ledger, clock)

        sender.notify(note(*FAILED))

        kept = json.dumps(NotificationState(ledger.store).items("threads:email"))
        assert "@" not in kept
