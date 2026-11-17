# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of the notice about copies that could not be sent to a destination, and of the one that says
they arrived (item 201).

They belong to :data:`noust.core.messages.MESSAGES` (which merges this table in, so there is one
catalog and one test of it); they live in their own module so the catalog does not grow a section per
lane. The same rules hold: a whole sentence with placeholders, never a fragment, in both languages with
the same placeholders. The sentences carry no count: how many copies there are is a fact, so the verb
does not have to agree with a number in either language.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.messages import Locale

SENDS_MESSAGES: dict[str, dict[Locale, str]] = {
    "title.backup.sends_failed": {
        "en": "Backups not delivered",
        "es": "Copias sin entregar",
    },
    "summary.backup.sends_failed": {
        "en": (
            "The copies could not be sent and stay on this server. Noust sends them again by "
            "itself, and says so when they arrive."
        ),
        "es": (
            "Las copias no se han podido enviar y siguen en este servidor. Noust las vuelve a "
            "enviar solo y avisa cuando lleguen."
        ),
    },
    "title.backup.sends_recovered": {
        "en": "Backups delivered",
        "es": "Copias entregadas",
    },
    "summary.backup.sends_recovered": {
        "en": "Every copy that was waiting has reached the destination.",
        "es": "Todas las copias que esperaban han llegado al destino.",
    },
    "fact.copies": {"en": "Copies", "es": "Copias"},
    "fact.tried": {"en": "Tried", "es": "Intentos"},
    "fact.next_try": {"en": "Next try", "es": "Próximo intento"},
    "fact.kept_in": {"en": "Kept in", "es": "Guardadas en"},
    "fact.send_now": {"en": "Send them now with", "es": "Envíalas ahora con"},
    "ui.and_more": {"en": "and {count} more", "es": "y {count} más"},
}
