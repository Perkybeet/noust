# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The application a command is about, for the commands that take its domain."""

from __future__ import annotations

from noust.core.app_lookup import require_app
from noust.core.store import get_store
from noust.validators.domain import validate_domain


def known_app(domain: str) -> str:
    """
    Validate a domain and require an application behind it.

    Args:
        domain: What the operator typed.

    Returns:
        The validated domain.

    Raises:
        DomainError: The value is not a domain.
        NoustError: Nothing is deployed there.
    """
    validated = validate_domain(domain)
    require_app(get_store(), validated)
    return validated
