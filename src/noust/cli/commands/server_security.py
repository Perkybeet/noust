# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust server security``: hardening checks, SSH, the firewall, fail2ban, accepted risks.

The other client of :class:`~noust.managers.server.security.ServerSecurity`,
after ``/api/server/security``: the same checks, guards, confirm-or-revert and
audit records, in the same words. This module parses arguments and prints.

``noust server`` (:mod:`noust.cli.commands.server`) loads this module by name
and calls :func:`register`, so the group joins it without an edit there.

A change to sshd or the firewall ends pending: it undoes itself after 5
minutes unless ``noust server security confirm`` runs after a *new* SSH login
(open a second terminal, log in, confirm from there or from here).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.managers.server.security import ServerSecurity
from noust.managers.server.security_catalog import CATALOG
from noust.managers.server.security_checks import CheckReport
from noust.managers.server.security_ssh import SSH_FIXES

#: How each status is shown.
_STATUS = {
    "fail": ("error", "critical"),
    "warn": ("warning", "warning"),
    "unknown": ("warning", "unknown"),
    "accepted": ("info", "accepted"),
    "pass": ("ok", "ok"),
    "n/a": ("info", "n/a"),
}


def _helpers() -> Any:
    """
    The helpers ``noust server`` shares: actor, root check, confirmation, JSON.

    Imported when used: ``noust server`` imports this module while it is
    still being imported itself.

    Returns:
        The ``noust.cli.commands.server`` module.
    """
    from noust.cli.commands import server

    return server


def _security(ctx: Context, *, verbose_output: bool = True) -> ServerSecurity:
    """
    The security manager for this command, printing what changes run.

    Args:
        ctx: The command's context.
        verbose_output: Print each command and its output as it runs.

    Returns:
        The manager.
    """
    logger = ctx.logger
    on_output = None if ctx.json_output or not verbose_output else logger.substep
    return ServerSecurity(actor=_helpers()._actor(), on_output=on_output)


def _json(payload: Any) -> None:
    _helpers()._json(payload)


def _fail(ctx: Context, exc: NoustError) -> NoReturn:
    """
    Report a refusal or a failure the way every Noust command does, and exit 1.

    Args:
        ctx: The command's context.
        exc: The error.

    Raises:
        click.exceptions.Exit: Always.
    """
    if ctx.json_output:
        _json(
            {
                "error": type(exc).__name__.lower(),
                "detail": exc.message,
                "hint": exc.details or None,
                "output": exc.output,
                "required": getattr(exc, "required", None),
            }
        )
    else:
        ctx.logger.error(exc.message, exc.details)
        if exc.output:
            click.echo(exc.output)
    raise click.exceptions.Exit(1)


def _run(ctx: Context, operation: str, params: dict[str, Any], *, action: str) -> dict[str, Any]:
    """
    Check and make one change, as the console's job does.

    Args:
        ctx: The command's context.
        operation: One of :data:`~noust.managers.server.security.OPERATIONS`.
        params: Its parameters.
        action: What it is, for the root check.

    Returns:
        What was done.
    """
    _helpers()._require_root(ctx, action)
    security = _security(ctx)
    try:
        security.preflight(operation, params)
        result = security.execute(operation, params)
    except NoustError as exc:
        _fail(ctx, exc)
    return result


def _print_change(ctx: Context, result: dict[str, Any]) -> None:
    """
    Say what a change did, and what to do next when it waits for confirmation.

    Args:
        ctx: The command's context.
        result: What :meth:`ServerSecurity.execute` returned.
    """
    if ctx.json_output:
        _json(result)
        return
    change = result.get("change")
    if not isinstance(change, dict):
        ctx.logger.success("Done")
        return
    logger = ctx.logger
    expires = datetime.fromtimestamp(change["expires_at"], tz=timezone.utc).astimezone()
    logger.success(f"Applied: {change['title']} (change {change['id']})")
    for key, value in change.get("after", {}).items():
        logger.key_value(key, f"{change.get('before', {}).get(key, '?')} -> {value}")
    logger.blank()
    logger.warning(
        f"It undoes itself at {expires:%H:%M:%S} unless you confirm it. Keep this session open, "
        "log in again from a NEW terminal, then run:"
    )
    logger.info(f"  noust server security confirm {change['id']}")


