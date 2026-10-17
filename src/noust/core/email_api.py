# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Email through a provider's HTTP API instead of an SMTP relay (checklist K-06).

Plenty of servers cannot speak SMTP to the outside (providers block 25, 465 and
587 on new accounts) and plenty of operators already have a Resend, Postmark,
Brevo or Amazon SES account whose API takes a message over HTTPS. This module
only *builds* the request, like :mod:`noust.core.notifier_requests` does for the
push services, and the one place that sends HTTP (``Notifier._send``'s guard and
its redirect rule) is where it is sent.

An API provider replaces SMTP, it is never added to it (one switch,
``monitor.email_api.provider``): two ways to send the same alert would be one
way to send it twice.

The endpoints are fixed. The only value an operator types that reaches a URL is
Amazon SES's region, which is checked against the shape of a region name and
put into ``email.<region>.amazonaws.com``, so no setting can send a credential
to a host the provider does not run.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import formataddr
from typing import Any
from urllib.request import Request

from noust.core import aws_sigv4
from noust.core.notifier_requests import json_request, post

#: The providers, in the order a form offers them.
PROVIDERS: tuple[str, ...] = ("resend", "postmark", "brevo", "ses")

#: What each is called to a person.
PROVIDER_LABELS: dict[str, str] = {
    "resend": "Resend",
    "postmark": "Postmark",
    "brevo": "Brevo",
    "ses": "Amazon SES",
}

RESEND_URL = "https://api.resend.com/emails"
POSTMARK_URL = "https://api.postmarkapp.com/email"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
SES_URL = "https://email.{region}.amazonaws.com/v2/email/outbound-emails"

#: ``us-east-1``, ``eu-central-1``, ``ap-southeast-2``, ``us-gov-west-1``. The
#: China regions live under another domain and are not offered.
REGION_PATTERN = re.compile(r"[a-z]{2}(?:-[a-z]+){1,2}-\d{1,2}")

#: An access key id, and the shape of a secret access key.
_ACCESS_KEY_ID = re.compile(r"[A-Z0-9]{16,128}")
_NO_SPACE = re.compile(r"\S+")

#: The most recipients one message to a provider may have (the lowest of theirs,
#: Postmark's 50).
MAX_RECIPIENTS = 50

#: The settings of ``monitor.email_api`` that are credentials.
SECRET_FIELDS: tuple[str, ...] = ("api_key", "access_key_id", "secret_access_key")


@dataclass(frozen=True)
class ApiMail:
    """
    A message ready for a provider.

    Attributes:
        subject: The subject.
        text: The plain text part.
        html: The HTML part.
        from_address: The sender's address.
        sender_name: The display name in ``From``.
        recipients: Who it is for.
        message_id: A stable id for this message, so a retry is not a second
            message at providers that deduplicate on one.
        headers: Extra headers (``Auto-Submitted``...).
        raw: The whole MIME message, for the provider that takes it as it is.
    """

    subject: str
    text: str
    html: str
    from_address: str
    recipients: tuple[str, ...]
    sender_name: str | None = None
    message_id: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    raw: bytes = b""

    @property
    def sender(self) -> str:
        """The ``From`` value: ``Noust (web-1) <alerts@example.com>``."""
        return (
            formataddr((self.sender_name, self.from_address))
            if self.sender_name
            else self.from_address
        )


def validate_provider(value: Any) -> str:
    """
    Args:
        value: What ``monitor.email_api.provider`` was set to.

    Returns:
        The provider's name, or an empty string for none (SMTP).

    Raises:
        ValueError: A provider Noust does not speak.
    """
    text = str(value or "").strip().lower()
    if text and text not in PROVIDERS:
        raise ValueError(
            f"monitor.email_api.provider must be one of: {', '.join(PROVIDERS)} (or empty for SMTP)"
        )
    return text


def validate_region(value: Any) -> str:
    """
    Args:
        value: An AWS region name.

    Returns:
        The region, lower-cased and trimmed; empty for none.

    Raises:
        ValueError: When it is not the name of a region.
    """
    text = str(value or "").strip().lower()
    if text and (text.startswith("cn-") or not REGION_PATTERN.fullmatch(text)):
        raise ValueError(
            "monitor.email_api.region must be an AWS region such as us-east-1 or eu-west-1 "
            "(the China regions are not supported)"
        )
    return text


def missing(provider: str, settings: Mapping[str, Any]) -> list[str]:
    """
    Args:
        provider: The provider.
        settings: ``monitor.email_api``.

    Returns:
        The settings the provider cannot send without that are not set.
    """
    needed = ["from_address"]
    if provider == "ses":
        needed += ["region", "access_key_id", "secret_access_key"]
    else:
        needed.append("api_key")
    return [name for name in needed if not str(settings.get(name) or "").strip()]


