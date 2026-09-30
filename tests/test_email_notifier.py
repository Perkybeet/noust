# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.monitor.email_notifier`: one email path.

The process-observation report, the "send a test" message and every event the
notifier's email channel delivers go through one renderer and one transport.
These tests pin that the report and the test email render in the configured
language with the same layout as any notification, that the evidence they carry
- a process's command line, its detail - stays exactly what the kernel or the
scan reported, and that what reaches the SMTP server is a well-formed message:
``multipart/alternative`` (text, then HTML with the wordmark inline), with
``Date``, ``Message-ID`` and ``Auto-Submitted``.

:meth:`EmailNotifier.__init__` reloads the shared :class:`Config` singleton
from disk, so ``notifications.language`` is set on it *after* a notifier is
built, the way :class:`~noust.monitor.process_monitor.ProcessMonitor`'s own
cached notifier reads whatever the singleton currently holds at send time.
"""

from __future__ import annotations

import email as email_lib
from email import policy
from typing import Any

import pytest

from noust.core.config import Config
from noust.monitor.email_notifier import EmailContent, EmailNotifier, SMTPConfig
from noust.monitor.models import SEVERITY_WARNING, ProcessInfo, ProcessObservation
from tests.test_notifier import config  # noqa: F401  (pytest resolves fixtures by name)

# The notifier's config fixture is imported rather than replicated, so there
# stays one definition of "a sandboxed configuration".
# ruff: noqa: F811


def _notifier() -> EmailNotifier:
    """
    Build a notifier against the sandboxed config.

    SMTP details are never exercised by these tests - no test here actually
    sends anything - so they are fixed, unused values.
    """
    return EmailNotifier(
        smtp_config=SMTPConfig(host="smtp.example.com", port=465, use_ssl=True),
        recipients=["ops@example.com"],
    )


def _observation(**overrides: Any) -> ProcessObservation:
    """A process worth a human's attention, with sensible defaults."""
    fields: dict[str, Any] = {
        "process": ProcessInfo(pid=1234, name="xmrig", user="nobody", cpu_percent=97.5),
        "signal": "name-pattern",
        "severity": SEVERITY_WARNING,
        "detail": "Executable name matches the known-malware pattern 'xmrig'.",
    }
    fields.update(overrides)
    return ProcessObservation(**fields)


class TestLocale:
    """``notifications.language`` is read for this notifier's own text too."""

    def test_defaults_to_english(self, config: Config) -> None:
        assert _notifier()._locale() == "en"

    def test_follows_the_configured_language(self, config: Config) -> None:
        notifier = _notifier()
        config.set("notifications.language", "es")

        assert notifier._locale() == "es"


class TestRenderObservations:
    """The process-observation report, in the configured language."""

    def test_english_subject_and_heading(self, config: Config) -> None:
        content = _notifier().render_observations([_observation()])

        assert content.subject.startswith("[Noust] Process observations (")
        assert "Process observations" in content.text
        assert "Process observations" in content.html

    def test_spanish_subject_and_heading(self, config: Config) -> None:
        notifier = _notifier()
        config.set("notifications.language", "es")

        content = notifier.render_observations([_observation()])

        assert content.subject.startswith("[Noust] Observaciones de procesos (")
        assert "Observaciones de procesos" in content.text
        assert 'lang="es"' in content.html

    def test_the_server_is_named_by_server_name(self, config: Config) -> None:
        notifier = _notifier()
        config.set("server.name", "edge-3")

        content = notifier.render_observations([_observation()])

        assert content.subject == "[Noust] Process observations (edge-3)"
        assert "Server: edge-3" in content.text
        assert content.headers["X-Noust-Server"] == "edge-3"

    def test_spanish_counts_and_disclaimer(self, config: Config) -> None:
        notifier = _notifier()
        config.set("notifications.language", "es")

        content = notifier.render_observations(
            [_observation(), _observation(severity=SEVERITY_WARNING)]
        )

        assert "Procesos: 2" in content.text
        assert "Avisos: 2" in content.text
        assert "El monitor solo informa" in content.text
        assert "El monitor solo informa" in content.html

    def test_the_labels_of_a_process_are_translated_too(self, config: Config) -> None:
        """The table headings used to stay English in a Spanish report."""
        notifier = _notifier()
        config.set("notifications.language", "es")

        content = notifier.render_observations([_observation()])

        assert "Señal: name-pattern" in content.text
        assert "Usuario: nobody" in content.text
        assert "Aviso: xmrig (PID 1234)" in content.text

    def test_the_evidence_itself_is_never_translated(self, config: Config) -> None:
        """A process's own detail is not a catalog value, in either locale."""
        config.set("notifications.language", "es")
        observation = _observation(detail="Executable name matches the known-malware pattern")

        content = _notifier().render_observations([observation])

        assert "Executable name matches the known-malware pattern" in content.text
        assert "Executable name matches the known-malware pattern" in content.html

    def test_html_escapes_a_hostile_command_line(self, config: Config) -> None:
        observation = _observation(
            process=ProcessInfo(pid=1, name="x", user="u", command="<script>alert(1)</script>")
        )

        content = _notifier().render_observations([observation])

        assert "<script>alert(1)</script>" not in content.html
        assert "&lt;script&gt;" in content.html

    def test_it_is_the_same_layout_as_every_notification(self, config: Config) -> None:
        content = _notifier().render_observations([_observation()])

        assert content.headers["Auto-Submitted"] == "auto-generated"
        assert "cid:noust-wordmark" in content.html
        assert len(content.images) == 1