def _proof_line(change: dict[str, Any]) -> str:
    """
    Whether the new login a pending change waits for is already on record.

    The state is the console's, read from the same fields the API sends: it comes
    from the code that ``confirm`` refuses on.

    Args:
        change: A pending change as :meth:`ServerSecurity.describe_change` returns it.

    Returns:
        One sentence: the login seen, why none can be seen, or that it is awaited.
    """
    if not change["proof_readable"]:
        return f"Cannot see new logins: {change['proof_error']}"
    login = change["proof_login"]
    if login is None:
        return "Waiting for a new SSH login: a session that was already open proves nothing"
    when = datetime.fromtimestamp(login["at"]).strftime("%H:%M:%S")
    return f"New login seen: {login['user']} from {login['source']} at {when}"


def _print_checks(ctx: Context, report: CheckReport, *, show_all: bool) -> None:
    """
    Print the checks: the findings, and every check with ``--all``.

    Args:
        ctx: The command's context.
        report: The checks.
        show_all: Print passed and non-applicable checks too.
    """
    logger = ctx.logger
    counts = report.counts()
    logger.header("Security hardening")
    logger.info(
        f"{counts['critical']} critical, {counts['warning']} warning(s), {counts['accepted']} "
        f"accepted, {counts['unknown']} unknown, {counts['passed']} passed"
    )
    logger.blank()
    for check in report.checks:
        if not show_all and check.status in ("pass", "n/a"):
            continue
        outcome, label = _STATUS[check.status]
        logger.check(f"{check.id} [{label}]", check.reason, outcome)
        for line in check.evidence[:5]:
            logger.list_item(line, indent=6)
        if check.accepted is not None:
            logger.list_item(
                f"accepted by {check.accepted.accepted_by} until {check.accepted.expires_at}: "
                f"{check.accepted.reason}",
                indent=6,
            )
        elif check.fix is not None and check.status in ("fail", "warn"):
            if check.fix.kind == "automatic":
                logger.list_item(f"fix: noust server security fix {check.id}", indent=6)
            elif check.fix.cli:
                logger.list_item(f"fix: {check.fix.cli}", indent=6)
            if check.fix.blocked:
                logger.list_item(f"not automatic now: {check.fix.blocked}", indent=6)
            for step in check.fix.steps if check.fix.kind == "guided" else ():
                logger.list_item(step, indent=8)


# The group ------------------------------------------------------------------------


@click.group("security", cls=NoustGroup)
def security() -> None:
    """Hardening checks, SSH, the firewall, fail2ban and accepted risks."""


def register(group: click.Group) -> None:
    """
    Join ``noust server``: what :mod:`noust.cli.commands.server` calls.

    Args:
        group: The ``server`` group.
    """
    group.add_command(security)


