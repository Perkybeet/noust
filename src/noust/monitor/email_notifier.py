# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Email delivery: the one SMTP implementation, and the monitor's observation report.

Three properties matter here, and each maps to a defect this module used to
have:

- **Every socket has a deadline.** The monitor loop is single threaded. An SMTP
  connection without a timeout does not fail, it hangs, and the daemon stops
  monitoring forever while systemd still reports it as active.
- **Credentials never cross a plaintext session.** Authenticating over an
  unencrypted connection puts the password on the wire; the notifier refuses.
- **The password never reaches a log.** Server replies are echoed into error
  details, and some servers echo back what was sent, so details are redacted.

Instead of an SMTP relay a message can go through a provider's HTTP API
(``monitor.email_api.provider``: Resend, Postmark, Brevo or Amazon SES, see
:mod:`noust.core.email_api`). It is the same message laid out by the same
renderer: only the last step differs, and the request goes through the
notifier's one guarded sender.

There is one email path. An event notification (``noust.core.notifier``'s email
channel), the monitor's process-observation report and the "send a test" button
all build a :class:`~noust.core.notifications.model.Notification` and reach the
server through :meth:`EmailNotifier.send_notification`, which lays it out with
the one email renderer (:mod:`noust.core.notifications.render.email`): a
``multipart/alternative`` message with a text part and a table-based HTML part,
the wordmark attached inline, and the ``Date``, ``Message-ID``,
``Auto-Submitted`` headers a well-behaved automatic message carries.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from http.client import HTTPException
from typing import Any, cast
from urllib.error import HTTPError, URLError

from noust.core import email_api
from noust.core.config import Config
from noust.core.exceptions import EmailError, EmailRefused
from noust.core.logger import Logger
from noust.core.messages import Locale, message, normalize_locale
from noust.core.notifications.composers import compose_observations, compose_test
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Fact, Notification, Section, State
from noust.core.notifications.render import email as render_email
from noust.monitor.models import SEVERITY_WARNING, ProcessObservation

#: Deadline for every SMTP socket operation, in seconds. Long enough for a slow
#: relay, short enough that a scan loop recovers within one interval.
DEFAULT_SMTP_TIMEOUT = 30

#: Beyond this a "timeout" stops protecting the scan loop it exists to protect.
MAX_SMTP_TIMEOUT = 120

_REDACTED = "***"

#: Providers whose API takes a message as text and HTML only. Amazon SES takes
#: the whole MIME message, so it keeps the inline wordmark.
_NO_INLINE_IMAGES = frozenset({"resend", "postmark", "brevo"})

#: The statuses of a provider that are worth a second try.
_TRANSIENT_STATUSES = frozenset({408, 425, 429})


