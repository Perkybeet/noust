# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The background job that updates an application to its recipe's current version.

A job function, like the ones in :mod:`noust.web.jobs`: it reports progress to
the :class:`~noust.web.jobs.JobContext` the manager injects, and does its work
by calling :func:`noust.recipes.updates.update_recipe`, which is what ``noust
recipe update`` calls too.
"""

from __future__ import annotations

from typing import Any

from noust.recipes.updates import update_recipe
from noust.web.jobs import JobContext


def update_recipe_job(
    domain: str,
    variables: dict[str, str] | None = None,
    force: bool = False,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Update an application installed from a recipe.

    Args:
        domain: The application's domain.
        variables: Values for the variables the new version asks for.
        force: Deploy the catalogue's version even when it is the one installed.
        job_context: Injected by the job manager.

    Returns:
        What was done: the versions, the deployment, the variables added and
        the notes.

    Raises:
        ValueError: Called outside the job manager.
        NoustError: The update failed; the message says whether the previous
            version is serving again.
    """
    if job_context is None:
        raise ValueError("job_context is required; job functions run under the job manager")
    context = job_context
    context.set_metadata("domain", domain)
    outcome = update_recipe(
        domain,
        overrides=variables or {},
        force=force,
        trigger="panel",
        on_phase=lambda index, total, message: context.update(message, 100 * (index - 1) // total),
        on_step=context.log,
        job_id=context.job_id,
    )
    if outcome.warnings:
        context.log(f"Updated with warnings: {outcome.warnings}", "warning")
    context.update("Update complete" if outcome.updated else "Already up to date", 100)
    return {
        "domain": outcome.domain,
        "recipe": outcome.recipe,
        "status": "updated" if outcome.updated else "unchanged",
        "from_version": outcome.from_version,
        "to_version": outcome.to_version,
        "deployment_id": outcome.deployment_id,
        "added_variables": list(outcome.added_variables),
        "warnings": outcome.warnings,
    }
