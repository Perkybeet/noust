# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust ens`` command group: Spain's ENS, category MEDIUM (RD 311/2022).

A front end over :mod:`noust.core.ens`, the same code ``/api/ens`` serves the
console's Compliance view from:

- ``check`` compares this server with the ``ens-medium`` profile, check by
  check, each mapped to its measure, and exits 0 (ok), 1 (warnings) or 2
  (failures), the way a monitoring check does.
- ``report`` writes the evidence bundle for an auditor: JSON and Markdown, with
  their SHA-256 in a ``SHA256SUMS`` and in the audit log.
- ``profile`` prints what the profile fixes, and which one is on.
- ``inventory`` lists (JSON or CSV) and sets each application's owner,
  criticality and classification (op.exp.1).
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import audit
from noust.core.ens import checks, inventory, profile, report
from noust.core.fs import SECRET_MODE, get_fs

_STATUS_OUTCOME = {"ok": "ok", "warning": "warning", "fail": "error", "n/a": "skip"}


@click.group("ens", cls=NoustGroup)
def cli() -> None:
    """Check this server against Spain's ENS (category MEDIUM) and write the evidence."""


def _run(cached: bool) -> tuple[checks.ComplianceCheck, checks.Facts]:
    return checks.run_check(checks.Sources(refresh_hardening=not cached))


@cli.command("check", read_only=True)
@click.option(
    "--cached",
    is_flag=True,
    help="Reuse the last hardening checks instead of probing the server again (faster).",
)
@click.option("--all", "show_all", is_flag=True, help="Also show the checks that pass.")
@json_option("Print the result as JSON.")
@pass_context
def check_command(ctx: Context, cached: bool, show_all: bool) -> None:
    """
    Compare this server with the ens-medium profile, measure by measure.

    Exits 0 when every check passes, 1 when some only warn and 2 when any
    fails, so it can run from a monitoring system.
    """
    result, _facts = _run(cached)
    audit.record("compliance.read", actor=audit.cli_actor(), target="ens:check")
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        logger = ctx.logger
        counts = result.counts()
        logger.info(
            f"Profile {result.profile}: {counts['ok']} ok, {counts['warning']} warning(s), "
            f"{counts['fail']} failure(s), {counts['n/a']} not applicable."
        )
        for finding in result.findings:
            if finding.status == "ok" and not show_all:
                continue
            if finding.status == "n/a" and not show_all:
                continue
            logger.check(
                f"{finding.id} {finding.title} ({', '.join(finding.measures)})",
                finding.summary,
                _STATUS_OUTCOME[finding.status],
            )
            for line in finding.evidence:
                logger.info(f"    {line}")
            if finding.remediation and finding.status != "ok":
                logger.info(f"    Fix: {finding.remediation}")
        for area, error in result.errors.items():
            logger.warning(f"Could not read the {area}: {error}")
    if result.exit_code:
        raise click.exceptions.Exit(result.exit_code)


@cli.command("report", read_only=True)
@click.option(
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the bundle to (default: ens-reports/ beside the store).",
)
@click.option("--cached", is_flag=True, help="Reuse the last hardening checks.")
@json_option("Print the report itself as JSON instead of writing files.")
@pass_context
def report_command(ctx: Context, output: Path | None, cached: bool) -> None:
    """
    Write the evidence bundle for an ENS auditor (JSON and Markdown, hashed).

    The findings grouped by RD 311/2022 measure, the op.mon.2 indicators, the
    profile's baseline, accounts and roles, API tokens, the audit chain and
    its destinations, backups, hardening and accepted risks, inventory and
    fleet. Its SHA-256 goes to the audit log.
    """
    result, facts = _run(cached)
    built = report.build_report(result, facts)
    if ctx.json_output:
        audit.record(
            "compliance.read",
            actor=audit.cli_actor(),
            target="ens:report",
            details={"sha256": built["sha256"], "verdict": built["verdict"]},
        )
        click.echo(json.dumps(built, indent=2, sort_keys=True, default=str))
        return
    if ctx.dry_run:
        ctx.logger.info("would write the ENS evidence bundle")
        return
    if output is None:
        from noust.core.store import get_store

        output = get_store().db_path.parent / "ens-reports"
    written = report.write_bundle(built, output)
    audit.record(
        "compliance.read",
        actor=audit.cli_actor(),
        target="ens:report",
        details={
            "sha256": built["sha256"],
            "verdict": built["verdict"],
            "files": [str(path) for path in written.values()],
        },
    )
    logger = ctx.logger
    logger.success(f"ENS evidence written ({built['verdict']}):")
    for kind, path in written.items():
        logger.key_value(kind.upper(), str(path))
    logger.key_value("Report SHA-256", built["sha256"])


@cli.command("profile", read_only=True)
@json_option("Print the profiles as JSON.")
@pass_context
def profile_command(ctx: Context) -> None:
    """Show which security profile is on, and every value ens-medium fixes."""
    current = profile.current_profile()
    items = profile.baseline()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "profile": current,
                    "baseline": [item.to_dict() for item in items],
                    "values": checks.profile_values(profile.defaults_for(current)),
                },
                indent=2,
            )
        )
        return
    ctx.logger.key_value("security.profile", current)
    ctx.logger.table(
        ["Value", "Standard", "ens-medium", "Measures"],
        [
            [item.key, item.standard_value, item.ens_value, ", ".join(item.measures)]
            for item in items
        ],
    )
    if current != profile.PROFILE_ENS_MEDIUM:
        ctx.logger.info("Turn it on with: noust config set security.profile ens-medium")


@cli.group("inventory", cls=NoustGroup)
def inventory_group() -> None:
    """Each application's owner, criticality and classification (op.exp.1)."""


