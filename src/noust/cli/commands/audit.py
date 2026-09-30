# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust audit`` command group: read, verify, export and review the trail.

A front end over :mod:`noust.core.audit`, the same log the console writes and
``GET /api/audit`` reads, so the terminal and the console always agree.

- ``list`` and ``show`` read it; ``export`` writes a copy for an auditor.
- ``verify`` checks the HMAC chain and names the first broken link. It is
  honest about its limit: root on this machine can rewrite a local chain, so
  a pass here means "consistent", and the copy shipped off the machine is the
  evidence that it is also *original*.
- ``review`` records that someone reviewed the log for a period (ENS
  op.exp.8.r1): the attestation is itself an audit event.
- ``ship`` and ``prune`` do from a timer what the console's worker does
  continuously, for a server that does not run the console.
- ``events`` prints the catalog, the table ``docs/ENS.md`` includes.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import audit as audit_trail
from noust.core.audit import Actor, get_log, health, record
from noust.core.audit.catalog import markdown_table
from noust.core.audit.chain import LIMITATION as HONESTY_NOTE
from noust.core.audit.chain import VerifyResult
from noust.core.exceptions import NoustError
from noust.core.fs import SECRET_MODE, get_fs


class AuditReadError(NoustError):
    """The audit log could not be read."""


def _read(action: Any) -> Any:
    try:
        return action()
    except PermissionError as exc:
        raise AuditReadError(
            f"Cannot read the audit log: {exc}",
            details="The audit log is readable by root only. Run the command with sudo.",
        ) from exc
    except OSError as exc:
        raise AuditReadError(f"Cannot read the audit log: {exc}") from exc


def _day(value: str | None, *, end: bool = False) -> str | None:
    """
    Turn a date or a timestamp given on the command line into a ``ts`` bound.

    Args:
        value: ``YYYY-MM-DD`` or an ISO 8601 timestamp.
        end: For a bare date, the end of that day rather than its start.

    Returns:
        The bound, comparable with the log's ``ts``, or None.

    Raises:
        click.BadParameter: The value is not a date.
    """
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter(f"{value!r} is not a date (YYYY-MM-DD) or ISO timestamp") from exc
    if len(value) == 10 and end:
        moment = moment.replace(hour=23, minute=59, second=59, microsecond=999999)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _between(since: str | None, until: str | None) -> list[dict[str, Any]]:
    """
    Every event in a period, oldest first.

    Args:
        since: Lower ``ts`` bound, inclusive.
        until: Upper ``ts`` bound, inclusive.

    Returns:
        The events.
    """
    log = get_log()
    selected: list[dict[str, Any]] = []
    for entry in log.iter_newest_first():
        timestamp = str(entry.get("ts", ""))
        if until is not None and timestamp > until:
            continue
        if since is not None and timestamp < since:
            break
        selected.append(entry)
    selected.reverse()
    return selected


@click.group("audit", cls=NoustGroup)
def cli() -> None:
    """Read, verify, export and review the audit log."""


@cli.command("list", read_only=True)
@click.option("-n", "--limit", default=50, show_default=True, help="Most events shown.")
@click.option("--action", help="Only this event, such as apps.delete.")
@click.option("--actor", help="Only this actor, as shown in the Actor column.")
@click.option("--result", help="Only this outcome, such as denied.")
@click.option("--category", help="Only this category, such as access or change.")
@click.option("--correlation", help="Only the events of one request, command or job.")
@click.option("--since", help="Only events from this date (YYYY-MM-DD) on.")
@json_option("Print the events as JSON.")
@pass_context
def list_command(
    ctx: Context,
    limit: int,
    action: str | None,
    actor: str | None,
    result: str | None,
    category: str | None,
    correlation: str | None,
    since: str | None,
) -> None:
    """List audit events, newest first."""
    entries = _read(
        lambda: get_log().read(
            limit=limit,
            action=action,
            actor=actor,
            result=result,
            category=category,
            correlation_id=correlation,
            since=_day(since),
        )
    )
    if ctx.json_output:
        click.echo(json.dumps({"events": entries}, indent=2))
        return
    if not entries:
        ctx.logger.info("No audit events match.")
        return
    ctx.logger.table(
        ["Seq", "Time (UTC)", "Actor", "Event", "Target", "Result"],
        [
            [
                str(entry.get("seq", "-")),
                str(entry.get("ts", ""))[:19].replace("T", " "),
                str(entry.get("actor", "")),
                str(entry.get("action", "")),
                str(entry.get("resource") or "-"),
                str(entry.get("result", "")),
            ]
            for entry in entries
        ],
    )


