# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What never goes to a model, and what goes only in disguise (item 171).

Two different promises are made here, and they are kept by one object, the :class:`Masker` of a
conversation:

- **A secret never leaves.** The values Noust knows are secret (every application's variables,
  Noust's own credentials, the provider's key) and the shapes of a credential in text are removed
  before anything is sent, by the one implementation there is: :func:`noust.core.scrubbing.scrub_text`
  and :func:`~noust.core.scrubbing.hide_secrets`, which the MCP endpoint uses too (rule 3), plus the
  values of *every* application, not only the one a text is about, because a person pastes a value
  into a question and one application's log carries another's. Nothing here can recognise a secret
  it was not told about, so what the assistant is given to read is also limited at its source.
- **Personal data of a provider outside is disguised, and reversibly.** With a provider that is
  not on this machine or the operator's own network, the emails and the network addresses in the
  text are replaced by markers (``<email-1>``, ``<ip-2>``) that are the same every time inside the
  conversation, and put back in what the person sees and in the arguments of the calls the model
  makes. Loopback and unspecified addresses, which name nobody, are left. The operator may turn
  it off for a model of their own.

What comes from outside (a log, a README, a commit message) is also marked as data
(:func:`mark_as_data`). That helps the model and defends nothing: the controls that matter are the
server's, not the model's.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable, Mapping
from typing import Any

from noust.core.redact import Scrubber, known_credentials
from noust.core.scrubbing import hide_secrets, scrub_text

__all__ = ["DATA_TAG", "Masker", "mark_as_data", "server_secret_values"]

#: The tag text from outside is wrapped in.
DATA_TAG = "untrusted-data"

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"
)
_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:.])")
_MARKER = re.compile(r"<(?P<kind>ip|email)-(?P<number>\d+)>")


def server_secret_values(api_key: str = "") -> list[str]:
    """
    Collect every secret value this server knows.

    Args:
        api_key: The provider's key, which the model must never be sent back.

    Returns:
        Noust's own credentials and the secret variables of every application, whoever may see
        the application: this only removes, it discloses nothing. An application whose secrets
        cannot be read is skipped (logged by the reader); the shapes still apply to its text.
    """
    from noust.core.redact import app_secret_values
    from noust.core.store import get_store

    values = list(known_credentials())
    if api_key:
        values.append(api_key)
    for app in get_store().list_apps():
        values.extend(app_secret_values(app.domain))
    return values


