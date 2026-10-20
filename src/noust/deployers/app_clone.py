# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cloning an application: the same application, on another domain.

A clone is an import of the application's own export: the document is the one
``noust app export`` writes (secrets included, since it stays on this server),
made into a plan by :func:`~noust.deployers.app_export.plan_import` and carried
out by :func:`~noust.deployers.app_export.apply_import`, so a clone is created by
the normal deploy path, built, gated and recorded like any other, and a part
that cannot be applied is reported and never guessed. This module only decides
what is *not* copied and what is copied besides the export:

- **Not copied**: the names the original answers on (aliases, redirects, ``www``),
  which a second application cannot also answer on, and its scheduled update, which
  the clone's operator decides.
- **A database is a decision.** An application's variables point at its database,
  so a clone given the same variables writes into the original's data. When the
  application has a database, the clone is told to share it (``share``) or to
  get one of its own (``empty``); there is no default. A clone with its own gets
  none of the original's connection variables
  (:mod:`noust.managers.database.isolation`).
- **Copied besides the export**: the tags, the build block, the deploy hooks,
  the tags it follows, how its previews are made and the variables previews are
  given. The variables that are made of references are copied as the
  references, so ``{{app.URL}}`` is the clone's own address.
- **Server-owned parts** (cron jobs, which run as root, and the backup
  schedule, which names destinations) are copied only when the caller sees the
  whole server.

The clone lives in the project and environment named, or the original's.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from noust.core import app_build_store
from noust.core.app_tags import AppTags
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.preview_store import PreviewStore
from noust.core.store import App, get_store
from noust.core.variables.model import PREVIEW
from noust.core.variables.store import VariableStore
from noust.deployers.app_export import (
    CreateSpec,
    ImportPlan,
    ImportReport,
    ImportStep,
    apply_import,
    export_app,
    plan_import,
)
from noust.deployers.helpers.app_env import read_environment_of
from noust.deployers.helpers.hooks import operator_hooks, set_operator_hooks
from noust.deployers.helpers.layout import RELEASES
from noust.managers.database.isolation import connection_variables, own_database
from noust.validators.domain import validate_domain

#: What a clone does with the application's database.
SHARE = "share"
EMPTY = "empty"
DATABASE_CHOICES: tuple[str, ...] = (SHARE, EMPTY)


@dataclass
class ClonePlan:
    """
    What a clone will do, decided before anything is created.

    Attributes:
        source: The application copied.
        plan: The import the clone is made by.
        database: ``share``, ``empty`` or None when the application has none.
        extras: What is copied besides the export, one line each.
        omitted: What is left out, and why.
    """

    source: App
    plan: ImportPlan
    database: str | None = None
    extras: list[str] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)


def plan_clone(
    source_domain: str,
    new_domain: str,
    *,
    database: str | None = None,
    source: str | None = None,
    server_parts: bool = True,
) -> ClonePlan:
    """
    Decide what cloning an application does.

    Args:
        source_domain: The application to clone.
        new_domain: The clone's domain.
        database: ``share`` or ``empty`` when the application has a database.
        source: Deploy the clone from this source instead of the original's.
        server_parts: Copy the cron jobs and the backup schedule too.

    Returns:
        The plan.

    Raises:
        ValidationError: The application does not exist, is a preview, is not a
            type that builds releases, has a database and no choice was made
            about it, or the choice is not one.
        DomainConflictError: An application is already deployed on the new domain.
    """
    source_domain = validate_domain(source_domain)
    new_domain = validate_domain(new_domain)
    app = get_store().get_app(source_domain)
    if app is None:
        raise ValidationError(
            f"Application not found: {source_domain}", details="Run 'noust list'."
        )
    if app.preview_parent:
        raise ValidationError(f"{source_domain} is a preview", details="Previews are not cloned.")
    if app.layout != RELEASES:
        raise ValidationError(
            f"{source_domain} is not on the release layout",
            details="Only applications that build releases can be cloned "
            f"(noust app migrate {source_domain}).",
        )
    if database is not None and database not in DATABASE_CHOICES:
        raise ValidationError(
            f"Unknown way to give a clone a database: {database!r}",
            details="Use 'empty' (a database of its own) or 'share' (the original's).",
            field="database",
        )
    environment = read_environment_of(app)
    document = export_app(source_domain, with_secrets=True)
    omitted = ["the names it answers on (aliases, redirects, www)"]
    document["domains"] = {"aliases": [], "redirects": []}
    document["app"]["include_www"] = False
    if not server_parts:
        for part in ("cron", "backup"):
            if document.get(part):
                omitted.append(
                    f"{'its cron jobs' if part == 'cron' else 'its backup schedule'}"
                    " (the server's, not a team's)"
                )
        document["cron"] = []
        document["backup"] = None
    delivery = document.get("delivery") or {}
    if delivery.get("update_schedule") is not None:
        # A clone is another application: it updates when somebody asks, or when its operator
        # schedules it. Two applications updating at the same minute is a decision, not a copy.
        omitted.append(
            "its scheduled update (schedule the clone yourself: noust deploy schedule set)"
        )
        delivery["update_schedule"] = None
    document["databases"] = []
    for name, expression in environment.expressions.items():
        if name in document["env"]:
            document["env"][name]["value"] = expression
    wish = own_database(app, environment.values, "clone")
    chosen: str | None = None
    db_request: dict[str, Any] | None = None
    if wish is not None:
        if database is None:
            raise ValidationError(
                f"{source_domain} has a database, and a clone's variables point at it",
                details="Say what the clone does with it: --database empty (a database of its "
                "own) or --database share (writes into the original's data).",
                field="database",
            )
        chosen = database
        if database == EMPTY:
            for name in connection_variables(environment.values):
                document["env"].pop(name, None)
            db_request = {
                "engine": wish.engine,
                "name": wish.name,
                "env_var": wish.env_var,
                "extra_vars": wish.extra_vars,
                "instance": wish.instance,
            }
    import_plan = plan_import(document, domain=new_domain, source=source)
    # A clone never has the original's port; the deploy picks it a free one.
    import_plan.skipped = [
        step for step in import_plan.skipped if not step.part.startswith("port ")
    ]
    create = dataclasses.replace(
        import_plan.create,
        env_audience={
            n: k for n, k in environment.audience.items() if n in import_plan.create.env_vars
        },
        database=db_request,
    )
    import_plan.create = create
    clone = ClonePlan(app, import_plan, chosen, omitted=omitted)
    clone.extras = _extras(app)
    if chosen == EMPTY and wish is not None:
        clone.extras.append(f"a database of its own, {wish.name}")
    elif chosen == SHARE:
        clone.extras.append("the original's database, shared")
    return clone


