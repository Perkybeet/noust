# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust preview``: pull request previews of an application (2.2).

A presentation layer over :mod:`noust.managers.previews`, the module the
console's ``/api/apps/{domain}/previews`` endpoints and the webhooks call
too. The CLI runs without the console, so removals here happen in this
process instead of as console jobs.
"""

from __future__ import annotations

import dataclasses
import json
import sys

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.preview_store import DATA_MODES, INHERIT_MODES, PreviewStore
from noust.core.store import get_store
from noust.core.variables.model import AUDIENCES, BOTH, PREVIEW
from noust.core.variables.store import VariableStore
from noust.managers import previews
from noust.validators.domain import validate_domain


@click.group("preview", cls=NoustGroup)
def cli() -> None:
    """Pull request previews: a short-lived copy of an application per pull request."""


@cli.command("enable")
@click.argument("domain")
@click.option(
    "--domain",
    "base_domain",
    default=None,
    help="Base domain previews answer under; *.BASE must point at this server. "
    "Required to turn previews on; kept when changing other settings.",
)
@click.option(
    "--max",
    "max_previews",
    type=int,
    default=None,
    help=f"How many previews may exist at once (1 to 20). "
    f"[default: {previews.DEFAULT_MAX_PREVIEWS}, or the current value]",
)
@click.option(
    "--ttl",
    default=None,
    help="How long a preview lives without a push: 12h, 7d, 2w (1 hour to 90 days). "
    "[default: 7d, or the current value]",
)
@click.option(
    "--allow-bots/--no-allow-bots",
    "allow_bots",
    default=None,
    help="Build previews of pull requests opened or pushed to by bot accounts "
    "(Dependabot, Renovate). [default: no, or the current value]",
)
@click.option(
    "--exclude-env",
    "exclude_env",
    multiple=True,
    metavar="NAME",
    help="A variable never copied to previews; repeat for more. Replaces the current "
    "list; existing previews lose them at their next push.",
)
@click.option(
    "--no-exclude-env",
    "clear_exclude_env",
    is_flag=True,
    help="Copy every variable again (clear the excluded list).",
)
@click.option(
    "--env-mode",
    "inherit_env",
    type=click.Choice(INHERIT_MODES),
    default=None,
    help="Which of the application's variables a preview is given: 'allowlist' (only "
    "--allow-env, the default for previews turned on now) or 'all' (every one less "
    "--exclude-env, as before 3.4).",
)
@click.option(
    "--allow-env",
    "allow_env",
    multiple=True,
    metavar="NAME",
    help="A variable an 'allowlist' preview copies; repeat for more. Replaces the current list.",
)
@click.option(
    "--no-allow-env",
    "clear_allow_env",
    is_flag=True,
    help="Copy no variable of the application's (clear the allowed list).",
)
@click.option(
    "--data",
    "data_mode",
    type=click.Choice(DATA_MODES),
    default=None,
    help="'empty' gives each preview a database of its own, 'shared' lets it use the "
    "application's (a pull request can then write into production's). Default for "
    "previews turned on now: empty, when the application has a database.",
)
@global_flags
@json_option("Print the settings as JSON.")
@pass_context
def enable(
    ctx: Context,
    domain: str,
    base_domain: str | None,
    max_previews: int | None,
    ttl: str | None,
    allow_bots: bool | None,
    exclude_env: tuple[str, ...],
    clear_exclude_env: bool,
    inherit_env: str | None,
    allow_env: tuple[str, ...],
    clear_allow_env: bool,
    data_mode: str | None,
) -> None:
    """
    Turn previews on for an application, or change their settings.

    Each pull request opened against the application's repository gets its
    own copy at pr-<n>-<app>.BASE, built from the pull request's branch and
    removed when it is closed or its time-to-live runs out. An option left
    out keeps its current value.

    A preview is built in the build sandbox, not as root. Previews turned on now
    are given only the variables you name with --allow-env, and those defined for
    previews ('noust preview vars'), and a database of their own; with '--env-mode
    all' they are given every variable of the application, production secrets
    included, as before 3.4. Only people trusted with the repository get a preview:
    pull requests from forks never do, on GitHub the author must be an owner, member
    or collaborator, and bots only with --allow-bots.
    """
    if exclude_env and clear_exclude_env:
        raise click.UsageError("Give --exclude-env or --no-exclude-env, not both.")
    if allow_env and clear_allow_env:
        raise click.UsageError("Give --allow-env or --no-allow-env, not both.")
    excluded: list[str] | None = None
    if clear_exclude_env:
        excluded = []
    elif exclude_env:
        excluded = list(exclude_env)
    allowed: list[str] | None = None
    if clear_allow_env:
        allowed = []
    elif allow_env:
        allowed = list(allow_env)
    stored = previews.enable_previews(
        domain,
        base_domain,
        max_previews=max_previews,
        ttl_hours=previews.parse_ttl(ttl) if ttl is not None else None,
        allow_bots=allow_bots,
        exclude_env=excluded,
        inherit_env=inherit_env,
        allow_env=allowed,
        data_mode=data_mode,
    )
    options = PreviewStore().options(stored.app_domain)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    **dataclasses.asdict(stored),
                    "inherit_env": options.inherit_env,
                    "allow_env": list(options.allow_env),
                    "data_mode": options.data_mode,
                }
            )
        )
        return
    logger = ctx.logger
    logger.success(f"Previews on for {stored.app_domain}")
    logger.key_value("Answer at", f"pr-<n>-...{stored.base_domain}")
    logger.key_value("At most", str(stored.max_previews))
    logger.key_value("Time-to-live", f"{stored.ttl_hours} hours without a push")
    logger.key_value("Bots", "allowed" if stored.allow_bots else "refused")
    if options.inherit_env == "all":
        logger.key_value(
            "Never copied", ", ".join(stored.exclude_env) if stored.exclude_env else "(none)"
        )
    else:
        logger.key_value(
            "Copied from the app", ", ".join(options.allow_env) if options.allow_env else "(none)"
        )
    logger.key_value("Data", options.data_mode)
    logger.blank()
    risks = []
    if options.inherit_env == "all":
        risks.append(
            f"run with a copy of {stored.app_domain}'s environment variables, production "
            "secrets included" + (" (except the ones never copied)" if stored.exclude_env else "")
        )
    if options.data_mode == "shared" and previews.has_database(stored.app_domain):
        risks.append("use the same databases, which a pull request can write into")
    if risks:
        logger.warning("Previews are built in the sandbox, and " + ", and ".join(risks) + ".")
    logger.info(f"Point a wildcard record *.{stored.base_domain} at this server.")


@cli.command("disable")
@click.argument("domain")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before removing existing previews.")
@global_flags
@json_option("Print the previews removed as JSON.")
@pass_context
def disable(ctx: Context, domain: str, yes: bool) -> None:
    """
    Turn previews off for an application and remove the previews it has.
    """
    domain = validate_domain(domain)
    existing = [record.domain for record in get_store().list_previews(domain)]
    if existing and not yes:
        question = (
            f"Remove {len(existing)} preview(s) of {domain} ({', '.join(existing)})? "
            "This deletes their files and certificates"
        )
        try:
            confirmed = click.confirm(question, default=False)
        except click.Abort:
            confirmed = False
        if not confirmed:
            ctx.logger.info("Cancelled; previews are still on")
            return
    removed = previews.disable_previews(domain, logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps({"domain": domain, "enabled": False, "removed": removed}))
        return
    ctx.logger.success(f"Previews off for {domain}")
    if removed:
        ctx.logger.info(f"Removed {len(removed)}: {', '.join(removed)}")


@cli.command("list")
@click.argument("domain", required=False)
@json_option("Print the settings and previews as JSON.")
@pass_context
def list_command(ctx: Context, domain: str | None) -> None:
    """
    List previews, of one application or of every one.
    """
    store = get_store()
    parent = validate_domain(domain) if domain else None
    records = store.list_previews(parent)
    settings = store.get_preview_settings(parent) if parent else None
    if ctx.json_output:
        payload: dict[str, object] = {
            "items": [
                dataclasses.asdict(record)
                | {
                    "url": previews.preview_url(record.domain),
                    "kind": PreviewStore().origin(record.domain).kind,
                }
                for record in records
            ]
        }
        if parent:
            payload["domain"] = parent
            payload["settings"] = dataclasses.asdict(settings) if settings else None
        click.echo(json.dumps(payload))
        return
    logger = ctx.logger
    if parent:
        if settings is None:
            logger.info(f"Previews are off for {parent}")
        else:
            options = PreviewStore().options(settings.app_domain)
            logger.key_value(
                "Previews",
                f"under {settings.base_domain}, at most {settings.max_previews}, "
                f"{settings.ttl_hours} h without a push, bots "
                f"{'allowed' if settings.allow_bots else 'refused'}",
            )
            if options.inherit_env == "all":
                if settings.exclude_env:
                    logger.key_value("Never copied", ", ".join(settings.exclude_env))
            else:
                logger.key_value(
                    "Copied from the app",
                    ", ".join(options.allow_env) if options.allow_env else "(none)",
                )
            logger.key_value("Data", options.data_mode)
    if not records:
        logger.info("No previews")
        return
    extras = PreviewStore()
    logger.table(
        ["Preview", "Of", "From", "Branch", "Status", "Expires"],
        [
            [
                record.domain,
                record.parent_domain,
                _from(record, extras.origin(record.domain).kind),
                record.branch,
                record.status,
                record.expires_at,
            ]
            for record in records
        ],
    )


@cli.command("remove")
@click.argument("preview_domain")
@global_flags
@pass_context
def remove(ctx: Context, preview_domain: str) -> None:
    """
    Remove one preview now: its application, certificate and record.
    """
    warnings = previews.remove_preview(preview_domain, logger=ctx.logger)
    for warning in warnings:
        ctx.logger.warning(warning)
    previews.refresh_sweep_timer(ctx.logger)
    ctx.logger.success(f"Removed {preview_domain}")


@cli.command("sweep")
@global_flags
@json_option("Print the previews removed as JSON.")
@pass_context
def sweep_command(ctx: Context) -> None:
    """
    Remove expired previews, and those whose application is gone.

    What noust-previews.timer runs every hour.
    """
    removed = previews.sweep(logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps({"removed": removed}))
        return
    if removed:
        ctx.logger.success(f"Removed {len(removed)}: {', '.join(removed)}")
    else:
        ctx.logger.info("Nothing expired")


def _from(record: previews.PreviewRecord, kind: str) -> str:
    """
    Args:
        record: A preview.
        kind: ``pr``, ``branch`` or ``tag``.

    Returns:
        What it was made from, as the table shows it.
    """
    if kind == "pr":
        return f"PR #{record.number}"
    return f"{kind} {record.branch}"


@cli.command("pulls", read_only=True)
@click.argument("domain")
@json_option("Print the pull requests as JSON.")
@pass_context
def pulls_command(ctx: Context, domain: str) -> None:
    """List the open pull requests of an application's repository (GitHub App)."""
    found = previews.list_pull_requests(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps({"pull_requests": found}))
        return
    if not found:
        ctx.logger.info("No open pull requests")
        return
    ctx.logger.table(
        ["PR", "Title", "Branch", "Author", "Preview", "Note"],
        [
            [
                f"#{pr['number']}",
                pr["title"][:40],
                pr["branch"],
                pr["author"],
                pr["status"] or "-",
                "fork" if pr["from_fork"] else "draft" if pr["draft"] else "",
            ]
            for pr in found
        ],
    )
    ctx.logger.info(f"Make one: noust preview create {domain} --pr NUMBER")


