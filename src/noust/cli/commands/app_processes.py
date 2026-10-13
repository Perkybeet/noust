# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app process-types``: the process types a repository declares and what a deployment does with them.

``show`` reads ``processes:`` of ``noust.yaml`` or the ``Procfile`` of the code that runs now and
says, for each process type, whether the next deployment uses it. It is
:mod:`noust.deployers.helpers.process_report`, which ``/api/apps/{d}/process-types`` calls too.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.deployers.helpers.process_report import describe_processes
from noust.validators.domain import validate_domain


@click.group("process-types", cls=NoustGroup)
def processes() -> None:
    """The process types (web, release, workers) a repository declares."""


@processes.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the process types as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Show the process types an application declares and which ones a deployment uses."""
    report = describe_processes(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Application", report.domain)
    logger.key_value("Declared in", report.found.source)
    if report.found.problem:
        logger.warning(f"The Procfile is not used: {report.found.problem}")
    if not report.found.declared:
        logger.info("No process types are declared.")
    for process in report.found:
        state = "used" if report.in_effect(process.name) else "declared"
        logger.key_value(f"{process.name} ({state})", process.command)
    for note in report.notes:
        logger.info(note)