@cli.command("show", read_only=True)
@click.argument("event")
@json_option("Print the event as JSON.")
@pass_context
def show_command(ctx: Context, event: str) -> None:
    """Show one event in full, by sequence number or id."""
    entry = _read(lambda: get_log().find(event))
    if entry is None:
        raise NoustError(
            f"No audit event {event!r}",
            details="Give a sequence number (the Seq column of 'noust audit list') or an id.",
        )
    click.echo(json.dumps(entry, indent=2, sort_keys=True))


def _verify() -> VerifyResult:
    result: VerifyResult = _read(get_log().verify)
    record(
        "audit.verify",
        actor=audit_trail.cli_actor(),
        outcome="ok" if result.ok else "failure",
        details=result.to_dict(),
    )
    return result


@cli.command("verify", read_only=True)
@json_option("Print the result as JSON.")
@pass_context
def verify_command(ctx: Context) -> None:
    """
    Check the audit chain and report the first broken link.

    A pass proves the log is consistent under the local key. Root on this
    machine holds that key and could rewrite a consistent chain, so compare
    the head with the copy shipped off the machine to prove it is original.
    Exits 1 when the chain is broken.
    """
    result = _verify()
    if ctx.json_output:
        click.echo(json.dumps({**result.to_dict(), "note": HONESTY_NOTE}, indent=2))
    else:
        logger = ctx.logger
        if result.ok:
            logger.success(
                f"The audit chain holds: {result.checked} event(s), "
                f"seq {result.first_seq} to {result.last_seq}."
            )
            if result.last_mac:
                logger.key_value("Head", f"seq {result.last_seq}, mac {result.last_mac}")
        elif (broken := result.broken) is not None:
            logger.error(
                f"The audit chain is broken at {broken.file}, line {broken.line}"
                + (f" (seq {broken.seq})" if broken.seq is not None else ""),
                details=broken.reason,
            )
            logger.info(f"{result.checked} event(s) before it verified.")
        for note in result.notes:
            logger.info(note)
        logger.blank()
        logger.info(HONESTY_NOTE)
    if not result.ok:
        raise click.exceptions.Exit(1)


@cli.command("export", read_only=True)
@click.option("--since", help="From this date (YYYY-MM-DD) or timestamp.")
@click.option("--until", help="Up to this date (YYYY-MM-DD) or timestamp, inclusive.")
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write to this file (created 0600) instead of standard output.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["ndjson", "csv"]),
    default="ndjson",
    show_default=True,
    help="NDJSON keeps every field and the chain; CSV is for a spreadsheet.",
)
@pass_context
def export_command(
    ctx: Context, since: str | None, until: str | None, output: Path | None, fmt: str
) -> None:
    """Export the audit log for a period, for an auditor or a SIEM."""
    entries = _read(lambda: _between(_day(since), _day(until, end=True)))
    if fmt == "ndjson":
        text = "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries)
    else:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["seq", "ts", "actor", "action", "resource", "result", "ip", "corr", "mac"])
        for entry in entries:
            writer.writerow(
                [
                    entry.get(field, "")
                    for field in (
                        "seq",
                        "ts",
                        "actor",
                        "action",
                        "resource",
                        "result",
                        "ip",
                        "corr",
                        "mac",
                    )
                ]
            )
        text = buffer.getvalue()
    record(
        "audit.export",
        actor=audit_trail.cli_actor(),
        target=str(output) if output else "stdout",
        details={"events": len(entries), "since": since, "until": until, "format": fmt},
    )
    if output is None:
        click.echo(text, nl=False)
        return
    fs = get_fs()
    fs.write_text(output, text, mode=SECRET_MODE)
    ctx.logger.success(f"Exported {len(entries)} event(s) to {output}")


