# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust support`` command group: what to attach to an issue.

``noust support report`` prints the support report (:mod:`noust.managers.support_report`):
the version and how Noust was installed, the machine, the health report, every
application's state, the diagnosis of the ones that need attention, the Noust units and
the last lines of the console's journal. Secrets are masked before anything is printed.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.fs import SECRET_MODE, get_fs
from noust.managers.support_report import (
    DEFAULT_DIAGNOSE_LIMIT,
    DEFAULT_JOURNAL_LINES,
    SupportReporter,
)


@click.group("support", cls=NoustGroup)
def cli() -> None:
    """Collect what a maintainer needs to help you, with the secrets masked."""


@cli.command("report", read_only=True)
@click.option(
    "--lines",
    type=click.IntRange(0, 2000),
    default=DEFAULT_JOURNAL_LINES,
    show_default=True,
    help="Lines of the console's and the monitor's journal to include.",
)
@click.option(
    "--diagnose",
    "diagnose_limit",
    type=click.IntRange(0, 20),
    default=DEFAULT_DIAGNOSE_LIMIT,
    show_default=True,
    help="How many applications that need attention are diagnosed.",
)
@click.option(
    "--hide-domains",
    is_flag=True,
    help="Replace domains, application names and this host's name (app-1, host).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the report to this file (owner-only) instead of printing it.",
)
@json_option("Print the report as JSON.")
@pass_context
def report_command(
    ctx: Context, lines: int, diagnose_limit: int, hide_domains: bool, output: Path | None
) -> None:
    """
    Print a report to paste into an issue, with secrets masked.

    It reads and changes nothing. Read it before you post it: domains and the host's name
    are included unless you ask for --hide-domains.
    """
    reporter = SupportReporter(
        journal_lines=lines,
        journal_left_out="--lines 0 was given" if lines == 0 else None,
        diagnose_limit=diagnose_limit,
        hide_domains=hide_domains,
    )
    built = reporter.build()
    text = json.dumps(built.to_dict(), indent=2) if ctx.json_output else built.to_markdown()
    if output is not None:
        fs = get_fs()
        fs.write_text(output, text, mode=SECRET_MODE)
        ctx.logger.success(f"Support report written to {output}")
        return
    click.echo(text)