class TestSendTestEmail:
    """The settings page's SMTP connectivity check."""

    def _captured(self, notifier: EmailNotifier, monkeypatch: pytest.MonkeyPatch) -> EmailContent:
        """
        Args:
            notifier: The notifier under test.
            monkeypatch: Patching helper, scoped to the test.

        Returns:
            What would have been handed to the SMTP transport.
        """
        sent: list[EmailContent] = []
        monkeypatch.setattr(notifier, "_send", lambda content: sent.append(content) or True)
        notifier.send_test_email()
        return sent[0]

    def test_english(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        notifier = _notifier()
        config.set("server.name", "web-1")

        content = self._captured(notifier, monkeypatch)

        assert content.subject == "[Noust] Test notification: Email (web-1)"
        assert "If you can read this, the Email channel is configured correctly." in content.text
        assert "If you can read this, the Email channel is configured correctly." in content.html

    def test_spanish(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        notifier = _notifier()
        config.set("notifications.language", "es")
        config.set("server.name", "web-1")

        content = self._captured(notifier, monkeypatch)

        assert content.subject == "[Noust] Notificación de prueba: Correo electrónico (web-1)"
        assert "Si ves esto, el canal Correo electrónico está bien configurado." in content.text


class _Server:
    """An SMTP server that keeps what it was given."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, list[str], bytes]] = []

    def sendmail(self, sender: str, recipients: list[str], message: bytes) -> None:
        self.sent.append((sender, recipients, message))

    def quit(self) -> None:
        return None


class TestWhatReachesTheServer:
    """The MIME message: structure, headers, inline wordmark."""

    def _delivered(  # type: ignore[no-untyped-def]
        self,
        config: Config,
        monkeypatch: pytest.MonkeyPatch,
        settings: dict[str, str] | None = None,
    ):
        notifier = EmailNotifier(
            smtp_config=SMTPConfig(
                host="smtp.example.com",
                port=465,
                username="alerts@example.com",
                use_ssl=True,
            ),
            recipients=["ops@example.com", "dev@example.com"],
        )
        for key, value in (settings or {}).items():
            config.set(key, value)
        server = _Server()
        monkeypatch.setattr(notifier, "_create_connection", lambda: server)
        notifier.send_test_email()
        sender, recipients, raw = server.sent[0]
        return sender, recipients, email_lib.message_from_bytes(raw, policy=policy.default)

    def test_the_envelope_and_the_recipients(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sender, recipients, mail = self._delivered(config, monkeypatch)

        assert sender == "alerts@example.com"
        assert recipients == ["ops@example.com", "dev@example.com"]
        assert mail["To"] == "ops@example.com, dev@example.com"

    def test_the_sender_has_a_name(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        _, _, mail = self._delivered(config, monkeypatch, {"server.name": "web-1"})

        assert mail["From"].addresses[0].display_name == "Noust (web-1)"
        assert mail["From"].addresses[0].addr_spec == "alerts@example.com"

    def test_it_carries_the_headers_an_automatic_message_should(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, mail = self._delivered(config, monkeypatch)

        assert mail["Date"] is not None
        assert mail["Date"].datetime.tzinfo is not None
        assert mail["Message-ID"].endswith("@example.com>")
        assert mail["Auto-Submitted"] == "auto-generated"
        assert mail["X-Auto-Response-Suppress"] == "All"
        assert mail["X-Noust-Event"] == "test"

    def test_the_structure_is_text_then_html_with_the_wordmark_inline(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, mail = self._delivered(config, monkeypatch)

        assert mail.get_content_type() == "multipart/alternative"
        text, related = mail.get_payload()
        assert text.get_content_type() == "text/plain"
        assert related.get_content_type() == "multipart/related"
        html, image = related.get_payload()
        assert html.get_content_type() == "text/html"
        assert image.get_content_type() == "image/png"
        assert image["Content-ID"] == "<noust-wordmark>"
        assert image.get_content_disposition() == "inline"
        assert image.get_content().startswith(b"\x89PNG")

    def test_non_ascii_survives_the_trip(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, _, mail = self._delivered(config, monkeypatch, {"notifications.language": "es"})

        assert mail["Subject"].startswith("[Noust] Notificación de prueba")
        body = mail.get_body(preferencelist=("plain",))
        assert body is not None
        assert "Si ves esto" in body.get_content()
        html_body = mail.get_body(preferencelist=("html",))
        assert html_body is not None
        assert "está bien configurado" in html_body.get_content()

    def test_a_subject_cannot_smuggle_a_header(self) -> None:
        notifier = _notifier()
        content = EmailContent(subject="x", text="t", html="h")
        content.subject = "line one\r\nBcc: attacker@example.com"

        with pytest.raises(ValueError, match="linefeed"):
            notifier._build_message(content)
