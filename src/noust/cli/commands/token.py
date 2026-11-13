# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust token`` command group: named, scoped API tokens.

Before this existed, an API token could only be issued, listed or revoked from
the panel's settings screen: an operator scripting against the API from a
server with no browser had no way to mint the credential the script needs.
This is a thin front end over :class:`~noust.web.auth.TokenManager`, the exact
manager ``POST /api/auth/tokens`` and its siblings call, built over the panel's
own on-disk state (:class:`~noust.web.auth.SecurityConfig`'s state directory) -
a token issued here authenticates against the panel and vice versa, because
there is one store of them, not two.

A token is shown exactly once, at creation: only its salted hash is stored,
the same way the master token and a TOTP backup code are, so it cannot be
shown again afterwards.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.web_state import access_manager, token_manager
from noust.core.access import AccessStore
from noust.core.token_presets import PRESET_IDS

#: Scopes ``POST /api/auth/tokens`` accepts, in the order shown by --help.
SCOPES: tuple[str, ...] = ("read", "deploy", "admin")

#: The scope of a central's token for this server, which only ``noust fleet
#: authorize`` issues. Spelled here rather than imported: the console's module
#: is only imported when a command runs.
FLEET_SCOPE = "fleet"


class _ScopeChoice(click.Choice):
    """The token scopes, refusing ``fleet`` with where fleet tokens come from."""

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> Any:
        """
        Refuse ``fleet`` with a pointer, then validate like any choice.

        Args:
            value: What was typed.
            param: The option.
            ctx: The Click context.

        Returns:
            The scope.
        """
        if value == FLEET_SCOPE:
            self.fail(
                "fleet tokens are issued on the node by 'noust fleet authorize "
                "--central-key <key> --name <central>', which also installs the "
                "central's restricted SSH key and prints the join code for it.",
                param,
                ctx,
            )
        return super().convert(value, param, ctx)


def _fmt(timestamp: float | None) -> str:
    """
    Render a UNIX timestamp for a table cell.

    Args:
        timestamp: The timestamp, or None.

    Returns:
        A human-readable local time, or a placeholder for None.
    """
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")


def _bound(access: AccessStore, record: dict[str, Any]) -> str:
    """
    Args:
        access: The access store, to spell a scope out.
        record: A token record.

    Returns:
        The team, project or environment the token is bound to, or a dash.
    """
    if not record.get("bound_kind"):
        return "-"
    return access.scope_label(str(record["bound_kind"]), int(record["bound_id"])) or "(deleted)"


def _sudo(record: dict[str, Any]) -> str:
    """
    Args:
        record: A token record.

    Returns:
        Whether the token acts where sudo mode is asked: ``yes`` for one made to, ``no``
        for one that is refused there, ``legacy`` for one issued before 3.1, which was
        never asked (the rule it keeps until it is revoked).
    """
    allowed = record.get("allow_elevated")
    if allowed is None:
        return "legacy"
    return "yes" if allowed else "no"


def _expires(record: dict[str, Any]) -> str:
    """
    Args:
        record: A token record.

    Returns:
        When it expires, with how long is left, so a token about to lapse is
        seen in the list (I-32); ``never`` for one that only dies by revocation.
    """
    expires = record.get("expires_at")
    if not expires:
        return "never"
    left = float(expires) - time.time()
    if record.get("revoked_at"):
        return _fmt(expires)
    if left <= 0:
        return f"{_fmt(expires)} (expired)"
    days = int(left // 86400)
    hours = int(left // 3600)
    return f"{_fmt(expires)} (in {days}d)" if days >= 1 else f"{_fmt(expires)} (in {hours}h)"


@click.group("token", cls=NoustGroup)
def cli() -> None:
    """Manage API tokens: named, scoped credentials for scripts and automation."""


@cli.command("list")
@json_option("Print the tokens as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """
    List every API token ever issued, live and revoked alike.

    No output from this command ever shows a token: only its salted hash is
    stored, so it cannot be shown again after 'noust token create'.
    """
    records = token_manager().list_api_tokens()

    if ctx.json_output:
        click.echo(json.dumps({"tokens": records}))
        return

    logger = ctx.logger
    if not records:
        logger.info("No API tokens have been issued.")
        return

    access = AccessStore()
    logger.table(
        [
            "ID",
            "Name",
            "Scope",
            "Sudo",
            "Bound to",
            "From",
            "Created",
            "Expires",
            "Last used",
            "Revoked",
        ],
        [
            [
                str(record["id"]),
                record["name"],
                record["scope"],
                _sudo(record),
                _bound(access, record),
                ", ".join(record.get("allowed_cidrs") or []) or "anywhere",
                _fmt(record["created_at"]),
                _expires(record),
                _fmt(record["last_used_at"]),
                _fmt(record["revoked_at"]),
            ]
            for record in records
        ],
    )


@cli.command("create")
@click.argument("name")
@click.option(
    "--scope",
    type=_ScopeChoice(SCOPES),
    default="read",
    show_default=True,
    help="What the token may do.",
)
@click.option(
    "--expires-hours",
    type=int,
    default=None,
    help="Lifetime in hours. Omitted: the token only dies by revocation.",
)
@click.option(
    "--owner",
    default=None,
    help="Account the token acts for, with at most its role. Required under the ENS profile.",
)
@click.option(
    "--permission",
    "permissions",
    multiple=True,
    help="Narrow the token to this permission (repeat). The owner must hold it.",
)
@click.option(
    "--preset",
    type=click.Choice(PRESET_IDS),
    default=None,
    help=(
        "Narrow the token to what a job needs, by name: it holds those permissions and "
        "nothing else of its scope. Instead of --permission."
    ),
)
@click.option(
    "--team",
    default=None,
    help="Bind the token to this team: it sees nothing else (needs --owner).",
)
@click.option("--project", default=None, help="Bind it to this project of the team.")
@click.option("--env", "environment", default=None, help="Bind it to this environment.")
@click.option(
    "--allow-ip",
    "allow_ips",
    multiple=True,
    help="Accept the token only from this address or network (repeat). Omitted: from anywhere.",
)
@click.option(
    "--elevated",
    is_flag=True,
    help=(
        "Let the token act where sudo mode is asked, with nobody confirming each time "
        "(needs --owner). Every action it takes there is in the audit trail."
    ),
)
@pass_context
def create_command(
    ctx: Context,
    name: str,
    scope: str,
    expires_hours: int | None,
    owner: str | None,
    permissions: tuple[str, ...],
    preset: str | None,
    team: str | None,
    project: str | None,
    environment: str | None,
    allow_ips: tuple[str, ...],
    elevated: bool,
) -> None:
    """
    Issue a named, scoped API token. NAME must be unique across every token
    ever issued, live or revoked.

    The token is printed exactly once, here. Store it now: Noust only ever
    keeps a salted hash of it, the same as the master token, so it cannot be
    shown again.

    A token made for an account is refused wherever sudo mode is asked, because a
    token cannot confirm anything. --elevated is the explicit way to make one that
    is not (a pipeline that changes settings over the API): it needs --owner, it
    shows in 'noust token list', every action it takes there is audited, and a
    server whose compliance controls restrict tokens refuses it.
    """
    if elevated and not owner:
        raise click.UsageError(
            "--elevated needs --owner: sudo mode is a person's, and a token that acts in it "
            "must name whose."
        )
    if preset is not None and permissions:
        raise click.UsageError("Give --preset or --permission, not both.")
    manager = token_manager()
    account = manager.accounts.require(owner) if owner else None
    bound = access_manager().bound_scope(team=team, project=project, environment=environment)
    issued: dict[str, Any] = manager.create_api_token(
        name,
        scope,
        expires_hours,
        owner=account,
        created_by="cli",
        permissions=list(permissions) or None,
        preset=preset,
        allowed_cidrs=list(allow_ips) or None,
        bound=bound,
        allow_elevated=elevated,
    )

    logger = ctx.logger
    where = f", bound to {AccessStore().scope_label(*bound)}" if bound is not None else ""
    sudo = ", acts in sudo mode" if elevated else ""
    narrowed = f", holding {', '.join(issued['permissions'])}" if preset else ""
    logger.success(
        f"API token issued: {issued['name']} (scope: {issued['scope']}{where}{sudo}{narrowed})"
    )
    if not ctx.dry_run:
        from noust.core.audit import record

        record(
            "auth.token.create",
            target=f"token:{issued['id']}",
            details={
                "name": issued["name"],
                "scope": issued["scope"],
                "owner": owner,
                "sudo_mode": bool(issued.get("allow_elevated")),
            },
        )
    logger.blank()
    click.echo(f"Token: {issued['token']}")
    logger.blank()
    if ctx.dry_run:
        # A dry run opens the session database as a private in-memory copy
        # (noust.web.auth.SessionStore._rehearsal_copy), so this token was
        # never written to the one Noust actually authenticates against. It
        # looks real and is not: printing it without saying so is how an
        # operator pastes a credential into a script that then never works.
        logger.warning("Rehearsal: this token was not saved and will not authenticate.")
    else:
        logger.warning("This is the only time the token is shown. Store it now.")
        if elevated:
            logger.warning(
                "This token acts in sudo mode: nobody confirms what it does. Each action is in "
                f"the audit trail (auth.token.elevated). End it with: noust token revoke {issued['id']}"
            )


@cli.command("set")
@click.argument("token_id", type=int)
@click.option(
    "--allow-ip",
    "allow_ips",
    multiple=True,
    help="Accept the token only from this address or network (repeat); replaces the list.",
)
@click.option("--any-ip", is_flag=True, help="Accept the token from anywhere again.")
@pass_context
def set_command(ctx: Context, token_id: int, allow_ips: tuple[str, ...], any_ip: bool) -> None:
    """
    Change the networks a live API token is accepted from, without issuing another.

    The token keeps working from the new list the moment this returns, and a
    request from anywhere else is refused as a stolen credential would be.
    """
    if bool(allow_ips) == any_ip:
        raise click.UsageError("Give --allow-ip (one or more) or --any-ip.")
    manager = token_manager()
    before = manager.get_api_token(token_id)
    changed = manager.set_api_token_networks(token_id, list(allow_ips) or None)
    if before is None or changed is None:
        raise click.ClickException(f"No live API token with id {token_id}.")
    from noust.core.audit import record

    record(
        "auth.token.update",
        target=f"token:{token_id}",
        details={
            "detail": f"token '{before['name']}' accepted from "
            f"{', '.join(changed.get('allowed_cidrs') or []) or 'anywhere'}"
        },
    )
    ctx.logger.success(
        f"API token {before['name']} is accepted from "
        f"{', '.join(changed.get('allowed_cidrs') or []) or 'anywhere'}."
    )


@cli.command("revoke")
@click.argument("token_id", type=int)
@click.option(
    "-f", "-y", "--force", "--yes", "force", is_flag=True, help="Do not ask for confirmation."
)
@pass_context
def revoke_command(ctx: Context, token_id: int, force: bool) -> None:
    """
    Revoke one API token by its id, as listed by 'noust token list'.

    Requests presenting it stop authenticating immediately.
    """
    if not force and not click.confirm(
        f"Revoke API token {token_id}? Anything using it stops authenticating immediately",
        default=False,
    ):
        ctx.logger.info("Cancelled")
        return

    name = token_manager().revoke_api_token(token_id)
    if name is None:
        ctx.logger.error(f"No API token with id {token_id}")
        raise SystemExit(1)

    ctx.logger.success(f"Revoked API token: {name}")


def _credential_of(name: str) -> tuple[Any, Any]:
    """
    Find the token a name, or ``assistant:<person>``, stands for.

    Args:
        name: A token's name, or what the built-in assistant of a person is called.

    Returns:
        ``(record, reference)``: the token's record and what a reading of its activity follows.

    Raises:
        click.ClickException: When no token has that name.
    """
    from noust.core.credential_activity import reference_of

    manager = token_manager()
    records = manager.list_api_tokens()
    wanted = name
    if name.startswith("assistant:"):
        account = manager.accounts.find(name.partition(":")[2])
        mine = [
            r
            for r in records
            if account is not None
            and r.get("caller") == "assistant"
            and r.get("owner_account_id") == account.id
        ]
        if not mine:
            raise click.ClickException(f"The built-in assistant has not worked for {name[10:]!r}.")
        wanted = str(max(mine, key=lambda r: int(r["id"]))["name"])
    record = next((r for r in records if r["name"] == wanted), None)
    if record is None:
        raise click.ClickException(
            f"No API token is called {name!r}. 'noust token list' shows them, and the built-in "
            "assistant of a person is assistant:<username>."
        )
    owner = (
        manager.accounts.get(int(record["owner_account_id"]))
        if record.get("owner_account_id")
        else None
    )
    return record, reference_of(record, records, owner.username if owner else None)


@cli.command("activity", read_only=lambda params: not params["apply_undo"])
@click.argument("name")
@click.argument("intervention", type=int, required=False)
@click.option(
    "--days",
    type=click.IntRange(1, 365),
    default=30,
    show_default=True,
    help="How far back to read.",
)
@click.option(
    "--undo", "show_undo", is_flag=True, help="Show how to leave the intervention as it was."
)
@click.option(
    "--apply",
    "apply_undo",
    is_flag=True,
    help="Do it: put back the applications the plan can (as root, on the audit trail).",
)
@click.option("--app", "apps", multiple=True, help="Only this application (repeat).")
@click.option(
    "--confirm-schema",
    "confirm_schema",
    multiple=True,
    help="Confirm going back past a deployment that changed the database's schema, for this application.",
)
@json_option("Print the activity as JSON.")
@pass_context
def activity_command(
    ctx: Context,
    name: str,
    intervention: int | None,
    days: int,
    show_undo: bool,
    apply_undo: bool,
    apps: tuple[str, ...],
    confirm_schema: tuple[str, ...],
) -> None:
    """
    Show what a token did, by interventions, and leave one as it was.

    Without a number, the interventions: what the token did without a silence of half an hour. With one, its
    timeline. ``--undo`` shows how to put it back (nothing changes), ``--apply`` does it, one application at a
    time by the way back Noust already has, and says what went back and what did not. The built-in assistant
    is one credential for each person: ``assistant:<username>``, its interventions its conversations.
    """
    from noust.core.credential_activity import CredentialActivity
    from noust.deployers.credential_undo import Undoer
    from noust.web.mcp.tools import BY_NAME

    def reads(tool: str) -> bool:
        found = BY_NAME.get(tool)
        return found is not None and found.read_only

    _record, ref = _credential_of(name)
    activity = CredentialActivity()
    if intervention is None:
        found, complete = activity.interventions(ref, days=days, is_read_tool=reads)
        if ctx.json_output:
            click.echo(
                json.dumps(
                    {
                        "credential": ref.label,
                        "complete": complete,
                        "interventions": [shown.to_dict() for shown in found],
                    }
                )
            )
            return
        if not found:
            ctx.logger.info(
                f"{ref.label} did nothing in the last {days} days that the audit trail holds."
            )
            return
        ctx.logger.table(
            ["Number", "From", "To", "Calls", "Changes", "Waited", "Refused", "Applications"],
            [
                [
                    str(shown.n),
                    shown.started_at[:19].replace("T", " "),
                    shown.ended_at[:19].replace("T", " ")
                    + (" (going on)" if shown.ongoing else ""),
                    str(len(shown.entries)),
                    str(shown.to_dict()["changes"]),
                    str(shown.to_dict()["proposed"]),
                    str(shown.to_dict()["refused"]),
                    ", ".join(shown.apps) or "-",
                ]
                for shown in found
            ],
        )
        if not complete:
            ctx.logger.warning(
                "The audit trail is longer than one reading goes through: older ones are not here."
            )
        return
    chosen = activity.intervention(ref, intervention, is_read_tool=reads)
    if chosen is None:
        raise click.ClickException(
            f"{ref.label} has no intervention {intervention} in the audit trail."
        )
    undoer = Undoer(activity)
    if apply_undo:
        plan = undoer.plan(ref, chosen)
        domains = list(apps) or [i.domain for i in plan.items]
        result = undoer.apply(
            ref,
            intervention,
            domains=domains,
            confirmed=list(confirm_schema),
            is_read_tool=reads,
        )
        if ctx.json_output:
            click.echo(json.dumps(result.to_dict()))
            return
        for entry in result.reverted:
            ctx.logger.success(f"{entry['domain']} went back to deployment {entry['back_to']}.")
        for entry in result.not_reverted:
            ctx.logger.warning(
                f"{entry['domain'] or ''} did not go back ({entry['reason']}): {entry['detail']}"
            )
        for entry in result.irreversible:
            ctx.logger.info(
                f"No way back from here: {entry['detail']} ({entry['method']} {entry['path']})"
            )
        if not result.reverted:
            raise SystemExit(1)
        return
    if show_undo:
        plan = undoer.plan(ref, chosen)
        if ctx.json_output:
            click.echo(json.dumps(plan.to_dict()))
            return
        for step in plan.items:
            line = f"{step.domain}: {step.status}"
            if step.back_to is not None:
                line += f", back to deployment {step.back_to} ({step.back_to_commit or step.back_to_release or '-'})"
            ctx.logger.info(line + (f": {step.detail}" if step.detail else ""))
            if step.schema_changed:
                ctx.logger.warning(
                    f"  Passes deployments that changed the database's schema: {step.schema_changed}. "
                    f"Confirm with --confirm-schema {step.domain}; Noust puts code back, never the database."
                )
        for lost in plan.irreversible:
            ctx.logger.info(f"No way back from here: {lost.detail} ({lost.method} {lost.path})")
        if not plan.items and not plan.irreversible:
            ctx.logger.info(
                "This intervention deployed nothing and changed nothing that has no way back."
            )
        return
    if ctx.json_output:
        click.echo(json.dumps({"credential": ref.label, **chosen.to_dict(with_entries=True)}))
        return
    ctx.logger.table(
        ["At", "Via", "What", "Application", "Result"],
        [
            [
                entry.at[:19].replace("T", " "),
                entry.via,
                f"{entry.method} {entry.path}" if entry.method else (entry.tool or "-"),
                entry.domain or "-",
                entry.result + (f" (request {entry.approval})" if entry.approval else ""),
            ]
            for entry in chosen.entries
        ],
    )
