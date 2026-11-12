# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a change to how the server is reached was, said in the language of whoever reads it (item 138).

A firewall or SSH change is recorded in the ledger with what it is: a code (``firewall_allow``) and
its values (the port, the source). The English sentence of the record, its ``title``, is made from
them here, so the CLI, the audit trail and the console's page say the same words as a notification
in Spanish says in its own: a sentence a person wrote when the change was made could only be pasted
into a notice of another language, with an English clause in the middle of a Spanish sentence.

A record from before the ledger kept the code has only that sentence. :func:`describe_record` shows
it as it is, between quotes, as the text of the system that it is.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from noust.core.messages import MESSAGES, Locale, message

__all__ = ["describe", "describe_record", "known"]

log = logging.getLogger(__name__)


def known(what: str) -> bool:
    """
    Whether a code is one the catalog says.

    Args:
        what: The code a record keeps (``firewall_allow``).

    Returns:
        True when there is a sentence for it.
    """
    return f"change.{what}" in MESSAGES


def describe(what: str, values: Mapping[str, str], locale: Locale = "en") -> str:
    """
    Say what a change is.

    Args:
        what: The code of the sentence, one of the ``change.*`` keys of the catalog.
        values: What the sentence puts in (the port, the source, the rules deleted).
        locale: The language.

    Returns:
        The sentence.

    Raises:
        KeyError: ``what`` is not a code of the catalog.
        ValueError: ``values`` lacks one the sentence puts in.
    """
    return message(f"change.{what}", locale, **values)


def describe_record(what: str, values: Mapping[str, str], recorded: str, locale: Locale) -> str:
    """
    Say what a change was, from a record that may predate the code.

    Args:
        what: The code the record keeps; empty for a record from before 4.3.
        values: The values the record keeps.
        recorded: The sentence the record was written with.
        locale: The language.

    Returns:
        The sentence of the catalog in ``locale``; for a record that has no code, or one this
        version does not know (a server that went back), the recorded sentence between quotes.
    """
    if known(what):
        try:
            return describe(what, values, locale)
        except ValueError as exc:
            # A record that names a sentence but not what it puts in is damaged, and says what it can.
            log.warning("A change record has not the values of its sentence %r: %s", what, exc)
    return message("change.recorded", locale, text=recorded)