@security.command("status", read_only=True)
@json_option("Print the summary as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """
    Show the security summary: the quick checks' counts, what needs attention, pending changes.

    The same summary as the console's Security tab. 'checks' lists every check.
    """
    security = _security(ctx, verbose_output=False)
    security.checks(refresh=True, host_checks=False)
    data = security.overview()
    if ctx.json_output:
        _json(data)
        return
    logger = ctx.logger
    counts = data["counts"]
    logger.header("Security")
    logger.info(
        f"{counts['critical']} critical, {counts['warning']} warning(s), "
        f"{counts['accepted']} accepted, {counts['passed']} passed"
    )
    for check in data["attention"][:5]:
        outcome, label = _STATUS[check["status"]]
        logger.check(f"{check['id']} [{label}]", check["reason"], outcome)
    for change in data["pending"]:
        logger.warning(
            f"Waiting for confirmation: {change['title']} "
            f"(noust server security confirm {change['id']})"
        )
        logger.info(f"  {_proof_line(change)}")


@security.command("checks", read_only=True)
@click.option("--all", "show_all", is_flag=True, help="Also list the checks that pass.")
@click.option(
    "--quick", is_flag=True, help="Skip the package manager and system checks (seconds faster)."
)
@json_option("Print every check as JSON.")
@pass_context
def checks_command(ctx: Context, show_all: bool, quick: bool) -> None:
    """
    Run the hardening checks and print what needs attention.

    Each finding comes with the reason, what the system said and how to fix
    it: 'noust server security fix <id>' when Noust can do it safely, or the
    exact steps. Exits 1 when a critical finding is open.
    """
    report = _security(ctx, verbose_output=False).checks(refresh=True, host_checks=not quick)
    if ctx.json_output:
        _json(report.to_dict())
    else:
        _print_checks(ctx, report, show_all=show_all)
    if report.counts()["critical"]:
        raise click.exceptions.Exit(1)


@security.command("fix")
@click.argument("check_id", type=click.Choice(sorted(CATALOG)), metavar="CHECK_ID")
@click.option("--epel", is_flag=True, help="Allow enabling EPEL (fail2ban on RHEL rebuilds).")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print what was done as JSON.")
@pass_context
def fix_command(ctx: Context, check_id: str, epel: bool, yes: bool) -> None:
    """
    Apply a check's automatic fix, with its guard.

    SSH and firewall fixes wait for confirmation and undo themselves after
    5 minutes without it.
    """
    _helpers()._confirm(ctx, f"Apply the fix of {check_id}?", yes=yes)
    _print_change(
        ctx, _run(ctx, "check.fix", {"check_id": check_id, "epel": epel}, action="security fix")
    )


@security.command("pending", read_only=True)
@json_option("Print the changes as JSON.")
@pass_context
def pending_command(ctx: Context) -> None:
    """List the SSH and firewall changes, the ones waiting for confirmation first."""
    changes = _security(ctx, verbose_output=False).described_changes()
    if ctx.json_output:
        _json(changes)
        return
    if not changes:
        ctx.logger.info("No SSH or firewall change has been made with Noust.")
        return
    ordered = sorted(changes, key=lambda item: item["status"] != "pending")
    rows = [
        [
            change["id"],
            change["status"],
            change["title"],
            datetime.fromtimestamp(change["applied_at"]).strftime("%Y-%m-%d %H:%M"),
            change["actor"],
        ]
        for change in ordered
    ]
    ctx.logger.table(["Change", "Status", "What", "Applied", "By"], rows)
    for change in ordered:
        if change["status"] == "pending":
            ctx.logger.info(f"{change['id']}: {_proof_line(change)}")


def _only_pending(ctx: Context, change_id: str | None) -> str:
    """
    The change a confirm or revert is about: the one given, or the only pending one.

    Args:
        ctx: The command's context.
        change_id: What was given.

    Returns:
        The change id.

    Raises:
        click.UsageError: None was given and there is not exactly one pending.
    """
    if change_id:
        return change_id
    waiting = [
        change
        for change in _security(ctx, verbose_output=False).pending()
        if change.status == "pending"
    ]
    if len(waiting) != 1:
        raise click.UsageError(
            "Name the change: " + (", ".join(c.id for c in waiting) or "none is pending")
        )
    return waiting[0].id


@security.command("confirm")
@click.argument("change_id", required=False)
@json_option("Print the change as JSON.")
@pass_context
def confirm_command(ctx: Context, change_id: str | None) -> None:
    """
    Keep a pending change, once a new SSH login since it was applied is on record.

    Log in from a new terminal first: a session that was already open proves
    nothing, because reloading sshd or changing the firewall leaves open
    connections alone.
    """
    _helpers()._require_root(ctx, "security confirm")
    target = _only_pending(ctx, change_id)
    try:
        change = _security(ctx).confirm(target)
    except NoustError as exc:
        _fail(ctx, exc)
    if ctx.json_output:
        _json(change.to_dict())
        return
    ctx.logger.success(f"Kept: {change.title}")
    ctx.logger.info(change.resolution)


@security.command("revert")
@click.argument("change_id", required=False)
@json_option("Print the change as JSON.")
@pass_context
def revert_command(ctx: Context, change_id: str | None) -> None:
    """Undo a pending SSH or firewall change now."""
    _helpers()._require_root(ctx, "security revert")
    target = _only_pending(ctx, change_id)
    try:
        change = _security(ctx).revert(target)
    except NoustError as exc:
        _fail(ctx, exc)
    if ctx.json_output:
        _json(change.to_dict())
        return
    ctx.logger.success(f"Undone: {change.title} ({change.status})")


@security.command("accept")
@click.argument("check_id", type=click.Choice(sorted(CATALOG)), metavar="CHECK_ID")
@click.option(
    "--why", "why", required=True, help="Why the risk is acceptable (10 to 500 characters)."
)
@click.option("--until", "until", default=None, metavar="DATE", help="Until when: 2026-12-31.")
@click.option("--days", type=click.IntRange(1, 365), default=None, help="For this many days.")
@json_option("Print the acceptance as JSON.")
@pass_context
def accept_command(
    ctx: Context, check_id: str, why: str, until: str | None, days: int | None
) -> None:
    """
    Accept a check's finding as a documented risk, until a date (a year at most).

    The check shows as accepted - never as passed - until then, and is back
    on the list afterwards. Recorded in the audit log.
    """
    _helpers()._require_root(ctx, "security accept")
    if (until is None) == (days is None):
        raise click.UsageError("Give either --until DATE or --days N.")
    if days is not None:
        moment = datetime.now(timezone.utc) + timedelta(days=days)
    else:
        try:
            moment = datetime.fromisoformat(str(until)).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        except ValueError as exc:
            raise click.BadParameter("use a date such as 2026-12-31", param_hint="--until") from exc
    try:
        risk = _security(ctx).accept_risk(check_id, reason=why, until=moment)
    except NoustError as exc:
        _fail(ctx, exc)
    if ctx.json_output:
        _json(risk.to_dict())
        return
    ctx.logger.success(f"Accepted {check_id} until {risk.expires_at}")


@security.command("unaccept")
@click.argument("check_id", type=click.Choice(sorted(CATALOG)), metavar="CHECK_ID")
@pass_context
def unaccept_command(ctx: Context, check_id: str) -> None:
    """Withdraw a check's acceptance, so its finding shows again."""
    _helpers()._require_root(ctx, "security unaccept")
    risk = _security(ctx).revoke_risk(check_id)
    if risk is None:
        ctx.logger.info(f"No accepted risk holds for {check_id}.")
        return
    ctx.logger.success(f"Withdrawn: {check_id}")


@security.command("risks", read_only=True)
@json_option("Print the acceptances as JSON.")
@pass_context
def risks_command(ctx: Context) -> None:
    """List accepted risks, withdrawn and expired ones included."""
    risks = _security(ctx, verbose_output=False).accepted_risks()
    if ctx.json_output:
        _json([risk.to_dict() for risk in risks])
        return
    if not risks:
        ctx.logger.info("No risk has been accepted.")
        return
    now = datetime.now(timezone.utc)
    rows = [
        [
            risk.check_id,
            "active" if risk.active(now) else ("withdrawn" if risk.revoked_at else "expired"),
            risk.expires_at,
            risk.accepted_by,
            risk.reason,
        ]
        for risk in risks
    ]
    ctx.logger.table(["Check", "State", "Until", "By", "Why"], rows)


# SSH ----------------------------------------------------------------------------------


@security.group("ssh", cls=NoustGroup)
def ssh() -> None:
    """sshd's effective configuration, its fixes and the administrators' keys."""


@ssh.command("status", read_only=True)
@json_option("Print sshd's state as JSON.")
@pass_context
def ssh_status_command(ctx: Context) -> None:
    """Show sshd's effective values (sshd -T -C), Noust's drop-in and the open sessions."""
    data = _security(ctx, verbose_output=False).ssh_status()
    if ctx.json_output:
        _json(data)
        return
    logger = ctx.logger
    if data["error"]:
        logger.error(data["error"], data.get("error_output") or "")
        raise click.exceptions.Exit(1)
    logger.header("sshd, as it runs")
    for key, value in data["effective"].items():
        logger.key_value(key, value)
    logger.blank()
    logger.key_value("Root password", data["root_password"])
    logger.key_value(
        "Noust's drop-in", "present" if data["dropin"] else "none (nothing changed by Noust)"
    )
    if not data["include_present"]:
        logger.warning("sshd_config does not include sshd_config.d: Noust's fixes cannot apply.")
    logger.blank()
    for name, plan in data["fixes"].items():
        state = "applied" if not plan["needed"] else "available" if plan["allowed"] else "blocked"
        logger.key_value(name, f"{state}: {plan['title']}")


@ssh.command("plan", read_only=True)
@click.argument("fix", type=click.Choice(sorted(SSH_FIXES)))
@json_option("Print the plan as JSON.")
@pass_context
def ssh_plan_command(ctx: Context, fix: str) -> None:
    """Show what a fix would change (before and after) and whether its guard holds."""
    plan = _security(ctx, verbose_output=False).ssh_plan(fix).to_dict()
    if ctx.json_output:
        _json(plan)
        return
    logger = ctx.logger
    logger.header(plan["title"])
    for change in plan["changes"]:
        logger.key_value(
            change["directive"], f"{change['before'] or '(unset)'} -> {change['after']}"
        )
    if plan["proof"]:
        logger.info(plan["proof"])
    for blocker in plan["blockers"]:
        logger.warning(blocker)
    for step in plan["guidance"]:
        logger.list_item(step)


@ssh.command("harden")
@click.argument("fix", type=click.Choice(sorted(SSH_FIXES)))
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print the pending change as JSON.")
@pass_context
def ssh_harden_command(ctx: Context, fix: str, yes: bool) -> None:
    """
    Apply an sshd fix safely: proof of another way in, sshd -t, reload, verify.

    It undoes itself after 5 minutes unless confirmed after a new login.
    """
    _helpers()._confirm(ctx, f"Apply '{SSH_FIXES[fix].title}'?", yes=yes)
    _print_change(ctx, _run(ctx, "ssh.fix", {"fix": fix}, action="ssh harden"))


@ssh.command("keys", read_only=True)
@json_option("Print the keys as JSON.")
@pass_context
def ssh_keys_command(ctx: Context) -> None:
    """List root's and every administrator's keys, with when each was last used."""
    accounts = _security(ctx, verbose_output=False).ssh_keys()
    if ctx.json_output:
        _json(accounts)
        return
    rows = []
    for account in accounts:
        for file in account["files"]:
            for key in file["keys"]:
                used = (
                    datetime.fromtimestamp(key["last_used"]).strftime("%Y-%m-%d")
                    if key["last_used"]
                    else "-"
                )
                rows.append(
                    [
                        account["user"],
                        key["fingerprint"],
                        f"{key['type']} {key['bits'] or ''}".strip(),
                        key["kind"] + (" (in use)" if key["in_use"] else ""),
                        used,
                        key["comment"] or "-",
                    ]
                )
    ctx.logger.table(["User", "Fingerprint", "Type", "Kind", "Last used", "Comment"], rows)
    for account in accounts:
        for file in account["files"]:
            for problem in file["problems"]:
                ctx.logger.warning(problem)


@ssh.command("add-key")
@click.option("--user", default="root", show_default=True, help="The account.")
@click.option(
    "--file",
    "key_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="A .pub file.",
)
@click.argument("public_key", required=False)
@json_option("Print what was done as JSON.")
@pass_context
def ssh_add_key_command(
    ctx: Context, user: str, key_file: Path | None, public_key: str | None
) -> None:
    """Let a public key log in as root or an account that can become root."""
    if key_file is not None:
        public_key = key_file.read_text(encoding="utf-8").strip()
    if not public_key:
        raise click.UsageError("Give the key, or --file with a .pub file.")
    result = _run(ctx, "ssh.key.add", {"user": user, "public_key": public_key}, action="add-key")
    if ctx.json_output:
        _json(result)
        return
    if result["added"]:
        ctx.logger.success(f"Added {result['fingerprint']} to {result['file']}")
    else:
        ctx.logger.info(f"{result['fingerprint']} was already in {result['file']}")
    for problem in result.get("strict_mode_problems", []):
        ctx.logger.warning(problem)


@ssh.command("remove-key")
@click.argument("fingerprint")
@click.option("--user", default="root", show_default=True, help="The account.")
@click.option("--force", is_flag=True, help="Remove it even when the guard refuses.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print what was removed as JSON.")
@pass_context
def ssh_remove_key_command(
    ctx: Context, fingerprint: str, user: str, force: bool, yes: bool
) -> None:
    """
    Remove a key by its SHA256 fingerprint.

    Refused, unless --force, for a central's tunnel key, for a key an SSH
    session open now logged in with, and for the last key while passwords
    are off.
    """
    if force:
        _helpers()._confirm(ctx, f"Remove {fingerprint} from {user} despite the guard?", yes=yes)
    params = {"user": user, "fingerprint": fingerprint, "force": force}
    result = _run(ctx, "ssh.key.remove", params, action="remove-key")
    if ctx.json_output:
        _json(result)
        return
    ctx.logger.success(f"Removed {fingerprint} from {user}")
    for reason in result.get("overridden", []):
        ctx.logger.warning(f"Overridden: {reason}")


# Firewall --------------------------------------------------------------------------------


@security.group("firewall", cls=NoustGroup)
def firewall() -> None:
    """The firewall against the ports that really answer, changed without locking you out."""


@firewall.command("status", read_only=True)
@json_option("Print the firewall and the ports as JSON.")
@pass_context
def firewall_status_command(ctx: Context) -> None:
    """Show the firewall's rules and every port that answers, with the verdict for each."""
    data = _security(ctx, verbose_output=False).firewall_status()
    if ctx.json_output:
        _json(data)
        return
    logger = ctx.logger
    state = data["firewall"]
    logger.header(
        f"{state['backend']}: {'active' if state['active'] else 'inactive'}"
        + (f", default {state['default_incoming']} incoming" if state["default_incoming"] else "")
    )
    for warning in state["warnings"]:
        logger.warning(warning)
    if state["rules"]:
        logger.table(
            ["Id", "Rule", "Comment"],
            [[rule["id"], rule["spec"], rule["comment"] or "-"] for rule in state["rules"]],
        )
    logger.blank()
    logger.table(
        ["Port", "Address", "Process", "Verdict", "Note"],
        [
            [
                f"{port['port']}/{port['proto']}",
                port["address"],
                port["process"] or "-",
                port["verdict"] + (f" ({', '.join(port['sources'])})" if port["sources"] else ""),
                port["risky"] or ("expected" if port["baseline"] else ""),
            ]
            for port in data["ports"]
        ],
    )


def _rule_command(
    ctx: Context, action: str, port: int, proto: str, source: str, comment: str
) -> None:
    params = {"action": action, "port": port, "proto": proto, "source": source, "comment": comment}
    _print_change(ctx, _run(ctx, "firewall.add", params, action=f"firewall {action}"))


@firewall.command("allow")
@click.argument("port", type=click.IntRange(1, 65535))
@click.option("--proto", type=click.Choice(["tcp", "udp", "any"]), default="tcp", show_default=True)
@click.option("--from", "source", default="any", show_default=True, help="An address or network.")
@click.option("--comment", default="", help="A note on the rule.")
@json_option("Print the pending change as JSON.")
@pass_context
def firewall_allow_command(ctx: Context, port: int, proto: str, source: str, comment: str) -> None:
    """Open a port, to everyone or to one address or network. Undone unless confirmed."""
    _rule_command(ctx, "allow", port, proto, source, comment)


@firewall.command("deny")
@click.argument("port", type=click.IntRange(1, 65535))
@click.option("--proto", type=click.Choice(["tcp", "udp", "any"]), default="tcp", show_default=True)
@click.option("--from", "source", default="any", show_default=True, help="An address or network.")
@click.option("--comment", default="", help="A note on the rule.")
@json_option("Print the pending change as JSON.")
@pass_context
def firewall_deny_command(ctx: Context, port: int, proto: str, source: str, comment: str) -> None:
    """Close a port. Never SSH's or a public console's. Undone unless confirmed."""
    _rule_command(ctx, "deny", port, proto, source, comment)


@firewall.command("delete")
@click.argument("rule_id")
@json_option("Print the pending change as JSON.")
@pass_context
def firewall_delete_command(ctx: Context, rule_id: str) -> None:
    """Delete a rule by the id 'firewall status' shows. Undone unless confirmed."""
    _print_change(ctx, _run(ctx, "firewall.delete", {"rule_id": rule_id}, action="firewall delete"))


@firewall.command("enable")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print the pending change as JSON.")
@pass_context
def firewall_enable_command(ctx: Context, yes: bool) -> None:
    """Turn ufw on, denying incoming by default, SSH and a public console allowed first."""
    _helpers()._confirm(ctx, "Turn the firewall on, denying incoming by default?", yes=yes)
    _print_change(ctx, _run(ctx, "firewall.enable", {}, action="firewall enable"))


@firewall.command("disable")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print the pending change as JSON.")
@pass_context
def firewall_disable_command(ctx: Context, yes: bool) -> None:
    """Turn the firewall off. It comes back on unless confirmed."""
    _helpers()._confirm(ctx, "Turn the firewall off?", yes=yes)
    _print_change(ctx, _run(ctx, "firewall.disable", {}, action="firewall disable"))


# fail2ban --------------------------------------------------------------------------------


@security.group("fail2ban", cls=NoustGroup)
def fail2ban() -> None:
    """fail2ban: its jails and bans, lifting a ban, installing it."""


@fail2ban.command("status", read_only=True)
@json_option("Print fail2ban's state as JSON.")
@pass_context
def fail2ban_status_command(ctx: Context) -> None:
    """Show whether fail2ban runs, its jails and the addresses banned now."""
    data = _security(ctx, verbose_output=False).fail2ban_status()
    if ctx.json_output:
        _json(data)
        return
    logger = ctx.logger
    if not data["installed"]:
        logger.info("fail2ban is not installed: noust server security fail2ban install")
    elif not data["running"]:
        logger.warning("fail2ban is installed but not running.")
        if data["error"]:
            click.echo(data["error"])
    for jail in data["jails"]:
        logger.key_value(
            jail["name"],
            f"{jail['currently_banned']} banned now ({jail['total_banned']} in total): "
            + (" ".join(jail["banned"]) or "-"),
        )
    for name in data["substitutes"]:
        logger.info(f"{name} is active")


@fail2ban.command("install")
@click.option(
    "--epel", is_flag=True, help="Allow enabling EPEL, where fail2ban comes from on RHEL rebuilds."
)
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print what was done as JSON.")
@pass_context
def fail2ban_install_command(ctx: Context, epel: bool, yes: bool) -> None:
    """Install fail2ban with an sshd jail that never bans the SSH sessions open now."""
    _helpers()._confirm(ctx, "Install and start fail2ban?", yes=yes)
    ignore = []
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if connection:
        ignore.append(connection[0])
    result = _run(
        ctx, "fail2ban.install", {"epel": epel, "ignore": ignore}, action="fail2ban install"
    )
    if ctx.json_output:
        _json(result)
        return
    ctx.logger.success(f"fail2ban watches SSH on port(s) {', '.join(map(str, result['ports']))}")
    ctx.logger.info(f"Never banned: {', '.join(result['ignored']) or 'loopback only'}")


@fail2ban.command("unban")
@click.argument("address")
@click.option("--jail", default=None, help="Only this jail.")
@json_option("Print what was done as JSON.")
@pass_context
def fail2ban_unban_command(ctx: Context, address: str, jail: str | None) -> None:
    """Lift the ban of an address."""
    result = _run(
        ctx, "fail2ban.unban", {"address": address, "jail": jail}, action="fail2ban unban"
    )
    if ctx.json_output:
        _json(result)
        return
    ctx.logger.success(f"Lifted the ban of {result['address']} in {', '.join(result['jails'])}")


#: What ``noust server`` adds when it does not call :func:`register`.
commands = (security,)

__all__ = ["commands", "register", "security"]
