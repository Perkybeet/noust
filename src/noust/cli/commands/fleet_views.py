# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet from a central's terminal: views across every node, and bulk actions.

``noust fleet apps|certs|backups|updates|activity`` are the views of
``/api/fleet/*`` (the same aggregator, :mod:`noust.fleet.aggregate`); ``noust
fleet run`` is ``POST /api/fleet/actions`` (the same plan and engine,
:mod:`noust.web.fleet_jobs`), run here and recorded like any fleet job; ``noust
fleet jobs`` lists them and ``noust fleet retry`` runs a job's failed servers
again. ``noust node label`` groups nodes for ``--label``.

The commands are attached to the ``noust fleet`` and ``noust node`` groups,
which import this module; the handlers only print what those modules answer.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import click

from noust.cli.app import Context, json_option, pass_context
from noust.cli.commands.fleet import cli as fleet_cli
from noust.cli.commands.node import cli as node_cli
from noust.core.exceptions import ValidationError

if TYPE_CHECKING:
    from noust.fleet.aggregate import FleetResult
    from noust.web.fleet_jobs import FleetRequest, Plan

#: Seconds each request to a node may take from the terminal, which waits for all.
CLI_NODE_TIMEOUT = 20.0


def _gather(resource: str, nodes: tuple[str, ...], refresh: bool, **params: Any) -> FleetResult:
    """
    Ask every node for one view, from the terminal.

    Args:
        resource: The view.
        nodes: Only these nodes.
        refresh: Always true in practice: the terminal has no cache worth reusing.
        **params: The view's parameters.

    Returns:
        The view.
    """
    from noust.fleet.aggregate import gather

    return gather(
        resource,
        params,
        nodes=list(nodes) or None,
        refresh=refresh,
        node_timeout=CLI_NODE_TIMEOUT,
        deadline=None,
    )


def _report(ctx: Context, result: FleetResult) -> None:
    """
    Say, under a table, which nodes did not answer fully, in their own words.

    Args:
        ctx: The CLI context.
        result: The view.
    """
    logger = ctx.logger
    for outcome in result.nodes:
        if outcome.status == "ok":
            continue
        age = (
            f" (showing data from {outcome.age_seconds:.0f}s ago)"
            if outcome.status == "stale"
            else ""
        )
        logger.warning(
            f"{outcome.name}: {outcome.status}{age}: {outcome.message or ''}".rstrip(": ")
        )
        if outcome.error_verbatim:
            click.echo(outcome.error_verbatim)


def _emit(
    ctx: Context, result: FleetResult, headers: list[str], rows: list[list[str]], empty: str
) -> None:
    """
    Print a view as JSON, or as a table and the nodes that did not answer.

    Args:
        ctx: The CLI context.
        result: The view.
        headers: The table's headers.
        rows: Its rows.
        empty: What to say when there are none.
    """
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    if rows:
        ctx.logger.table(headers, rows)
    elif not result.nodes:
        ctx.logger.info("This central manages no nodes. Add one with 'noust node key NAME'.")
    else:
        ctx.logger.info(empty)
    _report(ctx, result)


def _node_option(function: Any) -> Any:
    """Add ``--node`` (repeatable) to a view."""
    return click.option("--node", "nodes", multiple=True, help="Only this node (repeat for more).")(
        function
    )


@fleet_cli.command("apps", read_only=True)
@_node_option
@click.option("--status", "status", default=None, help="Only applications in this state.")
@json_option("Print the view as JSON: every node's outcome and every application.")
@pass_context
def apps_command(ctx: Context, nodes: tuple[str, ...], status: str | None) -> None:
    """Every application of every node, with its state, type and last deployment."""
    result = _gather("apps", nodes, True)
    if status:
        result.items = [item for item in result.items if item.get("status") == status]
    rows = [
        [
            str(item["node"]),
            str(item.get("domain") or ""),
            str(item.get("status") or "-"),
            str(item.get("app_type") or "-"),
            str(item.get("branch") or "-"),
            str((item.get("last_deployment") or {}).get("status") or "-")
            if isinstance(item.get("last_deployment"), dict)
            else "-",
        ]
        for item in result.items
    ]
    _emit(
        ctx,
        result,
        ["Node", "Application", "State", "Type", "Branch", "Last deploy"],
        rows,
        "No applications.",
    )


@fleet_cli.command("certs", read_only=True)
@_node_option
@click.option(
    "--expiring", "expiring", type=int, default=None, help="Only certificates expiring within DAYS."
)
@json_option("Print the view as JSON.")
@pass_context
def certs_command(ctx: Context, nodes: tuple[str, ...], expiring: int | None) -> None:
    """Every certificate of every node, the soonest to expire first."""
    result = _gather("certificates", nodes, True)
    if expiring is not None:
        result.items = [
            item
            for item in result.items
            if isinstance(item.get("days_remaining"), int) and item["days_remaining"] <= expiring
        ]
    rows = [
        [
            str(item["node"]),
            str(item.get("domain") or ""),
            str(item.get("expires_on") or "-"),
            "-" if item.get("days_remaining") is None else str(item["days_remaining"]),
            "yes" if item.get("auto_renew") else "no",
        ]
        for item in result.items
    ]
    _emit(
        ctx,
        result,
        ["Node", "Certificate", "Expires", "Days", "Auto-renew"],
        rows,
        "No certificates.",
    )


@fleet_cli.command("backups", read_only=True)
@_node_option
@json_option("Print the view as JSON.")
@pass_context
def backups_command(ctx: Context, nodes: tuple[str, ...]) -> None:
    """Every application's newest backup, the gaps first: none, old, unverified, unscheduled."""
    result = _gather("backups", nodes, True)
    rows = [
        [
            str(item["node"]),
            str(item.get("domain") or ""),
            str((item.get("last_backup") or {}).get("timestamp") or "never"),
            str(item.get("backups") or 0),
            str(item.get("verified") or "never"),
            str((item.get("schedule") or {}).get("schedule") or "-"),
        ]
        for item in result.items
    ]
    _emit(
        ctx,
        result,
        ["Node", "Application", "Last backup", "Count", "Verified", "Schedule"],
        rows,
        "No applications.",
    )


@fleet_cli.command("updates", read_only=True)
@_node_option
@json_option("Print the view as JSON.")
@pass_context
def updates_command(ctx: Context, nodes: tuple[str, ...]) -> None:
    """Every node's Noust (installed and available) and pending system updates."""
    result = _gather("updates", nodes, True)
    rows = []
    for item in result.items:
        noust = item.get("noust") or {}
        os_updates = ((item.get("os") or {}).get("updates") or {}) if item.get("os") else {}
        reboot = ((item.get("os") or {}).get("reboot") or {}) if item.get("os") else {}
        rows.append(
            [
                str(item["node"]),
                str(noust.get("current_version") or item.get("version") or "-"),
                str(noust.get("latest_version") or "-"),
                str(noust.get("update_state") or "-"),
                "-" if os_updates.get("pending") is None else str(os_updates["pending"]),
                "-" if os_updates.get("security") is None else str(os_updates["security"]),
                "yes"
                if reboot.get("required")
                else ("no" if reboot.get("required") is False else "-"),
            ]
        )
    _emit(
        ctx,
        result,
        ["Node", "Noust", "Available", "State", "OS pending", "Security", "Reboot"],
        rows,
        "No nodes answered.",
    )


@fleet_cli.command("activity", read_only=True)
@_node_option
@click.option("--limit", type=click.IntRange(1, 200), default=20, help="Events per node.")
@json_option("Print the view as JSON.")
@pass_context
def activity_command(ctx: Context, nodes: tuple[str, ...], limit: int) -> None:
    """What happened lately on every node, newest first."""
    result = _gather("activity", nodes, True, limit=limit)
    rows = [
        [
            str(item.get("timestamp") or ""),
            str(item["node"]),
            str(item.get("actor") or "-"),
            str(item.get("action") or ""),
            str(item.get("resource") or "-"),
            str(item.get("result") or ""),
        ]
        for item in result.items
    ]
    _emit(
        ctx,
        result,
        ["When", "Node", "Actor", "Action", "Resource", "Result"],
        rows,
        "Nothing recorded.",
    )


# ---------------------------------------------------------------- actions


def _print_plan(ctx: Context, the_plan: Plan) -> None:
    """
    Show what a bulk action will do, server by server.

    Args:
        ctx: The CLI context.
        the_plan: The plan.
    """
    logger = ctx.logger
    described = the_plan.to_dict()
    runs = described["summary"]["run"]
    logger.info(
        f"{described['title']} on {runs} server{'s' if runs != 1 else ''}, "
        f"{the_plan.serial} at a time"
        + (
            f", stopping after {the_plan.max_failures} failure(s)"
            if the_plan.max_failures is not None
            else ""
        )
        + (f", {the_plan.request.canary} first" if the_plan.request.canary else "")
    )
    logger.table(
        ["Node", "Batch", "Will", "Why"],
        [
            [
                node.node,
                str(node.batch + (0 if the_plan.request.canary else 1)),
                "run" if node.state == "queued" else node.state,
                node.step or ""
                if node.state != "queued"
                else ("needs sudo mode" if node.requires_elevation else ""),
            ]
            for node in the_plan.nodes
        ],
    )
    for note in the_plan.notes:
        logger.info(note)


class _TerminalReporter:
    """A fleet job's progress, printed as it happens."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def log(self, message: str, level: str = "info") -> None:
        """Print a line (to standard error under --json, so stdout stays JSON)."""
        if self._ctx.json_output:
            click.echo(message, err=True)
            return
        logger = self._ctx.logger
        if level == "success":
            logger.success(message)
        elif level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def publish(self, snapshot: dict[str, Any], done: int, step: str) -> None:
        """Nothing to publish at a terminal: every line was printed."""


def _run(ctx: Context, fleet_request: FleetRequest, assume_yes: bool, plan_only: bool) -> None:
    """
    Plan a bulk action, show it, and run it here unless only planning.

    Args:
        ctx: The CLI context.
        fleet_request: The request.
        assume_yes: Do not ask before running.
        plan_only: Only show the plan.
    """
    from dataclasses import replace

    from noust.fleet.aggregate import cli_asker
    from noust.fleet.nodes import NodeManager
    from noust.web.fleet_jobs import FleetJobs, Runner, new_job_id, plan

    manager = NodeManager()
    # The terminal is root on this central: the master token's authority,
    # which sudo mode does not ask for, so the nodes are told it is confirmed.
    asker = replace(cli_asker(), elevated=True)
    the_plan = plan(fleet_request, manager=manager, asker=asker)
    if plan_only or ctx.dry_run:
        if ctx.json_output:
            click.echo(json.dumps({"plan": the_plan.to_dict()}))
        else:
            _print_plan(ctx, the_plan)
        return
    if not the_plan.batches():
        raise ValidationError(
            "No selected server can run this action",
            details="Every server was skipped; 'noust fleet run ... --plan' says why for each.",
        )
    if not ctx.json_output:
        _print_plan(ctx, the_plan)
    if not assume_yes and not click.confirm("Run it?", default=False, err=ctx.json_output):
        ctx.logger.info("Nothing was run.")
        return
    job_id = new_job_id()
    jobs = FleetJobs()
    jobs.create(job_id, the_plan, created_by=asker.actor)
    snapshot = Runner(
        plan=the_plan,
        job_id=job_id,
        manager=manager,
        asker=asker,
        reporter=_TerminalReporter(ctx),
        elevated_until=float("inf"),
        jobs=jobs,
    ).run()
    if ctx.json_output:
        click.echo(json.dumps(snapshot))
    else:
        summary = snapshot["summary"]
        ctx.logger.blank()
        ctx.logger.info(
            f"Fleet job {job_id} {snapshot['status']}: {summary['succeeded']} succeeded, "
            f"{summary['failed'] + summary['unreachable'] + summary['refused']} failed, "
            f"{summary['skipped']} skipped"
        )
        if snapshot["status"] != "succeeded":
            ctx.logger.info(f"Retry the failed ones: noust fleet retry {job_id}")
    if snapshot["status"] != "succeeded":
        click.get_current_context().exit(1)


def _options(pairs: tuple[str, ...]) -> dict[str, Any]:
    """
    Read ``--option key=value`` pairs; values are JSON when they parse as JSON.

    Args:
        pairs: The pairs.

    Returns:
        The options.

    Raises:
        ValidationError: A pair has no ``=``.
    """
    options: dict[str, Any] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise ValidationError(f"An option is key=value, not {pair!r}", field="option")
        try:
            options[key] = json.loads(value)
        except ValueError:
            options[key] = value
    return options


@fleet_cli.command("run")
@click.argument(
    "action",
    type=click.Choice(
        [
            "certs_renew",
            "backups_run",
            "backups_verify",
            "apps_update",
            "apps_restart",
            "noust_update",
            "os_updates",
        ]
    ),
)
@click.option("--nodes", default="", help="Comma-separated nodes.")
@click.option(
    "--label", "labels", multiple=True, help="Nodes carrying KEY=VALUE (repeat: all must match)."
)
@click.option("--serial", default=None, help="Nodes at a time: a number or a percentage (25%).")
@click.option(
    "--max-failures",
    type=int,
    default=None,
    help="Failures tolerated before the rest are skipped; -1 never stops.",
)
@click.option(
    "--canary", default=None, help="A node that goes alone first; if it fails, nothing else runs."
)
@click.option(
    "--domains",
    default=None,
    help="Only these applications (comma-separated), for app and backup actions.",
)
@click.option(
    "--option",
    "option_pairs",
    multiple=True,
    help="An action option, KEY=VALUE (force=true, scope=all).",
)
@click.option("--plan", "plan_only", is_flag=True, help="Only show what would happen.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask before running.")
@json_option("Print the plan, then the job's result, as JSON.")
@pass_context
def run_command(
    ctx: Context,
    action: str,
    nodes: str,
    labels: tuple[str, ...],
    serial: str | None,
    max_failures: int | None,
    canary: str | None,
    domains: str | None,
    option_pairs: tuple[str, ...],
    plan_only: bool,
    assume_yes: bool,
) -> None:
    """
    Run ACTION on several nodes: renew certificates, back up, update, restart, update Noust or the OS.

    Nodes are chosen by name (--nodes) or by label (--label env=prod). The plan
    is shown first; nodes that cannot run it (too old, not allowed, nothing to
    do) are skipped with the reason. The exit status is 1 when any node failed.
    """
    from noust.fleet.labels import parse_selector
    from noust.web.fleet_jobs import FleetRequest

    options = _options(option_pairs)
    if domains:
        options["domains"] = [domain.strip() for domain in domains.split(",") if domain.strip()]
    fleet_request = FleetRequest(
        action=action,
        nodes=[name.strip() for name in nodes.split(",") if name.strip()],
        labels=parse_selector(labels),
        serial=int(serial) if serial and serial.isdigit() else serial,
        max_failures=max_failures,
        canary=canary,
        options=options,
    )
    _run(ctx, fleet_request, assume_yes, plan_only)


@fleet_cli.command("retry")
@click.argument("job_id")
@click.option("--plan", "plan_only", is_flag=True, help="Only show what would happen.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask before running.")
@json_option("Print the plan, then the job's result, as JSON.")
@pass_context
def retry_command(ctx: Context, job_id: str, plan_only: bool, assume_yes: bool) -> None:
    """Run a fleet job again on its nodes that failed or were not reached."""
    from noust.web.fleet_jobs import retry_request

    _run(ctx, retry_request(job_id), assume_yes, plan_only)


@fleet_cli.command("jobs", read_only=True)
@click.argument("job_id", required=False)
@click.option("--limit", type=click.IntRange(1, 200), default=20, help="How many jobs.")
@json_option("Print the jobs, or the job, as JSON.")
@pass_context
def jobs_command(ctx: Context, job_id: str | None, limit: int) -> None:
    """List the fleet jobs, or show one with every node's state and words."""
    from noust.web.fleet_jobs import FleetJobs

    jobs = FleetJobs()
    if job_id is None:
        listing = jobs.list(limit)
        if ctx.json_output:
            click.echo(json.dumps({"jobs": listing}))
            return
        if not listing:
            ctx.logger.info("No fleet job has run yet.")
            return
        ctx.logger.table(
            ["Job", "Action", "Status", "Started", "Succeeded", "Failed", "Skipped"],
            [
                [
                    job["job_id"],
                    job["title"],
                    job["status"],
                    str(job["created_at"]),
                    str(job["summary"]["succeeded"]),
                    str(sum(job["summary"][s] for s in ("failed", "unreachable", "refused"))),
                    str(job["summary"]["skipped"]),
                ]
                for job in listing
            ],
        )
        return
    job = jobs.get(job_id)
    if job is None:
        raise ValidationError(
            f"No fleet job {job_id}", details="List them with 'noust fleet jobs'."
        )
    if ctx.json_output:
        click.echo(json.dumps(job))
        return
    ctx.logger.info(f"{job['title']} ({job['job_id']}): {job['status']}")
    ctx.logger.table(
        ["Node", "State", "Step"],
        [[node["node"], node["state"], node.get("step") or ""] for node in job["nodes"]],
    )
    for node in job["nodes"]:
        if node.get("output") and node["state"] not in ("succeeded", "queued"):
            ctx.logger.blank()
            ctx.logger.warning(f"{node['node']}:")
            click.echo(node["output"])


# ----------------------------------------------------------------- labels


@node_cli.command("label")
@click.argument("name")
@click.argument("changes", nargs=-1)
@json_option("Print the node's labels as JSON.")
@pass_context
def label_command(ctx: Context, name: str, changes: tuple[str, ...]) -> None:
    """
    Show or change NAME's labels: KEY=VALUE sets one, KEY- removes it.

    Labels are this central's grouping of its nodes (env=prod); 'noust fleet
    run --label env=prod' acts on every node carrying it. The node never sees them.
    """
    from noust.fleet.audit import audit
    from noust.fleet.labels import NodeLabels, parse_label, validate_key

    labels = NodeLabels()
    if changes and not ctx.dry_run:
        wanted: dict[str, str] = {}
        removed: list[str] = []
        for change in changes:
            if change.endswith("-") and "=" not in change:
                removed.append(validate_key(change[:-1]))
            else:
                key, value = parse_label(change)
                wanted[key] = value
        current = labels.change(name, set_labels=wanted, remove=removed)
        audit("fleet.node.label", "success", resource=f"node:{name}", detail=", ".join(changes))
    else:
        current = labels.of(name)
    if ctx.json_output:
        click.echo(json.dumps({"node": name, "labels": current}))
        return
    if ctx.dry_run and changes:
        ctx.logger.info(f"would change {name}'s labels: {' '.join(changes)}")
        return
    if not current:
        ctx.logger.info(f"{name} carries no labels. Add one: noust node label {name} env=prod")
        return
    click.echo(" ".join(f"{key}={value}" for key, value in current.items()))