@cli.command("create")
@click.argument("domain")
@click.option("--pr", "pull_request", type=int, default=None, help="An open pull request's number.")
@click.option("--branch", default=None, help="A branch.")
@click.option("--tag", default=None, help="A tag.")
@click.option(
    "--allow-fork", is_flag=True, help="Preview a pull request whose branch is in a fork."
)
@click.option(
    "--no-wait", "no_wait", is_flag=True, help="Record it and return; a console build follows."
)
@global_flags
@json_option("Print the preview as JSON.")
@pass_context
def create_command(
    ctx: Context,
    domain: str,
    pull_request: int | None,
    branch: str | None,
    tag: str | None,
    allow_fork: bool,
    no_wait: bool,
) -> None:
    """
    Make a preview by hand, from an open pull request, a branch or a tag.

    The build runs here, in this process, and the command returns when it ends;
    the preview then answers at its own address like any other.
    """
    created = previews.create_preview(
        domain,
        pull_request=pull_request,
        branch=branch,
        tag=tag,
        allow_fork=allow_fork,
        queue=False,
    )
    record = created.record
    if not no_wait:
        ctx.logger.info(f"Building {record.domain} from {record.branch}")
        previews.build_in_process(record.parent_domain, record.number, ctx.logger)
        record = get_store().get_preview(record.parent_domain, record.number) or record
    if ctx.json_output:
        click.echo(
            json.dumps(
                dataclasses.asdict(record)
                | {"url": previews.preview_url(record.domain), "kind": created.kind}
            )
        )
        return
    ctx.logger.success(f"{record.domain} is {record.status}")
    ctx.logger.key_value("URL", previews.preview_url(record.domain))


