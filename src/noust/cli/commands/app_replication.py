# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app replication``: could this application run on more than one server?

Reads this server's records of the application and says what a second server would get wrong or
do twice. It places nothing: putting an application on several servers is the central's
(``noust fleet placement``). All of it is :mod:`noust.managers.replication`, which
``/api/apps/{d}/replication`` calls too.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.managers import replication
from noust.validators.domain import validate_domain


@click.command("replication", cls=NoustCommand, read_only=True)
@click.argument("domain")
@json_option("Print the verdict as JSON.")
@pass_context
def replication_command(ctx: Context, domain: str) -> None:
    """
    Say whether the application could run on more than one server, and what stops it.

    A finding that blocks is one a second server would get wrong (files kept here, a database that
    runs here); one that warns would work with a cost (scheduled tasks that would run once per
    server). It places nothing: spread an application with 'noust fleet placement' on a central.
    """
    validated = validate_domain(domain)
    app = get_store().get_app(validated)
    if app is None:
        raise NoustError(
            f"Application not found: {validated}",
            details="Check 'noust list' for the exact domain.",
        )
    verdict = replication.assess(app)
    if ctx.json_output:
        click.echo(json.dumps(verdict.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Application", verdict.domain)
    logger.key_value(
        "Several servers",
        "ready: nothing here stops it"
        if verdict.ready
        else "not as it is: something here blocks it",
    )
    if verdict.by_image:
        logger.key_value("Image", "a registry image: roll a tag out with --image-tag")
    for finding in verdict.findings:
        logger.check(
            finding.message,
            finding.fix + (f" ({', '.join(finding.items)})" if finding.items else ""),
            "error" if finding.severity == "blocks" else "warn",
        )
    if not verdict.findings:
        logger.success("Nothing in its records speaks against it")
