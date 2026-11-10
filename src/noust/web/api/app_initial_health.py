# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The health settings a new application is created with, checked before its deployment is queued.

Moved out of :mod:`noust.web.api.apps`, which is past the size a module may have: the rule it
applies is the one ``PATCH /api/apps/{domain}/health`` applies, and lives in
:mod:`noust.validators.health`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from noust.core.exceptions import ValidationError

__all__ = ["check_initial_health"]


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
