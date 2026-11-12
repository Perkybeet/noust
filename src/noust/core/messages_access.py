# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of the notification about a change to how the server is reached that was undone (item 138).

A change to the firewall or to SSH waits for somebody to confirm it from a new session and is undone
by a timer when nobody does. They belong to :data:`noust.core.messages.MESSAGES` (which merges this
table in, so there is one catalog and one test of it); they live in their own module so the catalog
does not grow a section per lane. The same rules hold: a whole sentence with placeholders, never a
fragment, in both languages with the same placeholders, and nothing here for a program's own words
(what the undo printed travels as an excerpt).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.messages import Locale

ACCESS_MESSAGES: dict[str, dict[Locale, str]] = {
    "title.access.firewall_reverted": {
        "en": "Firewall change undone",
        "es": "Cambio del cortafuegos deshecho",
    },
    "title.access.ssh_reverted": {
        "en": "SSH change undone",
        "es": "Cambio de SSH deshecho",
    },
    "summary.access.firewall_reverted": {
        "en": (
            "The firewall change “{change}” was undone because nobody confirmed it within "
            "{minutes} {unit}."
        ),
        "es": (
            "El cambio del cortafuegos «{change}» se deshizo porque nadie lo confirmó en "
            "{minutes} {unit}."
        ),
    },
    "summary.access.ssh_reverted": {
        "en": (
            "The SSH change “{change}” was undone because nobody confirmed it within "
            "{minutes} {unit}."
        ),
        "es": (
            "El cambio de SSH «{change}» se deshizo porque nadie lo confirmó en {minutes} {unit}."
        ),
    },
    "fact.change": {"en": "Change", "es": "Cambio"},
    "fact.change_by": {"en": "Applied by", "es": "Lo aplicó"},
    "fact.change_at": {"en": "Applied", "es": "Aplicado"},
    "excerpt.access.undo": {"en": "What the undo printed", "es": "Lo que imprimió al deshacerlo"},
}

#: Nouns the new sentences count; merged into :data:`noust.core.messages._PLURAL_FORMS`.
ACCESS_PLURALS: dict[str, dict[Locale, tuple[str, str]]] = {
    "minute": {"en": ("minute", "minutes"), "es": ("minuto", "minutos")},
}
