# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of the notifications about what ends, and what killed a process.

A certificate that stopped renewing (item 172), the registration of a domain that is about to
lapse (item 173) and an application the kernel killed for running out of memory (item 175). They
belong to :data:`noust.core.messages.MESSAGES` (which merges this table in, so there is one catalog
and one test of it); they live in their own module so the catalog does not grow a section per lane.
The same rules hold: a whole sentence with placeholders, never a fragment, in both languages with
the same placeholders, and nothing here for a program's own words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.messages import Locale

DATES_MESSAGES: dict[str, dict[Locale, str]] = {
    # -- certbot's renewal ----------------------------------------------------------------------
    "title.cert.renewal_failed": {
        "en": "Certificate renewal failed",
        "es": "Ha fallado la renovación de certificados",
    },
    "title.cert.renewal_stopped": {
        "en": "Certificate renewal stopped",
        "es": "La renovación de certificados está parada",
    },
    "summary.cert.renewal_failed": {
        "en": (
            "The last time certbot tried to renew the certificates it failed, so they will not "
            "renew by themselves until it works."
        ),
        "es": (
            "La última vez que certbot intentó renovar los certificados falló, así que no se "
            "renovarán solos hasta que funcione."
        ),
    },
    "summary.cert.renewal_stopped": {
        "en": (
            "The timer that makes certbot renew the certificates is not running, so they will "
            "not renew by themselves."
        ),
        "es": (
            "El temporizador que hace que certbot renueve los certificados no está en marcha, "
            "así que no se renovarán solos."
        ),
    },
    "fact.renewal_last": {"en": "Last attempt", "es": "Último intento"},
    "fact.renewal_next": {"en": "Next attempt", "es": "Próximo intento"},
    "fact.certificates": {"en": "Certificates", "es": "Certificados"},
    "fact.enable_timer": {
        "en": "Start it again with",
        "es": "Vuelve a ponerlo en marcha con",
    },
    "excerpt.cert.renewal": {
        "en": "What certbot reported",
        "es": "Lo que ha informado certbot",
    },
    # -- the kernel killed an application for running out of memory ----------------------------
    "title.app.oom_killed": {
        "en": "Application killed for lack of memory",
        "es": "Aplicación cerrada por falta de memoria",
    },
    "summary.app.oom_killed": {
        "en": "The kernel killed {domain} for running out of memory.",
        "es": "El núcleo ha cerrado {domain} por falta de memoria.",
    },
    "fact.oom_kills": {"en": "Killed", "es": "Cerrada"},
    "fact.oom_limit": {"en": "Memory limit", "es": "Límite de memoria"},
    "fact.oom_peak": {"en": "Most it used", "es": "Lo máximo que usó"},
    "fact.oom_since": {"en": "Counting since", "es": "Contando desde"},
    "fact.oom_none": {"en": "none", "es": "ninguno"},
}
