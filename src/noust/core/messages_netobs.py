# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of the notifications the network and monitoring lane added.

A web server that fell and was started again, a release that fixes a vulnerability, a container
that restarts in a loop, and a recipe with a newer version. They belong to
:data:`noust.core.messages.MESSAGES` (which merges this table in, so there is one catalog and one
test of it); they live in their own module so the catalog does not grow a section per lane. The
same rules hold: a whole sentence with placeholders, never a fragment, in both languages with the
same placeholders, and nothing here for a program's own words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.messages import Locale

NETOBS_MESSAGES: dict[str, dict[Locale, str]] = {
    # -- the web server ---------------------------------------------------------------------
    "title.webserver.restarted": {
        "en": "Web server restarted",
        "es": "Servidor web reiniciado",
    },
    "title.webserver.down": {"en": "Web server down", "es": "Servidor web caído"},
    "title.webserver.config_invalid": {
        "en": "Web server cannot start",
        "es": "El servidor web no puede arrancar",
    },
    "title.webserver.restart_failed": {
        "en": "Web server restart failed",
        "es": "Ha fallado el reinicio del servidor web",
    },
    "title.webserver.limit": {
        "en": "Web server keeps falling",
        "es": "El servidor web sigue cayendo",
    },
    "title.webserver.stopped": {"en": "Web server stopped", "es": "Servidor web detenido"},
    "summary.webserver.restarted": {
        "en": "It had stopped working and Noust started it again once its configuration test passed.",
        "es": (
            "Había dejado de funcionar y Noust lo ha arrancado de nuevo tras pasar su prueba de "
            "configuración."
        ),
    },
    "summary.webserver.down": {
        "en": "It is not running, and the automatic restart is switched off.",
        "es": "No está en marcha y el reinicio automático está desactivado.",
    },
    "summary.webserver.config_invalid": {
        "en": (
            "It is not running and its configuration does not pass its own test, so Noust did "
            "not restart it."
        ),
        "es": (
            "No está en marcha y su configuración no pasa su propia prueba, así que Noust no lo "
            "ha reiniciado."
        ),
    },
    "summary.webserver.restart_failed": {
        "en": "It is not running and starting it again did not work.",
        "es": "No está en marcha y arrancarlo de nuevo no ha funcionado.",
    },
    "summary.webserver.limit": {
        "en": (
            "It has fallen {count} {times} in the last hour, so Noust stopped starting it again."
        ),
        "es": (
            "Ha caído {count} {times} en la última hora, así que Noust ha dejado de arrancarlo de "
            "nuevo."
        ),
    },
    "summary.webserver.stopped": {
        "en": (
            "It is stopped but starts at boot, so every site on this server is down until "
            "somebody starts it."
        ),
        "es": (
            "Está detenido pero arranca con el sistema, así que todos los sitios de este "
            "servidor están caídos hasta que alguien lo arranque."
        ),
    },
    "fact.start": {"en": "Start it with", "es": "Arráncalo con"},
    # -- a release that fixes a vulnerability -------------------------------------------------
    "title.security.release": {
        "en": "Security release available",
        "es": "Versión de seguridad disponible",
    },
    "summary.security.release": {
        "en": "Noust {version} fixes a vulnerability and this server still runs {current}.",
        "es": "Noust {version} corrige una vulnerabilidad y este servidor sigue en la versión {current}.",
    },
    "fact.running": {"en": "Running", "es": "En marcha"},
    "fact.fixed_in": {"en": "Fixed in", "es": "Corregida en"},
    "fact.advisory": {"en": "Advisory", "es": "Aviso"},
    "fact.update_with": {"en": "Update with", "es": "Actualiza con"},
    # -- a container restarting in a loop -----------------------------------------------------
    "title.container.restart_loop": {
        "en": "Container restarting in a loop",
        "es": "Contenedor reiniciándose en bucle",
    },
    "title.container.recovered": {
        "en": "Container stable again",
        "es": "Contenedor estable de nuevo",
    },
    "summary.container.restart_loop": {
        "en": (
            "Docker has restarted it {count} {times} since it was first seen, and the limit is "
            "{limit}."
        ),
        "es": (
            "Docker lo ha reiniciado {count} {times} desde que se vio por primera vez, y el "
            "límite es {limit}."
        ),
    },
    "summary.container.recovered": {
        "en": "It has run for {minutes} minutes without restarting.",
        "es": "Lleva {minutes} minutos en marcha sin reiniciarse.",
    },
    "fact.container": {"en": "Container", "es": "Contenedor"},
    "fact.restart_limit": {"en": "Limit", "es": "Límite"},
    "fact.exit_code": {"en": "Last exit code", "es": "Último código de salida"},
    "fact.recipe": {"en": "Recipe", "es": "Receta"},
    "fact.more": {"en": "Not listed", "es": "Sin listar"},
    # -- a recipe with a newer version --------------------------------------------------------
    "title.recipe.outdated": {
        "en": "Recipe updates available",
        "es": "Actualizaciones de recetas disponibles",
    },
    "summary.recipe.outdated": {
        "en": "{count} installed {recipes} have a newer version in the catalogue.",
        "es": "{count} {recipes} instaladas tienen una versión más nueva en el catálogo.",
    },
}

#: Nouns the new sentences count; merged into :data:`noust.core.messages._PLURAL_FORMS`.
NETOBS_PLURALS: dict[str, dict[Locale, tuple[str, str]]] = {
    "recipe": {"en": ("recipe", "recipes"), "es": ("receta", "recetas")},
}