@inventory_group.command("list", read_only=True)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["table", "json", "csv"]),
    default="table",
    show_default=True,
    help="How to print it.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write to this file (created 0600) instead of standard output.",
)
@json_option("Same as --format json.")
@pass_context
def inventory_list_command(ctx: Context, fmt: str, output: Path | None) -> None:
    """List the inventory, or export it as JSON or CSV for an auditor."""
    entries = inventory.Inventory().list()
    if ctx.json_output:
        fmt = "json"
    if fmt == "table" and output is None:
        ctx.logger.table(
            ["Application", "Owner", "Criticality", "Classification", "Source"],
            [
                [
                    entry.domain,
                    entry.owner or "-",
                    entry.criticality or "-",
                    entry.classification or "-",
                    entry.source,
                ]
                for entry in entries
            ],
        )
        missing = sum(1 for entry in entries if not entry.complete)
        if missing:
            ctx.logger.warning(
                f"{missing} application(s) have no owner or criticality: "
                "noust ens inventory set <domain> --owner ... --criticality ..."
            )
        return
    text = inventory.export_csv(entries) if fmt == "csv" else inventory.export_json(entries)
    if output is None:
        click.echo(text, nl=False)
        return
    fs = get_fs()
    fs.write_text(output, text, mode=SECRET_MODE)
    ctx.logger.success(f"Exported {len(entries)} application(s) to {output}")


@inventory_group.command("set")
@click.argument("domain")
@click.option("--owner", help="Who answers for it: a person, a team, a contact. Empty clears it.")
@click.option(
    "--criticality",
    type=click.Choice([*inventory.CRITICALITIES, ""]),
    help="The category of the service it supports (ENS Anexo I).",
)
@click.option(
    "--classification",
    type=click.Choice([*inventory.CLASSIFICATIONS, ""]),
    help="How its information is handled (mp.info.2).",
)
@click.option("--notes", help="Anything else the organisation records.")
@json_option("Print the entry as JSON.")
@pass_context
def inventory_set_command(
    ctx: Context,
    domain: str,
    owner: str | None,
    criticality: str | None,
    classification: str | None,
    notes: str | None,
) -> None:
    """Set an application's owner, criticality or classification."""
    changes = {
        key: value
        for key, value in (
            ("owner", owner),
            ("criticality", criticality),
            ("classification", classification),
            ("notes", notes),
        )
        if value is not None
    }
    if not changes:
        raise click.UsageError(
            "Give at least one of --owner, --criticality, --classification, --notes."
        )
    if ctx.dry_run:
        ctx.logger.info(f"would set {', '.join(changes)} of {domain}")
        return
    entry = inventory.Inventory().set(domain, actor=audit.cli_actor().label, **changes)
    if ctx.json_output:
        click.echo(json.dumps(entry.to_dict(), indent=2))
        return
    ctx.logger.success(f"Inventory of {domain} updated.")
    ctx.logger.key_value("Owner", entry.owner or "-")
    ctx.logger.key_value("Criticality", entry.criticality or "-")
    ctx.logger.key_value("Classification", entry.classification or "-")


@cli.group("access-review", cls=NoustGroup)
def access_review_group() -> None:
    """The periodic review of who may do what (op.acc.4.4)."""


def _review_tokens() -> list[dict[str, object]] | None:
    """
    Returns:
        The console's API tokens, or None when there is no console here.
    """
    from noust.cli.web_state import token_manager
    from noust.core.exceptions import DependencyError, SecurityError

    try:
        manager = token_manager()
    except (DependencyError, SecurityError, OSError):
        return None
    try:
        return list(manager.list_api_tokens())
    finally:
        manager.sessions.close()


@access_review_group.command("list", read_only=True)
@json_option("Print the list and its digest as JSON.")
@pass_context
def access_review_list_command(ctx: Context) -> None:
    """Show every account, role, second factor and token, and past reviews."""
    from noust.core.ens import access_review

    review = access_review.build_review(tokens=_review_tokens())
    if ctx.json_output:
        click.echo(
            json.dumps({**review, "reviews": access_review.last_reviews()}, indent=2, default=str)
        )
        return
    ctx.logger.table(
        ["Account", "Role", "Status", "MFA", "Tokens", "Person"],
        [
            [
                entry["username"],
                entry["role"],
                entry["status"],
                "yes" if entry["mfa"] else "NO",
                str(entry["tokens"]),
                entry["person_ref"] or "-",
            ]
            for entry in review["accounts"]
        ],
    )
    for conflict in review["conflicts"]:
        ctx.logger.warning(
            f"{conflict['person_ref']} holds incompatible roles"
            + (" (exception recorded)" if conflict.get("exception") else "")
        )
    ctx.logger.key_value("Digest", review["digest"])
    ctx.logger.info("When it is right: noust ens access-review attest --notes '...'")


@access_review_group.command("attest")
@click.option("--notes", default="", help="What was looked at and what was changed.")
@json_option("Print what was recorded as JSON.")
@pass_context
def access_review_attest_command(ctx: Context, notes: str) -> None:
    """Record that you reviewed the list of accounts and roles (op.acc.4.4)."""
    from noust.core.ens import access_review

    if ctx.dry_run:
        ctx.logger.info("would record an access review")
        return
    review = access_review.build_review(tokens=_review_tokens())
    recorded = access_review.attest(review, actor=audit.cli_actor(), notes=notes)
    if ctx.json_output:
        click.echo(json.dumps(recorded, indent=2))
        return
    ctx.logger.success(
        f"Access review recorded: {recorded['accounts']} account(s), digest {recorded['digest']}."
    )
