# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Email through a provider's HTTP API (checklist K-06): Resend, Postmark, Brevo and Amazon SES.

The request each provider is sent, what the notifier does when one refuses or
is down, and that an API provider replaces SMTP instead of adding to it. The
suite never opens a socket: the opener is injected and DNS is replaced.
"""

from __future__ import annotations

import base64
import email as email_lib
import io
import json
from datetime import datetime, timezone
from email import policy
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import noust.core.notifier as notifier_module
from noust.core import email_api
from noust.core.config import Config
from noust.core.exceptions import ConfigError, EmailError, EmailRefused
from noust.core.notifications.model import Notification, State
from noust.core.notifier import Notifier, is_transient
from noust.monitor.email_notifier import EmailNotifier, SMTPConfig
from tests.test_notifier import config  # noqa: F401  (pytest resolves fixtures by name)

# ruff: noqa: F811

MOMENT = datetime(2026, 10, 16, 9, 30, tzinfo=timezone.utc)
SES = {
    "provider": "ses",
    "region": "eu-west-1",
    "access_key_id": "AKIAIOSFODNN7EXAMPLE",
    "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "from_address": "alerts@example.com",
}
KEYED = {"api_key": "re_live_secret_key_123", "from_address": "alerts@example.com"}


def mail(**overrides: Any) -> email_api.ApiMail:
    fields: dict[str, Any] = {
        "subject": "Deploy failed",
        "text": "plain",
        "html": "<p>html</p>",
        "from_address": "alerts@example.com",
        "sender_name": "Noust (web-1)",
        "recipients": ("ops@example.com", "dev@example.com"),
        "message_id": "abc-123",
        "headers": {"Auto-Submitted": "auto-generated"},
        "raw": b"From: a\r\n\r\nraw message",
    }
    fields.update(overrides)
    return email_api.ApiMail(**fields)


def body_of(request: Request) -> dict[str, Any]:
    return json.loads(request.data)  # type: ignore[arg-type]


class TestTheRequestEachProviderIsSent:
    def test_resend(self) -> None:
        request = email_api.request_for("resend", mail(), {"provider": "resend", **KEYED})

        assert request.full_url == "https://api.resend.com/emails"
        assert request.get_header("Authorization") == "Bearer re_live_secret_key_123"
        assert request.get_header("Idempotency-key") == "abc-123"
        body = body_of(request)
        assert body["from"] == '"Noust (web-1)" <alerts@example.com>'
        assert body["to"] == ["ops@example.com", "dev@example.com"]
        assert body["subject"] == "Deploy failed" and body["html"] == "<p>html</p>"
        assert body["headers"] == {"Auto-Submitted": "auto-generated"}

    def test_postmark(self) -> None:
        request = email_api.request_for("postmark", mail(), {"provider": "postmark", **KEYED})

        assert request.full_url == "https://api.postmarkapp.com/email"
        assert request.get_header("X-postmark-server-token") == "re_live_secret_key_123"
        body = body_of(request)
        assert body["To"] == "ops@example.com, dev@example.com"
        assert body["HtmlBody"] == "<p>html</p>" and body["TextBody"] == "plain"
        assert body["MessageStream"] == "outbound"
        assert body["Headers"] == [{"Name": "Auto-Submitted", "Value": "auto-generated"}]

    def test_brevo(self) -> None:
        request = email_api.request_for("brevo", mail(), {"provider": "brevo", **KEYED})

        assert request.full_url == "https://api.brevo.com/v3/smtp/email"
        assert request.get_header("Api-key") == "re_live_secret_key_123"
        body = body_of(request)
        assert body["sender"] == {"email": "alerts@example.com", "name": "Noust (web-1)"}
        assert body["to"] == [{"email": "ops@example.com"}, {"email": "dev@example.com"}]
        assert body["htmlContent"] == "<p>html</p>" and body["textContent"] == "plain"

    def test_amazon_ses_sends_the_whole_message_signed(self) -> None:
        request = email_api.request_for("ses", mail(), SES, moment=MOMENT)

        assert request.full_url == "https://email.eu-west-1.amazonaws.com/v2/email/outbound-emails"
        body = body_of(request)
        assert base64.b64decode(body["Content"]["Raw"]["Data"]) == b"From: a\r\n\r\nraw message"
        assert body["Destination"] == {"ToAddresses": ["ops@example.com", "dev@example.com"]}
        authorization = request.get_header("Authorization") or ""
        assert authorization.startswith(
            "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20261016/eu-west-1/ses/aws4_request"
        )
        assert request.get_header("X-amz-date") == "20261016T093000Z"
        assert SES["secret_access_key"] not in authorization

    def test_no_credential_is_ever_part_of_a_url(self) -> None:
        for provider, settings in (
            ("resend", KEYED),
            ("postmark", KEYED),
            ("brevo", KEYED),
            ("ses", SES),
        ):
            request = email_api.request_for(provider, mail(), settings, moment=MOMENT)
            assert "secret" not in request.full_url and "re_live" not in request.full_url

    def test_a_region_cannot_point_the_credentials_at_another_host(self) -> None:
        for region in (
            "x.evil.test/",
            "us-east-1.evil.test#",
            "us-east-1\nHost: evil",
            "cn-north-1",
        ):
            with pytest.raises(ValueError, match="region"):
                email_api.request_for("ses", mail(), {**SES, "region": region}, moment=MOMENT)

    def test_what_a_provider_needs_must_be_set(self) -> None:
        assert email_api.missing("resend", {"provider": "resend"}) == ["from_address", "api_key"]
        assert email_api.missing("ses", {"from_address": "a@b.co"}) == [
            "region",
            "access_key_id",
            "secret_access_key",
        ]
        with pytest.raises(ValueError, match="api_key"):
            email_api.request_for("resend", mail(), {"from_address": "a@b.co"})

    def test_nobody_to_send_to_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="nobody"):
            email_api.request_for("resend", mail(recipients=()), KEYED)

    def test_a_provider_takes_a_bounded_number_of_recipients(self) -> None:
        many = tuple(f"u{i}@example.com" for i in range(email_api.MAX_RECIPIENTS + 1))

        with pytest.raises(ValueError, match="recipients"):
            email_api.request_for("resend", mail(recipients=many), KEYED)

    def test_a_key_with_a_space_is_refused(self) -> None:
        with pytest.raises(ValueError, match="spaces"):
            email_api.request_for("resend", mail(), {**KEYED, "api_key": "re_ a b"})

    def test_an_access_key_id_is_checked_for_its_shape(self) -> None:
        with pytest.raises(ValueError, match="access key id"):
            email_api.request_for(
                "ses", mail(), {**SES, "access_key_id": "not a key"}, moment=MOMENT
            )

    def test_the_provider_name_is_checked_when_it_is_written(self) -> None:
        assert email_api.validate_provider(" Resend ") == "resend"
        assert email_api.validate_provider("") == ""
        with pytest.raises(ValueError, match="provider"):
            email_api.validate_provider("mailgun")


class Opener:
    def __init__(self) -> None:
        self.requests: list[Request] = []
        self.error: Exception | None = None

    def __call__(self, request: Request, timeout: float | None = None) -> io.BytesIO:
        if self.error is not None:
            raise self.error
        self.requests.append(request)
        return io.BytesIO(b'{"id": "msg_1"}')


def note(state: State = State.FAILED) -> Notification:
    return Notification(
        kind="deploy_failed",
        code="deploy.failed",
        state=state,
        locale="en",
        title="Deploy failed",
        subject="shop.example.com",
        summary="It stopped at an error.",
        server="web-1",
        domain="shop.example.com",
    )


def api_notifier(settings: dict[str, Any], opener: Opener | None = None) -> EmailNotifier:
    return EmailNotifier(
        smtp_config=SMTPConfig(host="smtp.example.com", port=465, use_ssl=True),
        recipients=["ops@example.com"],
        api_settings=settings,
        opener=opener or Opener(),
    )


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("93.184.216.34",))


class TestTheNotifierSendsThroughTheProvider:
    def test_a_configured_provider_is_configured_and_smtp_is_not_touched(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opener = Opener()
        sender = api_notifier({"provider": "resend", **KEYED}, opener)

        def smtp_must_not_be_used(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("an API provider replaces SMTP")

        monkeypatch.setattr("smtplib.SMTP_SSL", smtp_must_not_be_used)
        monkeypatch.setattr("smtplib.SMTP", smtp_must_not_be_used)

        assert sender.is_configured
        assert sender.send_notification(note()) is True
        assert opener.requests[0].full_url == "https://api.resend.com/emails"

    def test_without_the_providers_credentials_it_is_not_configured(self, config: Config) -> None:
        assert not api_notifier({"provider": "resend", "from_address": "a@b.co"}).is_configured

    def test_without_a_provider_the_mail_goes_by_smtp_as_always(self, config: Config) -> None:
        sender = api_notifier({})

        assert sender.provider == "" and sender.is_configured

    def test_a_provider_that_is_not_one_falls_back_to_smtp(self, config: Config) -> None:
        assert api_notifier({"provider": "mailgun", **KEYED}).provider == ""

    def test_the_json_providers_get_the_word_for_the_wordmark_not_a_dead_image(
        self, config: Config
    ) -> None:
        for provider in ("resend", "postmark", "brevo"):
            opener = Opener()
            api_notifier({"provider": provider, **KEYED}, opener).send_notification(note())

            assert "cid:" not in opener.requests[0].data.decode()  # type: ignore[union-attr]

    def test_ses_carries_the_wordmark_in_the_message_it_sends(self, config: Config) -> None:
        opener = Opener()
        api_notifier(SES, opener).send_notification(note())

        raw = base64.b64decode(json.loads(opener.requests[0].data)["Content"]["Raw"]["Data"])  # type: ignore[arg-type]
        parsed = email_lib.message_from_bytes(raw, policy=policy.default)
        assert parsed["Subject"] and parsed["To"] == "ops@example.com"
        assert parsed["From"].endswith("<alerts@example.com>")
        assert any(part.get_content_type() == "image/png" for part in parsed.walk())

    def test_a_refusal_is_final_and_says_what_the_provider_said(self, config: Config) -> None:
        opener = Opener()
        opener.error = HTTPError(
            "https://api.resend.com/emails",
            403,
            "Forbidden",
            {},  # type: ignore[arg-type]
            io.BytesIO(b'{"message":"The re_live_secret_key_123 domain is not verified"}'),
        )

        with pytest.raises(EmailRefused) as raised:
            api_notifier({"provider": "resend", **KEYED}, opener).send_notification(note())

        assert "403" in str(raised.value) and "not verified" in str(raised.value)
        assert "re_live_secret_key_123" not in str(raised.value)
        assert is_transient(raised.value) is False

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_a_provider_having_a_bad_moment_is_tried_again(
        self, config: Config, status: int
    ) -> None:
        opener = Opener()
        opener.error = HTTPError(
            "https://api.resend.com/emails",
            status,
            "Busy",
            {},  # type: ignore[arg-type]
            io.BytesIO(b"later"),
        )

        with pytest.raises(EmailError) as raised:
            api_notifier({"provider": "resend", **KEYED}, opener).send_notification(note())

        assert not isinstance(raised.value, EmailRefused)
        assert is_transient(raised.value) is True

    def test_an_unreachable_provider_is_tried_again(self, config: Config) -> None:
        opener = Opener()
        opener.error = URLError("name resolution failed")

        with pytest.raises(EmailError) as raised:
            api_notifier({"provider": "brevo", **KEYED}, opener).send_notification(note())

        assert is_transient(raised.value) is True

    def test_the_destination_guard_still_applies(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(notifier_module, "_resolve_host", lambda host: ("10.0.0.5",))
        opener = Opener()

        with pytest.raises(EmailRefused):
            api_notifier({"provider": "resend", **KEYED}, opener).send_notification(note())

        assert opener.requests == []

    def test_the_email_channel_of_the_notifier_uses_it(self, config: Config) -> None:
        opener = Opener()
        # Built first: an EmailNotifier reloads the shared configuration from disk.
        email = api_notifier({"provider": "postmark", **KEYED}, opener)
        config.set("notifications.enabled", True)
        config.set("notifications.channels.email", {"enabled": True})
        sender = Notifier(config, email_notifier=email, record=False)

        sender.notify(note())

        assert opener.requests[0].full_url == "https://api.postmarkapp.com/email"


class TestTheSettings:
    def test_the_provider_is_checked_when_it_is_written(self, config: Config) -> None:
        with pytest.raises(ConfigError, match="provider"):
            config.set("monitor.email_api.provider", "mailgun")

        config.set("monitor.email_api.provider", "Resend")

        assert config.get("monitor.email_api.provider") == "resend"

    def test_the_region_is_checked_when_it_is_written(self, config: Config) -> None:
        with pytest.raises(ConfigError, match="region"):
            config.set("monitor.email_api.region", "somewhere.evil.test")

    def test_the_sender_must_be_an_address(self, config: Config) -> None:
        with pytest.raises(ConfigError, match="valid email"):
            config.set("monitor.email_api.from_address", "not an address")

    def test_every_credential_is_redacted_when_the_configuration_is_shown(
        self, config: Config
    ) -> None:
        from noust.core.config import redact_secrets

        config.set("monitor.email_api.api_key", "re_live_secret_key_123")
        config.set("monitor.email_api.secret_access_key", "s3cr3t-s3cr3t-s3cr3t")
        config.set("monitor.email_api.access_key_id", "AKIAIOSFODNN7EXAMPLE")

        shown = redact_secrets(config.to_dict())["monitor"]["email_api"]

        assert shown["api_key"] == shown["secret_access_key"] == shown["access_key_id"] == "***"
        assert shown["provider"] == ""
