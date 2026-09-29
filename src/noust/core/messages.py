# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A typed catalog of WASM's own words in every notification WASM sends.

2.3 adds ``notifications.language`` (see :data:`wasm.core.config.DEFAULT_CONFIG`):
an operator can read a deploy failure or a disk warning in Spanish instead of
English. This module is the one place that pairing lives - every title and
body a notification carries is a key here, in both locales, never built by
concatenating translated fragments, because word order is not the same
sentence in both languages.

What this catalog is not for: the evidence inside a body - a health gate's
probes, a journal line, rclone's or certbot's own stderr, a :class:`WASMError`
message - is never a value in :data:`MESSAGES`. That text stays in English
and reaches the operator verbatim, exactly as the console shows it, because
paraphrasing another program's own words is how an operator stops trusting
what WASM tells them. A caller passes it in as a ``str.format`` parameter of
a key that translates only the sentence around it.

The CLI and every other server-generated string are out of scope for 2.3 (see
``docs/superpowers/specs/2026-09-28-wasm-2.3-design.md`` S1): this catalog
covers notifications only.
"""

from __future__ import annotations

from typing import Any, Literal

#: The two locales a notification can be rendered in. Config validates
#: ``notifications.language`` against exactly these two spellings.
Locale = Literal["en", "es"]

#: What an unset or unrecognised ``notifications.language`` falls back to.
DEFAULT_LOCALE: Locale = "en"

#: Every notification text WASM builds, keyed by a short name and then by
#: locale. ``tests/test_messages.py`` enforces the invariant a Python dict
#: cannot: both locales define exactly the same keys, with exactly the same
#: ``str.format`` placeholders, and neither text is empty.
MESSAGES: dict[str, dict[Locale, str]] = {
    # -- Deploys (wasm.core.deploy_notifications, wasm.web.server) ----------
    "deploy_started_title": {
        "en": "Deploying {domain}",
        "es": "Desplegando {domain}",
    },
    "deploy_succeeded_title": {
        "en": "{domain} deployed{detail}",
        "es": "{domain} desplegado{detail}",
    },
    "deploy_failed_title": {
        "en": "{domain} failed to deploy",
        "es": "No se ha podido desplegar {domain}",
    },
    # wasm.web.server.JobNotificationSubscriber's fallback for a deploy,
    # update or rollback job that failed before the recorder opened and
    # carries no domain in its metadata - the job's own English name (console
    # text, out of scope per the module docstring) is the placeholder rather
    # than a paraphrase of it.
    "deploy_failed_title_no_domain": {
        "en": "{name} failed",
        "es": "{name} ha fallado",
    },
    "deploy_rolled_back_title": {
        "en": "{domain} rolled back",
        "es": "Se ha vuelto a la versión anterior de {domain}",
    },
    "deploy_trigger": {
        "en": "Trigger: {trigger}",
        "es": "Origen: {trigger}",
    },
    "deploy_commit": {
        "en": "Commit: {commit}",
        "es": "Commit: {commit}",
    },
    "deploy_preview_with_number": {
        "en": "Preview of {parent} #{number}.",
        "es": "Vista previa de {parent} n.º {number}.",
    },
    "deploy_preview": {
        "en": "Preview of {parent}.",
        "es": "Vista previa de {parent}.",
    },
    # -- Backup restores (wasm.web.server) -----------------------------------
    "restore_succeeded_title": {
        "en": "{domain} restored",
        "es": "Se ha restaurado {domain}",
    },
    "restore_succeeded_title_no_domain": {
        "en": "Restore completed",
        "es": "Restauración completada",
    },
    # v2.2.1 carried the backup id in the job's own description, reused
    # verbatim as the notification body; that text is English-only console
    # copy, out of scope for a translated notification (see the module
    # docstring), so the backup id travels as a placeholder here instead.
    "restore_succeeded_body": {
        "en": "Restored from backup {backup_id}.",
        "es": "Restaurado a partir de la copia de seguridad {backup_id}.",
    },
    "restore_failed_title": {
        "en": "{domain} restore failed",
        "es": "No se ha podido restaurar {domain}",
    },
    "restore_failed_title_no_domain": {
        "en": "Restore failed",
        "es": "No se ha podido completar la restauración",
    },
    # -- A backup job's own failure (wasm.web.server) ------------------------
    "backup_job_failed_title": {
        "en": "Backup of {domain} failed",
        "es": "La copia de seguridad de {domain} ha fallado",
    },
    "backup_job_failed_title_no_domain": {
        "en": "Backup failed",
        "es": "La copia de seguridad ha fallado",
    },
    # -- Scheduled backups (wasm.managers.backup_scheduler) ------------------
    "backup_schedule_missing_title": {
        "en": "Backup schedule settings missing: {domain}",
        "es": "Faltan los ajustes de la copia de seguridad programada: {domain}",
    },
    "backup_schedule_missing_body": {
        "en": (
            "The timer for {domain} fired but WASM's store has no schedule for it, so the "
            "backup was taken as 2.1 took it: databases included, backup.max_per_app "
            "rotation, no remote destinations. Check which store WASM is using "
            "(/var/lib/wasm), then save the schedule again with 'wasm backup schedule "
            "update {domain}' or from the console."
        ),
        "es": (
            "El temporizador de {domain} se ha activado, pero el almacén de WASM no "
            "tiene una programación para él, así que la copia de seguridad "
            "se ha hecho como en 2.1: con las bases de datos incluidas, rotación por "
            "backup.max_per_app y sin destinos remotos. Comprueba qué almacén "
            "está usando WASM (/var/lib/wasm) y vuelve a guardar la programación "
            "con 'wasm backup schedule update {domain}' o desde la consola."
        ),
    },
    "backup_scheduled_failed_title": {
        "en": "Scheduled backup failed: {domain}",
        "es": "La copia de seguridad programada de {domain} ha fallado",
    },
    "backup_upload_failed_title": {
        "en": "Backup upload to {name} failed: {domain}",
        "es": "No se ha podido subir la copia de seguridad de {domain} a {name}",
    },
    # -- The monitor daemon (wasm.monitor.process_monitor) -------------------
    "disk_threshold_title": {
        "en": "Disk usage at {percent}% on {mountpoint}",
        "es": "Uso de disco al {percent}% en {mountpoint}",
    },
    "disk_threshold_body": {
        "en": (
            "{mountpoint} is {percent}% full, past the {threshold}% alert threshold. A "
            "full disk stops deployments, logs and databases on this machine."
        ),
        "es": (
            "{mountpoint} está al {percent}% de su capacidad, por encima del umbral "
            "de aviso del {threshold}%. Un disco lleno detiene los despliegues, los "
            "registros y las bases de datos de esta máquina."
        ),
    },
    "cert_expiring_title": {
        "en": "Certificate for {name} expires in {days} {unit}",
        "es": "El certificado de {name} caduca en {days} {unit}",
    },
    "cert_expiring_body": {
        "en": "{covers} expires on {expiry}. Renew it with: wasm cert renew {name}",
        "es": "{covers} caduca el {expiry}. Renuévalo con: wasm cert renew {name}",
    },
    "unit_failed_title_failed": {
        "en": "Unit {unit} failed",
        "es": "La unidad {unit} ha fallado",
    },
    "unit_failed_title_crash_loop": {
        "en": "Unit {unit} is crash-looping",
        "es": "La unidad {unit} está en bucle de reinicios",
    },
    "unit_failed_title_stopped_on_failure": {
        "en": "Unit {unit} stopped on a failure",
        "es": "La unidad {unit} se ha detenido tras un fallo",
    },
    "unit_failed_body": {
        "en": "{detail}\nInspect it with: systemctl status {unit} and journalctl -u {unit} -n 50",
        "es": "{detail}\nRevísalo con: systemctl status {unit} y journalctl -u {unit} -n 50",
    },
    # -- The settings page's "send a test" button (wasm.core.notifier) ------
    "test_notification_title": {
        "en": "WASM test notification",
        "es": "Notificación de prueba de WASM",
    },
    "test_notification_body": {
        "en": "Receiving this means the {channel} channel is configured correctly.",
        "es": "Si recibes esto, el canal {channel} está bien configurado.",
    },
    # -- The monitor's own SMTP report (wasm.monitor.email_notifier) --------
    # This is a second delivery path from wasm.core.notifier's multi-channel
    # one above: EmailNotifier renders its own EmailContent directly, rather
    # than a NotificationEvent already built from this catalog, so it reads
    # notifications.language for itself.
    "email_observations_subject": {
        "en": "[WASM] {count} process observation(s) on {hostname}",
        "es": "[WASM] {count} observación(es) de proceso en {hostname}",
    },
    "email_observations_heading": {
        "en": "WASM monitor - process observations",
        "es": "WASM monitor - observaciones de procesos",
    },
    "email_server_line": {
        "en": "Server: {hostname}",
        "es": "Servidor: {hostname}",
    },
    "email_time_line": {
        "en": "Time: {timestamp}",
        "es": "Hora: {timestamp}",
    },
    "email_observations_noted_line": {
        "en": "Noted: {count} process(es), {warnings} of them as warnings",
        "es": "Detectados: {count} proceso(s), {warnings} de ellos marcados como aviso",
    },
    "email_observations_disclaimer": {
        "en": (
            "The monitor reports only. No process was signalled and no file was "
            "touched. Review each entry before taking any action."
        ),
        "es": (
            "El monitor solo informa. No se ha enviado ninguna señal a ningún proceso "
            "ni se ha tocado ningún archivo. Revisa cada entrada antes de actuar."
        ),
    },
    "email_test_subject": {
        "en": "[WASM] Test email - {hostname}",
        "es": "[WASM] Correo de prueba - {hostname}",
    },
    "email_test_heading": {
        "en": "WASM monitor - test email",
        "es": "WASM monitor - correo de prueba",
    },
    "email_test_body": {
        "en": "Receiving this means monitor notifications are configured correctly.",
        "es": "Si recibes esto, las notificaciones del monitor están bien configuradas.",
    },
}

#: Singular and plural nouns for the few counted quantities a notification
#: spells out, by :func:`plural`'s own ``key`` and then locale. English's
#: pre-2.3 text sidestepped grammatical number altogether - "3 day(s)" - and
#: that wording is a byte-for-byte contract existing tests assert on, so
#: both its forms are the same literal here; Spanish gets the real singular
#: and plural, "día" and "días", since it never had that shortcut
#: to preserve.
_PLURAL_FORMS: dict[str, dict[Locale, tuple[str, str]]] = {
    "day": {"en": ("day(s)", "day(s)"), "es": ("día", "días")},
}


def normalize_locale(value: Any) -> Locale:
    """
    Coerce a configuration value to a supported locale.

    Args:
        value: Whatever ``notifications.language`` holds - normally already
            validated by :mod:`wasm.core.config`, but this is also the one
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
    Render one of WASM's own notification texts.

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