@cli.command("redeploy")
@click.argument("preview_domain")
@global_flags
@pass_context
def redeploy_command(ctx: Context, preview_domain: str) -> None:
    """Build a preview again, from the head of its branch as it is now."""
    created = previews.redeploy_preview(preview_domain, queue=False)
    record = created.record
    ctx.logger.info(f"Building {record.domain} from {record.branch}")
    previews.build_in_process(record.parent_domain, record.number, ctx.logger)
    fresh = get_store().get_preview(record.parent_domain, record.number) or record
    ctx.logger.success(f"{fresh.domain} is {fresh.status}")


@cli.command("stop")
@click.argument("preview_domain")
@global_flags
@pass_context
def stop_command(ctx: Context, preview_domain: str) -> None:
    """Stop a preview without removing it."""
    record = previews.stop_preview(preview_domain)
    ctx.logger.success(f"{record.domain} is stopped")


@cli.command("start")
@click.argument("preview_domain")
@global_flags
@pass_context
def start_command(ctx: Context, preview_domain: str) -> None:
    """Start a preview that was stopped."""
    record = previews.start_preview(preview_domain)
    ctx.logger.success(f"{record.domain} is running")


@cli.command("logs", read_only=True)
@click.argument("preview_domain")
@click.option("--lines", type=int, default=100, show_default=True, help="Journal lines.")
@click.option("--build", "build", is_flag=True, help="Show the log of its last build instead.")
@pass_context
def logs_command(ctx: Context, preview_domain: str, lines: int, build: bool) -> None:
    """Show what a preview's process printed, or the log of its last build."""
    from noust.core.utils import domain_to_app_name
    from noust.managers.service_manager import ServiceManager

    domain = validate_domain(preview_domain)
    if get_store().get_preview_by_domain(domain) is None:
        raise ValidationError(f"{domain} is not a preview", details="Run 'noust preview list'.")
    if build:
        text = previews.last_build_log(domain)
        click.echo(text or "No build log")
        return
    click.echo(ServiceManager(verbose=ctx.verbose).logs(domain_to_app_name(domain), lines=lines))


