# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What updating an application would do (item 183).

The update is :func:`noust.deployers.lifecycle.update_app`, called as it is called to apply it and
rehearsed: the commands it would run, in their order, and the files it would write come from that
code. What the plan adds is what looking can tell without building: whether the repository has
anything new (``git ls-remote``, with a deadline, nothing downloaded), whether deploys are frozen,
what is serving now, and, said as what it does not know, everything that only a build can answer.
"""

from __future__ import annotations

from noust.core.appdir.layout import RELEASES, app_root
from noust.core.appdir.releases import ReleaseManager
from noust.core.exceptions import NoustError
from noust.core.plans import engine
from noust.core.plans.shape import PlanBlock, PlanOut
from noust.core.server_text import ServerText, say
from noust.core.store import App, AppType, get_store, is_compose_backed
from noust.deployers import lifecycle
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.plans import common
from noust.validators.domain import validate_domain

OPERATION = "app.update"


def _short(commit: str) -> str:
    """
    Args:
        commit: A commit id.

    Returns:
        Its first seven characters, as a person says it.
    """
    return commit[:7]


def _effect(app: App, branch: str, commit: str | None, tag: str | None) -> ServerText:
    """
    Args:
        app: The application.
        branch: What it follows.
        commit: The commit asked for, when one was.
        tag: The tag asked for, when one was.

    Returns:
        What changes, in a sentence.
    """
    if commit is not None or tag is not None:
        what = f"tag {tag}" if tag is not None else f"commit {_short(commit or '')}"
        return say("plans.update.exact", what=what, domain=app.domain)
    if app.app_type == AppType.CONTAINER.value or is_compose_backed(app.app_type):
        return say("plans.update.containers", domain=app.domain)
    if app.layout == RELEASES:
        return say("plans.update.release", domain=app.domain, branch=branch)
    return say("plans.update.in_place", domain=app.domain, branch=branch)


def plan_update(
    domain: str,
    *,
    force: bool = False,
    no_cache: bool = False,
    commit: str | None = None,
    tag: str | None = None,
) -> PlanOut:
    """
    Say what updating an application would do.

    Args:
        domain: The application's domain.
        force: Rebuild even when the branch has nothing new.
        no_cache: Build from scratch; implies ``force``.
        commit: Deploy exactly this commit.
        tag: Deploy the commit this tag points at.

    Returns:
        The plan: what would run and change, what the plan found out, what it does not know, what
        applying it will ask to confirm and, when it would be refused now, why.

    Raises:
        NoustError: The application is not deployed.
    """
    domain = validate_domain(domain)
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}",
            details="Run 'noust list' to see what is deployed.",
        )
    exact = commit is not None or tag is not None
    asks_remote = not (force or no_cache or exact)
    phases = engine.Phases()

    def run(frozen_ok: bool, schema_ok: bool) -> engine.Rehearsal[lifecycle.UpstreamState | None]:
        def operation() -> lifecycle.UpstreamState | None:
            state = (
                lifecycle.check_upstream(domain, logger=common.quiet_logger())
                if asks_remote
                else None
            )
            lifecycle.update_app(
                domain,
                commit=commit,
                tag=tag,
                no_cache=no_cache,
                frozen_ok=frozen_ok,
                schema_changed_ok=schema_ok,
                on_phase=phases.phase,
                on_step=phases.announce,
                logger=common.quiet_logger(),
            )
            return state

        return engine.rehearse(operation, scrub=common.scrub_for(domain), phases=phases)

    confirmed = common.confirming(domain, f"commit {commit}" if commit else "that version", run)
    attempt = confirmed.rehearsal
    state = attempt.result
    branch = lifecycle.branch_to_build(app) or app.branch or "HEAD"

    findings: list[ServerText] = []
    unknown: list[ServerText] = []
    blocked: PlanBlock | None = common.busy(domain)

    if state is not None:
        if state.has_new_commits:
            findings.append(
                say(
                    "plans.found.upstream_new",
                    branch=state.branch,
                    domain=domain,
                    live=_short(state.live_commit),
                    remote=_short(state.remote_commit),
                )
            )
        elif blocked is None:
            blocked = PlanBlock(
                say(
                    "plans.blocked.nothing_new",
                    branch=state.branch,
                    live=_short(state.live_commit),
                )
            )
    elif asks_remote and app.source:
        unknown.append(say("plans.unknown.remote", reason="no answer"))

    active = ReleaseManager(app_root(app)).current() if app.layout == RELEASES else None
    if active is not None:
        findings.append(say("plans.found.serving_now", release=active.id))

    if blocked is None:
        blocked, stopped = common.stopped(attempt)
        unknown.extend(stopped)

    unknown.append(say("plans.unknown.build"))
    if not exact:
        unknown.append(say("plans.unknown.new_code"))
    unknown.append(say("plans.unknown.health", check=HealthCheck.for_app(app).describe(app.port)))
    if attempt.files or any(not command.probe for command in attempt.commands):
        unknown.append(say("plans.unknown.readback"))
    unknown.extend(common.undone_probes(attempt))

    return common.assemble(
        OPERATION,
        domain,
        _effect(app, branch, commit, tag),
        attempt,
        findings=findings,
        unknown=unknown,
        confirmations=confirmed.confirmations,
        blocked=blocked,
    )
