# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``POST /api/apps`` prepares before it queues a deployment.

Moved out of :mod:`noust.web.api.apps`, which is past the size a module may have: the health
settings the new application is created with, the container settings and the database it is
described to the deploy job with. Each is checked before anything is queued, so a value the job
could not use is a refusal the wizard shows beside its field, not a failed deployment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from noust.core.exceptions import ValidationError
from noust.web.api.app_container import ContainerSettingsRequest, request_to_changes
from noust.web.pydantic_compat import dump_model

__all__ = ["check_initial_health", "database_for_job", "new_container_settings"]


def check_initial_health(path: str | None, expect: str | None, timeout: int | None) -> None:
    """
    Refuse health settings the gate could not use, naming the field.

    Args:
        path: Path to probe, or None.
        expect: Accepted statuses, or None.
        timeout: Seconds to wait, or None.

    Raises:
        ValidationError: A value is not usable; ``field`` names it.
    """
    from noust.validators.health import (
        check_health_expect,
        check_health_path,
        check_health_timeout,
    )

    checks: tuple[tuple[str, Callable[[Any], Any], Any], ...] = (
        ("health_path", check_health_path, path),
        ("health_expect", check_health_expect, expect),
        ("health_timeout", check_health_timeout, timeout),
    )
    for field, check, value in checks:
        if value is None:
            continue
        try:
            check(value)
        except ValidationError as exc:
            exc.field = field
            raise


def database_for_job(database: Any) -> dict[str, Any]:
    """
    Describe the new application's database the way the deploy job takes it.

    The version was checked when the request arrived; the job only needs to be
    told what it did not know before 4.0, so a request that names none is the
    job it always was.

    Args:
        database: The request's database (``NewAppDatabaseRequest``).

    Returns:
        The database as a plain mapping, without an unset version.
    """
    described = dump_model(database)
    for optional in ("version", "instance"):
        if described.get(optional) is None:
            described.pop(optional, None)
    return described


def new_container_settings(
    domain: str, request: ContainerSettingsRequest | None, source: str | None
) -> dict[str, Any]:
    """
    Turn the container settings of a creation request into what the deploy job takes.

    Args:
        domain: The new application's domain.
        request: What the client gave, or None for a repository with a Dockerfile.
        source: The request's source, which an image from a registry has none of.

    Returns:
        The validated settings as a mapping; their secrets are already in the
        secret store.

    Raises:
        ValidationError: The settings are not valid, an image was given a
            source, or a build was given none.
    """
    from noust.deployers.helpers.container_settings import settings_for_new_app

    body = request if request is not None else ContainerSettingsRequest()
    changes = request_to_changes(body)
    method = changes.get("method") or ("image" if changes.get("image") else "dockerfile")
    changes["method"] = method
    if method == "image" and source:
        raise ValidationError(
            "An image from a registry has no source",
            details="Give the image in container.image and leave source out.",
            field="source",
        )
    if method != "image" and not source:
        raise ValidationError(
            "A source is required",
            details="Give the repository to build in source, or an image in container.image.",
            field="source",
        )
    return asdict(settings_for_new_app(domain, changes))