@cli.command("review", read_only=True)
@click.option("--from", "period_start", help="First day reviewed (YYYY-MM-DD).")
@click.option("--to", "period_end", help="Last day reviewed (YYYY-MM-DD).")
@click.option("--notes", default="", help="What was looked at and what was found.")
@click.option("--list", "list_reviews", is_flag=True, help="List the reviews recorded so far.")
@json_option("Print the review as JSON.")
@pass_context
def review_command(
    ctx: Context,
    period_start: str | None,
    period_end: str | None,
    notes: str,
    list_reviews: bool,
) -> None:
    """
    Record that you reviewed the audit log for a period (ENS op.exp.8.r1).

    The chain is verified first and the result is part of the attestation,
    which is itself an audit event (audit.review).
    """
    if list_reviews:
        reviews = _read(lambda: get_log().read(action="audit.review", limit=100))
        if ctx.json_output:
            click.echo(json.dumps({"reviews": reviews}, indent=2))
            return
        if not reviews:
            ctx.logger.info("No review has been recorded yet.")
            return
        ctx.logger.table(
            ["When (UTC)", "Reviewer", "Period", "Chain", "Notes"],
            [
                [
                    str(entry.get("ts", ""))[:19].replace("T", " "),
                    str(entry.get("actor", "")),
                    f"{(entry.get('details') or {}).get('period_start')} to "
                    f"{(entry.get('details') or {}).get('period_end')}",
                    "ok" if (entry.get("details") or {}).get("chain_ok") else "BROKEN",
                    str((entry.get("details") or {}).get("notes") or ""),
                ]
                for entry in reviews
            ],
        )
        return
    if not period_start or not period_end:
        raise click.UsageError("Give the period reviewed with --from and --to (YYYY-MM-DD).")
    start, end = _day(period_start) or "", _day(period_end, end=True) or ""
    if start > end:
        raise click.UsageError("--from is after --to.")
    reviewed = _read(lambda: _between(start, end))
    result = _verify()
    actor: Actor = audit_trail.cli_actor()
    details = {
        "period_start": period_start,
        "period_end": period_end,
        "notes": notes,
        "events_in_period": len(reviewed),
        "denied_in_period": sum(1 for entry in reviewed if entry.get("result") == "denied"),
        "chain_ok": result.ok,
        "chain_head_seq": result.last_seq,
        "chain_head_mac": result.last_mac,
    }
    record("audit.review", actor=actor, target=f"{period_start}..{period_end}", details=details)
    if ctx.json_output:
        click.echo(json.dumps(details, indent=2))
        return
    ctx.logger.success(
        f"Review of {period_start} to {period_end} recorded as {actor.label}: "
        f"{len(reviewed)} event(s) in the period."
    )
    if not result.ok:
        ctx.logger.warning(
            "The chain is broken; the review records that. Run 'noust audit verify' for where."
        )


@cli.command("ship", read_only=True)
@json_option("Print what was shipped as JSON.")
@pass_context
def ship_command(ctx: Context) -> None:
    """Send what each destination has not received yet (for a timer without the console)."""
    from noust.core.audit.shipper import Shipper

    shipper = Shipper(get_log())
    try:
        delivered = _read(shipper.ship_once)
    finally:
        shipper.close()
    if ctx.json_output:
        click.echo(json.dumps({"delivered": delivered}, indent=2))
        return
    if not delivered:
        ctx.logger.info(
            "No destination to ship to: set audit.syslog, or audit.stdout inside a container."
        )
        return
    for sink_id, count in delivered.items():
        ctx.logger.key_value(sink_id, f"{count} event(s)")


@cli.command("prune")
@json_option("Print what was deleted as JSON.")
@pass_context
def prune_command(ctx: Context) -> None:
    """Apply retention now: old audit files and Noust's other records past their period."""
    from noust.core.audit.retention import prune
    from noust.core.audit.shipper import Shipper

    log = get_log()
    purged = _read(
        lambda: log.purge(
            retention_days=log.settings.retention_days,
            shipped_seq=Shipper(log).lowest_cursor(),
        )
    )
    report = prune()
    payload = {
        "audit_files": list(purged.files) if purged else [],
        "records": report.to_dict(),
    }
    if ctx.json_output:
        click.echo(json.dumps(payload, indent=2))
        return
    ctx.logger.key_value("Audit files deleted", str(len(payload["audit_files"])))
    for kind, count in sorted(report.deleted.items()):
        ctx.logger.key_value(kind.capitalize(), f"{count} deleted")
    for kind, error in sorted(report.errors.items()):
        ctx.logger.warning(f"{kind}: {error}")


@cli.command("events", read_only=True)
@click.option("--markdown", is_flag=True, help="Print the Markdown table docs/ENS.md includes.")
@pass_context
def events_command(ctx: Context, markdown: bool) -> None:
    """List every event the audit log can record."""
    from noust.core.audit.catalog import CATEGORIES, EVENTS

    if markdown:
        click.echo(markdown_table(), nl=False)
        return
    order = {category: index for index, category in enumerate(CATEGORIES)}
    ctx.logger.table(
        ["Event", "Category", "Severity", "Description"],
        [
            [name, spec.category, str(spec.severity), spec.description]
            for name, spec in sorted(
                EVENTS.items(), key=lambda item: (order[item[1].category], item[0])
            )
        ],
    )


@cli.command("status", read_only=True)
@json_option("Print the report as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Say whether the audit trail works and where it is shipped."""
    report = health()
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict(), indent=2))
        return
    logger = ctx.logger
    outcome = {"ok": "ok", "warning": "warning", "error": "error"}[report.status]
    logger.check("Audit trail", report.status, outcome)
    logger.key_value("Log", report.log_path)
    logger.key_value("Size", f"{report.total_bytes // 1024} KiB")
    for sink in report.sinks:
        state = "degraded" if sink.get("degraded") else ("failing" if sink.get("error") else "ok")
        logger.key_value(f"Destination {sink['sink_id']}", state)
    for problem in report.problems:
        logger.warning(problem)
