# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two messages an account receives: an invitation to the console, and a link to choose a new password.

Both leave through the same transport every notification does
(:class:`~noust.monitor.email_notifier.EmailNotifier`: one SMTP implementation, one
provider client), so "is there a mail channel" has one answer, and both carry a link to
the console built from ``web.public_url``. The code rides in the link's fragment
(``#code=...``), which a browser never sends to a server, so it is not in any access log
on the way; the console reads it from there.

The texts are Noust's own, in the language of ``notifications.language``, written here
because they are not notifications: nothing is filtered, retried or recorded in the
delivery ledger, and a failure reaches the person who asked for the message.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from noust.core.accounts.model import AccountError
from noust.core.messages import DEFAULT_LOCALE, normalize_locale

#: Both languages, key by key. A test holds them to the same keys and placeholders.
TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "invite.subject": "You have been invited to Noust",
        "invite.greeting": "Hello,",
        "invite.body": (
            "An account named {username} has been made for you on Noust{by}. "
            "Open the link, choose a password and set up your authenticator."
        ),
        "invite.by": " by {inviter}",
        "invite.expiry": "The link works once and for {hours} hours.",
        "invite.ignore": "If you were not expecting this, ignore this message: nothing happens until the link is used.",
        "reset.subject": "Choose a new Noust password",
        "reset.greeting": "Hello,",
        "reset.body": (
            "Somebody asked to choose a new password for the account {username}. "
            "Open the link and enter a new password and a code from your authenticator."
        ),
        "reset.expiry": "The link works once and for {minutes} minutes.",
        "reset.ignore": (
            "If you did not ask for this, ignore this message: your password has not changed "
            "and the link is useless without your authenticator."
        ),
        "link.label": "Open the link:",
    },
    "es": {
        "invite.subject": "Te han invitado a Noust",
        "invite.greeting": "Hola,",
        "invite.body": (
            "Se ha creado para ti la cuenta {username} en Noust{by}. "
            "Abre el enlace, elige una contraseña y configura tu autenticador."
        ),
        "invite.by": " por {inviter}",
        "invite.expiry": "El enlace sirve una sola vez y durante {hours} horas.",
        "invite.ignore": "Si no esperabas este mensaje, ignóralo: no ocurre nada hasta que se use el enlace.",
        "reset.subject": "Elige una nueva contraseña de Noust",
        "reset.greeting": "Hola,",
        "reset.body": (
            "Alguien ha pedido elegir una nueva contraseña para la cuenta {username}. "
            "Abre el enlace e introduce una nueva contraseña y un código de tu autenticador."
        ),
        "reset.expiry": "El enlace sirve una sola vez y durante {minutes} minutos.",
        "reset.ignore": (
            "Si no lo has pedido tú, ignora este mensaje: tu contraseña no ha cambiado "
            "y el enlace no sirve sin tu autenticador."
        ),
        "link.label": "Abre el enlace:",
    },
}

#: The console's pages the links open.
INVITE_PATH = "/invite"
RESET_PATH = "/reset-password"


class MailUnavailable(AccountError):
    """Mail cannot be sent from here yet, and the message says what to set."""


class AccountMailer:
    """
    Send an account its invitation or its reset link.

    Args:
        read: Reads a configuration key (``read(key, default)``); the shared
            configuration by default.
        notifier: Builds the transport for a list of recipients; the email notifier by default.
    """

    def __init__(
        self,
        read: Callable[..., Any] | None = None,
        notifier: Callable[..., Any] | None = None,
    ) -> None:
        if read is None:
            from noust.core.config import Config

            config = Config()
            config.reload()
            read = config.get
        self._read = read
        if notifier is None:
            from noust.monitor.email_notifier import EmailNotifier

            notifier = EmailNotifier
        self._notifier = notifier

    def public_url(self) -> str:
        """
        Returns:
            ``web.public_url`` without a trailing slash; empty when none is set.
        """
        return str(self._read("web.public_url", "") or "").strip().rstrip("/")

    def _transport(self) -> bool:
        return bool(self._notifier(recipients=["probe@example.invalid"]).has_transport)

    def ready(self) -> bool:
        """
        Returns:
            Whether mail can be sent and a link can be built: a transport is set
            and the console has a public address.
        """
        return bool(self.public_url()) and self._transport()

    def _require(self) -> str:
        """
        Returns:
            The console's public address.

        Raises:
            MailUnavailable: When there is no transport or no public address.
        """
        if not self._transport():
            raise MailUnavailable(
                "There is no way to send mail from this server",
                details="Set monitor.smtp.* or monitor.email_api.* (Settings > Notifications).",
            )
        url = self.public_url()
        if not url:
            raise MailUnavailable(
                "The console has no public address to put in the link",
                details="Set web.public_url to the address people open the console at.",
            )
        return url

    def _locale(self) -> str:
        return str(normalize_locale(self._read("notifications.language", DEFAULT_LOCALE)))

    def _send(
        self, address: str, subject: str, paragraphs: list[str], link: str, label: str
    ) -> None:
        """
        Args:
            address: Who it is for.
            subject: The subject.
            paragraphs: The body, one string per paragraph.
            link: The link to the console.
            label: The words that introduce the link.
        """
        text = "\n\n".join([*paragraphs[:-1], f"{label}\n{link}", paragraphs[-1]]) + "\n"
        body = "".join(f"<p>{html.escape(item)}</p>" for item in paragraphs[:-1])
        markup = (
            f"{body}<p>{html.escape(label)} "
            f'<a href="{html.escape(link, quote=True)}">{html.escape(link)}</a></p>'
            f"<p>{html.escape(paragraphs[-1])}</p>"
        )
        self._notifier(recipients=[address]).send_message(subject, text, markup)

    def send_invitation(
        self,
        address: str,
        username: str,
        code: str,
        hours: int | float,
        *,
        invited_by: str | None = None,
    ) -> None:
        """
        Mail an invitation.

        Args:
            address: Where to send it.
            username: The account it is for.
            code: The invitation code, in clear.
            hours: How long it is good for.
            invited_by: Who invited them, when known.

        Raises:
            MailUnavailable: When mail cannot be sent from here.
            EmailError: When the mail server did not accept it.
        """
        base = self._require()
        texts = TEXTS[self._locale()]
        by = texts["invite.by"].format(inviter=invited_by) if invited_by else ""
        self._send(
            address,
            texts["invite.subject"],
            [
                texts["invite.greeting"],
                texts["invite.body"].format(username=username, by=by),
                texts["invite.expiry"].format(hours=int(hours)),
                texts["invite.ignore"],
            ],
            f"{base}{INVITE_PATH}#code={code}",
            texts["link.label"],
        )

    def send_reset(self, address: str, username: str, code: str, minutes: int = 60) -> None:
        """
        Mail a password reset link.

        Args:
            address: Where to send it.
            username: The account it is for.
            code: The reset code, in clear.
            minutes: How long it is good for.

        Raises:
            MailUnavailable: When mail cannot be sent from here.
            EmailError: When the mail server did not accept it.
        """
        base = self._require()
        texts = TEXTS[self._locale()]
        self._send(
            address,
            texts["reset.subject"],
            [
                texts["reset.greeting"],
                texts["reset.body"].format(username=username),
                texts["reset.expiry"].format(minutes=minutes),
                texts["reset.ignore"],
            ],
            f"{base}{RESET_PATH}#code={code}",
            texts["link.label"],
        )
