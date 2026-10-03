# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A typed catalog of Noust's own words in every notification Noust sends.

An operator reads a deploy failure or a disk warning in English or Spanish
(``notifications.language``, see :data:`noust.core.config.DEFAULT_CONFIG`).
This module is the one place that pairing lives: every phrase a notification
carries is a key here, in both locales, a whole sentence with placeholders and
never a fragment to glue to another one, because word order is not the same
sentence in both languages.

The keys are grouped by what they are, and the composers
(:mod:`noust.core.notifications.composers`) are the only callers that know
which belongs where:

- ``title.<code>``: the state as a phrase (``Rolled back``), first thing read;
- ``summary.<code>``: the one sentence under it;
- ``fact.<key>``: the label of a fact (``Started by``);
- ``trigger.<name>`` and ``channel.<name>``: what an enum value is called;
- ``ui.*`` and ``excerpt.*``: the words around the parts every channel draws.

What this catalog is not for: the evidence inside a notification - a health
gate's probes, a journal line, rclone's or certbot's own stderr, a
:class:`NoustError` message, a branch name - is never a value here. That text
stays as the program wrote it and reaches the operator verbatim, exactly as the
console shows it, because paraphrasing another program's own words is how an
operator stops trusting what Noust tells them. A composer passes it in as a
``str.format`` parameter, or, more often, as a fact value or an excerpt line
that no catalog sentence wraps at all.

