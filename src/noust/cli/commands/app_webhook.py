# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app webhook``: the deploy webhook of one application.

A push to the application's repository deploys it when the forge signs the
delivery with the application's secret. ``show`` says how far the setup is
(the same answer the console's guided setup draws from:
:func:`noust.integrations.webhook.status`), ``rotate`` creates or replaces the
secret, ``disable`` discards it, and ``deliveries`` lists what the forge has
sent, whatever became of it. This module only parses, asks and prints.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core import webhook_deliveries
from noust.core.exceptions import DeploymentError, NoustError
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.core.store import App, get_store
from noust.integrations import webhook as service
from noust.integrations.webhook import WebhookStatus

#: What each outcome is called on a terminal.
OUTCOME_WORDS = {
    webhook_deliveries.DEPLOY_STARTED: "deployed",
    webhook_deliveries.PREVIEW_STARTED: "preview started",
    webhook_deliveries.PING: "ping",
    webhook_deliveries.IGNORED_BRANCH: "ignored (other branch)",
    webhook_deliveries.IGNORED_TAG: "ignored (tag)",
    webhook_deliveries.IGNORED_EVENT: "ignored (event)",
    webhook_deliveries.IGNORED_PULL_REQUEST: "ignored (pull request)",
    webhook_deliveries.DUPLICATE: "repeat (ignored)",
    webhook_deliveries.BAD_SIGNATURE: "bad signature",
    webhook_deliveries.LOCKED: "refused (locked)",
}

#: What each state is called, and what to do about it.
STATE_WORDS = {
    "disabled": "disabled",
    "waiting": "waiting for the first delivery",
    "connected": "connected",
    "problem": "problem: the last delivery was refused",
}


