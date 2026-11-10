# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a background job is, said as one sentence of the catalog (item 114).

A job's ``name`` is English that the call site wrote, so every client that lists jobs (the Activity
page, the notices, the console's tray, a script) said it in words of its own, and 19 of the 35 types
had no words anywhere. :func:`title_for` is the one place that decides, from what a job is (its type
and what it knows of its subject), which sentence of :mod:`noust.core.server_text` names it. The
console says it in its own language from the code, and the English text is always beside it.

A title is derived, not stored: the same function titles a job that is running (its type and
metadata) and one that is only in history (the type and the application the store keeps), so a job
queued before this existed has one too. A type has a few ways to be said, from the most specific to
one that needs nothing, and the first whose values the job knows wins: ``Deploy shop.example.com``
when the application is known, ``Deploy an application`` when it is not. A table of literals, not
codes built from the type's name: a sentence nobody asks for is a defect the catalog's test finds.

A sentence is never glued to a subject by the client: the application, the destination or the role
is a value of the sentence, so word order is the language's own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.fleet_job_titles import coded as fleet_job_title
from noust.core.server_text import Param, ServerText, read_back, say

__all__ = ["TITLES", "database_backup_name", "title_for"]


@dataclass(frozen=True)
class Way:
    """
    One way to say what a job is.

    Attributes:
        code: The sentence, in :mod:`noust.core.server_text`.
        takes: ``(value of the sentence, key of the job's metadata)`` for each value it is said
            with. The way is used only when the job has every one as text, a number or a flag.
        when: ``(key, value)`` the job's metadata must hold for the way to apply.
        from_name: The job's name is this sentence, and its values are read back from it: what a
            job about something that is not an application (a database) has, because the store
            keeps a job's name and its application and nothing else of its subject. Then the keys
            of ``takes`` are not read from the metadata.
    """

    code: str
    takes: tuple[tuple[str, str], ...] = ()
    when: tuple[tuple[str, str], ...] = ()
    from_name: bool = False


def _app(code: str, any_code: str) -> tuple[Way, ...]:
    """
    Args:
        code: The sentence about an application, said with its ``domain``.
        any_code: The sentence for a job that does not know which.

    Returns:
        The two ways, in order.
    """
    return (Way(code, (("domain", "domain"),)), Way(any_code))