@dataclass
class SMTPConfig:
    """
    Connection settings for the outgoing mail server.

    Attributes:
        host: SMTP server hostname.
        port: SMTP server port.
        username: Account used to authenticate, empty for anonymous relays.
        password: Password for that account.
        use_ssl: Connect with implicit TLS (SMTPS, usually port 465).
        use_tls: Connect in the clear and upgrade with STARTTLS (usually 587).
        from_address: Envelope sender. Defaults to the username.
        timeout: Socket deadline in seconds.
    """

    host: str
    port: int
    username: str = ""
    password: str = ""
    use_ssl: bool = True
    use_tls: bool = False
    from_address: str | None = None
    timeout: int = DEFAULT_SMTP_TIMEOUT

    def __post_init__(self) -> None:
        """
        Normalise the sender and reject a deadline that is not one.

        Raises:
            EmailError: When the timeout is missing, zero or negative.
        """
        if not self.from_address:
            self.from_address = self.username
        if not self.timeout or self.timeout <= 0:
            raise EmailError(
                "SMTP timeout must be a positive number of seconds",
                details=(
                    "A missing or zero timeout hangs the monitor loop forever. "
                    f"Set monitor.smtp.timeout to a value between 1 and {MAX_SMTP_TIMEOUT}."
                ),
            )
        self.timeout = min(int(self.timeout), MAX_SMTP_TIMEOUT)

    @property
    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in a log or an error message."""
        return tuple(value for value in (self.password,) if value)


@dataclass
class EmailContent:
    """
    A rendered message, ready to be handed to the server.

    Attributes:
        subject: Message subject.
        text: Plain text body.
        html: HTML body.
        headers: Extra headers to set.
        images: Images the HTML refers to by ``cid:``, attached inline.
        message_id: The local part of ``Message-ID``, when the caller wants
            the message identifiable (the notification's own id); a fresh
            one is made otherwise.
        sender_name: The display name in ``From`` (``Noust (web-1)``).
    """

    subject: str
    text: str
    html: str
    headers: dict[str, str] = field(default_factory=dict)
    images: Sequence[render_email.InlineImage] = ()
    message_id: str | None = None
    sender_name: str | None = None


class EmailNotifier:
    """Sends notifications and the monitor's observation report by email."""

    def __init__(
        self,
        smtp_config: SMTPConfig | None = None,
        recipients: list[str] | None = None,
        verbose: bool = False,
        *,
        api_settings: Mapping[str, Any] | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        """
        Args:
            smtp_config: Server settings. Loaded from the global config if None.
            recipients: Destination addresses. Loaded from the config if None.
            verbose: Enable verbose logging.
            api_settings: ``monitor.email_api``, when it is not the configured
                one (tests).
            opener: Replacement for the HTTP opener of a provider's API; tests
                inject one so the suite never opens a socket.
        """
        self.logger = Logger(verbose=verbose)
        self.config = Config()
        self.config.reload()

        self.smtp_config = smtp_config or self._load_smtp_config()
        self.recipients = recipients if recipients is not None else self._load_recipients()
        self.api_settings: Mapping[str, Any] = (
            api_settings if api_settings is not None else self._load_api_settings()
        )
        self._opener = opener

    def _load_api_settings(self) -> dict[str, Any]:
        """
        Read the provider settings from the global configuration.

        Returns:
            ``monitor.email_api``; empty fields when it was never set.
        """
        block = self.config.get("monitor.email_api", {})
        return dict(block) if isinstance(block, dict) else {}

    @property
    def provider(self) -> str:
        """The email provider the API settings name; empty when mail goes by SMTP."""
        try:
            return email_api.validate_provider(self.api_settings.get("provider"))
        except ValueError:
            # A hand edit that names a provider Noust does not speak: SMTP, and
            # the configuration check says why.
            self.logger.warning("monitor.email_api.provider is not a known provider; using SMTP")
            return ""

    def _load_smtp_config(self) -> SMTPConfig:
        """
        Build the server settings from the global configuration.

        Returns:
            The SMTP settings.

        Raises:
            EmailError: When the configured timeout is not positive.
        """
        return SMTPConfig(
            host=self.config.get("monitor.smtp.host", ""),
            port=int(self.config.get("monitor.smtp.port", 465)),
            username=self.config.get("monitor.smtp.username", ""),
            password=self.config.get("monitor.smtp.password", ""),
            use_ssl=bool(self.config.get("monitor.smtp.use_ssl", True)),
            use_tls=bool(self.config.get("monitor.smtp.use_tls", False)),
            from_address=self.config.get("monitor.smtp.from_address", ""),
            timeout=int(self.config.get("monitor.smtp.timeout", DEFAULT_SMTP_TIMEOUT)),
        )

    def _load_recipients(self) -> list[str]:
        """
        Read the destination addresses from the global configuration.

        Returns:
            The configured recipients, possibly empty.
        """
        recipients = self.config.get("monitor.email_recipients", [])
        if isinstance(recipients, str):
            return [recipients]
        return list(recipients or [])

    def _locale(self) -> Locale:
        """
        Language this notifier's own subjects and bodies render in.

        Read from the same configuration object every other reader of
        ``notifications.language`` shares (:class:`~noust.core.config.Config`
        is a singleton), so a language switched in the panel takes effect
        from the next report this notifier sends - the same lag every other
        setting read from this instance's ``self.config`` already has, since
        it is loaded once in :meth:`__init__` rather than reloaded per call.

        Returns:
            ``notifications.language``, normalised.
        """
        return normalize_locale(self.config.get("notifications.language"))

    def _redact(self, text: str) -> str:
        """
        Strip credentials out of a message before it is logged or raised.

        Args:
            text: Text that may quote a server reply.

        Returns:
            The text with every known secret replaced.
        """
        secrets = [
            *self.smtp_config.secrets,
            *(str(self.api_settings.get(name) or "") for name in email_api.SECRET_FIELDS),
        ]
        for secret in secrets:
            if secret:
                text = text.replace(secret, _REDACTED)
        return text

    @property
    def has_transport(self) -> bool:
        """True when mail can leave this server: a provider's API with what it needs, or SMTP."""
        provider = self.provider
        if provider:
            return not email_api.missing(provider, self.api_settings)
        return bool(self.smtp_config.host)

    @property
    def is_configured(self) -> bool:
        """True when there is a way to send and someone to send to."""
        return bool(self.recipients) and self.has_transport

    def _create_connection(self) -> smtplib.SMTP:
        """
        Open an authenticated connection to the mail server.

        Returns:
            The connected client.

        Raises:
            EmailError: When the transport is insecure or the server refuses.
        """
        config = self.smtp_config
        needs_login = bool(config.username or config.password)

        if needs_login and not (config.use_ssl or config.use_tls):
            raise EmailError(
                "Refusing to send SMTP credentials over an unencrypted connection",
                details=(
                    "Set monitor.smtp.use_ssl (port 465) or monitor.smtp.use_tls (port 587). "
                    "Only an anonymous local relay may run without encryption."
                ),
            )

        context = ssl.create_default_context()

        try:
            if config.use_ssl:
                server: smtplib.SMTP = smtplib.SMTP_SSL(
                    config.host,
                    config.port,
                    context=context,
                    timeout=config.timeout,
                )
            else:
                server = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
                if config.use_tls:
                    server.starttls(context=context)

            if needs_login:
                server.login(config.username, config.password)
            return server

        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError(
                "SMTP authentication failed",
                details=self._redact(f"Check monitor.smtp.username and password: {exc}"),
            ) from exc
        except smtplib.SMTPConnectError as exc:
            raise EmailError(
                "Failed to connect to the SMTP server",
                details=self._redact(f"{config.host}:{config.port} - {exc}"),
            ) from exc
        except (smtplib.SMTPException, ssl.SSLError, TimeoutError, OSError) as exc:
            raise EmailError(
                "Failed to establish an SMTP connection",
                details=self._redact(
                    f"{config.host}:{config.port} (timeout {config.timeout}s) - {exc}"
                ),
            ) from exc

    def _build_message(self, content: EmailContent) -> EmailMessage:
        """
        Assemble the MIME message of a rendered email.

        A ``multipart/alternative`` with the text part first (RFC 2046: the
        preferred part goes last) and the HTML after it, whose inline images
        travel in a ``multipart/related`` next to it.

        Args:
            content: The rendered message.

        Returns:
            The message, with ``Date`` and ``Message-ID`` set.
        """
        return self._build_message_for(
            content, self.smtp_config.from_address or self.smtp_config.username
        )

    def _build_message_for(self, content: EmailContent, sender: str) -> EmailMessage:
        """
        Assemble the MIME message of a rendered email from a given sender.

        Args:
            content: The rendered message.
            sender: The address it is from.

        Returns:
            The message, with ``Date`` and ``Message-ID`` set.
        """
        domain = sender.rpartition("@")[2] if "@" in sender else self._hostname()
        mail = EmailMessage(policy=policy.SMTP)
        mail["Subject"] = content.subject
        mail["From"] = formataddr((content.sender_name, sender)) if content.sender_name else sender
        mail["To"] = ", ".join(self.recipients)
        mail["Date"] = formatdate(usegmt=True)
        mail["Message-ID"] = f"<{content.message_id or uuid.uuid4()}@{domain}>"
        for header, value in content.headers.items():
            mail[header] = value
        mail.set_content(content.text)
        mail.add_alternative(content.html, subtype="html")
        html_part = cast("list[EmailMessage]", mail.get_payload())[1]
        for image in content.images:
            html_part.add_related(
                image.data,
                "image",
                image.subtype,
                cid=f"<{image.cid}>",
                filename=image.filename,
                disposition="inline",
            )
        return mail

    def _send(self, content: EmailContent) -> bool:
        """
        Deliver a rendered message.

        Args:
            content: The message to send.

        Returns:
            True when the server accepted the message.

        Raises:
            EmailError: When the message could not be delivered.
        """
        if self.provider:
            return self._send_api(content)
        mail = self._build_message(content)
        server = self._create_connection()
        try:
            server.sendmail(
                self.smtp_config.from_address or self.smtp_config.username,
                self.recipients,
                mail.as_bytes(),
            )
        except (smtplib.SMTPException, TimeoutError, OSError) as exc:
            raise EmailError(
                "Failed to send the notification email",
                details=self._redact(str(exc)),
            ) from exc
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError) as exc:
                self.logger.debug(
                    f"SMTP connection did not close cleanly: {self._redact(str(exc))}"
                )

        self.logger.debug(f"Sent '{content.subject}' to {len(self.recipients)} recipient(s)")
        return True

    def _send_api(self, content: EmailContent) -> bool:
        """
        Hand a rendered message to the configured provider's API.

        Args:
            content: The message to send.

        Returns:
            True when the provider accepted it.

        Raises:
            EmailRefused: When the provider refuses it for a reason another try
                will not change (a key it does not know, a sender it has not
                verified), or the settings are not what it needs.
            EmailError: When the provider could not be reached or is having a
                bad moment: the delivery is worth trying again.
        """
        # Deferred import: the notifier delivers to this module's email channel,
        # so importing it here at load would be a cycle.
        from noust.core.notifier import send_guarded

        provider = self.provider
        sender = self.api_settings.get("from_address") or self.smtp_config.from_address or ""
        mail = email_api.ApiMail(
            subject=content.subject,
            text=content.text,
            html=content.html,
            from_address=str(sender),
            sender_name=content.sender_name,
            recipients=tuple(self.recipients),
            message_id=content.message_id,
            headers=content.headers,
            raw=self._build_message_for(content, str(sender)).as_bytes()
            if provider == "ses"
            else b"",
        )
        label = email_api.PROVIDER_LABELS[provider]
        try:
            request = email_api.request_for(provider, mail, self.api_settings)
            send_guarded(request, self.config, self._opener)
        except HTTPError as exc:
            reply = self._redact(exc.read(2048).decode("utf-8", "replace").strip())
            transient = exc.code in _TRANSIENT_STATUSES or exc.code >= 500
            error = EmailError if transient else EmailRefused
            raise error(
                f"{label} did not accept the email (HTTP {exc.code})",
                details=reply or str(exc.reason),
                output=reply or None,
            ) from exc
        except (URLError, HTTPException, TimeoutError, OSError) as exc:
            raise EmailError(f"Failed to reach {label}", details=self._redact(str(exc))) from exc
        except ValueError as exc:
            raise EmailRefused(
                f"The {label} settings are not usable", details=self._redact(str(exc))
            ) from exc
        self.logger.debug(f"Sent '{content.subject}' to {len(self.recipients)} recipient(s)")
        return True

    def send_notification(self, notification: Notification) -> bool:
        """
        Lay a notification out as an email and send it.

        The one way anything reaches the mail server: an event, the test
        message and the observation report differ only in the notification.

        Args:
            notification: What to tell the recipients.

        Returns:
            True when the server accepted the message.

        Raises:
            EmailError: When the message could not be delivered.
        """
        return self._send(self.content_for(notification, self._inline_images()))

    def send_message(
        self, subject: str, text: str, html: str, *, message_id: str | None = None
    ) -> bool:
        """
        Send one transactional message (an invitation, a password reset) to the recipients.

        It leaves through the same transport as every notification, so there is one SMTP
        implementation and one provider client. Nothing in it is rendered from a
        notification: the caller wrote the text, and it is answered to nobody.

        Args:
            subject: The subject.
            text: The plain text part.
            html: The HTML part.
            message_id: The local part of ``Message-ID``, when the caller wants one.

        Returns:
            True when the server accepted the message.

        Raises:
            EmailError: When the message could not be delivered or no transport is set.
        """
        if not self.has_transport:
            raise EmailError(
                "No way to send mail is configured",
                details="Set monitor.smtp.* or monitor.email_api.* first.",
            )
        return self._send(
            EmailContent(
                subject=subject,
                text=text,
                html=html,
                headers={"Auto-Submitted": "auto-generated"},
                message_id=message_id,
                sender_name=f"Noust ({self._hostname()})",
            )
        )

    def _inline_images(self) -> bool:
        """Returns: Whether the message can carry the wordmark as an attachment."""
        return self.provider not in _NO_INLINE_IMAGES

    @staticmethod
    def content_for(notification: Notification, inline_images: bool = True) -> EmailContent:
        """
        Render a notification into the message the transport sends.

        Args:
            notification: What to tell the recipients.
            inline_images: Attach the wordmark. Off for a provider whose API
                takes only text and HTML.

        Returns:
            The subject, both parts, the headers and the inline image.
        """
        rendered = render_email.render(notification, inline_images=inline_images)
        return EmailContent(
            subject=rendered.subject,
            text=rendered.text,
            html=rendered.html,
            headers=rendered.headers,
            images=rendered.images,
            message_id=rendered.message_id,
            sender_name=f"Noust ({notification.server})",
        )

    def _hostname(self) -> str:
        """
        Return the machine name used in subjects and bodies.

        Returns:
            The hostname, or "unknown" when it cannot be resolved.
        """
        try:
            return socket.gethostname()
        except OSError:
            return "unknown"

    def _context(self) -> NotificationContext:
        """
        Who is speaking and in which language, for this notifier's own reports.

        Returns:
            The context of ``notifications.language`` and ``server.name``; the
            report carries no link.
        """
        return NotificationContext.from_config(self.config)

    def _observation_section(self, observation: ProcessObservation, locale: Locale) -> Section:
        """
        Args:
            observation: One process the scan noted.
            locale: The language of the labels.

        Returns:
            The block that describes it. What the scan and the kernel said
            (the signal, the detail, the command line) stays verbatim.
        """
        process = observation.process
        severity = message(
            "severity.warning" if observation.severity == SEVERITY_WARNING else "severity.notice",
            locale,
        )
        rows = [
            Fact("signal", message("fact.signal", locale), observation.signal),
            Fact("user", message("fact.user", locale), process.user),
            Fact("cpu", message("fact.cpu", locale), f"{process.cpu_percent:.1f}%"),
            Fact("memory", message("fact.memory", locale), f"{process.memory_percent:.1f}%"),
            Fact("detail", message("fact.detail", locale), observation.detail),
            Fact("command", message("fact.command", locale), process.command, mono=True),
        ]
        if process.parent_pid:
            parent = f"{process.parent_name or '?'} (PID {process.parent_pid})"
            rows.append(Fact("parent", message("fact.parent", locale), parent))
        return Section(
            heading=f"{severity}: {process.name} (PID {process.pid})",
            rows=tuple(rows),
            state=State.WARNING if observation.severity == SEVERITY_WARNING else State.INFO,
        )

    def render_observations(self, observations: list[ProcessObservation]) -> EmailContent:
        """
        Render an observation report.

        Args:
            observations: What the scan noticed.

        Returns:
            The message to send: the same layout as every other notification.
        """
        ctx = self._context()
        warnings = sum(1 for o in observations if o.severity == SEVERITY_WARNING)
        report = compose_observations(
            [self._observation_section(o, ctx.locale) for o in observations],
            processes=len(observations),
            warnings=warnings,
            ctx=ctx,
        )
        return self.content_for(report, self._inline_images())

    def send_observation_alert(self, observations: list[ProcessObservation]) -> bool:
        """
        Email a set of observations.

        Args:
            observations: What the scan noticed.

        Returns:
            True when the report was sent, False when there was nothing to send
            or no working configuration.

        Raises:
            EmailError: When delivery fails.
        """
        if not observations:
            return False
        if not self.is_configured:
            self.logger.debug("SMTP or recipients not configured, skipping notification")
            return False

        return self._send(self.render_observations(observations))

    def send_test_email(self) -> bool:
        """
        Send a message that proves the configuration works.

        Returns:
            True when the message was accepted.

        Raises:
            EmailError: When delivery fails.
        """
        return self.send_notification(compose_test("email", self._context()))
