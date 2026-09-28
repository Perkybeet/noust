# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`wasm.monitor.email_notifier`'s own WASM-authored text.

:class:`~wasm.monitor.email_notifier.EmailNotifier` renders its subjects and
bodies directly into an :class:`~wasm.monitor.email_notifier.EmailContent`,
a second path from :mod:`wasm.core.notifier`'s multi-channel one, which
already builds every event from :mod:`wasm.core.messages`. That is why this
one used to ignore ``notifications.language`` entirely: nothing here ever
called into the catalog. These tests pin that the process-observation report
and the test email now render in the configured locale, while the evidence
they carry - a process's command line, its detail - stays exactly what the
kernel or the scan itself reported, never a catalog value.

:meth:`EmailNotifier.__init__` reloads the shared :class:`Config` singleton
from disk, so ``notifications.language`` is set on it *after* a notifier is
built, the way :class:`~wasm.monitor.process_monitor.ProcessMonitor`'s own
cached notifier reads whatever the singleton currently holds at send time.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_notifier import config  # noqa: F401  (pytest resolves fixtures by name)
from wasm.core.config import Config
from wasm.monitor.email_notifier import EmailContent, EmailNotifier, SMTPConfig
from wasm.monitor.models import SEVERITY_WARNING, ProcessInfo, ProcessObservation

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
        notifier = _notifier()

        content = notifier.render_observations([_observation()])

        assert content.subject == f"[WASM] 1 process observation(s) on {notifier._hostname()}"
        assert "WASM monitor - process observations" in content.text
        assert "<h2>WASM monitor - process observations</h2>" in content.html

    def test_spanish_subject_and_heading(self, config: Config) -> None:
        notifier = _notifier()
        config.set("notifications.language", "es")

        content = notifier.render_observations([_observation()])

        assert content.subject == f"[WASM] 1 observación(es) de proceso en {notifier._hostname()}"
        assert "WASM monitor - observaciones de procesos" in content.text
        assert "<h2>WASM monitor - observaciones de procesos</h2>" in content.html

    def test_spanish_counts_and_disclaimer(self, config: Config) -> None:
        notifier = _notifier()
        config.set("notifications.language", "es")

        content = notifier.render_observations(
            [_observation(), _observation(severity=SEVERITY_WARNING)]
        )

        assert "Detectados: 2 proceso(s), 2 de ellos marcados como aviso" in content.text
        assert "El monitor solo informa." in content.text
        assert "El monitor solo informa." in content.html

    def test_the_evidence_itself_is_never_translated(self, config: Config) -> None:
        """A process's own detail is not a catalog value, in either locale."""
        notifier = _notifier()
        config.set("notifications.language", "es")
        observation = _observation(detail="Executable name matches the known-malware pattern")

        content = notifier.render_observations([observation])

        assert "Executable name matches the known-malware pattern" in content.text
        assert "Executable name matches the known-malware pattern" in content.html

    def test_html_escapes_a_hostile_command_line(self, config: Config) -> None:
        observation = _observation(
            process=ProcessInfo(pid=1, name="x", user="u", command="<script>alert(1)</script>")
        )

        content = _notifier().render_observations([observation])

        assert "<script>alert(1)</script>" not in content.html
        assert "&lt;script&gt;" in content.html


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

        content = self._captured(notifier, monkeypatch)

        assert content.subject == f"[WASM] Test email - {notifier._hostname()}"
        assert "WASM monitor - test email" in content.text
        assert "Receiving this means monitor notifications are configured correctly." in (
            content.text
        )
        assert "<h2>WASM monitor - test email</h2>" in content.html

    def test_spanish(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        notifier = _notifier()
        config.set("notifications.language", "es")

        content = self._captured(notifier, monkeypatch)

        assert content.subject == f"[WASM] Correo de prueba - {notifier._hostname()}"
        assert "WASM monitor - correo de prueba" in content.text
        assert (
            "Si recibes esto, las notificaciones del monitor están bien configuradas."
            in content.text
        )
        assert "<h2>WASM monitor - correo de prueba</h2>" in content.html
