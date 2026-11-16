# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a change to the firewall or to SSH was, as sentences the console can say (item 151).

A change is recorded with what it is: a code (``firewall_allow``) and its values (the port, the
source). The notification says it in the language of whoever reads it from the table of
:mod:`noust.core.messages_access`, and the console's page of pending changes and the banner that
counts one down must say the same words. This is those sentences, in English, as sentences of
:mod:`noust.core.server_text`: they are *derived* from the notifications' table, so the English
exists once (rule 3), and ``tests/test_access_changes_coded.py`` holds the console's Spanish to the
Spanish of the notification.
"""

from __future__ import annotations

from noust.core.messages_access import ACCESS_MESSAGES

#: The prefix of a change's sentences in the catalog (``access_changes.firewall_allow``).
PREFIX = "access_changes."

#: A change recorded before the ledger kept what it was has only the sentence somebody wrote then, which is
#: the system's own text and is not one of the catalog's.
_NOT_A_SENTENCE = "change.recorded"

SENTENCES: dict[str, str] = {
    PREFIX + key.removeprefix("change."): texts["en"]
    for key, texts in ACCESS_MESSAGES.items()
    if key.startswith("change.") and key != _NOT_A_SENTENCE
}
