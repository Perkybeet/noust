# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The composers of what the network and monitoring lane added.

The same rules as :mod:`noust.core.notifications.composers` (a title that is a state and nothing
else, one sentence, the server first, a program's own words verbatim in the excerpt), for:

- the **web server** that fell, and what was done about it;
- a **release that fixes a vulnerability**;
- a **container** that restarts in a loop;
- a **recipe** that has a newer version.

Pure, like the rest of the package: the monitor reads the machine and hands these functions the
facts.
"""

from __future__ import annotations

from collections.abc import Sequence

from noust.core.messages import message, plural
from noust.core.notifications.composers import _fact, build
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.excerpt import make_excerpt
from noust.core.notifications.model import Notification, State

#: The recipes a notification names; the rest are counted.
MAX_RECIPES_LISTED = 6

#: How each web server outcome is told: the state, and the command that helps next.
_WEBSERVER_STATES: dict[str, State] = {
    "restarted": State.WARNING,
    "down": State.FAILED,
    "config_invalid": State.FAILED,
    "restart_failed": State.FAILED,
    "limit": State.FAILED,
    "stopped": State.WARNING,
}


def compose_webserver_down(
    outcome: str,
    server: str,
    ctx: NotificationContext,
    *,
    unit: str = "",
    test: str = "",
    output: str | None = None,
    restarts: int = 0,
) -> Notification:
    """
    Compose the notification for a web server that is not running.

    Args:
        outcome: ``restarted`` (it fell and was started again), ``down`` (automatic restarts
            are off), ``config_invalid`` (its own test failed, so it was not restarted),
            ``restart_failed``, ``limit`` (it fell too often in an hour) or ``stopped`` (a
            person stopped it).
        server: ``nginx`` or ``apache``.
        ctx: The context.
        unit: Its systemd unit (``nginx`` or ``apache2``).
        test: The command that tests its configuration.
        output: What the server's test or the restart said, verbatim.
        restarts: The restarts in the last hour, for ``limit``.

    Returns:
        ``webserver.<outcome>``, under the ``unit_failed`` switch that opens a unit's failure.
    """
    code = f"webserver.{outcome}"
    summary = message(
        f"summary.{code}", ctx.locale, count=restarts, times=plural("time", ctx.locale, restarts)
    )
    unit = unit or server
    if outcome == "stopped":
        command = _fact("start", f"systemctl start {unit}", ctx, mono=True)
    elif outcome == "config_invalid" and test:
        command = _fact("verify", test, ctx, mono=True)
    else:
        command = _fact("inspect", f"journalctl -u {unit} -n 50", ctx, mono=True)
    excerpt = None
    if output and output.strip():
        excerpt = make_excerpt(
            output,
            label=message("excerpt.output", ctx.locale),
            avoid=[summary, command.value],
            pin_error=True,
        )
    return build(
        ctx,
        kind="unit_failed",
        code=code,
        state=_WEBSERVER_STATES[outcome],
        subject=server,
        summary=summary,
        command=command,
        excerpt=excerpt,
        path="/server",
    )


def compose_security_release(
    version: str,
    current: str,
    ctx: NotificationContext,
    *,
    advisory: str = "",
    update: str = "",
) -> Notification:
    """
    Compose the notification for a Noust release that fixes a vulnerability.

    Args:
        version: The newest release that fixes one.
        current: The version this server runs.
        ctx: The context.
        advisory: Where the advisory (or, failing that, the release notes) is.
        update: The command that installs it on this server.

    Returns:
        ``security.release`` (a warning), under the ``security_release`` switch.
    """
    facts = [_fact("running", current, ctx), _fact("fixed_in", version, ctx)]
    if advisory:
        facts.append(_fact("advisory", advisory, ctx, mono=True))
    return build(
        ctx,
        kind="security_release",
        code="security.release",
        state=State.WARNING,
        subject=f"Noust {version}",
        summary=message("summary.security.release", ctx.locale, version=version, current=current),
        facts=facts,
        command=_fact("update_with", update, ctx, mono=True) if update else None,
        path="/settings/about",
    )


def compose_container_loop(
    outcome: str,
    container: str,
    ctx: NotificationContext,
    *,
    domain: str | None = None,
    restarts: int = 0,
    limit: int = 0,
    stable_minutes: int = 0,
    exit_code: int | None = None,
    output: str | None = None,
) -> Notification:
    """
    Compose the notification for a container that Docker keeps restarting, or that stopped.

    Args:
        outcome: ``restart_loop`` or ``recovered``.
        container: The container's name.
        ctx: The context.
        domain: The application whose stack it belongs to.
        restarts: How many times Docker restarted it since it was first seen.
        limit: ``monitor.container_restart_limit``.
        stable_minutes: For ``recovered``: how long it has run without restarting.
        exit_code: The code of its last exit, when Docker kept one.
        output: The container's last lines, verbatim.

    Returns:
        ``container.<outcome>``, under the ``container_restart_loop`` switch.
    """
    code = f"container.{outcome}"
    summary = message(
        f"summary.{code}",
        ctx.locale,
        count=restarts,
        times=plural("time", ctx.locale, restarts),
        limit=limit,
        minutes=stable_minutes,
    )
    facts = [_fact("container", container, ctx, mono=True)]
    if outcome == "restart_loop":
        facts.append(_fact("restarts", str(restarts), ctx))
        if exit_code is not None:
            facts.append(_fact("exit_code", str(exit_code), ctx))
    command = _fact("inspect", f"docker logs --tail 50 {container}", ctx, mono=True)
    excerpt = None
    if output and output.strip():
        excerpt = make_excerpt(
            output,
            label=message("excerpt.output", ctx.locale),
            avoid=[summary, command.value, *(fact.value for fact in facts)],
            pin_error=True,
        )
    return build(
        ctx,
        kind="container_restart_loop",
        code=code,
        state=State.FAILED if outcome == "restart_loop" else State.OK,
        subject=domain or container,
        summary=summary,
        facts=facts,
        command=command,
        excerpt=excerpt,
        path=f"/apps/{domain}" if domain else "/server",
        domain=domain,
    )


def compose_recipes_outdated(
    behind: Sequence[tuple[str, str, str, str]], ctx: NotificationContext
) -> Notification:
    """
    Compose the notification for installed recipes that have a newer version.

    Args:
        behind: ``(application, recipe, installed version, newest version)`` for each.
        ctx: The context.

    Returns:
        ``recipe.outdated`` (a warning), under the ``recipe_outdated`` switch.
    """
    count = len(behind)
    shown = behind[:MAX_RECIPES_LISTED]
    facts = [
        _fact("recipe", f"{app}: {old} -> {new}", ctx, mono=True) for app, _, old, new in shown
    ]
    if count > len(shown):
        facts.append(_fact("more", str(count - len(shown)), ctx))
    return build(
        ctx,
        kind="recipe_outdated",
        code="recipe.outdated",
        state=State.INFO,
        subject="",
        summary=message(
            "summary.recipe.outdated",
            ctx.locale,
            count=count,
            recipes=plural("recipe", ctx.locale, count),
        ),
        facts=facts,
        command=_fact("update_with", "noust recipe outdated", ctx, mono=True),
        path="/catalog",
    )