@cli.group("vars")
def vars_group() -> None:
    """The variables the previews of an application are given besides its own."""


def _app_id(domain: str) -> int:
    """
    Args:
        domain: An application's domain.

    Returns:
        Its id.

    Raises:
        ValidationError: There is no such application.
    """
    app = get_store().get_app(validate_domain(domain))
    if app is None or app.id is None:
        raise ValidationError(f"No application named {domain}", details="Run 'noust list'.")
    return app.id


@vars_group.command("list", read_only=True)
@click.argument("domain")
@json_option("Print the variables as JSON.")
@pass_context
def vars_list(ctx: Context, domain: str) -> None:
    """List the variables defined for the previews of an application (secrets hidden)."""
    from noust.core.config import REDACTED
    from noust.core.secret_detection import classify

    found = VariableStore().list(PREVIEW, _app_id(domain))
    marks = {v.name: v.secret for v in found if v.secret is not None}
    rows = [
        {
            "name": v.name,
            "value": REDACTED if classify(v.name, v.value, marks).secret else v.value,
            "audience": v.audience,
        }
        for v in found
    ]
    if ctx.json_output:
        click.echo(json.dumps({"variables": rows}))
        return
    if not rows:
        ctx.logger.info("No variable is defined for previews")
        return
    ctx.logger.table(
        ["Name", "Value", "Audience"], [[r["name"], r["value"], r["audience"]] for r in rows]
    )


@vars_group.command("set")
@click.argument("domain")
@click.argument("name")
@click.argument("value", required=False)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read the value from standard input.")
@click.option("--secret/--not-secret", "secret", default=None, help="Say whether it is a secret.")
@click.option(
    "--audience", type=click.Choice(AUDIENCES), default=BOTH, show_default=True, help="Who sees it."
)
@pass_context
def vars_set(
    ctx: Context,
    domain: str,
    name: str,
    value: str | None,
    from_stdin: bool,
    secret: bool | None,
    audience: str,
) -> None:
    """Define a variable previews are given; it wins over the application's own."""
    if from_stdin == (value is not None):
        raise click.UsageError("Give the value as an argument, or with --stdin, not both.")
    text = sys.stdin.read().rstrip("\n") if from_stdin else (value or "")
    VariableStore().set(
        PREVIEW, _app_id(domain), name, text, secret=secret, audience=audience, actor="cli"
    )
    ctx.logger.success(f"Previews of {domain} are given {name}")


@vars_group.command("unset")
@click.argument("domain")
@click.argument("names", nargs=-1, required=True)
@pass_context
def vars_unset(ctx: Context, domain: str, names: tuple[str, ...]) -> None:
    """Stop giving previews variables."""
    app_id = _app_id(domain)
    store = VariableStore()
    missing = [name for name in names if not store.delete(PREVIEW, app_id, name)]
    if missing:
        raise ValidationError(f"No variable {', '.join(missing)} is defined for previews")
    ctx.logger.success(f"Removed {', '.join(names)}")
