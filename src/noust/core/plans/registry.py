# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which operations have a plan, and where it is asked for.

An operation's plan is its own route, and the API says so in the operation itself, with the
extension ``x-noust-plan`` (:data:`PLAN_EXTENSION`), so that the console and the built-in assistant
know without a table of their own whether an operation has a "See what it will do". This module is
that table, as data: the keys of the operations, the route of each operation and the route of its
plan. The functions that make the plans are in :mod:`noust.deployers.plans`; the routes are in
:mod:`noust.web.api.plans`; a test holds the three to one another.

The convention a client follows:

- The plan is a ``POST`` that changes nothing, asked with the permission to **read** what the
  operation would change, and never asks for sudo mode.
- A ``{name}`` in the plan's path is the operation's own path parameter, or else the field of its
  body of that name (``POST /api/jobs/update`` names its application in the body, and its plan has
  it in the path).
- The plan's body is made of the operation's body fields and of its query parameters, by name,
  without the ones that went to the path. A field the plan has no use for is accepted and ignored,
  so the operation's own body can be sent as it is.
- The answer is :class:`~noust.core.plans.shape.PlanOut`, whatever the operation.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What the OpenAPI operation of a planned operation carries: ``{"method", "path", "operation"}``.
PLAN_EXTENSION = "x-noust-plan"


@dataclass(frozen=True)
class PlannedOperation:
    """
    One operation of the API that has a plan.

    Attributes:
        key: What the operation is, as the plan says it (``app.update``): one key for the several
            routes that start the same thing.
        method: The operation's HTTP method.
        path: The operation's route, as FastAPI reports its template.
        plan_path: The route of its plan.
        plan_method: The method of the plan; always ``POST``, said here so a client never guesses.
    """

    key: str
    method: str
    path: str
    plan_path: str
    plan_method: str = "POST"


#: Every operation that has a plan. It only grows: ``tests/test_plans_registry.py`` holds each of
#: these to a route that exists, a plan route that exists, and a plan that reads.
PLANNED: tuple[PlannedOperation, ...] = (
    PlannedOperation("app.update", "POST", "/api/jobs/update", "/api/apps/{domain}/update/plan"),
    PlannedOperation(
        "app.update", "POST", "/api/apps/{domain}/deploy", "/api/apps/{domain}/update/plan"
    ),
    PlannedOperation(
        "app.rollback",
        "POST",
        "/api/apps/{domain}/deployments/{deployment_id}/rollback",
        "/api/apps/{domain}/deployments/{deployment_id}/rollback/plan",
    ),
    PlannedOperation(
        "app.restart", "POST", "/api/apps/{domain}/restart", "/api/apps/{domain}/restart/plan"
    ),
    PlannedOperation("app.stop", "POST", "/api/apps/{domain}/stop", "/api/apps/{domain}/stop/plan"),
    PlannedOperation(
        "app.start", "POST", "/api/apps/{domain}/start", "/api/apps/{domain}/start/plan"
    ),
    PlannedOperation(
        "app.limits", "PATCH", "/api/apps/{domain}/limits", "/api/apps/{domain}/limits/plan"
    ),
    PlannedOperation(
        "cert.renew", "POST", "/api/certs/{domain}/renew", "/api/certs/{domain}/renew/plan"
    ),
    PlannedOperation(
        "app.domain.add", "POST", "/api/apps/{domain}/domains", "/api/apps/{domain}/domains/plan"
    ),
    PlannedOperation(
        "app.domain.remove",
        "DELETE",
        "/api/apps/{domain}/domains/{name}",
        "/api/apps/{domain}/domains/{name}/plan",
    ),
    PlannedOperation(
        "app.delete", "DELETE", "/api/apps/{domain}", "/api/apps/{domain}/delete/plan"
    ),
    PlannedOperation("app.delete", "POST", "/api/jobs/delete", "/api/apps/{domain}/delete/plan"),
    PlannedOperation(
        "app.migrate", "POST", "/api/apps/{domain}/migrate", "/api/apps/{domain}/migrate/plan"
    ),
    PlannedOperation("docker.cleanup", "POST", "/api/docker/cleanup", "/api/docker/cleanup/plan"),
)


def plan_of(method: str, path: str) -> PlannedOperation | None:
    """
    Args:
        method: An operation's HTTP method.
        path: Its route's template.

    Returns:
        Its entry, or None when it has no plan.
    """
    verb = method.upper()
    return next((item for item in PLANNED if item.method == verb and item.path == path), None)


def plan_routes() -> dict[tuple[str, str], str]:
    """
    Returns:
        The routes that are plans, ``(method, path)`` to the key of the operation each plans.
    """
    return {(item.plan_method, item.plan_path): item.key for item in PLANNED}
