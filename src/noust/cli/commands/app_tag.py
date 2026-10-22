# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app tag``: free labels on applications, and deploying every application that carries one.

    noust app tag add shop.example.com prod customer-a
    noust app tag list --tag prod
    noust app tag deploy prod

``deploy`` updates the applications one after the other in this process (a
console queues them as jobs instead: ``POST /api/tags/{tag}/deploy``). Each is
skipped when its branch has nothing new, as a single update is refused, unless
``--force`` says to rebuild it anyway.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.app_tags import AppTags, validate_tag
from noust.core.audit import record as audit_record
from noust.core.exceptions import NoustError
from noust.managers import tag_deploys
from noust.validators.domain import validate_domain


@click.group("tag", cls=NoustGroup)
def tag() -> None:
    """Tags on applications: find them, and deploy the ones that share a tag."""


@tag.command("list", read_only=True)
@click.argument("domain", required=False)
@click.option("--tag", "wanted", default=None, help="Only the applications with this tag.")
@json_option("Print the tags as JSON.")
@pass_context
def list_command(ctx: Context, domain: str | None, wanted: str | None) -> None:
    """List the tags of one application, or every tagged application."""
    tags = AppTags()
    rows = {domain: tags.of(validate_domain(domain))} if domain else tags.by_application()
    if wanted is not None:
        needle = validate_tag(wanted)
        rows = {d: t for d, t in rows.items() if needle in t}
    if ctx.json_output:
        click.echo(json.dumps({"applications": rows}))
        return
    if not rows:
        ctx.logger.info(
            "No application carries a tag" if wanted is None else f"Nothing has the tag {wanted}"
        )
        return
    ctx.logger.table(
        ["Application", "Tags"], [[d, ", ".join(t) or "-"] for d, t in sorted(rows.items())]
    )


@tag.command("add")
@click.argument("domain")
@click.argument("tags", nargs=-1, required=True)
@pass_context
def add_command(ctx: Context, domain: str, tags: tuple[str, ...]) -> None:
    """Give an application more tags."""
    domain = validate_domain(domain)
    now = AppTags().add(domain, tags)
    audit_record("apps.tags.set", target=f"app:{domain}", details={"tags": now})
    ctx.logger.success(f"{domain}: {', '.join(now)}")


@tag.command("remove")
@click.argument("domain")
@click.argument("tags", nargs=-1, required=True)
@pass_context
def remove_command(ctx: Context, domain: str, tags: tuple[str, ...]) -> None:
    """Take tags off an application."""
    domain = validate_domain(domain)
    now = AppTags().remove(domain, tags)
    audit_record("apps.tags.set", target=f"app:{domain}", details={"tags": now})
    ctx.logger.success(f"{domain}: {', '.join(now) if now else 'no tags'}")


@tag.command("set")
@click.argument("domain")
@click.argument("tags", nargs=-1)
@pass_context
def set_command(ctx: Context, domain: str, tags: tuple[str, ...]) -> None:
    """Make an application carry exactly these tags (none clears them)."""
    domain = validate_domain(domain)
    now = AppTags().set(domain, tags)
    audit_record("apps.tags.set", target=f"app:{domain}", details={"tags": now})
    ctx.logger.success(f"{domain}: {', '.join(now) if now else 'no tags'}")