#: Every type of job (:class:`noust.web.jobs.JobType`, by value) and the ways to say it, the last
#: of which needs nothing so that a job is always named. ``tests/test_job_titles.py`` holds the keys
#: to the enum and each code to the catalog and to the console's English and Spanish catalogs.
TITLES: dict[str, tuple[Way, ...]] = {
    "deploy": _app("jobs.title.deploy", "jobs.title.deploy_any"),
    "update": _app("jobs.title.update", "jobs.title.update_any"),
    "backup": (
        Way("jobs.title.backup_database", (("database", "database"),), from_name=True),
        *_app("jobs.title.backup", "jobs.title.backup_any"),
    ),
    "restore": _app("jobs.title.restore", "jobs.title.restore_any"),
    "rollback": _app("jobs.title.rollback", "jobs.title.rollback_any"),
    "push": (
        Way("jobs.title.push", (("destination", "destination"),)),
        Way("jobs.title.push_any"),
    ),
    # ``acme`` is how the registration of the account is keyed and is not a domain.
    "cert_create": (
        Way("jobs.title.cert_acme_account", when=(("domain", "acme"),)),
        Way("jobs.title.cert_create", (("domain", "domain"),)),
        Way("jobs.title.cert_create_any"),
    ),
    # ``all`` is the renewal of every certificate that is due and is not a domain.
    "cert_renew": (
        Way("jobs.title.cert_renew_all", when=(("domain", "all"),)),
        Way("jobs.title.cert_renew", (("domain", "domain"),)),
        Way("jobs.title.cert_renew_all"),
    ),
    "service_action": (Way("jobs.title.service_action"),),
    "site_action": (
        Way(
            "jobs.title.site_status_page_publish",
            (("domain", "status_domain"),),
            (("kind", "status_page_publish"),),
        ),
        Way("jobs.title.site_status_page_unpublish", when=(("kind", "status_page_unpublish"),)),
        Way(
            "jobs.title.site_console_expose",
            (("domain", "console_domain"),),
            (("kind", "console_expose"),),
        ),
        Way("jobs.title.site_console_unexpose", when=(("kind", "console_unexpose"),)),
        Way("jobs.title.site_webserver_move", (("webserver", "webserver"),)),
        Way("jobs.title.site_action"),
    ),
    "delete": _app("jobs.title.delete", "jobs.title.delete_any"),
    "migrate": _app("jobs.title.migrate", "jobs.title.migrate_any"),
    "zero_downtime": _app("jobs.title.zero_downtime", "jobs.title.zero_downtime_any"),
    "sandbox_test": _app("jobs.title.sandbox_test", "jobs.title.sandbox_test_any"),
    "identity_migrate": _app("jobs.title.identity_migrate", "jobs.title.identity_migrate_any"),
    "cloudflared_install": (Way("jobs.title.cloudflared_install"),),
    "hardening": _app("jobs.title.hardening", "jobs.title.hardening_any"),
    "tuning": _app("jobs.title.tuning", "jobs.title.tuning_any"),
    "disk": _app("jobs.title.disk", "jobs.title.disk_any"),
    "replicas": _app("jobs.title.replicas", "jobs.title.replicas_any"),
    "os_refresh": (Way("jobs.title.os_refresh"),),
    "os_update": (Way("jobs.title.os_update"),),
    "cleanup": (Way("jobs.title.cleanup"),),
    "swap": (
        Way("jobs.title.swap_create", when=(("action", "create"),)),
        Way("jobs.title.swap_remove", when=(("action", "remove"),)),
        Way("jobs.title.swap"),
    ),
    "disk_scan": (Way("jobs.title.disk_scan"),),
    "server_action": (
        Way("jobs.title.server_action_dns_install", when=(("kind", "dns_server_install"),)),
        Way(
            "jobs.title.server_action_dns_setup",
            (("role", "role"),),
            (("kind", "dns_server_setup"),),
        ),
        # The Software page's install and removal name the component (the registry's id) in the sentence.
        Way(
            "jobs.title.server_action_software_install",
            (("component", "component"),),
            (("action", "install"),),
        ),
        Way(
            "jobs.title.server_action_software_remove",
            (("component", "component"),),
            (("action", "remove"),),
        ),
        Way("jobs.title.server_action"),
    ),
    # What a security job did (an operation of a dozen) is not something to say without words of its own.
    "server_security": (Way("jobs.title.server_security"),),
    "database": (
        Way("jobs.title.database_of_app", (("domain", "domain"),)),
        Way("jobs.title.database"),
    ),
    "fleet": (Way("jobs.title.fleet"),),
    "node_enroll": (
        Way("jobs.title.node_enroll", (("node", "node"),)),
        Way("jobs.title.node_enroll_any"),
    ),
    "node_provision": (
        Way("jobs.title.node_provision", (("node", "node"),)),
        Way("jobs.title.node_provision_any"),
    ),
    "self_update": (Way("jobs.title.self_update"),),
    "app_exec": _app("jobs.title.app_exec", "jobs.title.app_exec_any"),
    "manifest": (Way("jobs.title.manifest"),),
    "custom": (
        Way("jobs.title.custom_of_app", (("domain", "domain"),)),
        Way("jobs.title.custom"),
    ),
}


def database_backup_name(database: str) -> str:
    """
    Name the job that copies a database: its own title, so a stored job keeps it.

    Args:
        database: The database.

    Returns:
        ``Back up the database shop``, the sentence :func:`title_for` reads back from the name.
    """
    return say("jobs.title.backup_database", database=database).text


def _values(way: Way, metadata: Mapping[str, Any], name: str) -> dict[str, Param] | None:
    """
    Args:
        way: One way to say a job.
        metadata: What the job knows of its subject.
        name: The job's name.

    Returns:
        The values for the way's sentence, or None when the job lacks one or the way's condition
        does not hold.
    """
    if any(metadata.get(key) != value for key, value in way.when):
        return None
    if way.from_name:
        said = read_back(name, [way.code])
        return dict(said.params) if said is not None else None
    values: dict[str, Param] = {}
    for name, key in way.takes:
        found = metadata.get(key)
        # A list, an object or an empty string is not something to put in a sentence.
        if not isinstance(found, str | int | float | bool) or found == "":
            return None
        values[name] = found
    return values


def title_for(job_type: str, metadata: Mapping[str, Any], name: str = "") -> ServerText:
    """
    Name a job.

    Args:
        job_type: The job's type (``deploy``), as :class:`noust.web.jobs.JobType` spells it. A type
            this version does not know, from a newer one, is a task.
        metadata: What the job knows of its subject, such as ``domain``. A stored job keeps only
            its application, and gets the sentence of its type for that.
        name: The job's name. A fleet job's is read, and so is a database copy's
            (:func:`database_backup_name`): each is already a sentence of the catalog, which says
            more than the type does and is all that a stored job keeps of it.

    Returns:
        The sentence. Its English text is always right; the console translates it by its code.
    """
    if job_type == "fleet":
        coded = fleet_job_title(name)
        if coded is not None:
            return coded
    for way in TITLES.get(job_type, TITLES["custom"]):
        values = _values(way, metadata, name)
        if values is not None:
            return say(way.code, **values)
    # Unreachable while the last way of every type needs nothing (a test holds the table to it).
    return say("jobs.title.custom")