The CLI and every other server-generated string are out of scope: this
catalog covers notifications only.
"""

from __future__ import annotations

from typing import Any, Literal

#: The two locales a notification can be rendered in. Config validates
#: ``notifications.language`` against exactly these two spellings.
Locale = Literal["en", "es"]

#: What an unset or unrecognised ``notifications.language`` falls back to.
DEFAULT_LOCALE: Locale = "en"

_GENERIC_FAILURE: dict[Locale, str] = {
    "en": "It stopped at an error before the new version could take over.",
    "es": "Se ha detenido por un error antes de que la nueva versión tomara el relevo.",
}

_ROLLED_BACK: dict[Locale, str] = {
    "en": "The new version did not answer its health check, so the previous one is serving again.",
    "es": (
        "La nueva versión no ha respondido a la comprobación de salud, así que la anterior "
        "vuelve a estar en servicio."
    ),
}

#: Every notification text Noust builds, keyed by a short name and then by
#: locale. ``tests/test_messages.py`` enforces the invariant a Python dict
#: cannot: both locales define exactly the same keys, with exactly the same
#: ``str.format`` placeholders, and neither text is empty.
MESSAGES: dict[str, dict[Locale, str]] = {
    # -- Titles: the state, as a phrase. Never the subject, never a fact. -----
    "title.deploy.started": {"en": "Deploying", "es": "Desplegando"},
    "title.deploy.succeeded": {"en": "Deployed", "es": "Desplegado"},
    "title.deploy.failed": {"en": "Deploy failed", "es": "Despliegue fallido"},
    "title.deploy.rolled_back": {"en": "Rolled back", "es": "Revertido"},
    "title.deploy.hook_failed": {"en": "Deployed with warnings", "es": "Desplegado con avisos"},
    "title.update.started": {"en": "Updating", "es": "Actualizando"},
    "title.update.succeeded": {"en": "Updated", "es": "Actualizado"},
    "title.update.failed": {"en": "Update failed", "es": "Actualización fallida"},
    "title.update.rolled_back": {"en": "Rolled back", "es": "Revertido"},
    "title.rollback.started": {"en": "Reverting", "es": "Volviendo atrás"},
    "title.rollback.succeeded": {"en": "Reverted", "es": "Vuelta atrás completada"},
    "title.rollback.failed": {"en": "Revert failed", "es": "Vuelta atrás fallida"},
    "title.rollback.rolled_back": {"en": "Revert failed", "es": "Vuelta atrás fallida"},
    "title.activate.started": {"en": "Activating release", "es": "Activando versión"},
    "title.activate.succeeded": {"en": "Release activated", "es": "Versión activada"},
    "title.activate.failed": {"en": "Activation failed", "es": "Activación fallida"},
    "title.activate.rolled_back": {"en": "Activation failed", "es": "Activación fallida"},
    "title.migrate.started": {"en": "Migrating to releases", "es": "Migrando a versiones"},
    "title.migrate.succeeded": {"en": "Migrated to releases", "es": "Migrado a versiones"},
    "title.migrate.failed": {"en": "Migration failed", "es": "Migración fallida"},
    "title.restore.completed": {"en": "Restored", "es": "Restaurado"},
    "title.restore.failed": {"en": "Restore failed", "es": "Restauración fallida"},
    "title.backup.failed": {"en": "Backup failed", "es": "Copia de seguridad fallida"},
    "title.backup.upload_failed": {
        "en": "Backup upload failed",
        "es": "Subida de la copia fallida",
    },
    "title.backup.schedule_missing": {
        "en": "Backup schedule missing",
        "es": "Falta la programación de la copia",
    },
    "title.backup.completed": {"en": "Backup completed", "es": "Copia completada"},
    "title.cert.expiring": {
        "en": "Certificate expiring",
        "es": "Certificado a punto de caducar",
    },
    "title.cert.expired": {"en": "Certificate expired", "es": "Certificado caducado"},
    "title.unit.failed": {"en": "Service failed", "es": "Servicio fallido"},
    "title.unit.crash_loop": {
        "en": "Service crash-looping",
        "es": "Servicio en bucle de reinicios",
    },
    "title.unit.stopped_on_failure": {
        "en": "Service stopped on a failure",
        "es": "Servicio detenido por un fallo",
    },
    "title.unit.recovered": {"en": "Service recovered", "es": "Servicio recuperado"},
    "title.app.unreachable": {"en": "Application not answering", "es": "Aplicación sin respuesta"},
    "title.app.recovered": {
        "en": "Application answering again",
        "es": "Aplicación de nuevo operativa",
    },
    "title.app.outside_unit": {
        "en": "Application running outside Noust",
        "es": "Aplicación en marcha fuera de Noust",
    },
    "title.disk.threshold": {"en": "Disk almost full", "es": "Disco casi lleno"},
    "title.disk.recovered": {
        "en": "Disk space recovered",
        "es": "Espacio en disco recuperado",
    },
    "title.test": {"en": "Test notification", "es": "Notificación de prueba"},
    "title.node.unreachable": {"en": "Server unreachable", "es": "Servidor inaccesible"},
    "title.node.recovered": {
        "en": "Server reachable again",
        "es": "Servidor accesible de nuevo",
    },
    "title.node.host_key_changed": {"en": "Host key changed", "es": "Clave de host cambiada"},
    "title.server.rebooted": {"en": "Server restarted", "es": "Servidor reiniciado"},
    "title.server.back": {"en": "Server back", "es": "Servidor de vuelta"},
    "title.approval.requested": {"en": "Approval requested", "es": "Aprobación solicitada"},
    "title.approval.approved": {"en": "Request approved", "es": "Solicitud aprobada"},
    "title.approval.rejected": {"en": "Request rejected", "es": "Solicitud rechazada"},
    "title.sandbox.trial_failed": {
        "en": "Sandbox build failed",
        "es": "Compilación aislada fallida",
    },
    "title.database.backup.failed": {
        "en": "Backup failed",
        "es": "Copia de seguridad fallida",
    },
    "title.database.backup.upload_failed": {
        "en": "Backup upload failed",
        "es": "Subida de la copia fallida",
    },
    "title.database.backup.completed": {"en": "Backup completed", "es": "Copia completada"},
    "title.database.restore.completed": {"en": "Restored", "es": "Restaurado"},
    "title.database.restore.failed": {"en": "Restore failed", "es": "Restauración fallida"},
    "title.report.observations": {
        "en": "Process observations",
        "es": "Observaciones de procesos",
    },
    # -- Summaries: one sentence that repeats neither the title nor a fact. ---
    "summary.deploy.started": {
        "en": "Noust is building the new version and will tell you how it ends.",
        "es": "Noust está construyendo la nueva versión y te avisará de cómo termina.",
    },
    "summary.deploy.succeeded": {
        "en": "The new version is live.",
        "es": "La nueva versión ya está en servicio.",
    },
    "summary.deploy.failed": _GENERIC_FAILURE,
    "summary.deploy.rolled_back": _ROLLED_BACK,
    "summary.deploy.hook_failed": {
        "en": "The new version is serving, but a post-deploy hook failed once it took over.",
        "es": (
            "La nueva versión está en servicio, pero un gancho posterior al despliegue ha "
            "fallado después del relevo."
        ),
    },
    "summary.update.started": {
        "en": "Noust is fetching and building the latest code and will tell you how it ends.",
        "es": (
            "Noust está descargando y construyendo el código más reciente y te avisará de "
            "cómo termina."
        ),
    },
    "summary.update.succeeded": {
        "en": "The application runs the new version.",
        "es": "La aplicación ya ejecuta la nueva versión.",
    },
    "summary.update.failed": _GENERIC_FAILURE,
    "summary.update.rolled_back": _ROLLED_BACK,
    "summary.rollback.started": {
        "en": "Noust is putting an earlier version back and will tell you how it ends.",
        "es": "Noust está volviendo a una versión anterior y te avisará de cómo termina.",
    },
    "summary.rollback.succeeded": {
        "en": "The earlier version is serving again.",
        "es": "La versión anterior vuelve a estar en servicio.",
    },
    "summary.rollback.failed": {
        "en": "Going back stopped at an error before the earlier version could take over.",
        "es": (
            "La vuelta atrás se ha detenido por un error antes de que la versión anterior "
            "tomara el relevo."
        ),
    },
    "summary.rollback.rolled_back": {
        "en": (
            "The earlier version did not answer its health check, so the one serving before "
            "is serving again."
        ),
        "es": (
            "La versión anterior no ha respondido a la comprobación de salud, así que vuelve "
            "a estar en servicio la que lo estaba antes."
        ),
    },
    "summary.activate.started": {
        "en": "Noust is switching to the chosen release and will tell you how it ends.",
        "es": "Noust está cambiando a la versión elegida y te avisará de cómo termina.",
    },
    "summary.activate.succeeded": {
        "en": "The chosen release is serving.",
        "es": "La versión elegida está en servicio.",
    },
    "summary.activate.failed": {
        "en": "Switching stopped at an error before the chosen release could take over.",
        "es": (
            "El cambio se ha detenido por un error antes de que la versión elegida tomara "
            "el relevo."
        ),
    },
    "summary.activate.rolled_back": {
        "en": (
            "The chosen release did not answer its health check, so the previous one is "
            "serving again."
        ),
        "es": (
            "La versión elegida no ha respondido a la comprobación de salud, así que la "
            "anterior vuelve a estar en servicio."
        ),
    },
    "summary.migrate.started": {
        "en": "Noust is moving the application to releases and will tell you how it ends.",
        "es": "Noust está pasando la aplicación a versiones y te avisará de cómo termina.",
    },
    "summary.migrate.succeeded": {
        "en": "Every deploy now builds in its own release, and going back takes seconds.",
        "es": (
            "Cada despliegue se construye ahora en su propia versión y volver atrás lleva segundos."
        ),
    },
    "summary.migrate.failed": {
        "en": "It stopped at an error; Noust puts the in-place layout back when that happens.",
        "es": "Se ha detenido por un error; en ese caso Noust restaura la disposición anterior.",
    },
    "summary.restore.completed": {
        "en": "The application's files are back as the backup had them.",
        "es": ("Los archivos de la aplicación han vuelto a como estaban en la copia de seguridad."),
    },
    "summary.restore.failed": {
        "en": "It stopped at an error, so the application's files may be only partly restored.",
        "es": (
            "Se ha detenido por un error, así que los archivos de la aplicación podrían estar "
            "restaurados solo en parte."
        ),
    },
    "summary.backup.failed": {
        "en": "No new copy of the application was saved.",
        "es": "No se ha guardado ninguna copia nueva de la aplicación.",
    },
    "summary.backup.upload_failed": {
        "en": "The copy was kept on this server but could not be uploaded.",
        "es": "La copia se ha guardado en este servidor, pero no se ha podido subir.",
    },
    "summary.backup.schedule_missing": {
        "en": (
            "The timer fired but Noust has no schedule stored for this application, so the "
            "backup ran with the default settings."
        ),
        "es": (
            "El temporizador se ha activado, pero Noust no tiene una programación guardada "
            "para esta aplicación, así que la copia se ha hecho con los ajustes por defecto."
        ),
    },
    "summary.backup.completed": {
        "en": "A new copy of the application was saved.",
        "es": "Se ha guardado una copia nueva de la aplicación.",
    },
    "summary.cert.expiring": {
        "en": "Expires in {days} {unit} ({date}).",
        "es": "Caduca en {days} {unit} ({date}).",
    },
    "summary.cert.expiring_today": {
        "en": "Expires today ({date}).",
        "es": "Caduca hoy ({date}).",
    },
    "summary.cert.expired": {
        "en": "Expired {days} {unit} ago ({date}).",
        "es": "Caducó hace {days} {unit} ({date}).",
    },
    "summary.unit.failed": {
        "en": "systemd marked it as failed.",
        "es": "systemd lo ha marcado como fallido.",
    },
    "summary.unit.crash_loop": {
        "en": (
            "systemd restarted it {count} {times} since the last check and it still does not "
            "stay up."
        ),
        "es": (
            "systemd lo ha reiniciado {count} {times} desde la última comprobación y sigue "
            "sin arrancar."
        ),
    },
    "summary.unit.stopped_on_failure": {
        "en": "It stopped after a run that failed, and systemd is not restarting it.",
        "es": "Se ha detenido tras una ejecución fallida y systemd no lo está reiniciando.",
    },
    "summary.unit.recovered": {
        "en": "It is running again and has not restarted since the last check.",
        "es": "Vuelve a estar en marcha y no se ha reiniciado desde la última comprobación.",
    },
    "summary.app.unreachable": {
        "en": "Its service is running, but the application does not answer its health check.",
        "es": (
            "Su servicio está en marcha, pero la aplicación no responde a su comprobación de salud."
        ),
    },
    "summary.app.unreachable_containers": {
        "en": "A container of this stack stopped with an error or keeps restarting.",
        "es": "Un contenedor de este stack se ha detenido con un error o no deja de reiniciarse.",
    },
    "summary.app.recovered": {
        "en": "It passes its health check again.",
        "es": "Vuelve a pasar su comprobación de salud.",
    },
    "summary.app.recovered_containers": {
        "en": "Every container of this stack runs again.",
        "es": "Todos los contenedores de este stack vuelven a estar en marcha.",
    },
    "summary.app.outside_unit": {
        "en": (
            "Its containers run, but its unit is stopped: Noust is not supervising it, and a "
            "restart of the server would not bring it back."
        ),
        "es": (
            "Sus contenedores están en marcha, pero su unidad está parada: Noust no la "
            "supervisa y un reinicio del servidor no la volvería a levantar."
        ),
    },
    "summary.disk.threshold": {
        "en": (
            "It is past the {threshold}% alert threshold, and a full disk stops deployments, "
            "logs and databases on this server."
        ),
        "es": (
            "Ha superado el umbral de aviso del {threshold}%, y un disco lleno detiene los "
            "despliegues, los registros y las bases de datos de este servidor."
        ),
    },
    "summary.disk.recovered": {
        "en": "Usage is back under the alert threshold.",
        "es": "El uso vuelve a estar por debajo del umbral de aviso.",
    },
    "summary.test": {
        "en": "If you can read this, the {channel} channel is configured correctly.",
        "es": "Si ves esto, el canal {channel} está bien configurado.",
    },
    "summary.node.unreachable": {
        "en": "Noust cannot reach this server through its tunnel.",
        "es": "Noust no puede alcanzar este servidor a través de su túnel.",
    },
    "summary.node.recovered": {
        "en": "The tunnel is open again and the server answers.",
        "es": "El túnel vuelve a estar abierto y el servidor responde.",
    },
    "summary.node.host_key_changed": {
        "en": (
            "The server presented a different SSH host key, so the tunnel stays closed until "
            "someone checks it."
        ),
        "es": (
            "El servidor ha presentado otra clave de host SSH, así que el túnel sigue cerrado "
            "hasta que alguien la compruebe."
        ),
    },
    "summary.server.rebooted": {
        "en": ("Nobody asked for this restart from Noust: check that every application came back."),
        "es": (
            "Nadie ha pedido este reinicio desde Noust: comprueba que todas las aplicaciones "
            "han vuelto."
        ),
    },
    "summary.server.back": {
        "en": "The restart asked from Noust is done and the console is running again.",
        "es": "El reinicio pedido desde Noust ha terminado y la consola vuelve a funcionar.",
    },
    "summary.approval.requested": {
        "en": "A change that needs a second person is waiting for someone to decide it.",
        "es": "Un cambio que necesita a una segunda persona está esperando a que alguien lo decida.",
    },
    "summary.approval.approved": {
        "en": "The person who asked can make the change once, before the approval runs out.",
        "es": ("Quien lo pidió puede hacer el cambio una vez, antes de que caduque la aprobación."),
    },
    "summary.approval.rejected": {
        "en": "The change will not be made; whoever asked can ask again with more context.",
        "es": "El cambio no se hará; quien lo pidió puede volver a pedirlo con más contexto.",
    },
    "summary.sandbox.trial_failed": {
        "en": (
            "Its build was tried without root before the update and failed, so the update "
            "built as root, as it did before."
        ),
        "es": (
            "Se ha probado su compilación sin root antes de la actualización y ha fallado, así "
            "que la actualización ha compilado como root, igual que antes."
        ),
    },
    "summary.database.backup.failed": {
        "en": "No new copy of the database was saved.",
        "es": "No se ha guardado ninguna copia nueva de la base de datos.",
    },
    "summary.database.backup.upload_failed": {
        "en": "The copy of the database was kept on this server but could not be uploaded.",
        "es": (
            "La copia de la base de datos se ha guardado en este servidor, pero no se ha podido "
            "subir."
        ),
    },
    "summary.database.backup.completed": {
        "en": "A new copy of the database was saved.",
        "es": "Se ha guardado una copia nueva de la base de datos.",
    },
    "summary.database.restore.completed": {
        "en": "The database is back as the dump had it.",
        "es": "La base de datos ha vuelto a como estaba en el volcado.",
    },
    "summary.database.restore.failed": {
        "en": "It stopped at an error, so the database may be only partly restored.",
        "es": (
            "Se ha detenido por un error, así que la base de datos podría estar restaurada solo "
            "en parte."
        ),
    },
    "summary.report.observations": {
        "en": (
            "The monitor only reports: no process was signalled and no file was touched. "
            "Review each entry before acting."
        ),
        "es": (
            "El monitor solo informa: no se ha enviado ninguna señal a ningún proceso ni se "
            "ha tocado ningún archivo. Revisa cada entrada antes de actuar."
        ),
    },
    # -- Fact labels ---------------------------------------------------------
    "fact.server": {"en": "Server", "es": "Servidor"},
    "fact.release": {"en": "Release", "es": "Versión"},
    "fact.commit": {"en": "Commit", "es": "Commit"},
    "fact.trigger": {"en": "Started by", "es": "Iniciado por"},
    "fact.duration": {"en": "Duration", "es": "Duración"},
    "fact.preview": {"en": "Preview of", "es": "Vista previa de"},
    "fact.backup": {"en": "Backup", "es": "Copia"},
    "fact.destination": {"en": "Destination", "es": "Destino"},
    "fact.size": {"en": "Size", "es": "Tamaño"},
    "fact.destinations": {"en": "Uploaded to", "es": "Subida a"},
    "fact.covers": {"en": "Covers", "es": "Cubre"},
    "fact.unit": {"en": "Unit", "es": "Unidad"},
    "fact.restarts": {"en": "Restarts", "es": "Reinicios"},
    "fact.result": {"en": "Result", "es": "Resultado"},
    "fact.exit_status": {"en": "Exit status", "es": "Código de salida"},
    "fact.used": {"en": "Used", "es": "Usado"},
    "fact.free": {"en": "Free", "es": "Libre"},
    "fact.address": {"en": "Address", "es": "Dirección"},
    "fact.reason": {"en": "Reason", "es": "Motivo"},
    "fact.since": {"en": "Unreachable since", "es": "Inaccesible desde"},
    "fact.downtime": {"en": "Was unreachable for", "es": "Estuvo inaccesible durante"},
    "fact.pinned": {"en": "Pinned key", "es": "Clave fijada"},
    "fact.presented": {"en": "Presented key", "es": "Clave presentada"},
    "fact.renew": {"en": "Renew with", "es": "Renuévalo con"},
    "fact.inspect": {"en": "Inspect with", "es": "Revísalo con"},
    "fact.diagnose": {"en": "Diagnose with", "es": "Diagnostícala con"},
    "fact.reclaim": {"en": "Hand it back with", "es": "Devuélvela a Noust con"},
    "fact.containers": {"en": "Containers", "es": "Contenedores"},
    "fact.reschedule": {"en": "Save it again with", "es": "Guárdala de nuevo con"},
    "fact.verify": {"en": "Check it with", "es": "Compruébalo con"},
    "fact.processes": {"en": "Processes", "es": "Procesos"},
    "fact.warnings": {"en": "Warnings", "es": "Avisos"},
    "fact.signal": {"en": "Signal", "es": "Señal"},
    "fact.user": {"en": "User", "es": "Usuario"},
    "fact.cpu": {"en": "CPU", "es": "CPU"},
    "fact.memory": {"en": "Memory", "es": "Memoria"},
    "fact.detail": {"en": "Detail", "es": "Detalle"},
    "fact.command": {"en": "Command", "es": "Comando"},
    "fact.parent": {"en": "Parent", "es": "Padre"},
    "fact.returned": {"en": "Back at", "es": "De vuelta a las"},
    "fact.requested_by": {"en": "Asked by", "es": "Pedido por"},
    "fact.scheduled": {"en": "Scheduled for", "es": "Programado para"},
    "fact.took": {"en": "Took", "es": "Tardó"},
    "fact.request": {"en": "Request", "es": "Solicitud"},
    "fact.action": {"en": "Change", "es": "Cambio"},
    "fact.call": {"en": "Call", "es": "Llamada"},
    "fact.decided_by": {"en": "Decided by", "es": "Decidida por"},
    "fact.comment": {"en": "Comment", "es": "Comentario"},
    "fact.decide_before": {"en": "Decide before", "es": "Decídela antes de"},
    "fact.use_before": {"en": "Use it before", "es": "Úsala antes de"},
    "fact.decide": {"en": "Decide with", "es": "Decídela con"},
    # -- Names of enum values ------------------------------------------------
    "trigger.cli": {"en": "CLI", "es": "CLI"},
    "trigger.panel": {"en": "Console", "es": "Consola"},
    "trigger.webhook": {"en": "Webhook", "es": "Webhook"},
    "channel.webhook": {"en": "Webhook", "es": "Webhook"},
    "channel.slack": {"en": "Slack", "es": "Slack"},
    "channel.discord": {"en": "Discord", "es": "Discord"},
    "channel.telegram": {"en": "Telegram", "es": "Telegram"},
    "channel.email": {"en": "Email", "es": "Correo electrónico"},
    "severity.warning": {"en": "Warning", "es": "Aviso"},
    "severity.notice": {"en": "Notice", "es": "Nota"},
    # -- The words around what every channel draws ---------------------------
    "ui.open_console": {"en": "Open in the console", "es": "Abrir en la consola"},
    "ui.sent_by": {"en": "Sent by Noust", "es": "Enviado por Noust"},
    "ui.change_settings": {
        "en": "Change what you receive in Settings > Notifications",
        "es": "Cambia lo que recibes en Ajustes > Notificaciones",
    },
    "ui.omitted.one": {"en": "1 earlier line omitted", "es": "1 línea anterior omitida"},
    "ui.omitted.other": {
        "en": "{count} earlier lines omitted",
        "es": "{count} líneas anteriores omitidas",
    },
    "ui.first_error": {
        "en": "First error above; the last lines follow",
        "es": "Primer error arriba; siguen las últimas líneas",
    },
    "excerpt.journal": {
        "en": "Last lines of the journal of {unit}",
        "es": "Últimas líneas del registro de {unit}",
    },
    "excerpt.output": {"en": "What the system reported", "es": "Lo que ha informado el sistema"},
    "excerpt.probe": {"en": "The last probe", "es": "La última sonda"},
}

#: Singular and plural nouns for the counted quantities a notification spells
#: out, by :func:`plural`'s own ``key`` and then locale.
_PLURAL_FORMS: dict[str, dict[Locale, tuple[str, str]]] = {
    "day": {"en": ("day", "days"), "es": ("día", "días")},
    "time": {"en": ("time", "times"), "es": ("vez", "veces")},
    "process": {"en": ("process", "processes"), "es": ("proceso", "procesos")},
}

#: Abbreviated month names, for the dates a notification spells out. Not
#: sentences, so not in :data:`MESSAGES`.
MONTHS: dict[Locale, tuple[str, ...]] = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"),
}


def normalize_locale(value: Any) -> Locale:
    """
    Coerce a configuration value to a supported locale.

    Args:
        value: Whatever ``notifications.language`` holds - normally already
            validated by :mod:`noust.core.config`, but this is also the one
            place a stale or hand-edited config file's value is made safe to
            index :data:`MESSAGES` with.

    Returns:
        ``"es"`` when the value is exactly that, case and surrounding space
        aside; :data:`DEFAULT_LOCALE` otherwise.
    """
    text = str(value or "").strip().lower()
    return "es" if text == "es" else DEFAULT_LOCALE


def message(key: str, locale: Locale, **params: Any) -> str:
    """
    Render one of Noust's own notification texts.

    Args:
        key: A key of :data:`MESSAGES`.
        locale: Which language to render it in.
        params: Values for the template's ``{name}`` placeholders - a domain,
            a unit name, a pre-scrubbed evidence string, never something that
            still needs translating itself.

    Returns:
        The rendered text.

    Raises:
        KeyError: When ``key`` is not in :data:`MESSAGES`.
        ValueError: When ``params`` does not supply every placeholder the
            template names.
    """
    try:
        catalog = MESSAGES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown notification message key {key!r}") from exc
    template = catalog[locale]
    try:
        return template.format(**params)
    except (KeyError, IndexError) as exc:
        raise ValueError(
            f"Notification message {key!r} ({locale}) is missing a placeholder: {exc}"
        ) from exc


def plural(key: str, locale: Locale, count: int) -> str:
    """
    Pick the singular or plural noun for a count.

    Args:
        key: A key of :data:`_PLURAL_FORMS`, e.g. ``"day"``.
        locale: Which language to render it in.
        count: How many there are. Exactly one is singular; everything else,
            zero included, is plural - the rule both English and Spanish
            follow for every noun this project counts.

    Returns:
        The noun, unqualified by the number itself - the caller's template
        supplies ``{count}`` next to it.

    Raises:
        KeyError: When ``key`` is not in :data:`_PLURAL_FORMS`.
    """
    one, other = _PLURAL_FORMS[key][locale]
    return one if count == 1 else other