@tag.command("restart")
@click.argument("tag_name", metavar="TAG")
@json_option("Print what came of each as JSON.")
@pass_context
def restart_command(ctx: Context, tag_name: str) -> None:
    """
    Restart every application and service that carries TAG.

    Databases that carry it are listed and left: a database is restarted with its engine, which
    others share (noust db restart). One that fails does not stop the others. With the global
    --dry-run nothing is restarted.
    """
    from noust.cli.commands.webapp import _control_service
    from noust.core.logger import Logger
    from noust.core.resource_tags import KIND_DATABASE, KIND_SERVICE, ResourceTags
    from noust.managers.service_manager import ServiceManager

    wanted = validate_tag(tag_name)
    resources = ResourceTags()
    apps = [app.domain for app in tag_deploys.applications_with(wanted)]
    units = resources.with_tag(KIND_SERVICE, wanted)
    databases = resources.with_tag(KIND_DATABASE, wanted)
    if not (apps or units or databases):
        raise NoustError(
            f"Nothing carries the tag {wanted}",
            details="Give one a tag: noust app tag add DOMAIN TAG",
        )
    rows: list[dict[str, str]] = []
    manager = ServiceManager(verbose=False)
    for domain in apps:
        try:
            # The application's own restart prints as it goes; the table below is the summary.
            code = _control_service(domain, "restart", Logger(verbose=False))
            rows.append(
                {
                    "kind": "application",
                    "name": domain,
                    "outcome": "restarted" if code == 0 else "failed",
                    "detail": "",
                }
            )
        except NoustError as exc:
            rows.append(
                {
                    "kind": "application",
                    "name": domain,
                    "outcome": tag_deploys.FAILED,
                    "detail": exc.message,
                }
            )
    for unit in units:
        try:
            manager.restart(unit)
            rows.append({"kind": "service", "name": unit, "outcome": "restarted", "detail": ""})
        except NoustError as exc:
            rows.append(
                {
                    "kind": "service",
                    "name": unit,
                    "outcome": tag_deploys.FAILED,
                    "detail": exc.message,
                }
            )
    for key in databases:
        rows.append(
            {
                "kind": "database",
                "name": key,
                "outcome": tag_deploys.SKIPPED,
                "detail": "restarted with its engine: noust db restart ENGINE",
            }
        )
    audit_record(
        "apps.tags.restart",
        target=f"tag:{wanted}",
        details={
            "restarted": [r["name"] for r in rows if r["outcome"] == "restarted"],
            "failed": [r["name"] for r in rows if r["outcome"] == tag_deploys.FAILED],
        },
    )
    if ctx.json_output:
        click.echo(json.dumps({"tag": wanted, "results": rows}))
    else:
        ctx.logger.table(
            ["Kind", "Name", "Outcome", "Detail"],
            [[r["kind"], r["name"], r["outcome"], r["detail"]] for r in rows],
        )
    if any(r["outcome"] == tag_deploys.FAILED for r in rows):
        raise SystemExit(1)


@tag.command("deploy")
@click.argument("tag_name", metavar="TAG")
@click.option(
    "--force", is_flag=True, help="Rebuild applications even when their branch has nothing new."
)
@click.option(
    "--stop-on-failure", is_flag=True, help="Leave the rest alone after the first one that fails."
)
@json_option("Print what came of each application as JSON.")
@pass_context
def deploy_command(ctx: Context, tag_name: str, force: bool, stop_on_failure: bool) -> None:
    """Update every application that carries TAG, one after the other."""
    wanted = validate_tag(tag_name)
    outcomes = tag_deploys.deploy_tag_now(
        wanted, logger=ctx.logger, force=force, stop_on_failure=stop_on_failure
    )
    if not outcomes:
        raise NoustError(
            f"No application carries the tag {wanted}",
            details="Give one a tag: noust app tag add DOMAIN TAG",
        )
    audit_record(
        "apps.tags.deploy",
        target=f"tag:{wanted}",
        details={"applications": [o.domain for o in outcomes]},
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "tag": wanted,
                    "results": [
                        {"domain": o.domain, "outcome": o.outcome, "detail": o.detail}
                        for o in outcomes
                    ],
                }
            )
        )
    else:
        ctx.logger.table(
            ["Application", "Outcome", "Detail"],
            [[o.domain, o.outcome, o.detail] for o in outcomes],
        )
    if any(o.outcome == tag_deploys.FAILED for o in outcomes):
        raise SystemExit(1)