def _extras(app: App) -> list[str]:
    """
    Args:
        app: The application copied.

    Returns:
        What is copied besides the export, for the plan to say.
    """
    found = []
    if AppTags().of(app.domain):
        found.append(f"tags {', '.join(AppTags().of(app.domain))}")
    if app_build_store.get_document(app.domain) is not None:
        found.append("its build block")
    if operator_hooks(app.domain) is not None:
        found.append("its deploy hooks")
    if app.follow_tags:
        found.append(f"following tags {app.follow_tags}")
    if PreviewStore().has_options(app.domain) and get_store().get_preview_settings(app.domain):
        found.append("how its previews are made")
    return found


def apply_clone(
    plan: ClonePlan,
    *,
    deploy: Callable[[CreateSpec], None],
    logger: Logger | None = None,
    actor: str = "cli",
) -> ImportReport:
    """
    Create the clone and copy what the export does not carry.

    Args:
        plan: What :func:`plan_clone` decided.
        deploy: Creates the application through the normal deploy path.
        logger: Where progress goes.
        actor: Who asked, for the records.

    Returns:
        What was applied and what was not.

    Raises:
        NoustError: The deploy failed; nothing else was attempted.
    """
    log = logger or Logger()
    report = apply_import(plan.plan, deploy=deploy, logger=log, actor=actor)
    store = get_store()
    source = plan.source.domain
    target = plan.plan.domain

    def attempt(part: str, action: Callable[[], object]) -> None:
        try:
            action()
        except (NoustError, OSError) as exc:
            detail = exc.message if isinstance(exc, NoustError) else str(exc)
            log.warning(f"Not applied: {part}: {detail}")
            report.steps.append(ImportStep(part, False, detail))
            return
        log.substep(f"Applied {part}")
        report.steps.append(ImportStep(part, True))

    tags = AppTags(store).of(source)
    if tags:
        attempt("tags", lambda: AppTags(store).set(target, tags))
    block = app_build_store.get_document(source)
    if block is not None:
        attempt(
            "build block",
            lambda: app_build_store.set_document(target, block, updated_by=actor),
        )
    hooks = operator_hooks(source)
    if hooks is not None:
        attempt("deploy hooks", lambda: set_operator_hooks(target, hooks, actor=actor))
    if plan.source.follow_tags:
        from noust.deployers.lifecycle import set_follow_tags

        attempt("followed tags", lambda: set_follow_tags(target, plan.source.follow_tags))
    options = PreviewStore(store)
    if options.has_options(source) and store.get_preview_settings(target) is not None:
        attempt(
            "how previews are made",
            lambda: options.save_options(
                dataclasses.replace(options.options(source), app_domain=target)
            ),
        )
        src = store.get_app(source)
        dst = store.get_app(target)
        if src is not None and src.id is not None and dst is not None and dst.id is not None:
            attempt(
                "the variables previews are given",
                lambda: _copy_preview_set(src.id or 0, dst.id or 0),
            )
    return report


def _copy_preview_set(source_id: int, target_id: int) -> None:
    """
    Give an application the variables the previews of another are given.

    Args:
        source_id: The application copied.
        target_id: The clone.
    """
    store = VariableStore()
    for variable in store.list(PREVIEW, source_id):
        store.set(
            PREVIEW,
            target_id,
            variable.name,
            variable.value,
            secret=variable.secret,
            audience=variable.audience,
            actor=variable.updated_by,
        )


def deploy_through_job(job_context: Any) -> Callable[[CreateSpec], None]:
    """
    Build the deploy a clone runs: the console's deploy job, given the plan's spec.

    The console runs it as a job and the terminal runs it in its own process with
    a context that prints (:class:`~noust.core.terminal_context.TerminalContext`);
    either way it is the function ``POST /api/apps`` queues, so a clone's
    database, variables and audiences are made by the code that makes any
    application's.

    Args:
        job_context: What the job function reports its progress through.

    Returns:
        A function that deploys what a spec asks for and raises when it fails.
    """

    def deploy(spec: CreateSpec) -> None:
        from noust.web.jobs import deploy_app_job

        deploy_app_job(**spec.job_arguments(), job_context=job_context)

    return deploy
