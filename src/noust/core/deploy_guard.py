# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
May this deploy go ahead now? The one answer to it.

Two things hold deploys, and they are held differently because they are for
different reasons:

- A **freeze** (:mod:`noust.core.deploy_freeze`) says nobody changes production now:
  an incident, a sale, a holiday. It holds every deploy of what it covers. A person
  who knows can go ahead anyway and it is recorded; what starts by itself (a push, a
  tag, a schedule) cannot.
- A **pause** (:mod:`noust.core.app_rollout_store`) says that what deploys an
  application *by itself* stops. It is what going back does, so that the next push
  does not undo the rollback. A person's own update is never held by it.

A rollback is never held by either: going back is how an incident ends. Nor are the
previews of an application, which are throwaway copies and not production.

Every door that deploys asks here: :func:`noust.deployers.lifecycle.update_app` (so
the CLI, the console, the webhooks and the schedules cannot differ), and the queues
in front of it, which refuse before a job exists. This module only decides (rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from noust.core import app_rollout_store, audit, deploy_freeze
from noust.core.exceptions import DeploysFrozenError, DeploysPausedError
from noust.core.fs import is_rehearsal
from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class Gate:
    """
    Why a deploy is held.

    Attributes:
        kind: ``frozen`` or ``paused``.
        message: One sentence saying which application is held and why.
        details: What to do about it.
        freeze: The freeze, when ``frozen``.
        paused_by: Who paused it, when ``paused``.
    """

    kind: str
    message: str
    details: str
    freeze: deploy_freeze.Freeze | None = None
    paused_by: str | None = None


def _scope(freeze: deploy_freeze.Freeze) -> str:
    """
    Args:
        freeze: A freeze.

    Returns:
        What it covers, as a phrase for a sentence.
    """
    return f"for {freeze.domain}" if freeze.domain else "on this server"


def _until(freeze: deploy_freeze.Freeze) -> str:
    """
    Args:
        freeze: A freeze.

    Returns:
        When it ends, as a phrase.
    """
    return f"until {freeze.ends_at}" if freeze.ends_at else "until somebody lifts it"


def automatic_sources(domain: str, *, store: NoustStore | None = None) -> list[str]:
    """
    Say what can deploy an application without anybody asking.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        ``webhook`` (a secret for a code host's deliveries), ``github`` (the GitHub
        App reaches its repository), ``tags`` (it follows tags) and ``schedule``
        (an update is scheduled), whichever apply; empty for an application only
        a person updates.
    """
    target = store or get_store()
    app = target.get_app(domain)
    if app is None or app.preview_parent:
        return []
    found: list[str] = []
    if target.get_webhook_secret(domain):
        found.append("webhook")
    if app.github_installation_id is not None:
        found.append("github")
    if app.follow_tags:
        found.append("tags")
    return found


def pause_after_rollback(
    domain: str, *, target: str | None = None, store: NoustStore | None = None
) -> bool:
    """
    Pause what deploys an application by itself, because it was just rolled back.

    Without this the next push to the branch deploys the code that was rolled back
    from, an hour after somebody fixed the incident. It only pauses an application
    that has something to pause (:func:`automatic_sources`) and that did not switch
    ``pause_on_rollback`` off, and it leaves an existing pause alone.

    Args:
        domain: The application that was rolled back.
        target: What it went back to, for the reason.
        store: The store; the process-wide one when None.

    Returns:
        True when it was paused now.
    """
    if is_rehearsal():
        return False
    settings = app_rollout_store.get(domain, store=store)
    if not settings.pause_on_rollback or settings.paused:
        return False
    sources = automatic_sources(domain, store=store)
    if not sources:
        return False
    where = f" to {target}" if target else ""
    app_rollout_store.pause(
        domain,
        reason=f"Rolled back{where}: a push would deploy again what was rolled back from",
        by=app_rollout_store.ROLLBACK_PAUSE_ACTOR,
        store=store,
    )
    return True


def gate_for(
    domain: str,
    *,
    automatic: bool,
    store: NoustStore | None = None,
    now: datetime | None = None,
) -> Gate | None:
    """
    Decide whether something holds a deploy of an application.

    Args:
        domain: The application's domain.
        automatic: The deploy starts by itself: a push, a tag, a schedule.
        store: The store; the process-wide one when None.
        now: The moment to judge at; the clock when None.

    Returns:
        Why it is held, or None when it may go ahead.
    """
    target = store or get_store()
    app = target.get_app(domain)
    if app is not None and app.preview_parent:
        return None
    freeze = deploy_freeze.active_for(domain, store=target, now=now)
    if freeze is not None:
        who = f" by {freeze.created_by}" if freeze.created_by else ""
        message = f"Deploys are frozen {_scope(freeze)} {_until(freeze)}: {freeze.reason}"
        lift = f"noust deploy unfreeze{' ' + freeze.domain if freeze.domain else ''}"
        if automatic:
            details = (
                f"Frozen{who} since {freeze.starts_at}. A deploy that starts by itself waits "
                f"for the freeze to end: deploy by hand afterwards, or lift it with '{lift}'."
            )
        else:
            details = (
                f"Frozen{who} since {freeze.starts_at}. Lift the freeze with '{lift}', or "
                "deploy anyway if you are sure (--during-freeze, or frozen_ok in the API); "
                "that is recorded."
            )
        return Gate("frozen", message, details, freeze=freeze)
    if automatic:
        rollout = app_rollout_store.get(domain, store=target)
        if rollout.paused:
            why = f": {rollout.paused_reason}" if rollout.paused_reason else ""
            return Gate(
                "paused",
                f"What deploys {domain} by itself is paused since {rollout.paused_at}{why}",
                f"Resume it with 'noust deploy resume {domain}' once the older version has "
                "been looked at; an update you start yourself still runs.",
                paused_by=rollout.paused_by,
            )
    return None


def require_open(
    domain: str,
    *,
    automatic: bool = False,
    override: bool = False,
    actor: str | None = None,
    record: bool = True,
    store: NoustStore | None = None,
    now: datetime | None = None,
) -> None:
    """
    Refuse a deploy that something holds.

    Args:
        domain: The application's domain.
        automatic: The deploy starts by itself.
        override: A person confirmed deploying during a freeze. It is recorded, and
            it does nothing for a deploy that starts by itself or for a pause (a
            person's deploy is not held by one).
        actor: Who deploys, for the record of an override.
        record: Write the override in the audit trail. A queue in front of the
            deploy asks without, because the deploy itself will.
        store: The store; the process-wide one when None.
        now: The moment to judge at; the clock when None.

    Raises:
        DeploysFrozenError: A freeze covers it, and it is automatic or was not
            confirmed.
        DeploysPausedError: It is automatic and the application is paused.
    """
    gate = gate_for(domain, automatic=automatic, store=store, now=now)
    if gate is None:
        return
    if gate.kind == "frozen" and gate.freeze is not None:
        if override and not automatic:
            if record:
                audit.record(
                    "deploys.frozen_override",
                    target=f"app:{domain}",
                    details={
                        "freeze": gate.freeze.id,
                        "reason": gate.freeze.reason,
                        "by": actor,
                    },
                )
            return
        raise DeploysFrozenError(
            gate.message,
            gate.details,
            freeze=gate.freeze.to_dict(now),
            automatic=automatic,
        )
    raise DeploysPausedError(gate.message, gate.details, paused_by=gate.paused_by)


__all__ = ["Gate", "automatic_sources", "gate_for", "pause_after_rollback", "require_open"]
