# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What deleting an application would delete, and what it would leave (item 183).

The deletion is :func:`noust.deployers.lifecycle.delete_app`, the call of the delete job, rehearsed:
what it would stop, remove and rewrite. What stays is said as well, because that is what a person
asks before confirming: the directory, the certificate, the volumes and the databases, each kept
unless the request says to remove it.
"""

from __future__ import annotations

from noust.core.exceptions import NoustError
from noust.core.plans.shape import PlanOut
from noust.core.server_text import ServerText, say
from noust.core.store import AppType, get_store, is_compose_backed
from noust.deployers import lifecycle
from noust.deployers.plans import common
from noust.validators.domain import validate_domain

OPERATION = "app.delete"


def plan_delete(
    domain: str,
    *,
    remove_files: bool = False,
    remove_ssl: bool = False,
    remove_volumes: bool = False,
    remove_databases: bool = False,
    remove_adopted_directory: str | None = None,
) -> PlanOut:
    """
    Say what deleting an application would delete and what it would leave.

    Args:
        domain: The application's domain.
        remove_files: Also remove its directory.
        remove_ssl: Also remove its certificate.
        remove_volumes: Also remove the Docker volumes of a stack or a container.
        remove_databases: Also drop the databases that are its own and that no other application
            uses, each after a last dump.
        remove_adopted_directory: The directory of an adopted stack, named to remove it.

    Returns:
        The plan.

    Raises:
        NoustError: The application is not deployed.
    """
    domain = validate_domain(domain)
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}",
            details="Nothing to delete; check 'noust list' for the exact domain.",
        )
    findings: list[ServerText] = []
    if not remove_files:
        findings.append(say("plans.found.delete_files_kept", domain=domain))
    if not remove_ssl:
        findings.append(say("plans.found.delete_certificate_kept", domain=domain))
    holds_volumes = app.app_type == AppType.CONTAINER.value or is_compose_backed(app.app_type)
    if holds_volumes and not remove_volumes:
        findings.append(say("plans.found.delete_volumes_kept", domain=domain))
    for database in lifecycle.deletion_databases(domain):
        name = f"{database.engine}/{database.name}"
        if remove_databases and database.will_drop:
            findings.append(say("plans.found.delete_database_dropped", database=name))
        else:
            findings.append(say("plans.found.delete_database_kept", database=name))
    return common.plan_call(
        OPERATION,
        domain,
        say("plans.delete.app", domain=domain),
        lambda: lifecycle.delete_app(
            domain,
            remove_files=remove_files,
            remove_certificate=remove_ssl,
            remove_volumes=remove_volumes,
            remove_databases=remove_databases,
            remove_adopted_directory=remove_adopted_directory,
            logger=common.quiet_logger(),
        ),
        domain=domain,
        findings=findings,
    )
