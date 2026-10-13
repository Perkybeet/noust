# Copyright (c) 2024-2026 Yago Lopez Prado
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