def request_for(
    provider: str,
    mail: ApiMail,
    settings: Mapping[str, Any],
    *,
    moment: datetime | None = None,
) -> Request:
    """
    Build the request that hands a message to a provider.

    Args:
        provider: One of :data:`PROVIDERS`.
        mail: The message.
        settings: ``monitor.email_api``: the credentials and, for SES, the
            region.
        moment: The time to sign an SES request at; now by default.

    Returns:
        The request. Credentials are in headers (or, for SES, in the signature),
        never in a URL.

    Raises:
        ValueError: A provider that does not exist, a setting it needs that is
            not set or not shaped like one, or more recipients than it takes.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"{provider!r} is not an email provider; the providers are {PROVIDERS}")
    absent = missing(provider, settings)
    if absent:
        raise ValueError(f"monitor.email_api.{absent[0]} is needed to send through {provider}")
    if not mail.recipients:
        raise ValueError("There is nobody to send the email to; set monitor.email_recipients")
    if len(mail.recipients) > MAX_RECIPIENTS:
        raise ValueError(f"An email goes to at most {MAX_RECIPIENTS} recipients through a provider")
    key = str(settings.get("api_key") or "").strip()
    if provider != "ses" and not _NO_SPACE.fullmatch(key):
        raise ValueError("monitor.email_api.api_key must not contain spaces")
    builders = {
        "resend": lambda: _resend(mail, key),
        "postmark": lambda: _postmark(mail, key),
        "brevo": lambda: _brevo(mail, key),
        "ses": lambda: _ses(mail, settings, moment or datetime.now(timezone.utc)),
    }
    return builders[provider]()


def _resend(mail: ApiMail, key: str) -> Request:
    """
    Args:
        mail: The message.
        key: The API key.

    Returns:
        Resend's ``POST /emails``; its ``Idempotency-Key`` makes a retry of the
        same notification the same email.
    """
    body: dict[str, Any] = {
        "from": mail.sender,
        "to": list(mail.recipients),
        "subject": mail.subject,
        "html": mail.html,
        "text": mail.text,
    }
    if mail.headers:
        body["headers"] = dict(mail.headers)
    headers = {"Authorization": f"Bearer {key}"}
    if mail.message_id:
        headers["Idempotency-Key"] = mail.message_id
    return json_request(RESEND_URL, body, headers)


def _postmark(mail: ApiMail, key: str) -> Request:
    """
    Args:
        mail: The message.
        key: The server token.

    Returns:
        Postmark's ``POST /email`` on the transactional stream.
    """
    body: dict[str, Any] = {
        "From": mail.sender,
        "To": ", ".join(mail.recipients),
        "Subject": mail.subject,
        "HtmlBody": mail.html,
        "TextBody": mail.text,
        "MessageStream": "outbound",
        "Headers": [{"Name": name, "Value": value} for name, value in mail.headers.items()],
    }
    return json_request(POSTMARK_URL, body, {"X-Postmark-Server-Token": key})


def _brevo(mail: ApiMail, key: str) -> Request:
    """
    Args:
        mail: The message.
        key: The API key.

    Returns:
        Brevo's ``POST /v3/smtp/email``.
    """
    sender: dict[str, str] = {"email": mail.from_address}
    if mail.sender_name:
        sender["name"] = mail.sender_name
    body: dict[str, Any] = {
        "sender": sender,
        "to": [{"email": address} for address in mail.recipients],
        "subject": mail.subject,
        "htmlContent": mail.html,
        "textContent": mail.text,
    }
    if mail.headers:
        body["headers"] = dict(mail.headers)
    return json_request(BREVO_URL, body, {"api-key": key})


def _ses(mail: ApiMail, settings: Mapping[str, Any], moment: datetime) -> Request:
    """
    Args:
        mail: The message; its ``raw`` is what SES sends.
        settings: ``monitor.email_api``.
        moment: When to sign the request.

    Returns:
        SES v2's ``SendEmail`` with the whole MIME message, signed with
        Signature Version 4.

    Raises:
        ValueError: When the region, the access key id or the message is not
            what SES takes.
    """
    region = validate_region(settings.get("region"))
    access_key = str(settings.get("access_key_id") or "").strip()
    secret_key = str(settings.get("secret_access_key") or "").strip()
    if not _ACCESS_KEY_ID.fullmatch(access_key):
        raise ValueError("monitor.email_api.access_key_id is not an AWS access key id")
    if not _NO_SPACE.fullmatch(secret_key):
        raise ValueError("monitor.email_api.secret_access_key must not contain spaces")
    if not mail.raw:
        raise ValueError("Amazon SES needs the whole message, and there is none")
    body = json.dumps(
        {
            "FromEmailAddress": mail.sender,
            "Destination": {"ToAddresses": list(mail.recipients)},
            "Content": {"Raw": {"Data": base64.b64encode(mail.raw).decode("ascii")}},
        }
    ).encode("utf-8")
    url = SES_URL.format(region=region)
    signed = aws_sigv4.sign(
        "POST",
        url,
        {"Content-Type": "application/json"},
        body,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
        service="ses",
        moment=moment,
    )
    return post(url, body, signed)