def _application(domain: str) -> App:
    """
    Look up the application a command is about.

    Args:
        domain: The application's domain.

    Returns:
        The stored application.

    Raises:
        NoustError: When nothing is deployed at the domain.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    return app


def _when(iso: str | None) -> str:
    """
    Show a stored timestamp the way a terminal wants it.

    Args:
        iso: An ISO 8601 timestamp, or None.

    Returns:
        ``2026-09-29 10:15:02``, or ``-`` for none.
    """
    return iso[:19].replace("T", " ") if iso else "-"


def _describe(logger: Logger, state: WebhookStatus) -> None:
    """
    Print the state of a webhook for a person.

    Args:
        logger: The logger the command writes through.
        state: What :func:`noust.integrations.webhook.status` gathered.
    """
    logger.key_value("Webhook", f"{state.domain}: {STATE_WORDS[state.state]}")
    hooks = state.hooks
    if state.enabled:
        logger.key_value("Secret", "set (noust app webhook show --reveal prints it)")
    else:
        logger.key_value("Secret", f"not created (noust app webhook rotate {state.domain})")

    if hooks.exposed:
        logger.key_value("Payload URL", hooks.hook_url or "-")
    else:
        logger.key_value(
            "Payload URL",
            "not public: no forge can reach this server yet. Run "
            "'noust web expose-hooks hooks.example.com' first",
        )
    logger.key_value("Content type", hooks.content_type)
    logger.key_value("Events", ", ".join(hooks.events))

    forge = state.forge
    if forge.repository:
        where = f" - add the webhook at {forge.settings_url}" if forge.settings_url else ""
        logger.key_value("Repository", f"{forge.repository} ({forge.forge or forge.host}){where}")

    branch = state.branch
    if branch.pinned:
        logger.key_value("Branch", f"{branch.tracked}: only pushes to it deploy")
    else:
        logger.warning(
            "No branch is pinned: a push to any branch deploys this application. "
            "Deploy it once from the branch it should follow."
        )
    if state.inplace_warning:
        logger.warning(
            "This application is deployed in place: every push rebuilds the live tree and there "
            f"is no instant way back. 'noust app migrate {state.domain}' moves it to releases."
        )

    app = state.github_app
    if app.covers_repository:
        logger.warning(
            f"This server's GitHub App ({app.account}) already deploys every push to "
            f"{forge.repository}: a webhook of its own would deploy twice."
        )
    elif app.configured and forge.forge == "github" and app.repository_selection == "selected":
        logger.info(
            f"The GitHub App is installed on {app.account} for selected repositories; "
            f"{forge.repository} is not one of them yet."
        )

    deliveries = state.deliveries
    if deliveries.last is None:
        logger.key_value("Deliveries", "none yet")
        return
    last = deliveries.last
    logger.key_value(
        "Last delivery",
        f"{_when(last.received_at)}, {OUTCOME_WORDS.get(last.outcome, last.outcome)}",
    )
    logger.key_value("Last push that deployed", _when(deliveries.last_push_at))
    if deliveries.refused_since_last_verified:
        logger.warning(
            f"{deliveries.refused_since_last_verified} delivery(ies) had a wrong signature since "
            "the last good one: check the secret at the forge."
        )


@click.group("webhook", cls=NoustGroup)
def webhook() -> None:
    """The deploy webhook of an application: a push to its repository deploys it."""


@webhook.command("show", read_only=True)
@click.argument("domain")
@click.option("--reveal", is_flag=True, help="Print the secret too (it is never shown otherwise).")
@global_flags
@json_option("Print the state as JSON.")
@pass_context
def show_command(ctx: Context, domain: str, reveal: bool) -> None:
    """
    Show how far the webhook is set up.

    The public payload URL (and whether 'noust web expose-hooks' made one),
    the content type and events to enable at the forge, whether a secret
    exists, the branch that deploys (and the warning when none is pinned),
    whether the GitHub App already covers the repository, and what the forge
    has sent. --reveal prints the secret.
    """
    app = _application(domain)
    state = service.status(app)
    secret: str | None = None
    if reveal and state.enabled:
        secret = service.reveal_secret(app.domain)

    if ctx.json_output:
        payload: dict[str, Any] = state.to_dict()
        if reveal:
            payload["secret"] = secret
        click.echo(json.dumps(payload))
        return

    _describe(ctx.logger, state)
    if reveal:
        if secret is None:
            ctx.logger.info(f"No webhook secret yet: noust app webhook rotate {app.domain}")
        else:
            ctx.logger.key_value("Secret", secret)


@webhook.command("rotate")
@click.argument("domain")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@global_flags
@json_option("Print the new secret and the payload URL as JSON.")
@pass_context
def rotate_command(ctx: Context, domain: str, yes: bool) -> None:
    """
    Create the webhook secret, or replace it.

    The secret is printed once, here. Replacing one revokes the old in the same
    motion: the forge's webhook fails its signature until it is given the new
    secret, so an existing secret is only replaced after confirming.
    """
    app = _application(domain)
    existing = service.status(app).enabled
    if existing and not yes and not ctx.dry_run:
        click.confirm(
            f"Replace the webhook secret of {app.domain}? The forge's webhook stops working "
            "until it has the new one",
            abort=True,
        )

    secret = service.mint_secret(app.domain)
    if is_rehearsal():
        ctx.logger.info(
            f"Would {'replace' if existing else 'create'} the webhook secret of {app.domain}"
        )
        return

    state = service.status(app)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": app.domain,
                    "secret": secret,
                    "hook_url": state.hooks.hook_url,
                    "hook_url_public": state.hooks.hook_url_public,
                    "content_type": state.hooks.content_type,
                    "events": state.hooks.events,
                }
            )
        )
        return

    logger = ctx.logger
    logger.success(f"Webhook secret {'replaced' if existing else 'created'} for {app.domain}")
    logger.key_value("Secret", secret)
    logger.key_value("Payload URL", state.hooks.hook_url or "run 'noust web expose-hooks' first")
    logger.key_value("Content type", state.hooks.content_type)
    logger.key_value("Events", ", ".join(state.hooks.events))
    if state.forge.settings_url:
        logger.key_value("Add it at", state.forge.settings_url)
    logger.info("The secret is shown here once more only with: noust app webhook show --reveal")


@webhook.command("disable")
@click.argument("domain")
@global_flags
@pass_context
def disable_command(ctx: Context, domain: str) -> None:
    """
    Discard the webhook secret: deliveries are answered 404 from now on.

    The forge's webhook can stay where it is; it stops deploying. Creating a
    secret again ('noust app webhook rotate') turns it back on.
    """
    app = _application(domain)
    if not service.status(app).enabled:
        ctx.logger.info(f"The webhook of {app.domain} is already disabled")
        return
    service.disable_secret(app.domain)
    if is_rehearsal():
        ctx.logger.info(f"Would disable the webhook of {app.domain}")
        return
    ctx.logger.success(f"Webhook disabled for {app.domain}: pushes no longer deploy it")


@webhook.command("deliveries", read_only=True)
@click.argument("domain")
@click.option(
    "--limit",
    type=click.IntRange(1, webhook_deliveries.KEEP_PER_APP),
    default=20,
    show_default=True,
    help="How many deliveries to list, newest first.",
)
@global_flags
@json_option("Print the deliveries as JSON.")
@pass_context
def deliveries_command(ctx: Context, domain: str, limit: int) -> None:
    """
    List what the forge has sent to this application's webhook.

    A ping, a push that deployed, a push to another branch, a repeat, a wrong
    signature: whatever became of it. A burst of wrong signatures is one line
    with a count.
    """
    app = _application(domain)
    if app.id is None:
        raise DeploymentError(f"Application {domain} has no id in the store")
    rows = webhook_deliveries.list_deliveries(app.id, limit=limit)

    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": app.domain,
                    "items": [
                        {
                            "id": row.id,
                            "received_at": row.received_at,
                            "provider": row.provider,
                            "event": row.event,
                            "outcome": row.outcome,
                            "branch": row.branch,
                            "detail": row.detail,
                            "job_id": row.job_id,
                            "delivery_id": row.delivery_id,
                            "count": row.count,
                        }
                        for row in rows
                    ],
                }
            )
        )
        return

    if not rows:
        ctx.logger.info(
            f"No deliveries yet for {app.domain}: nothing has reached its webhook. "
            "'noust app webhook show' says what is missing."
        )
        return
    ctx.logger.table(
        ["Received", "Outcome", "Event", "Branch", "From", "Detail"],
        [
            [
                _when(row.received_at),
                OUTCOME_WORDS.get(row.outcome, row.outcome)
                + (f" x{row.count}" if row.count > 1 else ""),
                row.event or "-",
                row.branch or "-",
                row.provider or "-",
                row.detail or "-",
            ]
            for row in rows
        ],
    )
