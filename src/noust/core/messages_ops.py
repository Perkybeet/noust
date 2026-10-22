# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of the notifications the 4.0 monitor added.

User-defined alert rules, a scheduled backup that did not run, a cron job that
failed, operating system updates waiting, an API token about to expire, and the
digest of what the quiet hours held. They belong to
:data:`noust.core.messages.MESSAGES` (which merges this table in, so there is
one catalog and one test of it); they live in their own module so the catalog
does not grow a section per release. The same rules hold: a whole sentence with
placeholders, never a fragment, in both languages with the same placeholders,
and nothing here for a program's own words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noust.core.messages import Locale

OPS_MESSAGES: dict[str, dict[Locale, str]] = {
    # -- titles -------------------------------------------------------------
    "title.alert.above": {"en": "Alert firing", "es": "Alerta activa"},
    "title.alert.below": {"en": "Alert firing", "es": "Alerta activa"},
    "title.alert.resolved": {"en": "Alert resolved", "es": "Alerta resuelta"},
    "title.alert.acknowledged": {"en": "Alert taken", "es": "Alerta atendida"},
    "title.backup.missing": {"en": "No recent backup", "es": "Sin copia reciente"},
    "title.backup.never": {"en": "Never backed up", "es": "Aún sin copias"},
    "title.cron.failed": {"en": "Scheduled task failed", "es": "Tarea programada fallida"},
    "title.cron.succeeded": {"en": "Scheduled task ran", "es": "Tarea programada ejecutada"},
    "title.patches.available": {"en": "Updates available", "es": "Actualizaciones disponibles"},
    "title.patches.security": {
        "en": "Security updates available",
        "es": "Actualizaciones de seguridad disponibles",
    },
    "title.patches.check_failed": {
        "en": "Update check failed",
        "es": "Fallo al comprobar las actualizaciones",
    },
    "title.token.expiring": {
        "en": "API token expiring",
        "es": "Token de API a punto de caducar",
    },
    "title.token.today": {
        "en": "API token expires today",
        "es": "El token de API caduca hoy",
    },
    "title.token.expired": {"en": "API token expired", "es": "Token de API caducado"},
    "title.digest.held": {"en": "Held notifications", "es": "Avisos retenidos"},
    "title.digest.flood": {"en": "Notifications held back", "es": "Avisos contenidos"},
    "title.digest.summary": {"en": "Scheduled summary", "es": "Resumen programado"},
    "title.cleanup.completed": {"en": "Disk cleaned", "es": "Disco limpiado"},
    "title.cleanup.partial": {
        "en": "Disk cleanup partly failed",
        "es": "La limpieza del disco ha fallado en parte",
    },
    "title.cleanup.failed": {
        "en": "Disk cleanup failed",
        "es": "Ha fallado la limpieza del disco",
    },
    "title.cron.timer_stopped": {
        "en": "Scheduled task will not run",
        "es": "La tarea programada no se ejecutará",
    },
    "title.cron.timer_running": {
        "en": "Scheduled task runs again",
        "es": "La tarea programada vuelve a ejecutarse",
    },
    "title.probe.failed": {"en": "Address not answering", "es": "Dirección sin respuesta"},
    "title.probe.recovered": {
        "en": "Address answering again",
        "es": "La dirección vuelve a responder",
    },
    # -- summaries ------------------------------------------------------------
    "summary.alert.above": {
        "en": "{metric} has stayed above {threshold} for {duration}.",
        "es": "{metric} lleva por encima de {threshold} durante {duration}.",
    },
    "summary.alert.below": {
        "en": "{metric} has stayed below {threshold} for {duration}.",
        "es": "{metric} lleva por debajo de {threshold} durante {duration}.",
    },
    "summary.alert.acknowledged": {
        "en": "{who} is looking into it: {metric} is still beyond the {threshold} limit.",
        "es": "{who} se ocupa: {metric} sigue fuera del límite de {threshold}.",
    },
    "summary.alert.resolved": {
        "en": "{metric} is back to {value}, inside the {threshold} limit.",
        "es": "{metric} ha vuelto a {value}, dentro del límite de {threshold}.",
    },
    "summary.backup.missing": {
        "en": (
            "No backup of this application was made in the last {days} {unit}, although a "
            "backup schedule is set. Nothing failed: nothing ran."
        ),
        "es": (
            "No se ha hecho ninguna copia de esta aplicación en los últimos {days} {unit}, "
            "aunque hay una programación de copias. Nada ha fallado: nada se ha ejecutado."
        ),
    },
    "summary.backup.never": {
        "en": (
            "A backup schedule is set, but not one backup of this application exists. "
            "Nothing failed: nothing ran."
        ),
        "es": (
            "Hay una programación de copias, pero no existe ninguna de esta aplicación. "
            "Nada ha fallado: nada se ha ejecutado."
        ),
    },
    "summary.cron.failed": {
        "en": "The task ended with an error, so whatever it does was not done.",
        "es": "La tarea ha terminado con un error, así que lo que hace no se ha hecho.",
    },
    "summary.cron.succeeded": {
        "en": "The task ran and ended without an error.",
        "es": "La tarea se ha ejecutado y ha terminado sin error.",
    },
    "summary.patches.available": {
        "en": "{count} {unit} waiting to be installed.",
        "es": "{count} {unit} esperando a instalarse.",
    },
    "summary.patches.security": {
        "en": "{security} of the {count} {unit} waiting are security updates.",
        "es": "{security} de las {count} {unit} pendientes son de seguridad.",
    },
    "summary.patches.check_failed": {
        "en": "Noust could not list the updates this server is waiting for.",
        "es": "Noust no ha podido listar las actualizaciones que espera este servidor.",
    },
    "summary.token.expiring": {
        "en": "The token stops working in {days} {unit}. Whatever uses it will be refused.",
        "es": "El token dejará de funcionar dentro de {days} {unit}. Lo que lo use será rechazado.",
    },
    "summary.token.today": {
        "en": "The token stops working today. Whatever uses it will be refused.",
        "es": "El token deja de funcionar hoy. Lo que lo use será rechazado.",
    },
    "summary.token.expired": {
        "en": "The token has expired. Whatever still uses it is being refused.",
        "es": "El token ha caducado. Lo que todavía lo use está siendo rechazado.",
    },
    "summary.cleanup.completed": {
        "en": "The server's cleanup policy ran and freed space.",
        "es": "La política de limpieza del servidor se ha ejecutado y ha liberado espacio.",
    },
    "summary.cleanup.partial": {
        "en": "The cleanup policy ran, but some of its actions failed.",
        "es": "La política de limpieza se ha ejecutado, pero algunas de sus acciones han fallado.",
    },
    "summary.cleanup.failed": {
        "en": "The cleanup policy ran and every one of its actions failed, so the disk is as it was.",
        "es": (
            "La política de limpieza se ha ejecutado y todas sus acciones han fallado, "
            "así que el disco sigue como estaba."
        ),
    },
    "summary.cron.timer_stopped": {
        "en": (
            "The task is enabled but its timer is not running, so it will not start. "
            "Nothing failed: nothing runs."
        ),
        "es": (
            "La tarea está activada pero su temporizador no está en marcha, así que no "
            "arrancará. Nada ha fallado: nada se ejecuta."
        ),
    },
    "summary.cron.timer_running": {
        "en": "The task's timer is running again.",
        "es": "El temporizador de la tarea vuelve a estar en marcha.",
    },
    "summary.probe.failed": {
        "en": "The address stopped answering what the probe asks of it.",
        "es": "La dirección ha dejado de responder lo que le pide la sonda.",
    },
    "summary.probe.recovered": {
        "en": "The address answers what the probe asks of it again.",
        "es": "Contesta otra vez a lo que le pide la sonda.",
    },
    "summary.digest.flood": {
        "en": "{count} notifications passed this channel's limit and were held back until now.",
        "es": "{count} avisos pasaron el límite de este canal y se contuvieron hasta ahora.",
    },
    "summary.digest.summary": {
        "en": "{count} notifications since the last summary.",
        "es": "{count} avisos desde el último resumen.",
    },
    "summary.digest.held": {
        "en": "{count} notifications arrived while this channel was quiet.",
        "es": "Han llegado {count} avisos mientras este canal estaba en silencio.",
    },
    # -- fact labels ----------------------------------------------------------
    "fact.rule": {"en": "Rule", "es": "Regla"},
    "fact.metric": {"en": "Measures", "es": "Mide"},
    "fact.value": {"en": "Now", "es": "Ahora"},
    "fact.threshold": {"en": "Limit", "es": "Límite"},
    "fact.firing_for": {"en": "Firing for", "es": "Activa desde hace"},
    "fact.acknowledged_by": {"en": "Taken by", "es": "Atendida por"},
    "fact.last_backup": {"en": "Last backup", "es": "Última copia"},
    "fact.schedule": {"en": "Schedule", "es": "Programación"},
    "fact.job": {"en": "Task", "es": "Tarea"},
    "fact.last_run": {"en": "Ran", "es": "Ejecutada"},
    "fact.packages": {"en": "Updates", "es": "Actualizaciones"},
    "fact.security": {"en": "Security", "es": "Seguridad"},
    "fact.reboot": {"en": "Reboot", "es": "Reinicio"},
    "fact.token": {"en": "API token", "es": "Token de API"},
    "fact.expires": {"en": "Expires", "es": "Caduca"},
    "fact.owner": {"en": "Owner", "es": "Propietario"},
    "fact.events": {"en": "Notifications", "es": "Avisos"},
    "fact.period": {"en": "Between", "es": "Entre"},
    "fact.list": {"en": "List them", "es": "Verlas"},
    "fact.revoke": {"en": "Replace it", "es": "Sustituirlo"},
    "fact.backup_now": {"en": "Back it up now", "es": "Copiarla ahora"},
    "fact.runs": {"en": "See the runs", "es": "Ver las ejecuciones"},
    "fact.when": {"en": "When", "es": "Cuándo"},
    "fact.freed": {"en": "Freed", "es": "Liberado"},
    "fact.actions": {"en": "Actions", "es": "Acciones"},
    "fact.cleanup_runs": {"en": "See what ran", "es": "Ver lo ejecutado"},
    "fact.timer": {"en": "Timer", "es": "Temporizador"},
    "fact.enable_job": {"en": "Start it again with", "es": "Actívala de nuevo con"},
    "fact.probe_run": {"en": "Probe it now with", "es": "Pruébala ahora con"},
    "fact.latency": {"en": "Answered in", "es": "Respondió en"},
    # -- the excerpt's labels --------------------------------------------------
    "excerpt.cleanup.error": {
        "en": "What {action} reported",
        "es": "Lo que ha dicho {action}",
    },
    "excerpt.probe.reply": {
        "en": "What came back",
        "es": "Lo que ha respondido",
    },
    "excerpt.cron.output": {
        "en": "Last lines of the output of {job}",
        "es": "Últimas líneas de la salida de {job}",
    },
    # -- the public status page -----------------------------------------------
    "ui.status.operational": {
        "en": "All systems operational",
        "es": "Todos los sistemas funcionan",
    },
    "ui.status.degraded": {
        "en": "Some systems are not answering",
        "es": "Algunos sistemas no responden",
    },
    "ui.status.outage": {"en": "Major outage", "es": "Interrupción general"},
    "ui.status.unknown": {"en": "No data yet", "es": "Aún sin datos"},
    "ui.status.component.up": {"en": "Operational", "es": "Operativo"},
    "ui.status.component.down": {"en": "Not answering", "es": "Sin respuesta"},
    "ui.status.component.unknown": {"en": "Waiting for data", "es": "Esperando datos"},
    "ui.status.uptime": {
        "en": "{percent}% answered over {days} days",
        "es": "{percent}% de respuestas en {days} días",
    },
    "ui.status.history": {"en": "Last {days} days", "es": "Últimos {days} días"},
    "ui.status.incidents": {"en": "Recent incidents", "es": "Incidencias recientes"},
    "ui.status.ongoing": {"en": "ongoing", "es": "en curso"},
    "ui.status.updated": {"en": "Updated {when}", "es": "Actualizado {when}"},
    "ui.status.nothing": {
        "en": "Nothing is published here yet.",
        "es": "Aún no se publica nada aquí.",
    },
    "ui.status.no_data": {"en": "no data", "es": "sin datos"},
    "ui.status.day": {"en": "{percent}% answered", "es": "{percent}% de respuestas"},
    # -- words around the parts every channel draws ------------------------
    "ui.reboot_pending": {
        "en": "Pending, after the updates",
        "es": "Pendiente, tras las actualizaciones",
    },
    "ui.metric.cpu": {"en": "CPU of the server", "es": "CPU del servidor"},
    "ui.metric.memory": {"en": "Memory of the server", "es": "Memoria del servidor"},
    "ui.metric.swap": {"en": "Swap of the server", "es": "Swap del servidor"},
    "ui.metric.disk": {"en": "Disk of the server", "es": "Disco del servidor"},
    "ui.metric.load": {"en": "Load of the server", "es": "Carga del servidor"},
    "ui.metric.net_rx": {
        "en": "Network received by the server",
        "es": "Red recibida por el servidor",
    },
    "ui.metric.net_tx": {"en": "Network sent by the server", "es": "Red enviada por el servidor"},
    "ui.metric.app_cpu": {"en": "CPU of {app}", "es": "CPU de {app}"},
    "ui.metric.app_memory": {"en": "Memory of {app}", "es": "Memoria de {app}"},
    "ui.metric.app_requests": {
        "en": "Requests per minute of {app}",
        "es": "Peticiones por minuto de {app}",
    },
    "ui.metric.app_5xx": {
        "en": "5xx responses per minute of {app}",
        "es": "Respuestas 5xx por minuto de {app}",
    },
    "ui.metric.app_disk_read": {"en": "Disk reads of {app}", "es": "Lecturas de disco de {app}"},
    "ui.metric.app_disk_write": {
        "en": "Disk writes of {app}",
        "es": "Escrituras de disco de {app}",
    },
    "ui.metric.app_net_rx": {"en": "Network received by {app}", "es": "Red recibida por {app}"},
    "ui.metric.app_net_tx": {"en": "Network sent by {app}", "es": "Red enviada por {app}"},
    # -- what the channels are called -----------------------------------------
    "channel.pushover": {"en": "Pushover", "es": "Pushover"},
    "channel.ntfy": {"en": "ntfy", "es": "ntfy"},
    "channel.gotify": {"en": "Gotify", "es": "Gotify"},
    "channel.teams": {"en": "Microsoft Teams", "es": "Microsoft Teams"},
    "channel.mattermost": {"en": "Mattermost", "es": "Mattermost"},
    "channel.matrix": {"en": "Matrix", "es": "Matrix"},
    "channel.lark": {"en": "Lark", "es": "Lark"},
    "channel.pagerduty": {"en": "PagerDuty", "es": "PagerDuty"},
}

#: Nouns the new sentences count; merged into :data:`noust.core.messages._PLURAL_FORMS`.
OPS_PLURALS: dict[str, dict[Locale, tuple[str, str]]] = {
    "package": {"en": ("update", "updates"), "es": ("actualización", "actualizaciones")},
}
