# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words Noust says to keep notifications together: the line a message carries when its topic
is gone, the names of the topics it creates, and the noun of a conversation in an email's subject.

They belong to :data:`noust.core.messages.MESSAGES` (which merges this table in, so there is one
catalog and one test of it); they live in their own module so the catalog does not grow a section
per lane. The same rules hold: a whole sentence with placeholders, never a fragment, in both
languages with the same placeholders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.messages import Locale

GROUPING_MESSAGES: dict[str, dict[Locale, str]] = {
    # -- a topic that stopped taking messages -------------------------------------------------
    "topic.lost": {
        "en": (
            "Topic {topic} no longer takes messages in this group, so this one came to the "
            "general thread. Set the topics again in Settings > Notifications."
        ),
        "es": (
            "El tema {topic} ya no admite mensajes en este grupo, así que este ha llegado al "
            "hilo general. Vuelve a configurar los temas en Ajustes > Notificaciones."
        ),
    },
    # -- the topics Noust creates in a group: one per subject ---------------------------------
    "topic.name.deploys": {"en": "Noust: deploys", "es": "Noust: despliegues"},
    "topic.name.alerts": {"en": "Noust: alerts", "es": "Noust: alertas"},
    "topic.name.backups": {"en": "Noust: backups", "es": "Noust: copias"},
    "topic.name.security": {"en": "Noust: security", "es": "Noust: seguridad"},
    "topic.name.system": {"en": "Noust: system", "es": "Noust: sistema"},
    # -- what a thing is, in the subject of its emails: the same in every message about it ---
    "thing.app": {"en": "Application", "es": "Aplicación"},
    "thing.unit": {"en": "Service", "es": "Servicio"},
    "thing.disk": {"en": "Disk", "es": "Disco"},
    "thing.destination": {"en": "Backup destination", "es": "Destino de copias"},
    "thing.node": {"en": "Server", "es": "Servidor"},
    "thing.server": {"en": "Server", "es": "Servidor"},
    "thing.alert": {"en": "Alert", "es": "Alerta"},
    "thing.deploy": {"en": "Deploy", "es": "Despliegue"},
    "thing.backup": {"en": "Backup", "es": "Copia"},
    "thing.restore": {"en": "Restore", "es": "Restauración"},
    "thing.cron": {"en": "Scheduled task", "es": "Tarea programada"},
    "thing.cron_timer": {"en": "Scheduled task", "es": "Tarea programada"},
    "thing.cleanup": {"en": "Cleanup", "es": "Limpieza"},
    "thing.probe": {"en": "Probe", "es": "Sonda"},
}