class Masker:
    """What may be sent to a model in one conversation, and the way back."""

    def __init__(
        self,
        *,
        mask_personal: bool,
        secrets: Iterable[str] = (),
        markers: Mapping[str, str] | None = None,
    ) -> None:
        """
        Args:
            mask_personal: Disguise emails and network addresses with markers.
            secrets: Values to remove wherever they appear, besides what the scrubbing knows.
            markers: The markers of this conversation so far (``{"<ip-1>": "203.0.113.7"}``),
                to go on with when it is resumed.
        """
        self._personal = mask_personal
        self._scrubber = Scrubber(secrets)
        self._markers: dict[str, str] = dict(markers or {})
        self._by_value: dict[str, str] = {value: marker for marker, value in self._markers.items()}

    @classmethod
    def for_server(
        cls,
        *,
        mask_personal: bool,
        api_key: str = "",
        markers: Mapping[str, str] | None = None,
    ) -> Masker:
        """
        Make the masker of a conversation on this server.

        Args:
            mask_personal: Disguise emails and network addresses.
            api_key: The provider's key.
            markers: The markers of the conversation so far.

        Returns:
            A masker that knows every secret of the server as of now.
        """
        return cls(
            mask_personal=mask_personal, secrets=server_secret_values(api_key), markers=markers
        )

    @property
    def markers(self) -> dict[str, str]:
        """The markers made so far, to keep with the conversation."""
        return dict(self._markers)

    # -- the way out -----------------------------------------------------------------------------

    def mask(self, text: str, *, domain: str | None = None) -> str:
        """
        Make a piece of text fit to be sent.

        Args:
            text: Anything the model is to read: a question, a tool's answer.
            domain: The application it is about, whose secrets are removed first.

        Returns:
            The text with the secrets gone and, when asked, the personal data disguised.
        """
        cleaned = self._scrubber.scrub(scrub_text(text, domain))
        return self._disguise(cleaned) if self._personal else cleaned

    def mask_value(self, value: Any, *, domain: str | None = None) -> Any:
        """
        Make a structure fit to be sent.

        Args:
            value: Anything JSON can hold.
            domain: The application it is about.

        Returns:
            A copy in which a secret-named key's value is hidden and every string is masked.
        """
        return self._each(hide_secrets(value, domain))

    def _each(self, value: Any) -> Any:
        """
        Args:
            value: A structure already cleaned of what its keys say is secret.

        Returns:
            The same with the known secrets and the personal data of every string dealt with.
        """
        if isinstance(value, str):
            cleaned = self._scrubber.scrub(value)
            return self._disguise(cleaned) if self._personal else cleaned
        if isinstance(value, Mapping):
            return {key: self._each(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._each(item) for item in value]
        return value

    def _marker(self, kind: str, original: str) -> str:
        """
        Args:
            kind: ``ip`` or ``email``.
            original: The value being disguised.

        Returns:
            Its marker: the one it already has in this conversation, or the next.
        """
        found = self._by_value.get(original)
        if found is not None:
            return found
        number = 1 + sum(1 for marker in self._markers if marker.startswith(f"<{kind}-"))
        marker = f"<{kind}-{number}>"
        self._markers[marker] = original
        self._by_value[original] = marker
        return marker

    def _disguise(self, text: str) -> str:
        """
        Args:
            text: Text with its secrets already gone.

        Returns:
            The text with its emails and network addresses replaced by markers.
        """
        text = _EMAIL.sub(lambda m: self._email(m, text), text)
        text = _IPV4.sub(lambda m: self._address(m.group(0)), text)
        return _IPV6.sub(lambda m: self._address(m.group(0)), text)

    def _email(self, found: re.Match[str], text: str) -> str:
        """
        Args:
            found: A match of the email pattern.
            text: The text it is in.

        Returns:
            Its marker, or the text itself for what is not an email: the ``user@host:path`` of an
            ``scp``-style Git address names a repository, not a person.
        """
        after = text[found.end() : found.end() + 2]
        if after.startswith(":") and not after[1:].isdigit():
            return found.group(0)
        return self._marker("email", found.group(0))

    def _address(self, candidate: str) -> str:
        """
        Args:
            candidate: What looks like an IPv4 or IPv6 address.

        Returns:
            Its marker, or the candidate itself when it is not an address or names nobody (this
            machine, "any").
        """
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            return candidate
        if address.is_loopback or address.is_unspecified:
            return candidate
        return self._marker("ip", str(address) if address.version == 6 else candidate)

    # -- the way back ----------------------------------------------------------------------------

    def restore(self, text: str) -> str:
        """
        Put the original back in what a person reads, and in the arguments of a call.

        Args:
            text: Text that may carry markers.

        Returns:
            The text with every marker this conversation made replaced by what it stood for; a
            marker it did not make is left as it is.
        """
        if not self._markers:
            return text
        return _MARKER.sub(lambda m: self._markers.get(m.group(0), m.group(0)), text)

    def restore_value(self, value: Any) -> Any:
        """
        Args:
            value: Anything JSON can hold, such as the arguments of a call.

        Returns:
            A copy with the markers in every string restored.
        """
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, Mapping):
            return {key: self.restore_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.restore_value(item) for item in value]
        return value


def mark_as_data(text: str, source: str) -> str:
    """
    Wrap text that somebody else wrote, so that the model is told it is data.

    Args:
        text: A log, a README, a commit message.
        source: Where it came from, in a few words ("application logs").

    Returns:
        The text between ``<untrusted-data source="...">`` and its closing tag, with any
        occurrence of the tag inside the text made harmless so that it cannot close its own
        wrapper.
    """
    safe_source = re.sub(r'["<>\r\n]', " ", source).strip()[:80]
    body = text.replace(f"<{DATA_TAG}", f"<{DATA_TAG}_").replace(f"</{DATA_TAG}", f"</{DATA_TAG}_")
    return f'<{DATA_TAG} source="{safe_source}">\n{body}\n</{DATA_TAG}>'
